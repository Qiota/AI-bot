import discord
from discord import app_commands, ButtonStyle, Interaction
from discord.ui import Button, View, Modal, TextInput
import aiohttp
import io
import math
from typing import List, Optional, Dict, Tuple, Any
from dataclasses import dataclass
import asyncio
from contextlib import asynccontextmanager
import backoff
import logging
from cachetools import TTLCache
from concurrent.futures import ThreadPoolExecutor
from ...systemLog import logger
from ...utils.checker import checker
from ..restrict import check_bot_access, restrict_command_execution

# Отключаем логирование aiohttp.access для снижения шума
aiohttp_access_logger = logging.getLogger("aiohttp.access")
aiohttp_access_logger.propagate = False
aiohttp_access_logger.addHandler(logging.NullHandler())

# Конфигурационные константы
MAX_FILE_SIZE_DEFAULT = 10 * 1024 * 1024  # 10 МБ
MAX_FILE_SIZE_TIER_2 = 25 * 1024 * 1024  # 25 МБ
MAX_FILE_SIZE_TIER_3 = 100 * 1024 * 1024  # 100 МБ
SUPPORTED_MIME_TYPES = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/gif': '.gif',
    'video/mp4': '.mp4',
    'video/webm': '.webm'
}
REQUEST_TIMEOUT = 10  # Таймаут для HEAD/GET-запросов (секунды)
AUTOCOMPLETE_TIMEOUT = 1  # Таймаут для автодополнения (секунды)
SEMAPHORE_LIMIT = 10  # Ограничение параллельных запросов
INACTIVITY_TIMEOUT = 3600  # Таймаут бездействия (1 час)
TAG_CACHE_TTL = 7200  # Время жизни кэша тегов (2 часа)
TAG_CACHE_SIZE = 5000  # Размер кэша тегов
VIEW_TIMEOUT = 3600  # Таймаут для NavigationView (1 час)
COOLDOWN_TIME = 5  # Время кулдауна команды (секунды)
COOLDOWN_RATE = 1  # Количество использований команды за период
MAX_QUERY_LENGTH = 100  # Максимальная длина запроса для автодополнения
MAX_WORKERS = 4  # Количество потоков для ThreadPoolExecutor
POSTS_PER_PAGE = 20  # Количество постов на странице
POSTS_PER_CHUNK = 10  # Количество постов в чанке (файлы + пропущенные)

# Пользовательские исключения
class DanbooruAPIError(Exception):
    """Исключение для ошибок Danbooru API."""
    pass

# Структурированные данные для постов Danbooru
@dataclass
class DanbooruPost:
    id: int
    file_url: str
    preview_url: str
    tags: List[str]
    rating: str
    source: Optional[str]
    created_at: str

# Глобальное состояние
file_info_cache: Dict[str, Tuple[str, Optional[int]]] = {}  # Кэш для (content_type, file_size) по file_url
tag_suggestions_cache: TTLCache = TTLCache(maxsize=TAG_CACHE_SIZE, ttl=TAG_CACHE_TTL)
used_post_ids: set = set()  # Глобальный кэш использованных ID постов

def format_post_count(count: int) -> str:
    """Форматирует количество постов в читаемый вид (например, 2400 -> '2.4k').

    Args:
        count: Количество постов.

    Returns:
        str: Форматированная строка (например, '2.4k' или '1.2M').
    """
    if count < 1000:
        return str(count)
    elif count < 1000000:
        return f"{count / 1000:.1f}k".replace(".0k", "k")
    else:
        return f"{count / 1000000:.1f}M".replace(".0M", "M")

def parse_post_count(count_str: str) -> int:
    """Парсит форматированное количество постов в число (например, '2.4k' -> 2400).

    Args:
        count_str: Строка с количеством постов (например, '520', '2.4k').

    Returns:
        int: Числовое значение постов.

    Raises:
        ValueError: Если строка имеет неверный формат.
    """
    count_str = count_str.strip().lower()
    try:
        if count_str.endswith('k'):
            return int(float(count_str[:-1]) * 1000)
        elif count_str.endswith('m'):
            return int(float(count_str[:-1]) * 1000000)
        else:
            return int(count_str)
    except (ValueError, TypeError) as e:
        logger.error(f"Ошибка парсинга post-count '{count_str}': {e}")
        raise ValueError(f"Неверный формат post-count: {count_str}")

async def process_api_data(
    data: List[Dict[str, Any]],
    data_type: str,
    session: Optional[aiohttp.ClientSession] = None
) -> List[Any]:
    """Обрабатывает данные API в параллельном режиме.

    Args:
        data: Список словарей из ответа API.
        data_type: Тип данных ('posts' для DanbooruPost, 'tags' для Tuple[str, int]).
        session: Сессия aiohttp для HEAD-запросов (только для posts).

    Returns:
        List[Any]: Список обработанных объектов (DanbooruPost или Tuple[str, int]).

    Raises:
        ValueError: Если указан неподдерживаемый тип данных.
    """
    def process_post(item: Dict[str, Any]) -> Optional[DanbooruPost]:
        if not all(key in item for key in ["id", "file_url", "preview_file_url", "tag_string", "rating"]):
            return None
        post_id = item["id"]
        if post_id in used_post_ids:
            logger.debug(f"Пропущен дубликат поста с ID {post_id}")
            return None
        return DanbooruPost(
            id=post_id,
            file_url=item["file_url"] or "",
            preview_url=item["preview_file_url"] or "",
            tags=item["tag_string"].split(),
            rating=item["rating"],
            source=item.get("source"),
            created_at=item.get("created_at", "Неизвестно")
        )

    def process_tag(item: Dict[str, Any]) -> Optional[Tuple[str, int, str]]:
        if not isinstance(item, dict) or "name" not in item or "post_count" not in item:
            logger.warning(f"Пропущен некорректный элемент тега: {item}")
            return None
        tag_name = item["name"]
        post_count = item["post_count"]
        if not isinstance(post_count, int) or post_count < 0:
            logger.warning(f"Некорректное значение post_count для тега '{tag_name}': {post_count}")
            return None
        formatted_count = f"{tag_name} ({format_post_count(post_count)})"
        return (tag_name, post_count, formatted_count)

    async def check_file_info(url: str) -> None:
        if url in file_info_cache:
            return
        try:
            async with session.head(url, timeout=REQUEST_TIMEOUT) as response:
                if response.status == 200:
                    content_type = response.headers.get('Content-Type', 'application/octet-stream')
                    content_length = response.headers.get('Content-Length')
                    file_size = int(content_length) if content_length else None
                    file_info_cache[url] = (content_type, file_size)
                else:
                    file_info_cache[url] = ('unknown', None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.debug(f"Ошибка проверки файла для {url}: {e}")
            file_info_cache[url] = ('unknown', None)

    start_time = asyncio.get_event_loop().time()
    results = []

    if data_type not in ('posts', 'tags'):
        raise ValueError(f"Неподдерживаемый тип данных: {data_type}")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        if data_type == 'posts':
            loop = asyncio.get_event_loop()
            tasks = [loop.run_in_executor(executor, process_post, item) for item in data]
            results = await asyncio.gather(*tasks)
            results = [r for r in results if r is not None]
            if session and results:
                mime_tasks = [check_file_info(post.file_url) for post in results if post.file_url]
                await asyncio.gather(*mime_tasks, return_exceptions=True)
        else:
            loop = asyncio.get_event_loop()
            tasks = [loop.run_in_executor(executor, process_tag, item) for item in data]
            results = await asyncio.gather(*tasks)
            results = [r for r in results if r is not None]

    logger.debug(f"Обработка {len(data)} элементов ({data_type}) выполнена за {asyncio.get_event_loop().time() - start_time:.2f} сек")
    return results

class PageInputModal(Modal, title="Перейти к странице"):
    """Модальное окно для ввода номера страницы в навигации."""
    def __init__(self, view: 'NavigationView'):
        super().__init__()
        self.view = view
        self.page_input = TextInput(
            label="Номер страницы",
            placeholder="Введите номер страницы (например, 123)",
            required=True,
            min_length=1,
            max_length=10
        )
        self.add_item(self.page_input)

    async def on_submit(self, interaction: Interaction) -> None:
        """Обрабатывает ввод номера страницы."""
        if interaction.user != self.view.original_user:
            await interaction.response.send_message(
                "Только пользователь, вызвавший команду, может с ней взаимодействовать.",
                ephemeral=True
            )
            return

        if self.view.is_loading:
            await interaction.response.send_message("Подождите, идет загрузка.", ephemeral=True)
            return

        try:
            target_page = int(self.page_input.value)
            if target_page < 1 or target_page > self.view.total_pages:
                await interaction.response.send_message(
                    f"Введите число от 1 до {self.view.total_pages}.", ephemeral=True
                )
                return
        except ValueError:
            await interaction.response.send_message("Введите корректное число.", ephemeral=True)
            return

        self.view.is_loading = True
        self.view.loading_button = "page"
        self.view.last_interaction = asyncio.get_event_loop().time()
        self.view.start_inactivity_timer()
        self.view.update_buttons()
        try:
            await interaction.response.edit_message(view=self.view)
        except discord.errors.NotFound as e:
            logger.error(f"Взаимодействие не найдено при обновлении кнопок: {e}")
            self.view.is_loading = False
            self.view.loading_button = None
            self.view.update_buttons()
            await interaction.followup.send("Взаимодействие устарело. Попробуйте снова.", ephemeral=True)
            return

        try:
            await interaction.response.defer()
        except discord.errors.InteractionResponded:
            pass
        except discord.errors.NotFound as e:
            logger.error(f"Взаимодействие не найдено: {e}")
            self.view.is_loading = False
            self.view.loading_button = None
            self.view.update_buttons()
            await interaction.followup.send("Взаимодействие устарело. Попробуйте снова.", ephemeral=True)
            return

        if await self.view.load_page(target_page, reset_chunk=True):
            self.view.is_loading = False
            self.view.loading_button = None
            self.view.update_buttons()
            try:
                content, image_urls, skipped_posts = await self.view.create_message()
                files = await self.view.fetch_images(image_urls, interaction, skipped_posts)
                await interaction.edit_original_response(content=content, view=self.view, attachments=files)
            except discord.HTTPException as e:
                logger.error(f"Ошибка HTTP при обновлении сообщения: {e}")
                await interaction.followup.send(
                    "Не удалось обновить сообщение из-за слишком больших файлов. Попробуйте снова.",
                    ephemeral=True
                )
            except discord.DiscordException as e:
                logger.error(f"Ошибка при обновлении сообщения: {e}")
                await interaction.followup.send(
                    "Не удалось обновить сообщение. Попробуйте снова.",
                    ephemeral=True
                )
        else:
            self.view.is_loading = False
            self.view.loading_button = None
            self.view.update_buttons()
            await interaction.followup.send("Не удалось загрузить страницу. Попробуйте снова.", ephemeral=True)

class NavigationView(View):
    """View для навигации по чанкам и страницам Danbooru."""
    def __init__(
        self,
        results: List[DanbooruPost],
        original_user: discord.User,
        query: Optional[str],
        current_page: int,
        total_pages: int,
        max_file_size: int,
        current_chunk: int = 0,
        timeout: int = VIEW_TIMEOUT
    ):
        super().__init__(timeout=timeout)
        self.results = results
        self.original_user = original_user
        self.query = query
        self.current_page = current_page
        self.total_pages = total_pages
        self.max_file_size = max_file_size
        self.current_chunk = current_chunk
        self.is_loading = False
        self.loading_button: Optional[str] = None
        self.page_cache: Dict[int, List[DanbooruPost]] = {}  # Хранит все посты страницы
        self.last_interaction = asyncio.get_event_loop().time()
        self.inactivity_timeout = INACTIVITY_TIMEOUT
        self.inactivity_task: Optional[asyncio.Task] = None
        self.message: Optional[discord.Message] = None
        self.update_buttons()
        self.start_inactivity_timer()

    def start_inactivity_timer(self) -> None:
        """Запускает таймер бездействия для отключения кнопок."""
        if self.inactivity_task:
            self.inactivity_task.cancel()
        self.inactivity_task = asyncio.create_task(self.check_inactivity())
        logger.debug(f"Таймер бездействия запущен с таймаутом {self.inactivity_timeout} секунд")

    async def check_inactivity(self) -> None:
        """Отключает кнопки после 1 часа бездействия."""
        try:
            await asyncio.sleep(self.inactivity_timeout)
            logger.debug("Таймер бездействия сработал: отключаем кнопки")
            self.disable_navigation_buttons()
            if self.message:
                try:
                    await self.message.edit(view=self)
                    logger.debug("Кнопки успешно отключены в сообщении")
                except discord.HTTPException as e:
                    logger.error(f"Ошибка при отключении кнопок в сообщении: {e}")
        except asyncio.CancelledError:
            logger.debug("Таймер бездействия отменён")
        except Exception as e:
            logger.error(f"Ошибка в таймере бездействия: {e}")

    def disable_navigation_buttons(self) -> None:
        """Отключает все кнопки навигации."""
        for item in self.children:
            if isinstance(item, Button):
                item.disabled = True
        logger.debug("Все кнопки навигации отключены")

    def _create_button(self, label: str, style: ButtonStyle, disabled: bool, callback=None, url: Optional[str] = None):
        """Создает кнопку с заданными параметрами."""
        button = Button(label=label, style=style, disabled=disabled, url=url)
        if callback:
            button.callback = callback
        return button

    def update_buttons(self) -> None:
        """Обновляет состояние кнопок навигации."""
        self.clear_items()
        max_chunks = math.ceil(len(self.results) / POSTS_PER_CHUNK) if self.results else 1
        if self.is_loading:
            back_label = "⌛" if self.loading_button == "back" else "⬅️"
            self.add_item(self._create_button(
                label=back_label,
                style=ButtonStyle.gray,
                disabled=True,
                callback=lambda i: self.navigate(i, -1, "back")
            ))
            next_label = "⌛" if self.loading_button == "next" else "➡️"
            self.add_item(self._create_button(
                label=next_label,
                style=ButtonStyle.gray,
                disabled=True,
                callback=lambda i: self.navigate(i, 1, "next")
            ))
            page_label = "⌛" if self.loading_button == "page" else "📄"
            self.add_item(self._create_button(
                label=page_label,
                style=ButtonStyle.blurple,
                disabled=True,
                callback=self.open_page_modal
            ))
        else:
            self.add_item(self._create_button(
                label="⬅️",
                style=ButtonStyle.gray,
                disabled=(self.current_chunk == 0 and self.current_page == 1),
                callback=lambda i: self.navigate(i, -1, "back")
            ))
            self.add_item(self._create_button(
                label="➡️",
                style=ButtonStyle.gray,
                disabled=(self.current_chunk >= max_chunks - 1 and self.current_page >= self.total_pages),
                callback=lambda i: self.navigate(i, 1, "next")
            ))
            self.add_item(self._create_button(
                label="📄",
                style=ButtonStyle.blurple,
                disabled=False,
                callback=self.open_page_modal
            ))

    async def open_page_modal(self, interaction: Interaction) -> None:
        """Открывает модальное окно для ввода номера страницы."""
        if interaction.user != self.original_user:
            await interaction.response.send_message(
                "Только пользователь, вызвавший команду, может с ней взаимодействовать.",
                ephemeral=True
            )
            return
        if self.is_loading:
            await interaction.response.send_message("Подождите, идет загрузка.", ephemeral=True)
            return
        self.last_interaction = asyncio.get_event_loop().time()
        self.start_inactivity_timer()
        modal = PageInputModal(self)
        await interaction.response.send_modal(modal)

    async def navigate(self, interaction: Interaction, direction: int, button: str) -> None:
        """Обрабатывает навигацию по чанкам и страницам."""
        if interaction.user != self.original_user:
            await interaction.response.send_message(
                "Только пользователь, вызвавший команду, может с ней взаимодействовать.",
                ephemeral=True
            )
            return
        if self.is_loading:
            return
        self.is_loading = True
        self.loading_button = button
        self.last_interaction = asyncio.get_event_loop().time()
        self.start_inactivity_timer()
        self.update_buttons()
        try:
            await interaction.response.edit_message(view=self)
        except discord.errors.NotFound as e:
            logger.error(f"Взаимодействие не найдено при обновлении кнопок: {e}")
            self.is_loading = False
            self.loading_button = None
            self.update_buttons()
            await interaction.followup.send("Взаимодействие устарело. Попробуйте снова.", ephemeral=True)
            return

        try:
            await interaction.response.defer()
        except discord.errors.InteractionResponded:
            pass
        except discord.errors.NotFound as e:
            logger.error(f"Взаимодействие не найдено: {e}")
            self.is_loading = False
            self.loading_button = None
            self.update_buttons()
            await interaction.followup.send("Взаимодействие устарело. Попробуйте снова.", ephemeral=True)
            return

        max_chunks = math.ceil(len(self.results) / POSTS_PER_CHUNK) if self.results else 1
        new_chunk = self.current_chunk + direction
        success = True
        if new_chunk < 0 and self.current_page > 1:
            success = await self.load_page(self.current_page - 1, reset_chunk=False)
            if success:
                self.current_chunk = math.ceil(len(self.results) / POSTS_PER_CHUNK) - 1 if self.results else 0
            else:
                self.current_chunk = 0
        elif new_chunk >= max_chunks and self.current_page < self.total_pages:
            success = await self.load_page(self.current_page + 1, reset_chunk=True)
            if success:
                self.current_chunk = 0
            else:
                self.current_chunk = max_chunks - 1
        else:
            self.current_chunk = max(0, min(new_chunk, max_chunks - 1))

        self.is_loading = False
        self.loading_button = None
        self.update_buttons()
        try:
            content, image_urls, skipped_posts = await self.create_message()
            files = await self.fetch_images(image_urls, interaction, skipped_posts)
            await interaction.edit_original_response(content=content, view=self, attachments=files)
        except discord.HTTPException as e:
            logger.error(f"Ошибка HTTP при обновлении сообщения: {e}")
            await interaction.followup.send(
                "Не удалось обновить сообщение из-за слишком больших файлов. Попробуйте снова.",
                ephemeral=True
            )
        except discord.DiscordException as e:
            logger.error(f"Ошибка при обновлении сообщения: {e}")
            await interaction.followup.send(
                "Не удалось обновить сообщение. Попробуйте снова.",
                ephemeral=True
            )

    async def fetch_images(
        self,
        image_urls: List[str],
        interaction: Interaction,
        skipped_posts: List[DanbooruPost]
    ) -> List[discord.File]:
        """Загружает файлы по URL с проверкой размера и MIME-типа."""
        files = []
        new_skipped_posts = skipped_posts.copy()
        semaphore = asyncio.Semaphore(SEMAPHORE_LIMIT)

        async def fetch_single_image(idx: int, url: str) -> Tuple[Optional[discord.File], Optional[DanbooruPost]]:
            async with semaphore:
                try:
                    content_type, file_size = file_info_cache.get(url, ('unknown', None))
                    if content_type not in SUPPORTED_MIME_TYPES or (file_size is not None and file_size > self.max_file_size):
                        logger.debug(f"Пропущен файл {url} из кэша: MIME={content_type}, размер={file_size}")
                        return None, self.results[self.current_chunk * POSTS_PER_CHUNK + idx]

                    async with aiohttp_session() as session:
                        async with session.get(url, timeout=REQUEST_TIMEOUT) as response:
                            if response.status == 200:
                                image_data = await response.read()
                                if len(image_data) > self.max_file_size:
                                    logger.debug(f"Файл {url} слишком большой ({len(image_data)} байт)")
                                    return None, self.results[self.current_chunk * POSTS_PER_CHUNK + idx]
                                content_type = response.headers.get('Content-Type', 'application/octet-stream')
                                if content_type not in SUPPORTED_MIME_TYPES:
                                    logger.debug(f"Пропущен файл {url} с неподдерживаемым MIME-типом: {content_type}")
                                    return None, self.results[self.current_chunk * POSTS_PER_CHUNK + idx]
                                extension = SUPPORTED_MIME_TYPES[content_type]
                                post = self.results[self.current_chunk * POSTS_PER_CHUNK + idx]
                                file = discord.File(
                                    fp=io.BytesIO(image_data),
                                    filename=f"post_{post.id}{extension}"
                                )
                                return file, None
                            else:
                                logger.error(f"Ошибка загрузки файла {url}: HTTP {response.status}")
                                return None, self.results[self.current_chunk * POSTS_PER_CHUNK + idx]
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logger.error(f"Ошибка загрузки файла {url}: {e}")
                    return None, self.results[self.current_chunk * POSTS_PER_CHUNK + idx]

        tasks = [fetch_single_image(idx, url) for idx, url in enumerate(image_urls)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Ошибка в задаче загрузки изображения: {result}")
                continue
            try:
                file, skipped_post = result
                if file:
                    files.append(file)
                elif skipped_post:
                    new_skipped_posts.append(skipped_post)
            except ValueError as e:
                logger.error(f"Некорректный результат задачи загрузки: {result}, ошибка: {e}")
                continue

        return files

    async def fill_chunk(self, chunk_posts: List[DanbooruPost]) -> List[DanbooruPost]:
        """Дозаполняет чанк до 10 постов, загружая посты с последующих страниц."""
        needed_posts = POSTS_PER_CHUNK - len(chunk_posts)
        if needed_posts <= 0:
            return chunk_posts

        current_page = self.current_page
        new_posts = []
        while needed_posts > 0 and current_page < self.total_pages:
            current_page += 1
            if current_page in self.page_cache:
                page_posts = self.page_cache[current_page]
            else:
                try:
                    async with aiohttp_session() as session:
                        page_posts = await fetch_danbooru_posts(session, self.query, current_page)
                        if not page_posts:
                            break
                        page_posts = filter_duplicates(page_posts)
                        self.page_cache[current_page] = page_posts
                except DanbooruAPIError as e:
                    logger.error(f"Ошибка дозаполнения чанка со страницы {current_page}: {e}")
                    break

            # Фильтруем дубликаты
            for post in page_posts:
                if post.id not in used_post_ids:
                    new_posts.append(post)
                    used_post_ids.add(post.id)
                    needed_posts -= 1
                    if needed_posts <= 0:
                        break

        logger.debug(f"Дозаполнено {len(new_posts)} постов для чанка, страница {self.current_page}, чанк {self.current_chunk + 1}")
        return chunk_posts + new_posts[:POSTS_PER_CHUNK - len(chunk_posts)]

    async def load_page(self, target_page: int, reset_chunk: bool = False) -> bool:
        """Загружает результаты для указанной страницы с использованием кэша."""
        if target_page in self.page_cache:
            self.results = self.page_cache[target_page]
            self.current_page = target_page
            if reset_chunk:
                self.current_chunk = 0
            return True

        try:
            async with aiohttp_session() as session:
                posts = await fetch_danbooru_posts(session, self.query, target_page)
                if not posts:
                    return False
                posts = filter_duplicates(posts)
                self.results = posts
                self.page_cache[target_page] = posts
                self.current_page = target_page
                if reset_chunk:
                    self.current_chunk = 0
                return True
        except DanbooruAPIError as e:
            logger.error(f"Ошибка загрузки страницы {target_page}: {e}")
            return False

    async def create_message(self) -> Tuple[str, List[str], List[DanbooruPost]]:
        """Создает сообщение и список URL файлов для текущего чанка."""
        start_idx = self.current_chunk * POSTS_PER_CHUNK
        end_idx = min(start_idx + POSTS_PER_CHUNK, len(self.results))
        chunk_posts = self.results[start_idx:end_idx]

        # Дозаполняем чанк, если постов меньше 10
        chunk_posts = await self.fill_chunk(chunk_posts)
        if len(chunk_posts) > POSTS_PER_CHUNK:
            chunk_posts = chunk_posts[:POSTS_PER_CHUNK]

        max_chunks = math.ceil(len(self.results) / POSTS_PER_CHUNK) if self.results else 1
        if len(chunk_posts) < POSTS_PER_CHUNK and self.current_page >= self.total_pages:
            max_chunks = self.current_chunk + 1

        message_lines = [f"**Чанк {self.current_chunk + 1}/{max_chunks} | Страница {self.current_page}/{self.total_pages}**"]
        image_urls = []
        skipped_posts = []

        for post in chunk_posts:
            used_post_ids.add(post.id)  # Добавляем ID в глобальный кэш
            if not post.file_url:
                skipped_posts.append(post)
                continue
            content_type, file_size = file_info_cache.get(post.file_url, ('unknown', None))
            if content_type not in SUPPORTED_MIME_TYPES or (file_size is not None and file_size > self.max_file_size):
                skipped_posts.append(post)
            else:
                image_urls.append(post.file_url)

        if skipped_posts:
            skipped_message = "Файл слишком велик или формат не поддерживается: " + ", ".join(
                [f"[Пост #{post.id}]({post.file_url})" for post in skipped_posts if post.file_url]
            )
            message_lines.append(skipped_message)

        message = "\n".join(message_lines)
        logger.debug(f"Сформирован чанк {self.current_chunk + 1}/{max_chunks}: {len(image_urls)} файлов, {len(skipped_posts)} пропущено")
        return message, image_urls, skipped_posts

    async def on_timeout(self) -> None:
        """Обрабатывает таймаут view, очищая кэш."""
        self.clear_items()
        self.page_cache.clear()
        if self.inactivity_task:
            self.inactivity_task.cancel()
        if self.message:
            try:
                await self.message.edit(view=None)
                logger.debug("Кнопки удалены из сообщения по таймауту")
            except discord.HTTPException as e:
                if e.code == 50027:  # Invalid Webhook Token
                    logger.debug("Не удалось отредактировать сообщение: недействительный токен вебхука")
                else:
                    logger.error(f"Ошибка при удалении кнопок по таймауту: {e}")

        global file_info_cache, tag_suggestions_cache, used_post_ids
        file_info_cache.clear()
        tag_suggestions_cache.clear()
        used_post_ids.clear()

@asynccontextmanager
async def aiohttp_session():
    """Контекстный менеджер для сессии aiohttp с оптимизированными настройками."""
    timeout = aiohttp.ClientTimeout(total=20, connect=10)
    connector = aiohttp.TCPConnector(limit=50)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        yield session

def filter_duplicates(posts: List[DanbooruPost]) -> List[DanbooruPost]:
    """Удаляет дубликаты постов на основе их идентификаторов."""
    seen_ids = set()
    unique_posts = []
    for post in posts:
        if post.id not in seen_ids and post.id not in used_post_ids:
            seen_ids.add(post.id)
            unique_posts.append(post)
    return unique_posts

@backoff.on_exception(
    backoff.expo,
    (aiohttp.ClientError, asyncio.TimeoutError, DanbooruAPIError),
    max_tries=3,
    max_time=10
)
async def fetch_post_count(session: aiohttp.ClientSession, tags: Optional[str]) -> int:
    """Получает общее количество постов для заданных тегов."""
    url = "https://danbooru.donmai.us/counts/posts.json"
    params = {}
    if tags:
        params["tags"] = tags.strip()

    async with session.get(url, params=params) as response:
        rate_limit_remaining = response.headers.get('X-Rate-Limit-Remaining', 'unknown')
        logger.debug(f"Rate-Limit-Remaining for /counts/posts.json: {rate_limit_remaining}")
        if response.status != 200:
            error_map = {
                403: "Доступ к API ограничен.",
                429: "Превышен лимит запросов.",
                500: "Внутренняя ошибка сервера Danbooru."
            }
            response_text = await response.text()
            logger.error(f"API Error: {error_map.get(response.status, f'Неизвестная ошибка API: Код {response.status}')}, Response: {response_text[:200]}")
            raise DanbooruAPIError(error_map.get(response.status, f"Неизвестная ошибка API: Код {response.status}"))
        
        data = await response.json()
        if not isinstance(data, dict) or "counts" not in data or "posts" not in data["counts"]:
            raise DanbooruAPIError("Неправильный формат ответа от API")
        
        return data["counts"]["posts"]

@backoff.on_exception(
    backoff.expo,
    (aiohttp.ClientError, asyncio.TimeoutError, DanbooruAPIError),
    max_tries=3,
    max_time=10
)
async def fetch_danbooru_posts(session: aiohttp.ClientSession, tags: Optional[str], page: int) -> List[DanbooruPost]:
    """Получает посты из Danbooru API."""
    start_time = asyncio.get_event_loop().time()
    base_url = "https://danbooru.donmai.us/posts.json"
    params = {"page": page, "limit": POSTS_PER_PAGE}
    if tags:
        params["tags"] = tags.strip()

    async with session.get(base_url, params=params) as response:
        rate_limit_remaining = response.headers.get('X-Rate-Limit-Remaining', 'unknown')
        logger.debug(f"Rate-Limit-Remaining for /posts.json: {rate_limit_remaining}")
        if response.status != 200:
            error_map = {
                403: "Доступ к API ограничен.",
                429: "Превышен лимит запросов.",
                500: "Внутренняя ошибка сервера Danbooru."
            }
            response_text = await response.text()
            logger.error(f"API Error: {error_map.get(response.status, f'Неизвестная ошибка API: Код {response.status}')}, Response: {response_text[:200]}")
            raise DanbooruAPIError(error_map.get(response.status, f"Неизвестная ошибка API: Код {response.status}"))
        
        data = await response.json()
        if not isinstance(data, list):
            raise DanbooruAPIError("Неправильный формат ответа от API")

        posts = await process_api_data(data, 'posts', session)
        logger.debug(f"Загрузка постов (страница {page}, теги: {tags}) выполнена за {asyncio.get_event_loop().time() - start_time:.2f} сек")
        return posts

@backoff.on_exception(
    backoff.expo,
    (aiohttp.ClientError, asyncio.TimeoutError, DanbooruAPIError),
    max_tries=3,
    max_time=10
)
async def fetch_tag_suggestions(session: aiohttp.ClientSession, query: str) -> List[Tuple[str, int, str]]:
    """Получает предложения тегов через Danbooru Tags API."""
    global tag_suggestions_cache
    query = query.strip().lower()
    cache_key = query or "__all_tags__"

    if cache_key in tag_suggestions_cache:
        logger.debug(f"Использован кэш для запроса '{query}': {len(tag_suggestions_cache[cache_key])} тегов")
        return tag_suggestions_cache[cache_key]

    start_time = asyncio.get_event_loop().time()
    url = "https://danbooru.donmai.us/tags.json"
    params = {
        "search[hide_empty]": "yes",
        "search[order]": "count"
    }
    if query:
        params["search[name_matches]"] = f"{query}*"
    else:
        params["limit"] = 100

    logger.debug(f"Запрос тегов с параметрами: {params}")

    async with session.get(url, params=params, timeout=REQUEST_TIMEOUT) as response:
        rate_limit_remaining = response.headers.get('X-Rate-Limit-Remaining', 'unknown')
        logger.debug(f"Rate-Limit-Remaining for /tags.json: {rate_limit_remaining}")
        if response.status != 200:
            error_map = {
                403: "Доступ к API ограничен.",
                429: "Превышен лимит запросов.",
                500: "Внутренняя ошибка сервера Danbooru."
            }
            response_text = await response.text()
            logger.error(f"API Error: {error_map.get(response.status, f'Неизвестная ошибка API: Код {response.status}')}, Response: {response_text[:200]}")
            raise DanbooruAPIError(error_map.get(response.status, f"Неизвестная ошибка API: Код {response.status}"))

        data = await response.json()
        if not isinstance(data, list):
            logger.error(f"Неправильный формат ответа от /tags.json: {data}")
            raise DanbooruAPIError("Неправильный формат ответа от API")

        tags = await process_api_data(data, 'tags')
        tag_suggestions_cache[cache_key] = tags
        logger.debug(f"Кэшировано {len(tags)} тегов для запроса '{query}' за {asyncio.get_event_loop().time() - start_time:.2f} сек")
        return tags

async def tags_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    """Обработчик автодополнения для тегов."""
    start_time = asyncio.get_event_loop().time()
    try:
        if len(current) > MAX_QUERY_LENGTH:
            logger.debug(f"Запрос автодополнения '{current}' слишком длинный")
            return []

        tags = current.strip().split()
        query = tags[-1] if tags else ""
        cache_key = query.lower() or "__all_tags__"

        if cache_key in tag_suggestions_cache:
            suggestions = tag_suggestions_cache[cache_key]
            logger.debug(f"Использован кэш для автодополнения '{query}': {len(suggestions)} тегов")
        else:
            async with aiohttp_session() as session:
                suggestions = await fetch_tag_suggestions(session, query)

        prefix = ' '.join(tags[:-1]) + ' ' if tags[:-1] else ''
        choices = [
            app_commands.Choice(
                name=formatted_count,
                value=f"{prefix}{tag}".strip()
            )
            for tag, _, formatted_count in suggestions[:25]
        ]

        logger.debug(f"Автодополнение для '{current}' выполнено за {asyncio.get_event_loop().time() - start_time:.2f} сек")
        return choices
    except Exception as e:
        logger.error(f"Ошибка автодополнения тегов для '{current}': {e}")
        return []

async def danbooru(interaction: discord.Interaction, bot_client, tags: Optional[str] = None) -> None:
    """Слеш-команда для поиска постов на Danbooru."""
    guild_id = str(interaction.guild.id) if interaction.guild else "ЛС"
    channel_id = str(interaction.channel.id) if interaction.channel else "ЛС"
    logger.debug(f"Команда /danbooru вызвана пользователем {interaction.user.id} в гильдии {guild_id}, канал {channel_id}, теги: {tags}")

    # Проверка ограничений команды
    result, reason = await restrict_command_execution(interaction, bot_client)
    if not result:
        await interaction.response.send_message(reason or "Конфигурация сервера не найдена!", ephemeral=True)
        return

    # Проверка доступа бота
    result, reason = await check_bot_access(interaction, bot_client)
    if not result:
        await interaction.response.send_message(reason, ephemeral=True)
        return

    # Проверка NSFW-канала
    if interaction.guild and not interaction.channel.nsfw:
        await interaction.response.send_message("Команда доступна только в NSFW-каналах или ЛС.", ephemeral=True)
        return

    # Проверка ограничений пользователя
    if interaction.guild:
        restriction, reason = await checker.check_user_restriction(interaction)
        if not restriction:
            await interaction.response.send_message(reason or "Ваш доступ ограничен.", ephemeral=True)
            return

    # Проверка корректности тегов
    if tags and any(tag.strip() == "" for tag in tags.split()):
        await interaction.response.send_message("Теги содержат пустые значения.", ephemeral=True)
        return

    # Определение максимального размера файла на основе уровня подписки гильдии
    if interaction.guild:
        if interaction.guild.premium_tier == 2:
            max_file_size = MAX_FILE_SIZE_TIER_2
        elif interaction.guild.premium_tier == 3:
            max_file_size = MAX_FILE_SIZE_TIER_3
        else:
            max_file_size = MAX_FILE_SIZE_DEFAULT
    else:
        max_file_size = MAX_FILE_SIZE_DEFAULT

    try:
        await interaction.response.defer(ephemeral=False)
    except discord.errors.NotFound as e:
        logger.error(f"Взаимодействие не найдено при defer: {e}")
        await interaction.followup.send("Взаимодействие устарело.", ephemeral=True)
        return

    try:
        async with aiohttp_session() as session:
            # Параллельная загрузка количества постов и постов первой страницы
            total_posts_task = fetch_post_count(session, tags)
            posts_task = fetch_danbooru_posts(session, tags, page=1)
            total_posts, posts = await asyncio.gather(total_posts_task, posts_task, return_exceptions=True)

            if isinstance(total_posts, Exception):
                raise DanbooruAPIError(f"Ошибка получения количества постов: {total_posts}")
            if isinstance(posts, Exception):
                raise DanbooruAPIError(f"Ошибка получения постов: {posts}")

            # Вычисление общего количества страниц
            total_pages = min(1000, math.ceil(total_posts / POSTS_PER_PAGE)) if total_posts > 0 else 1
            if not posts:
                await interaction.followup.send(
                    f"Посты по тегам '{tags or 'без тегов'}' не найдены.",
                    ephemeral=False
                )
                return

            # Удаление дубликатов и добавление ID в глобальный кэш
            posts = filter_duplicates(posts)
            for post in posts:
                used_post_ids.add(post.id)

            # Создание представления для навигации
            view = NavigationView(
                posts,
                interaction.user,
                tags,
                current_page=1,
                total_pages=total_pages,
                max_file_size=max_file_size
            )
            view.page_cache[1] = posts
            content, image_urls, skipped_posts = await view.create_message()
            files = await view.fetch_images(image_urls, interaction, skipped_posts)
            message = await interaction.followup.send(content=content, view=view, files=files, ephemeral=False)
            view.message = message

    except DanbooruAPIError as e:
        logger.error(f"Ошибка Danbooru API для тегов '{tags}': {e}")
        await interaction.followup.send(f"Не удалось получить посты: {str(e)}", ephemeral=False)
    except Exception as e:
        logger.error(f"Неизвестная ошибка в /danbooru для тегов '{tags}': {e}")
        await interaction.followup.send("Произошла ошибка. Попробуйте позже.", ephemeral=False)

def create_command(bot_client) -> app_commands.Command:
    """Создает слеш-команду /danbooru с автодополнением тегов."""
    @app_commands.command(name="danbooru", description="Поиск постов на Danbooru по тегам")
    @app_commands.describe(tags="Теги для поиска (например, 'blue_archive')")
    @app_commands.autocomplete(tags=tags_autocomplete)
    @app_commands.checks.cooldown(rate=COOLDOWN_RATE, per=COOLDOWN_TIME)
    async def wrapper(interaction: discord.Interaction, tags: Optional[str] = None) -> None:
        await danbooru(interaction, bot_client, tags)

    @wrapper.error
    async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        guild_id = str(interaction.guild.id) if interaction.guild else "ЛС"
        channel_id = str(interaction.channel.id) if interaction.channel else "ЛС"
        tags = getattr(interaction.namespace, 'tags', None)
        
        if isinstance(error, app_commands.CommandOnCooldown):
            retry_after = int(error.retry_after)
            logger.debug(f"Кулдаун /danbooru для {interaction.user.id} (гильдия: {guild_id}, канал: {channel_id}, теги: {tags}, осталось: {retry_after} сек)")
            await interaction.response.send_message(
                f"Команда на кулдауне. Попробуйте снова через {retry_after} секунд.",
                ephemeral=True
            )
            return

        logger.error(f"Ошибка в /danbooru для {interaction.user.id} (гильдия: {guild_id}, канал: {channel_id}, теги: {tags}): {error}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("Ошибка команды.", ephemeral=True)
            else:
                await interaction.followup.send("Ошибка команды.", ephemeral=True)
        except discord.DiscordException as e:
            logger.error(f"Не удалось отправить сообщение об ошибке: {e}")

    return wrapper