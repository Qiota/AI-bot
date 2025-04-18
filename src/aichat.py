import discord
from discord import app_commands, Forbidden, HTTPException
import asyncio
from typing import List, Dict, Optional, DefaultDict, Set
from g4f.client import AsyncClient as G4FClient
from g4f.Provider import PollinationsAI
from .systemLog import logger
import time
from collections import defaultdict
import uuid
from .commands.prompt import load_user_prompt, default_prompt, create_command as prompt_command
from .commands.restrict import check_user_restriction, check_bot_access, create_command as restrict_command
from .commands.giveaway import create_command as giveaway_command  # Добавляем импорт для розыгрышей
from .firebase.firebase_manager import FirebaseManager
import aiohttp
import json
import hashlib
import backoff
from aiohttp import ClientSession, ClientTimeout
from g4f.errors import ProviderNotFoundError, ModelNotSupportedError, ResponseError, RateLimitError
import g4f

# Безопасная проверка версии g4f
version = getattr(g4f, "__version__", "неизвестна")
logger.info(f"Используемая версия g4f: {version}")

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
        self.giveaways: Dict = {}  # Добавляем для активных розыгрышей
        self.completed_giveaways: Dict = {}  # Добавляем для завершённых розыгрышей
        self.models: Dict[str, List[str] | float | Dict[str, List[str]] | Dict[str, Dict[str, int]] | None] = {
            "text": [],  # Заполняется через API или Firebase
            "vision": [],  # Заполняется через API или Firebase
            "last_update": None,
            "unavailable": {"text": [], "vision": []},
            "last_successful": {"text": None, "vision": None},
            "model_stats": {"text": {}, "vision": {}}  # Статистика успешных/неудачных запросов
        }
        self.models_loaded: bool = False  # Флаг для отслеживания загрузки моделей
        self.prompt_cache: Dict = {}
        self.chat_memory: DefaultDict[str, List[Dict]] = defaultdict(list)
        self.topic_memory: DefaultDict[str, List[str]] = defaultdict(list)
        self.current_conversation: DefaultDict[str, Dict] = defaultdict(lambda: {
            "id": str(uuid.uuid4()),
            "last_message_time": time.time(),
            "request_count": 0,  # Для отслеживания активности пользователя
            "ttl_seconds": 86400  # Начальный TTL: 24 часа
        })
        self.processed_messages: Set[str] = set()
        self.message_to_response: Dict[str | int, int] = {}
        self.user_settings: DefaultDict[str, Dict[str, int]] = defaultdict(lambda: {"max_response_length": 2000})
        self.last_message_time: DefaultDict[str, float] = defaultdict(float)
        self._initialize_settings()
        self.tree.add_command(prompt_command(self))
        self.tree.add_command(restrict_command(self))
        self.bot.event(self.on_ready)
        self.bot.setup_hook = self._setup_hook  # Регистрируем асинхронный хук

    async def _setup_hook(self):
        """Асинхронный хук для инициализации Firebase и запуска задач."""
        logger.debug("Начало setup_hook")
        await self._ensure_firebase_initialized()
        logger.debug("Firebase инициализирован, запуск асинхронных задач")
        # Добавляем команды розыгрышей
        giveaway, reroll, edit = giveaway_command(self)
        self.tree.add_command(giveaway)
        self.tree.add_command(reroll)
        self.tree.add_command(edit)
        logger.debug("Команды розыгрышей добавлены в CommandTree")
        asyncio.create_task(self.update_models_periodically())
        asyncio.create_task(self.cleanup_conversations_periodically())
        # Возобновляем розыгрыши
        from .commands.giveaway import resume_giveaways
        asyncio.create_task(resume_giveaways(self))
        logger.info("Асинхронные задачи запущены в setup_hook")

    async def _ensure_firebase_initialized(self) -> FirebaseManager:
        """Гарантирует инициализацию Firebase и возвращает экземпляр FirebaseManager."""
        if not self.firebase_manager:
            try:
                logger.debug("Начало инициализации Firebase")
                self.firebase_manager = await FirebaseManager.initialize()
                logger.debug("Firebase инициализирован")
            except Exception as e:
                logger.error(f"Ошибка инициализации Firebase в BotClient: {e}")
                raise
        return self.firebase_manager

    async def on_ready(self) -> None:
        """Обработчик события, вызываемого после полной инициализации бота."""
        logger.info(f"Бот {self.bot.user} готов к работе")
        try:
            await self.fetch_available_models()
            if not self.models["text"] or not self.models["vision"]:
                logger.error("Не удалось загрузить модели при старте: text или vision список пуст")
                self.models_loaded = False
            else:
                self.models_loaded = True
                logger.info("Модели успешно загружены при старте бота")
        except Exception as e:
            logger.error(f"Критическая ошибка загрузки моделей при старте бота: {e}")
            self.models_loaded = False

    async def close(self) -> None:
        """Закрытие клиента Discord."""
        try:
            await self.bot.close()
            logger.info("Клиент Discord закрыт")
        except Exception as e:
            logger.error(f"Ошибка закрытия ресурсов: {e}")

    def _setup_intents(self) -> discord.Intents:
        """Настройка намерений (intents) для Discord API."""
        intents = discord.Intents.default()
        intents.message_content = intents.dm_messages = intents.members = True
        return intents

    def _initialize_settings(self) -> None:
        """Инициализация настроек бота."""
        self.cache_limits: Dict[str, int] = {
            "messages": 25,  # Максимум сообщений в памяти
            "topics": 2,
            "memory_days": 1,  # Устаревший параметр, заменён на ttl_seconds
            "cache_ttl_seconds": 3600,  # Базовый TTL для кэша ответов (1 час)
            "models_ttl_seconds": 86400,  # TTL для кэша моделей (24 часа)
            "max_conversation_ttl": 604800,  # Максимальный TTL: 7 дней
            "min_conversation_ttl": 3600  # Минимальный TTL: 1 час
        }
        self.request_settings: Dict[str, float | int] = {
            "rate_limit_delay": 3.0,
            "max_retries": 3,
            "retry_delay_base": 5.0
        }
        self.spam_cooldown: float = 3.0

    async def check_spam(self, user_id: str) -> bool:
        """Проверка на спам от пользователя."""
        current_time = time.time()
        last_time = self.last_message_time[user_id]
        if current_time - last_time < self.spam_cooldown:
            return False
        self.last_message_time[user_id] = current_time
        return True

    async def is_bot_mentioned(self, message: discord.Message) -> bool:
        """Проверка, упомянут ли бот в сообщении."""
        if isinstance(message.channel, discord.DMChannel):
            return True
        return self.bot.user in message.mentions or f"<@{self.bot.user.id}>" in message.content

    async def update_models_periodically(self) -> None:
        """Периодическое обновление списка доступных моделей."""
        logger.debug("Начало выполнения update_models_periodically")
        while True:
            try:
                if not self.models["last_update"] or (time.time() - self.models["last_update"]) > self.cache_limits["models_ttl_seconds"]:
                    await self.fetch_available_models()
                    if not self.models["text"] or not self.models["vision"]:
                        logger.error("Периодическое обновление не загрузило модели: text или vision список пуст")
                        self.models_loaded = False
                    else:
                        self.models_loaded = True
                        logger.info("Модели успешно обновлены в периодическом обновлении")
                await asyncio.sleep(3600)
            except Exception as e:
                logger.error(f"Ошибка периодического обновления моделей: {e}")
                self.models_loaded = False
                await asyncio.sleep(1800)

    async def fetch_available_models(self) -> None:
        """Загрузка доступных моделей из API Pollinations и сохранение в Firebase, затем чтение из Firebase."""
        logger.debug("Начало fetch_available_models")
        vision_models = []
        text_models = []

        try:
            logger.debug("Попытка загрузки моделей из Pollinations API")
            async with aiohttp.ClientSession() as session:
                async with session.get("https://text.pollinations.ai/models", timeout=10) as response:
                    if response.status == 200:
                        models_data = await response.json()
                        logger.debug(f"Ответ API Pollinations: {json.dumps(models_data, indent=2)}")
                        vision_models = [
                            m.get("name") for m in models_data
                            if isinstance(m, dict) and m.get("vision", False) and m.get("name")
                        ]
                        text_models = [
                            m.get("name") for m in models_data
                            if isinstance(m, dict) and not m.get("vision", False) and m.get("name")
                        ]
                        if not vision_models:
                            logger.warning("API Pollinations не вернул vision моделей")
                        else:
                            logger.debug(f"Vision модели из API: {vision_models}")
                        if not text_models:
                            logger.warning("API Pollinations не вернул текстовых моделей")
                        else:
                            logger.debug(f"Text модели из API: {text_models}")
                    else:
                        logger.warning(f"Ошибка API Pollinations: статус {response.status}")
        except Exception as e:
            logger.error(f"Ошибка загрузки моделей из Pollinations API: {e}")

        try:
            logger.debug("Перед сохранением моделей в Firebase")
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
            await self.firebase_manager.save_models({
                **self.models,
                "timestamp": self.models["last_update"]
            })
            logger.info(f"Модели сохранены в Firebase: Text моделей = {len(self.models['text'])}, Vision моделей = {len(self.models['vision'])}")
        except Exception as e:
            logger.error(f"Ошибка сохранения моделей в Firebase: {e}")
            raise

        try:
            logger.debug("Чтение моделей из Firebase")
            loaded_models = await self.firebase_manager.load_models()
            logger.debug(f"Модели из Firebase: {loaded_models}")
            if isinstance(loaded_models, dict) and loaded_models.get("timestamp", 0) + self.cache_limits["models_ttl_seconds"] > time.time():
                self.models.update(loaded_models)
                self.models["vision"] = loaded_models.get("vision", [])
                self.models["text"] = loaded_models.get("text", [])
                self.models["unavailable"]["vision"] = loaded_models.get("unavailable", {}).get("vision", [])
                self.models["unavailable"]["text"] = loaded_models.get("unavailable", {}).get("text", [])
                self.models["model_stats"]["vision"] = loaded_models.get("model_stats", {}).get("vision", {})
                self.models["model_stats"]["text"] = loaded_models.get("model_stats", {}).get("text", {})
                for key in ["text", "vision"]:
                    if isinstance(self.models["unavailable"].get(key), set):
                        self.models["unavailable"][key] = list(self.models["unavailable"][key])
                for model in self.models["vision"]:
                    if model not in self.models["model_stats"]["vision"]:
                        self.models["model_stats"]["vision"][model] = {"success": 0, "failure": 0}
                for model in self.models["text"]:
                    if model not in self.models["model_stats"]["text"]:
                        self.models["model_stats"]["text"][model] = {"success": 0, "failure": 0}
                logger.info(f"Модели загружены из Firebase: Text моделей = {len(self.models['text'])}, Vision моделей = {len(self.models['vision'])}")
            else:
                logger.warning("Некорректные или устаревшие данные моделей из Firebase, списки остаются пустыми")
                self.models["vision"] = []
                self.models["text"] = []
                self.models["unavailable"]["vision"] = []
                self.models["unavailable"]["text"] = []
                self.models["last_update"] = time.time()
        except Exception as e:
            logger.error(f"Ошибка чтения моделей из Firebase: {e}")
            raise

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
                logger.warning(f"Модели ещё не загружены, сообщение от {user_id} пропущено")
                await self._send_temp_message(message.channel, "Бот ещё инициализируется, попробуйте снова через несколько секунд.", user_id)
                return

            if not await self.check_spam(user_id):
                await self._send_temp_message(message.channel, "Слишком быстро! Подождите 3 секунды.", user_id)
                return

            await self.start_new_conversation(user_id, channel_id, message.content)
            if isinstance(message.channel, discord.DMChannel):
                result, reason = await check_user_restriction(message)
                if result:
                    await self._process_message(message)
                else:
                    await self._send_temp_message(message.channel, f"Ошибка: {reason}", user_id, ephemeral=True)
            else:
                access_result, access_reason = await check_bot_access(message)
                restriction_result, restriction_reason = await check_user_restriction(message)
                if access_result and restriction_result:
                    await self._process_message(message)
                else:
                    reason = access_reason if not access_result else restriction_reason
                    await self._send_temp_message(message.channel, f"Ошибка: {reason}", user_id, ephemeral=True)
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения {msg_key}: {e}")
            await self._send_temp_message(message.channel, "Ошибка обработки сообщения.", user_id, ephemeral=True)

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
                logger.warning(f"Модели ещё не загружены, редактирование от {user_id} пропущено")
                await self._send_temp_message(after.channel, "Бот ещё инициализируется, попробуйте снова через несколько секунд.", user_id)
                return

            if not await self.check_spam(user_id):
                await self._send_temp_message(after.channel, "Слишком быстро! Подождите 3 секунды.", user_id)
                return

            await self.start_new_conversation(user_id, channel_id, after.content)
            if isinstance(after.channel, discord.DMChannel):
                result, reason = await check_user_restriction(after)
                if result:
                    await self._process_edit(after)
                else:
                    await self._send_temp_message(after.channel, f"Ошибка: {reason}", user_id, ephemeral=True)
            else:
                access_result, access_reason = await check_bot_access(after)
                restriction_result, restriction_reason = await check_user_restriction(after)
                if access_result and restriction_result:
                    await self._process_edit(after)
                else:
                    reason = access_reason if not access_result else restriction_reason
                    await self._send_temp_message(after.channel, f"Ошибка: {reason}", user_id, ephemeral=True)
        except Exception as e:
            logger.error(f"Ошибка обработки редактирования {msg_key}: {e}")
            await self._send_temp_message(after.channel, "Ошибка обработки редактирования.", user_id, ephemeral=True)

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
            logger.info(f"Сообщение {after.id}-{after.channel.id} уже имеет ответ, редактирование игнорируется")
            return
        
        async with after.channel.typing():
            text = after.content.replace(f"<@{self.bot.user.id}>", "").strip()
            parts = await self.generate_response(str(after.author.id), str(after.id), text, after, is_edit=True)
            if parts:
                await self._send_split_message(after, parts)

    def _split_response(self, response: str, max_length: int = 2000) -> List[str]:
        """Разделение длинного ответа на части, не превышающие max_length."""
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
        """Отправка частей ответа пользователю."""
        for i, part in enumerate(parts):
            try:
                logger.debug(f"Отправка части {i+1}/{len(parts)} сообщения {message.id}: {len(part)} символов")
                sent_msg = await (message.reply(part) if i == 0 else message.channel.send(part))
                self.message_to_response[f"{message.id}_{i}" if i > 0 else message.id] = sent_msg.id
                # Сохраняем ответ бота в контекст разговора
                conversation_id = self.current_conversation[str(message.author.id)]["id"]
                self.chat_memory[conversation_id].append({"role": "assistant", "content": part})
                await self._save_conversation(str(message.author.id), conversation_id)
            except (Forbidden, HTTPException) as e:
                logger.error(f"Ошибка отправки части сообщения {i+1}: {e}")
                await self._send_temp_message(message.channel, "Ошибка отправки.", str(message.author.id), ephemeral=True)

    async def generate_response(self, user_id: str, message_id: str, text: str, message: discord.Message, is_edit: bool = False) -> Optional[List[str]]:
        """Генерация ответа с учетом текста и изображений, с разделением обязанностей моделей."""
        try:
            if not (text or message.attachments):
                return ["Введите текст или прикрепите изображение."]
            
            context = await self.get_context(user_id, message.channel)
            guild_id = str(message.guild.id) if message.guild else "DM"
            system_prompt = await self._build_system_prompt(user_id, guild_id, message.attachments)
            
            attachments = [
                a.url for a in message.attachments
                if a.content_type and a.content_type.startswith("image/")
            ]
            user_content = [{"type": "text", "text": text}] if text else []
            user_content.extend({"type": "image_url", "image_url": {"url": url}} for url in attachments)
            messages = context + [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}]
            
            final_response = None

            # Если есть изображение, используем vision модель
            if attachments:
                if not self.models["vision"]:
                    logger.error(f"Список vision моделей пуст для пользователя {user_id}: {self.models['vision']}")
                    return ["Ошибка: vision модели недоступны. Пожалуйста, обратитесь к администратору."]
                logger.debug(f"Обработка запроса с изображением (текст: {'есть' if text else 'нет'}) с использованием vision модели")
                vision_response = await self._try_generate_response(messages, has_image=True, max_tokens=2000)
                if not vision_response:
                    return ["Не удалось обработать изображение. Попробуйте снова."]
                logger.debug(f"Vision модель вернула: {vision_response}")
                final_response = vision_response
            # Если есть только текст, используем текстовую модель
            else:
                if not self.models["text"]:
                    logger.error(f"Список текстовых моделей пуст для пользователя {user_id}: {self.models['text']}")
                    return ["Ошибка: текстовые модели недоступны. Пожалуйста, обратитесь к администратору."]
                logger.debug(f"Обработка текстового запроса: {text} с использованием текстовой модели")
                text_response = await self._try_generate_response(messages, has_image=False, max_tokens=2000)
                if not text_response:
                    return ["Не удалось обработать текстовый запрос. Попробуйте снова."]
                logger.debug(f"Текстовая модель вернула: {text_response}")
                final_response = text_response

            # Разбиваем ответ на части, если он слишком длинный
            return self._split_response(final_response, self.user_settings[user_id]["max_response_length"])
        except Exception as e:
            logger.error(f"Ошибка генерации ответа для сообщения {message_id}: {e}")
            return ["Произошла ошибка при генерации ответа."]

    def _generate_cache_key(self, messages: List[Dict], model_type: str) -> str:
        """Генерация уникального ключа кэша на основе сообщений и типа модели."""
        message_data = json.dumps(messages, sort_keys=True)
        return f"{model_type}:{hashlib.sha256(message_data.encode()).hexdigest()}"

    @backoff.on_exception(
        backoff.expo,
        (ProviderNotFoundError, ModelNotSupportedError, ResponseError, RateLimitError, ConnectionError, TimeoutError),
        max_tries=5,
        max_time=60,
        factor=2,
        jitter=backoff.full_jitter
    )
    async def _try_generate_response(self, messages: List[Dict], has_image: bool, max_tokens: int) -> Optional[str]:
        """Попытка генерации ответа с использованием подходящей модели и кэшированием в Firebase."""
        model_type = "vision" if has_image else "text"
        available_models = [m for m in self.models[model_type] if m not in self.models["unavailable"][model_type]]
        
        if not available_models:
            logger.error(f"Нет доступных моделей для типа {model_type}")
            return None

        # Выбираем модель с наибольшим количеством успешных запросов
        model_stats = self.models["model_stats"][model_type]
        sorted_models = sorted(
            available_models,
            key=lambda m: model_stats.get(m, {"success": 0, "failure": 0})["success"],
            reverse=True
        )
        selected_model = sorted_models[0]
        logger.debug(f"Выбрана модель {selected_model} для типа {model_type}")

        # Проверка кэша в Firebase
        cache_key = self._generate_cache_key(messages, model_type)
        try:
            cached_response = await self.firebase_manager.load_cache(cache_key)
            if cached_response and cached_response.get("timestamp", 0) + self.cache_limits["cache_ttl_seconds"] > time.time():
                logger.debug(f"Ответ найден в кэше для {cache_key}")
                return cached_response["response"]
            else:
                logger.debug(f"Кэш для {cache_key} истёк или отсутствует")
        except Exception as e:
            logger.error(f"Ошибка чтения кэша для {cache_key}: {e}")

        try:
            # Настройка таймаута и заголовков для веб-поиска
            timeout = ClientTimeout(total=30)
            headers = {"User-Agent": "BotClient/1.0 (DiscordBot; PollinationsAI)"}

            async with ClientSession(timeout=timeout, headers=headers) as session:
                # Выполняем запрос к G4F с веб-поиском
                response = await self.g4f_client.chat.completions.create(
                    model=selected_model,
                    messages=messages,
                    max_tokens=max_tokens,
                    web_search=True,
                    session=session
                )
                response_text = response.choices[0].message.content.strip()

                if not response_text:
                    logger.warning(f"Пустой ответ от модели {selected_model}")
                    self.models["model_stats"][model_type][selected_model]["failure"] += 1
                    return None

                logger.debug(f"Успешный ответ от модели {selected_model}: {len(response_text)} символов")
                self.models["model_stats"][model_type][selected_model]["success"] += 1
                self.models["last_successful"][model_type] = selected_model
                await self.firebase_manager.save_models({
                    **self.models,
                    "timestamp": time.time()
                })

                # Сохранение ответа в кэш
                try:
                    await self.firebase_manager.save_cache(cache_key, {
                        "response": response_text,
                        "timestamp": time.time()
                    })
                    logger.debug(f"Ответ сохранён в кэш для {cache_key}")
                except Exception as e:
                    logger.error(f"Ошибка сохранения кэша для {cache_key}: {e}")

                return response_text

        except (ProviderNotFoundError, ModelNotSupportedError) as e:
            logger.error(f"Ошибка конфигурации G4F для модели {selected_model}: {e}")
            self.models["model_stats"][model_type][selected_model]["failure"] += 1
            self.models["unavailable"][model_type].append(selected_model)
            await self.firebase_manager.save_models({
                **self.models,
                "timestamp": time.time()
            })
            raise

        except (ResponseError, RateLimitError) as e:
            logger.error(f"Ошибка ответа G4F для модели {selected_model}: {e}")
            self.models["model_stats"][model_type][selected_model]["failure"] += 1
            await self.firebase_manager.save_models({
                **self.models,
                "timestamp": time.time()
            })
            raise

        except (ConnectionError, TimeoutError) as e:
            logger.error(f"Сетевая ошибка для модели {selected_model}: {e}")
            self.models["model_stats"][model_type][selected_model]["failure"] += 1
            await self.firebase_manager.save_models({
                **self.models,
                "timestamp": time.time()
            })
            raise

        except Exception as e:
            logger.error(f"Неизвестная ошибка для модели {selected_model}: {e.__class__.__name__}: {str(e)}")
            self.models["model_stats"][model_type][selected_model]["failure"] += 1
            await self.firebase_manager.save_models({
                **self.models,
                "timestamp": time.time()
            })
            return None

    async def get_context(self, user_id: str, channel: discord.abc.Messageable) -> List[Dict]:
        """Получение контекста разговора из памяти или Firebase."""
        conversation_id = self.current_conversation[user_id]["id"]
        
        # Проверка локальной памяти
        if conversation_id in self.chat_memory and self.chat_memory[conversation_id]:
            messages = self.chat_memory[conversation_id]
            context = [
                {"role": msg["role"], "content": msg["content"]}
                for msg in messages[-self.cache_limits["messages"]:]
                if msg["content"]
            ]
            logger.debug(f"Контекст загружен из локальной памяти для {conversation_id}: {len(context)} сообщений")
            return context

        # Загрузка из Firebase, если локальная память пуста
        try:
            conversation_data = await self.firebase_manager.load_conversation(user_id, conversation_id)
            if conversation_data:
                self.chat_memory[conversation_id] = conversation_data.get("messages", [])
                self.topic_memory[conversation_id] = conversation_data.get("topics", [])
                context = [
                    {"role": msg["role"], "content": msg["content"]}
                    for msg in self.chat_memory[conversation_id][-self.cache_limits["messages"]:]
                    if msg["content"]
                ]
                logger.debug(f"Контекст загружен из Firebase для {conversation_id}: {len(context)} сообщений")
                return context
        except Exception as e:
            logger.error(f"Ошибка загрузки контекста из Firebase для {conversation_id}: {e}")
            return []

    async def _build_system_prompt(self, user_id: str, guild_id: str, attachments: List) -> str:
        """Построение системного промпта с учетом пользовательских настроек."""
        prompt_key = f"{user_id}-{guild_id}"
        if prompt_key not in self.prompt_cache:
            self.prompt_cache[prompt_key] = await load_user_prompt(user_id, guild_id, self) or default_prompt
        base_prompt = self.prompt_cache[prompt_key]
        if attachments:
            base_prompt += "\n\nПожалуйста, проанализируйте прикреплённое изображение и дайте соответствующий ответ."
        return base_prompt

    def _adjust_conversation_ttl(self, user_id: str) -> None:
        """Динамическая настройка TTL разговора на основе активности пользователя."""
        conversation = self.current_conversation[user_id]
        request_count = conversation["request_count"]
        
        # Логика настройки TTL:
        # - Менее 5 запросов за сессию: минимальный TTL (1 час)
        # - 5-20 запросов: средний TTL (24 часа)
        # - Более 20 запросов: максимальный TTL (7 дней)
        if request_count < 5:
            conversation["ttl_seconds"] = self.cache_limits["min_conversation_ttl"]
        elif request_count < 20:
            conversation["ttl_seconds"] = 86400  # 24 часа
        else:
            conversation["ttl_seconds"] = self.cache_limits["max_conversation_ttl"]
        
        logger.debug(f"TTL для пользователя {user_id} установлен на {conversation['ttl_seconds']} секунд (запросов: {request_count})")

    async def start_new_conversation(self, user_id: str, channel_id: str, content: str) -> None:
        """Запуск новой беседы или продолжение существующей с динамическим TTL."""
        conversation = self.current_conversation[user_id]
        conversation_id = conversation["id"]
        current_time = time.time()

        # Проверка, истёк ли текущий разговор
        if (current_time - conversation["last_message_time"]) > conversation["ttl_seconds"]:
            logger.debug(f"Разговор {conversation_id} для пользователя {user_id} истёк, создание нового")
            conversation_id = str(uuid.uuid4())
            self.current_conversation[user_id] = {
                "id": conversation_id,
                "last_message_time": current_time,
                "request_count": 0,
                "ttl_seconds": 86400  # Начальный TTL: 24 часа
            }
            self.chat_memory[conversation_id] = []
            self.topic_memory[conversation_id] = []
        
        # Обновление времени и счётчика запросов
        conversation = self.current_conversation[user_id]
        conversation["last_message_time"] = current_time
        conversation["request_count"] += 1
        
        # Динамическая настройка TTL
        self._adjust_conversation_ttl(user_id)

        # Добавление сообщения пользователя
        self.chat_memory[conversation_id].append({"role": "user", "content": content})
        if len(self.chat_memory[conversation_id]) > self.cache_limits["messages"]:
            self.chat_memory[conversation_id] = self.chat_memory[conversation_id][-self.cache_limits["messages"]:]

        # Сохранение в Firebase
        await self._save_conversation(user_id, conversation_id)

    async def _save_conversation(self, user_id: str, conversation_id: str) -> None:
        """Сохранение контекста разговора в Firebase."""
        try:
            conversation_data = {
                "messages": self.chat_memory[conversation_id],
                "topics": self.topic_memory[conversation_id],
                "last_message_time": self.current_conversation[user_id]["last_message_time"],
                "ttl_seconds": self.current_conversation[user_id]["ttl_seconds"]
            }
            await self.firebase_manager.save_conversation(user_id, conversation_id, conversation_data)
            logger.debug(f"Разговор {conversation_id} сохранён в Firebase для пользователя {user_id}")
        except Exception as e:
            logger.error(f"Ошибка сохранения разговора {conversation_id} в Firebase: {e}")

    async def cleanup_conversations_periodically(self) -> None:
        """Периодическая очистка устаревших разговоров."""
        logger.debug("Начало выполнения cleanup_conversations_periodically")
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
                        logger.debug(f"Разговор {conversation_id} для пользователя {user_id} удалён из памяти")
                
                for user_id in expired_users:
                    del self.current_conversation[user_id]
                
                # Очистка в Firebase
                logger.debug("Начало очистки устаревших разговоров в Firebase")
                await self.firebase_manager.cleanup_expired_conversations(current_time)
                logger.info(f"Очищено {len(expired_users)} устаревших разговоров")
                
                await asyncio.sleep(3600)  # Проверка каждый час
            except Exception as e:
                logger.error(f"Ошибка периодической очистки разговоров: {e}")
                await asyncio.sleep(1800)

    async def _send_temp_message(self, channel: discord.abc.Messageable, content: str, user_id: str, ephemeral: bool = False) -> None:
        """Отправка временного сообщения с удалением через 10 секунд."""
        try:
            if isinstance(channel, discord.TextChannel) and ephemeral:
                await channel.send(content, delete_after=10)
            else:
                msg = await channel.send(content)
                await asyncio.sleep(10)
                await msg.delete()
        except (Forbidden, HTTPException) as e:
            logger.error(f"Ошибка отправки временного сообщения для {user_id}: {e}")