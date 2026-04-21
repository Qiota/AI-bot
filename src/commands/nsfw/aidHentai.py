import discord
from discord import app_commands, Embed, ButtonStyle, Interaction
from discord.ui import Modal, TextInput, Button, View
import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import quote, urlencode
import asyncio
import re
from typing import List, Optional, Dict
from dataclasses import dataclass
from contextlib import asynccontextmanager
from ...systemLog import logger
from ..restrict import check_bot_access, restrict_command_execution
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
    video_link: Optional[str]
    image_url: Optional[str]
    additional_info: List[Dict[str, str]]
    tags: List[Dict[str, str]]

@asynccontextmanager
async def aiohttp_session():
    """Контекстный менеджер для aiohttp сессии."""
    timeout = aiohttp.ClientTimeout(total=15, connect=5)
    connector = aiohttp.TCPConnector(limit=5, force_close=False)
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
    """Упрощённый View для навигации по результатам поиска."""
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
        self.message: Optional[discord.Message] = None
        self.last_interaction = asyncio.get_event_loop().time()
        self.inactivity_timeout = 60
        self.inactivity_task: Optional[asyncio.Task] = None
        self.update_buttons()
        self.start_inactivity_timer()

    def start_inactivity_timer(self) -> None:
        """Запускает таймер бездействия."""
        self.inactivity_task = asyncio.create_task(self.check_inactivity())

    async def check_inactivity(self) -> None:
        """Проверяет бездействие с интервалом 5 секунд."""
        try:
            while True:
                elapsed = asyncio.get_event_loop().time() - self.last_interaction
                if elapsed >= self.inactivity_timeout:
                    self.disable_navigation_buttons()
                    if self.message:
                        await self.message.edit(view=self)
                    break
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass

    def disable_navigation_buttons(self) -> None:
        """Отключает навигационные кнопки."""
        for item in self.children:
            if isinstance(item, Button) and item.label in ["⬅️", "➡️", "⌛", "🔢"]:
                item.disabled = True

    def _create_button(self, label: str, style: ButtonStyle, disabled: bool, callback=None, url: Optional[str] = None):
        """Создаёт кнопку с заданными параметрами."""
        button = Button(label=label, style=style, disabled=disabled, url=url)
        if callback:
            button.callback = callback
        return button

    def update_buttons(self) -> None:
        """Обновляет состояние кнопок."""
        self.clear_items()

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
            logger.error(f"Ошибка при открытии модального окна: {e}")
            await interaction.followup.send(
                "Не удалось открыть окно ввода. Попробуйте снова.",
                ephemeral=True
            )

    def is_valid_url(self, url: Optional[str]) -> bool:
        """Проверяет валидность URL с помощью regex."""
        if not url:
            return False
        return bool(re.match(r'^https?://', url))

    async def load_page(self, target_page: int) -> bool:
        """Загружает результаты для указанной страницы."""
        try:
            async with aiohttp_session() as session:
                url = construct_url(self.query, target_page)
                html = await fetch_html(session, url)
                soup = BeautifulSoup(html, 'html.parser')
                new_results = await parse_search_results(session, soup)
                if not new_results:
                    return False
                self.results = new_results
                self.current_page = target_page
                self.current_index = 0
                return True
        except (HttpError, ParseError, Exception) as e:
            logger.error(f"Ошибка при загрузке страницы {target_page}: {e}")
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
            await interaction.followup.send("Взаимодействие устарело.", ephemeral=True)
            return

        new_index = self.current_index + direction
        if new_index < 0 and self.current_page > 1:
            if await self.load_page(self.current_page - 1):
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
            logger.error(f"Ошибка при обновлении сообщения: {e}")
            await interaction.followup.send("Не удалось обновить сообщение.", ephemeral=True)

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
            logger.error(f"Взаимодействие не найдено: {e}")
            self.is_loading = False
            self.loading_button = None
            self.update_buttons()
            await interaction.followup.send("Взаимодействие устарело.", ephemeral=True)
            return
        except discord.errors.InteractionResponded:
            pass

        if page != self.current_page:
            if not await self.load_page(page):
                self.is_loading = False
                self.loading_button = None
                self.update_buttons()
                await interaction.followup.send(
                    f"Не удалось загрузить страницу {page}.",
                    ephemeral=True
                )
                return

        if title_index >= len(self.results):
            self.is_loading = False
            self.loading_button = None
            self.update_buttons()
            await interaction.followup.send(
                f"На странице {page} только {len(self.results)} тайтлов.",
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
            logger.error(f"Ошибка при редактировании сообщения: {e}")
            await interaction.followup.send("Не удалось обновить сообщение.", ephemeral=True)

    def create_embed(self) -> Embed:
        """Создаёт Embed для текущего результата."""
        result = self.results[self.current_index]
        description = result.description[:200] + ("..." if len(result.description) > 200 else "")
        if not result.image_url:
            description += "\n⚠️ Изображение отсутствует."

        if result.tags:
            tags_text = f"\n\n🏷 Теги: {', '.join(f'[{tag['name']}]({tag['url']})' for tag in result.tags[:5])}"
            if len(description) + len(tags_text) > 2000:
                description = description[:2000 - len(tags_text) - 3] + "..."
            description += tags_text

        embed = Embed(
            title=f"🎬 {result.title}"[:256],
            url=result.url,
            description=description[:2048],
            color=0xFF5733
        )

        embed.set_thumbnail(url=result.banner_url or "https://via.placeholder.com/100")
        if result.image_url:
            embed.set_image(url=result.image_url)

        for field in result.additional_info[:5]:
            embed.add_field(
                name=f"🔹 {field['name']}"[:256],
                value=field['value'][:512],
                inline=True
            )

        embed.set_footer(text=f"{self.current_index + 1}/{len(self.results)} | Страница {self.current_page}/{self.total_pages}")
        return embed

    async def on_timeout(self) -> None:
        """Обрабатывает таймаут view."""
        self.disable_navigation_buttons()
        if self.inactivity_task:
            self.inactivity_task.cancel()
        try:
            if self.message:
                await self.message.edit(view=self)
        except discord.DiscordException as e:
            logger.error(f"Ошибка при отключении view: {e}")

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

@backoff.on_exception(
    backoff.expo,
    (aiohttp.ClientError, asyncio.TimeoutError),
    max_tries=2,
    max_time=10,
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
        return 1

    page_numbers = pagination.select('a.page-numbers, span.page-numbers.current')
    try:
        return max(int(elem.get_text()) for elem in page_numbers if elem.get_text().isdigit())
    except ValueError:
        return 1

async def parse_search_results(session: aiohttp.ClientSession, soup: BeautifulSoup) -> List[SearchResult]:
    """Парсит результаты поиска."""
    try:
        elements = soup.select('a.lnk-blk[href][aria-label]')
        if not elements:
            return []

        banners = soup.select('div.anime-tb.pctr.rad1.por img[src]')
        descriptions = soup.select('div.description.dn p')
        metas = soup.select('p.meta.df.fww.aic.mgt.fz12.link-co.op05')

        semaphore = asyncio.Semaphore(3)
        async def process_with_semaphore(i: int, e: BeautifulSoup) -> Optional[SearchResult]:
            async with semaphore:
                return await process_search_element(session, e, i, banners, descriptions, metas)

        tasks = [process_with_semaphore(i, e) for i, e in enumerate(elements)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if not isinstance(r, Exception) and r]
    except Exception as e:
        logger.error(f"Ошибка парсинга результатов: {e}")
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
        if not (title and url and re.match(r'^https?://', url)):
            logger.warning(f"Некорректный элемент {index}")
            return None

        banner_url = banners[index].get('src') or "https://via.placeholder.com/100" if index < len(banners) else "https://via.placeholder.com/100"
        description = descriptions[index].get_text(strip=True) or 'Описание отсутствует.' if index < len(descriptions) else 'Описание отсутствует.'

        detail_html = await fetch_html(session, url)
        detail_soup = BeautifulSoup(detail_html, 'html.parser')

        iframe = detail_soup.select_one('iframe[src]')
        video_link = iframe.get('src') if iframe else None
        if video_link:
            if video_link.startswith('//'):
                video_link = f"https:{video_link}"
            elif not re.match(r'^https?://', video_link):
                video_link = None

        image_url = await fetch_image_url(session, video_link) if video_link else None
        if not image_url:
            img_element = detail_soup.select_one('img[src*="content/previews"]')
            image_url = img_element.get('src') if img_element and re.match(r'^https?://', img_element.get('src')) else None

        additional_info = parse_additional_info(detail_soup)
        tags = [
            {'name': tag.get('aria-label'), 'url': tag.get('href')}
            for tag in detail_soup.select('div.genres.mgt.df.fww.por a.btn.fz12.rad1.mgr.mgb.gray-bg[href][aria-label]')[:5]
            if tag.get('aria-label') and tag.get('href')
        ]

        return SearchResult(
            title=title,
            url=url,
            banner_url=banner_url,
            description=description,
            video_link=video_link,
            image_url=image_url,
            additional_info=additional_info,
            tags=tags
        )
    except Exception as e:
        logger.error(f"Ошибка обработки элемента {index}: {e}")
        return None

async def fetch_image_url(session: aiohttp.ClientSession, video_url: Optional[str]) -> Optional[str]:
    """Получает URL изображения без кэширования."""
    if not video_url or not re.match(r'^https?://', video_url):
        return None

    try:
        html = await fetch_html(session, video_url)
        soup = BeautifulSoup(html, 'html.parser')
        backdrop = soup.select_one('div.backdrop[style]')
        if backdrop and backdrop.get('style'):
            match = re.search(r'url\(["\']?(https:\/\/nhplayer\.com\/content\/previews\/[^"\']+\.jpg)["\']?\)', backdrop.get('style'))
            return match.group(1) if match else None
        return None
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.error(f"Ошибка сети при запросе изображения: {e}")
        return None
    except Exception as e:
        logger.error(f"Неизвестная ошибка при запросе изображения: {e}")
        return None

def parse_additional_info(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Парсит дополнительную информацию."""
    return [
        {
            'name': translate_field_name(row.select_one('th.field').get_text(strip=True)),
            'value': row.select_one('td.value').get_text(strip=True)[:512],
            'inline': True
        }
        for row in soup.select('tbody tr:has(th.field, td.value)')[:5]
        if row.select_one('th.field') and row.select_one('td.value') and translate_field_name(row.select_one('th.field').get_text(strip=True))
    ]

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

@backoff.on_exception(
    backoff.expo,
    (aiohttp.ClientError, asyncio.TimeoutError, discord.errors.NotFound),
    max_tries=2,
    max_time=5,
    jitter=backoff.full_jitter
)
async def aidhentai(interaction: discord.Interaction, bot_client, query: Optional[str] = None) -> None:
    """Команда /aidhentai: Поиск по AnimeIdHentai."""
    guild_id = str(interaction.guild.id) if interaction.guild else "DM"
    channel_id = str(interaction.channel.id) if interaction.channel else "DM"

    # Early defer to avoid 3s timeout
    try:
        await interaction.response.defer(ephemeral=True)
    except discord.errors.NotFound:
        logger.error(f"Interaction expired early in aidhentai")
        return

    if interaction.guild is None or interaction.guild not in [g for g in bot_client.bot.guilds]:
        await interaction.followup.send("Бот отсутствует на сервере!", ephemeral=True)
        return

    result, reason = await restrict_command_execution(interaction, bot_client)
    if not result:
        await interaction.followup.send(reason or "Конфигурация сервера не найдена!", ephemeral=True)
        return

    result, reason = await check_bot_access(interaction, bot_client)
    if not result:
        await interaction.followup.send(reason, ephemeral=True)
        return

    if interaction.guild and not interaction.channel.nsfw:
        await interaction.response.send_message("Команда доступна только в NSFW-каналах или ЛС.", ephemeral=True)
        return

    if interaction.guild:
        restriction, restriction_reason = await checker.check_user_restriction(interaction)
        if not restriction:
            await interaction.followup.send(restriction_reason or "Доступ ограничен.", ephemeral=True)
            return

    try:
        async with aiohttp_session() as session:
            url = construct_url(query, page=1)
            html = await fetch_html(session, url)
            soup = BeautifulSoup(html, 'html.parser')
            new_results = await parse_search_results(session, soup)
            total_pages = parse_total_pages(soup)

            if not new_results:
                await interaction.followup.send("Ничего не найдено.", ephemeral=False)
                return

            view = NavigationView(new_results, interaction.user, query, current_page=1, total_pages=total_pages)
            embed = view.create_embed()
            message = await interaction.followup.send(embed=embed, view=view, ephemeral=False)
            view.message = message

    except HttpError as e:
        logger.error(f"HTTP ошибка для URL {url}: {e}")
        await interaction.followup.send("Сайт не отвечает.", ephemeral=False)
    except ParseError as e:
        logger.error(f"Ошибка парсинга: {e}")
        await interaction.followup.send("Ошибка обработки данных.", ephemeral=False)
    except discord.errors.NotFound as e:
        logger.error(f"Взаимодействие не найдено: {e}")
        await interaction.followup.send("Взаимодействие устарело.", ephemeral=True)
    except Exception as e:
        logger.error(f"Неизвестная ошибка: {e}")
        await interaction.followup.send("Произошла ошибка.", ephemeral=False)

def create_command(bot_client) -> app_commands.Command:
    """Создаёт слеш-команду /aidhentai."""
    @app_commands.command(name="aidhentai", description=description)
    @app_commands.describe(query="Поисковый запрос")
    async def wrapper(interaction: discord.Interaction, query: Optional[str] = None) -> None:
        await aidhentai(interaction, bot_client, query)

    @wrapper.error
    async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        guild_id = str(interaction.guild.id) if interaction.guild else "DM"
        channel_id = str(interaction.channel.id) if interaction.channel else "DM"
        logger.error(f"Ошибка /aidhentai для {interaction.user.id} в гильдии {guild_id}, канал {channel_id}: {error}")
        try:
            await interaction.followup.send("Ошибка при выполнении команды.", ephemeral=True)
        except (discord.errors.NotFound, discord.errors.InteractionResponded):
            logger.debug("Interaction expired in wrapper error handler")
        except discord.DiscordException as e:
            logger.error(f"Не удалось отправить сообщение об ошибке: {e}")

    return wrapper