import firebase_admin
from firebase_admin import credentials, db
from ..systemLog import logger
import os
from ..commands.prompt import default_prompt

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
                logger.info("Firebase инициализирован")
            except Exception as e:
                logger.error(f"Ошибка инициализации Firebase: {e}")
                raise
        return cls._instance

    def load_guild_config(self, guild_id: str) -> dict:
        try:
            if guild_id == "DM":
                logger.debug("Конфигурация для DM, возвращается дефолтная")
                return {"bot_allowed_channels": [], "restricted_users": []}
            ref = db.reference(f"/guilds/{guild_id}")
            data = ref.get()
            if not isinstance(data, dict):
                logger.warning(f"Некорректные данные конфигурации для guild_id {guild_id}: {data}")
                return {"bot_allowed_channels": [], "restricted_users": []}
            return data or {"bot_allowed_channels": [], "restricted_users": []}
        except Exception as e:
            logger.error(f"Ошибка чтения конфигурации для guild_id {guild_id}: {e}")
            return {"bot_allowed_channels": [], "restricted_users": []}

    def save_guild_config(self, guild_id: str, config: dict):
        if not isinstance(config, dict):
            logger.error(f"Некорректный формат конфигурации для guild_id {guild_id}: {config}")
            raise ValueError("Конфигурация должна быть словарем")
        try:
            ref = db.reference(f"/guilds/{guild_id}")
            ref.set(config)
            logger.info(f"Конфигурация сохранена для guild_id {guild_id}")
        except Exception as e:
            logger.error(f"Ошибка сохранения конфигурации для guild_id {guild_id}: {e}")
            raise

    def update_guild_fields(self, guild_id: str, fields: dict):
        if not isinstance(fields, dict):
            logger.error(f"Некорректный формат полей для guild_id {guild_id}: {fields}")
            raise ValueError("Поля должны быть словарем")
        try:
            ref = db.reference(f"/guilds/{guild_id}")
            ref.update(fields)
            logger.info(f"Поля обновлены для guild_id {guild_id}: {fields.keys()}")
        except Exception as e:
            logger.error(f"Ошибка обновления полей для guild_id {guild_id}: {e}")
            raise

    def load_models(self) -> dict:
        try:
            ref = db.reference("/models")
            data = ref.get()
            default_models = {
                "text": [
                    "gpt-4o-mini", "gpt-4o", "o1-mini", "qwen-2.5-coder-32b", "llama-3.3-70b", "mistral-nemo",
                    "llama-3.1-8b", "deepseek-r1", "phi-4", "qwq-32b", "deepseek-v3", "llama-3.2-11b",
                    "grok-3", "claude-3.5-sonnet", "gemini-1.5-pro", "mixtral-8x7b"
                ],
                "vision": [
                    "gpt-4o", "gpt-4o-mini", "o1-mini", "flux", "flux-realism", "flux-anime", "flux-3d"
                ],
                "last_update": None,
                "unavailable": {"text": [], "vision": []},
                "last_successful": {"text": None, "vision": None}
            }
            if not data:
                return default_models
            if not isinstance(data.get("unavailable", {}).get("text", []), list) or \
               not isinstance(data.get("unavailable", {}).get("vision", []), list):
                logger.warning("Некорректный формат unavailable в моделях, сброс на дефолт")
                data["unavailable"] = {"text": [], "vision": []}
            return data
        except Exception as e:
            logger.error(f"Ошибка чтения моделей: {e}")
            return default_models

    def save_models(self, models: dict):
        if not isinstance(models, dict):
            logger.error(f"Некорректный формат моделей: {models}")
            raise ValueError("Модели должны быть словарем")
        try:
            ref = db.reference("/models")
            ref.set({
                "text": models["text"],
                "vision": models["vision"],
                "last_update": models["last_update"],
                "unavailable": {
                    "text": list(models["unavailable"]["text"]),
                    "vision": list(models["unavailable"]["vision"])
                },
                "last_successful": models["last_successful"]
            })
            logger.info("Модели сохранены в Firebase")
        except Exception as e:
            logger.error(f"Ошибка сохранения моделей: {e}")
            raise

    def load_giveaways(self) -> dict:
        try:
            ref = db.reference("/giveaways")
            data = ref.get()
            if not isinstance(data, dict):
                logger.warning(f"Некорректные данные розыгрышей: {data}")
                return {"active": {}, "completed": {}}
            return data or {"active": {}, "completed": {}}
        except Exception as e:
            logger.error(f"Ошибка чтения розыгрышей: {e}")
            return {"active": {}, "completed": {}}

    def save_giveaways(self, giveaways: dict):
        if not isinstance(giveaways, dict):
            logger.error(f"Некорректный формат розыгрышей: {giveaways}")
            raise ValueError("Розыгрыши должны быть словарем")
        try:
            ref = db.reference("/giveaways")
            ref.set(giveaways)
            logger.info("Розыгрыши сохранены в Firebase")
        except Exception as e:
            logger.error(f"Ошибка сохранения розыгрышей: {e}")
            raise

    def load_prompt(self, guild_id: str, user_id: str) -> str:
        try:
            ref = db.reference(f"/prompts/{guild_id}/{user_id}")
            data = ref.get()
            if not data or not isinstance(data.get("system_prompt", ""), str):
                return default_prompt
            return data.get("system_prompt", default_prompt)
        except Exception as e:
            logger.error(f"Ошибка чтения промпта для guild_id {guild_id}, user_id {user_id}: {e}")
            return default_prompt

    def save_prompt(self, guild_id: str, user_id: str, prompt: str):
        if not isinstance(prompt, str):
            logger.error(f"Некорректный формат промпта для guild_id {guild_id}, user_id {user_id}: {prompt}")
            raise ValueError("Промпт должен быть строкой")
        try:
            ref = db.reference(f"/prompts/{guild_id}/{user_id}")
            ref.set({"system_prompt": prompt})
            logger.info(f"Промпт сохранен для guild_id {guild_id}, user_id {user_id}")
        except Exception as e:
            logger.error(f"Ошибка сохранения промпта для guild_id {guild_id}, user_id {user_id}: {e}")
            raise

    def load_user_settings(self, user_id: str) -> dict:
        try:
            ref = db.reference(f"/user_settings/{user_id}")
            data = ref.get()
            if not isinstance(data, dict):
                logger.warning(f"Некорректные настройки пользователя {user_id}: {data}")
                return {"max_response_length": 2000, "preferred_model": None}
            return data or {"max_response_length": 2000, "preferred_model": None}
        except Exception as e:
            logger.error(f"Ошибка чтения настроек пользователя {user_id}: {e}")
            return {"max_response_length": 2000, "preferred_model": None}

    def save_user_settings(self, user_id: str, settings: dict):
        if not isinstance(settings, dict):
            logger.error(f"Некорректный формат настроек для пользователя {user_id}: {settings}")
            raise ValueError("Настройки должны быть словарем")
        try:
            ref = db.reference(f"/user_settings/{user_id}")
            ref.set(settings)
            logger.info(f"Настройки сохранены для пользователя {user_id}")
        except Exception as e:
            logger.error(f"Ошибка сохранения настроек для пользователя {user_id}: {e}")
            raise