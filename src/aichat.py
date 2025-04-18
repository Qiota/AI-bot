import discord
from discord import app_commands, Forbidden, HTTPException
import asyncio
from typing import List, Dict, Optional
from g4f.client import Client as G4FClient
from g4f.Provider import PollinationsAI
from .systemLog import logger
import time
from collections import defaultdict
import uuid
from .commands.prompt import load_user_prompt, default_prompt, create_command as prompt_command
from .commands.restrict import check_user_restriction, check_bot_access, create_command as restrict_command
from .firebase.firebase_manager import FirebaseManager

class BotClient:
    def __init__(self, config):
        logger.info("Инициализация BotClient")
        self.config = config
        self.bot = discord.Client(intents=self._setup_intents())
        self.tree = app_commands.CommandTree(self.bot)
        self.g4f_client = G4FClient(provider=PollinationsAI)
        self.models = {
            "text": [
                "gpt-4o-mini", "gpt-4o", "o1-mini", "qwen-2.5-coder-32b",
                "llama-3.3-70b", "mistral-nemo", "llama-3.1-8b",
                "deepseek-r1", "phi-4", "qwq-32b", "deepseek-v3", "llama-3.2-11b"
            ],
            "vision": ["gpt-4o", "gpt-4o-mini", "o1-mini", "o3-mini"],
            "last_update": None,
            "unavailable": {"text": [], "vision": []},
            "last_successful": {"text": None, "vision": None}
        }
        self.prompt_cache = {}
        self.chat_memory = defaultdict(list)
        self.topic_memory = defaultdict(list)
        self.current_conversation = defaultdict(lambda: {"id": str(uuid.uuid4()), "last_message_time": time.time()})
        self.processed_messages = set()
        self.message_to_response = {}
        self.user_settings = defaultdict(lambda: {"max_response_length": 2000})
        self.last_message_time = defaultdict(float)
        self._initialize_settings()
        self.tree.add_command(prompt_command(self))
        self.tree.add_command(restrict_command(self))
        asyncio.create_task(self.update_models_periodically())

    async def close(self):
        try:
            await self.bot.close()
            logger.info("Клиент Discord закрыт")
        except Exception as e:
            logger.error(f"Ошибка закрытия ресурсов: {e}")

    def _setup_intents(self) -> discord.Intents:
        intents = discord.Intents.default()
        intents.message_content = intents.dm_messages = intents.members = True
        return intents

    def _initialize_settings(self):
        self.cache_limits = {"messages": 25, "topics": 2, "memory_days": 1}
        self.request_settings = {
            "rate_limit_delay": 3.0,
            "max_retries": 3,
            "retry_delay_base": 5.0
        }
        self.spam_cooldown = 3.0

    async def check_spam(self, user_id: str) -> bool:
        current_time = time.time()
        last_time = self.last_message_time[user_id]
        if current_time - last_time < self.spam_cooldown:
            return False
        self.last_message_time[user_id] = current_time
        return True

    async def is_bot_mentioned(self, message: discord.Message) -> bool:
        if isinstance(message.channel, discord.DMChannel):
            return True
        return self.bot.user in message.mentions or f"<@{self.bot.user.id}>" in message.content

    async def update_models_periodically(self):
        await self.fetch_available_models()
        while True:
            try:
                if not self.models["last_update"] or (time.time() - self.models["last_update"]) > 86400:
                    await self.fetch_available_models()
                await asyncio.sleep(3600)
            except Exception as e:
                logger.error(f"Ошибка обновления моделей: {e}")
                await asyncio.sleep(1800)

    async def fetch_available_models(self):
        try:
            FirebaseManager.initialize()
            loaded_models = FirebaseManager().load_models()
            if isinstance(loaded_models, dict):
                if "unavailable" in loaded_models and isinstance(loaded_models["unavailable"], dict):
                    for key in ["text", "vision"]:
                        if isinstance(loaded_models["unavailable"].get(key), set):
                            loaded_models["unavailable"][key] = list(loaded_models["unavailable"][key])
                self.models.update(loaded_models)
            self.models["last_update"] = time.time()
            FirebaseManager().save_models(self.models)
            logger.info(f"Модели загружены из Firebase: Text моделей = {len(self.models['text'])}, Vision моделей = {len(self.models['vision'])}")
        except Exception as e:
            logger.error(f"Ошибка загрузки/сохранения моделей: {e}")

    async def on_message(self, message: discord.Message):
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

            if not await self.check_spam(user_id):
                await self._send_temp_message(message.channel, "Слишком быстро! Подождите 3 секунды.", user_id)
                return

            await self.start_new_conversation(user_id, channel_id, message.content)
            if isinstance(message.channel, discord.DMChannel):
                if await check_user_restriction(message):
                    await self._process_message(message)
            elif await check_bot_access(message) and await check_user_restriction(message):
                await self._process_message(message)
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения {msg_key}: {e}")

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
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

            if not await self.check_spam(user_id):
                await self._send_temp_message(after.channel, "Слишком быстро! Подождите 3 секунды.", user_id)
                return

            await self.start_new_conversation(user_id, channel_id, after.content)
            if isinstance(after.channel, discord.DMChannel):
                if await check_user_restriction(after):
                    await self._process_edit(after)
            elif await check_bot_access(after) and await check_user_restriction(after):
                await self._process_edit(after)
        except Exception as e:
            logger.error(f"Ошибка обработки редактирования {msg_key}: {e}")

    async def _process_message(self, message: discord.Message):
        async with message.channel.typing():
            text = message.content.replace(f"<@{self.bot.user.id}>", "").strip()
            response = await self.generate_response(str(message.author.id), str(message.id), text, message)
            if response:
                await self._send_split_message(message, response)

    async def _process_edit(self, after: discord.Message):
        async with after.channel.typing():
            text = after.content.replace(f"<@{self.bot.user.id}>", "").strip()
            response = await self.generate_response(str(after.author.id), str(after.id), text, after, is_edit=True)
            if response and after.id in self.message_to_response:
                try:
                    response_msg = await after.channel.fetch_message(self.message_to_response[after.id])
                    if response_msg.author == self.bot.user:
                        await response_msg.edit(content=response)
                    else:
                        sent_msg = await after.reply(response)
                        self.message_to_response[after.id] = sent_msg.id
                except discord.NotFound:
                    sent_msg = await after.reply(response)
                    self.message_to_response[after.id] = sent_msg.id
            elif response:
                sent_msg = await after.reply(response)
                self.message_to_response[after.id] = sent_msg.id

    async def _send_split_message(self, message: discord.Message, response: str):
        max_length = 2000
        parts = [response[i:i + max_length] for i in range(0, len(response), max_length)]
        for i, part in enumerate(parts):
            try:
                sent_msg = await (message.reply(part) if i == 0 else message.channel.send(part))
                self.message_to_response[f"{message.id}_{i}" if i > 0 else message.id] = sent_msg.id
            except (Forbidden, HTTPException):
                await self._send_temp_message(message.channel, "Ошибка отправки.", str(message.author.id), ephemeral=True)

    async def generate_response(self, user_id: str, message_id: str, text: str, message: discord.Message, is_edit: bool = False) -> Optional[str]:
        try:
            if not (text or message.attachments):
                return "Введите текст или прикрепите изображение."
            context = await self.get_context(user_id, message.channel)
            guild_id = str(message.guild.id) if message.guild else "DM"
            system_prompt = await self._build_system_prompt(user_id, guild_id, message.attachments)
            user_content = [{"type": "text", "text": text}] if text else []
            attachments = [a.url for a in message.attachments if a.content_type and a.content_type.startswith("image/")]
            user_content.extend({"type": "image_url", "image_url": {"url": url}} for url in attachments)
            messages = context + [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}]
            needs_web = self.needs_web_search(text, context) or (bool(attachments) and self.needs_web_search_for_image(text, context))
            response_text = await self._try_generate_response(messages, bool(attachments), needs_web, 6000)
            if not response_text:
                return "Не удалось ответить. Попробуйте снова."
            final_response = response_text[:self.user_settings[user_id]["max_response_length"]]
            await self.add_to_memory(user_id, message_id, "user", text, message.author.name, message.channel.id, attachments[0] if attachments else None)
            await self.add_to_memory(user_id, f"{message_id}_resp", "assistant", final_response, self.bot.user.name, message.channel.id)
            return final_response
        except Exception as e:
            logger.error(f"Ошибка генерации для {user_id}: {e}")
            await self._send_temp_message(message.channel, "Ошибка. Попробуйте позже.", user_id, ephemeral=True)
            return None

    async def _build_system_prompt(self, user_id: str, guild_id: str, attachments: List) -> str:
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        user_prompt = await load_user_prompt(user_id, guild_id, self)
        base_prompt = default_prompt.format(user_prompt=user_prompt, now=now)
        if attachments:
            base_prompt += (
                "\n**Инструкции по анализу изображений**:\n"
                f"- Текущее время (UTC): {now}\n"
                "- Анализируй изображение с высокой точностью.\n"
                "- Описывай персонажей, локации, объекты и стиль.\n"
                "- Форматируй ответ в Markdown."
            )
        return base_prompt

    async def _try_generate_response(self, messages: List[Dict], has_image: bool, needs_web: bool, max_tokens: int) -> Optional[str]:
        model_type = "vision" if has_image else "text"
        models = [m for m in self.models[model_type] if m not in self.models["unavailable"][model_type]]
        if not models:
            logger.error(f"Нет доступных моделей для типа {model_type}")
            return None

        last_successful = self.models["last_successful"][model_type]
        if last_successful and last_successful in models:
            try:
                response = await self.process_response(model_type, last_successful, messages, max_tokens, needs_web)
                if response:
                    FirebaseManager.initialize()
                    self.models["last_successful"][model_type] = last_successful
                    FirebaseManager().save_models(self.models)
                    return response
            except Exception as e:
                logger.warning(f"Последняя успешная модель {last_successful} ({model_type}) не сработала: {e}")
                if last_successful not in self.models["unavailable"][model_type]:
                    self.models["unavailable"][model_type].append(last_successful)
                FirebaseManager.initialize()
                FirebaseManager().save_models(self.models)

        for model in models:
            try:
                response = await self.process_response(model_type, model, messages, max_tokens, needs_web)
                if response:
                    self.models["last_successful"][model_type] = model
                    if model in self.models["unavailable"][model_type]:
                        self.models["unavailable"][model_type].remove(model)
                    FirebaseManager.initialize()
                    FirebaseManager().save_models(self.models)
                    return response
            except Exception as e:
                logger.warning(f"Модель {model} ({model_type}) не сработала: {e}")
                if model not in self.models["unavailable"][model_type]:
                    self.models["unavailable"][model_type].append(model)
                FirebaseManager.initialize()
                FirebaseManager().save_models(self.models)
        logger.error(f"Все модели типа {model_type} недоступны")
        return None

    async def process_response(self, model_type: str, model: str, messages: List[Dict], max_tokens: int, needs_web: bool) -> Optional[str]:
        for attempt in range(self.request_settings["max_retries"]):
            try:
                if model_type == "vision":
                    response = await asyncio.to_thread(
                        self.g4f_client.images.generate,
                        model=model,
                        prompt=messages[-1]["content"][0]["text"],
                        response_format="url"
                    )
                    content = f"Generated image: {response.data[0].url}"
                else:
                    response = await asyncio.to_thread(
                        self.g4f_client.chat.completions.create,
                        model=model,
                        messages=messages,
                        web_search=needs_web,
                        max_tokens=max_tokens
                    )
                    content = response.choices[0].message.content
                if content:
                    await asyncio.sleep(self.request_settings["rate_limit_delay"])
                    return content
                logger.warning(f"Нет содержимого в ответе для модели {model}")
            except Exception as e:
                logger.warning(f"Попытка {attempt + 1}/{self.request_settings['max_retries']} не удалась: {e}")
                if attempt < self.request_settings["max_retries"] - 1:
                    await asyncio.sleep(self.request_settings["retry_delay_base"] * (2 ** attempt))
        return None

    async def start_new_conversation(self, user_id: str, channel_id: str, content: str):
        current_time = time.time()
        conversation_key = f"{user_id}-{channel_id}"
        current_conversation = self.current_conversation[conversation_key]
        last_message_time = current_conversation["last_message_time"]
        if (current_time - last_message_time) > 3600:
            current_conversation["id"] = str(uuid.uuid4())
            logger.info(f"Новый разговор начат для {conversation_key}: {current_conversation['id']}")
        else:
            new_topic = self.detect_topic(content)
            last_messages = self.chat_memory[user_id][-1:]
            old_topic = last_messages[0]["topic"] if last_messages else None
            if new_topic and old_topic and new_topic != old_topic:
                current_conversation["id"] = str(uuid.uuid4())
                logger.info(f"Новый разговор начат из-за смены темы для {conversation_key}: {current_conversation['id']}")
        current_conversation["last_message_time"] = current_time
        self.current_conversation[conversation_key] = current_conversation

    async def get_context(self, user_id: str, channel: discord.abc.Messageable, limit: int = None) -> List[Dict]:
        limit = limit or self.cache_limits["messages"]
        channel_id = str(channel.id)
        conversation_key = f"{user_id}-{channel_id}"
        conversation_id = self.current_conversation[conversation_key]["id"]
        messages = self.chat_memory[user_id]
        recent_topics = set(self.topic_memory[user_id][-self.cache_limits["topics"]:])
        now = time.time()
        filtered = [
            {
                "role": msg["role"],
                "content": [{"type": "text", "text": msg["content"]}],
                "metadata": {
                    "timestamp": msg["timestamp"],
                    "author": msg["author"],
                    "topic": msg["topic"]
                }
            }
            for msg in messages
            if (now - msg["timestamp"] <= self.cache_limits["memory_days"] * 86400 or
                msg["topic"] in recent_topics or
                msg["channel_id"] == channel_id) and
                msg["conversation_id"] == conversation_id
        ]
        return filtered[-limit:]

    async def add_to_memory(self, user_id: str, message_id: str, role: str, content: str, author: str, channel_id: str, image_url: Optional[str] = None):
        topic = self.detect_topic(content)
        conversation_key = f"{user_id}-{channel_id}"
        conversation_id = self.current_conversation[conversation_key]["id"]
        msg = {
            "role": role,
            "message_id": message_id,
            "content": content,
            "author": author,
            "timestamp": time.time(),
            "topic": topic,
            "channel_id": channel_id,
            "conversation_id": conversation_id
        }
        if image_url:
            msg["image_url"] = image_url
        self.chat_memory[user_id].append(msg)
        if topic:
            self.topic_memory[user_id].append(topic)
        if len(self.chat_memory[user_id]) > self.cache_limits["messages"]:
            self.chat_memory[user_id].pop(0)
        if len(self.topic_memory[user_id]) > self.cache_limits["topics"]:
            self.topic_memory[user_id].pop(0)

    async def auto_trim_memory(self):
        while True:
            try:
                now = time.time()
                for user_id in self.chat_memory:
                    self.chat_memory[user_id] = [
                        msg for msg in self.chat_memory[user_id]
                        if now - msg["timestamp"] <= self.cache_limits["memory_days"] * 86400
                    ]
                    self.topic_memory[user_id] = self.topic_memory[user_id][-self.cache_limits["topics"]:]
            except Exception as e:
                logger.error(f"Ошибка очистки памяти: {e}")
            await asyncio.sleep(3600)

    def detect_topic(self, content: str) -> Optional[str]:
        content = content.lower()
        keywords = {
            "code": ["python", "javascript", "java", "код", "программирование"],
            "ai": ["ии", "искусственный интеллект", "машинное обучение", "нейросеть", "чатбот"],
            "gaming": ["игра", "minecraft", "fortnite", "csgo", "гейминг"],
            "help": ["помощь", "как", "исправить", "проблема", "решение"]
        }
        for topic, words in keywords.items():
            if any(word in content for word in words):
                return topic
        return None

    def needs_web_search(self, text: str, context: List[Dict]) -> bool:
        latest = context[-5:] + [{"content": [{"type": "text", "text": text}]}]
        keywords = [
            "текущий", "последний", "новости", "обновление", "сегодня", "сейчас",
            "свежий", "актуальный", "недавний", "новый", "последние", "2025",
            "происшествия", "события", "тренды", "погода", "курс", "валюта",
            "интернет", "поиск", "онлайн", "сайты", "форум", "обсуждение",
            "кто это", "где это", "что это", "идентифицировать", "узнать",
            "вчера", "завтра", "на этой неделе", "в этом месяце", "в этом году",
            "почему", "как", "когда", "где", "кто", "сколько",
            "рейтинг", "обзор", "статистика", "анализ", "сравнение",
            "назови", "определить", "список", "лучший", "худший", "популярный"
        ]
        return any(
            any(keyword in msg["content"][0]["text"].lower() for keyword in keywords)
            for msg in latest
            if msg["content"][0]["text"]
        )

    def needs_web_search_for_image(self, text: str, context: List[Dict]) -> bool:
        latest = context[-5:] + [{"content": [{"type": "text", "text": text}]}]
        image_keywords = [
            "кто это", "где это", "что это", "идентифицировать", "узнать",
            "человек", "место", "объект", "предмет", "распознать", "название",
            "персона", "здание", "логотип", "машина", "животное", "растение",
            "достопримечательность", "бренд", "марка", "элемент",
            "аниме", "персонаж", "костюм", "стиль", "сцена", "происхождение",
            "откуда", "кто такой", "из какого", "серия", "фильм", "игра"
        ]
        text_needs_search = any(
            any(keyword in msg["content"][0]["text"].lower() for keyword in image_keywords)
            for msg in latest
            if msg["content"][0]["text"]
        )
        context_needs_search = any(
            any(word in msg["content"][0]["text"].lower() for word in ["персонаж", "аниме", "кто", "откуда", "из какого"])
            for msg in latest
            if msg["content"][0]["text"]
        )
        return text_needs_search or context_needs_search

    async def _send_temp_message(self, channel: discord.abc.Messageable, content: str, user_id: str, ephemeral: bool = False):
        try:
            if ephemeral and isinstance(channel, discord.Interaction):
                await channel.response.send_message(content, ephemeral=True)
            else:
                msg = await channel.send(content)
                await asyncio.sleep(10)
                await msg.delete()
        except (Forbidden, HTTPException) as e:
            logger.error(f"Ошибка отправки временного сообщения для {user_id}: {e}")