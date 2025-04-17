import discord
from discord import app_commands, Forbidden, HTTPException
import asyncio
from typing import List, Dict, Optional
import aiohttp
import json
from g4f.client import Client
from g4f.Provider import PollinationsAI
from .systemLog import logger
import time
import random
from collections import defaultdict
import uuid

class BotClient:
    def __init__(self, config):
        logger.info("Инициализация BotClient")
        self.config = config
        self.client = Client(provider=PollinationsAI)
        self.bot = discord.Client(intents=self._setup_intents())
        self.tree = app_commands.CommandTree(self.bot)
        self.models = {
            "text": [
                "gpt-4o-mini", "gpt-4o", "o1-mini", "qwen-2.5-coder-32b", "llama-3.3-70b", "mistral-nemo",
                "llama-3.1-8b", "deepseek-r1", "phi-4", "qwq-32b", "deepseek-v3", "llama-3.2-11b",
                "grok-3", "claude-3.5-sonnet", "gemini-1.5-pro", "mixtral-8x7b"
            ],
            "vision": [
                "gpt-4o", "gpt-4o-mini", "o1-mini", "o3-mini", "clip-vit-large", "dall-e-3", "stable-diffusion-xl"
            ],
            "last_update": None,
            "unavailable": {"text": set(), "vision": set()},
            "last_successful": {"text": None, "vision": None}
        }
        self.prompt_cache = {}
        self.chat_memory = defaultdict(list)
        self.topic_memory = defaultdict(list)
        self.current_conversation = defaultdict(lambda: {"id": str(uuid.uuid4()), "last_message_time": time.time()})
        self.processed_messages = set()
        self.message_to_response = {}
        self.user_settings = defaultdict(lambda: {"max_response_length": 2000})
        self._initialize_settings()
        asyncio.create_task(self.update_models_periodically())

    async def close(self):
        try:
            if hasattr(self.client, '_session') and self.client._session:
                await self.client._session.close()
                logger.info("Сессия aiohttp закрыта")
            await self.bot.close()
            logger.info("Клиент Discord закрыт")
        except Exception as e:
            logger.error(f"Ошибка закрытия ресурсов: {e}")

    def _setup_intents(self) -> discord.Intents:
        intents = discord.Intents.default()
        intents.message_content = intents.dm_messages = intents.members = True
        return intents

    def _initialize_settings(self):
        self.cache_limits = {"messages": 100, "topics": 5, "memory_days": 14}
        self.request_settings = {
            "headers": {"Content-Type": "application/json", "Accept": "application/json"},
            "rate_limit_delay": 3.0,
            "max_retries": 3,
            "retry_delay_base": 5.0
        }

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
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                async with session.get("https://text.pollinations.ai/models") as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    new_models = {
                        "text": [],
                        "vision": [],
                        "last_update": time.time(),
                        "unavailable": {"text": set(), "vision": set()},
                        "last_successful": self.models["last_successful"]
                    }
                    models_data = data if isinstance(data, list) else data.get("models", [])
                    known_models = set(self.models["text"])
                    known_vision = set(self.models["vision"])
                    for model in models_data:
                        model_id = model if isinstance(model, str) else model.get("id", "")
                        if model_id in known_models:
                            new_models["text"].append(model_id)
                            if model_id in known_vision or (isinstance(model, dict) and model.get("supports_vision")):
                                new_models["vision"].append(model_id)
                    new_models["text"] = new_models["text"] or self.models["text"]
                    new_models["vision"] = new_models["vision"] or self.models["vision"]
                    new_models["unavailable"]["text"] = self.models["unavailable"]["text"]
                    new_models["unavailable"]["vision"] = self.models["unavailable"]["vision"]
                    self.models = new_models
                    logger.info(f"Модели обновлены: текст={len(new_models['text'])}, вижн={len(new_models['vision'])}")
                    from .firebase.firebase_manager import FirebaseManager
                    FirebaseManager.initialize()
                    FirebaseManager().save_models(self.models)
        except aiohttp.ClientError as e:
            logger.error(f"Сетевая ошибка: {e}")
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON: {e}")
        except Exception as e:
            logger.error(f"Ошибка получения моделей: {e}")

    async def on_message(self, message: discord.Message):
        from .commands.restrict import check_user_restriction, check_bot_access, handle_mention
        msg_key = f"{message.id}-{message.channel.id}"
        if message.author.bot or msg_key in self.processed_messages:
            return
        if isinstance(message.channel, (discord.StageChannel, discord.VoiceChannel)):
            return
        self.processed_messages.add(msg_key)
        try:
            user_id = str(message.author.id)
            channel_id = str(message.channel.id)
            await self.start_new_conversation(user_id, channel_id, message.content)
            if isinstance(message.channel, discord.DMChannel):
                if await check_user_restriction(message):
                    await self._process_message(message)
            elif await check_bot_access(message) and await check_user_restriction(message) and await handle_mention(message, self):
                await self._process_message(message)
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения {msg_key}: {e}")

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        from .commands.restrict import check_user_restriction, check_bot_access
        msg_key = f"{after.id}-{after.channel.id}"
        if before.content == after.content or after.author.bot or msg_key not in self.processed_messages:
            return
        if isinstance(after.channel, (discord.StageChannel, discord.VoiceChannel)):
            return
        try:
            user_id = str(after.author.id)
            channel_id = str(after.channel.id)
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
                await self._send_temp_message(message.channel, "Ошибка отправки.", str(message.author.id))

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
            await self._send_temp_message(message.channel, "Ошибка. Попробуйте позже.", user_id)
            return None

    async def _build_system_prompt(self, user_id: str, guild_id: str, attachments: List) -> str:
        from .commands.prompt import load_user_prompt
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        prompt = (
            f"System Instructions: {await load_user_prompt(user_id, guild_id, self)}\n"
            f"Current Date and Time (UTC): {now}\n"
            f"Output Format: Discord Markdown\n"
            "Provide accurate, concise, and clear responses. Structure answers with bullet points or numbered lists when appropriate. "
            "Use reliable information and avoid speculation. For queries requiring recent data, utilize web search to ensure accuracy. "
            "Always use the current date and time provided above to ensure responses are up-to-date."
        )
        if attachments:
            prompt += (
                "\nImage Analysis Instructions:\n"
                f"Current Date and Time (UTC): {now}\n"
                "- Analyze the image with high precision to identify its content and context.\n"
                "- Identify and describe:\n"
                "  - Characters: If the image depicts a character (e.g., from anime, games, or other media), provide their name, origin (title of the anime, game, or media), and relevant details (role, personality, or notable traits). Use web search to confirm identities if necessary.\n"
                "  - Locations: Specify the setting or geographical context of the scene. Use web search to identify landmarks or settings if needed.\n"
                "  - Objects: Detail notable items, their purpose, or context. Use web search for unfamiliar objects.\n"
                "  - Art Style: Determine the style (e.g., anime, realism, cartoon) and describe relevant visual elements (e.g., costume, background).\n"
                "- If the image contains text, transcribe and interpret it in the context of the image.\n"
                "- Use web search to enhance accuracy for ambiguous or context-dependent elements (e.g., identifying a character or location).\n"
                "Format the response in Discord Markdown, using clear headings and lists."
            )
        return prompt

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
                    # Сохранение успешной модели в Firebase
                    from .firebase.firebase_manager import FirebaseManager
                    FirebaseManager.initialize()
                    self.models["last_successful"][model_type] = last_successful
                    FirebaseManager().save_models(self.models)
                    return response
            except Exception as e:
                logger.warning(f"Последняя успешная модель {last_successful} ({model_type}) не сработала: {e}")
                self.models["unavailable"][model_type].add(last_successful)
                from .firebase.firebase_manager import FirebaseManager
                FirebaseManager.initialize()
                FirebaseManager().save_models(self.models)

        for model in models:
            try:
                response = await self.process_response(model_type, model, messages, max_tokens, needs_web)
                if response:
                    self.models["last_successful"][model_type] = model
                    self.models["unavailable"][model_type].discard(model)
                    # Сохранение в Firebase
                    from .firebase.firebase_manager import FirebaseManager
                    FirebaseManager.initialize()
                    FirebaseManager().save_models(self.models)
                    return response
            except Exception as e:
                logger.warning(f"Модель {model} ({model_type}) не сработала: {e}")
                self.models["unavailable"][model_type].add(model)
                # Сохранение в Firebase
                from .firebase.firebase_manager import FirebaseManager
                FirebaseManager.initialize()
                FirebaseManager().save_models(self.models)
        logger.error(f"Все модели типа {model_type} недоступны")
        return None

    async def process_response(self, model_type: str, model: str, messages: List[Dict], max_tokens: int, needs_web: bool) -> Optional[str]:
        url = "https://text.pollinations.ai/openai" if model_type == "vision" else "https://text.pollinations.ai/"
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.7,
            "top_p": 0.9
        }
        if needs_web:
            payload["web_search"] = True
        for attempt in range(self.request_settings["max_retries"]):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=self.request_settings["headers"], json=payload) as resp:
                        resp.raise_for_status()
                        content_type = resp.headers.get("Content-Type", "")
                        raw_text = await resp.text()
                        raw_text = raw_text.strip().lstrip('\ufeff')
                        if "application/json" in content_type:
                            try:
                                data = json.loads(raw_text)
                                content = data.get("choices", [{}])[0].get("message", {}).get("content")
                                if content:
                                    await asyncio.sleep(self.request_settings["rate_limit_delay"])
                                    return content
                                logger.warning(f"Нет содержимого в JSON-ответе для модели {model}")
                            except json.JSONDecodeError as e:
                                logger.error(f"Ошибка парсинга JSON для модели {model}: {e}, содержимое: {raw_text[:500]}")
                                return None
                        else:
                            try:
                                data = json.loads(raw_text)
                                content = data.get("choices", [{}])[0].get("message", {}).get("content")
                                if content:
                                    await asyncio.sleep(self.request_settings["rate_limit_delay"])
                                    return content
                                logger.warning(f"Нет содержимого в JSON (text/plain) для модели {model}")
                            except json.JSONDecodeError:
                                if raw_text:
                                    await asyncio.sleep(self.request_settings["rate_limit_delay"])
                                    return raw_text
                                logger.warning(f"Пустой текстовый ответ для модели {model}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"Попытка {attempt + 1}/{self.request_settings['max_retries']} не удалась: {e}")
                if attempt < self.request_settings["max_retries"] - 1:
                    await asyncio.sleep(self.request_settings["retry_delay_base"] * (2 ** attempt))
            except Exception as e:
                logger.error(f"Ошибка модели {model}: {e}")
                break
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

    async def _send_temp_message(self, channel: discord.abc.Messageable, content: str, user_id: str):
        try:
            msg = await channel.send(content)
            await asyncio.sleep(10)
            await msg.delete()
        except (Forbidden, HTTPException):
            logger.error(f"Ошибка отправки временного сообщения для {user_id}")