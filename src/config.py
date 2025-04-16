from decouple import config, UndefinedValueError
from .systemLog import logger

class BotConfig:
    """Конфигурация бота с валидацией переменных окружения."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_config()
        return cls._instance

    def _load_config(self):
        """Загрузка переменных из .env."""
        try:
            self.TOKEN = config("DISCORD_TOKEN")
            self.DEVELOPER_ID = config("DEVELOPER_ID", cast=int, default=None)
            self.FLASK_PORT = config("FLASK_PORT", cast=int, default=8000)
            self.FLASK_HOST = config("FLASK_HOST", default="0.0.0.0")
            self.ENV = config("ENV", default="development")
        except UndefinedValueError as e:
            logger.critical(f"Отсутствует переменная: {e}")
            raise RuntimeError("Ошибка конфигурации") from e

    def validate(self):
        """Проверка конфигурации."""
        if not self.TOKEN:
            logger.critical("DISCORD_TOKEN не указан")
            raise ValueError("DISCORD_TOKEN обязателен")
        if self.FLASK_PORT < 1024 or self.FLASK_PORT > 65535:
            logger.warning(f"FLASK_PORT={self.FLASK_PORT} вне допустимого диапазона, установлен 8000")
            self.FLASK_PORT = 8000

    @property
    def use_firebase(self) -> bool:
        """Проверка использования Firebase."""
        return False