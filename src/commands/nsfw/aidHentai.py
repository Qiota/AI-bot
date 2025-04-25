import discord
from discord import app_commands, Embed, ButtonStyle, Interaction
from discord.ui import Modal, TextInput, Button, View
import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import quote, urlencode, urlparse
import asyncio
import re
from typing import List, Optional, Dict
from dataclasses import dataclass
from functools import lru_cache
from contextlib import asynccontextmanager
from ...systemLog import logger
from ..restrict import check_bot_access, restrict_command_execution
import traceback
from ...utils.checker import checker
import backoff

description = "Поиск по AnimeIdHentai"

# Кастомные исключения
class HttpError(Exception):
    pass

class ParseError(Exception):
    pass

# Структурированные данные
@dataclass
class SearchResult:
    title: str
    url: str
    banner_url: str
    description: str
    meta: str
    video_link: Optional[str]
    image_url: Optional[str]
    additional_info: List[Dict[str, str]]
    tags: List[Dict[str, str]]

@asynccontextmanager
async def aiohttp_session():
    """Контекстный менеджер для aiohttp сессии."""
    timeout = aiohttp.ClientTimeout(total=30, connect=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        yield session

class PageSelectModal(Modal, title="Перейти к странице и тайтлу"):
    """Модальное окно для ввода номера страницы и тайтла."""
    page_input = TextInput(
        label="Номер страницы",
        placeholder="Введите номер страницы (например, 1)",
        default="1",
        required=True,
        min_length=1,
        max_length=5
    )
    title_input = TextInput(
        label="Номер тайтла",
        placeholder="Введите номер тайтла на странице",
        default="1",
        required=True,
        min_length=1,
        max_length=3
    )

    def __init__(self, view: 'NavigationView'):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: Interaction) -> None:
        """Обрабатывает отправку данных из модального окна."""
        if interaction.user != self.view.original_user:
            await interaction.response.send_message(
                "Только пользователь, вызвавший команду, может её использовать.",
                ephemeral=True
            )
            return

        try:
            page = int(self.page_input.value)
            title_index = int(self.title_input.value) - 1
        except ValueError:
            await interaction.response.send_message(
                "Пожалуйста, введите корректные числа для страницы и тайтла.",
                ephemeral=True
            )
            return

        if page < 1 or title_index < 0:
            await interaction.response.send_message(
                "Номер страницы и тайтла должны быть положительными.",
                ephemeral=True
            )
            return

        await self.view.navigate_to_page_and_title(interaction, page, title_index)

class NavigationView(View):
    """Кастомный View для навигации по результатам поиска."""
    def __init__(
        self,
        results: List[SearchResult],
        original_user: discord.User,
        query: Optional[str],
        current_page: int,
        total_pages: int,
        current_index: int = 0,
        timeout: int = 300
    ):
        super().__init__(timeout=timeout)
        self.results = results
        self.original_user = original_user
        self.query = query
        self.current_page = current_page
        self.total_pages = total_pages
        self.current_index = current_index
        self.is_loading = False
        self.loading_button: Optional[str] = None
        self.embed_cache: Dict[int, Embed] = {}
        self.last_interaction = asyncio.get_event_loop().time()
        self.inactivity_timeout = 120
        self.inactivity_task: Optional[asyncio.Task] = None
        self.message: Optional[discord.Message] = None
        self.update_buttons()
        self.start_inactivity_timer()

    def start_inactivity_timer(self) -> None:
        """Запускает таймер бездействия."""
        self.inactivity_task = asyncio.create_task(self.check_inactivity())
        logger.debug(f"Таймер бездействия запущен для view с {len(self.results)} результатами")

    async def check_inactivity(self) -> None:
        """Проверяет бездействие и отключает кнопки через 2 минуты."""
        try:
            while True:
                elapsed = asyncio.get_event_loop().time() - self.last_interaction
                if elapsed >= self.inactivity_timeout:
                    self.disable_navigation_buttons()
                    if self.message:
                        await self.message.edit(view=self)
                        logger.info(f"Кнопки отключены по таймауту для сообщения {self.message.id}")
                    break
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.debug("Задача проверки бездействия отменена")

    def disable_navigation_buttons(self) -> None:
        """Отключает навигационные кнопки."""
        for item in self.children:
            if isinstance(item, Button) and item.label in ["⬅️", "➡️", "⌛", "🔢"]:
                item.disabled = True

    def update_buttons(self) -> None:
        """Обновляет состояние кнопок."""
        self.clear_items()

        back_label = "⌛" if self.is_loading and self.loading_button == "back" else "⬅️"
        back_button = Button(
            label=back_label,
            style=ButtonStyle.gray,
            disabled=(self.current_index == 0 and self.current_page == 1) or self.is_loading
        )
        back_button.callback = lambda i: self.navigate(i, -1, "back")
        self.add_item(back_button)

        video_link = self.results[self.current_index].video_link
        watch_button = Button(
            label="📺 Смотреть онлайн",
            style=ButtonStyle.link,
            url=video_link,
            disabled=not video_link or not self.is_valid_url(video_link)
        )
        self.add_item(watch_button)

        next_label = "⌛" if self.is_loading and self.loading_button == "next" else "➡️"
        next_button = Button(
            label=next_label,
            style=ButtonStyle.gray,
            disabled=self.is_loading or (self.current_page >= self.total_pages and self.current_index >= len(self.results) - 1)
        )
        next_button.callback = lambda i: self.navigate(i, 1, "next")
        self.add_item(next_button)

        page_select_button = Button(
            label="🔢",
            style=ButtonStyle.gray,
            disabled=self.is_loading
        )
        page_select_button.callback = self.show_page_select_modal
        self.add_item(page_select_button)

    async def show_page_select_modal(self, interaction: Interaction) -> None:
        """Открывает модальное окно для выбора страницы и тайтла."""
        if interaction.user != self.original_user:
            await interaction.response.send_message(
                "Только пользователь, вызвавший команду, может её использовать.",
                ephemeral=True
            )
            return

        logger.debug(f"Открытие модального окна для пользователя {interaction.user.id}")
        modal = PageSelectModal(self)
        try:
            await interaction.response.send_modal(modal)
        except discord.DiscordException as e:
            logger.error(f"Ошибка при открытии модального окна: {e}\n{traceback.format_exc()}")
            await interaction.followup.send(
                "Не удалось открыть окно ввода. Попробуйте снова.",
                ephemeral=True
            )

    def is_valid_url(self, url: Optional[str]) -> bool:
        """Проверяет валидность URL."""
        if not url:
            return False
        parsed = urlparse(url)
        return parsed.scheme in ('http', 'https')

    async def load_page(self, target_page: int) -> bool:
        """Загружает результаты для указанной страницы."""
        try:
            async with aiohttp_session() as session:
                url = construct_url(self.query, target_page)
                html = await fetch_html(session, url)
                soup = BeautifulSoup(html, 'html.parser')
                new_results = await parse_search_results(session, soup)
                if not new_results:
                    logger.info(f"Нет результатов на странице {target_page} для запроса '{self.query}'")
                    return False
                self.results = new_results
                self.current_page = target_page
                self.embed_cache.clear()
                return True
        except (HttpError, ParseError) as e:
            logger.error(f"Ошибка при загрузке страницы {target_page}: {e}\n{traceback.format_exc()}")
            return False
        except Exception as e:
            logger.error(f"Неизвестная ошибка при загрузке страницы {target_page}: {e}\n{traceback.format_exc()}")
            return False

    async def navigate(self, interaction: Interaction, direction: int, button: str) -> None:
        """Обрабатывает навигацию по результатам."""
        if interaction.user != self.original_user:
            await interaction.response.send_message(
                "Только пользователь, вызвавший команду, может её использовать.",
                ephemeral=True
            )
            return

        if self.is_loading:
            logger.debug("Навигация заблокирована: is_loading=True")
            return

        self.is_loading = True
        self.loading_button = button
        self.last_interaction = asyncio.get_event_loop().time()
        self.update_buttons()

        await interaction.response.defer()

        new_index = self.current_index + direction
        if new_index < 0 and self.current_page > 1:
            if await self.load_page(self.current_page - 1):
                self.current_index = len(self.results) - 1
            else:
                self.current_index = 0
        elif new_index >= len(self.results):
            if await self.load_page(self.current_page + 1):
                self.current_index = 0
            else:
                self.current_index = len(self.results) - 1
        else:
            self.current_index = max(0, min(new_index, len(self.results) - 1))

        self.is_loading = False
        self.loading_button = None
        self.update_buttons()
        try:
            await interaction.edit_original_response(embed=self.create_embed(), view=self)
        except discord.DiscordException as e:
            logger.error(f"Ошибка при обновлении сообщения: {e}\n{traceback.format_exc()}")
            await interaction.followup.send(
                "Не удалось обновить сообщение. Попробуйте снова.",
                ephemeral=True
            )

    async def navigate_to_page_and_title(self, interaction: Interaction, page: int, title_index: int) -> None:
        """Переходит к указанной странице и тайтлу."""
        if self.is_loading:
            logger.debug("Навигация заблокирована: is_loading=True")
            return

        self.is_loading = True
        self.loading_button = "page_select"
        self.last_interaction = asyncio.get_event_loop().time()
        self.update_buttons()

        if page != self.current_page:
            if not await self.load_page(page):
                self.is_loading = False
                self.loading_button = None
                self.update_buttons()
                await interaction.response.defer()
                await interaction.followup.send(
                    f"Не удалось загрузить страницу {page}. Возможно, она не существует.",
                    ephemeral=True
                )
                return

        if title_index >= len(self.results):
            self.is_loading = False
            self.loading_button = None
            self.update_buttons()
            await interaction.response.defer()
            await interaction.followup.send(
                f"На странице {page} только {len(self.results)} тайтлов. Введите номер от 1 до {len(self.results)}.",
                ephemeral=True
            )
            return

        self.current_index = min(title_index, len(self.results) - 1)
        self.is_loading = False
        self.loading_button = None
        self.update_buttons()

        await interaction.response.defer()

        try:
            if self.message:
                await self.message.edit(embed=self.create_embed(), view=self)
        except discord.DiscordException as e:
            logger.error(f"Ошибка при редактировании сообщения: {e}\n{traceback.format_exc()}")
            await interaction.followup.send(
                "Не удалось обновить сообщение. Попробуйте снова.",
                ephemeral=True
            )

    def create_embed(self) -> Embed:
        """Создает Embed для текущего результата."""
        if self.current_index in self.embed_cache:
            return self.embed_cache[self.current_index]

        result = self.results[self.current_index]
        description = result.description[:300] + ("..." if len(result.description) > 300 else "")
        if result.tags:
            tag_links = [f"[{tag['name']}]({tag['url']})" for tag in result.tags if tag.get('name') and tag.get('url')]
            tags_text = f"\n\n🏷 Теги: {', '.join(tag_links)}"
            if len(description) + len(tags_text) > 4000:
                description = description[:4000 - len(tags_text) - 3] + "..."
            description += tags_text

        embed = Embed(
            title=f"🎬 {result.title}"[:256],
            url=result.url,
            description=description,
            color=0xFF5733
        )

        embed.set_thumbnail(url=result.banner_url or "https://via.placeholder.com/100")
        if result.image_url:
            embed.set_image(url=result.image_url)

        for field in result.additional_info[:12]:
            embed.add_field(
                name=f"🔹 {field['name']}"[:256],
                value=field['value'][:1024],
                inline=True
            )

        embed.set_footer(text=f"{self.current_index + 1}/{len(self.results)} | Страница {self.current_page} из {self.total_pages} • {result.meta}")

        self.embed_cache[self.current_index] = embed
        return embed

    async def on_timeout(self) -> None:
        """Обрабатывает таймаут view."""
        self.disable_navigation_buttons()
        if self.inactivity_task:
            self.inactivity_task.cancel()
        try:
            if self.message:
                await self.message.edit(view=self)
                logger.info(f"View отключен по таймауту для сообщения {self.message.id}")
        except discord.DiscordException as e:
            logger.error(f"Ошибка при отключении view: {e}\n{traceback.format_exc()}")

async def aidhentai(interaction: discord.Interaction, bot_client, query: Optional[str] = None) -> None:
    """Команда /aidhentai: Поиск по AnimeIdHentai."""
    guild_id = str(interaction.guild.id) if interaction.guild else "DM"
    channel_id = str(interaction.channel.id) if interaction.channel else "DM"

    logger.debug(f"Попытка выполнения команды /aidhentai для пользователя {interaction.user.id} в гильдии {guild_id}, канал {channel_id}")

    # Проверка состояния бота и гильдии
    result, reason = await restrict_command_execution(interaction, bot_client)
    if not result:
        await interaction.response.send_message(reason or "Конфигурация сервера не найдена! Настройте через /restrict.", ephemeral=True)
        return

    # Проверка доступа бота к каналу
    result, reason = await check_bot_access(interaction, bot_client)
    if not result:
        await interaction.response.send_message(reason, ephemeral=True)
        return

    # Проверка NSFW-статуса канала
    if interaction.guild and not interaction.channel.nsfw:
        await interaction.response.send_message("Эта команда доступна только в NSFW-каналах или ЛС.", ephemeral=True)
        return

    # Проверка ограничений пользователя
    if interaction.guild:
        restriction, restriction_reason = await checker.check_user_restriction(interaction)
        if not restriction:
            await interaction.response.send_message(restriction_reason or "Ваш доступ к боту ограничен.", ephemeral=True)
            return

    logger.debug(f"Все проверки пройдены, выполняется команда /aidhentai в гильдии {guild_id}, канал {channel_id}")

    # Отложить ответ немедленно
    await interaction.response.defer(ephemeral=False)

    try:
        async with aiohttp_session() as session:
            url = construct_url(query, page=1)
            html = await fetch_html(session, url)
            soup = BeautifulSoup(html, 'html.parser')
            new_results = await parse_search_results(session, soup)
            total_pages = parse_total_pages(soup)

            if not new_results:
                await interaction.followup.send("По вашему запросу ничего не найдено.", ephemeral=False)
                return

            view = NavigationView(new_results, interaction.user, query, current_page=1, total_pages=total_pages)
            embed = view.create_embed()
            message = await interaction.followup.send(embed=embed, view=view, ephemeral=False)
            view.message = message

    except HttpError as e:
        logger.error(f"HTTP ошибка при выполнении /aidhentai: {e}\n{traceback.format_exc()}")
        await interaction.followup.send("Сайт не отвечает. Попробуйте позже.", ephemeral=False)
    except ParseError as e:
        logger.error(f"Ошибка парсинга при выполнении /aidhentai: {e}\n{traceback.format_exc()}")
        await interaction.followup.send("Ошибка при обработке данных. Попробуйте другой запрос.", ephemeral=False)
    except Exception as e:
        logger.error(f"Неизвестная ошибка /aidhentai: {e}\n{traceback.format_exc()}")
        await interaction.followup.send("Произошла неизвестная ошибка. Обратитесь к администратору.", ephemeral=False)

@lru_cache(maxsize=1000)
def construct_url(query: Optional[str], page: int) -> str:
    """Формирует URL для запроса."""
    if page < 1:
        raise ValueError("Номер страницы должен быть положительным")

    base_url = "https://animeidhentai.com"
    if page > 1:
        base_url += f"/page/{page}"
    params: Dict[str, str] = {}
    if query and query.strip():
        params["s"] = query.strip()
    else:
        params["s"] = "a"

    query_string = urlencode(params, quote_via=quote)
    return f"{base_url}/?{query_string}"

@backoff.on_exception(
    backoff.expo,
    (aiohttp.ClientError, asyncio.TimeoutError),
    max_tries=3,
    max_time=30,
    jitter=backoff.full_jitter
)
async def fetch_html(session: aiohttp.ClientSession, url: str) -> str:
    """Получает HTML страницы."""
    try:
        async with session.get(url) as response:
            if response.status != 200:
                raise HttpError(f"HTTP error! Status: {response.status}")
            return await response.text()
    except aiohttp.ClientError as e:
        raise HttpError(f"Ошибка при запросе {url}: {e}")

def parse_total_pages(soup: BeautifulSoup) -> int:
    """Парсит общее количество страниц."""
    pagination = soup.select_one('div.pagination-wrapper')
    if not pagination:
        logger.warning("Пагинация не найдена")
        return 1

    page_numbers = pagination.select('a.page-numbers, span.page-numbers.current')
    if not page_numbers:
        logger.warning("Номера страниц не найдены")
        return 1

    try:
        return max(int(elem.get_text()) for elem in page_numbers if elem.get_text().isdigit())
    except ValueError:
        logger.warning("Ошибка парсинга страниц")
        return 1

async def parse_search_results(session: aiohttp.ClientSession, soup: BeautifulSoup) -> List[SearchResult]:
    """Парсит результаты поиска."""
    elements = soup.select('a.lnk-blk')
    if not elements:
        return []

    # Ограничение числа одновременно обрабатываемых элементов
    semaphore = asyncio.Semaphore(5)
    async def process_with_semaphore(i: int, e: BeautifulSoup) -> Optional[SearchResult]:
        async with semaphore:
            return await process_search_element(session, soup, i, e)

    tasks = [process_with_semaphore(i, e) for i, e in enumerate(elements)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if not isinstance(r, Exception) and r]

async def process_search_element(session: aiohttp.ClientSession, soup: BeautifulSoup, index: int, element: BeautifulSoup) -> Optional[SearchResult]:
    """Обрабатывает элемент поиска."""
    try:
        title = element.get('aria-label')
        if not title:
            logger.warning(f"Элемент {index}: Отсутствует title")
            return None

        url = element.get('href')
        if not url:
            logger.warning(f"Элемент {index}: Отсутствует URL")
            return None

        banner_elements = soup.select('div.anime-tb.pctr.rad1.por img')
        banner_url = banner_elements[index].get('src') or "https://via.placeholder.com/100" if index < len(banner_elements) else "https://via.placeholder.com/100"

        description_elements = soup.select('div.description.dn p')
        description = description_elements[index].get_text(strip=True) or 'Описание отсутствует.' if index < len(description_elements) else 'Описание отсутствует.'

        meta_elements = soup.select('p.meta.df.fww.aic.mgt.fz12.link-co.op05')
        meta = '•'.join(item.strip() for item in meta_elements[index].get_text().split('•') if item.strip()) if index < len(meta_elements) else ''

        try:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.warning(f"Элемент {index}: HTTP ошибка {response.status}")
                    return None
                detail_html = await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"Элемент {index}: Ошибка запроса деталей: {e}")
            return None

        detail_soup = BeautifulSoup(detail_html, 'html.parser')
        iframe = detail_soup.select_one('iframe')
        video_link = iframe.get('src') if iframe else None

        if video_link:
            parsed = urlparse(video_link)
            if not parsed.scheme and video_link.startswith('//'):
                video_link = f"https:{video_link}"
            elif parsed.scheme not in ('http', 'https'):
                video_link = None

        image_url = await fetch_image_url(session, video_link) if video_link else None
        additional_info = parse_additional_info(detail_soup)

        tags = [
            {'name': tag.get('aria-label'), 'url': tag.get('href')}
            for tag in detail_soup.select('div.genres.mgt.df.fww.por a.btn.fz12.rad1.mgr.mgb.gray-bg')
            if tag.get('aria-label') and tag.get('href')
        ]

        return SearchResult(
            title=title,
            url=url,
            banner_url=banner_url,
            description=description,
            meta=meta,
            video_link=video_link,
            image_url=image_url,
            additional_info=additional_info,
            tags=tags
        )

    except Exception as e:
        logger.warning(f"Элемент {index}: Ошибка обработки: {e}\n{traceback.format_exc()}")
        return None

# Кэш для изображений
_image_cache: Dict[str, Optional[str]] = {}
_image_cache_lock = asyncio.Lock()

async def fetch_image_url(session: aiohttp.ClientSession, video_url: Optional[str]) -> Optional[str]:
    """Получает URL изображения."""
    if not video_url or urlparse(video_url).scheme not in ('http', 'https'):
        return None

    async with _image_cache_lock:
        if video_url in _image_cache:
            return _image_cache[video_url]

    try:
        async with session.get(video_url) as response:
            if response.status != 200:
                logger.debug(f"Не удалось загрузить изображение: HTTP {response.status}")
                return None
            body = await response.text()

        soup = BeautifulSoup(body, 'html.parser')
        backdrop = soup.select_one('div.backdrop')
        if backdrop and backdrop.get('style'):
            match = re.search(r'url\(["\']?(https:\/\/nhplayer\.com\/content\/previews\/[^"\']+\.jpg)["\']?\)', backdrop.get('style'))
            image_url = match.group(1) if match else None
        else:
            image_url = None

        async with _image_cache_lock:
            _image_cache[video_url] = image_url
            if len(_image_cache) > 100:  # Ограничение размера кэша
                _image_cache.pop(next(iter(_image_cache)))

        return image_url

    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.debug(f"Сетевая ошибка при запросе изображения: {e}")
        return None
    except Exception as e:
        logger.warning(f"Неизвестная ошибка при запросе изображения: {e}\n{traceback.format_exc()}")
        return None

def parse_additional_info(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Парсит дополнительную информацию."""
    return [
        {
            'name': translate_field_name(row.select_one('th.field').get_text(strip=True)),
            'value': row.select_one('td.value').get_text(strip=True),
            'inline': True
        }
        for row in soup.select('tbody tr')
        if row.select_one('th.field') and row.select_one('td.value') and translate_field_name(row.select_one('th.field').get_text(strip=True))
    ]

@lru_cache(maxsize=100)
def translate_field_name(field_name: str) -> str:
    """Переводит названия полей."""
    translations = {
        'Main Title': 'Название:',
        'Official Title': 'Оригинальное название:',
        'Type': 'Тип:',
        'Year': 'Дата выпуска:',
        'Season': 'Сезон:'
    }
    return translations.get(field_name, field_name)

def create_command(bot_client) -> app_commands.Command:
    """Создает слеш-команду /aidhentai."""
    @app_commands.command(name="aidhentai", description=description)
    @app_commands.describe(query="Поисковый запрос")
    async def wrapper(interaction: discord.Interaction, query: Optional[str] = None) -> None:
        await aidhentai(interaction, bot_client, query)

    @wrapper.error
    async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        guild_id = str(interaction.guild.id) if interaction.guild else "DM"
        channel_id = str(interaction.channel.id) if interaction.channel else "DM"
        logger.error(f"Ошибка /aidhentai для {interaction.user.id} в гильдии {guild_id}, канал {channel_id}: {error}\n{traceback.format_exc()}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("Ошибка при выполнении команды. Попробуйте снова.", ephemeral=True)
            else:
                await interaction.followup.send("Ошибка при выполнении команды. Попробуйте снова.", ephemeral=True)
        except discord.DiscordException as e:
            logger.error(f"Не удалось отправить сообщение об ошибке: {e}\n{traceback.format_exc()}")

    return wrapper