import discord
from discord import app_commands
import asyncio
from typing import List, Dict
from g4f.client import Client
from g4f.Provider import PollinationsAI
from .logging_config import logger
from .database import Database
from .command_registry import register_commands
from datetime import datetime
import re
import aiohttp

class BotClient:
    """Управление клиентом бота Discord."""
    def __init__(self, config):
        self.config = config
        self.db = Database()
        logger.info(f"Используется {'Firebase' if self.db.use_firebase else 'локальное хранилище'} для хранения данных.")
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
        self.current_model_index = 0
        self.intents = discord.Intents.default()
        self.intents.message_content = True
        self.bot = discord.Client(intents=self.intents)
        self.tree = app_commands.CommandTree(self.bot)
        self.processed_messages = set()
        self.link_cache = {}

    async def setup(self) -> None:
        """Настройка бота."""
        self.config.validate()
        self.bot.event(self.on_ready)
        self.bot.event(self.on_message)
        self.bot.event(self.on_message_edit)
        register_commands(self.tree, self)
        logger.info("Бот настроен.")

    async def close(self) -> None:
        """Закрытие бота."""
        await self.bot.close()
        logger.info("Бот закрыт.")

    async def on_ready(self) -> None:
        """Событие, когда бот готов."""
        self.processed_messages.clear()
        await self.tree.sync()
        logger.info(f"Бот {self.bot.user.name} готов!")

    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        """Обрабатывает редактирование сообщений, чтобы избежать дублирования."""
        if before.content == after.content:
            return
        await self.on_message(after)

    def split_response(self, text: str, max_length: int = 2000) -> List[str]:
        """Разделяет текст на части, если он превышает max_length."""
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
        """Проверяет валидность ссылки, возвращает True, если статус-код 200."""
        if url in self.link_cache:
            logger.debug(f"Использую кэшированный результат для {url}: {self.link_cache[url]}")
            return self.link_cache[url]
        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(url, timeout=5, allow_redirects=True) as response:
                    is_valid = response.status == 200
                    self.link_cache[url] = is_valid
                    logger.debug(f"Проверка ссылки {url}: статус {response.status}, валидность: {is_valid}")
                    return is_valid
        except Exception as e:
            logger.error(f"Ошибка проверки ссылки {url}: {e}")
            self.link_cache[url] = False
            return False

    async def format_links(self, text: str) -> str:
        """Форматирует ссылки в тексте в формате [text](<ссылка>), зачеркивает текст невалидных ссылок."""
        url_pattern = r'(?<!\]\()https?://[^\s<>\]\)]+[^\s<>\]\)\.,/A-Z0-9-]?[/)](?!\))?'
        urls = re.findall(url_pattern, text, re.IGNORECASE)
        logger.debug(f"Найденные URL: {urls}")
        for url in urls:
            if not re.search(r'\[.*?\]\(<' + re.escape(url) + r'>\)', text):
                match = re.search(r'([^\s\(]+)(?:\s*\(|\s+)' + re.escape(url), text)
                if match:
                    link_text = match.group(1).strip().rstrip(':').rstrip('.')
                else:
                    link_text = "Link"
                clean_url = url.rstrip('/').rstrip(')')
                is_valid = await self.check_link_validity(clean_url)
                formatted_link = f"[{link_text}](<{clean_url}>)" if is_valid else f"~~{link_text}~~"
                if match:
                    text = text.replace(f"{match.group(1)} {url}", formatted_link).replace(f"{match.group(1)} ({url})", formatted_link)
                else:
                    text = text.replace(url, formatted_link)
                logger.debug(f"Форматированная ссылка: {formatted_link}, валидность: {is_valid}")
        return text

    async def generate_response(self, user_id: str, message_id: str, text: str) -> str:
        """Генерирует ответ на запрос пользователя с использованием одной модели."""
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
            keywords_requiring_web_search = ["сегодня", "сейчас", "новости", "события", "погода", "статистика"]
            needs_web_search = any(keyword in text.lower() for keyword in keywords_requiring_web_search)

            # Используем первую модель для без веб-поиска
            model = self.models[0]
            try:
                logger.info(f"Попытка запроса к модели {model} без веб-поиска")
                response = await asyncio.to_thread(self.client.chat.completions.create,
                    model=model,
                    messages=messages,
                    max_tokens=2000,
                    stream=False,
                    web_search=False
                )
                response_text = response.choices[0].message.content.strip()
                logger.debug(f"Ответ модели {model} (без веб-поиска): {response_text}")
            except Exception as e:
                logger.error(f"Ошибка с моделью {model} (без веб-поиска): {e}")
                response_text = None

            if not response_text or needs_web_search:
                try:
                    logger.info(f"Попытка запроса к модели {model} с веб-поиском")
                    response = await asyncio.to_thread(self.client.chat.completions.create,
                        model=model,
                        messages=messages,
                        max_tokens=700,
                        stream=False,
                        web_search=True
                    )
                    response_text = response.choices[0].message.content.strip()
                    logger.debug(f"Ответ модели {model} (с веб-поиском): {response_text}")
                    if needs_web_search:
                        response_text = f"*Проверяю актуальные данные...*\n{response_text}"
                except Exception as e:
                    logger.error(f"Ошибка с моделью {model} (с веб-поиском): {e}")
                    response_text = "**Ой!** *Не могу получить данные.* Попробуй позже."

            final_response = response_text or "**Ой!** *Не знаю, что сказать.* Чем могу помочь?"
            final_response = await self.format_links(final_response)
            await self.db.add_message(user_id, message_id, "user", text)
            await self.db.add_message(user_id, message_id, "assistant", final_response)
            return final_response

        except Exception as e:
            logger.error(f"Ошибка генерации ответа: {e}")
            return "**Упс!** *Произошла ошибка.* Попробуй снова!"

    async def on_message(self, message: discord.Message) -> None:
        """Обрабатывает входящие сообщения."""
        if message.author.bot:
            return

        message_key = f"{message.id}-{message.channel.id}"
        if message_key in self.processed_messages:
            logger.debug(f"Сообщение {message_key} уже обработано, пропускаем.")
            return

        should_respond = (
            self.bot.user in message.mentions or
            (message.reference and message.reference.resolved and message.reference.resolved.author == self.bot.user) or
            isinstance(message.channel, discord.DMChannel)
        )

        if not should_respond:
            logger.debug(f"Сообщение {message_key} не требует ответа.")
            return

        logger.info(f"Обрабатываю сообщение {message_key} от {message.author}: {message.content}")
        self.processed_messages.add(message_key)

        async with message.channel.typing():
            content = message.content.replace(f"<@{self.bot.user.id}>", "").strip()
            response = await self.generate_response(str(message.author.id), str(message.id), content)
            parts = self.split_response(response)

            for i, part in enumerate(parts):
                if i == 0 and message.reference:
                    await message.reply(part)
                else:
                    await message.channel.send(part)