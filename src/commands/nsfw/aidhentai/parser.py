import asyncio
import re
from typing import List, Optional, Dict
from urllib.parse import quote, urlencode

import aiohttp
import backoff
from bs4 import BeautifulSoup

from ....systemLog import logger
from .models import HttpError, ParseError, SearchResult


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

        semaphore = asyncio.Semaphore(3)

        async def process_with_semaphore(i: int, e: BeautifulSoup) -> Optional[SearchResult]:
            async with semaphore:
                return await process_search_element(session, e, i, banners, descriptions)

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
    descriptions: List[BeautifulSoup]
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

