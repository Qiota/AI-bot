"""Общие константы, сообщения об ошибках и настройки для всего бота."""

from typing import Final

# ── Timeouts ──
REQUEST_TIMEOUT: Final[int] = 20
AUTOCOMPLETE_TIMEOUT: Final[float] = 2.5
VIEW_TIMEOUT: Final[int] = 3600
INACTIVITY_TIMEOUT: Final[int] = 3600

# ── File Limits ──
MAX_FILE_SIZE_DEFAULT: Final[int] = 10 * 1024 * 1024  # 10 MB
MAX_FILE_SIZE_TIER_2: Final[int] = 25 * 1024 * 1024  # 25 MB
MAX_FILE_SIZE_TIER_3: Final[int] = 100 * 1024 * 1024  # 100 MB
MAX_QUERY_LENGTH: Final[int] = 100

# ── Cache Settings ──
CONFIG_CACHE_TTL: Final[int] = 300  # 5 минут
TAG_CACHE_TTL: Final[int] = 7200
TAG_CACHE_SIZE: Final[int] = 5000
AUTOCOMPLETE_CACHE_SIZE: Final[int] = 1000
AUTOCOMPLETE_CACHE_TTL: Final[int] = 3600

# ── Command Cooldowns ──
COOLDOWN_TIME: Final[int] = 5
COOLDOWN_RATE: Final[int] = 1

# ── Error Messages ──
ERROR_GENERIC: Final[str] = "Произошла ошибка при выполнении команды."
ERROR_CONFIG: Final[str] = "Ошибка конфигурации бота."
ERROR_ACCESS_DENIED: Final[str] = "Бот не имеет доступа к этому каналу."
ERROR_NOT_READY: Final[str] = "Бот ещё не готов."
ERROR_GUILD_ONLY: Final[str] = "Команда только для серверов!"
ERROR_NSFW_ONLY: Final[str] = "Эта команда доступна только в NSFW-каналах или ЛС."
ERROR_RESTRICTED: Final[str] = "Ваш доступ к боту ограничен."
ERROR_ADMIN_ONLY: Final[str] = "Требуются права администратора или статус разработчика."
ERROR_BOT_NOT_IN_GUILD: Final[str] = "Бот отсутствует на этом сервере."

# ── Colors ──
COLOR_DEFAULT = 0x2F3136  # discord.Color.dark_grey() equivalent
COLOR_ERROR = 0xED4245
COLOR_SUCCESS = 0x57F287
COLOR_INFO = 0x5865F2

# ── Supported MIME Types ──
SUPPORTED_MIME_TYPES: Final[dict[str, str]] = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/gif': '.gif',
    'video/mp4': '.mp4',
    'video/webm': '.webm',
}

# ── Pagination ──
POSTS_PER_PAGE: Final[int] = 20
POSTS_PER_CHUNK: Final[int] = 10
ITEMS_PER_PAGE: Final[int] = 25

# ── Session Limits ──
SEMAPHORE_LIMIT: Final[int] = 15
MAX_WORKERS: Final[int] = 4
AIOHTTP_CONNECTOR_LIMIT: Final[int] = 50

