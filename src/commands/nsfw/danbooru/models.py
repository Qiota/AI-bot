from dataclasses import dataclass
from typing import List, Optional


class DanbooruAPIError(Exception):
    """Исключение для ошибок Danbooru API."""
    pass


@dataclass
class DanbooruPost:
    id: int
    file_url: str
    preview_url: str
    tags: List[str]
    rating: str
    source: Optional[str]
    created_at: str

