import asyncio
import io
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import backoff
import discord
from cachetools import TTLCache

from ....systemLog import logger
from .models import DanbooruAPIError, DanbooruPost

# Конфигурационные константы
MAX_FILE_SIZE_DEFAULT = 10 * 1024 * 1024
MAX_FILE_SIZE_TIER_2 = 25 * 1024 * 1024
MAX_FILE_SIZE_TIER_3 = 100 * 1024 * 1024
SUPPORTED_MIME_TYPES = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/gif': '.gif',
    'video/mp4': '.mp4',
    'video/webm': '.webm'
}
REQUEST_TIMEOUT = 20
SEMAPHORE_LIMIT = 15
TAG_CACHE_TTL = 7200
TAG_CACHE_SIZE = 5000
MAX_WORKERS = 4
POSTS_PER_PAGE = 20
POSTS_PER_CHUNK = 10

# Глобальное состояние
file_info_cache: Dict[str, Tuple[str, Optional[int]]] = {}
tag_suggestions_cache: TTLCache = TTLCache(maxsize=TAG_CACHE_SIZE, ttl=TAG_CACHE_TTL)
used_post_ids: set = set()
autocomplete_cache: TTLCache = TTLCache(maxsize=1000, ttl=3600)


def format_post_count(count: int) -> str:
    """Форматирует количество постов в читаемый вид."""
    if count < 1000:
        return str(count)
    elif count < 1000000:
        return f"{count / 1000:.1f}k".replace(".0k", "k")
    else:
        return f"{count / 1000000:.1f}M".replace(".0M", "M")


def parse_post_count(count_str: str) -> int:
    """Парсит форматированное количество постов в число."""
    count_str = count_str.strip().lower()
    try:
        if count_str.endswith('k'):
            return int(float(count_str[:-1]) * 1000)
        elif count_str.endswith('m'):
            return int(float(count_str[:-1]) * 1000000)
        else:
            return int(count_str)
    except (ValueError, TypeError) as e:
        logging.error(f"Ошибка парсинга post-count '{count_str}': {e}")
        raise ValueError(f"Неверный формат post-count: {count_str}")


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


async def process_api_data(
    data: List[Dict[str, Any]],
    data_type: str,
    session: Optional[aiohttp.ClientSession] = None
) -> List[Any]:
    """Обрабатывает данные API в параллельном режиме."""
    def process_post(item: Dict[str, Any]) -> Optional[DanbooruPost]:
        if not all(key in item for key in ["id", "file_url", "preview_file_url", "tag_string", "rating"]):
            return None
        post_id = item["id"]
        if post_id in used_post_ids:
            logging.debug(f"Пропущен дубликат поста с ID {post_id}")
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
            logging.warning(f"Пропущен некорректный элемент тега: {item}")
            return None
        tag_name = item["name"]
        post_count = item["post_count"]
        if not isinstance(post_count, int) or post_count < 0:
            logging.warning(f"Некорректное значение post_count для тега '{tag_name}': {post_count}")
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
            logging.debug(f"Ошибка проверки файла для {url}: {e}")
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

    logging.debug(f"Обработка {len(data)} элементов ({data_type}) выполнена за {asyncio.get_event_loop().time() - start_time:.2f} сек")
    return results


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
        logging.debug(f"Rate-Limit-Remaining for /counts/posts.json: {rate_limit_remaining}")
        if response.status != 200:
            error_map = {
                403: "Доступ к API ограничен.",
                429: "Превышен лимит запросов.",
                500: "Внутренняя ошибка сервера Danbooru."
            }
            response_text = await response.text()
            logging.error(f"API Error: {error_map.get(response.status, f'Неизвестная ошибка API: Код {response.status}')}, Response: {response_text[:200]}")
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
        logging.debug(f"Rate-Limit-Remaining for /posts.json: {rate_limit_remaining}")
        if response.status != 200:
            error_map = {
                403: "Доступ к API ограничен.",
                429: "Превышен лимит запросов.",
                500: "Внутренняя ошибка сервера Danbooru."
            }
            response_text = await response.text()
            logging.error(f"API Error: {error_map.get(response.status, f'Неизвестная ошибка API: Код {response.status}')}, Response: {response_text[:200]}")
            raise DanbooruAPIError(error_map.get(response.status, f"Неизвестная ошибка API: Код {response.status}"))

        data = await response.json()
        if not isinstance(data, list):
            raise DanbooruAPIError("Неправильный формат ответа от API")

        posts = await process_api_data(data, 'posts', session)
        logging.debug(f"Загрузка постов (страница {page}, теги: {tags}) выполнена за {asyncio.get_event_loop().time() - start_time:.2f} сек")
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
        logging.debug(f"Использован кэш для запроса '{query}': {len(tag_suggestions_cache[cache_key])} тегов")
        return tag_suggestions_cache[cache_key]

    start_time = asyncio.get_event_loop().time()
    url = "https://danbooru.donmai.us/tags.json"
    params = {
        "search[hide_empty]": "yes",
        "search[order]": "count",
        "limit": 25
    }
    if query:
        params["search[name_matches]"] = f"{query}*"
    else:
        params["limit"] = 25

    logging.debug(f"Запрос тегов с параметрами: {params}")

    async with session.get(url, params=params, timeout=REQUEST_TIMEOUT) as response:
        rate_limit_remaining = response.headers.get('X-Rate-Limit-Remaining', 'unknown')
        logging.debug(f"Rate-Limit-Remaining for /tags.json: {rate_limit_remaining}")
        if response.status != 200:
            error_map = {
                403: "Доступ к API ограничен.",
                429: "Превышен лимит запросов.",
                500: "Внутренняя ошибка сервера Danbooru."
            }
            response_text = await response.text()
            logging.error(f"API Error: {error_map.get(response.status, f'Неизвестная ошибка API: Код {response.status}')}, Response: {response_text[:200]}")
            raise DanbooruAPIError(error_map.get(response.status, f"Неизвестная ошибка API: Код {response.status}"))

        data = await response.json()
        if not isinstance(data, list):
            logging.error(f"Неправильный формат ответа от /tags.json: {data}")
            raise DanbooruAPIError("Неправильный формат ответа от API")

        tags = await process_api_data(data, 'tags')
        tag_suggestions_cache[cache_key] = tags
        logging.debug(f"Кэшировано {len(tags)} тегов для запроса '{query}' за {asyncio.get_event_loop().time() - start_time:.2f} сек")
        return tags

