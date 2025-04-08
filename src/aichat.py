import discord
from discord import app_commands, Forbidden, HTTPException
import asyncio
from typing import List, Dict, Optional
import requests
from g4f.client import Client
from g4f.Provider import PollinationsAI
from .logging_config import logger
from .sharding import ShardedBotClient
from datetime import datetime, timedelta
import re
import aiohttp
from collections import OrderedDict, defaultdict
import json
import os

class BotClient:
    def __init__(self, config, shard_count: int = 2):
        logger.info("Создаётся новый экземпляр BotClient.")
        self.config = config
        self.client = Client(provider=PollinationsAI)
        self.models_file = "models.json"
        self.intents = discord.Intents.default()
        self.intents.message_content = self.intents.dm_messages = self.intents.members = True
        self.bot = ShardedBotClient(shard_count=shard_count, intents=self.intents)
        self.tree = app_commands.CommandTree(self.bot)
        self.processed_messages = set()
        self.link_cache = OrderedDict()
        self.chat_memory = defaultdict(lambda: OrderedDict()) 
        self.cache_limits = {"links": 10, "messages": 20}
        self.request_settings = {
            "vision_headers": {"Content-Type": "application/json"},
            "rate_limit_delay": 5.0,
            "max_retries": 3,
            "retry_delay_base": 10.0
        }
        self.models = {"text": [], "vision": [], "data": None, "last_update": None}
        self.user_settings = defaultdict(lambda: {"max_response_length": 2000})
        self.load_models_from_json()
        self.initialized = False
        asyncio.ensure_future(self.update_models_periodically())

    def load_models_from_json(self):
        default_models = {"text_models": ["gpt-4o-mini", "gpt-4o", "o1-mini"], "vision_models": ["openai", "openai-large"], "last_update": None}
        try:
            if os.path.exists(self.models_file):
                with open(self.models_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.models.update({
                    "text": data.get("text_models", default_models["text_models"]),
                    "vision": data.get("vision_models", default_models["vision_models"]),
                    "last_update": datetime.fromisoformat(data["last_update"]) if data.get("last_update") else None
                })
                logger.info(f"Модели загружены: text={self.models['text']}, vision={self.models['vision']}")
            else:
                raise FileNotFoundError
        except (json.JSONDecodeError, FileNotFoundError, ValueError) as e:
            logger.error(f"Ошибка загрузки моделей: {e}")
            self.models.update({"text": default_models["text_models"], "vision": default_models["vision_models"], "last_update": None})
            self.save_models_to_json()

    def save_models_to_json(self):
        try:
            with open(self.models_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "text_models": self.models["text"],
                    "vision_models": self.models["vision"],
                    "last_update": self.models["last_update"].isoformat() if self.models["last_update"] else None
                }, f, indent=2, ensure_ascii=False)
            logger.info("Модели сохранены")
        except Exception as e:
            logger.error(f"Ошибка сохранения моделей: {e}")

    async def update_models_periodically(self):
        if not self.initialized:
            await self.fetch_available_models()
            self.initialized = True
        while True:
            try:
                last_update = self.models["last_update"]
                if not last_update or (datetime.now() - last_update).total_seconds() > 1800:
                    await self.fetch_available_models()
                else:
                    logger.info("Модели актуальны, пропускаем обновление")
                await asyncio.sleep(600)
            except Exception as e:
                logger.error(f"Ошибка в цикле обновления моделей: {e}")
                await asyncio.sleep(300)

    async def fetch_available_models(self):
        url = "https://text.pollinations.ai/models"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    self.models["data"] = await response.json()
                    self.models["last_update"] = datetime.now()
                    new_models = {"text": [], "vision": []}
                    if isinstance(self.models["data"], list):
                        for m in self.models["data"]:
                            if m.get('id'):
                                (new_models["vision"] if m.get('supports_vision', False) else new_models["text"]).append(m['id'])
                    elif isinstance(self.models["data"], dict):
                        new_models["text"], new_models["vision"] = self.models["data"].get('text_models', []), self.models["data"].get('vision_models', [])
                    
                    for model_type in ["text", "vision"]:
                        valid_models = await asyncio.gather(*[self.check_model_availability(m, model_type == "vision") for m in new_models[model_type]])
                        updated_models = [m for m, valid in zip(new_models[model_type], valid_models) if valid]
                        self.models[model_type] = updated_models or self.models[model_type]
                        if not updated_models and self.models[model_type]:
                            logger.warning(f"Нет доступных {model_type} моделей, используются сохраненные")
                        elif not self.models[model_type]:
                            logger.error(f"Все {model_type} модели недоступны, включая сохраненные")
                    self.save_models_to_json()
                    logger.info(f"Модели обновлены: text={self.models['text']}, vision={self.models['vision']}")
        except asyncio.TimeoutError:
            logger.error("Таймаут при обновлении моделей")
        except Exception as e:
            logger.error(f"Ошибка получения моделей: {e}")

    async def check_model_availability(self, model: str, is_vision: bool) -> bool:
        try:
            if is_vision:
                async with aiohttp.ClientSession() as session:
                    async with session.head(f"https://text.pollinations.ai/{model}", timeout=aiohttp.ClientTimeout(total=5)) as response:
                        return response.status == 200
            else:
                response = await asyncio.to_thread(self.client.chat.completions.create, model=model, messages=[{"role": "user", "content": "ping"}], max_tokens=1)
                return response.choices[0].message.content is not None
        except Exception as e:
            logger.error(f"Модель {model} недоступна: {e}")
            return False

    async def process_response(self, model_type: str, model: str, messages: List[Dict], max_tokens: int, web_search: bool) -> str | None:
        url = f"https://text.pollinations.ai/{model}" if model_type == "vision" else None
        payload = {"messages": messages, "max_tokens": max_tokens, "stream": False, "web_search": web_search} if url else None
        for attempt in range(self.request_settings["max_retries"]):
            try:
                if url:
                    response = await asyncio.to_thread(requests.post, url, headers=self.request_settings["vision_headers"], json=payload)
                    response.raise_for_status()
                    result = response.json()
                    await asyncio.sleep(self.request_settings["rate_limit_delay"])
                    return result['choices'][0]['message']['content']
                else:
                    response = await asyncio.to_thread(self.client.chat.completions.create, model=model, messages=messages, max_tokens=max_tokens, web_search=web_search)
                    return response.choices[0].message.content
            except Exception as e:
                status = getattr(getattr(e, "response", None), "status_code", None) if url else "429" in str(e)
                if status in (429, 502) if url else "429" in str(e):
                    delay = self.request_settings["retry_delay_base"] * (2 ** attempt)
                    logger.warning(f"Ошибка {status or '429'} для {model} (попытка {attempt + 1}). Ожидание {delay}с")
                    await asyncio.sleep(delay)
                    continue
                logger.error(f"Ошибка модели {model}: {e}")
                return None
        logger.error(f"Не удалось получить ответ от {model} после {self.request_settings['max_retries']} попыток")
        return None

    async def get_context(self, user_id: str, channel: discord.abc.Messageable, limit: int = None) -> List[Dict]:
        user_messages = self.chat_memory[user_id]
        limit = limit or self.cache_limits["messages"]
        memory_context = [
            {
                "role": msg["role"],
                "content": [{"type": "text", "text": msg["content"]}] + ([{"type": "image_url", "image_url": {"url": msg["image"]}}] if "image" in msg else [])
            }
            for msg in list(user_messages.values())[-limit:]
        ]
        if not isinstance(channel, discord.Thread):
            return memory_context
        thread_context = []
        async for msg in channel.history(limit=10):
            if msg.author.bot and msg.author != self.bot.user:
                continue
            role = "assistant" if msg.author == self.bot.user else "user"
            content = [{"type": "text", "text": msg.content}]
            if msg.attachments:
                content.extend({"type": "image_url", "image_url": {"url": a.url}} for a in msg.attachments if a.content_type and a.content_type.startswith("image/"))
            thread_context.append({"role": role, "content": content})
        return memory_context + thread_context[::-1]

    async def add_to_memory(self, user_id: str, message_id: str, role: str, content: str, author: str, image_url: str = None):
        user_messages = self.chat_memory[user_id]
        message_data = {"role": role, "content": content, "author": author, "timestamp": datetime.now().isoformat()}
        if image_url:
            message_data["image"] = image_url
        user_messages[message_id] = message_data
        if len(user_messages) > self.cache_limits["messages"]:
            user_messages.popitem(last=False)

    async def format_response(self, text: str) -> str:
        url_pattern = r'(?<!\]\()https?://[^\s<>\]\)]+[^\s<>\]\)\.,/A-Z0-9-]?[/)](?!\))?'
        urls = re.findall(url_pattern, text, re.IGNORECASE)
        if not urls:
            return text
        validities = await asyncio.gather(*[self.check_link_validity(url) for url in urls])
        for url, is_valid in zip(urls, validities):
            if not re.search(r'\[.*?\]\(<' + re.escape(url) + r'>\)', text):
                match = re.search(r'([^\s\(]+)(?:\s*\(|\s+)' + re.escape(url), text)
                link_text = match.group(1).strip().rstrip(':').rstrip('.') if match else "Link"
                clean_url = url.rstrip('/').rstrip(')')
                formatted_link = f"[{link_text}](<{clean_url}>)" if is_valid else f"~~{link_text}~~"
                text = text.replace(f"{match.group(1)} {url}", formatted_link) if match else text.replace(url, formatted_link)
        return text

    async def check_link_validity(self, url: str) -> bool:
        if url in self.link_cache:
            return self.link_cache[url]
        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(url, timeout=aiohttp.ClientTimeout(total=5), allow_redirects=True) as response:
                    is_valid = response.status == 200
                    self.link_cache[url] = is_valid
                    if len(self.link_cache) > self.cache_limits["links"]:
                        self.link_cache.popitem(last=False)
                    return is_valid
        except Exception as e:
            logger.error(f"Ошибка проверки {url}: {e}")
            self.link_cache[url] = False
            if len(self.link_cache) > self.cache_limits["links"]:
                self.link_cache.popitem(last=False)
            return False

    def needs_web_search(self, text: str, context: List[Dict]) -> bool:
        keywords = [
            "сегодня", "сейчас", "новости", "погода", "статистика", "курс", "цена", "недавно", "текущий", "тренды",
            "вчера", "завтра", "на этой неделе", "в этом месяце", "в этом году", "только что", "прямо сейчас",
            "в реальном времени", "актуально", "последний", "новый", "происходит", "результаты", "прогноз",
            "расписание", "время", "дата", "сколько стоит", "где купить", "обзор", "рейтинг", "отзывы",
            "информация", "данные", "факты", "ситуация", "кризис", "рынок", "биржа", "котировки", "выборы",
            "спорт", "матч", "игра", "турнир", "кино", "фильм", "сериал", "релиз", "премьера", "запуск",
            "анонс", "объявление", "кто такой", "что такое", "где находится", "как добраться", "сколько времени",
            "какой счет", "кто выиграл", "что нового", "праздник", "мероприятие", "концерт", "выставка", "фестиваль"
        ]
        text_lower = (text if isinstance(text, str) else " ".join(str(item) for item in text)).lower()
        return (
            any(kw in text_lower for kw in keywords) or
            any(kw in str(msg.get("content", "")).lower() for msg in context[-3:] if "user" in msg.get("role", "") for kw in keywords) or
            bool(re.search(r"\b(что происходит|какая погода|последние новости|кто выиграл|где сейчас|когда будет)\b", text_lower)) or
            bool(re.search(r"\b(202[0-9]|сейчас|в этом году|на сегодня|на завтра)\b", text_lower))
        )

    async def generate_response(self, user_id: str, message_id: str, text: str, message: discord.Message) -> str | None:
        try:
            context = await self.get_context(user_id, message.channel)
            now = datetime.now()
            system_prompt = f"Ты - дружелюбный чат-бот от Qiota. Отвечай кратко. " \
                          f"Дата: {now:%Y-%m-%d}, время: {now:%H:%M:%S}. Используй интернет при необходимости. Формат: Discord Markdown. Всегда указываей точные данные."
            user_content = [{"type": "text", "text": text}]
            image_url = None
            if message.attachments:
                for a in message.attachments:
                    if a.content_type and a.content_type.startswith("image/"):
                        user_content.append({"type": "image_url", "image_url": {"url": a.url}})
                        image_url = a.url
            messages = context + [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}]
            needs_web = self.needs_web_search(text, context)
            has_image = bool(image_url)
            max_length = self.user_settings[user_id]["max_response_length"]

            response_text = await self._try_generate_response(messages, needs_web, has_image, max_tokens=4000)
            if not response_text:
                return "**Ой!** Нет данных."

            if has_image:
                image_description = await self._try_generate_response(messages, True, True, max_tokens=700)
                if not image_description:
                    return "**Ой!** Не удалось описать изображение."
                if image_description == "content_policy_violation":
                    await self._send_temp_message(message.channel, "Нарушение политики! 🚫", user_id)
                    return None
                search_query = f"На изображении: {image_description}. {text or 'Расскажи подробнее.'}"
                search_messages = context + [{"role": "system", "content": system_prompt}, {"role": "user", "content": [{"type": "text", "text": search_query}]}]
                response_text = await self._try_generate_response(search_messages, True, False, 700) or f"На изображении: {image_description}. Информации нет."

            if response_text == "content_policy_violation":
                await self._send_temp_message(message.channel, "Нарушение политики! 🚫", user_id)
                return None

            final_response = await self.format_response(response_text[:max_length])
            await self.add_to_memory(user_id, message_id, "user", text, message.author.name, image_url)
            await self.add_to_memory(user_id, f"{message_id}_resp", "assistant", final_response, self.bot.user.name)
            logger.info(f"Ответ для {user_id}")
            return final_response
        except Exception as e:
            logger.error(f"Ошибка генерации: {e}")
            await self._send_temp_message(message.channel, "**Упс!** Ошибка.", user_id)
            return None

    async def _try_generate_response(self, messages: List[Dict], needs_web: bool, has_image: bool, max_tokens: int) -> str | None:
        model_type = "vision" if has_image else "text"
        models = self.models[model_type]
        for model in models:
            response = await self.process_response(model_type, model, messages, max_tokens, web_search=needs_web or has_image)
            if response:
                return response
        return None

    async def _send_temp_message(self, channel, content: str, user_id: str, duration: int = 5) -> None:
        try:
            msg = await channel.send(content)
            await asyncio.sleep(duration)
            await msg.delete()
            logger.info(f"Временное сообщение удалено для {user_id}")
        except Exception as e:
            logger.error(f"Ошибка временного сообщения: {e}")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or f"{message.id}-{message.channel.id}" in self.processed_messages:
            return
        should_respond = (
            self.bot.user in message.mentions or
            (message.reference and message.reference.resolved and message.reference.resolved.author == self.bot.user) or
            isinstance(message.channel, (discord.DMChannel, discord.Thread))
        )
        if not should_respond:
            return
        self.processed_messages.add(f"{message.id}-{message.channel.id}")
        async with message.channel.typing():
            content = message.content.replace(f"<@{self.bot.user.id}>", "").strip()
            response = await self.generate_response(str(message.author.id), str(message.id), content, message)
            if response:
                await self._send_split_message(message, response)

    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.content != after.content:
            await self.on_message(after)

    async def _send_split_message(self, message: discord.Message, response: str) -> None:
        max_length = self.user_settings[str(message.author.id)]["max_response_length"]
        parts = [response[i:i+max_length] for i in range(0, len(response), max_length)] if len(response) > max_length else [response]
        for i, part in enumerate(parts):
            try:
                await (message.reply if i == 0 else message.channel.send)(part)
            except (Forbidden, HTTPException) as e:
                logger.error(f"Ошибка отправки: {e}")
                await self._send_temp_message(message.channel, f"**Упс!** {'Нет прав' if isinstance(e, Forbidden) else 'Ошибка API'}.", str(message.author.id))

    async def clear_user_memory(self, user_id: str) -> None:
        if user_id in self.chat_memory:
            self.chat_memory[user_id].clear()
            logger.info(f"Память очищена для {user_id}")

    async def get_user_stats(self, user_id: str) -> Dict[str, int]:
        user_messages = self.chat_memory[user_id]
        return {
            "total_messages": len(user_messages),
            "user_messages": sum(1 for msg in user_messages.values() if msg["role"] == "user"),
            "bot_messages": sum(1 for msg in user_messages.values() if msg["role"] == "assistant")
        }

    async def trim_old_messages(self, user_id: str, days: int = 7) -> None:
        user_messages = self.chat_memory[user_id]
        threshold = datetime.now() - timedelta(days=days)
        for msg_id, msg in list(user_messages.items()):
            if datetime.fromisoformat(msg["timestamp"]) < threshold:
                del user_messages[msg_id]
        logger.info(f"Старые сообщения удалены для {user_id}")

    async def export_memory_to_json(self, user_id: str, filename: str) -> bool:
        try:
            user_messages = dict(self.chat_memory[user_id])
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(user_messages, f, indent=2, ensure_ascii=False)
            logger.info(f"Память экспортирована для {user_id} в {filename}")
            return True
        except Exception as e:
            logger.error(f"Ошибка экспорта памяти для {user_id}: {e}")
            return False