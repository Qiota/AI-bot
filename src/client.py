import discord
from discord import app_commands
import asyncio
from typing import Dict, Optional, DefaultDict
from .systemLog import logger
import time
from collections import defaultdict
import uuid
import traceback
import aiohttp
from .commands.restrict import create_command as restrict_command
from .commands.giveaway import create_command as giveaway_command
from .utils.firebase.firebase_manager import FirebaseManager
from .utils.checker import checker
from g4f.client import AsyncClient as G4FClient
from g4f.Provider import PollinationsAI

class BotClient:
    """Клиент Discord-бота с поддержкой текстовых и vision моделей через G4F и PollinationsAI."""
    
    def __init__(self, config: Dict) -> None:
        """Инициализация клиента бота с конфигурацией."""
        logger.info("Инициализация BotClient")
        self.config: Dict = config
        self.bot: discord.Client = discord.Client(intents=self._setup_intents())
        self.tree: app_commands.CommandTree = app_commands.CommandTree(self.bot)
        self.g4f_client: Optional[G4FClient] = None
        try:
            self.g4f_client = G4FClient(provider=PollinationsAI)
        except Exception as e:
            logger.error(f"Ошибка инициализации G4FClient: {e}")
        self.firebase_manager: Optional[FirebaseManager] = None
        self.giveaways: Dict = {}
        self.completed_giveaways: Dict = {}
        self.models: Dict = {
            "text": [],
            "vision": [],
            "last_update": None,
            "unavailable": {"text": [], "vision": []},
            "last_successful": {"text": None, "vision": None},
            "model_stats": {"text": {}, "vision": {}}
        }
        self.models_loaded: bool = False
        self.chat_memory: DefaultDict[str, list] = defaultdict(list)
        self.topic_memory: DefaultDict[str, list] = defaultdict(list)
        self.current_conversation: DefaultDict[str, Dict] = defaultdict(lambda: {
            "id": str(uuid.uuid4()),
            "last_message_time": time.time(),
            "request_count": 0,
            "ttl_seconds": 86400
        })
        self.processed_messages: set = set()
        self.message_to_response: Dict = {}
        self.user_settings: DefaultDict[str, Dict[str, int]] = defaultdict(lambda: {"max_response_length": 2000})
        self.last_message_time: DefaultDict[str, float] = defaultdict(float)
        self.model_queues: Dict[str, asyncio.Queue] = {}
        self.model_semaphores: Dict[str, asyncio.Semaphore] = {}
        self._initialize_settings()
        self.bot.event(self.on_ready)
        self.bot.setup_hook = self._setup_hook
        self.start_time: float = time.time()  # Для команды /uptime

    async def _setup_hook(self) -> None:
        """Асинхронный хук для инициализации Firebase и запуска задач."""
        if not self.bot:
            logger.error("Бот не инициализирован в setup_hook")
            return
        try:
            await self._ensure_firebase_initialized()
            self.tree.add_command(restrict_command(self))
            giveaway, reroll, edit = giveaway_command(self)
            self.tree.add_command(giveaway)
            self.tree.add_command(reroll)
            self.tree.add_command(edit)
            logger.debug("Команды розыгрышей и restrict добавлены в CommandTree")
            asyncio.create_task(self.update_models_periodically())
            asyncio.create_task(self.cleanup_conversations_periodically())
            from .commands.giveaway import resume_giveaways
            asyncio.create_task(resume_giveaways(self))
            logger.success("Асинхронные задачи запущены в setup_hook")
        except Exception as e:
            logger.error(f"Ошибка в setup_hook: {e}\n{traceback.format_exc()}")

    async def _ensure_firebase_initialized(self) -> Optional[FirebaseManager]:
        """Гарантирует инициализацию Firebase."""
        if not self.firebase_manager:
            try:
                self.firebase_manager = await FirebaseManager.initialize()
                logger.info("FirebaseManager успешно инициализирован")
            except Exception as e:
                logger.error(f"Ошибка инициализации Firebase: {e}\n{traceback.format_exc()}")
                self.firebase_manager = None
        return self.firebase_manager

    async def on_ready(self) -> None:
        """Обработчик события готовности бота."""
        logger.info(f"Бот {self.bot.user} готов к работе")
        try:
            await checker.initialize()
            logger.info("Checker инициализирован")
            await self.fetch_available_models()
            if not self.models["text"] or not self.models["vision"]:
                logger.error("Не удалось загрузить модели при старте")
                self.models_loaded = False
            else:
                self.models_loaded = True
                logger.info("Модели успешно загружены")
                self._initialize_model_queues()
        except Exception as e:
            logger.error(f"Ошибка загрузки моделей в on_ready: {e}\n{traceback.format_exc()}")
            self.models_loaded = False

    async def close(self) -> None:
        """Закрытие клиента Discord."""
        try:
            if self.bot:
                await self.bot.close()
                logger.info("Клиент Discord закрыт")
        except Exception as e:
            logger.error(f"Ошибка закрытия клиента: {e}\n{traceback.format_exc()}")

    def _setup_intents(self) -> discord.Intents:
        """Настройка намерений Discord API."""
        intents = discord.Intents.default()
        intents.message_content = intents.dm_messages = intents.members = True
        return intents

    def _initialize_settings(self) -> None:
        """Инициализация настроек бота."""
        self.cache_limits: Dict[str, int] = {
            "messages": 25,
            "topics": 2,
            "memory_days": 1,
            "cache_ttl_seconds": 3600,
            "models_ttl_seconds": 86400,
            "max_conversation_ttl": 604800,
            "min_conversation_ttl": 3600
        }
        self.request_settings: Dict[str, float | int] = {
            "rate_limit_delay": 3.0,
            "max_retries": 3,
            "retry_delay_base": 5.0,
            "max_queue_size": 5,
            "max_concurrent_requests": 2
        }
        self.spam_cooldown: float = 3.0

    def _initialize_model_queues(self) -> None:
        """Инициализация очередей и семафоров для моделей."""
        for model_type in ["text", "vision"]:
            for model in self.models[model_type]:
                self.model_queues[model] = asyncio.Queue(maxsize=self.request_settings["max_queue_size"])
                self.model_semaphores[model] = asyncio.Semaphore(self.request_settings["max_concurrent_requests"])
                logger.debug(f"Инициализирована очередь для модели {model} ({model_type})")

    async def check_spam(self, user_id: str) -> bool:
        """Проверка на спам."""
        current_time = time.time()
        last_time = self.last_message_time[user_id]
        if current_time - last_time < self.spam_cooldown:
            logger.debug(f"Спам обнаружен для пользователя {user_id}")
            return False
        self.last_message_time[user_id] = current_time
        return True

    async def is_bot_mentioned(self, message: discord.Message) -> bool:
        """Проверка упоминания бота."""
        if not self.bot or not self.bot.user:
            logger.error("Бот или его пользователь не инициализированы")
            return False
        if isinstance(message.channel, discord.DMChannel):
            return True
        return self.bot.user in message.mentions or f"<@{self.bot.user.id}>" in message.content

    async def update_models_periodically(self) -> None:
        """Периодическое обновление моделей."""
        logger.debug("Запуск update_models_periodically")
        while True:
            try:
                if not self.models["last_update"] or (time.time() - self.models["last_update"]) > self.cache_limits["models_ttl_seconds"]:
                    await self.fetch_available_models()
                    if not self.models["text"] or not self.models["vision"]:
                        logger.error("Не удалось обновить модели")
                        self.models_loaded = False
                    else:
                        self.models_loaded = True
                        logger.info("Модели обновлены")
                        self._initialize_model_queues()
                await asyncio.sleep(3600)
            except Exception as e:
                logger.error(f"Ошибка обновления моделей: {e}\n{traceback.format_exc()}")
                self.models_loaded = False
                await asyncio.sleep(1800)

    async def fetch_available_models(self) -> None:
        """Загрузка моделей из Pollinations API и Firebase."""
        logger.debug("Начало fetch_available_models")
        vision_models: list = []
        text_models: list = []

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://text.pollinations.ai/models", timeout=10) as response:
                    if response.status == 200:
                        models_data = await response.json()
                        vision_models = [m.get("name") for m in models_data if isinstance(m, dict) and m.get("vision", False) and m.get("name")]
                        text_models = [m.get("name") for m in models_data if isinstance(m, dict) and not m.get("vision", False) and m.get("name")]
                        logger.debug(f"Vision: {vision_models}, Text: {text_models}")
                    else:
                        logger.warning(f"Ошибка API Pollinations: статус {response.status}")
        except Exception as e:
            logger.error(f"Ошибка загрузки моделей из API Pollinations: {e}\n{traceback.format_exc()}")

        if text_models or vision_models:
            try:
                self.models["vision"] = vision_models
                self.models["text"] = text_models
                self.models["unavailable"]["vision"] = []
                self.models["unavailable"]["text"] = []
                self.models["last_update"] = time.time()
                for model in vision_models:
                    if model not in self.models["model_stats"]["vision"]:
                        self.models["model_stats"]["vision"][model] = {"success": 0, "failure": 0}
                for model in text_models:
                    if model not in self.models["model_stats"]["text"]:
                        self.models["model_stats"]["text"][model] = {"success": 0, "failure": 0}
                if self.firebase_manager:
                    await self.firebase_manager.save_models({"timestamp": self.models["last_update"], **self.models})
                    logger.success(f"Модели сохранены в Firebase: Text={len(text_models)}, Vision={len(vision_models)}")
                else:
                    logger.warning("FirebaseManager не инициализирован, модели не сохранены")
            except Exception as e:
                logger.error(f"Ошибка сохранения моделей в Firebase: {e}\n{traceback.format_exc()}")

        if self.firebase_manager:
            try:
                loaded_models = await self.firebase_manager.load_models()
                if isinstance(loaded_models, dict) and loaded_models.get("timestamp", 0) + self.cache_limits["models_ttl_seconds"] > time.time():
                    self.models.update(loaded_models)
                    logger.success(f"Модели загружены из Firebase: Text={len(self.models['text'])}, Vision={len(self.models['vision'])}")
                else:
                    if not self.models["text"] and not self.models["vision"]:
                        self.models["text"] = ["text"]
                        self.models["vision"] = ["vision"]
                        self.models["last_update"] = time.time()
                        for model in self.models["vision"]:
                            if model not in self.models["model_stats"]["vision"]:
                                self.models["model_stats"]["vision"][model] = {"success": 0, "failure": 0}
                        for model in self.models["text"]:
                            if model not in self.models["model_stats"]["text"]:
                                self.models["model_stats"]["text"][model] = {"success": 0, "failure": 0}
                        await self.firebase_manager.save_models({"timestamp": self.models["last_update"], **self.models})
                        logger.success(f"Резервные модели сохранены в Firebase")
            except Exception as e:
                logger.error(f"Ошибка загрузки моделей из Firebase: {e}\n{traceback.format_exc()}")
        else:
            logger.warning("FirebaseManager не инициализирован, загрузка моделей пропущена")

    async def cleanup_conversations_periodically(self) -> None:
        """Очистка устаревших разговоров."""
        while True:
            try:
                current_time = time.time()
                expired_users = []
                
                for user_id, conversation in self.current_conversation.items():
                    if current_time - conversation["last_message_time"] > conversation["ttl_seconds"]:
                        conversation_id = conversation["id"]
                        expired_users.append(user_id)
                        del self.chat_memory[conversation_id]
                        del self.topic_memory[conversation_id]
                
                for user_id in expired_users:
                    del self.current_conversation[user_id]
                
                if self.firebase_manager:
                    await self.firebase_manager.cleanup_expired_conversations(current_time)
                    logger.debug(f"Очищено {len(expired_users)} разговоров")
                else:
                    logger.warning("FirebaseManager не инициализирован, очистка разговоров пропущена")
                
                await asyncio.sleep(3600)
            except Exception as e:
                logger.error(f"Ошибка очистки разговоров: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(1800)