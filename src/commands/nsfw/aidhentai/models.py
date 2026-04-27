from dataclasses import dataclass
from typing import List, Optional, Dict


class HttpError(Exception):
    pass


class ParseError(Exception):
    pass


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

