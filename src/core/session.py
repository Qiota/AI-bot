"""Unified aiohttp session management with shared connector pool."""

import aiohttp
from contextlib import asynccontextmanager
from .constants import REQUEST_TIMEOUT, AIOHTTP_CONNECTOR_LIMIT
from ..systemLog import logger

# Shared connector to avoid creating multiple TCP pools
_connector: aiohttp.TCPConnector | None = None


def _get_connector() -> aiohttp.TCPConnector:
    """Returns a lazily initialized shared TCP connector."""
    global _connector
    if _connector is None or _connector.closed:
        _connector = aiohttp.TCPConnector(limit=AIOHTTP_CONNECTOR_LIMIT)
        logger.debug("Создан общий aiohttp TCPConnector")
    return _connector


@asynccontextmanager
async def aiohttp_session(timeout: int = REQUEST_TIMEOUT):
    """Context manager yielding an aiohttp ClientSession with shared connector.
    
    Args:
        timeout: Total request timeout in seconds.
    """
    connector = _get_connector()
    client_timeout = aiohttp.ClientTimeout(total=timeout, connect=min(10, timeout // 2))
    async with aiohttp.ClientSession(timeout=client_timeout, connector=connector) as session:
        yield session


async def close_connector() -> None:
    """Gracefully close the shared connector. Call on bot shutdown."""
    global _connector
    if _connector and not _connector.closed:
        await _connector.close()
        logger.info("Общий aiohttp коннектор закрыт")
        _connector = None
