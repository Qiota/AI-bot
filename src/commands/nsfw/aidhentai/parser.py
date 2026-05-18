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

        banners: List[BeautifulSoup] = list(soup.select("div.anime-tb.pctr.rad1.por img[src]"))
        descriptions: List[BeautifulSoup] = list(soup.select("div.description.dn p"))

        semaphore = asyncio.Semaphore(3)

        async def process_with_semaphore(i: int, e: BeautifulSoup) -> Optional[SearchResult]:
            async with semaphore:
                return await process_search_element(session, e, i, banners, descriptions)

        tasks = [process_with_semaphore(i, e) for i, e in enumerate(elements)]
        results: List[Optional[SearchResult]] = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, SearchResult)]
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
        raw_title = element.get("aria-label") or ""
        raw_url = element.get("href") or ""
        title = str(raw_title) if raw_title else ""
        url = str(raw_url) if raw_url else ""

        if not (title and url and re.match(r"^https?://", url)):
            logger.warning(f"Некорректный элемент {index}")
            return None

        banner_url = str(banners[index].get("src")) if (index < len(banners) and banners[index].get("src")) else "https://via.placeholder.com/100"
        raw_desc = descriptions[index].get_text(strip=True) if index < len(descriptions) else None
        description = str(raw_desc) if raw_desc else "Описание отсутствует."

        detail_html = await fetch_html(session, url)
        detail_soup = BeautifulSoup(detail_html, "html.parser")

        iframe = detail_soup.select_one("iframe[src]")
        raw_video = iframe.get("src") if iframe else None
        video_link: Optional[str] = None
        if raw_video:
            vp = str(raw_video)
            if vp.startswith("//"):
                video_link = f"https:{vp}"
            elif re.match(r"^https?://", vp):
                video_link = vp

        image_url = await fetch_image_url(session, video_link) if video_link else None
        if not image_url:
            img_el = detail_soup.select_one("img[src*='content/previews']")
            raw_src = img_el.get("src") if img_el else None
            if raw_src and re.match(r"^https?://", str(raw_src)):
                image_url = str(raw_src)

        additional_info = parse_additional_info(detail_soup)
        tags: List[Dict[str, str]] = [
            {"name": str(t.get("aria-label") or ""), "url": str(t.get("href") or "")}
            for t in detail_soup.select("div.genres.mgt.df.fww.por a.btn.fz12.rad1.mgr.mgb.gray-bg[href][aria-label]")[:5]
            if t.get("aria-label") and t.get("href")
        ]

        return SearchResult(
            title=title,
            url=url,
            banner_url=banner_url,
            description=description,
            video_link=video_link,
            image_url=image_url,
            additional_info=additional_info,
            tags=tags,
        )
    except Exception as e:
        logger.error(f"Ошибка обработки элемента {index}: {e}")
        return None


async def fetch_image_url(session: aiohttp.ClientSession, video_url: Optional[str]) -> Optional[str]:
    """Получает URL изображения без кэширования."""
    if not video_url or not re.match(r"^https?://", str(video_url)):
        return None

    try:
        html = await fetch_html(session, video_url)
        soup = BeautifulSoup(html, "html.parser")
        backdrop = soup.select_one("div.backdrop[style]")
        raw_style = backdrop.get("style") if backdrop else None
        if backdrop and raw_style:
            m = re.search(r'url\(["\']?(https://nhplayer\.com/content/previews/[^"\']+\.jpg)["\']?\)', str(raw_style))
            return m.group(1) if m else None
        return None
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.error(f"Ошибка сети при запросе изображения: {e}")
        return None
    except Exception as e:
        logger.error(f"Неизвестная ошибка при запросе изображения: {e}")
        return None


def parse_additional_info(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Парсит дополнительную информацию."""
    result: List[Dict[str, str]] = []
    for row in soup.select("tbody tr:has(th.field, td.value)")[:5]:
        th_el = row.select_one("th.field")
        td_el = row.select_one("td.value")
        if th_el is None or td_el is None:
            continue
        th_text = th_el.get_text(strip=True)
        translated = translate_field_name(th_text)
        if translated:
            result.append({
                "name": translated,
                "value": td_el.get_text(strip=True)[:512],
                "inline": True
            })
    return result


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

