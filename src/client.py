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

class BotClient:
    """Управление клиентом бота Discord."""
    def __init__(self, config, shard_count: int = 2):
        self.config = config
        self.db = Database()
        self.client = Client(provider=PollinationsAI)
        self.models = [
            "gpt-4o-mini",
            "gpt-4o",
            "o1-mini",
            "qwen-2.5-coder-32b",
            "llama-3.3-70b",
            "mistral-nemo",
            "llama-3.1-8b",
            "deepseek-r1",
            "phi-4"
        ]
        self.intents = discord.Intents.default()
        self.intents.message_content = True
        self.intents.dm_messages = True
        self.intents.members = True
        self.bot = ShardedBotClient(shard_count=shard_count, intents=self.intents)
        self.tree = app_commands.CommandTree(self.bot)
        self.processed_messages = set()
        self.link_cache = OrderedDict()
        self.link_cache_max_size = 1000

    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        """Обрабатывает редактирование сообщений."""
        if before.content == after.content:
            return
        await self.on_message(after)

    def split_response(self, text: str, max_length: int = 2000) -> List[str]:
        """Разделяет текст на части, если превышает max_length."""
        if len(text) <= max_length:
            return [text]
        parts = []
        current_part = ""
        sentences = text.split(". ")
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            sentence += ". "
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
                async with session.head(url, timeout=5, allow_redirects=True) as response:
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
        if urls:
            validities = await asyncio.gather(*[self.check_link_validity(url) for url in urls])
        else:
            validities = []
        for url, is_valid in zip(urls, validities):
            if not re.search(r'\[.*?\]\(<' + re.escape(url) + r'>\)', text):
                match = re.search(r'([^\s\(]+)(?:\s*\(|\s+)' + re.escape(url), text)
                link_text = match.group(1).strip().rstrip(':').rstrip('.') if match else "Link"
                clean_url = url.rstrip('/').rstrip(')')
                formatted_link = f"[{link_text}](<{clean_url}>)" if is_valid else f"~~{link_text}~~"
                if match:
                    text = text.replace(f"{match.group(1)} {url}", formatted_link).replace(f"{match.group(1)} ({url})", formatted_link)
                else:
                    text = text.replace(url, formatted_link)
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
            "релиз", "премьера", "запуск", "анонс", "объявление"
        ]
        text_lower = text.lower()
        if any(keyword in text_lower for keyword in keywords):
            return True
        for msg in context[-3:]:
            if "user" in msg.get("role", "") and any(keyword in msg.get("content", "").lower() for keyword in keywords):
                return True
        if re.search(r"\b(что происходит|какая погода|какой курс|последние новости|что нового|кто выиграл|где сейчас|когда будет|сколько времени|какой счет)\b", text_lower):
            return True
        if re.search(r"\b(202[0-9]|сейчас|в этом году|на сегодня|на завтра|вчера|на этой неделе)\b", text_lower):
            return True
        return False

    async def try_model(self, model: str, messages: List[Dict], max_tokens: int, web_search: bool) -> str | None:
        """Пытается получить ответ от указанной модели."""
        try:
            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                stream=False,
                web_search=web_search
            )
            response_text = response.choices[0].message.content.strip()
            if response_text:
                return response_text
        except Exception as e:
            error_msg = str(e)
            if "Response 400: Content policy violation detected" in error_msg:
                return "content_policy_violation"
            logger.error(f"Ошибка с {model} ({'с веб-поиском' if web_search else 'без веб-поиска'}): {e}")
        return None

    async def generate_response(self, user_id: str, message_id: str, text: str, message: discord.Message) -> str:
        """Генерирует ответ на запрос пользователя."""
        try:
            context = await self.db.get_context(user_id)
            current_date = datetime.now().strftime("%Y-%m-%d")
            current_time = datetime.now().strftime("%H:%M:%S")
            system_prompt = (
                "Ты - дружелюбный чат-бот, созданный Qiota. Следуй этим правилам:\n"
                "1. Отвечай **коротко и по делу**. Не добавляй лишнего.\n"
                "2. Используй **дружелюбный тон**. Добавляй смайлики, если уместно.\n"
                "3. Если не знаешь ответа, предложи альтернативу: 'Я не уверен, но могу помочь с...'\n"
                "4. Текущая дата: {current_date}, время: {current_time}. Используй для актуализации.\n"
                "5. Веб-поиск: сначала попробуй ответить без веб-поиска. Если ответ требует актуальных данных или ты не уверен, используй веб-поиск.\n"
                "6. Форматируй ответы с использованием Discord Markdown.\n"
            ).format(current_date=current_date, current_time=current_time)

            messages = context + [{"role": "system", "content": system_prompt}, {"role": "user", "content": text}]
            needs_web_search = self.determine_web_search_need(text, context)

            response_text = None
            if not needs_web_search:
                for model in self.models:
                    response_text = await self.try_model(model, messages, max_tokens=2000, web_search=False)
                    if response_text == "content_policy_violation":
                        temp_message = await message.channel.send("Нарушение политики контента, запрос заблокирован! 🚫")
                        await asyncio.sleep(5)
                        try:
                            await temp_message.delete()
                            logger.info(f"Сообщение о нарушении политики удалено для пользователя {user_id}")
                        except Exception as e:
                            logger.error(f"Ошибка удаления сообщения о нарушении политики: {e}")
                        return None
                    if response_text:
                        break

            if response_text is None or needs_web_search:
                for model in self.models:
                    response_text = await self.try_model(model, messages, max_tokens=700, web_search=True)
                    if response_text == "content_policy_violation":
                        temp_message = await message.channel.send("Нарушение политики контента, запрос заблокирован! 🚫")
                        await asyncio.sleep(5)
                        try:
                            await temp_message.delete()
                            logger.info(f"Сообщение о нарушении политики удалено для пользователя {user_id}")
                        except Exception as e:
                            logger.error(f"Ошибка удаления сообщения о нарушении политики: {e}")
                        return None
                    if response_text:
                        if needs_web_search:
                            response_text = f"{response_text}"
                        break
                else:
                    response_text = "**Ой!** Не могу получить данные. Попробуй позже. 😓"

            if not response_text:
                response_text = "**Ой!** Не знаю, что сказать... Чем могу помочь? 🤔"
            final_response = await self.format_links(response_text)
            await self.db.add_message(user_id, message_id, "user", text)
            await self.db.add_message(user_id, message_id, "assistant", final_response)
            logger.info(f"Ответ успешно сгенерирован для пользователя {user_id}")
            return final_response

        except Exception as e:
            logger.error(f"Ошибка генерации ответа: {e}")
            error_msg = "**Упс!** Произошла ошибка. Попробуй снова! 😅"
            temp_message = await message.channel.send(error_msg)
            await asyncio.sleep(5)
            try:
                await temp_message.delete()
                logger.info(f"Сообщение об ошибке генерации удалено для пользователя {user_id}")
            except Exception as e:
                logger.error(f"Ошибка удаления сообщения об ошибке генерации: {e}")
            return None

    async def on_message(self, message: discord.Message) -> None:
        """Обрабатывает входящие сообщения."""
        if message.author.bot:
            return

        message_key = f"{message.id}-{message.channel.id}"
        if message_key in self.processed_messages:
            return

        should_respond = (
            self.bot.user in message.mentions or
            (message.reference and message.reference.resolved and message.reference.resolved.author == self.bot.user) or
            isinstance(message.channel, discord.DMChannel)
        )

        if not should_respond:
            return

        self.processed_messages.add(message_key)

        async with message.channel.typing():
            content = message.content.replace(f"<@{self.bot.user.id}>", "").strip()
            response = await self.generate_response(str(message.author.id), str(message.id), content, message)
            if response is None:
                return
            parts = self.split_response(response)

        for i, part in enumerate(parts):
            try:
                if i == 0:
                    await message.reply(part)
                else:
                    await message.channel.send(part)
            except Forbidden:
                error_msg = (
                    "Не удалось отправить сообщение. 😓\n"
                    "Проверьте, есть ли у меня права на отправку сообщений в этом канале, "
                    "или разрешите мне отправлять вам личные сообщения в настройках приватности."
                )
                logger.error(f"Ошибка отправки сообщения пользователю {message.author.id}: отсутствуют права")
                temp_message = await message.channel.send(error_msg)
                await asyncio.sleep(5)
                try:
                    await temp_message.delete()
                    logger.info(f"Сообщение об ошибке прав удалено для пользователя {message.author.id}")
                except Exception as e:
                    logger.error(f"Ошибка удаления сообщения об ошибке прав: {e}")
                if not isinstance(message.channel, discord.DMChannel):
                    try:
                        temp_dm = await message.author.send(error_msg)
                        await asyncio.sleep(5)
                        try:
                            await temp_dm.delete()
                            logger.info(f"DM об ошибке прав удалено для пользователя {message.author.id}")
                        except Exception as e:
                            logger.error(f"Ошибка удаления DM об ошибке прав: {e}")
                    except Forbidden:
                        logger.error(f"Не удалось отправить DM пользователю {message.author.id}: отсутствуют права")
            except HTTPException as e:
                logger.error(f"Ошибка HTTP при отправке сообщения: {e}")
                error_msg = "**Упс!** *Не удалось отправить сообщение из-за ошибки Discord API.* Попробуй позже! 😅"
                temp_message = await message.channel.send(error_msg)
                await asyncio.sleep(5)
                try:
                    await temp_message.delete()
                    logger.info(f"Сообщение об ошибке API удалено для пользователя {message.author.id}")
                except Exception as e:
                    logger.error(f"Ошибка удаления сообщения об ошибке API: {e}")
                if not isinstance(message.channel, discord.DMChannel):
                    try:
                        temp_dm = await message.author.send(error_msg)
                        await asyncio.sleep(5)
                        try:
                            await temp_dm.delete()
                            logger.info(f"DM об ошибке API удалено для пользователя {message.author.id}")
                        except Exception as e:
                            logger.error(f"Ошибка удаления DM об ошибке API: {e}")
                    except Forbidden:
                        logger.error(f"Не удалось отправить DM пользователю {message.author.id}: отсутствуют права")