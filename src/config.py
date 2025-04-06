from decouple import config, UndefinedValueError
from pathlib import Path
import json
import firebase_admin
from firebase_admin import credentials
from .logging_config import logger  

class BotConfig:
    """Конфигурация бота с валидацией переменных окружения и .env файла."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BotConfig, cls).__new__(cls)
            cls._instance._load_config()
            cls._instance.validate()
            cls._instance._initialize_firebase()
        return cls._instance

    def _load_config(self):
        """Загрузка конфигурационных переменных из .env файла."""
        try:
            self.TOKEN = config("DISCORD_TOKEN")
            self.DEVELOPER_ID = config("DEVELOPER_ID", cast=int, default=None)
            self.FIREBASE_CRED_PATH = config("FIREBASE_CRED_PATH", default=None)
            self.FLASK_PORT = config("FLASK_PORT", default=8000, cast=int)
            self.SHARD_COUNT = config("SHARD_COUNT", default=2, cast=int)
        except UndefinedValueError as e:
            logger.critical(f"Отсутствует переменная: {e}")
            raise RuntimeError("Ошибка конфигурации") from e

    def get(self, key, default=None):
        """Получение значения атрибута по ключу."""
        return getattr(self, key, default)

    def _find_firebase_credentials(self) -> str | None:
        """Рекурсивно ищет файл serviceAccountKey.json в проекте."""
        project_root = Path(__file__).parent.parent
        if not project_root.exists():
            logger.debug("Корень проекта не найден")
            return None

        for file_path in project_root.rglob("serviceAccountKey.json"):
            if file_path.is_file():
                try:
                    with file_path.open('r') as f:
                        json.load(f)
                    logger.debug(f"Найден файл Firebase: {file_path}")
                    return str(file_path)
                except json.JSONDecodeError:
                    logger.warning(f"Некорректный JSON: {file_path}")
                    continue
        logger.info("Файл serviceAccountKey.json не найден в проекте")
        return None

    def validate(self):
        """Проверка корректности конфигурационных значений."""
        if not self.TOKEN:
            logger.critical("DISCORD_TOKEN обязателен")
            raise ValueError("DISCORD_TOKEN обязателен")

        if self.FIREBASE_CRED_PATH:
            cred_path = Path(self.FIREBASE_CRED_PATH)
            if not cred_path.is_file():
                logger.warning(f"Файл Firebase не найден: {self.FIREBASE_CRED_PATH}. Поиск в проекте...")
                self.FIREBASE_CRED_PATH = self._find_firebase_credentials()
            elif not cred_path.suffix == ".json":
                logger.warning(f"Файл Firebase должен быть JSON: {self.FIREBASE_CRED_PATH}. Поиск в проекте...")
                self.FIREBASE_CRED_PATH = self._find_firebase_credentials()
            else:
                try:
                    with cred_path.open('r') as f:
                        json.load(f)
                except json.JSONDecodeError:
                    logger.warning(f"Некорректный JSON файл Firebase: {self.FIREBASE_CRED_PATH}. Поиск в проекте...")
                    self.FIREBASE_CRED_PATH = self._find_firebase_credentials()
        else:
            logger.info("FIREBASE_CRED_PATH не указан. Поиск файла serviceAccountKey.json...")
            self.FIREBASE_CRED_PATH = self._find_firebase_credentials()

        if self.SHARD_COUNT < 1:
            logger.warning(f"SHARD_COUNT должен быть >= 1, установлено 2")
            self.SHARD_COUNT = 2

    def _initialize_firebase(self):
        """Инициализация Firebase."""
        self.firebase_initialized = False
        if self.FIREBASE_CRED_PATH:
            try:
                if not firebase_admin._apps:
                    cred = credentials.Certificate(self.FIREBASE_CRED_PATH)
                    firebase_admin.initialize_app(cred)
                    self.firebase_initialized = True
                    logger.info("Firebase успешно инициализирован")
                else:
                    self.firebase_initialized = True
                    logger.debug("Firebase уже инициализирован другим процессом")
            except Exception as e:
                logger.error(f"Ошибка инициализации Firebase: {e}")
                self.firebase_initialized = False
        else:
            logger.info("Firebase не используется: отсутствует файл учетных данных")
            self.firebase_initialized = False

    @property
    def use_firebase(self) -> bool:
        """Проверка использования Firebase."""
        return self.firebase_initialized