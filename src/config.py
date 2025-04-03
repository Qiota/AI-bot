from decouple import config, UndefinedValueError
from pathlib import Path
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
            self.FIREBASE_CRED_PATH = config("FIREBASE_CRED_PATH", default=None)
            self.FLASK_PORT = config("FLASK_PORT", default=8000, cast=int)
        except UndefinedValueError as e:
            logger.critical(f"Отсутствует обязательная переменная окружения: {e}")
            raise RuntimeError("Ошибка конфигурации") from e

    def validate(self):
        """Проверка корректности конфигурационных значений."""
        if not self.TOKEN:
            logger.critical("DISCORD_TOKEN обязателен для работы бота")
            raise ValueError("DISCORD_TOKEN обязателен для работы бота")

        if self.FIREBASE_CRED_PATH:
            if not Path(self.FIREBASE_CRED_PATH).is_file():
                logger.warning(f"Файл Firebase не найден: {self.FIREBASE_CRED_PATH}. Будет использовано локальное хранилище.")
                self.FIREBASE_CRED_PATH = None
            elif not self.FIREBASE_CRED_PATH.endswith(".json"):
                logger.warning(f"Файл Firebase должен быть в формате JSON: {self.FIREBASE_CRED_PATH}. Будет использовано локальное хранилище.")
                self.FIREBASE_CRED_PATH = None
        else:
            logger.warning("FIREBASE_CRED_PATH не указан. Будет использовано локальное хранилище.")