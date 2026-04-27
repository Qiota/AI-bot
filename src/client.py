import discord
from discord import app_commands
from typing import Dict
from .systemLog import logger
import time
from collections import defaultdict
import uuid
from src.utils.firebase.firebase_manager import FirebaseManager
from g4f.client import AsyncClient as G4FClient

class BotClient:
    def __init__(self, config: Dict) -> None:
        logger.info("Инициализация BotClient")
        self.config = config
        self.bot = discord.Client(intents=self._setup_intents())
        self.tree = app_commands.CommandTree(self.bot)
        
        # Инициализация G4F клиента
        self.g4f_client = G4FClient()
        
        self.firebase_manager = None
        self.chat_memory = defaultdict(list)
        self.current_conversation = defaultdict(lambda: {
            "id": str(uuid.uuid4()),
            "last_message_time": time.time(),
            "ttl_seconds": 86400
        })

        # Важно: всегда возвращаем словарь настроек
        self.user_settings = defaultdict(lambda: {
            "selected_text_model": "gpt-4o",
            "selected_provider": "Auto"
        })
        
        self.bot.event(self.on_ready)
        self.bot.setup_hook = self._setup_hook

    def _setup_intents(self) -> discord.Intents:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        return intents

    async def _setup_hook(self) -> None:
        logger.success("BotClient: Хуки настроены.")

    async def on_ready(self) -> None:
        logger.info(f"Бот онлайн как {self.bot.user}")

    async def _ensure_firebase_initialized(self):
        """Ensure FirebaseManager is initialized and return instance."""
        if self.firebase_manager is None:
            self.firebase_manager = await FirebaseManager.initialize()
        return self.firebase_manager

    @property
    def is_ready(self) -> bool:
        """Returns whether the underlying Discord client is ready."""
        return self.bot is not None and self.bot.is_ready()

    async def is_bot_mentioned(self, message: discord.Message) -> bool:
        if isinstance(message.channel, discord.DMChannel): return True
        return self.bot.user in message.mentions or f"<@{self.bot.user.id}>" in message.content
