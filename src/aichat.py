import discord
from discord import app_commands, Forbidden, HTTPException
import asyncio
from typing import List, Dict, Optional, DefaultDict, Set, Tuple
from g4f.client import AsyncClient as G4FClient
from g4f.Provider import PollinationsAI
from .systemLog import logger
import time
from collections import defaultdict
import uuid
from .commands.restrict import check_bot_access, create_command as restrict_command
from .commands.giveaway import create_command as giveaway_command
from .utils.firebase.firebase_manager import FirebaseManager
from .utils.checker import checker
import aiohttp
import json
import hashlib
import backoff
from aiohttp import ClientSession, ClientTimeout
from g4f.errors import ProviderNotFoundError, ModelNotSupportedError, ResponseError, RateLimitError, ResponseStatusError
import g4f
from datetime import datetime, timezone
import traceback

# Безопасная проверка версии g4f
version = getattr(g4f, "__version__", "неизвестна")
logger.info(f"Используемая версия g4f: {version}")

# Фиксированные промпты
DEFAULT_PROMPT = "Ты полезный и дружелюбный ассистент. Отвечай кратко, по делу, на русском языке. Учитывай контекст и предоставляй точные ответы. Время: {now}"
DEFAULT_VISION_PROMPT = "Ты эксперт по анализу изображений. Опиши изображение кратко и точно, отвечая на запрос пользователя. Время: {now}"

class BotClient:
    """Клиент Discord-бота с поддержкой текстовых и vision моделей через G4F и PollinationsAI."""
    
    def __init__(self, config: Dict) -> None:
        """Инициализация клиента бота с конфигурацией."""
        logger.info("Инициализация BotClient")
        self.config: Dict = config
        self.bot: discord.Client = discord.Client(intents=self._setup_intents())
        self.tree: app_commands.CommandTree = app_commands.CommandTree(self.bot)
        self.g4f_client: G4FClient = G4FClient(provider=PollinationsAI)
        self.firebase_manager: Optional[FirebaseManager] = None
        self.giveaways: Dict = {}
        self.completed_giveaways: Dict = {}
        self.models: Dict[str, List[str] | float | Dict[str, List[str]] | Dict[str, Dict[str, int]] | None] = {
            "text": [],
            "vision": [],
            "last_update": None,
            "unavailable": {"text": [], "vision": []},
            "last_successful": {"text": None, "vision": None},
            "model_stats": {"text": {}, "vision": {}}
        }
        self.models_loaded: bool = False
        self.chat_memory: DefaultDict[str, List[Dict]] = defaultdict(list)
        self.topic_memory: DefaultDict[str, List[str]] = defaultdict(list)
        self.current_conversation: DefaultDict[str, Dict] = defaultdict(lambda: {
            "id": str(uuid.uuid4()),
            "last_message_time": time.time(),
            "request_count": 0,
            "ttl_seconds": 86400
        })
        self.processed_messages: Set[str] = set()
        self.message_to_response: Dict[str | int, int] = {}
        self.user_settings: DefaultDict[str, Dict[str, int]] = defaultdict(lambda: {"max_response_length": 2000})
        self.last_message_time: DefaultDict[str, float] = defaultdict(float)
        self.model_queues: Dict[str, asyncio.Queue] = {}
        self.model_semaphores: Dict[str, asyncio.Semaphore] = {}
        self._initialize_settings()
        self.bot.event(self.on_ready)
        self.bot.setup_hook = self._setup_hook

    async def _setup_hook(self):
        """Асинхронный хук для инициализации Firebase и запуска задач."""
        logger.debug("Начало setup_hook")
        await self._ensure_firebase_initialized()
        logger.debug("Firebase инициализирован, запуск асинхронных задач")

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
        logger.info("Асинхронные задачи запущены в setup_hook")

    async def _ensure_firebase_initialized(self) -> FirebaseManager:
        """Гарантирует инициализацию Firebase."""
        if not self.firebase_manager:
            try:
                logger.debug("Начало инициализации Firebase")
                self.firebase_manager = await FirebaseManager.initialize()
                logger.debug("Firebase инициализирован")
            except Exception as e:
                logger.error(f"Ошибка инициализации Firebase: {e}")
                raise
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
            logger.error(f"Ошибка загрузки моделей: {e}\n{traceback.format_exc()}")
            self.models_loaded = False

    async def close(self) -> None:
        """Закрытие клиента Discord."""
        try:
            await self.bot.close()
            logger.info("Клиент Discord закрыт")
        except Exception as e:
            logger.error(f"Ошибка закрытия: {e}")

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
            return False
        self.last_message_time[user_id] = current_time
        return True

    async def is_bot_mentioned(self, message: discord.Message) -> bool:
        """Проверка упоминания бота."""
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
        vision_models = []
        text_models = []

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://text.pollinations.ai/models", timeout=10) as response:
                    if response.status == 200:
                        models_data = await response.json()
                        vision_models = [m.get("name") for m in models_data if isinstance(m, dict) and m.get("vision", False) and m.get("name")]
                        text_models = [m.get("name") for m in models_data if isinstance(m, dict) and not m.get("vision", False) and m.get("name")]
                        logger.debug(f"Vision: {vision_models}, Text: {text_models}")
                    else:
                        logger.warning(f"Ошибка API: статус {response.status}")
        except Exception as e:
            logger.error(f"Ошибка загрузки моделей из API: {e}")

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
                await self.firebase_manager.save_models({"timestamp": self.models["last_update"], **self.models})
                logger.info(f"Модели сохранены в Firebase: Text={len(text_models)}, Vision={len(vision_models)}")
            except Exception as e:
                logger.error(f"Ошибка сохранения моделей: {e}")

        try:
            loaded_models = await self.firebase_manager.load_models()
            if isinstance(loaded_models, dict) and loaded_models.get("timestamp", 0) + self.cache_limits["models_ttl_seconds"] > time.time():
                self.models.update(loaded_models)
                logger.info(f"Модели загружены из Firebase: Text={len(self.models['text'])}, Vision={len(self.models['vision'])}")
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
                    logger.info(f"Резервные модели сохранены")
        except Exception as e:
            logger.error(f"Ошибка загрузки моделей из Firebase: {e}")

    async def on_message(self, message: discord.Message) -> None:
        """Обработка входящих сообщений."""
        msg_key = f"{message.id}-{message.channel.id}"
        if message.author.bot or msg_key in self.processed_messages:
            return
        if isinstance(message.channel, (discord.StageChannel, discord.VoiceChannel)):
            return
        self.processed_messages.add(msg_key)
        try:
            user_id = str(message.author.id)
            channel_id = str(message.channel.id)
            
            if not await self.is_bot_mentioned(message):
                return

            if not self.models_loaded:
                logger.debug(f"Модели не загружены, сообщение от {user_id} пропущено")
                await self._send_temp_message(message.channel, "Бот инициализируется.", user_id, ephemeral=True)
                return

            if not await self.check_spam(user_id):
                await self._send_temp_message(message.channel, "Слишком быстро! Подождите 3 секунды.", user_id, ephemeral=True)
                return

            await self.start_new_conversation(user_id, channel_id, message.content)
            if isinstance(message.channel, discord.DMChannel):
                result, restriction_reason = await checker.check_user_restriction(message)
                if result:
                    await self._process_message(message)
                else:
                    logger.debug(f"Пользователь {user_id} ограничен в DM")
                    await self._send_temp_message(message.channel, restriction_reason or "Ваш доступ к боту ограничен.", user_id, ephemeral=True)
                    return
            else:
                access_result, access_reason = await check_bot_access(message, self)
                restriction_result, restriction_reason = await checker.check_user_restriction(message)
                if access_result and restriction_result:
                    await self._process_message(message)
                else:
                    if not access_result:
                        await self._send_temp_message(message.channel, f"Ошибка: {access_reason}", user_id, ephemeral=True)
                    else:
                        logger.debug(f"Пользователь {user_id} ограничен в гильдии {message.guild.id}")
                        await self._send_temp_message(message.channel, restriction_reason or "Ваш доступ к боту ограничен.", user_id, ephemeral=True)
                    return
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения {msg_key}: {e}\n{traceback.format_exc()}")
            await self._send_temp_message(message.channel, "Ошибка обработки.", user_id, ephemeral=True)

    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        """Обработка редактирования сообщений."""
        msg_key = f"{after.id}-{after.channel.id}"
        if before.content == after.content or after.author.bot or msg_key not in self.processed_messages:
            return
        if isinstance(after.channel, (discord.StageChannel, discord.VoiceChannel)):
            return
        try:
            user_id = str(after.author.id)
            channel_id = str(after.channel.id)

            if not await self.is_bot_mentioned(after):
                return

            if not self.models_loaded:
                logger.debug(f"Модели не загружены, редактирование от {user_id} пропущено")
                await self._send_temp_message(after.channel, "Бот инициализируется.", user_id, ephemeral=True)
                return

            if not await self.check_spam(user_id):
                await self._send_temp_message(after.channel, "Слишком быстро! Подождите 3 секунды.", user_id, ephemeral=True)
                return

            await self.start_new_conversation(user_id, channel_id, after.content)
            if isinstance(after.channel, discord.DMChannel):
                result, restriction_reason = await checker.check_user_restriction(after)
                if result:
                    await self._process_edit(after)
                else:
                    logger.debug(f"Пользователь {user_id} ограничен в DM")
                    await self._send_temp_message(after.channel, restriction_reason or "Ваш доступ к боту ограничен.", user_id, ephemeral=True)
                    return
            else:
                access_result, access_reason = await check_bot_access(after, self)
                restriction_result, restriction_reason = await checker.check_user_restriction(after)
                if access_result and restriction_result:
                    await self._process_edit(after)
                else:
                    if not access_result:
                        await self._send_temp_message(after.channel, f"Ошибка: {access_reason}", user_id, ephemeral=True)
                    else:
                        logger.debug(f"Пользователь {user_id} ограничен в гильдии {after.guild.id}")
                        await self._send_temp_message(after.channel, restriction_reason or "Ваш доступ к боту ограничен.", user_id, ephemeral=True)
                    return
        except Exception as e:
            logger.error(f"Ошибка обработки редактирования {msg_key}: {e}\n{traceback.format_exc()}")
            await self._send_temp_message(after.channel, "Ошибка обработки.", user_id, ephemeral=True)

    async def _process_message(self, message: discord.Message) -> None:
        """Обработка сообщения с генерацией ответа."""
        async with message.channel.typing():
            text = message.content.replace(f"<@{self.bot.user.id}>", "").strip()
            parts = await self.generate_response(str(message.author.id), str(message.id), text, message)
            if parts:
                await self._send_split_message(message, parts)

    async def _process_edit(self, after: discord.Message) -> None:
        """Обработка отредактированного сообщения."""
        if after.id in self.message_to_response:
            logger.debug(f"Сообщение {after.id} имеет ответ, редактирование игнорируется")
            return
        
        async with after.channel.typing():
            text = after.content.replace(f"<@{self.bot.user.id}>", "").strip()
            parts = await self.generate_response(str(after.author.id), str(after.id), text, after, is_edit=True)
            if parts:
                await self._send_split_message(after, parts)

    def _split_response(self, response: str, max_length: int = 2000) -> List[str]:
        """Разделение длинного ответа на части."""
        parts: List[str] = []
        remaining = response
        separators = [". ", "! ", "? ", "; "]

        while remaining:
            if len(remaining) <= max_length:
                if remaining.strip():
                    parts.append(remaining)
                break
            
            split_index = -1
            for sep in separators:
                idx = remaining[:max_length].rfind(sep)
                if idx != -1 and idx > split_index:
                    split_index = idx + len(sep)

            if split_index == -1:
                split_index = max_length

            part = remaining[:split_index]
            if part.strip():
                parts.append(part)
            remaining = remaining[split_index:]

        return parts if parts else ["Ответ пуст или некорректен."]

    async def _send_split_message(self, message: discord.Message, parts: List[str]) -> None:
        """Отправка частей ответа."""
        for i, part in enumerate(parts):
            try:
                logger.debug(f"Отправка части {i+1}/{len(parts)} сообщения {message.id}")
                sent_msg = await (message.reply(part) if i == 0 else message.channel.send(part))
                self.message_to_response[f"{message.id}_{i}" if i > 0 else message.id] = sent_msg.id
                conversation_id = self.current_conversation[str(message.author.id)]["id"]
                self.chat_memory[conversation_id].append({"role": "assistant", "content": part})
                await self._save_conversation(str(message.author.id), conversation_id)
            except (Forbidden, HTTPException) as e:
                logger.error(f"Ошибка отправки части {i+1}: {e}")
                await self._send_temp_message(message.channel, "Ошибка отправки.", str(message.author.id), ephemeral=True)

    async def generate_response(self, user_id: str, message_id: str, text: str, message: discord.Message, is_edit: bool = False) -> Optional[List[str]]:
        """Генерация ответа."""
        try:
            if not (text or message.attachments):
                return ["Введите текст или прикрепите изображение."]
            
            channel_type = "DM" if isinstance(message.channel, discord.DMChannel) else "guild"
            channel_id = str(message.channel.id)
            
            context = await self.get_context(user_id, message.channel)
            has_image = any(a.content_type and a.content_type.startswith("image/") for a in message.attachments)
            system_prompt = await self._build_system_prompt(has_image)
            
            attachments = [a.url for a in message.attachments if a.content_type and a.content_type.startswith("image/")]
            user_content = [{"type": "text", "text": text}] if text else []
            user_content.extend({"type": "image_url", "image_url": {"url": url}} for url in attachments)
            
            messages = [{"role": "system", "content": system_prompt}] + context + [{"role": "user", "content": user_content}]
            
            model_type = "vision" if has_image else "text"
            response = await self._try_generate_response(messages, has_image, 2000, user_id, channel_type, channel_id)
            if not response:
                return [f"Не удалось обработать {'изображение' if has_image else 'текст'}."]
            
            return self._split_response(response, self.user_settings[user_id]["max_response_length"])
        except Exception as e:
            logger.error(f"Ошибка генерации ответа для {message_id}: {e}\n{traceback.format_exc()}")
            return ["Ошибка генерации ответа."]

    def _generate_cache_key(self, messages: List[Dict], model_type: str, user_id: str, channel_type: str, channel_id: str) -> str:
        """Генерация ключа кэша."""
        message_data = json.dumps(messages, sort_keys=True)
        return f"{user_id}:{channel_type}:{channel_id}:{model_type}:{hashlib.sha256(message_data.encode()).hexdigest()}"

    async def _enqueue_request(self, model: str, messages: List[Dict], max_tokens: int, session: ClientSession) -> Optional[str]:
        """Добавление запроса в очередь модели."""
        queue = self.model_queues.get(model)
        if not queue:
            logger.error(f"Очередь для модели {model} не найдена")
            return None

        try:
            await queue.put((messages, max_tokens, session))
            logger.debug(f"Запрос добавлен в очередь {model}, размер: {queue.qsize()}")
            async with self.model_semaphores[model]:
                messages, max_tokens, session = await queue.get()
                try:
                    response = await self.g4f_client.chat.completions.create(
                        model=model,
                        messages=messages,
                        max_tokens=max_tokens,
                        web_search=True,
                        session=session
                    )
                    response_text = response.choices[0].message.content.strip()
                    logger.debug(f"Успешный ответ от {model}: {len(response_text)} символов")
                    return response_text
                finally:
                    queue.task_done()
        except Exception as e:
            logger.error(f"Ошибка в очереди для {model}: {e}")
            queue.task_done()
            raise

    async def _distribute_request(self, model_type: str, messages: List[Dict], max_tokens: int, session: ClientSession) -> Optional[str]:
        """Распределение запроса между моделями."""
        available_models = [m for m in self.models[model_type] if m not in self.models["unavailable"][model_type]]
        if not available_models:
            logger.error(f"Нет доступных моделей для {model_type}")
            return None

        model_queue_sizes = [(model, self.model_queues[model].qsize()) for model in available_models]
        sorted_models = sorted(model_queue_sizes, key=lambda x: x[1])

        for model, _ in sorted_models:
            queue = self.model_queues[model]
            if not queue.full():
                logger.debug(f"Выбрана модель {model}, очередь: {queue.qsize()}")
                return await self._enqueue_request(model, messages, max_tokens, session)

        logger.warning(f"Все очереди для {model_type} заполнены")
        tasks = [asyncio.create_task(queue.join()) for model, queue in [(m[0], self.model_queues[m[0]]) for m in sorted_models]]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()

        for model, _ in sorted_models:
            if not self.model_queues[model].full():
                logger.debug(f"Повторная попытка с {model}")
                return await self._enqueue_request(model, messages, max_tokens, session)

        logger.error(f"Не удалось распределить запрос для {model_type}")
        return None

    @backoff.on_exception(
        backoff.expo,
        (ProviderNotFoundError, ModelNotSupportedError, ResponseError, RateLimitError, ConnectionError, TimeoutError),
        max_tries=5,
        max_time=60,
        factor=2,
        jitter=backoff.full_jitter
    )
    async def _try_generate_response(self, messages: List[Dict], has_image: bool, max_tokens: int, user_id: str, channel_type: str, channel_id: str) -> Optional[str]:
        """Попытка генерации ответа."""
        model_type = "vision" if has_image else "text"
        available_models = [m for m in self.models[model_type] if m not in self.models["unavailable"][model_type]]
        
        if not available_models:
            logger.error(f"Нет моделей для {model_type}")
            return None

        model_stats = self.models["model_stats"][model_type]
        sorted_models = sorted(
            available_models,
            key=lambda m: model_stats.get(m, {"success": 0, "failure": 0})["success"],
            reverse=True
        )

        cache_key = self._generate_cache_key(messages, model_type, user_id, channel_type, channel_id)
        try:
            cached_response = await self.firebase_manager.load_cache(user_id, channel_type, channel_id, cache_key)
            if cached_response and cached_response.get("timestamp", 0) + self.cache_limits["cache_ttl_seconds"] > time.time():
                logger.debug(f"Ответ из кэша для {cache_key}")
                return cached_response["response"]
        except Exception as e:
            logger.error(f"Ошибка чтения кэша: {e}")

        timeout = ClientTimeout(total=60)
        headers = {"User-Agent": "BotClient/1.0 (DiscordBot; PollinationsAI)"}
        async with ClientSession(timeout=timeout, headers=headers) as session:
            for selected_model in sorted_models:
                logger.debug(f"Попытка с моделью {selected_model}")
                
                for attempt in range(self.request_settings["max_retries"]):
                    try:
                        response_text = await self._enqueue_request(selected_model, messages, max_tokens, session)
                        if not response_text:
                            logger.warning(f"Пустой ответ от {selected_model}, попытка {attempt + 1}")
                            raise ValueError("Empty response")

                        self.models["model_stats"][model_type][selected_model]["success"] += 1
                        self.models["last_successful"][model_type] = selected_model
                        await self.firebase_manager.save_models({"timestamp": time.time(), **self.models})

                        try:
                            await self.firebase_manager.save_cache(user_id, channel_type, channel_id, cache_key, {
                                "response": response_text,
                                "timestamp": time.time()
                            })
                            logger.debug(f"Ответ сохранен в кэш: {cache_key}")
                        except Exception as e:
                            logger.error(f"Ошибка сохранения кэша: {e}")

                        return response_text

                    except ResponseStatusError as e:
                        if e.status >= 500 and attempt < self.request_settings["max_retries"] - 1:
                            logger.warning(f"Серверная ошибка {e.status}, попытка {attempt + 1}")
                            await asyncio.sleep(self.request_settings["retry_delay_base"])
                        else:
                            logger.error(f"Ошибка {selected_model} после {attempt + 1} попыток: {e}")
                            break

                    except (ProviderNotFoundError, ModelNotSupportedError, ResponseError, RateLimitError, ConnectionError, TimeoutError) as e:
                        logger.error(f"Ошибка G4F для {selected_model}, попытка {attempt + 1}: {e}")
                        if attempt < self.request_settings["max_retries"] - 1:
                            await asyncio.sleep(self.request_settings["retry_delay_base"])
                        else:
                            break

                    except Exception as e:
                        logger.error(f"Неизвестная ошибка для {selected_model}: {e}")
                        break

                logger.error(f"Все попытки для {selected_model} провалились")
                self.models["model_stats"][model_type][selected_model]["failure"] += 1
                self.models["unavailable"][model_type].append(selected_model)
                await self.firebase_manager.save_models({"timestamp": time.time(), **self.models})
                await asyncio.sleep(3)

            response_text = await self._distribute_request(model_type, messages, max_tokens, session)
            if response_text:
                last_model = self.models["last_successful"][model_type] or sorted_models[0]
                self.models["model_stats"][model_type][last_model]["success"] += 1
                await self.firebase_manager.save_models({"timestamp": time.time(), **self.models})
                try:
                    await self.firebase_manager.save_cache(user_id, channel_type, channel_id, cache_key, {
                        "response": response_text,
                        "timestamp": time.time()
                    })
                except Exception as e:
                    logger.error(f"Ошибка сохранения кэша: {e}")
                return response_text

        logger.error(f"Все модели ({model_type}) не смогли обработать запрос")
        return None

    async def get_context(self, user_id: str, channel: discord.abc.Messageable) -> List[Dict]:
        """Получение контекста разговора."""
        conversation_id = self.current_conversation[user_id]["id"]
        
        if conversation_id in self.chat_memory and self.chat_memory[conversation_id]:
            messages = self.chat_memory[conversation_id]
            context = [{"role": msg["role"], "content": msg["content"]} for msg in messages[-self.cache_limits["messages"]:] if msg["content"]]
            logger.debug(f"Контекст из памяти: {len(context)} сообщений")
            return context

        try:
            conversation_data = await self.firebase_manager.load_conversation(user_id, conversation_id)
            if conversation_data:
                self.chat_memory[conversation_id] = conversation_data.get("messages", [])
                self.topic_memory[conversation_id] = conversation_data.get("topics", [])
                context = [{"role": msg["role"], "content": msg["content"]} for msg in self.chat_memory[conversation_id][-self.cache_limits["messages"]:] if msg["content"]]
                logger.debug(f"Контекст из Firebase: {len(context)} сообщений")
                return context
        except Exception as e:
            logger.error(f"Ошибка загрузки контекста: {e}")
            return []

    async def _build_system_prompt(self, has_image: bool) -> str:
        """Построение системного промпта."""
        try:
            current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            prompt = f"[PERSONALITY]\n{(DEFAULT_VISION_PROMPT if has_image else DEFAULT_PROMPT).format(now=current_date)}\n[INSTRUCTIONS]\n{'Analyze image.' if has_image else 'Respond.'}"
            logger.debug(f"Сформирован промпт ({'vision' if has_image else 'text'})")
            return prompt
        except Exception as e:
            logger.error(f"Ошибка построения промпта: {e}")
            return f"[PERSONALITY]\n{(DEFAULT_VISION_PROMPT if has_image else DEFAULT_PROMPT).format(now=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'))}\n[INSTRUCTIONS]\n{'Analyze image.' if has_image else 'Respond.'}"

    def _adjust_conversation_ttl(self, user_id: str) -> None:
        """Настройка TTL разговора."""
        conversation = self.current_conversation[user_id]
        request_count = conversation["request_count"]
        
        if request_count < 5:
            conversation["ttl_seconds"] = self.cache_limits["min_conversation_ttl"]
        elif request_count < 20:
            conversation["ttl_seconds"] = 86400
        else:
            conversation["ttl_seconds"] = self.cache_limits["max_conversation_ttl"]
        
        logger.debug(f"TTL для {user_id}: {conversation['ttl_seconds']} секунд")

    async def start_new_conversation(self, user_id: str, channel_id: str, content: str) -> None:
        """Запуск новой беседы."""
        conversation = self.current_conversation[user_id]
        conversation_id = conversation["id"]
        current_time = time.time()

        if (current_time - conversation["last_message_time"]) > conversation["ttl_seconds"]:
            logger.debug(f"Разговор {conversation_id} истек")
            conversation_id = str(uuid.uuid4())
            self.current_conversation[user_id] = {
                "id": conversation_id,
                "last_message_time": current_time,
                "request_count": 0,
                "ttl_seconds": 86400
            }
            self.chat_memory[conversation_id] = []
            self.topic_memory[conversation_id] = []
        
        conversation = self.current_conversation[user_id]
        conversation["last_message_time"] = current_time
        conversation["request_count"] += 1
        
        self._adjust_conversation_ttl(user_id)
        self.chat_memory[conversation_id].append({"role": "user", "content": content})
        if len(self.chat_memory[conversation_id]) > self.cache_limits["messages"]:
            self.chat_memory[conversation_id] = self.chat_memory[conversation_id][-self.cache_limits["messages"]:]

        await self._save_conversation(user_id, conversation_id)

    async def _save_conversation(self, user_id: str, conversation_id: str) -> None:
        """Сохранение разговора."""
        try:
            conversation_data = {
                "messages": self.chat_memory[conversation_id],
                "topics": self.topic_memory[conversation_id],
                "last_message_time": self.current_conversation[user_id]["last_message_time"],
                "ttl_seconds": self.current_conversation[user_id]["ttl_seconds"]
            }
            await self.firebase_manager.save_conversation(user_id, conversation_id, conversation_data)
            logger.debug(f"Разговор {conversation_id} сохранен")
        except Exception as e:
            logger.error(f"Ошибка сохранения разговора: {e}")

    async def cleanup_conversations_periodically(self) -> None:
        """Очистка устаревших разговоров."""
        logger.debug("Запуск cleanup_conversations_periodically")
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
                        logger.debug(f"Разговор {conversation_id} удален")
                
                for user_id in expired_users:
                    del self.current_conversation[user_id]
                
                await self.firebase_manager.cleanup_expired_conversations(current_time)
                logger.debug(f"Очищено {len(expired_users)} разговоров")
                
                await asyncio.sleep(3600)
            except Exception as e:
                logger.error(f"Ошибка очистки: {e}")
                await asyncio.sleep(1800)

    async def _send_temp_message(self, channel: discord.abc.Messageable, content: str, user_id: str, ephemeral: bool = False) -> None:
        """Отправка временного сообщения."""
        try:
            if ephemeral and isinstance(channel, discord.TextChannel):
                await channel.send(content, delete_after=10)
            else:
                msg = await channel.send(content)
                await asyncio.sleep(10)
                await msg.delete()
        except (Forbidden, HTTPException) as e:
            logger.error(f"Ошибка отправки сообщения для {user_id}: {e}")