import discord
from discord import app_commands, Embed, ButtonStyle, Interaction
from discord.ui import Modal, TextInput, Button, View
import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import quote, urlencode, urlparse
import asyncio
import re
import socket
from typing import List, Optional, Dict, Tuple
from dataclasses import dataclass
from functools import lru_cache
from contextlib import asynccontextmanager
from collections import OrderedDict
from ...systemLog import logger
from ..restrict import check_bot_access, restrict_command_execution
import traceback
from ...utils.checker import checker
import backoff
import math

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
    connector = aiohttp.TCPConnector(limit=12)  # Оптимизирован лимит соединений
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
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

        page_str, title_str = self.page_input.value, self.title_input.value
        if not (re.match(r'^\d+$', page_str) and re.match(r'^\d+$', title_str)):
            await interaction.response.send_message(
                "Введите только числа для страницы и тайтла.",
                ephemeral=True
            )
            return

        page, title_index = int(page_str), int(title_str) - 1
        if page < 1 or title_index < 0:
            await interaction.response.send_message(
                "Номер страницы и тайтла должны быть положительными.",
                ephemeral=True
            )
            return

        await self.view.navigate_to_page_and_title(interaction, page, title_index)

class NavigationView(View):
    """Кастомный View для навигации по результатам поиска с динамическими зонами."""
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

    def get_zones(self) -> Tuple[List[SearchResult], int, int]:
        """Динамически разделяет результаты на 3 зоны."""
        if not self.results:
            return [], 0, 0
        zone_size = math.ceil(len(self.results) / 3)
        zone_idx = self.current_index // zone_size
        zone_start = zone_idx * zone_size
        zone_end = min((zone_idx + 1) * zone_size, len(self.results))
        zone_index = self.current_index % zone_size
        return self.results[zone_start:zone_end], zone_index, zone_idx

    def start_inactivity_timer(self) -> None:
        """Запускает таймер бездействия."""
        self.inactivity_task = asyncio.create_task(self.check_inactivity())

    async def check_inactivity(self) -> None:
        """Проверяет бездействие и отключает кнопки через 2 минуты."""
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
        """Отключает навигационные кнопки."""
        for item in self.children:
            if isinstance(item, Button) and item.label in ["⬅️", "➡️", "⌛", "🔢"]:
                item.disabled = True

    def _create_button(self, label: str, style: ButtonStyle, disabled: bool, callback=None, url: Optional[str] = None):
        """Создает кнопку с заданными параметрами."""
        button = Button(label=label, style=style, disabled=disabled, url=url)
        if callback:
            button.callback = callback
        return button

    def update_buttons(self) -> None:
        """Обновляет состояние кнопок."""
        self.clear_items()

        current_zone, zone_index, zone_idx = self.get_zones()
        back_label = "⌛" if self.is_loading and self.loading_button == "back" else "⬅️"
        self.add_item(self._create_button(
            back_label, ButtonStyle.gray,
            disabled=(self.current_index == 0 and self.current_page == 1) or self.is_loading,
            callback=lambda i: self.navigate(i, -1, "back")
        ))

        video_link = self.results[self.current_index].video_link
        self.add_item(self._create_button(
            "📺 Смотреть онлайн", ButtonStyle.link,
            disabled=not video_link or not self.is_valid_url(video_link),
            url=video_link
        ))

        next_label = "⌛" if self.is_loading and self.loading_button == "next" else "➡️"
        self.add_item(self._create_button(
            next_label, ButtonStyle.gray,
            disabled=self.is_loading or (self.current_page >= self.total_pages and self.current_index >= len(self.results) - 1),
            callback=lambda i: self.navigate(i, 1, "next")
        ))

        self.add_item(self._create_button(
            "🔢", ButtonStyle.gray,
            disabled=self.is_loading,
            callback=self.show_page_select_modal
        ))

    async def show_page_select_modal(self, interaction: Interaction) -> None:
        """Открывает модальное окно для выбора страницы и тайтла."""
        if interaction.user != self.original_user:
            await interaction.response.send_message(
                "Только пользователь, вызвавший команду, может её использовать.",
                ephemeral=True
            )
            return

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
                soup = await asyncio.to_thread(BeautifulSoup, html, 'html.parser')  # Асинхронный парсинг
                new_results = await parse_search_results(session, soup)
                if not new_results:
                    return False
                self.results = new_results
                self.current_page = target_page
                self.current_index = 0
                self.embed_cache.clear()  # Очистка кэша при смене страницы
                return True
        except (HttpError, ParseError, Exception) as e:
            logger.error(f"Ошибка при загрузке страницы {target_page} для URL {url}: {e}\n{traceback.format_exc()}")
            return False

    async def navigate(self, interaction: Interaction, direction: int, button: str) -> None:
        """Обрабатывает навигацию по результатам с автоматическим переключением зон."""
        if interaction.user != self.original_user:
            await interaction.response.send_message(
                "Только пользователь, вызвавший команду, может её использовать.",
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

        new_index = self.current_index + direction
        current_zone, zone_index, zone_idx = self.get_zones()

        if new_index < 0 and self.current_page > 1:
            if await self.load_page(self.current_page - 1):
                zone_size = math.ceil(len(self.results) / 3)
                self.current_index = len(self.results) - 1
            else:
                self.current_index = 0
        elif new_index >= len(self.results) and self.current_page < self.total_pages:
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
            return

        self.is_loading = True
        self.loading_button = "page_select"
        self.last_interaction = asyncio.get_event_loop().time()
        self.update_buttons()

        try:
            await interaction.response.defer()
        except discord.errors.NotFound as e:
            logger.error(f"Взаимодействие не найдено при defer: {e}")
            self.is_loading = False
            self.loading_button = None
            self.update_buttons()
            await interaction.followup.send("Взаимодействие устарело. Попробуйте снова.", ephemeral=True)
            return
        except discord.errors.InteractionResponded:
            pass

        if page != self.current_page:
            if not await self.load_page(page):
                self.is_loading = False
                self.loading_button = None
                self.update_buttons()
                await interaction.followup.send(
                    f"Не удалось загрузить страницу {page}. Возможно, она не существует.",
                    ephemeral=True
                )
                return

        if title_index >= len(self.results):
            self.is_loading = False
            self.loading_button = None
            self.update_buttons()
            await interaction.followup.send(
                f"На странице {page} только {len(self.results)} тайтлов. Введите номер от 1 до {len(self.results)}.",
                ephemeral=True
            )
            return

        self.current_index = title_index
        self.is_loading = False
        self.loading_button = None
        self.update_buttons()

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
        current_zone, zone_index, zone_idx = self.get_zones()
        description = result.description[:300] + ("..." if len(result.description) > 300 else "")
        if not result.image_url:
            description += "\n⚠️ Не удалось загрузить изображение."

        if result.tags:
            tags_text = f"\n\n🏷 Теги: {', '.join(f'[{tag['name']}]({tag['url']})' for tag in result.tags if tag.get('name') and tag.get('url'))}"
            if len(description) + len(tags_text) > 4000:
                description = description[:4000 - len(tags_text) - 3] + "..."
            description += tags_text

        embed = Embed(
            title=f"🎬 {result.title}"[:256],
            url=result.url,
            description=description[:4096],  # Ограничение длины
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

        embed.set_footer(text=f"{self.current_index + 1}/{len(self.results)} | Страница {self.current_page}/{self.total_pages} • {result.meta}")

        self.embed_cache[self.current_index] = embed
        return embed

    async def on_timeout(self) -> None:
        """Обрабатывает таймаут view."""
        self.disable_navigation_buttons()
        self.embed_cache.clear()
        if self.inactivity_task:
            self.inactivity_task.cancel()
        try:
            if self.message:
                await self.message.edit(view=self)
        except discord.DiscordException as e:
            logger.error(f"Ошибка при отключении view: {e}\n{traceback.format_exc()}")

@backoff.on_exception(
    backoff.expo,
    (aiohttp.ClientError, asyncio.TimeoutError, discord.errors.NotFound),
    max_tries=3,
    max_time=10,
    jitter=backoff.full_jitter
)
async def aidhentai(interaction: discord.Interaction, bot_client, query: Optional[str] = None) -> None:
    """Команда /aidhentai: Поиск по AnimeIdHentai."""
    guild_id = str(interaction.guild.id) if interaction.guild else "DM"
    channel_id = str(interaction.channel.id) if interaction.channel else "DM"

    result, reason = await restrict_command_execution(interaction, bot_client)
    if not result:
        await interaction.response.send_message(reason or "Конфигурация сервера не найдена! Настройте через /restrict.", ephemeral=True)
        return

    result, reason = await check_bot_access(interaction, bot_client)
    if not result:
        await interaction.response.send_message(reason, ephemeral=True)
        return

    if interaction.guild and not interaction.channel.nsfw:
        await interaction.response.send_message("Эта команда доступна только в NSFW-каналах или ЛС.", ephemeral=True)
        return

    if interaction.guild:
        restriction, restriction_reason = await checker.check_user_restriction(interaction)
        if not restriction:
            await interaction.response.send_message(restriction_reason or "Ваш доступ к боту ограничен.", ephemeral=True)
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
            url = construct_url(query, page=1)
            html = await fetch_html(session, url)
            soup = await asyncio.to_thread(BeautifulSoup, html, 'html.parser')
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
        logger.error(f"HTTP ошибка при выполнении /aidhentai для URL {url}: {e}\n{traceback.format_exc()}")
        await interaction.followup.send("Сайт не отвечает. Попробуйте позже.", ephemeral=False)
    except ParseError as e:
        logger.error(f"Ошибка парсинга при выполнении /aidhentai: {e}\n{traceback.format_exc()}")
        await interaction.followup.send("Ошибка при обработке данных. Попробуйте другой запрос.", ephemeral=False)
    except discord.errors.NotFound as e:
        logger.error(f"Взаимодействие не найдено: {e}\n{traceback.format_exc()}")
        await interaction.followup.send("Взаимодействие устарело. Попробуйте снова.", ephemeral=True)
    except Exception as e:
        logger.error(f"Неизвестная ошибка /aidhentai: {e}\n{traceback.format_exc()}")
        await interaction.followup.send("Произошла неизвестная ошибка. Обратитесь к администратору.", ephemeral=False)

@lru_cache(maxsize=1000)
def construct_url(query: Optional[str], page: int) -> str:
    """Формирует URL для запроса."""
    if page < 1:
        raise ValueError("Номер страницы должен быть положительным")
    if query and len(query.strip()) > 200:
        raise ValueError("Запрос слишком длинный")

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

@lru_cache(maxsize=500)
@backoff.on_exception(
    backoff.expo,
    (aiohttp.ClientError, asyncio.TimeoutError),
    max_tries=2,
    max_time=20,
    jitter=backoff.full_jitter
)
async def fetch_html(session: aiohttp.ClientSession, url: str) -> str:
    """Получает HTML страницы."""
    try:
        async with session.get(url) as response:
            if response.status != 200:
                raise HttpError(f"HTTP error! Status: {response.status} for {url}")
            return await response.text()
    except aiohttp.ClientError as e:
        raise HttpError(f"Ошибка при запросе {url}: {e}")

def parse_total_pages(soup: BeautifulSoup) -> int:
    """Парсит общее количество страниц."""
    pagination = soup.select_one('div.pagination-wrapper')
    if not pagination:
        return 1

    page_numbers = pagination.select('a.page-numbers, span.page-numbers.current')
    try:
        return max(int(elem.get_text()) for elem in page_numbers if elem.get_text().isdigit())
    except ValueError:
        return 1

@lru_cache(maxsize=100)
async def parse_search_results(session: aiohttp.ClientSession, soup: BeautifulSoup) -> List[SearchResult]:
    """Парсит результаты поиска."""
    try:
        elements = soup.select('a.lnk-blk[href][aria-label]')
        if not elements:
            return []

        banners = soup.select('div.anime-tb.pctr.rad1.por img[src]')
        descriptions = soup.select('div.description.dn p')
        metas = soup.select('p.meta.df.fww.aic.mgt.fz12.link-co.op05')

        semaphore = asyncio.Semaphore(12)  # Увеличен лимит для ускорения
        async def process_with_semaphore(i: int, e: BeautifulSoup) -> Optional[SearchResult]:
            async with semaphore:
                return await process_search_element(session, e, i, banners, descriptions, metas)

        tasks = [process_with_semaphore(i, e) for i, e in enumerate(elements)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if not isinstance(r, Exception) and r]
    except Exception as e:
        logger.error(f"Ошибка парсинга результатов: {e}\n{traceback.format_exc()}")
        return []

async def process_search_element(
    session: aiohttp.ClientSession,
    element: BeautifulSoup,
    index: int,
    banners: List[BeautifulSoup],
    descriptions: List[BeautifulSoup],
    metas: List[BeautifulSoup]
) -> Optional[SearchResult]:
    """Обрабатывает элемент поиска."""
    try:
        title = element.get('aria-label')
        url = element.get('href')
        if not (title and url and urlparse(url).scheme in ('http', 'https')):
            logger.warning(f"Некорректный элемент {index}: title={title}, url={url}")
            return None

        banner_url = banners[index].get('src') or "https://via.placeholder.com/100" if index < len(banners) else "https://via.placeholder.com/100"
        description = descriptions[index].get_text(strip=True) or 'Описание отсутствует.' if index < len(descriptions) else 'Описание отсутствует.'
        meta = '•'.join(item.strip() for item in metas[index].get_text().split('•') if item.strip()) if index < len(metas) else ''

        detail_html = await fetch_html(session, url)
        detail_soup = await asyncio.to_thread(BeautifulSoup, detail_html, 'html.parser')

        iframe = detail_soup.select_one('iframe[src]')
        video_link = iframe.get('src') if iframe else None
        if video_link:
            parsed = urlparse(video_link)
            if not parsed.scheme and video_link.startswith('//'):
                video_link = f"https:{video_link}"
            elif parsed.scheme not in ('http', 'https'):
                video_link = None

        image_url = await fetch_image_url(session, video_link) if video_link else None
        if not image_url:
            img_element = detail_soup.select_one('img[src*="content/previews"]')
            image_url = img_element.get('src') if img_element and urlparse(img_element.get('src')).scheme in ('http', 'https') else None

        additional_info = parse_additional_info(detail_soup)
        tags = [
            {'name': tag.get('aria-label'), 'url': tag.get('href')}
            for tag in detail_soup.select('div.genres.mgt.df.fww.por a.btn.fz12.rad1.mgr.mgb.gray-bg[href][aria-label]')
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
        logger.error(f"Ошибка обработки элемента {index} (URL: {url}): {e}\n{traceback.format_exc()}")
        return None

# Кэш для изображений
_image_cache: OrderedDict[str, Tuple[Optional[str], float]] = OrderedDict()
_image_cache_lock = asyncio.Lock()

async def fetch_image_url(session: aiohttp.ClientSession, video_url: Optional[str]) -> Optional[str]:
    """Получает URL изображения."""
    if not video_url or urlparse(video_url).scheme not in ('http', 'https'):
        return None

    async with _image_cache_lock:
        if video_url in _image_cache:
            image_url, timestamp = _image_cache[video_url]
            if asyncio.get_event_loop().time() - timestamp < 14400:  # Увеличено до 4 часов
                return image_url

    parsed = urlparse(video_url)
    try:
        socket.getaddrinfo(parsed.hostname, 443, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        async with _image_cache_lock:
            _image_cache[video_url] = (None, asyncio.get_event_loop().time())
            if len(_image_cache) > 100:
                _image_cache.popitem(last=False)
        return None

    try:
        html = await fetch_html(session, video_url)
        soup = await asyncio.to_thread(BeautifulSoup, html, 'html.parser')
        backdrop = soup.select_one('div.backdrop[style]')
        image_url = None
        if backdrop and backdrop.get('style'):
            match = re.search(r'url\(["\']?(https:\/\/nhplayer\.com\/content\/previews\/[^"\']+\.jpg)["\']?\)', backdrop.get('style'))
            image_url = match.group(1) if match else None

        async with _image_cache_lock:
            _image_cache[video_url] = (image_url, asyncio.get_event_loop().time())
            if len(_image_cache) > 100:
                _image_cache.popitem(last=False)

        return image_url
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.error(f"Ошибка сети при запросе изображения для {video_url}: {e}")
        async with _image_cache_lock:
            _image_cache[video_url] = (None, asyncio.get_event_loop().time())
            if len(_image_cache) > 100:
                _image_cache.popitem(last=False)
        return None
    except Exception as e:
        logger.error(f"Неизвестная ошибка при запросе изображения для {video_url}: {e}\n{traceback.format_exc()}")
        async with _image_cache_lock:
            _image_cache[video_url] = (None, asyncio.get_event_loop().time())
            if len(_image_cache) > 100:
                _image_cache.popitem(last=False)
        return None

async def clear_image_cache_periodically():
    """Периодическая очистка кэша изображений."""
    while True:
        await asyncio.sleep(14400)  # Очистка каждые 4 часа
        async with _image_cache_lock:
            current_time = asyncio.get_event_loop().time()
            for key in list(_image_cache.keys()):
                if current_time - _image_cache[key][1] > 14400:
                    _image_cache.pop(key)

def parse_additional_info(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Парсит дополнительную информацию."""
    return [
        {
            'name': translate_field_name(row.select_one('th.field').get_text(strip=True)),
            'value': row.select_one('td.value').get_text(strip=True)[:1024],  # Ограничение длины
            'inline': True
        }
        for row in soup.select('tbody tr:has(th.field, td.value)')[:12]
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

    asyncio.create_task(clear_image_cache_periodically())
    return wrapper