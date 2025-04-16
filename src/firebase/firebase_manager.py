import firebase_admin
from firebase_admin import credentials, db
from ..systemLog import logger
import os

class FirebaseManager:
    _instance = None

    @classmethod
    def initialize(cls):
        if cls._instance is None:
            try:
                cred_path = os.path.join(os.path.dirname(__file__), "serviceAccountKey.json")
                if not os.path.exists(cred_path):
                    logger.error(f"Файл учетных данных Firebase не найден: {cred_path}")
                    raise FileNotFoundError(f"Firebase credentials file not found: {cred_path}")
                cred = credentials.Certificate(cred_path)
                firebase_admin.initialize_app(cred, {
                    'databaseURL': "https://ai-assist-fe86c-default-rtdb.europe-west1.firebasedatabase.app/"
                })
                cls._instance = cls()
                logger.success("Firebase успешно инициализирован")
            except Exception as e:
                logger.error(f"Ошибка инициализации Firebase: {e}")
                raise
        return cls._instance

    def load(self, guild_id: str) -> dict:
        try:
            ref = db.reference(f"/guilds/{guild_id}")
            data = ref.get()
            return data or {"bot_allowed_channels": [], "restricted_users": []}
        except Exception as e:
            logger.error(f"Ошибка чтения данных для guild_id {guild_id}: {e}")
            return {"bot_allowed_channels": [], "restricted_users": []}

    def save(self, guild_id: str, config: dict):
        try:
            ref = db.reference(f"/guilds/{guild_id}")
            ref.set(config)
            logger.success(f"Конфигурация сохранена для guild_id {guild_id}")
        except Exception as e:
            logger.error(f"Ошибка сохранения данных для guild_id {guild_id}: {e}")

    def update_fields(self, guild_id: str, fields: dict):
        try:
            ref = db.reference(f"/guilds/{guild_id}")
            ref.update(fields)
            logger.success(f"Поля обновлены для guild_id {guild_id}: {fields.keys()}")
        except Exception as e:
            logger.error(f"Ошибка обновления полей для guild_id {guild_id}: {e}")
            raise

    def load_models(self) -> dict:
        try:
            ref = db.reference("/models")
            data = ref.get()
            return data or {"text": ["gpt-4o-mini", "gpt-4o", "o1-mini"], "vision": ["openai", "openai-large"], "last_update": None}
        except Exception as e:
            logger.error(f"Ошибка чтения моделей: {e}")
            return {"text": ["gpt-4o-mini", "gpt-4o", "o1-mini"], "vision": ["openai", "openai-large"], "last_update": None}

    def save_models(self, models: dict):
        try:
            ref = db.reference("/models")
            ref.set({
                "text": models["text"],
                "vision": models["vision"],
                "last_update": models["last_update"]
            })
            logger.success("Модели сохранены в Firebase")
        except Exception as e:
            logger.error(f"Ошибка сохранения моделей: {e}")

    def load_giveaways(self) -> dict:
        try:
            ref = db.reference("/giveaways")
            data = ref.get() or {"active": {}, "completed": {}}
            return data
        except Exception as e:
            logger.error(f"Ошибка чтения розыгрышей: {e}")
            return {"active": {}, "completed": {}}

    def save_giveaways(self, active: dict, completed: dict):
        try:
            ref = db.reference("/giveaways")
            ref.set({"active": active, "completed": completed})
            logger.success("Розыгрыши сохранены в Firebase")
        except Exception as e:
            logger.error(f"Ошибка сохранения розыгрышей: {e}")