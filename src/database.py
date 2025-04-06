import asyncio
from typing import List, Dict
import time
import json
from pathlib import Path
import firebase_admin
from firebase_admin import firestore
from .logging_config import logger
from .config import BotConfig
from datetime import datetime

class Database:
    """Управление базой данных: Firebase Firestore или локальное хранилище."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)
            cls._instance.__init_inner__()
        return cls._instance

    def __init_inner__(self):
        self.memory_dir = Path(__file__).parent.parent / "memories"
        self.local_files: Dict[str, Dict[str, str]] = {}
        self.user_dirs: Dict[str, Path] = {}
        self.use_firebase = False
        config = BotConfig()

        try:
            if config.use_firebase and firebase_admin._apps:
                self.db = firestore.client()
                self.collection = self.db.collection("memories")
                self.use_firebase = True
                logger.debug("Firestore подключён")
            else:
                if config.use_firebase:
                    logger.error("Firebase не инициализирован, хотя config.use_firebase=True")
                self._init_local_storage()
        except Exception as e:
            logger.error(f"Ошибка подключения к Firestore: {e}")
            self.use_firebase = False
            self._init_local_storage()

    def _init_local_storage(self):
        """Инициализация локального хранилища."""
        if not self.use_firebase:
            if not self.memory_dir.exists():
                self.memory_dir.mkdir()
            logger.debug("Локальное хранилище готово")

    def _get_user_dir(self, user_id: str, username: str, server_id: str = "global") -> Path:
        """Получение или создание директории пользователя только с корректным username."""
        if not username:
            raise ValueError("Username обязателен для создания директории")

        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            username = username.replace(char, '_')

        if user_id not in self.user_dirs:
            server_dir = self.memory_dir / server_id
            user_dir = server_dir / f"{username} - ({user_id})"

            if not server_dir.exists():
                server_dir.mkdir()
            if not user_dir.exists():
                user_dir.mkdir()
                logger.debug(f"Создана директория для {user_id}: {user_dir}")

            self.user_dirs[user_id] = user_dir
        return self.user_dirs[user_id]

    def _get_user_file(self, user_id: str, username: str, server_id: str = "global") -> Path:
        """Получение пути к файлу пользователя за текущий день (только для локального хранилища)."""
        if self.use_firebase:
            raise RuntimeError("Локальное хранилище не используется при активном Firebase")

        if not username:
            raise ValueError("Username обязателен для создания файла")

        current_date = datetime.now().strftime("%Y-%m-%d")
        if user_id not in self.local_files:
            self.local_files[user_id] = {}

        if current_date not in self.local_files[user_id]:
            user_dir = self._get_user_dir(user_id, username, server_id)
            file_path = user_dir / f"{current_date}.json"

            if not file_path.exists():
                with file_path.open('w') as f:
                    json.dump({"user_info": {"id": user_id, "username": username}, "threads": []}, f)
                logger.debug(f"Создан файл для {user_id} на {current_date}")

            self.local_files[user_id][current_date] = str(file_path)

        return Path(self.local_files[user_id][current_date])

    def _load_user_data(self, user_id: str, date: str) -> Dict:
        """Загрузка данных пользователя за указанную дату (только для локального хранилища)."""
        if self.use_firebase:
            raise RuntimeError("Локальное хранилище не используется при активном Firebase")

        if user_id not in self.local_files or date not in self.local_files[user_id]:
            return {"user_info": {"id": user_id, "username": ""}, "threads": []}
        file_path = Path(self.local_files[user_id][date])
        try:
            with file_path.open('r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            logger.warning(f"Ошибка JSON в {file_path}")
            return {"user_info": {"id": user_id, "username": ""}, "threads": []}

    def _save_user_data(self, user_id: str, date: str, data: Dict) -> None:
        """Сохранение данных пользователя за указанную дату (только для локального хранилища)."""
        if self.use_firebase:
            raise RuntimeError("Локальное хранилище не используется при активном Firebase")

        file_path = Path(self.local_files[user_id][date])
        with file_path.open('w') as f:
            json.dump(data, f)

    async def add_message(self, user_id: str, message_id: str, role: str, content: str, username: str = "", server_id: str = "global") -> None:
        """Добавление сообщения в базу данных."""
        if not username:
            raise ValueError("Username обязателен для добавления сообщения")

        current_date = datetime.now().strftime("%Y-%m-%d")
        message_data = {
            "message_id": message_id,
            "role": role,
            "content": content,
            "timestamp": time.time()
        }

        if self.use_firebase:
            try:
                batch = self.db.batch()
                server_doc = self.collection.document(server_id)
                user_doc = server_doc.collection("users").document(f"{username} - ({user_id})")
                date_doc = user_doc.collection("dates").document(current_date)
                thread_doc = date_doc.collection("threads").document(message_id)

                batch.set(server_doc, {"server_id": server_id}, merge=True)
                batch.set(user_doc, {"user_info": {"id": user_id, "username": username}}, merge=True)
                batch.set(date_doc, {"date": current_date}, merge=True)
                batch.set(thread_doc, message_data)

                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, batch.commit)
                logger.debug(f"Сообщение добавлено в Firebase для {user_id} на {current_date}")
            except Exception as e:
                logger.error(f"Ошибка записи в Firestore: {e}")
                raise
        else:
            file_path = self._get_user_file(user_id, username, server_id)
            user_data = self._load_user_data(user_id, current_date)
            user_data["user_info"]["username"] = username
            user_data["threads"].append(message_data)
            self._save_user_data(user_id, current_date, user_data)
            logger.debug(f"Сообщение добавлено локально для {user_id} на {current_date}")

    async def get_context(self, user_id: str, limit: int = 10, server_id: str = "global") -> List[Dict[str, str]]:
        """Получение контекста сообщений за последние дни."""
        if self.use_firebase:
            try:
                user_doc = self.collection.document(server_id).collection("users").document(f"{(await self.get_user_info(user_id, server_id))['username']} - ({user_id})")
                date_docs = user_doc.collection("dates").order_by("date", direction=firestore.Query.DESCENDING).limit(2)
                loop = asyncio.get_running_loop()
                dates = await loop.run_in_executor(None, lambda: [doc.id for doc in date_docs.stream()])

                all_threads = []
                for date in dates:
                    threads_ref = user_doc.collection("dates").document(date).collection("threads")
                    query = threads_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit)
                    threads = await loop.run_in_executor(None, lambda: [doc.to_dict() for doc in query.stream()])
                    all_threads.extend(threads)

                all_threads.sort(key=lambda x: x["timestamp"], reverse=True)
                return [{"role": doc["role"], "content": doc["content"]} for doc in all_threads[:limit]]
            except Exception as e:
                logger.error(f"Ошибка чтения из Firestore: {e}")
                raise
        else:
            if user_id not in self.user_dirs:
                return []

            user_dir = self.user_dirs[user_id]
            if not user_dir.exists():
                return []

            all_threads = []
            for file_path in sorted(user_dir.glob("*.json"), reverse=True)[:2]:
                data = self._load_user_data(user_id, file_path.stem)
                all_threads.extend(data["threads"])

            all_threads.sort(key=lambda x: x["timestamp"], reverse=True)
            return [{"role": doc["role"], "content": doc["content"]} for doc in all_threads[:limit]]

    async def get_user_info(self, user_id: str, server_id: str = "global") -> Dict[str, str]:
        """Получение информации о пользователе без создания директории."""
        if self.use_firebase:
            try:
                user_doc = self.collection.document(server_id).collection("users").document(f"{user_id}")
                loop = asyncio.get_running_loop()
                doc = await loop.run_in_executor(None, lambda: user_doc.get())
                if doc.exists:
                    user_info = doc.to_dict().get("user_info", {"id": user_id, "username": ""})
                    return user_info
                return {"id": user_id, "username": ""}
            except Exception as e:
                logger.error(f"Ошибка чтения информации пользователя из Firestore: {e}")
                raise
        else:
            current_date = datetime.now().strftime("%Y-%m-%d")
            if user_id in self.local_files and current_date in self.local_files[user_id]:
                return self._load_user_data(user_id, current_date)["user_info"]
            return {"id": user_id, "username": ""}

    async def clear_user_data(self, user_id: str, server_id: str = "global") -> None:
        """Очистка данных пользователя."""
        if self.use_firebase:
            try:
                user_doc = self.collection.document(server_id).collection("users").document(f"{(await self.get_user_info(user_id, server_id))['username']} - ({user_id})")
                date_docs = user_doc.collection("dates").stream()
                loop = asyncio.get_running_loop()
                dates = await loop.run_in_executor(None, lambda: [doc.id for doc in date_docs])

                for date in dates:
                    threads_ref = user_doc.collection("dates").document(date).collection("threads")
                    threads_docs = await loop.run_in_executor(None, lambda: [doc for doc in threads_ref.stream()])
                    for i in range(0, len(threads_docs), 500):
                        batch = self.db.batch()
                        for doc in threads_docs[i:i + 500]:
                            batch.delete(doc.reference)
                        await loop.run_in_executor(None, batch.commit)
                    await loop.run_in_executor(None, lambda: user_doc.collection("dates").document(date).delete())

                await loop.run_in_executor(None, lambda: user_doc.delete())
                logger.info(f"Данные {user_id} удалены из Firebase")
            except Exception as e:
                logger.error(f"Ошибка удаления данных из Firestore: {e}")
                raise
        else:
            if user_id in self.local_files:
                user_dir = self.user_dirs.get(user_id)
                if user_dir and user_dir.exists():
                    for file_path in user_dir.glob("*.json"):
                        file_path.unlink()
                    user_dir.rmdir()
                    logger.info(f"Удалена папка {user_dir}")
                del self.local_files[user_id]
                if user_id in self.user_dirs:
                    del self.user_dirs[user_id]

    def __del__(self):
        """Очистка при завершении работы (только для локального хранилища)."""
        if self.use_firebase:
            return
        for user_id, dates in self.local_files.items():
            for file_path in dates.values():
                file = Path(file_path)
                if file.exists():
                    file.unlink()
                    logger.info(f"Удалён файл: {file}")
        for user_id, user_dir in self.user_dirs.items():
            if user_dir.exists() and not any(user_dir.iterdir()):
                user_dir.rmdir()
                logger.info(f"Удалена папка: {user_dir}")
        for server_dir in self.memory_dir.iterdir():
            if server_dir.is_dir() and not any(server_dir.iterdir()):
                server_dir.rmdir()
        if self.memory_dir.exists() and not any(self.memory_dir.iterdir()):
            self.memory_dir.rmdir()
            logger.info(f"Удалена папка: {self.memory_dir}")