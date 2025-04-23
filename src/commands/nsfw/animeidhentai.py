import discord
from discord import app_commands, Embed, ButtonStyle, Interaction
from discord.ui import Button, View
import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import quote, urlencode, urlparse
import asyncio
from datetime import datetime
from typing import List, Optional, Dict
from dataclasses import dataclass
from functools import lru_cache
from contextlib import asynccontextmanager
from ...systemLog import logger

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

@asynccontextmanager
async def aiohttp_session():
    """Контекстный менеджер для aiohttp сессии."""
    timeout = aiohttp.ClientTimeout(total=10, connect=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        yield session

class NavigationView(View):
    """Кастомный View для навигации по результатам поиска."""
    def __init__(self, results: List[SearchResult], original_user: discord.User, current_index: int = 0, timeout: int = 300):
        super().__init__(timeout=timeout)
        self.results = results
        self.original_user = original_user
        self.current_index = current_index
        self.is_loading = False
        self.loading_button = None
        self.embed_cache = {}
        self.update_buttons()

    def update_buttons(self):
        """Обновляет состояние кнопок."""
        self.clear_items()
        
        # Кнопка "Назад"
        back_label = "⌛" if self.is_loading and self.loading_button == "back" else "⬅️"
        back_button = Button(
            label=back_label,
            style=ButtonStyle.gray,
            disabled=self.current_index == 0 or self.is_loading
        )
        back_button.callback = lambda i: self.navigate(i, -1, "back")
        self.add_item(back_button)
        
        # Кнопка "Смотреть онлайн"
        video_link = self.results[self.current_index].video_link
        watch_button = Button(
            label="📺 Смотреть онлайн",
            style=ButtonStyle.link,
            url=video_link,
            disabled=not video_link or not self.is_valid_url(video_link)
        )
        self.add_item(watch_button)
        
        # Кнопка "Вперед"
        next_label = "⌛" if self.is_loading and self.loading_button == "next" else "➡️"
        next_button = Button(
            label=next_label,
            style=ButtonStyle.gray,
            disabled=self.current_index == len(self.results) - 1 or self.is_loading
        )
        next_button.callback = lambda i: self.navigate(i, 1, "next")
        self.add_item(next_button)

    def is_valid_url(self, url: Optional[str]) -> bool:
        """Проверяет валидность URL для Discord."""
        if not url:
            return False
        parsed = urlparse(url)
        return parsed.scheme in ('http', 'https', 'discord')

    async def navigate(self, interaction: Interaction, direction: int, button: str):
        """Общая логика навигации."""
        if interaction.user != self.original_user:
            await interaction.response.send_message(
                "Только пользователь, вызвавший команду, может использовать эти кнопки.",
                ephemeral=True
            )
            return

        if self.is_loading:
            return
        self.is_loading = True
        self.loading_button = button
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_embed(), view=self)
        
        new_index = self.current_index + direction
        if 0 <= new_index < len(self.results):
            self.current_index = new_index
        self.is_loading = False
        self.loading_button = None
        self.update_buttons()
        await interaction.edit_original_response(embed=self.create_embed(), view=self)

    def create_embed(self) -> Embed:
        """Создает Embed для текущего результата."""
        if self.current_index in self.embed_cache:
            return self.embed_cache[self.current_index]

        result = self.results[self.current_index]
        description = result.description[:200] + ("..." if len(result.description) > 200 else "")
        embed = Embed(
            title=f"🎬 {result.title}",
            url=result.url,
            description=description,
            color=0xFF5733
        )
        
        # Установка изображений
        embed.set_thumbnail(url=result.banner_url or "https://via.placeholder.com/100")
        if result.image_url:
            embed.set_image(url=result.image_url)
        
        # Добавление полей (максимум 12)
        for field in result.additional_info[:12]:
            embed.add_field(name=f"🔹 {field['name']}", value=field['value'], inline=True)
        
        # Футер
        embed.set_footer(text=f"{self.current_index + 1}/{len(self.results)} • {result.meta}")
        
        self.embed_cache[self.current_index] = embed
        return embed

    async def on_timeout(self):
        """Отключает кнопки после 5 минут бездействия."""
        self.is_loading = False
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
            logger.info(f"Кнопки навигации отключены по таймауту (5 минут) для сообщения {self.message.id}")
        except Exception as e:
            logger.error(f"Ошибка при отключении кнопок по таймауту: {e}")

async def aihentai(interaction: discord.Interaction, query: Optional[str] = None, page: int = 1, year: Optional[int] = None, tag: Optional[str] = None) -> None:
    """Команда /aihentai: Поиск по AnimeIdHentai."""
    if interaction.guild is not None and not interaction.channel.nsfw:
        await interaction.response.send_message(
            "Эта команда доступна только в NSFW-каналах или ЛС.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=False)

    try:
        async with aiohttp_session() as session:
            url = construct_url(query, page, year, tag)
            html = await fetch_html(session, url)
            soup = BeautifulSoup(html, 'html.parser')
            results = await parse_search_results(session, soup)
            
            if not results:
                await interaction.followup.send("По вашему запросу ничего не найдено.", ephemeral=False)
                return
            
            view = NavigationView(results, original_user=interaction.user)
            embed = view.create_embed()
            message = await interaction.followup.send(embed=embed, view=view, ephemeral=False)
            view.message = message

    except HttpError as e:
        logger.error(f"HTTP ошибка для {interaction.user.id}: {e}")
        await interaction.followup.send("Ошибка при запросе к сайту.", ephemeral=False)
    except ParseError as e:
        logger.error(f"Ошибка парсинга для {interaction.user.id}: {e}")
        await interaction.followup.send("Ошибка при обработке данных.", ephemeral=False)
    except Exception as e:
        logger.error(f"Неизвестная ошибка /aihentai для {interaction.user.id}: {e}")
        await interaction.followup.send("Произошла неизвестная ошибка.", ephemeral=False)

@lru_cache(maxsize=1000)
def construct_url(query: Optional[str], page: int, year: Optional[int], tag: Optional[str]) -> str:
    """Формирует URL для запроса."""
    if page < 1:
        raise ValueError("Номер страницы должен быть положительным")

    base_url = "https://animeidhentai.com"
    params = {}
    if query and query.strip():
        params["s"] = query.strip()
    else:
        params["s"] = "2025"  # Поиск по умолчанию
    if page > 1:
        params["page"] = str(page)
    if year and 1900 <= year <= datetime.now().year:
        params["year"] = str(year)
    if tag:
        params["tag"] = tag.strip()

    query_string = urlencode(params, quote_via=quote)
    return f"{base_url}/?{query_string}"

async def fetch_html(session: aiohttp.ClientSession, url: str) -> str:
    """Получает HTML страницы."""
    async with session.get(url) as response:
        if response.status != 200:
            raise HttpError(f"HTTP error! Status: {response.status}")
        return await response.text()

async def parse_search_results(session: aiohttp.ClientSession, soup: BeautifulSoup) -> List[SearchResult]:
    """Парсит результаты поиска."""
    elements = soup.select('a.lnk-blk')[:20]
    if not elements:
        return []

    tasks = [process_search_element(session, soup, i, e) for i, e in enumerate(elements)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    return [r for r in results if not isinstance(r, Exception) and r]

async def process_search_element(session: aiohttp.ClientSession, soup: BeautifulSoup, index: int, element: BeautifulSoup) -> Optional[SearchResult]:
    """Обрабатывает элемент поиска."""
    try:
        title = element.get('aria-label')
        if not title:
            logger.warning(f"Элемент {index}: Отсутствует title (aria-label)")
            return None
        
        url = element.get('href')
        if not url:
            logger.warning(f"Элемент {index}: Отсутствует URL (href)")
            return None

        # Проверка наличия элементов для banner_url
        banner_elements = soup.select('div.anime-tb.pctr.rad1.por img')
        if index >= len(banner_elements):
            logger.warning(f"Элемент {index}: Отсутствует banner_url, найдено {len(banner_elements)} элементов")
            return None
        banner_url = banner_elements[index].get('src') or "https://via.placeholder.com/100"

        # Проверка наличия элементов для description
        description_elements = soup.select('div.description.dn p')
        if index >= len(description_elements):
            logger.warning(f"Элемент {index}: Отсутствует description, найдено {len(description_elements)} элементов")
            return None
        description = description_elements[index].get_text(strip=True) or 'Описание отсутствует.'

        # Проверка наличия элементов для meta
        meta_elements = soup.select('p.meta.df.fww.aic.mgt.fz12.link-co.op05')
        if index >= len(meta_elements):
            logger.warning(f"Элемент {index}: Отсутствует meta, найдено {len(meta_elements)} элементов")
            return None
        meta = '•'.join(item.strip() for item in meta_elements[index].get_text().split('•'))

        async with session.get(url) as response:
            if response.status != 200:
                logger.warning(f"Элемент {index}: HTTP ошибка при запросе {url}, статус: {response.status}")
                return None
            detail_html = await response.text()

        detail_soup = BeautifulSoup(detail_html, 'html.parser')
        video_link = detail_soup.select_one('iframe').get('src')
        
        if video_link:
            parsed = urlparse(video_link)
            if not parsed.scheme and video_link.startswith('//'):
                video_link = f"https:{video_link}"
            elif parsed.scheme not in ('http', 'https', 'discord'):
                video_link = None

        image_url = await fetch_image_url(session, video_link) if video_link else None
        additional_info = parse_additional_info(detail_soup)

        return SearchResult(
            title=title,
            url=url,
            banner_url=banner_url,
            description=description,
            meta=meta,
            video_link=video_link,
            image_url=image_url,
            additional_info=additional_info
        )
    
    except Exception as e:
        logger.warning(f"Элемент {index}: Ошибка обработки: {str(e)}")
        return None

@lru_cache(maxsize=100)
async def fetch_image_url(session: aiohttp.ClientSession, video_url: Optional[str]) -> Optional[str]:
    """Получает URL изображения."""
    if not video_url or urlparse(video_url).scheme not in ('http', 'https'):
        return None

    try:
        async with session.get(video_url) as response:
            if response.status != 200:
                return None
            body = await response.text()

        soup = BeautifulSoup(body, 'html.parser')
        backdrop = soup.select_one('div.backdrop')
        if backdrop and backdrop.get('style'):
            import re
            match = re.search(r'url\(["\']?(https:\/\/nhplayer\.com\/content\/previews\/[^"\']+\.jpg)["\']?\)', backdrop.get('style'))
            return match.group(1) if match else None
        return None

    except Exception:
        return None

def parse_additional_info(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Парсит дополнительную информацию."""
    return [
        {'name': translate_field_name(row.select_one('th.field').get_text(strip=True)), 'value': row.select_one('td.value').get_text(strip=True), 'inline': True}
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
        'Season': 'Сезон:',
        'Tags': 'Теги:'
    }
    return translations.get(field_name, field_name)

def create_command(bot_client=None):
    """Создает слеш-команду /aihentai."""
    @app_commands.command(name="aihentai", description=description)
    @app_commands.describe(
        query="Поисковый запрос",
        page="Номер страницы",
        year="Год выпуска",
        tag="Тег"
    )
    async def wrapper(interaction: discord.Interaction, query: Optional[str] = None, page: int = 1, year: Optional[int] = None, tag: Optional[str] = None) -> None:
        await aihentai(interaction, query, page, year, tag)
    
    @wrapper.error
    async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        logger.error(f"Ошибка /aihentai для {interaction.user.id}: {error}")
        await interaction.response.send_message("Ошибка при выполнении команды.", ephemeral=False)
    
    return wrapper