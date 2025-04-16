import discord
from discord import app_commands, Forbidden, HTTPException
import asyncio
from typing import List, Dict, Optional
import aiohttp
from g4f.client import Client
from g4f.Provider import PollinationsAI
from .systemLog import logger
import time
import re
from collections import defaultdict
import json
import random

class BotClient:
    def __init__(self, config):
        logger.info("Создание BotClient")
        self.config = config
        self.client = Client(provider=PollinationsAI)
        self.bot = discord.Client(intents=self._setup_intents())
        self.tree = app_commands.CommandTree(self.bot)
        self.models = {"text": [], "vision": [], "last_update": None}
        self.chat_memory = defaultdict(list)
        self.topic_memory = defaultdict(list)
        self.processed_messages = set()
        self.message_to_response = {}
        self.link_cache = {}
        self.prompt_cache = {}  # Добавлен кэш для промптов
        self.user_settings = defaultdict(lambda: {"max_response_length": 2000})
        self.giveaways = {}
        self.completed_giveaways = {}
        self._initialize_settings()
        asyncio.create_task(self.update_models_periodically())
        asyncio.create_task(self.auto_trim_memory())

    async def close(self):
        """Закрывает все ресурсы."""
        try:
            if hasattr(self.client, '_session') and self.client._session:
                await self.client._session.close()
                logger.info("Сессия aiohttp закрыта")
            await self.bot.close()
            logger.info("Клиент Discord закрыт")
        except Exception as e:
            logger.error(f"Ошибка при закрытии ресурсов: {e}")

    def _setup_intents(self) -> discord.Intents:
        intents = discord.Intents.default()
        intents.message_content = intents.dm_messages = intents.members = True
        return intents

    def _initialize_settings(self):
        self.cache_limits = {
            "messages": 50,
            "topics": 5,
            "memory_days": 14
        }
        self.request_settings = {
            "vision_headers": {"Content-Type": "application/json"},
            "rate_limit_delay": 2.0,
            "max_retries": 5,
            "retry_delay_base": 5.0
        }
        try:
            from .firebase.firebase_manager import FirebaseManager
            self.models = FirebaseManager.initialize().load_models()
            logger.info(f"Модели загружены: text={self.models['text']}, vision={self.models['vision']}")
        except Exception as e:
            logger.error(f"Ошибка загрузки моделей: {e}")
            self.models = {"text": ["gpt-4o-mini"], "vision": [], "last_update": None}

    async def update_models_periodically(self):
        await self.fetch_available_models()
        while True:
            try:
                if not self.models["last_update"] or (time.time() - self.models["last_update"]) > 86400:
                    await self.fetch_available_models()
                await asyncio.sleep(3600)
            except Exception as e:
                logger.error(f"Ошибка периодического обновления моделей: {e}")
                await asyncio.sleep(1800)

    async def fetch_available_models(self):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
                async with session.get("https://text.pollinations.ai/models") as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    new_models = {"text": [], "vision": [], "last_update": time.time()}
                    if isinstance(data, list):
                        for m in data:
                            if m.get("id"):
                                (new_models["vision"] if m.get("supports_vision", False) else new_models["text"]).append(m["id"])
                    else:
                        new_models["text"] = data.get("text_models", [])
                        new_models["vision"] = data.get("vision_models", [])
                    for mt in ["text", "vision"]:
                        valid = await asyncio.gather(*[self.check_model_availability(m, mt == "vision") for m in new_models[mt]])
                        new_models[mt] = [m for m, (v, _, _) in zip(new_models[mt], valid) if v] or self.models[mt]
                    self.models = new_models
                    from .firebase.firebase_manager import FirebaseManager
                    FirebaseManager.initialize().save_models(self.models)
                    logger.info(f"Модели обновлены: text={self.models['text']}, vision={self.models['vision']}")
        except Exception as e:
            logger.error(f"Ошибка получения моделей: {e}")

    async def check_model_availability(self, model: str, is_vision: bool) -> tuple[bool, float, float]:
        try:
            async with aiohttp.ClientSession() as session:
                start_time = time.time()
                if is_vision:
                    async with session.post(f"https://text.pollinations.ai/{model}", json={
                        "messages": [{"role": "user", "content": [{"type": "text", "text": "ping"}]}],
                        "max_tokens": 10
                    }, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                        latency = time.time() - start_time
                        return resp.status == 200, latency, 1.0
                resp = await asyncio.to_thread(self.client.chat.completions.create, model=model, messages=[{"role": "user", "content": "ping"}], max_tokens=1)
                return bool(resp.choices[0].message.content), time.time() - start_time, 1.0
        except Exception:
            return False, float('inf'), 0.0

    async def on_message(self, message: discord.Message):
        from .commands.restrict import check_user_restriction, check_bot_access, handle_mention
        msg_key = f"{message.id}-{message.channel.id}"
        if message.author.bot or msg_key in self.processed_messages:
            return
        self.processed_messages.add(msg_key)
        try:
            if isinstance(message.channel, discord.DMChannel):
                if not await check_user_restriction(message):
                    return
                await self._process_message(message)
            else:
                if not await check_bot_access(message) or not await check_user_restriction(message):
                    return
                if await handle_mention(message, self):
                    await self._process_message(message)
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения {msg_key}: {e}")

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        from .commands.restrict import check_user_restriction, check_bot_access
        msg_key = f"{after.id}-{after.channel.id}"
        if before.content == after.content or after.author.bot or msg_key not in self.processed_messages:
            return
        try:
            if isinstance(after.channel, discord.DMChannel):
                if not await check_user_restriction(after):
                    return
                await self._process_edit(after)
            else:
                if not await check_bot_access(after) or not await check_user_restriction(after):
                    return
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
        sent_msg = None
        for i, part in enumerate(parts):
            try:
                if i == 0:
                    sent_msg = await message.reply(part)
                    self.message_to_response[message.id] = sent_msg.id
                else:
                    sent_msg = await message.channel.send(part)
                    self.message_to_response[f"{message.id}_{i}"] = sent_msg.id
            except (Forbidden, HTTPException) as e:
                error_message = "Упс, нет прав." if isinstance(e, Forbidden) else "Упс, ошибка API."
                await self._send_temp_message(message.channel, error_message, str(message.author.id))

    async def generate_response(self, user_id: str, message_id: str, text: str, message: discord.Message, is_edit: bool = False) -> Optional[str]:
        try:
            if not (text or message.attachments):
                return "😅 Ой, кажется, ты не добавил текст или изображение. Давай попробуем снова?"
            context = await self.get_context(user_id, message.channel)
            guild_id = str(message.guild.id) if message.guild else "DM"
            system_prompt = await self._build_system_prompt(user_id, guild_id, message.attachments)
            user_content = [{"type": "text", "text": text}] if text else []
            attachments = [a.url for a in message.attachments if a.content_type and a.content_type.startswith("image/")]
            user_content.extend({"type": "image_url", "image_url": {"url": url}} for url in attachments)
            messages = context + [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}]
            response_text = await self._try_generate_response(messages, self.needs_web_search(text, context, bool(attachments)), bool(attachments), 6000, text)
            if not response_text:
                return "Хм, не получилось найти ответ. Может, попробуем ещё раз с другим изображением или запросом?"
            final_response = response_text[:self.user_settings[user_id]["max_response_length"]]
            await self.add_to_memory(user_id, message_id, "user", text, message.author.name, message.channel.id, attachments[0] if attachments else None)
            await self.add_to_memory(user_id, f"{message_id}_resp", "assistant", final_response, self.bot.user.name, message.channel.id)
            return final_response
        except Exception as e:
            logger.error(f"Ошибка генерации для {user_id}: {e}")
            await self._send_temp_message(message.channel, "Упс, что-то пошло не так. Давай попробуем ещё раз?", user_id)
            return None

    async def _build_system_prompt(self, user_id: str, guild_id: str, attachments: List) -> str:
        from .commands.prompt import load_user_prompt
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))
        prompt = f"{await load_user_prompt(user_id, guild_id, self)}\n📅 Дата: {now}. Формат: Discord Markdown."
        if attachments:
            prompt += "\nПривет! Я помогу разобрать изображение:\n- **Персонажи**: Кто на картинке?\n- **Места**: Где это?\n- **Предметы**: Что интересного?\n\nДам точный ответ в формате Discord Markdown.\nДавай начнём! 🎨"
        return prompt

    async def _try_generate_response(self, messages: List[Dict], needs_web: bool, has_image: bool, max_tokens: int, text: str) -> Optional[str]:
        model_type = "vision" if has_image else "text"
        models = self.models[model_type][:] or ["gpt-4o-mini"]
        random.shuffle(models)
        for model in models:
            try:
                if response := await self.process_response(model_type, model, messages, max_tokens, text):
                    return response
            except Exception as e:
                logger.warning(f"Модель {model} ({model_type}) не сработала: {e}")
        logger.error(f"Нет ответа от моделей типа {model_type}")
        return None

    async def process_response(self, model_type: str, model: str, messages: List[Dict], max_tokens: int, text: str) -> Optional[str]:
        url = f"https://pollinations.ai/{model}" if model_type == "vision" else None
        if model_type == "vision":
            context = await self.analyze_image_context(messages, text) or "Контекст не определён."
            messages[-1]["content"].append({"type": "text", "text": context})
        payload = {
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False,
            "temperature": 0.7,
            "top_p": 0.9
        } if url else None
        for attempt in range(self.request_settings["max_retries"]):
            try:
                if url:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(url, headers=self.request_settings["vision_headers"], json=payload) as resp:
                            resp.raise_for_status()
                            result = await resp.json()
                            await asyncio.sleep(self.request_settings["rate_limit_delay"])
                            return result["choices"][0]["message"]["content"]
                resp = await asyncio.to_thread(self.client.chat.completions.create, model=model, messages=messages, max_tokens=max_tokens)
                return resp.choices[0].message.content
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"Попытка {attempt + 1}/{self.request_settings['max_retries']} не удалась для модели {model}: {e}")
                if attempt < self.request_settings["max_retries"] - 1:
                    await asyncio.sleep(self.request_settings["retry_delay_base"] * (2 ** attempt))
            except Exception as e:
                logger.error(f"Ошибка модели {model}: {e}")
                break
        return None

    async def get_context(self, user_id: str, channel: discord.abc.Messageable, limit: int = None) -> List[Dict]:
        limit = limit or self.cache_limits["messages"]
        channel_id = str(channel.id)
        messages = self.chat_memory[user_id]
        recent_topics = set(self.topic_memory[user_id][-self.cache_limits["topics"]:])
        now = time.time()
        filtered = [
            {
                "role": msg["role"],
                "content": [{"type": "text", "text": msg["content"]}],
                "metadata": {"timestamp": msg["timestamp"], "author": msg["author"], "topic": msg["topic"]}
            }
            for msg in messages
            if (now - msg["timestamp"] <= self.cache_limits["memory_days"] * 86400 or
                msg["topic"] in recent_topics or msg["channel_id"] == channel_id)
        ]
        return filtered[-limit:]

    async def add_to_memory(self, user_id: str, message_id: str, role: str, content: str, author: str, channel_id: str, image_url: Optional[str] = None):
        topic = self.detect_topic(content)
        msg = {
            "role": role,
            "message_id": message_id,
            "content": content,
            "author": author,
            "timestamp": time.time(),
            "topic": topic,
            "channel_id": channel_id
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
            "code": ["python", "javascript", "java", "code", "programming"],
            "ai": ["ai", "machine learning", "neural network", "chatbot"],
            "gaming": ["game", "minecraft", "fortnite", "csgo"],
            "help": ["help", "how to", "fix", "issue"]
        }
        for topic, words in keywords.items():
            if any(word in content for word in words):
                return topic
        return None

    async def _send_temp_message(self, channel: discord.abc.Messageable, content: str, user_id: str):
        try:
            msg = await channel.send(content)
            await asyncio.sleep(10)
            await msg.delete()
        except (Forbidden, HTTPException) as e:
            logger.error(f"Ошибка отправки временного сообщения для {user_id}: {e}")

    def needs_web_search(self, text: str, context: List[Dict], has_image: bool) -> bool:
        if has_image:
            return False
        latest_messages = context[-3:] + [{"content": [{"type": "text", "text": text}]}]
        keywords = ["current", "recent", "news", "update", "today", "now"]
        return any(
            any(keyword in msg["content"][0]["text"].lower() for keyword in keywords)
            for msg in latest_messages
            if msg["content"][0]["text"]
        )

    async def analyze_image_context(self, messages: List[Dict], text: str) -> Optional[str]:
        try:
            prompt = "Опиши контекст изображения: что происходит, кто или что на нём, где это может быть."
            if text:
                prompt += f"\nДополнительный запрос: {text}"
            messages = messages[-3:] + [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
            resp = await asyncio.to_thread(
                self.client.chat.completions.create,
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=100
            )
            return resp.choices[0].message.content
        except Exception as e:
            logger.error(f"Ошибка анализа изображения: {e}")
            return None