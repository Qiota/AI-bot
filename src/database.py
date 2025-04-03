import asyncio
from functools import lru_cache
from typing import List, Dict
import time
import firebase_admin
from firebase_admin import credentials, firestore
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
        self.local_memory: Dict[str, Dict] = {}
        self.local_limit: int = 5

        try:
            config = BotConfig()
            if config.FIREBASE_CRED_PATH:
                cred = credentials.Certificate(config.FIREBASE_CRED_PATH)
                firebase_admin.initialize_app(cred)
                self.db = firestore.client()
                self.collection = self.db.collection("bot_memory")
                self.use_firebase = True
                logger.info("Firebase успешно инициализирован.")
            else:
                self.use_firebase = False
        except Exception as e:
            self.use_firebase = False
            logger.info("Локальное хранилище инициализировано с лимитом 5 сообщений.")

    async def add_message(self, user_id: str, message_id: str, role: str, content: str) -> None:
        message_data = {"user_id": user_id, "message_id": message_id, "role": role, "content": content, "timestamp": time.time()}
        if self.use_firebase:
            batch = self.db.batch()
            user_thread = self.collection.document(user_id).collection("threads").document(message_id)
            batch.set(user_thread, message_data)
            await asyncio.get_event_loop().run_in_executor(None, batch.commit)
            self.get_context.cache_clear()
        else:
            if user_id not in self.local_memory:
                self.local_memory[user_id] = {"threads": []}
            self.local_memory[user_id]["threads"].append(message_data)
            if len(self.local_memory[user_id]["threads"]) > self.local_limit:
                self.local_memory[user_id]["threads"].pop(0)
            self.get_context.cache_clear()

    @lru_cache(maxsize=200)
    async def get_context(self, user_id: str, limit: int = 5) -> List[Dict[str, str]]:
        if self.use_firebase:
            user_thread = self.collection.document(user_id).collection("threads")
            query = user_thread.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit)
            docs = await asyncio.get_event_loop().run_in_executor(None, lambda: [doc.to_dict() for doc in query.stream()])
            return [{"role": doc["role"], "content": doc["content"]} for doc in reversed(docs)]
        else:
            if user_id not in self.local_memory:
                return []
            threads = sorted(self.local_memory[user_id]["threads"], key=lambda x: x["timestamp"], reverse=True)[:limit]
            return [{"role": doc["role"], "content": doc["content"]} for doc in reversed(threads)]

    async def clear_user_data(self, user_id: str) -> None:
        if self.use_firebase:
            try:
                threads_ref = self.collection.document(user_id).collection("threads")
                threads_docs = await asyncio.get_event_loop().run_in_executor(None, lambda: [doc for doc in threads_ref.stream()])
                for i in range(0, len(threads_docs), 500):
                    batch = self.db.batch()
                    for doc in threads_docs[i:i + 500]:
                        batch.delete(doc.reference)
                    await asyncio.get_event_loop().run_in_executor(None, batch.commit)
                user_doc_ref = self.collection.document(user_id)
                await asyncio.get_event_loop().run_in_executor(None, lambda: user_doc_ref.delete())
                self.get_context.cache_clear()
                logger.info(f"Все данные пользователя {user_id} успешно удалены из Firebase.")
            except Exception as e:
                logger.error(f"Ошибка при удалении данных пользователя {user_id} из Firebase: {e}")
                raise
        else:
            if user_id in self.local_memory:
                del self.local_memory[user_id]
                logger.info(f"Все данные пользователя {user_id} успешно удалены из локального хранилища.")
            self.get_context.cache_clear()