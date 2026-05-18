"""GIF search service for Noxi."""

import random
from typing import Optional, List

import aiohttp
from ..systemLog import logger

TENOR_API_KEY = "AIzaSyAy2luBwE1U6VInjw3ChJXG0UBuq-BWBqM"
TENOR_SEARCH_URL = "https://tenor.googleapis.com/v2/search"
TENOR_TRENDING_URL = "https://tenor.googleapis.com/v2/featured"

CATEGORIES = {
    "happy": ["anime happy", "anime celebration", "anime yay", "anime smile"],
    "sad": ["anime sad", "anime crying", "anime tears"],
    "angry": ["anime angry", "anime rage", "anime frustrated"],
    "cute": ["anime cute", "kawaii", "anime blush", "chibi cute"],
    "flirty": ["anime flirt", "anime wink", "anime love"],
    "surprised": ["anime shocked", "anime surprised", "anime wow"],
    "relaxed": ["anime relaxed", "anime sleep", "anime peaceful"],
    "curious": ["anime curious", "anime thinking", "anime interest"],
    "mischievous": ["anime evil", "anime laugh", "anime smug"],
    "default": ["anime reaction", "anime gif"],
}

EMOJI_TO_CATEGORY = {
    "happy": ["радість", "щастя", "good", "ура", "хаха", "lol", "класно", "fun"],
    "sad": ["сум", "смуток", "bad", "погано", "жаль"],
    "angry": ["злість", "бля", "хам", "тупий"],
    "cute": ["милашка", "гарно", "красиво", "ава"],
    "flirty": ["кохання", "кохаю", "секс", "hot", "сексуально"],
    "surprised": ["вау", "ого", "неочікувано", "шок"],
    "relaxed": ["спати", "втомився", "відпочинок", "лінь"],
    "curious": ["цікаво", "як", "чому", "що це"],
    "mischievous": ["жарт", "прикол", "насміхаюсь"],
}


class GiphyService:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: dict[str, List[str]] = {}
        self._cache_time: dict[str, float] = {}
        self._cache_ttl = 600

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _get_category(self, text: str) -> str:
        text_lower = text.lower()
        for category, keywords in EMOJI_TO_CATEGORY.items():
            if any(kw in text_lower for kw in keywords):
                return category
        return random.choice(list(CATEGORIES.keys()))

    async def search_gif(self, query: str, limit: int = 10) -> Optional[str]:
        session = await self._get_session()
        cache_key = f"search:{query}"

        if cache_key in self._cache and (self._cache_time.get(cache_key, 0) + self._cache_ttl > random.random() * 10000):
            urls = self._cache[cache_key]
            return random.choice(urls) if urls else None

        try:
            params = {
                "q": query,
                "key": TENOR_API_KEY,
                "limit": limit,
                "media_filter": "gif,static",
                "contentfilter": "medium",
            }
            async with session.get(TENOR_SEARCH_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get("results", [])
                    if results:
                        urls = [
                            r["media_formats"]["gif"]["url"]
                            for r in results
                            if "media_formats" in r and "gif" in r["media_formats"]
                        ]
                        if urls:
                            self._cache[cache_key] = urls
                            return random.choice(urls)
        except Exception as e:
            logger.warning(f"[GIF] Search error: {e}")

        return None

    async def get_reaction_gif(self, context: str) -> Optional[str]:
        category = self._get_category(context)
        queries = CATEGORIES.get(category, CATEGORIES["default"])
        query = random.choice(queries)

        gif_url = await self.search_gif(query, limit=10)
        if not gif_url and category != "default":
            query = random.choice(CATEGORIES["default"])
            gif_url = await self.search_gif(query, limit=10)

        return gif_url

    async def get_random_gif(self, category: str = "default") -> Optional[str]:
        queries = CATEGORIES.get(category, CATEGORIES["default"])
        query = random.choice(queries)
        return await self.search_gif(query, limit=10)


_gif_service: Optional[GiphyService] = None


def get_gif_service() -> GiphyService:
    global _gif_service
    if _gif_service is None:
        _gif_service = GiphyService()
    return _gif_service