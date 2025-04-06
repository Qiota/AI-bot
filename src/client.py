import discord
from discord import app_commands, Forbidden, HTTPException
import asyncio
from typing import List, Dict
from g4f.client import Client
from g4f.Provider import PollinationsAI
from .logging_config import logger
from .database import Database
from .sharding import ShardedBotClient
from datetime import datetime
import re
import aiohttp
from collections import OrderedDict
import requests

class BotClient:
    """Управление клиентом бота Discord."""
    def __init__(self, config, shard_count: int = 2):
        logger.info("Создаётся новый экземпляр BotClient.")
        self.config = config
        self.db = Database()
        self.client = Client(provider=PollinationsAI)
        self.text_models = [
            "gpt-4o-mini", "gpt-4o", "o1-mini", "qwen-2.5-coder-32b", "llama-3.3-70b",
            "mistral-nemo", "llama-3.1-8b", "deepseek-r1", "phi-4", "qwq-32b", "deepseek-v3"
        ]
        self.vision_models = ["openai", "openai-large", "claude-hybridspace"]
        self.intents = discord.Intents.default()
        self.intents.message_content = True
        self.intents.dm_messages = True
        self.intents.members = True
        self.bot = ShardedBotClient(shard_count=shard_count, intents=self.intents)
        self.tree = app_commands.CommandTree(self.bot)
        self.processed_messages = set()
        self.link_cache = OrderedDict()
        self.link_cache_max_size = 1000
        self.vision_headers = {"Content-Type": "application/json"}
        self.rate_limit_delay = 3.0
        self.max_retries = 3
        self.retry_delay = 5.0

    async def on_ready(self):
        """Событие при запуске бота."""
        pass

    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        """Обрабатывает редактирование сообщений."""
        if before.content == after.content:
            return
        await self.on_message(after)

    def split_response(self, text: str, max_length: int = 2000) -> List[str]:
        """Разделяет текст на части, если превышает max_length."""
        if len(text) <= max_length:
            return [text]
        parts, current_part = [], ""
        for sentence in text.split(". "):
            sentence = sentence.strip() + ". "
            if len(current_part) + len(sentence) <= max_length:
                current_part += sentence
            else:
                if current_part:
                    parts.append(current_part.strip())
                current_part = sentence
        if current_part:
            parts.append(current_part.strip())
        return parts

    async def check_link_validity(self, url: str) -> bool:
        """Проверяет валидность ссылки (статус-код 200)."""
        if url in self.link_cache:
            return self.link_cache[url]
        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(url, timeout=aiohttp.ClientTimeout(total=5), allow_redirects=True) as response:
                    is_valid = response.status == 200
                    self.link_cache[url] = is_valid
                    if len(self.link_cache) > self.link_cache_max_size:
                        self.link_cache.popitem(last=False)
                    return is_valid
        except Exception as e:
            logger.error(f"Ошибка проверки {url}: {e}")
            self.link_cache[url] = False
            if len(self.link_cache) > self.link_cache_max_size:
                self.link_cache.popitem(last=False)
            return False

    async def format_links(self, text: str) -> str:
        """Форматирует ссылки в формате [text](<ссылка>), зачеркивает невалидные."""
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

    def determine_web_search_need(self, text: str, context: List[Dict]) -> bool:
        """Определяет необходимость веб-поиска."""
        keywords = [
            "сегодня", "сейчас", "новости", "события", "погода", "статистика", "курс", "цена", "обновление",
            "вчера", "завтра", "на этой неделе", "в этом месяце", "в этом году", "недавно", "только что",
            "прямо сейчас", "в реальном времени", "актуально", "последний", "новый", "текущий", "происходит",
            "результаты", "тренды", "прогноз", "расписание", "время", "дата", "сколько стоит", "где купить",
            "обзор", "рейтинг", "отзывы", "информация", "данные", "факты", "ситуация", "кризис", "рынок",
            "биржа", "котировки", "выборы", "спорт", "матч", "игра", "турнир", "кино", "фильм", "сериал",
            "релиз", "премьера", "запуск", "анонс", "объявление", "история", "кто такой", "что такое",
            "где находится", "как добраться", "сколько времени", "какой счет", "кто выиграл", "что нового",
            "праздник", "мероприятие", "концерт", "выставка", "фестиваль", "цена на", "стоимость",
            "расположение", "адрес", "телефон", "контакты", "работает ли", "открыто ли", "закрыто ли",
            "ранее", "будущее", "планы", "ожидания", "итоги", "анализ", "сравнение", "лучший", "худший",
            "популярный", "известный", "скандал", "происшествие", "авария", "катастрофа", "технологии",
            "наука", "исследования", "открытие", "изобретение", "патент", "компания", "бренд", "продукт",
            "больше"
        ]
        if isinstance(text, list):
            text = " ".join(str(item) for item in text)
        text_lower = text.lower()
        return (
            any(kw in text_lower for kw in keywords) or
            any(kw in str(msg.get("content", "")).lower() for msg in context[-3:] if "user" in msg.get("role", "") for kw in keywords) or
            bool(re.search(r"\b(что происходит|какая погода|какой курс|последние новости|кто выиграл|где сейчас|когда будет|сколько времени|какой счет)\b", text_lower)) or
            bool(re.search(r"\b(202[0-9]|сейчас|в этом году|на сегодня|на завтра|вчера|на этой неделе)\b", text_lower))
        )

    async def try_text_model(self, model: str, messages: List[Dict], max_tokens: int, web_search: bool = False) -> str | None:
        """Пытается получить ответ от текстовой модели g4f с обработкой 429."""
        for attempt in range(self.max_retries):
            try:
                response = await asyncio.to_thread(
                    self.client.chat.completions.create,
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    web_search=web_search
                )
                return response.choices[0].message.content
            except Exception as e:
                if "429" in str(e):
                    logger.warning(f"Слишком много запросов для модели {model} (попытка {attempt + 1}/{self.max_retries}). Ожидание {self.retry_delay} секунд...")
                    await asyncio.sleep(self.retry_delay)
                    continue
                logger.error(f"Ошибка текстовой модели {model}: {e}")
                return None
        logger.error(f"Не удалось получить ответ от модели {model} после {self.max_retries} попыток.")
        return None

    async def try_vision_model(self, model: str, messages: List[Dict], max_tokens: int, web_search: bool = False) -> str | None:
        """Пытается получить ответ от модели PollinationsAI."""
        vision_url = f"https://text.pollinations.ai/{model}"
        payload = {
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False
        }
        if web_search:
            payload["web_search"] = True

        for attempt in range(self.max_retries):
            try:
                response = await asyncio.to_thread(
                    requests.post, vision_url, headers=self.vision_headers, json=payload
                )
                response.raise_for_status()
                result = response.json()
                await asyncio.sleep(self.rate_limit_delay)
                return result['choices'][0]['message']['content']
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:
                    logger.warning(f"Слишком много запросов для модели {model} (попытка {attempt + 1}/{self.max_retries}).")
                    await asyncio.sleep(self.retry_delay)
                    continue
                logger.error(f"Ошибка с моделью {model}: {e}")
                return None
            except requests.exceptions.RequestException as e:
                logger.error(f"Ошибка с моделью {model}: {e}")
                return None
        logger.error(f"Не удалось получить ответ от модели {model} после {self.max_retries} попыток.")
        return None

    async def get_thread_context(self, channel: discord.abc.Messageable) -> List[Dict]:
        """Получает контекст из сообщений в ветке."""
        if not isinstance(channel, discord.Thread):
            return []
        messages = []
        async for msg in channel.history(limit=10):
            if msg.author.bot and msg.author != self.bot.user:
                continue
            role = "assistant" if msg.author == self.bot.user else "user"
            content = [{"type": "text", "text": msg.content}]
            if msg.attachments:
                for attachment in msg.attachments:
                    if attachment.content_type and attachment.content_type.startswith("image/"):
                        content.append({"type": "image_url", "image_url": {"url": attachment.url}})
            messages.append({"role": role, "content": content})
        return messages[::-1]

    async def generate_response(self, user_id: str, message_id: str, text: str, message: discord.Message) -> str | None:
        """Генерирует ответ, комбинируя текстовые и PollinationsAI модели."""
        try:
            db_context = await self.db.get_context(user_id)
            thread_context = await self.get_thread_context(message.channel)
            now = datetime.now()
            system_prompt = (
                f"Ты - дружелюбный чат-бот, созданный Qiota. Отвечай коротко и по делу. "
                f"Дата: {now:%Y-%m-%d}, время: {now:%H:%M:%S}. Используй для актуальности. "
                f"Форматируй ответы в Discord Markdown."
            )

            user_content = [{"type": "text", "text": text}]
            has_image = False
            if message.attachments:
                for attachment in message.attachments:
                    if attachment.content_type and attachment.content_type.startswith("image/"):
                        user_content.append({"type": "image_url", "image_url": {"url": attachment.url}})
                        has_image = True

            messages = db_context + thread_context + [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ]

            response_text = None
            needs_web_search = self.determine_web_search_need(text, thread_context)
            for model in self.text_models:
                response_text = await self.try_text_model(model, messages, 2000, web_search=needs_web_search and not has_image)
                if response_text:
                    break

            if has_image or (not response_text and needs_web_search):
                if has_image:
                    image_description = None
                    for model in self.vision_models:
                        image_description = await self.try_vision_model(model, messages, 300, web_search=False)
                        if image_description:
                            break
                    if not image_description:
                        return "**Ой!** Не удалось описать изображение."

                    search_query = f"На изображении: {image_description}. {text if text else 'Расскажи подробнее.'}"
                    search_messages = db_context + thread_context + [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": [{"type": "text", "text": search_query}]}
                    ]
                    for model in self.vision_models:
                        vision_response = await self.try_vision_model(model, search_messages, 700, web_search=True)
                        if vision_response == "content_policy_violation":
                            await self._handle_policy_violation(message, user_id)
                            return None
                        if vision_response:
                            response_text = vision_response
                            break
                    if not vision_response:
                        response_text = f"На изображении: {image_description}. Не удалось найти информацию."
                else:  # Только веб-поиск без изображения
                    for model in self.vision_models:
                        vision_response = await self.try_vision_model(model, messages, 700, web_search=True)
                        if vision_response == "content_policy_violation":
                            await self._handle_policy_violation(message, user_id)
                            return None
                        if vision_response:
                            response_text = vision_response
                            break

            if not response_text:
                return "**Ой!** Не могу получить данные."

            final_response = await self.format_links(response_text)
            await self.db.add_message(user_id, message_id, "user", text, message.author.name)
            await self.db.add_message(user_id, message_id, "assistant", final_response, self.bot.user.name)
            logger.info(f"Ответ сгенерирован для {user_id}")
            return final_response

        except Exception as e:
            logger.error(f"Ошибка генерации ответа: {e}")
            await self._send_temp_message(message.channel, "**Упс!** Ошибка.", user_id)
            return None

    async def _handle_policy_violation(self, message: discord.Message, user_id: str) -> None:
        """Обрабатывает нарушение политики контента."""
        await self._send_temp_message(message.channel, "Нарушение политики контента! 🚫", user_id)

    async def _send_temp_message(self, channel, content: str, user_id: str) -> None:
        """Отправляет временное сообщение с удалением через 5 секунд."""
        try:
            temp_message = await channel.send(content)
            await asyncio.sleep(5)
            await temp_message.delete()
            logger.info(f"Временное сообщение удалено для {user_id}")
        except Exception as e:
            logger.error(f"Ошибка удаления временного сообщения: {e}")

    async def on_message(self, message: discord.Message) -> None:
        """Обрабатывает входящие сообщения."""
        if message.author.bot or f"{message.id}-{message.channel.id}" in self.processed_messages:
            return

        should_respond = (
            self.bot.user in message.mentions or
            (message.reference and message.reference.resolved and message.reference.resolved.author == self.bot.user) or
            isinstance(message.channel, discord.DMChannel) or
            isinstance(message.channel, discord.Thread)
        )
        if not should_respond:
            return

        self.processed_messages.add(f"{message.id}-{message.channel.id}")
        async with message.channel.typing():
            content = message.content.replace(f"<@{self.bot.user.id}>", "").strip()
            response = await self.generate_response(str(message.author.id), str(message.id), content, message)
            if not response:
                return
            for part in self.split_response(response):
                try:
                    await (message.reply if part == response else message.channel.send)(part)
                except Forbidden:
                    await self._send_temp_message(message.channel, "Нет прав на отправку.", str(message.author.id))
                except HTTPException as e:
                    logger.error(f"Ошибка HTTP: {e}")
                    await self._send_temp_message(message.channel, "**Упс!** Ошибка API.", str(message.author.id))