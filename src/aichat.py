import discord
from discord import app_commands, Forbidden, HTTPException
import asyncio
from typing import List, Dict, Optional
import aiohttp
from g4f.client import Client
from g4f.Provider import PollinationsAI
from .logging_config import logger
from datetime import datetime, timedelta
import re
from collections import OrderedDict, defaultdict
import json
import os

class BotClient:
    def __init__(self, config):
        logger.info("Создание BotClient")
        self.config = config
        self.client = Client(provider=PollinationsAI)
        self.models_file = "models.json"
        intents = discord.Intents.default()
        intents.message_content = intents.dm_messages = intents.members = True
        self.bot = discord.Client(intents=intents)
        self.tree = app_commands.CommandTree(self.bot)
        self.processed_messages = set()
        self.message_to_response = {}
        self.link_cache = OrderedDict(maxlen=20)
        self.chat_memory = defaultdict(lambda: OrderedDict())
        self.cache_limits = {"messages": 50}
        self.request_settings = {
            "vision_headers": {"Content-Type": "application/json"},
            "rate_limit_delay": 3.0,
            "max_retries": 5,
            "retry_delay_base": 8.0
        }
        self.models = {"text": [], "vision": [], "last_update": None}
        self.user_settings = defaultdict(lambda: {"max_response_length": 4000})
        self.load_models()
        asyncio.create_task(self.update_models_periodically())
        asyncio.create_task(self.auto_trim_memory())
        from .commands.set_prompt import create_command, load_user_prompt
        self.load_user_prompt = load_user_prompt
        self.tree.add_command(create_command(self))

    def load_models(self):
        default = {"text": ["gpt-4o-mini", "gpt-4o", "o1-mini"], "vision": ["openai", "openai-large"], "last_update": None}
        try:
            if os.path.exists(self.models_file):
                with open(self.models_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.models.update({
                    "text": data.get("text_models", default["text"]),
                    "vision": data.get("vision_models", default["vision"]),
                    "last_update": datetime.fromisoformat(data["last_update"]) if data.get("last_update") else None
                })
                logger.info(f"Модели: text={self.models['text']}, vision={self.models['vision']}")
            else:
                raise FileNotFoundError
        except Exception as e:
            logger.error(f"Ошибка загрузки моделей: {e}")
            self.models.update(default)
            self.save_models()

    def save_models(self):
        try:
            with open(self.models_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "text_models": self.models["text"],
                    "vision_models": self.models["vision"],
                    "last_update": self.models["last_update"].isoformat() if self.models["last_update"] else None
                }, f, ensure_ascii=False)
            logger.info("Модели сохранены")
        except Exception as e:
            logger.error(f"Ошибка сохранения моделей: {e}")

    async def update_models_periodically(self):
        await self.fetch_available_models()
        while True:
            try:
                if not self.models["last_update"] or (datetime.now() - self.models["last_update"]).total_seconds() > 1800:
                    await self.fetch_available_models()
                await asyncio.sleep(600)
            except Exception as e:
                logger.error(f"Ошибка обновления моделей: {e}")
                await asyncio.sleep(300)

    async def fetch_available_models(self):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            try:
                async with session.get("https://text.pollinations.ai/models") as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    self.models["last_update"] = datetime.now()
                    new_models = {"text": [], "vision": []}
                    if isinstance(data, list):
                        for m in data:
                            if m.get("id"):
                                (new_models["vision"] if m.get("supports_vision", False) else new_models["text"]).append(m["id"])
                    else:
                        new_models["text"], new_models["vision"] = data.get("text_models", []), data.get("vision_models", [])
                    for mt in ["text", "vision"]:
                        valid = await asyncio.gather(*[self.check_model_availability(m, mt == "vision") for m in new_models[mt]])
                        self.models[mt] = [m for m, v in zip(new_models[mt], valid) if v] or self.models[mt]
                    self.save_models()
                    logger.info(f"Модели обновлены: text={self.models['text']}, vision={self.models['vision']}")
            except Exception as e:
                logger.error(f"Ошибка получения моделей: {e}")

    async def check_model_availability(self, model: str, is_vision: bool) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                if is_vision:
                    async with session.head(f"https://text.pollinations.ai/{model}", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        return resp.status == 200
                resp = await asyncio.to_thread(self.client.chat.completions.create, model=model, messages=[{"role": "user", "content": "ping"}], max_tokens=1)
                return bool(resp.choices[0].message.content)
        except Exception:
            return False

    async def process_response(self, model_type: str, model: str, messages: List[Dict], max_tokens: int, web_search: bool) -> Optional[str]:
        url = f"https://text.pollinations.ai/{model}" if model_type == "vision" else None
        payload = {"messages": messages, "max_tokens": max_tokens, "stream": False, "web_search": web_search, "temperature": 0.7, "top_p": 0.9} if url else None
        for attempt in range(self.request_settings["max_retries"]):
            try:
                if url:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(url, headers=self.request_settings["vision_headers"], json=payload) as resp:
                            resp.raise_for_status()
                            result = await resp.json()
                            await asyncio.sleep(self.request_settings["rate_limit_delay"])
                            return result["choices"][0]["message"]["content"]
                resp = await asyncio.to_thread(self.client.chat.completions.create, model=model, messages=messages, max_tokens=max_tokens, web_search=web_search)
                return resp.choices[0].message.content
            except Exception as e:
                if str(e).find("429") != -1 or (url and getattr(e, "status", 0) in (429, 502)):
                    delay = self.request_settings["retry_delay_base"] * (2 ** attempt)
                    await asyncio.sleep(delay)
                    continue
                logger.error(f"Ошибка модели {model}: {e}")
                return None
        return None

    async def get_context(self, user_id: str, channel: discord.abc.Messageable, limit: int = None) -> List[Dict]:
        limit = limit or self.cache_limits["messages"]
        memory = [{"role": m["role"], "content": [{"type": "text", "text": m["content"]}], "metadata": {"timestamp": m["timestamp"], "author": m["author"]}}
                  for m in list(self.chat_memory[user_id].values())[-limit:]]
        return memory

    async def add_to_memory(self, user_id: str, message_id: str, role: str, content: str, author: str, image_url: Optional[str] = None):
        msg = {"role": role, "content": content, "author": author, "timestamp": datetime.now().isoformat(), "expires": (datetime.now() + timedelta(days=30)).isoformat()}
        if image_url:
            msg["image"] = image_url
        self.chat_memory[user_id][message_id] = msg
        while len(self.chat_memory[user_id]) > self.cache_limits["messages"]:
            self.chat_memory[user_id].popitem(last=False)

    async def auto_trim_memory(self):
        while True:
            now = datetime.now()
            for user_id, messages in list(self.chat_memory.items()):
                for msg_id, msg in list(messages.items()):
                    if "expires" in msg and datetime.fromisoformat(msg["expires"]) < now:
                        del messages[msg_id]
            await asyncio.sleep(3600)

    async def check_link_validity(self, url: str) -> bool:
        if url in self.link_cache:
            return self.link_cache[url]
        async with aiohttp.ClientSession() as session:
            try:
                async with session.head(url, timeout=aiohttp.ClientTimeout(total=5), allow_redirects=True) as resp:
                    valid = resp.status == 200
                    self.link_cache[url] = valid
                    return valid
            except Exception:
                self.link_cache[url] = False
                return False

    def needs_web_search(self, text: str, context: List[Dict]) -> bool:
        keywords = {"найди", "сегодня", "сейчас", "новости", "погода", "статистика", "курс", "цена", "поиск", "интернет", "онлайн", "актуально", "свежие", "обновления"}
        time_phrases = r"\b(сегодня|вчера|завтра|на этой неделе|в этом месяце|в этом году|последние|текущие|недавно)\b"
        question_phrases = r"\b(что|где|когда|как|почему|сколько|какой|какая|какие)\b"
        text_lower = text.lower() if isinstance(text, str) else " ".join(str(item) for item in text).lower()
        context_lower = " ".join(str(msg.get("content", "")).lower() for msg in context[-5:] if msg.get("role") == "user")
        return (any(kw in text_lower for kw in keywords) or
                any(kw in context_lower for kw in keywords) or
                bool(re.search(time_phrases, text_lower)) or
                bool(re.search(question_phrases, text_lower)) or
                bool(re.search(r"\b(что происходит|какая погода|последние новости|узнать|проверить|найди в интернете)\b", text_lower)))

    async def generate_response(self, user_id: str, message_id: str, text: str, message: discord.Message, is_edit: bool = False) -> Optional[str]:
        try:
            if not (text or message.attachments):
                return "**Ой!** Текст или вложения отсутствуют"
            context = await self.get_context(user_id, message.channel)
            now = datetime.now()
            guild_id = str(message.guild.id) if message.guild else "DM"
            system_prompt = f"{self.load_user_prompt(user_id, guild_id)}\nДата: {now:%Y-%m-%d %H:%M:%S}. Формат: Discord Markdown."
            user_content = [{"type": "text", "text": text}] if text else []
            attachments = [a.url for a in message.attachments if a.content_type and a.content_type.startswith("image/")]
            user_content.extend({"type": "image_url", "image_url": {"url": url}} for url in attachments)
            messages = context + [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}]
            response_text = await self._try_generate_response(messages, self.needs_web_search(text, context), bool(attachments), 6000)
            if not response_text:
                return "**Ой!** Нет ответа"
            final_response = response_text[:self.user_settings[user_id]["max_response_length"]]
            await self.add_to_memory(user_id, message_id, "user", text, message.author.name, attachments[0] if attachments else None)
            await self.add_to_memory(user_id, f"{message_id}_resp", "assistant", final_response, self.bot.user.name)
            return final_response
        except Exception as e:
            logger.error(f"Ошибка генерации для {user_id}: {e}")
            await self._send_temp_message(message.channel, "**Упс!** Ошибка", user_id)
            return None

    async def _try_generate_response(self, messages: List[Dict], needs_web: bool, has_image: bool, max_tokens: int) -> Optional[str]:
        model_type = "vision" if has_image else "text"
        for model in self.models[model_type] or []:
            if response := await self.process_response(model_type, model, messages, max_tokens, needs_web or has_image):
                return response
        logger.error(f"Нет ответа от моделей типа {model_type}")
        return None

    async def _send_temp_message(self, channel, content: str, user_id: str, duration: int = 5):
        try:
            msg = await channel.send(content)
            await asyncio.sleep(duration)
            await msg.delete()
        except Exception as e:
            logger.error(f"Ошибка временного сообщения для {user_id}: {e}")

    async def on_message(self, message: discord.Message):
        msg_key = f"{message.id}-{message.channel.id}"
        if message.author.bot or msg_key in self.processed_messages:
            return
        if self.bot.user not in message.mentions and not isinstance(message.channel, discord.DMChannel):
            return
        self.processed_messages.add(msg_key)
        async with message.channel.typing():
            text = message.content.replace(f"<@{self.bot.user.id}>", "").strip()
            response = await self.generate_response(str(message.author.id), str(message.id), text, message)
            if response:
                sent_msg = await message.reply(response)
                self.message_to_response[message.id] = sent_msg.id

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        msg_key = f"{after.id}-{after.channel.id}"
        if before.content == after.content or after.author.bot or msg_key not in self.processed_messages:
            return
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

    def setup_events(self):
        @self.bot.event
        async def on_message(message):
            await self.on_message(message)

        @self.bot.event
        async def on_message_edit(before, after):
            await self.on_message_edit(before, after)

    async def _send_split_message(self, message: discord.Message, response: str):
        max_length = self.user_settings[str(message.author.id)]["max_response_length"]
        parts = [response[i:i + max_length] for i in range(0, len(response), max_length)]
        sent_msg = None
        for i, part in enumerate(parts):
            try:
                if i == 0:
                    sent_msg = await message.reply(part)
                    self.message_to_response[message.id] = sent_msg.id
                else:
                    await message.channel.send(part)
            except (Forbidden, HTTPException) as e:
                await self._send_temp_message(message.channel, f"**Упс!** {'Нет прав' if isinstance(e, Forbidden) else 'Ошибка API'}", str(message.author.id))

    async def clear_user_memory(self, user_id: str):
        self.chat_memory[user_id].clear()
        logger.info(f"Память очищена для {user_id}")

    async def get_user_stats(self, user_id: str) -> Dict[str, int]:
        msgs = self.chat_memory[user_id]
        return {"total": len(msgs), "user": sum(m["role"] == "user" for m in msgs.values()), "bot": sum(m["role"] == "assistant" for m in msgs.values())}

    async def trim_old_messages(self, user_id: str, days: int = 7):
        threshold = datetime.now() - timedelta(days=days)
        self.chat_memory[user_id] = OrderedDict((k, v) for k, v in self.chat_memory[user_id].items() if datetime.fromisoformat(v["timestamp"]) >= threshold)
        logger.info(f"Старые сообщения удалены для {user_id}")

    async def export_memory_to_json(self, user_id: str, filename: str) -> bool:
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(dict(self.chat_memory[user_id]), f, ensure_ascii=False)
            logger.info(f"Память экспортирована для {user_id} в {filename}")
            return True
        except Exception as e:
            logger.error(f"Ошибка экспорта для {user_id}: {e}")
            return False