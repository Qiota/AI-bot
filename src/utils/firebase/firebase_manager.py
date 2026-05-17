import firebase_admin
from firebase_admin import credentials, db
from typing import Dict, Optional, Any, Callable
import os
from decouple import config
import logging
import backoff
import asyncio
import json
import aiohttp
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FirebaseManager:
    """Менеджер для работы с Firebase Realtime Database."""
    _instance: Optional['FirebaseManager'] = None
    _file_cache: Dict[str, str] = {}
    _credentials_cache: Optional[Dict] = None
    _initialized: bool = False
    _init_lock: asyncio.Lock = asyncio.Lock()
    _db_url: str = config('FIREBASE_DATABASE_URL')

    @staticmethod
    def find_file(filename: str) -> Optional[str]:
        """Рекурсивный поиск файла по имени в проекте и стандартных директориях."""
        cache_key = filename
        if cache_key in FirebaseManager._file_cache:
            return FirebaseManager._file_cache[cache_key]

        logger.debug(f"Поиск файла {filename}")
        try:
            start_dir = os.path.abspath(os.path.dirname(__file__))
            project_root = start_dir
            while project_root != os.path.dirname(project_root) and not os.path.exists(os.path.join(project_root, '.git')):
                project_root = os.path.dirname(project_root)

            search_dirs = [
                project_root,
                os.path.join(project_root, 'firebase'),
                os.path.join(project_root, 'config'),
                os.path.join(project_root, 'credentials'),
                os.path.join(project_root, 'src', 'firebase'),
                start_dir
            ]

            for search_dir in search_dirs:
                for root, _, files in os.walk(search_dir):
                    if filename in files:
                        file_path = os.path.join(root, filename)
                        FirebaseManager._file_cache[cache_key] = file_path
                        logger.info(f"Файл {filename} найден: {file_path}")
                        return file_path

            env_path = config('FIREBASE_KEY_PATH', default=None)
            if env_path and os.path.exists(env_path):
                FirebaseManager._file_cache[cache_key] = env_path
                return env_path

            logger.warning(f"Файл {filename} не найден")
            return None
        except Exception as e:
            logger.error(f"Ошибка при поиске файла {filename}: {e}")
            return None

    @staticmethod
    async def fetch_credentials_from_url(url: str, headers: Optional[Dict] = None) -> Optional[Dict]:
        """Получение учетных данных Firebase через HTTP-запрос."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info("Учетные данные загружены через HTTP")
                        return data
                    logger.error(f"Ошибка загрузки через HTTP: {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Ошибка HTTP-запроса учетных данных: {e}")
            return None

    @classmethod
    async def get_credentials(cls) -> Optional[credentials.Certificate]:
        """Получение учетных данных Firebase из файла, переменной окружения или URL."""
        if cls._credentials_cache:
            return credentials.Certificate(cls._credentials_cache)

        firebase_key_path = cls.find_file('serviceAccountKey.json')
        if firebase_key_path:
            return credentials.Certificate(firebase_key_path)

        firebase_credentials_json = config('FIREBASE_CREDENTIALS', default=None)
        if firebase_credentials_json:
            try:
                if not firebase_credentials_json.strip():
                    return None
                credentials_dict = json.loads(firebase_credentials_json)
                required_fields = ["type", "project_id", "private_key", "client_email"]
                if not all(field in credentials_dict for field in required_fields):
                    logger.error(f"FIREBASE_CREDENTIALS缺少字段: {required_fields}")
                    return None
                cls._credentials_cache = credentials_dict
                return credentials.Certificate(credentials_dict)
            except json.JSONDecodeError as e:
                logger.error(f"Ошибка парсинга FIREBASE_CREDENTIALS: {e}")
                return None

        credentials_url = config('FIREBASE_CREDENTIALS_URL', default=None)
        if credentials_url:
            headers = config('FIREBASE_CREDENTIALS_HEADERS', default=None)
            try:
                headers = json.loads(headers) if headers else None
            except json.JSONDecodeError as e:
                logger.error(f"Ошибка парсинга FIREBASE_CREDENTIALS_HEADERS: {e}")
                return None
            credentials_dict = await cls.fetch_credentials_from_url(credentials_url, headers)
            if credentials_dict:
                required_fields = ["type", "project_id", "private_key", "client_email"]
                if not all(field in credentials_dict for field in required_fields):
                    return None
                cls._credentials_cache = credentials_dict
                return credentials.Certificate(credentials_dict)

        logger.error("Учетные данные Firebase не найдены")
        return None

    @classmethod
    async def initialize(cls) -> 'FirebaseManager':
        """Инициализация Firebase or SQLite fallback."""
        async with cls._init_lock:
            if cls._instance and cls._initialized:
                return cls._instance

            try:
                try:
                    firebase_admin.get_app(name='[DEFAULT]')
                except ValueError:
                    cred = await cls.get_credentials()
                    if not cred:
                        raise FileNotFoundError("Учетные данные Firebase не найдены")

                    firebase_admin.initialize_app(cred, {
                        'databaseURL': cls._db_url,
                        'httpTimeout': 30
                    })
                    logger.info("Firebase Realtime Database инициализирован")

                cls._instance = cls()
                cls._instance._db = db.reference()
                cls._initialized = True
                return cls._instance
            except Exception as e:
                logger.warning(f"Firebase недоступен ({e}). Используем SQLite fallback.")
                from src.utils.sqlite_manager import SQLiteManager
                sqlite_mgr = await SQLiteManager().initialize()
                cls._instance = type('FallbackManager', (), {
                    'load_guild_config': sqlite_mgr.load_guild_config,
                    'update_guild_fields': sqlite_mgr.update_guild_fields
                })()
                cls._initialized = True
                return cls._instance

    def _ensure_db_initialized(self) -> None:
        if not self._initialized or not hasattr(self, '_db') or self._db is None:
            raise AttributeError("FirebaseManager._db не инициализирован. Вызовите initialize().")

    async def _run_sync_in_executor(self, func):
        """Запуск синхронной функции в пуле потоков с тайм-аутом."""
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, func),
                timeout=30
            )
        except asyncio.TimeoutError:
            logger.error(f"Тайм-аут операции: {func}")
            raise Exception(f"Тайм-аут: {func}")

    async def _db_operation(self, sync_func, debug_msg: str, error_msg: str) -> Any:
        """Generic helper for Firebase DB operations with retry, logging, and executor."""
        self._ensure_db_initialized()
        try:
            logger.debug(debug_msg)
            result = await self._run_sync_in_executor(sync_func)
            logger.debug(f"{debug_msg} — успешно")
            return result
        except Exception as e:
            logger.error(f"{error_msg}: {e}")
            raise Exception(f"{error_msg}: {e}")

    @backoff.on_exception(backoff.expo, Exception, max_tries=3, max_time=30, factor=2, jitter=backoff.full_jitter)
    async def load_models(self) -> Dict:
        return await self._db_operation(
            lambda: self._db.child("models/available_models").get() or {},
            "Загрузка моделей",
            "Ошибка загрузки моделей"
        )

    @backoff.on_exception(backoff.expo, Exception, max_tries=3, max_time=30, factor=2, jitter=backoff.full_jitter)
    async def save_models(self, models: Dict) -> None:
        await self._db_operation(
            lambda: self._db.child("models/available_models").set(models),
            "Сохранение моделей",
            "Ошибка сохранения моделей"
        )

    @backoff.on_exception(backoff.expo, Exception, max_tries=3, max_time=30, factor=2, jitter=backoff.full_jitter)
    async def load_guild_config(self, guild_id: str) -> Dict:
        return await self._db_operation(
            lambda: self._db.child(f"guilds/{guild_id}").get() or {},
            f"Загрузка конфига гильдии {guild_id}",
            f"Ошибка загрузки конфига гильдии {guild_id}"
        )

    @backoff.on_exception(backoff.expo, Exception, max_tries=3, max_time=30, factor=2, jitter=backoff.full_jitter)
    async def update_guild_fields(self, guild_id: str, updates: Dict) -> None:
        await self._db_operation(
            lambda: self._db.child(f"guilds/{guild_id}").update(updates),
            f"Обновление конфига гильдии {guild_id}",
            f"Ошибка обновления конфига гильдии {guild_id}"
        )

    @backoff.on_exception(backoff.expo, Exception, max_tries=3, max_time=30, factor=2, jitter=backoff.full_jitter)
    async def save_user_settings(self, user_id: str, settings: Dict) -> None:
        await self._db_operation(
            lambda: self._db.child(f"users/{user_id}/settings").set(settings),
            f"Сохранение настроек пользователя {user_id}",
            f"Ошибка сохранения настроек пользователя {user_id}"
        )

    @backoff.on_exception(backoff.expo, Exception, max_tries=3, max_time=30, factor=2, jitter=backoff.full_jitter)
    async def load_user_settings(self, user_id: str) -> Optional[Dict]:
        return await self._db_operation(
            lambda: self._db.child(f"users/{user_id}/settings").get() or None,
            f"Загрузка настроек пользователя {user_id}",
            f"Ошибка загрузки настроек пользователя {user_id}"
        )

    @backoff.on_exception(backoff.expo, Exception, max_tries=3, max_time=30, factor=2, jitter=backoff.full_jitter)
    async def save_cache(self, user_id: str, channel_type: str, channel_id: str, cache_key: str, cache_data: Dict) -> None:
        path = f"memories/{user_id}/{channel_type}/{channel_id}/{cache_key}"
        await self._db_operation(
            lambda: self._db.child(path).set(cache_data),
            f"Сохранение кэша {path}",
            f"Ошибка сохранения кэша {path}"
        )

    @backoff.on_exception(backoff.expo, Exception, max_tries=3, max_time=30, factor=2, jitter=backoff.full_jitter)
    async def load_cache(self, user_id: str, channel_type: str, channel_id: str, cache_key: str) -> Optional[Dict]:
        path = f"memories/{user_id}/{channel_type}/{channel_id}/{cache_key}"
        return await self._db_operation(
            lambda: self._db.child(path).get() or None,
            f"Загрузка кэша {path}",
            f"Ошибка загрузки кэша {path}"
        )

    @backoff.on_exception(backoff.expo, Exception, max_tries=3, max_time=30, factor=2, jitter=backoff.full_jitter)
    async def save_conversation(self, user_id: str, conversation_id: str, conversation_data: Dict) -> None:
        path = f"conversations/{user_id}/sessions/{conversation_id}"
        await self._db_operation(
            lambda: self._db.child(path).set(conversation_data),
            f"Сохранение разговора {conversation_id}",
            f"Ошибка сохранения разговора {conversation_id}"
        )

    @backoff.on_exception(backoff.expo, Exception, max_tries=3, max_time=30, factor=2, jitter=backoff.full_jitter)
    async def load_conversation(self, user_id: str, conversation_id: str) -> Optional[Dict]:
        path = f"conversations/{user_id}/sessions/{conversation_id}"
        return await self._db_operation(
            lambda: self._db.child(path).get() or None,
            f"Загрузка разговора {conversation_id}",
            f"Ошибка загрузки разговора {conversation_id}"
        )

    @backoff.on_exception(backoff.expo, Exception, max_tries=5, max_time=60, factor=2, jitter=backoff.full_jitter)
    async def cleanup_expired_conversations(self, current_time: float) -> None:
        self._ensure_db_initialized()
        try:
            users = await self._run_sync_in_executor(lambda: self._db.child("conversations").get() or {})
            logger.debug(f"Найдено {len(users)} пользователей для проверки")

            batch_updates = {}
            processed_sessions = 0

            for user_id, user_data in users.items():
                for session_id, session_data in user_data.get("sessions", {}).items():
                    last_time = session_data.get("last_message_time", 0)
                    ttl = session_data.get("ttl_seconds", 86400)
                    if current_time - last_time > ttl:
                        batch_updates[f"conversations/{user_id}/sessions/{session_id}"] = None
                        processed_sessions += 1

                if len(batch_updates) >= 100:
                    await self._run_sync_in_executor(lambda: self._db.update(batch_updates))
                    batch_updates = {}

            if batch_updates:
                await self._run_sync_in_executor(lambda: self._db.update(batch_updates))

            logger.info(f"Очистка завершена: удалено {processed_sessions} сессий")
        except Exception as e:
            logger.error(f"Ошибка очистки разговоров: {e}")
            raise Exception(f"Ошибка очистки разговоров: {e}")

    @backoff.on_exception(backoff.expo, Exception, max_tries=5, max_time=60, factor=2, jitter=backoff.full_jitter)
    async def cleanup_expired_cache(self, current_time: float, ttl_seconds: float) -> None:
        self._ensure_db_initialized()
        try:
            cache_data = await self._run_sync_in_executor(lambda: self._db.child("memories").get() or {})
            batch_updates = {}

            for user_id, user_data in cache_data.items():
                for channel_type in ['DM', 'guild']:
                    for channel_id, entries in user_data.get(channel_type, {}).items():
                        for cache_key, entry_data in entries.items():
                            if current_time - entry_data.get("timestamp", 0) > ttl_seconds:
                                batch_updates[f"memories/{user_id}/{channel_type}/{channel_id}/{cache_key}"] = None

            if batch_updates:
                await self._run_sync_in_executor(lambda: self._db.update(batch_updates))

            logger.info(f"Очистка кэша завершена: удалено {len(batch_updates)} записей")
        except Exception as e:
            logger.error(f"Ошибка очистки кэша: {e}")
            raise Exception(f"Ошибка очистки кэша: {e}")

    @backoff.on_exception(backoff.expo, Exception, max_tries=5, max_time=60, factor=2, jitter=backoff.full_jitter)
    async def cleanup_expired_giveaways(self, current_time: float, retention_seconds: float = 7 * 24 * 60 * 60) -> None:
        self._ensure_db_initialized()
        try:
            data = await self._run_sync_in_executor(lambda: self._db.child("giveaways").get() or {"active": {}, "completed": {}})
            batch_updates = {}

            for custom_id, giveaway_data in data.get("completed", {}).items():
                if current_time - giveaway_data.get("completed_at", 0) > retention_seconds:
                    batch_updates[f"giveaways/completed/{custom_id}"] = None

            for custom_id, giveaway_data in data.get("active", {}).items():
                if current_time - giveaway_data.get("end_time", 0) > retention_seconds:
                    batch_updates[f"giveaways/active/{custom_id}"] = None

            if batch_updates:
                await self._run_sync_in_executor(lambda: self._db.update(batch_updates))

            logger.info(f"Очистка розыгрышей завершена: удалено {len(batch_updates)}")
        except Exception as e:
            logger.error(f"Ошибка очистки розыгрышей: {e}")
            raise Exception(f"Ошибка очистки розыгрышей: {e}")

    @backoff.on_exception(backoff.expo, Exception, max_tries=3, max_time=30, factor=2, jitter=backoff.full_jitter)
    async def save_giveaways(self, active_giveaways: Dict, completed_giveaways: Dict) -> None:
        await self._db_operation(
            lambda: (self._db.child("giveaways/active").set(active_giveaways),
                     self._db.child("giveaways/completed").set(completed_giveaways)),
            "Сохранение розыгрышей",
            "Ошибка сохранения розыгрышей"
        )

    @backoff.on_exception(backoff.expo, Exception, max_tries=3, max_time=30, factor=2, jitter=backoff.full_jitter)
    async def load_giveaways(self) -> Dict:
        return await self._db_operation(
            lambda: self._db.child("giveaways").get() or {"active": {}, "completed": {}},
            "Загрузка розыгрышей",
            "Ошибка загрузки розыгрышей"
        )
