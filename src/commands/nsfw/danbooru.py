import discord
from discord import app_commands, ButtonStyle, Interaction
from discord.ui import Button, View, Modal, TextInput
import aiohttp
import io
import math
from typing import List, Optional, Dict, Tuple
from dataclasses import dataclass
import asyncio
from contextlib import asynccontextmanager
import backoff
import logging
from aiohttp import web
import html
from ...systemLog import logger
from ...utils.checker import checker
from ..restrict import check_bot_access, restrict_command_execution

# Отключаем логирование aiohttp.access
aiohttp_access_logger = logging.getLogger("aiohttp.access")
aiohttp_access_logger.propagate = False
aiohttp_access_logger.addHandler(logging.NullHandler())

# Пользовательские исключения
class DanbooruAPIError(Exception):
    """Исключение для ошибок Danbooru API."""
    pass

# Структурированные данные
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
web_server: Optional[web.TCPSite] = None
skipped_posts_global: List[DanbooruPost] = []
content_type_cache: Dict[str, str] = {}  # Кэш для Content-Type по file_url
server_lock = asyncio.Lock()

# MIME-типы и соответствующие расширения
MIME_TO_EXTENSION = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/gif': '.gif',
    'video/mp4': '.mp4',
    'video/webm': '.webm'
}

# HTML-страница для отображения больших файлов
HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Danbooru Large Files</title>
    <style>
        body {{
            font-family: system-ui, -apple-system, sans-serif;
            margin: 1rem;
            background-color: #f4f4f4;
            line-height: 1.5;
        }}
        h1 {{
            font-size: 1.5rem;
            color: #333;
            margin-bottom: 1rem;
        }}
        .post {{
            margin: 1rem 0;
            border: 1px solid #ddd;
            padding: 0.75rem;
            border-radius: 0.5rem;
            background-color: #fff;
        }}
        .post-id {{
            font-weight: 600;
            font-size: 1.1rem;
            color: #1a73e8;
        }}
        .tags {{
            color: #666;
            font-size: 0.85rem;
            margin-top: 0.5rem;
            word-break: break-word;
        }}
        img, video {{
            max-width: 100%;
            height: auto;
            margin-top: 0.5rem;
            border-radius: 0.25rem;
            object-fit: contain;
        }}
        a {{
            color: #1a73e8;
            text-decoration: none;
            font-size: 1rem;
            padding: 0.5rem;
            display: inline-block;
        }}
        a:hover, a:focus {{
            text-decoration: underline;
            background-color: #e8f0fe;
        }}
        #to-top {{
            position: fixed;
            bottom: 1rem;
            right: 1rem;
            background-color: #1a73e8;
            color: #fff;
            border: none;
            border-radius: 50%;
            width: 3rem;
            height: 3rem;
            font-size: 1rem;
            cursor: pointer;
            display: none;
            align-items: center;
            justify-content: center;
        }}
        #to-top.visible {{
            display: flex;
        }}
        @media (max-width: 600px) {{
            body {{
                margin: 0.5rem;
            }}
            h1 {{
                font-size: 1.25rem;
            }}
            .post {{
                padding: 0.5rem;
            }}
            .post-id {{
                font-size: 1rem;
            }}
            .tags {{
                font-size: 0.8rem;
            }}
        }}
    </style>
</head>
<body>
    <h1>Danbooru Large Files</h1>
    {posts}
    <button id="to-top" title="Наверх">↑</button>
    <script>
        const toTopBtn = document.getElementById('to-top');
        window.addEventListener('scroll', () => {{
            toTopBtn.classList.toggle('visible', window.scrollY > 300);
        }});
        toTopBtn.addEventListener('click', () => {{
            window.scrollTo({{ top: 0, behavior: 'smooth' }});
        }});
    </script>
</body>
</html>
"""

# Модальное окно для ввода номера страницы
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
                content, image_urls, skipped_posts = self.view.create_message()
                files = await self.view.fetch_images(image_urls, interaction, skipped_posts)
                await interaction.edit_original_response(content=content, view=self.view, attachments=files)
            except discord.HTTPException as e:
                logger.error(f"Ошибка HTTP при обновлении сообщения: {e}")
                await interaction.followup.send("Не удалось обновить сообщение из-за слишком больших файлов. Попробуйте снова.", ephemeral=True)
            except discord.DiscordException as e:
                logger.error(f"Ошибка при обновлении сообщения: {e}")
                await interaction.followup.send("Не удалось обновить сообщение. Попробуйте снова.", ephemeral=True)
        else:
            self.view.is_loading = False
            self.view.loading_button = None
            self.view.update_buttons()
            await interaction.followup.send("Не удалось загрузить страницу. Попробуйте снова.", ephemeral=True)

# Класс для навигации по результатам поиска
class NavigationView(View):
    """View для навигации по чанкам и страницам Danbooru."""
    def __init__(
        self,
        results: List[DanbooruPost],
        original_user: discord.User,
        query: Optional[str],
        current_page: int,
        total_pages: int,
        current_chunk: int = 0,
        timeout: int = 300
    ):
        super().__init__(timeout=timeout)
        self.results = results
        self.original_user = original_user
        self.query = query
        self.current_page = current_page
        self.total_pages = total_pages
        self.current_chunk = current_chunk
        self.is_loading = False
        self.loading_button: Optional[str] = None
        self.message_cache: Dict[Tuple[int, int], Tuple[str, List[str], List[DanbooruPost]]] = {}
        self.page_cache: Dict[int, List[DanbooruPost]] = {}  # Кэш для страниц
        self.last_interaction = asyncio.get_event_loop().time()
        self.inactivity_timeout = 120
        self.inactivity_task: Optional[asyncio.Task] = None
        self.message: Optional[discord.Message] = None
        self.update_buttons()
        self.start_inactivity_timer()

    def start_inactivity_timer(self) -> None:
        """Запускает таймер бездействия."""
        self.inactivity_task = asyncio.create_task(self.check_inactivity())

    async def check_inactivity(self) -> None:
        """Отключает кнопки после 2 минут бездействия."""
        try:
            while True:
                elapsed = asyncio.get_event_loop().time() - self.last_interaction
                if elapsed >= self.inactivity_timeout:
                    self.disable_navigation_buttons()
                    if self.message:
                        await self.message.edit(view=self)
                    break
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    def disable_navigation_buttons(self) -> None:
        """Отключает все кнопки навигации."""
        for item in self.children:
            if isinstance(item, Button):
                item.disabled = True

    def _create_button(self, label: str, style: ButtonStyle, disabled: bool, callback=None, url: Optional[str] = None):
        """Создает кнопку с заданными параметрами."""
        button = Button(label=label, style=style, disabled=disabled, url=url)
        if callback:
            button.callback = callback
        return button

    def update_buttons(self) -> None:
        """Обновляет состояние кнопок навигации."""
        self.clear_items()
        if self.is_loading:
            back_label = "⌛" if self.loading_button == "back" else "⬅️"
            back_disabled = True
            self.add_item(self._create_button(
                back_label, ButtonStyle.gray, back_disabled,
                callback=lambda i: self.navigate(i, -1, "back")
            ))

            next_label = "⌛" if self.loading_button == "next" else "➡️"
            next_disabled = True
            self.add_item(self._create_button(
                next_label, ButtonStyle.gray, next_disabled,
                callback=lambda i: self.navigate(i, 1, "next")
            ))

            page_label = "⌛" if self.loading_button == "page" else "📄"
            page_disabled = True
            self.add_item(self._create_button(
                page_label, ButtonStyle.blurple, page_disabled,
                callback=self.open_page_modal
            ))

            self.add_item(self._create_button(
                "🔗 Большие файлы", ButtonStyle.link, False, url="http://localhost:8001"
            ))
        else:
            self.add_item(self._create_button(
                "⬅️", ButtonStyle.gray,
                disabled=(self.current_chunk == 0 and self.current_page == 1),
                callback=lambda i: self.navigate(i, -1, "back")
            ))
            self.add_item(self._create_button(
                "➡️", ButtonStyle.gray,
                disabled=(self.current_page >= self.total_pages and self.current_chunk >= 1),
                callback=lambda i: self.navigate(i, 1, "next")
            ))
            self.add_item(self._create_button(
                "📄", ButtonStyle.blurple,
                disabled=False,
                callback=self.open_page_modal
            ))
            self.add_item(self._create_button(
                "🔗 Большие файлы", ButtonStyle.link, False, url="http://localhost:8001"
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
        self.update_buttons()
        try:
            await interaction.response.edit_message(view=self)
        except discord.errors.NotFound as e:
            logger.error(f"Взаимодействие не найдено при обновлении кнопок: {e}")
            self.is_loading = False
            self.view.loading_button = None
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
            self.view.loading_button = None
            self.update_buttons()
            await interaction.followup.send("Взаимодействие устарело. Попробуйте снова.", ephemeral=True)
            return

        new_chunk = self.current_chunk + direction
        success = True
        if new_chunk < 0 and self.current_page > 1:
            success = await self.load_page(self.current_page - 1, reset_chunk=False)
            if success:
                self.current_chunk = 1
            else:
                self.current_chunk = 0
        elif new_chunk >= 2 and self.current_page < self.total_pages:
            success = await self.load_page(self.current_page + 1, reset_chunk=False)
            if success:
                self.current_chunk = 0
            else:
                self.current_chunk = 1
        else:
            self.current_chunk = max(0, min(new_chunk, 1))

        self.is_loading = False
        self.loading_button = None
        self.update_buttons()
        try:
            content, image_urls, skipped_posts = self.create_message()
            files = await self.fetch_images(image_urls, interaction, skipped_posts)
            await interaction.edit_original_response(content=content, view=self, attachments=files)
        except discord.HTTPException as e:
            logger.error(f"Ошибка HTTP при обновлении сообщения: {e}")
            await interaction.followup.send("Не удалось обновить сообщение из-за слишком больших файлов. Попробуйте снова.", ephemeral=True)
        except discord.DiscordException as e:
            logger.error(f"Ошибка при обновлении сообщения: {e}")
            await interaction.followup.send("Не удалось обновить сообщение. Попробуйте снова.", ephemeral=True)

    async def fetch_images(
        self,
        image_urls: List[str],
        interaction: Interaction,
        skipped_posts: List[DanbooruPost]
    ) -> List[discord.File]:
        """Загружает файлы по URL с предварительной проверкой размера."""
        global skipped_posts_global, content_type_cache
        files = []
        new_skipped_posts = skipped_posts.copy()
        start_idx = self.current_chunk * 10
        end_idx = min(start_idx + 10, len(self.results))
        posts = self.results[start_idx:end_idx]
        posts_with_url = [post for post in posts if post.file_url]

        max_file_size = 8 * 1024 * 1024
        if interaction.guild:
            if interaction.guild.premium_tier == 2:
                max_file_size = 25 * 1024 * 1024
            elif interaction.guild.premium_tier == 3:
                max_file_size = 100 * 1024 * 1024

        semaphore = asyncio.Semaphore(10)

        async def fetch_single_image(idx: int, url: str) -> Tuple[Optional[discord.File], Optional[DanbooruPost]]:
            async with semaphore:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.head(url, timeout=5) as head_response:
                            if head_response.status != 200:
                                logger.error(f"HEAD-запрос для {url} вернул {head_response.status}")
                                return None, posts_with_url[idx]
                            content_length = head_response.headers.get('Content-Length')
                            if content_length and int(content_length) > max_file_size:
                                logger.debug(f"Файл {url} слишком большой ({content_length} байт)")
                                return None, posts_with_url[idx]
                            content_type = head_response.headers.get('Content-Type', 'application/octet-stream')
                            content_type_cache[url] = content_type

                        async with session.get(url) as response:
                            if response.status == 200:
                                image_data = await response.read()
                                if len(image_data) > max_file_size:
                                    logger.debug(f"Файл {url} слишком большой ({len(image_data)} байт)")
                                    return None, posts_with_url[idx]
                                extension = MIME_TO_EXTENSION.get(content_type, '.bin')
                                post = posts_with_url[idx]
                                file = discord.File(
                                    fp=io.BytesIO(image_data),
                                    filename=f"post_{post.id}{extension}"
                                )
                                return file, None
                            else:
                                logger.error(f"Ошибка загрузки файла {url}: HTTP {response.status}")
                                return None, posts_with_url[idx]
                except aiohttp.ClientError as e:
                    logger.error(f"Ошибка загрузки файла {url}: {e}")
                    return None, posts_with_url[idx]

        tasks = [fetch_single_image(idx, url) for idx, url in enumerate(image_urls)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for file, skipped_post in results:
            if isinstance(file, Exception) or isinstance(skipped_post, Exception):
                logger.error(f"Ошибка в задаче загрузки: {file or skipped_post}")
                continue
            if file:
                files.append(file)
            if skipped_post:
                new_skipped_posts.append(skipped_post)

        async with server_lock:
            skipped_posts_global.extend([post for post in new_skipped_posts if post not in skipped_posts_global])

        cache_key = (self.current_page, self.current_chunk)
        if cache_key in self.message_cache:
            content, image_urls, _ = self.message_cache[cache_key]
            self.message_cache[cache_key] = (content, image_urls, new_skipped_posts)
        return files

    async def load_page(self, target_page: int, reset_chunk: bool = False) -> bool:
        """Загружает результаты для указанной страницы с использованием кэша."""
        if target_page in self.page_cache:
            self.results = self.page_cache[target_page]
            self.current_page = target_page
            if reset_chunk:
                self.current_chunk = 0
            self.message_cache.clear()
            return True

        try:
            async with aiohttp_session() as session:
                posts = await fetch_danbooru_posts(session, self.query, target_page)
                if not posts:
                    return False
                self.results = posts
                self.page_cache[target_page] = posts
                self.current_page = target_page
                if reset_chunk:
                    self.current_chunk = 0
                self.message_cache.clear()
                return True
        except DanbooruAPIError as e:
            logger.error(f"Ошибка загрузки страницы {target_page}: {e}")
            return False

    def create_message(self) -> Tuple[str, List[str], List[DanbooruPost]]:
        """Создает сообщение и список URL файлов для текущего чанка."""
        cache_key = (self.current_page, self.current_chunk)
        if cache_key in self.message_cache:
            return self.message_cache[cache_key]

        start_idx = self.current_chunk * 10
        end_idx = min(start_idx + 10, len(self.results))
        posts = self.results[start_idx:end_idx]

        message_lines = [f"**Чанк {self.current_chunk + 1}/2 | Страница {self.current_page}/{self.total_pages}**"]
        skipped_posts = []
        image_urls = []

        for post in posts:
            if post.file_url:
                image_urls.append(post.file_url)
            else:
                skipped_posts.append(post)

        for post in skipped_posts:
            if post.file_url:
                message_lines.append(f"[Пост #{post.id}]({post.file_url})")

        message = "\n".join(message_lines)
        self.message_cache[cache_key] = (message, image_urls, skipped_posts)
        return message, image_urls, skipped_posts

    async def on_timeout(self) -> None:
        """Обрабатывает таймаут view, очищая кэш."""
        global skipped_posts_global, content_type_cache
        self.clear_items()
        self.message_cache.clear()
        self.page_cache.clear()
        if self.inactivity_task:
            self.inactivity_task.cancel()
        try:
            if self.message:
                await self.message.edit(view=None)
        except discord.DiscordException as e:
            logger.error(f"Ошибка при удалении кнопок: {e}")

        async with server_lock:
            skipped_posts_global.clear()
            content_type_cache.clear()

# Веб-сервер для отображения больших файлов
async def handle_large_files(request):
    """Обрабатывает запросы к веб-серверу, встраивая изображения, GIF и видео."""
    global skipped_posts_global, content_type_cache
    if request.path == "/favicon.ico":
        return web.Response(status=404)

    async with server_lock:
        try:
            posts_html = []
            urls_to_check = [post.file_url for post in skipped_posts_global if post.file_url]

            async def fetch_content_type(url: str) -> Tuple[str, Optional[str]]:
                if url in content_type_cache:
                    return url, content_type_cache[url]
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.head(url, timeout=5) as response:
                            if response.status == 200:
                                content_type = response.headers.get('Content-Type', '')
                                content_type_cache[url] = content_type
                                return url, content_type
                            else:
                                logger.warning(f"HEAD-запрос для {url} вернул {response.status}")
                                return url, None
                except aiohttp.ClientError as e:
                    logger.error(f"Ошибка HEAD-запроса для {url}: {e}")
                    return url, None

            content_types = await asyncio.gather(*[fetch_content_type(url) for url in urls_to_check], return_exceptions=True)

            for post in skipped_posts_global:
                if not post.file_url:
                    continue
                tags = ", ".join(html.escape(tag) for tag in post.tags)
                content = f'<a href="{html.escape(post.file_url)}" target="_blank">Открыть файл</a>'
                for url, content_type in content_types:
                    if isinstance(content_type, Exception):
                        logger.error(f"Ошибка в задаче HEAD-запроса для {url}: {content_type}")
                        continue
                    if url == post.file_url and content_type:
                        if content_type.startswith('image/'):
                            content = f'<img src="{html.escape(post.file_url)}" alt="Post #{post.id}" loading="lazy">'
                        elif content_type.startswith('video/'):
                            content = (
                                f'<video controls preload="metadata">'
                                f'<source src="{html.escape(post.file_url)}" type="{html.escape(content_type)}">'
                                f'Your browser does not support the video tag.</video>'
                            )
                        break
                posts_html.append(
                    f'<div class="post">'
                    f'<div class="post-id">Пост #{post.id}</div>'
                    f'<div>{content}</div>'
                    f'<div class="tags">Теги: {tags}</div>'
                    f'</div>'
                )
            posts_content = "".join(posts_html) if posts_html else "<p>Нет больших файлов</p>"
            logger.debug(f"Сформирован posts_content: {posts_content[:100]}...")
            return web.Response(text=HTML_PAGE.format(posts=posts_content), content_type="text/html")
        except Exception as e:
            logger.error(f"Ошибка в handle_large_files: {e}", exc_info=True)
            return web.Response(status=500, text="Internal Server Error")

@asynccontextmanager
async def aiohttp_session():
    """Контекстный менеджер для сессии aiohttp."""
    timeout = aiohttp.ClientTimeout(total=20, connect=10)
    connector = aiohttp.TCPConnector(limit=10)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        yield session

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
        if response.status != 200:
            error_map = {
                403: "Доступ запрещен",
                429: "Превышен лимит запросов",
                500: "Ошибка сервера"
            }
            raise DanbooruAPIError(f"Ошибка API: {error_map.get(response.status, f'Код {response.status}')}")
        
        data = await response.json()
        if not isinstance(data, dict) or "counts" not in data or "posts" not in data["counts"]:
            raise DanbooruAPIError("Неправильный формат ответа")
        
        return data["counts"]["posts"]

@backoff.on_exception(
    backoff.expo,
    (aiohttp.ClientError, asyncio.TimeoutError, DanbooruAPIError),
    max_tries=3,
    max_time=10
)
async def fetch_danbooru_posts(session: aiohttp.ClientSession, tags: Optional[str], page: int) -> List[DanbooruPost]:
    """Получает посты из Danbooru API."""
    base_url = "https://danbooru.donmai.us/posts.json"
    params = {"page": page, "limit": 20}
    if tags:
        params["tags"] = tags.strip()

    async with session.get(base_url, params=params) as response:
        if response.status != 200:
            error_map = {
                403: "Доступ запрещен",
                429: "Превышен лимит запросов",
                500: "Ошибка сервера"
            }
            raise DanbooruAPIError(f"Ошибка API: {error_map.get(response.status, f'Код {response.status}')}")
        
        data = await response.json()
        if not isinstance(data, list):
            raise DanbooruAPIError("Неправильный формат ответа")

        posts = []
        for item in data:
            if not all(key in item for key in ["id", "file_url", "preview_file_url", "tag_string", "rating"]):
                continue
            posts.append(DanbooruPost(
                id=item["id"],
                file_url=item["file_url"] or "",
                preview_url=item["preview_file_url"] or "",
                tags=item["tag_string"].split(),
                rating=item["rating"],
                source=item.get("source"),
                created_at=item.get("created_at", "Неизвестно")
            ))
        return posts

async def start_web_server():
    """Инициализация и запуск веб-сервера один раз при старте бота."""
    global web_server
    async with server_lock:
        if not web_server:
            app = web.Application()
            app.router.add_get("/", handle_large_files)
            runner = web.AppRunner(app)
            await runner.setup()
            port = 8001
            try:
                web_server = web.TCPSite(runner, "localhost", port)
                await web_server.start()
                logger.info(f"Веб-сервер для danbooru запущен на http://localhost:{port}")
            except OSError as e:
                logger.error(f"Не удалось запустить веб-сервер на порту {port}: {e}")
                raise

async def stop_web_server():
    """Остановка веб-сервера при завершении работы бота."""
    global web_server
    async with server_lock:
        if web_server:
            await web_server.stop()
            web_server = None
            logger.info("Веб-сервер остановлен")

async def danbooru(interaction: discord.Interaction, bot_client, tags: Optional[str] = None) -> None:
    """Слеш-команда для поиска постов на Danbooru."""
    guild_id = str(interaction.guild.id) if interaction.guild else "ЛС"
    channel_id = str(interaction.channel.id) if interaction.channel else "ЛС"

    result, reason = await restrict_command_execution(interaction, bot_client)
    if not result:
        await interaction.response.send_message(reason or "Конфигурация сервера не найдена!", ephemeral=True)
        return

    result, reason = await check_bot_access(interaction, bot_client)
    if not result:
        await interaction.response.send_message(reason, ephemeral=True)
        return

    if interaction.guild and not interaction.channel.nsfw:
        await interaction.response.send_message("Команда доступна только в NSFW-каналах или ЛС.", ephemeral=True)
        return

    if interaction.guild:
        restriction, restriction_reason = await checker.check_user_restriction(interaction)
        if not restriction:
            await interaction.response.send_message(restriction_reason or "Ваш доступ ограничен.", ephemeral=True)
            return

    try:
        await interaction.response.defer(ephemeral=False)
    except discord.errors.NotFound as e:
        logger.error(f"Взаимодействие не найдено при defer: {e}")
        await interaction.followup.send("Взаимодействие устарело. Попробуйте снова.", ephemeral=True)
        return
    except discord.errors.InteractionResponded:
        pass

    try:
        async with aiohttp_session() as session:
            total_posts_task = fetch_post_count(session, tags)
            posts_task = fetch_danbooru_posts(session, tags, page=1)
            total_posts, posts = await asyncio.gather(total_posts_task, posts_task, return_exceptions=True)

            if isinstance(total_posts, Exception):
                raise DanbooruAPIError(f"Ошибка получения количества постов: {total_posts}")
            if isinstance(posts, Exception):
                raise DanbooruAPIError(f"Ошибка получения постов: {posts}")

            # Ограничение количества страниц до 1000
            total_pages = min(1000, math.ceil(total_posts / 20)) if total_posts > 0 else 1
            if not posts:
                await interaction.followup.send("Посты по тегам не найдены.", ephemeral=False)
                return

            view = NavigationView(posts, interaction.user, tags, current_page=1, total_pages=total_pages)
            view.page_cache[1] = posts
            content, image_urls, skipped_posts = view.create_message()
            files = await view.fetch_images(image_urls, interaction, skipped_posts)
            message = await interaction.followup.send(content=content, view=view, files=files, ephemeral=False)
            view.message = message

    except DanbooruAPIError as e:
        logger.error(f"Ошибка Danbooru API: {e}")
        await interaction.followup.send(f"Не удалось получить посты: {str(e)}", ephemeral=False)
    except Exception as e:
        logger.error(f"Неизвестная ошибка в /danbooru: {e}", exc_info=True)
        await interaction.followup.send("Произошла ошибка. Попробуйте позже.", ephemeral=False)

def create_command(bot_client) -> app_commands.Command:
    """Создает слеш-команду /danbooru."""
    @app_commands.command(name="danbooru", description="Поиск постов на Danbooru по тегам")
    @app_commands.describe(tags="Теги для поиска (например, 'cat_ears solo')")
    async def wrapper(interaction: discord.Interaction, tags: Optional[str] = None) -> None:
        await danbooru(interaction, bot_client, tags)

    @wrapper.error
    async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        guild_id = str(interaction.guild.id) if interaction.guild else "ЛС"
        channel_id = str(interaction.channel.id) if interaction.channel else "ЛС"
        logger.error(f"Ошибка в /danbooru для пользователя {interaction.user.id} в гильдии {guild_id}, канал {channel_id}: {error}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("Ошибка команды. Попробуйте снова.", ephemeral=True)
            else:
                await interaction.followup.send("Ошибка команды. Попробуйте снова.", ephemeral=True)
        except discord.DiscordException as e:
            logger.error(f"Не удалось отправить сообщение об ошибке: {e}")

    return wrapper
