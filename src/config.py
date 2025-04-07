from decouple import config, UndefinedValueError
from .logging_config import logger

class BotConfig:
    """Конфигурация бота с валидацией переменных окружения и .env файла."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BotConfig, cls).__new__(cls)
            cls._instance._load_config()
            cls._instance.validate()
        return cls._instance

    def _load_config(self):
        """Загрузка конфигурационных переменных из .env файла."""
        try:
            self.TOKEN = config("DISCORD_TOKEN")
            self.DEVELOPER_ID = config("DEVELOPER_ID", cast=int, default=None)
            self.FLASK_PORT = config("FLASK_PORT", default=8000, cast=int)
        except UndefinedValueError as e:
            logger.critical(f"Отсутствует переменная: {e}")
            raise RuntimeError("Ошибка конфигурации") from e

    def get(self, key, default=None):
        """Получение значения атрибута по ключу."""
        return getattr(self, key, default)

    def validate(self):
        """Проверка корректности конфигурационных значений."""
        if not self.TOKEN:
            logger.critical("DISCORD_TOKEN обязателен")
            raise ValueError("DISCORD_TOKEN обязателен")

    @property
    def use_firebase(self) -> bool:
        """Проверка использования Firebase (всегда False, так как Firebase не используется)."""
        return False