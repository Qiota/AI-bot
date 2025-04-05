import asyncio
from typing import List, Dict
import time
import json
import os
from pathlib import Path
import firebase_admin
from firebase_admin import firestore
from .logging_config import logger
from .config import BotConfig

class Database:
    """Управление базой данных: Firebase Firestore или локальное хранилище."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)
            cls._instance.__init_inner__()
        return cls._instance

    def __init_inner__(self):
        self.local_limit: int = 10
        self.memory_dir = Path(__file__).parent.parent / "memories"
        self.local_files: Dict[str, str] = {}
        self.use_firebase = False
        config = BotConfig()

        try:
            if config.use_firebase:
                if firebase_admin._apps:
                    self.db = firestore.client()
                    self.collection = self.db.collection("memories")
                    self.use_firebase = True
                    logger.debug("Firestore подключён")
                else:
                    logger.error("Firebase не инициализирован, хотя config.use_firebase=True")
                    self._init_local_storage()
            else:
                self._init_local_storage()
        except Exception as e:
            logger.error(f"Ошибка подключения к Firestore: {e}")
            self.use_firebase = False
            self._init_local_storage()

    def _init_local_storage(self):
        """Инициализация локального хранилища."""
        if not self.memory_dir.exists():
            self.memory_dir.mkdir()
        logger.debug("Локальное хранилище готово")

    def _get_user_file(self, user_id: str) -> Path:
        """Получение пути к файлу пользователя."""
        if user_id not in self.local_files:
            file_path = self.memory_dir / f"{user_id}_memory.json"
            self.local_files[user_id] = str(file_path)
            if not file_path.exists():
                with file_path.open('w') as f:
                    json.dump({"user_info": {"id": user_id, "username": ""}, "threads": []}, f)
                logger.debug(f"Создан файл для {user_id}")
        return Path(self.local_files[user_id])

    def _load_user_data(self, user_id: str) -> Dict:
        """Загрузка данных пользователя."""
        file_path = self._get_user_file(user_id)
        try:
            with file_path.open('r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            logger.warning(f"Ошибка JSON в {file_path}")
            return {"user_info": {"id": user_id, "username": ""}, "threads": []}

    def _save_user_data(self, user_id: str, data: Dict) -> None:
        """Сохранение данных пользователя."""
        file_path = self._get_user_file(user_id)
        with file_path.open('w') as f:
            json.dump(data, f)

    async def add_message(self, user_id: str, message_id: str, role: str, content: str, username: str = "") -> None:
        """Добавление сообщения в базу данных."""
        message_data = {
            "user_id": user_id,
            "message_id": message_id,
            "role": role,
            "content": content,
            "timestamp": time.time()
        }
        if self.use_firebase:
            try:
                batch = self.db.batch()
                user_doc = self.collection.document(user_id)
                user_thread = user_doc.collection("threads").document(message_id)
                batch.set(user_doc, {"username": username}, merge=True)
                batch.set(user_thread, message_data)
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, batch.commit)
            except Exception as e:
                logger.error(f"Ошибка записи в Firestore: {e}")
                raise
        else:
            user_data = self._load_user_data(user_id)
            user_data["user_info"]["username"] = username
            user_data["threads"].append(message_data)
            if len(user_data["threads"]) > self.local_limit:
                user_data["threads"] = sorted(user_data["threads"], key=lambda x: x["timestamp"])[-self.local_limit:]
            self._save_user_data(user_id, user_data)

    async def get_context(self, user_id: str, limit: int = 10) -> List[Dict[str, str]]:
        """Получение контекста сообщений."""
        if self.use_firebase:
            try:
                user_thread = self.collection.document(user_id).collection("threads")
                query = user_thread.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit)
                loop = asyncio.get_running_loop()
                docs = await loop.run_in_executor(None, lambda: [doc.to_dict() for doc in query.stream()])
                return [{"role": doc["role"], "content": doc["content"]} for doc in reversed(docs)]
            except Exception as e:
                logger.error(f"Ошибка чтения из Firestore: {e}")
                raise
        else:
            user_data = self._load_user_data(user_id)
            threads = sorted(user_data["threads"], key=lambda x: x["timestamp"], reverse=True)[:limit]
            return [{"role": doc["role"], "content": doc["content"]} for doc in reversed(threads)]

    async def get_user_info(self, user_id: str) -> Dict[str, str]:
        """Получение информации о пользователе."""
        if self.use_firebase:
            try:
                user_doc = self.collection.document(user_id)
                loop = asyncio.get_running_loop()
                doc = await loop.run_in_executor(None, lambda: user_doc.get())
                return {"id": user_id, "username": doc.to_dict().get("username", "")} if doc.exists else {"id": user_id, "username": ""}
            except Exception as e:
                logger.error(f"Ошибка чтения информации пользователя из Firestore: {e}")
                raise
        else:
            return self._load_user_data(user_id)["user_info"]

    async def clear_user_data(self, user_id: str) -> None:
        """Очистка данных пользователя."""
        if self.use_firebase:
            try:
                threads_ref = self.collection.document(user_id).collection("threads")
                loop = asyncio.get_running_loop()
                threads_docs = await loop.run_in_executor(None, lambda: [doc for doc in threads_ref.stream()])
                for i in range(0, len(threads_docs), 500):
                    batch = self.db.batch()
                    for doc in threads_docs[i:i + 500]:
                        batch.delete(doc.reference)
                    await loop.run_in_executor(None, batch.commit)
                await loop.run_in_executor(None, lambda: self.collection.document(user_id).delete())
                logger.info(f"Данные {user_id} удалены из Firebase")
            except Exception as e:
                logger.error(f"Ошибка удаления данных из Firestore: {e}")
                raise
        else:
            if user_id in self.local_files:
                file_path = Path(self.local_files[user_id])
                if file_path.exists():
                    file_path.unlink()
                    logger.info(f"Удалён файл {user_id}")
                del self.local_files[user_id]

    def __del__(self):
        """Очистка при завершении работы."""
        if getattr(self, 'use_firebase', False):
            return
        for file_path in self.local_files.values():
            file = Path(file_path)
            if file.exists():
                file.unlink()
                logger.info(f"Удалён файл: {file}")
        if self.memory_dir.exists() and not os.listdir(self.memory_dir):
            self.memory_dir.rmdir()
            logger.info(f"Удалена папка: {self.memory_dir}")