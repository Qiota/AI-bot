"""Shared exports for command modules.

Re-exports common utilities to reduce boilerplate in individual command files.
"""

from ..core.middleware import (
    require_bot_access,
    require_bot_ready,
    require_guild,
    require_nsfw,
    require_admin_or_developer,
    require_permissions,
)
from ..core.session import aiohttp_session, close_connector
from ..core.constants import (
    ERROR_GENERIC,
    ERROR_CONFIG,
    ERROR_ACCESS_DENIED,
    MAX_FILE_SIZE_DEFAULT,
    MAX_FILE_SIZE_TIER_2,
    MAX_FILE_SIZE_TIER_3,
    SUPPORTED_MIME_TYPES,
    REQUEST_TIMEOUT,
    SEMAPHORE_LIMIT,
)

__all__ = [
    "require_bot_access",
    "require_bot_ready",
    "require_guild",
    "require_nsfw",
    "require_admin_or_developer",
    "require_permissions",
    "aiohttp_session",
    "close_connector",
    "ERROR_GENERIC",
    "ERROR_CONFIG",
    "ERROR_ACCESS_DENIED",
    "MAX_FILE_SIZE_DEFAULT",
    "MAX_FILE_SIZE_TIER_2",
    "MAX_FILE_SIZE_TIER_3",
    "SUPPORTED_MIME_TYPES",
    "REQUEST_TIMEOUT",
    "SEMAPHORE_LIMIT",
]
