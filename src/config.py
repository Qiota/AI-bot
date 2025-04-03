from decouple import config, UndefinedValueError
from pathlib import Path
from .logging_config import logger  

class BotConfig:
    """Конфигурация бота с валидацией переменных окружения и .env файла."""

    def __init__(self):
        self._load_config()
        self.validate()

    def _load_config(self):
        """Загрузка конфигурационных переменных из .env файла."""
        try:
            self.TOKEN = config("DISCORD_TOKEN")
            self.DEVELOPER_ID = config("DEVELOPER_ID", cast=int) or None
            self.FIREBASE_CRED_PATH = config("FIREBASE_CRED_PATH")
            self.FLASK_PORT = config("FLASK_PORT", default=8000, cast=int)
        except UndefinedValueError as e:
            logger.critical(f"Отсутствует обязательная переменная окружения: {e}")
            raise RuntimeError("Ошибка конфигурации") from e

    def validate(self):
        """Проверка корректности конфигурационных значений."""
        if not self.TOKEN:
            raise ValueError("DISCORD_TOKEN обязателен для работы бота")

        if not Path(self.FIREBASE_CRED_PATH).is_file():
            raise FileNotFoundError(f"Файл Firebase не найден: {self.FIREBASE_CRED_PATH}")

        if not self.FIREBASE_CRED_PATH.endswith(".json"):
            raise ValueError("Файл Firebase должен быть в формате JSON")