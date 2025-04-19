import firebase_admin
from firebase_admin import credentials, db
from typing import Dict, Optional
import os
from decouple import config
import logging
import backoff
import asyncio
import json
import aiohttp
from contextlib import asynccontextmanager

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FirebaseManager:
    """Менеджер для работы с Firebase Realtime Database."""
    _instance: Optional['FirebaseManager'] = None
    _file_cache: Dict[str, str] = {}  # Кэш путей к файлам
    _initialized: bool = False  # Флаг инициализации
    _init_lock: asyncio.Lock = asyncio.Lock()  # Замок для синхронизации инициализации
    _db_url: str = "https://ai-assist-fe86c-default-rtdb.europe-west1.firebasedatabase.app/"

    @staticmethod
    def find_file(filename: str) -> Optional[str]:
        """
        Рекурсивный поиск файла по имени в проекте и стандартных директориях.
        
        Args:
            filename: Имя файла для поиска.
        
        Returns:
            Полный путь к файлу или None, если файл не найден.
        """
        cache_key = filename
        if cache_key in FirebaseManager._file_cache:
            logger.debug(f"Путь к файлу {filename} взят из кэша: {FirebaseManager._file_cache[cache_key]}")
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
                        logger.info(f"Файл {filename} найден по пути: {file_path}")
                        return file_path

            env_path = config('FIREBASE_KEY_PATH', default=None)
            if env_path and os.path.exists(env_path):
                FirebaseManager._file_cache[cache_key] = env_path
                logger.info(f"Файл {filename} найден по пути из FIREBASE_KEY_PATH: {env_path}")
                return env_path

            logger.warning(f"Файл {filename} не найден в проекте или FIREBASE_KEY_PATH")
            return None
        except Exception as e:
            logger.error(f"Ошибка при поиске файла {filename}: {e}")
            return None

    @classmethod
    async def initialize(cls) -> 'FirebaseManager':
        """Инициализация подключения к Firebase Realtime Database с использованием asyncio.Lock."""
        async with cls._init_lock:
            if cls._instance and cls._initialized:
                logger.debug("Firebase уже инициализирован")
                return cls._instance

            try:
                try:
                    firebase_admin.get_app(name='[DEFAULT]')
                    logger.debug("Приложение Firebase уже инициализировано")
                except ValueError:
                    firebase_key_filename = 'serviceAccountKey.json'
                    firebase_key_path = cls.find_file(firebase_key_filename)
                    
                    if not firebase_key_path:
                        raise FileNotFoundError(
                            f"Файл ключа Firebase '{firebase_key_filename}' не найден в проекте и не указан в FIREBASE_KEY_PATH"
                        )
                    
                    cred = credentials.Certificate(firebase_key_path)
                    firebase_admin.initialize_app(cred, {
                        'databaseURL': cls._db_url,
                        'httpTimeout': 30  # Увеличенный тайм-аут для HTTP-запросов
                    })
                    logger.info("Firebase Realtime Database успешно инициализирован")

                cls._instance = cls()
                cls._instance._db = db.reference()
                cls._initialized = True
                logger.debug(f"Клиент Realtime Database инициализирован: _db={cls._instance._db}")
                return cls._instance
            except Exception as e:
                logger.error(f"Ошибка инициализации Firebase: {e}")
                cls._initialized = False
                raise Exception(f"Ошибка инициализации Firebase: {e}")

    def _ensure_db_initialized(self) -> None:
        """Проверка, что клиент Realtime Database инициализирован."""
        if not self._initialized or not hasattr(self, '_db') or self._db is None:
            logger.error("Клиент Realtime Database не инициализирован")
            raise AttributeError("FirebaseManager._db не инициализирован. Вызовите initialize() перед использованием.")

    async def _run_sync_in_executor(self, func):
        """Запуск синхронной функции в пуле потоков с тайм-аутом."""
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, func),
                timeout=30  # Тайм-аут 30 секунд для синхронных операций
            )
        except asyncio.TimeoutError:
            logger.error(f"Тайм-аут выполнения синхронной операции: {func}")
            raise Exception(f"Тайм-аут выполнения операции: {func}")

    @asynccontextmanager
    async def _http_session(self):
        """Контекстный менеджер для создания HTTP-сессии с увеличенным тайм-аутом."""
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            yield session

    @backoff.on_exception(
        backoff.expo,
        Exception,
        max_tries=3,
        max_time=30,
        factor=2,
        jitter=backoff.full_jitter
    )
    async def load_models(self) -> Dict:
        """Загрузка моделей из Realtime Database."""
        self._ensure_db_initialized()
        try:
            logger.debug("Начало загрузки моделей из Realtime Database")
            def sync_get():
                data = self._db.child("models/available_models").get()
                return data if data else {}
            data = await self._run_sync_in_executor(sync_get)
            logger.debug("Модели успешно загружены из Realtime Database")
            return data
        except Exception as e:
            logger.error(f"Ошибка загрузки моделей из Realtime Database: {e}")
            raise Exception(f"Ошибка загрузки моделей: {e}")

    @backoff.on_exception(
        backoff.expo,
        Exception,
        max_tries=3,
        max_time=30,
        factor=2,
        jitter=backoff.full_jitter
    )
    async def save_models(self, models: Dict) -> None:
        """Сохранение моделей в Realtime Database."""
        self._ensure_db_initialized()
        try:
            logger.debug("Начало сохранения моделей в Realtime Database")
            def sync_set():
                self._db.child("models/available_models").set(models)
            await self._run_sync_in_executor(sync_set)
            logger.debug("Модели успешно сохранены в Realtime Database")
        except Exception as e:
            logger.error(f"Ошибка сохранения моделей в Realtime Database: {e}")
            raise Exception(f"Ошибка сохранения моделей: {e}")

    @backoff.on_exception(
        backoff.expo,
        Exception,
        max_tries=3,
        max_time=30,
        factor=2,
        jitter=backoff.full_jitter
    )
    async def load_guild_config(self, guild_id: str) -> Dict:
        """Загрузка конфигурации гильдии из Realtime Database."""
        self._ensure_db_initialized()
        try:
            logger.debug(f"Начало загрузки конфигурации гильдии {guild_id}")
            def sync_get():
                data = self._db.child(f"guilds/{guild_id}").get()
                return data if data else {}
            data = await self._run_sync_in_executor(sync_get)
            logger.debug(f"Конфигурация гильдии {guild_id} загружена из Realtime Database")
            return data
        except Exception as e:
            logger.error(f"Ошибка загрузки конфигурации гильдии {guild_id}: {e}")
            raise Exception(f"Ошибка загрузки конфигурации гильдии {guild_id}: {e}")

    @backoff.on_exception(
        backoff.expo,
        Exception,
        max_tries=3,
        max_time=30,
        factor=2,
        jitter=backoff.full_jitter
    )
    async def update_guild_fields(self, guild_id: str, updates: Dict) -> None:
        """Обновление полей конфигурации гильдии в Realtime Database."""
        self._ensure_db_initialized()
        try:
            logger.debug(f"Начало обновления конфигурации гильдии {guild_id}")
            def sync_update():
                self._db.child(f"guilds/{guild_id}").update(updates)
            await self._run_sync_in_executor(sync_update)
            logger.debug(f"Конфигурация гильдии {guild_id} обновлена в Realtime Database")
        except Exception as e:
            logger.error(f"Ошибка обновления конфигурации гильдии {guild_id}: {e}")
            raise Exception(f"Ошибка обновления конфигурации гильдии {guild_id}: {e}")

    @backoff.on_exception(
        backoff.expo,
        Exception,
        max_tries=3,
        max_time=30,
        factor=2,
        jitter=backoff.full_jitter
    )
    async def save_cache(self, cache_key: str, cache_data: Dict) -> None:
        """Сохранение данных кэша в Realtime Database."""
        self._ensure_db_initialized()
        try:
            logger.debug(f"Начало сохранения кэша для ключа {cache_key}")
            def sync_set():
                self._db.child(f"response_cache/{cache_key}").set(cache_data)
            await self._run_sync_in_executor(sync_set)
            logger.debug(f"Кэш сохранён в Realtime Database для ключа {cache_key}")
        except Exception as e:
            logger.error(f"Ошибка сохранения кэша в Realtime Database для ключа {cache_key}: {e}")
            raise Exception(f"Ошибка сохранения кэша: {e}")

    @backoff.on_exception(
        backoff.expo,
        Exception,
        max_tries=3,
        max_time=30,
        factor=2,
        jitter=backoff.full_jitter
    )
    async def load_cache(self, cache_key: str) -> Optional[Dict]:
        """Загрузка данных кэша из Realtime Database."""
        self._ensure_db_initialized()
        try:
            logger.debug(f"Начало загрузки кэша для ключа {cache_key}")
            def sync_get():
                data = self._db.child(f"response_cache/{cache_key}").get()
                return data if data else None
            data = await self._run_sync_in_executor(sync_get)
            logger.debug(f"Кэш загружен из Realtime Database для ключа {cache_key}")
            return data
        except Exception as e:
            logger.error(f"Ошибка загрузки кэша из Realtime Database для ключа {cache_key}: {e}")
            raise Exception(f"Ошибка загрузки кэша: {e}")

    @backoff.on_exception(
        backoff.expo,
        Exception,
        max_tries=3,
        max_time=30,
        factor=2,
        jitter=backoff.full_jitter
    )
    async def save_conversation(self, user_id: str, conversation_id: str, conversation_data: Dict) -> None:
        """Сохранение контекста разговора в Realtime Database."""
        self._ensure_db_initialized()
        try:
            logger.debug(f"Начало сохранения разговора {conversation_id} для пользователя {user_id}")
            def sync_set():
                self._db.child(f"conversations/{user_id}/sessions/{conversation_id}").set(conversation_data)
            await self._run_sync_in_executor(sync_set)
            logger.debug(f"Разговор {conversation_id} сохранён в Realtime Database для пользователя {user_id}")
        except Exception as e:
            logger.error(f"Ошибка сохранения разговора {conversation_id} в Realtime Database: {e}")
            raise Exception(f"Ошибка сохранения разговора: {e}")

    @backoff.on_exception(
        backoff.expo,
        Exception,
        max_tries=3,
        max_time=30,
        factor=2,
        jitter=backoff.full_jitter
    )
    async def load_conversation(self, user_id: str, conversation_id: str) -> Optional[Dict]:
        """Загрузка контекста разговора из Realtime Database."""
        self._ensure_db_initialized()
        try:
            logger.debug(f"Начало загрузки разговора {conversation_id} для пользователя {user_id}")
            def sync_get():
                data = self._db.child(f"conversations/{user_id}/sessions/{conversation_id}").get()
                return data if data else None
            data = await self._run_sync_in_executor(sync_get)
            logger.debug(f"Разговор {conversation_id} загружен из Realtime Database для пользователя {user_id}")
            return data
        except Exception as e:
            logger.error(f"Ошибка загрузки разговора {conversation_id} из Realtime Database: {e}")
            raise Exception(f"Ошибка загрузки разговора: {e}")

    @backoff.on_exception(
        backoff.expo,
        Exception,
        max_tries=5,  # Увеличено до 5 попыток
        max_time=60,  # Увеличено до 60 секунд
        factor=2,
        jitter=backoff.full_jitter
    )
    async def cleanup_expired_conversations(self, current_time: float) -> None:
        """Очистка устаревших разговоров из Realtime Database с пагинацией и пакетным удалением."""
        self._ensure_db_initialized()
        try:
            logger.debug("Начало очистки устаревших разговоров в Realtime Database")
            
            async def process_user(user_id: str, sessions: Dict) -> Dict:
                """Обработка сессий одного пользователя."""
                updates = {}
                for session_id, session_data in sessions.items():
                    last_message_time = session_data.get("last_message_time", 0)
                    ttl_seconds = session_data.get("ttl_seconds", 86400)
                    if current_time - last_message_time > ttl_seconds:
                        updates[f"conversations/{user_id}/sessions/{session_id}"] = None
                        logger.debug(f"Запланировано удаление устаревшего разговора {session_id} для пользователя {user_id}")
                return updates

            # Загрузка пользователей по одному для минимизации нагрузки
            def sync_get_users():
                return self._db.child("conversations").get() or {}

            users = await self._run_sync_in_executor(sync_get_users)
            total_users = len(users)
            logger.debug(f"Найдено {total_users} пользователей для проверки")

            # Пакетное удаление
            batch_updates = {}
            processed_users = 0
            processed_sessions = 0

            for user_id, user_data in users.items():
                sessions = user_data.get("sessions", {})
                updates = await process_user(user_id, sessions)
                batch_updates.update(updates)
                processed_users += 1
                processed_sessions += len(updates)
                
                # Ограничение размера пакета (например, 100 обновлений)
                if len(batch_updates) >= 100:
                    def sync_batch_update():
                        self._db.update(batch_updates)
                    await self._run_sync_in_executor(sync_batch_update)
                    logger.debug(f"Выполнено пакетное удаление {len(batch_updates)} сессий")
                    batch_updates = {}

            # Обработка оставшихся обновлений
            if batch_updates:
                def sync_final_update():
                    self._db.update(batch_updates)
                await self._run_sync_in_executor(sync_final_update)
                logger.debug(f"Выполнено финальное пакетное удаление {len(batch_updates)} сессий")

            logger.info(f"Очистка завершена: обработано {processed_users}/{total_users} пользователей, удалено {processed_sessions} сессий")
        except Exception as e:
            logger.error(f"Ошибка очистки устаревших разговоров в Realtime Database: {e}")
            raise Exception(f"Ошибка очистки разговоров: {e}")

    @backoff.on_exception(
        backoff.expo,
        Exception,
        max_tries=3,
        max_time=30,
        factor=2,
        jitter=backoff.full_jitter
    )
    async def save_giveaways(self, active_giveaways: Dict, completed_giveaways: Dict) -> None:
        """Сохранение данных розыгрышей в Realtime Database."""
        self._ensure_db_initialized()
        try:
            logger.debug("Начало сохранения розыгрышей в Realtime Database")
            def sync_set():
                self._db.child("giveaways/active").set(active_giveaways)
                self._db.child("giveaways/completed").set(completed_giveaways)
            await self._run_sync_in_executor(sync_set)
            logger.debug("Розыгрыши успешно сохранены в Realtime Database")
        except Exception as e:
            logger.error(f"Ошибка сохранения розыгрышей в Realtime Database: {e}")
            raise Exception(f"Ошибка сохранения розыгрышей: {e}")

    @backoff.on_exception(
        backoff.expo,
        Exception,
        max_tries=3,
        max_time=30,
        factor=2,
        jitter=backoff.full_jitter
    )
    async def load_giveaways(self) -> Dict:
        """Загрузка данных розыгрышей из Realtime Database."""
        self._ensure_db_initialized()
        try:
            logger.debug("Начало загрузки розыгрышей из Realtime Database")
            def sync_get():
                data = self._db.child("giveaways").get()
                return data if data else {"active": {}, "completed": {}}
            data = await self._run_sync_in_executor(sync_get)
            logger.debug("Розыгрыши успешно загружены из Realtime Database")
            return data
        except Exception as e:
            logger.error(f"Ошибка загрузки розыгрышей из Realtime Database: {e}")
            raise Exception(f"Ошибка загрузки розыгрышей: {e}")