import discord
from discord import Forbidden, HTTPException
import asyncio
from typing import List, Dict, Optional, Tuple
from .systemLog import logger
import time
import json
import hashlib
import backoff
import uuid
from aiohttp import ClientSession, ClientTimeout
from g4f.errors import ProviderNotFoundError, StreamNotSupportedError, ResponseError, RateLimitError
from g4f.client import Client
from g4f.Provider import PollinationsAI
import base64
from datetime import datetime, timezone
import traceback
from .commands.restrict import check_bot_access
from .utils.checker import checker
from .client import BotClient
from duckduckgo_search.exceptions import DuckDuckGoSearchException
import tempfile
import g4f.debug
import warnings

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logger.warning("Модуль psutil не установлен, мониторинг памяти отключен")

# Подавление предупреждений pydub
warnings.filterwarnings("ignore", category=RuntimeWarning, module="pydub.utils")

# Отключение debug-режима для минимизации логов
g4f.debug.logging = False

# Фиксированные промпты
DEFAULT_PROMPT = "Ты — Чатбот. Отвечай на своё усмотрение. Текущее время: {now}."
DEFAULT_VISION_PROMPT = "Ты — эксперт по анализу изображений, мемов и культурных отсылок. Текущее время: {now}."

# Триггерные слова для веб-поиска
SEARCH_TRIGGER_WORDS = [
    "найди", "отыщи", "поищи", "разыщи", "ищи", "поиск", "поискни",
    "найди мне", "отыщи мне", "поищи мне", "разыщи мне", "поиск для меня",
    "найди-ка", "отыщи-ка", "поищи-ка", "разыщи-ка", "ищи-ка",
    "поищи пожалуйста", "отыщи пожалуйста", "найди пожалуйста",
    "глянь", "погляди", "посмотри", "просмотри", "подскажи где найти",
    "где найти", "где отыскать", "где посмотреть", "где разыскать",
    "где можно найти", "где можно посмотреть", "где искать",
    "как найти", "как отыскать", "как искать", "как разыскать",
    "что если поискать", "что если найдёшь", "что если найдёшь мне",
    "что можешь найти", "что ты можешь найти", "ты можешь найти",
    "ты не найдешь", "ты не мог бы найти", "не найдется ли", "не найдёшь ли",
    "не мог бы ты найти", "не подскажешь где", "сможешь найти", "ищешь ли",
    "не знаешь где", "не видел ли", "не попадалось ли", "не встречал ли",
    "ищу", "хочу найти", "мне нужно найти", "мне нужно отыскать",
    "нужно найти", "нужно поискать", "надо найти", "надо поискать",
    "интересно где", "можно ли найти", "есть ли где", "есть ли способ найти"
]


class AIChat:
    """Класс для обработки сообщений и генерации AI-ответов в Discord-боте."""
    MAX_MEMORY_SIZE = 10  # Ограничение на количество сообщений в памяти

    def __init__(self, bot_client: BotClient) -> None:
        """Инициализация AIChat с привязкой к BotClient."""
        self.bot_client: BotClient = bot_client
        logger.info("Инициализация AIChat")
        self.bot_client.bot.event(self.on_message)
        self.bot_client.bot.event(self.on_message_edit)
        self._search_semaphore = asyncio.Semaphore(2)

        # Используем временную директорию в памяти для cookies
        self.cookies_dir = tempfile.TemporaryDirectory(prefix="g4f_cookies_")
        try:
            g4f.cookies.set_cookies_dir(self.cookies_dir.name)
            g4f.cookies.read_cookie_files(self.cookies_dir.name)
            logger.info("Cookies настроены во временной директории в памяти")
        except Exception as e:
            logger.error(f"Ошибка настройки cookies: {e}")

    async def __aenter__(self):
        """Вход в контекстный менеджер."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Очистка временной директории cookies."""
        try:
            self.cookies_dir.cleanup()
            logger.info("Временная директория cookies очищена")
        except Exception as e:
            logger.error(f"Ошибка очистки cookies: {e}")

    def normalize_message_content(self, content: Optional[str], default: str = "Произошла ошибка.") -> str:
        """Нормализация содержимого сообщения, чтобы избежать пустых строк и превышения лимита Discord."""
        content = content.strip() if content and content.strip() else default
        if len(content) > 2000:
            logger.warning(f"Содержимое превышает лимит Discord (2000 символов): {len(content)}")
            content = content[:1997] + "..."
        return content

    async def on_message(self, message: discord.Message) -> None:
        """Обработка входящих сообщений."""
        msg_key = f"{message.id}-{message.channel.id}"
        if message.author.bot or msg_key in self.bot_client.processed_messages:
            return
        if isinstance(message.channel, (discord.StageChannel, discord.VoiceChannel)):
            return
        self.bot_client.processed_messages.add(msg_key)
        try:
            user_id = str(message.author.id)
            channel_id = str(message.channel.id)

            if not await self.bot_client.is_bot_mentioned(message):
                return

            if not self.bot_client.models_loaded:
                logger.debug(f"Модели не загружены, сообщение от {user_id} пропущено")
                await self._send_temp_message(message.channel, message.author, "Бот инициализируется.")
                return

            if not await self.bot_client.check_spam(user_id):
                await self._send_temp_message(message.channel, message.author, "Слишком быстро! Подождите 3 секунды.")
                return

            text = message.content.replace(f"<@{self.bot_client.bot.user.id}>", "").strip()
            if text.startswith(".model"):
                await self._handle_model_command(message, text, user_id)
                return

            await self.start_new_conversation(user_id, channel_id, text)
            if isinstance(message.channel, discord.DMChannel):
                result, restriction_reason = await checker.check_user_restriction(message)
                restriction_reason = self.normalize_message_content(restriction_reason, "Ваш доступ к боту ограничен.")
                if result:
                    await self._process_message(message)
                else:
                    logger.debug(f"Пользователь {user_id} ограничен в DM")
                    logger.debug(f"Отправка временного сообщения: {restriction_reason}")
                    await self._send_temp_message(message.channel, message.author, restriction_reason)
            else:
                access_result, access_reason = await check_bot_access(message, self.bot_client)
                restriction_result, restriction_reason = await checker.check_user_restriction(message)
                access_reason = self.normalize_message_content(access_reason, "Доступ к боту ограничен.")
                restriction_reason = self.normalize_message_content(restriction_reason, "Ваш доступ к боту ограничен.")
                if access_result and restriction_result:
                    await self._process_message(message)
                else:
                    reason = access_reason if not access_result else restriction_reason
                    logger.debug(f"Отправка причины ограничения: {reason}")
                    await self._send_temp_message(message.channel, message.author, reason)
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения {msg_key}: {e}\n{traceback.format_exc()}")
            await self._send_temp_message(message.channel, message.author, "Ошибка обработки.")
        finally:
            import gc
            gc.collect()

    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        """Обработка редактирования сообщений."""
        msg_key = f"{after.id}-{after.channel.id}"
        if before.content == after.content or after.author.bot or msg_key not in self.bot_client.processed_messages:
            return
        if isinstance(after.channel, (discord.StageChannel, discord.VoiceChannel)):
            return
        try:
            user_id = str(after.author.id)
            channel_id = str(after.channel.id)

            if not await self.bot_client.is_bot_mentioned(after):
                return

            if not self.bot_client.models_loaded:
                logger.debug(f"Модели не загружены, редактирование от {user_id} пропущено")
                await self._send_temp_message(after.channel, after.author, "Бот инициализируется.")
                return

            if not await self.bot_client.check_spam(user_id):
                await self._send_temp_message(after.channel, after.author, "Слишком быстро! Подождите 3 секунды.")
                return

            text = after.content.replace(f"<@{self.bot_client.bot.user.id}>", "").strip()
            if text.startswith(".model"):
                await self._handle_model_command(after, text, user_id)
                return

            await self.start_new_conversation(user_id, channel_id, text)
            if isinstance(after.channel, discord.DMChannel):
                result, restriction_reason = await checker.check_user_restriction(after)
                restriction_reason = self.normalize_message_content(restriction_reason, "Ваш доступ к боту ограничен.")
                if result:
                    await self._process_edit(after)
                else:
                    logger.debug(f"Отправка временного сообщения: {restriction_reason}")
                    await self._send_temp_message(after.channel, after.author, restriction_reason)
            else:
                access_result, access_reason = await check_bot_access(after, self.bot_client)
                restriction_result, restriction_reason = await checker.check_user_restriction(after)
                access_reason = self.normalize_message_content(access_reason, "Доступ к боту ограничен.")
                restriction_reason = self.normalize_message_content(restriction_reason, "Ваш доступ к боту ограничен.")
                if access_result and restriction_result:
                    await self._process_edit(after)
                else:
                    reason = access_reason if not access_result else restriction_reason
                    logger.debug(f"Отправка причины ограничения: {reason}")
                    await self._send_temp_message(after.channel, after.author, reason)
        except Exception as e:
            logger.error(f"Ошибка обработки редактирования {msg_key}: {e}\n{traceback.format_exc()}")
            await self._send_temp_message(after.channel, after.author, "Ошибка обработки.")
        finally:
            import gc
            gc.collect()

    async def _handle_model_command(self, message: discord.Message, text: str, user_id: str) -> None:
        """Обработка команд управления моделями."""
        parts = text.split()
        if len(parts) < 2:
            await self._send_temp_message(message.channel, message.author, "Используйте: .model list | .model view | .model use text/vision <модель>")
            return

        command = parts[1].lower()
        if command == "list":
            models_list = "\n".join([f"- {m}" for m in self.bot_client.models["text"] + self.bot_client.models["vision"]])
            await message.reply(f"Доступные модели:\n{models_list}")
        elif command == "view":
            current_text = self.bot_client.user_settings[user_id].get("selected_text_model", "openai-fast")
            current_vision = self.bot_client.user_settings[user_id].get("selected_vision_model", "openai-fast")
            await message.reply(f"Текущие модели:\nТекст: {current_text}\nVision: {current_vision}")
        elif command == "use" and len(parts) >= 4:
            model_type = parts[2].lower()
            model_name = parts[3]
            if model_type not in ["text", "vision"]:
                await self._send_temp_message(message.channel, message.author, "Тип модели должен быть 'text' или 'vision'")
                return
            if model_name not in self.bot_client.models[model_type]:
                await self._send_temp_message(message.channel, message.author, f"Модель {model_name} недоступна для {model_type}")
                return
            old_conversation_id = self.bot_client.current_conversation[user_id]["id"]
            del self.bot_client.chat_memory[old_conversation_id]
            del self.bot_client.topic_memory[old_conversation_id]
            new_conversation_id = str(uuid.uuid4())
            self.bot_client.current_conversation[user_id] = {
                "id": new_conversation_id,
                "last_message_time": time.time(),
                "request_count": 0,
                "ttl_seconds": 86400
            }
            self.bot_client.chat_memory[new_conversation_id] = []
            self.bot_client.topic_memory[new_conversation_id] = []
            self.bot_client.user_settings[user_id][f"selected_{model_type}_model"] = model_name
            await self._save_user_settings(user_id)
            await message.reply(f"Модель {model_name} установлена для {model_type}. Контекст разговора очищен.")
        else:
            await self._send_temp_message(message.channel, message.author, "Неверная команда. Используйте: .model list | .model view | .model use text/vision <модель>")

    async def _send_temp_message(self, channel: discord.abc.Messageable, user: discord.User, content: str) -> None:
        """Отправка временного сообщения с последующим удалением."""
        content = self.normalize_message_content(content)
        logger.debug(f"Отправка временного сообщения в канал {getattr(channel, 'id', 'неизвестно')}: {content}")

        try:
            if isinstance(channel, discord.TextChannel):
                permissions = channel.permissions_for(channel.guild.me)
                if not permissions.send_messages:
                    logger.warning(f"Нет прав на отправку сообщений в канал {channel.id}")
                    raise discord.Forbidden(None, "Отсутствуют права на отправку сообщений")
                if not permissions.manage_messages:
                    logger.warning(f"Нет прав на удаление сообщений в канал {channel.id}, отправка без удаления")
                    await channel.send(content)
                    return

            msg = await channel.send(content)
            await asyncio.sleep(10)
            await msg.delete()

        except discord.Forbidden as e:
            logger.error(f"Ошибка отправки сообщения в канал {getattr(channel, 'id', 'неизвестно')}: {e}")
            try:
                dm_channel = user.dm_channel or await user.create_dm()
                msg = await dm_channel.send(content)
                logger.info(f"Сообщение отправлено в личные сообщения пользователю {user.id}")
                await asyncio.sleep(10)
                await msg.delete()
            except discord.Forbidden:
                logger.error(f"Невозможно отправить личное сообщение пользователю {user.id}: Личные сообщения закрыты или бот заблокирован")
            except Exception as dm_e:
                logger.error(f"Ошибка отправки в личные сообщения пользователю {user.id}: {dm_e}\n{traceback.format_exc()}")
        except discord.HTTPException as e:
            logger.error(f"Ошибка HTTP при отправке сообщения в канал {getattr(channel, 'id', 'неизвестно')}: {e}\n{traceback.format_exc()}")

    async def _process_message(self, message: discord.Message) -> None:
        """Обработка сообщения с генерацией ответа."""
        async with message.channel.typing():
            text = message.content.replace(f"<@{self.bot_client.bot.user.id}>", "").strip()
            use_search, query = self._check_trigger_words(text)
            user_id = str(message.author.id)
            message_id = str(message.id)
            parts = await self.generate_response(user_id, message_id, query, message, use_search=use_search)
            if parts:
                await self._send_split_message(message, parts)
            else:
                logger.debug(f"Не удалось сгенерировать ответ для сообщения {message_id}")
                await self._send_temp_message(message.channel, message.author, "Не удалось сгенерировать ответ.")

    async def _process_edit(self, after: discord.Message) -> None:
        """Обработка отредактированного сообщения."""
        if after.id in self.bot_client.message_to_response:
            return

        async with after.channel.typing():
            text = after.content.replace(f"<@{self.bot_client.bot.user.id}>", "").strip()
            use_search, query = self._check_trigger_words(text)
            user_id = str(after.author.id)
            message_id = str(after.id)
            parts = await self.generate_response(user_id, message_id, query, after, is_edit=True, use_search=use_search)
            if parts:
                await self._send_split_message(after, parts)
            else:
                logger.debug(f"Не удалось сгенерировать ответ для отредактированного сообщения {message_id}")
                await self._send_temp_message(after.channel, after.author, "Не удалось сгенерировать ответ.")

    def _check_trigger_words(self, text: str) -> Tuple[bool, str]:
        """Проверка наличия триггерных слов для активации веб-поиска."""
        text_lower = text.lower().strip()
        for trigger in SEARCH_TRIGGER_WORDS:
            if text_lower.startswith(trigger + " ") or text_lower == trigger:
                query = text[len(trigger):].strip()
                logger.debug(f"Обнаружен триггер '{trigger}', запрос: '{query}'")
                return True, query if query else ""
        logger.debug(f"Триггерные слова не найдены в тексте: '{text}'")
        return False, text

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

        return parts if parts else [self.normalize_message_content(None, "Ответ пуст или некорректен.")]

    async def _send_split_message(self, message: discord.Message, parts: List[str]) -> None:
        """Отправка частей ответа в канал."""
        for i, part in enumerate(parts):
            part = self.normalize_message_content(part)
            try:
                sent_msg = await (message.reply(part) if i == 0 else message.channel.send(part))
                self.bot_client.message_to_response[f"{message.id}_{i}" if i > 0 else message.id] = sent_msg.id
                conversation_id = self.bot_client.current_conversation[str(message.author.id)]["id"]
                self.bot_client.chat_memory[conversation_id].append({"role": "assistant", "content": part})
                if len(self.bot_client.chat_memory[conversation_id]) > self.MAX_MEMORY_SIZE:
                    self.bot_client.chat_memory[conversation_id] = self.bot_client.chat_memory[conversation_id][-self.MAX_MEMORY_SIZE:]
                if len(self.bot_client.topic_memory[conversation_id]) > self.MAX_MEMORY_SIZE:
                    self.bot_client.topic_memory[conversation_id] = self.bot_client.topic_memory[conversation_id][-self.MAX_MEMORY_SIZE:]
                await self._save_conversation(str(message.author.id), conversation_id)
            except (Forbidden, HTTPException) as e:
                logger.error(f"Ошибка отправки части {i+1}: {e}\n{traceback.format_exc()}")
                await self._send_temp_message(message.channel, message.author, "Ошибка отправки ответа.")

    async def vision(self, prompt: str, images: List[Tuple[bytes, str]], user_id: str, channel_type: str, channel_id: str, use_search: bool = False, query: str = "") -> Optional[str]:
        """Обработка изображений с использованием PollinationsAI и веб-поиска."""
        for image_data, _ in images:
            if len(image_data) > 10 * 1024 * 1024:
                logger.warning(f"Изображение слишком большое: {len(image_data)} байт")
                return self.normalize_message_content(None, "Изображение слишком большое (макс. 10 МБ).")

        if PSUTIL_AVAILABLE:
            process = psutil.Process()
            mem_before = process.memory_info().rss
            logger.debug(f"Память до обработки vision: {mem_before / 1024 / 1024:.2f} МБ")

        async with ClientSession(timeout=ClientTimeout(total=10)) as session:
            @backoff.on_exception(
                backoff.expo,
                (ProviderNotFoundError, StreamNotSupportedError, ResponseError, RateLimitError, Exception),
                max_tries=3,
                max_time=30,
                jitter=backoff.full_jitter
            )
            async def call_vision_api():
                client = Client(provider=PollinationsAI)
                return await client.chat.completions.create(
                    model=selected_model,
                    messages=messages,
                    images=formatted_images,
                    max_tokens=1000
                )

            try:
                # Подготовка изображений
                formatted_images = []
                for image_data, filename in images:
                    mime_type = "image/jpeg" if filename.lower().endswith((".jpeg", ".jpg")) else "image/webp"
                    base64_image = base64.b64encode(image_data).decode("utf-8")
                    data_uri = f"data:{mime_type};base64,{base64_image}"
                    formatted_images.append([data_uri, filename])

                # Формирование промпта и сообщений
                current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                system_prompt = await self._build_system_prompt(True, user_id)
                messages = [{"role": "system", "content": system_prompt}]

                # Веб-поиск, если требуется
                search_results = ""
                if use_search and query:
                    search_query = query[:50].strip()
                    search_params = self.bot_client.user_settings[user_id].get("search_params", {
                        "max_results": 2,
                        "max_words": 1000,
                        "backend": "auto",
                        "add_text": True,
                        "timeout": 3
                    })
                    try:
                        async with self._search_semaphore:
                            from duckduckgo_search import AsyncDDGS
                            async with AsyncDDGS() as ddgs:
                                results = await ddgs.text(
                                    keywords=search_query,
                                    max_results=search_params["max_results"],
                                    timelimit="y",
                                    safesearch="off",
                                    language="auto"
                                )
                                search_results = "\n".join([f"- {r['title']}: {r['body']}" for r in results])[:search_params["max_words"]]
                                logger.debug(f"Веб-поиск успешен, результаты: {search_results[:100]}...")
                    except DuckDuckGoSearchException as e:
                        logger.error(f"Ошибка веб-поиска для '{search_query}': {e}")
                        search_results = "Веб-поиск недоступен, анализ основан только на изображении и запросе."

                user_message = {"role": "user", "content": prompt}
                if search_results:
                    user_message["content"] += f"\n\n[Результаты веб-поиска]\n{search_results}"
                messages.append(user_message)

                # Проверка кэша
                cache_key = self._generate_cache_key(messages, "vision", user_id, channel_type, channel_id)
                if self.bot_client.firebase_manager:
                    try:
                        cached_response = await self.bot_client.firebase_manager.load_cache(user_id, channel_type, channel_id, cache_key)
                        if cached_response and cached_response.get("timestamp", 0) + self.bot_client.cache_limits["cache_ttl_seconds"] > time.time():
                            logger.debug(f"Кэш найден для {cache_key}")
                            return cached_response["response"]
                    except Exception as e:
                        logger.error(f"Ошибка чтения кэша: {e}")

                # Вызов API
                selected_model = self.bot_client.user_settings[user_id].get("selected_vision_model", "openai-fast")
                logger.debug(f"Используется модель для vision: {selected_model}")
                response = await call_vision_api()

                # Проверка ответа
                if not hasattr(response, "choices") or not response.choices or not response.choices[0].message.content:
                    logger.warning("Пустой или некорректный ответ от PollinationsAI")
                    return self.normalize_message_content(None, "Не удалось обработать изображение.")

                response_text = response.choices[0].message.content.strip()
                logger.debug(f"Успешный ответ от PollinationsAI: {response_text[:100]}...")

                # Сохранение в кэш
                if self.bot_client.firebase_manager:
                    try:
                        await self.bot_client.firebase_manager.save_cache(user_id, channel_type, channel_id, cache_key, {
                            "response": response_text,
                            "timestamp": time.time()
                        })
                    except Exception as e:
                        logger.error(f"Ошибка сохранения кэша: {e}")

                return response_text

            except (ProviderNotFoundError, StreamNotSupportedError) as e:
                logger.error(f"Ошибка провайдера/модели: {e}")
                return self.normalize_message_content(None, "Модель или провайдер недоступны.")
            except RateLimitError as e:
                logger.error(f"Превышен лимит запросов: {e}")
                return self.normalize_message_content(None, "Превышен лимит запросов, попробуйте позже.")
            except ResponseError as e:
                logger.error(f"Ошибка ответа API: {e}")
                return self.normalize_message_content(None, "Ошибка обработки изображения.")
            except Exception as e:
                logger.error(f"Неизвестная ошибка при обработке изображений: {e}\n{traceback.format_exc()}")
                return self.normalize_message_content(None, "Не удалось обработать изображение.")
            finally:
                formatted_images = None
                if PSUTIL_AVAILABLE:
                    mem_after = process.memory_info().rss
                    logger.debug(f"Память после обработки vision: {mem_after / 1024 / 1024:.2f} МБ (разница: {(mem_after - mem_before) / 1024 / 1024:.2f} МБ)")
                import gc
                gc.collect()

    async def generate_response(self, user_id: str, message_id: str, text: str, message: discord.Message, is_edit: bool = False, use_search: bool = False) -> Optional[List[str]]:
        """Генерация ответа с учетом веб-поиска для триггерных слов."""
        if PSUTIL_AVAILABLE:
            process = psutil.Process()
            mem_before = process.memory_info().rss
            logger.debug(f"Память до обработки generate_response: {mem_before / 1024 / 1024:.2f} МБ")

        try:
            if not (text or message.attachments):
                return [self.normalize_message_content(None, "Введите текст или прикрепите изображение.")]

            channel_type = "DM" if isinstance(message.channel, discord.DMChannel) else "guild"
            channel_id = str(message.channel.id)

            has_image = any(a.content_type and a.content_type.startswith("image/") for a in message.attachments)
            if has_image:
                image_attachments = [(await a.read(), a.filename) for a in message.attachments if a.content_type and a.content_type.startswith("image/")]
                response = await self.vision(
                    prompt=text or "Опиши, что на изображениях",
                    images=image_attachments,
                    user_id=user_id,
                    channel_type=channel_type,
                    channel_id=channel_id,
                    use_search=use_search,
                    query=text
                )
                if not response:
                    return [self.normalize_message_content(None, "Не удалось обработать изображение.")]
                return self._split_response(response, self.bot_client.user_settings[user_id]["max_response_length"])

            context = await self.get_context(user_id, message.channel)
            system_prompt = await self._build_system_prompt(has_image, user_id)

            messages = [{"role": "system", "content": system_prompt}] + context
            if text:
                messages.append({"role": "user", "content": text})

            response = await self._generate_response_internal(
                messages=messages,
                has_image=has_image,
                max_tokens=1000,
                user_id=user_id,
                channel_type=channel_type,
                channel_id=channel_id,
                use_search=use_search,
                query=text
            )
            if not response:
                return [self.normalize_message_content(None, "Не удалось обработать текст.")]

            return self._split_response(response, self.bot_client.user_settings[user_id]["max_response_length"])

        except Exception as e:
            logger.error(f"Ошибка генерации ответа для {message_id}: {e}\n{traceback.format_exc()}")
            return [self.normalize_message_content(None, "Ошибка генерации ответа.")]
        finally:
            if PSUTIL_AVAILABLE:
                mem_after = process.memory_info().rss
                logger.debug(f"Память после обработки generate_response: {mem_after / 1024 / 1024:.2f} МБ (разница: {(mem_after - mem_before) / 1024 / 1024:.2f} МБ)")
            import gc
            gc.collect()

    def _generate_cache_key(self, messages: List[Dict], model_type: str, user_id: str, channel_type: str, channel_id: str) -> str:
        """Генерация ключа для кэширования ответа."""
        message_data = json.dumps(messages, sort_keys=True)
        return f"{user_id}:{channel_type}:{channel_id}:{model_type}:{hashlib.sha256(message_data.encode()).hexdigest()}"

    @backoff.on_exception(
        backoff.expo,
        (ProviderNotFoundError, StreamNotSupportedError, ResponseError, RateLimitError, ConnectionError, TimeoutError),
        max_tries=3,
        max_time=30,
        jitter=backoff.full_jitter
    )
    async def _generate_response_internal(self, messages: List[Dict], has_image: bool, max_tokens: int, user_id: str, channel_type: str, channel_id: str, use_search: bool, query: str = "") -> Optional[str]:
        """Генерация ответа с учетом веб-поиска."""
        if PSUTIL_AVAILABLE:
            process = psutil.Process()
            mem_before = process.memory_info().rss
            logger.debug(f"Память до обработки _generate_response_internal: {mem_before / 1024 / 1024:.2f} МБ")

        async with ClientSession(timeout=ClientTimeout(total=30)) as session:
            try:
                model_type = "vision" if has_image else "text"
                selected_model = self.bot_client.user_settings[user_id].get(f"selected_{model_type}_model", "openai-fast")
                logger.debug(f"Используется модель: {selected_model}")
                model_stats = self.bot_client.models["model_stats"][model_type]
                available_models = sorted(
                    [m for m in self.bot_client.models[model_type] if m not in self.bot_client.models["unavailable"][model_type]],
                    key=lambda m: model_stats.get(m, {"success": 0, "failure": 0})["success"] / (model_stats.get(m, {"success": 0, "failure": 0})["failure"] + 1),
                    reverse=True
                )
                if selected_model in available_models:
                    available_models.remove(selected_model)
                    available_models.insert(0, selected_model)

                if not available_models:
                    logger.error(f"Нет доступных моделей для {model_type}")
                    return None

                cache_key = self._generate_cache_key(messages, model_type, user_id, channel_type, channel_id)
                if self.bot_client.firebase_manager:
                    try:
                        cached_response = await self.bot_client.firebase_manager.load_cache(user_id, channel_type, channel_id, cache_key)
                        if cached_response and cached_response.get("timestamp", 0) + self.bot_client.cache_limits["cache_ttl_seconds"] > time.time():
                            logger.debug(f"Кэш найден для {cache_key}")
                            return cached_response["response"]
                    except Exception as e:
                        logger.error(f"Ошибка чтения кэша: {e}\n{traceback.format_exc()}")

                tool_calls = None
                if use_search and query:
                    search_query = query[:50].strip()
                    search_params = self.bot_client.user_settings[user_id].get("search_params", {
                        "max_results": 2,
                        "max_words": 1000,
                        "backend": "auto",
                        "add_text": True,
                        "timeout": 3
                    })
                    if not search_query or search_params["max_results"] < 1 or search_params["max_words"] < 100:
                        logger.warning("Недопустимый поисковый запрос или параметры, search_tool отключен")
                        use_search = False
                    else:
                        tool_calls = [
                            {
                                "function": {
                                    "arguments": {
                                        "query": search_query,
                                        **search_params
                                    },
                                    "name": "search_tool"
                                },
                                "type": "function"
                            }
                        ]
                        logger.debug(f"Активирован search_tool с параметрами: {json.dumps(search_params, ensure_ascii=False)}")

                for model in available_models:
                    queue = self.bot_client.model_queues.get(model)
                    if not queue:
                        logger.error(f"Очередь для модели {model} не найдена")
                        continue

                    if queue.qsize() > 5:
                        logger.warning(f"Очередь {model} переполнена, пропуск")
                        continue

                    for attempt in range(self.bot_client.request_settings["max_retries"]):
                        try:
                            await queue.put((messages, max_tokens, session))
                            logger.debug(f"Запрос добавлен в очередь {model}, размер: {queue.qsize()}")
                            async with self.bot_client.model_semaphores[model]:
                                messages, max_tokens, session = await queue.get()
                                try:
                                    if not self.bot_client.g4f_client:
                                        logger.error("G4FClient не инициализирован")
                                        return None
                                    response = await self.bot_client.g4f_client.chat.completions.create(
                                        model=model,
                                        messages=messages,
                                        max_tokens=max_tokens,
                                        tool_calls=tool_calls if use_search else None,
                                        session=session
                                    )
                                    response_text = response.choices[0].message.content.strip()
                                    if not response_text:
                                        logger.warning(f"Пустой ответ от {model}, попытка {attempt + 1}")
                                        if attempt < self.bot_client.request_settings["max_retries"] - 1:
                                            await asyncio.sleep(self.bot_client.request_settings["retry_delay_base"])
                                            continue
                                        else:
                                            logger.error(f"Пустой ответ от {model} после всех попыток")
                                            break

                                    self.bot_client.models["model_stats"][model_type][model]["success"] += 1
                                    self.bot_client.models["last_successful"][model_type] = model
                                    if self.bot_client.firebase_manager:
                                        await self.bot_client.firebase_manager.save_models({"timestamp": time.time(), **self.bot_client.models})
                                        try:
                                            await self.bot_client.firebase_manager.save_cache(user_id, channel_type, channel_id, cache_key, {
                                                "response": response_text,
                                                "timestamp": time.time()
                                            })
                                        except Exception as e:
                                            logger.error(f"Ошибка сохранения кэша: {e}\n{traceback.format_exc()}")

                                    return response_text

                                finally:
                                    queue.task_done()

                        except DuckDuckGoSearchException as e:
                            logger.error(f"Ошибка DuckDuckGo для {model}, попытка {attempt + 1}: {e}\n{traceback.format_exc()}")
                            if "Ratelimit" in str(e) and attempt < self.bot_client.request_settings["max_retries"] - 1:
                                wait_time = max(10, self.bot_client.request_settings["retry_delay_base"] * (2 ** attempt))
                                logger.info(f"Обнаружен ratelimit, ожидание {wait_time} секунд перед повторной попыткой")
                                await asyncio.sleep(wait_time)
                                continue
                            if use_search and attempt < self.bot_client.request_settings["max_retries"] - 1:
                                logger.info(f"Повторная попытка без search_tool для модели {model}")
                                try:
                                    response = await self.bot_client.g4f_client.chat.completions.create(
                                        model=model,
                                        messages=messages,
                                        max_tokens=max_tokens,
                                        tool_calls=None,
                                        session=session
                                    )
                                    response_text = response.choices[0].message.content.strip()
                                    if response_text:
                                        self.bot_client.models["model_stats"][model_type][model]["success"] += 1
                                        self.bot_client.models["last_successful"][model_type] = model
                                        if self.bot_client.firebase_manager:
                                            await self.bot_client.firebase_manager.save_models({"timestamp": time.time(), **self.bot_client.models})
                                            try:
                                                await self.bot_client.firebase_manager.save_cache(user_id, channel_type, channel_id, cache_key, {
                                                    "response": response_text,
                                                    "timestamp": time.time()
                                                })
                                            except Exception as e:
                                                logger.error(f"Ошибка сохранения кэша: {e}\n{traceback.format_exc()}")
                                        return response_text + "\n\n*Примечание: Веб-поиск временно недоступен из-за ограничений.*"
                                except Exception as retry_e:
                                    logger.error(f"Ошибка повторной попытки без search_tool: {retry_e}\n{traceback.format_exc()}")
                            break

                        except Exception as e:
                            logger.error(f"Ошибка для {model}, попытка {attempt + 1}: {e}\n{traceback.format_exc()}")
                            if attempt < self.bot_client.request_settings["max_retries"] - 1:
                                await asyncio.sleep(self.bot_client.request_settings["retry_delay_base"])
                            else:
                                break

                    logger.error(f"Все попытки для {model} провалились")
                    self.bot_client.models["model_stats"][model_type][model]["failure"] += 1
                    self.bot_client.models["unavailable"][model_type].append(model)
                    if self.bot_client.firebase_manager:
                        await self.bot_client.firebase_manager.save_models({"timestamp": time.time(), **self.bot_client.models})

                logger.error(f"Все модели для {model_type} провалились")
                return None
            finally:
                messages = None
                if PSUTIL_AVAILABLE:
                    mem_after = process.memory_info().rss
                    logger.debug(f"Память после обработки _generate_response_internal: {mem_after / 1024 / 1024:.2f} МБ (разница: {(mem_after - mem_before) / 1024 / 1024:.2f} МБ)")
                import gc
                gc.collect()

    async def _build_system_prompt(self, has_image: bool, user_id: str) -> str:
        """Формирование системного промпта."""
        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        prompt = DEFAULT_VISION_PROMPT if has_image else DEFAULT_PROMPT
        logger.debug(f"Тип prompt: {type(prompt)}, значение: {prompt}")
        return prompt.format(now=current_date)

    async def get_context(self, user_id: str, channel: discord.abc.Messageable) -> List[Dict]:
        """Получение контекста разговора."""
        conversation_id = self.bot_client.current_conversation[user_id]["id"]
        return self.bot_client.chat_memory.get(conversation_id, [])[-self.MAX_MEMORY_SIZE:]

    async def _save_user_settings(self, user_id: str) -> None:
        """Сохранение настроек пользователя."""
        if self.bot_client.firebase_manager:
            try:
                await self.bot_client.firebase_manager.save_user_settings(user_id, self.bot_client.user_settings[user_id])
            except Exception as e:
                logger.error(f"Ошибка сохранения настроек пользователя {user_id}: {e}\n{traceback.format_exc()}")

    async def _save_conversation(self, user_id: str, conversation_id: str) -> None:
        """Сохранение истории разговора."""
        if self.bot_client.firebase_manager:
            try:
                conversation_data = {
                    "chat_memory": self.bot_client.chat_memory[conversation_id][-self.MAX_MEMORY_SIZE:],
                    "topic_memory": self.bot_client.topic_memory[conversation_id][-self.MAX_MEMORY_SIZE:]
                }
                await self.bot_client.firebase_manager.save_conversation(
                    user_id,
                    conversation_id,
                    conversation_data
                )
            except Exception as e:
                logger.error(f"Ошибка сохранения разговора {conversation_id} для пользователя {user_id}: {e}\n{traceback.format_exc()}")
            finally:
                conversation_data = None
                import gc
                gc.collect()

    async def start_new_conversation(self, user_id: str, channel_id: str, initial_message: str) -> None:
        """Начало нового разговора."""
        if user_id not in self.bot_client.current_conversation:
            conversation_id = str(uuid.uuid4())
            self.bot_client.current_conversation[user_id] = {
                "id": conversation_id,
                "last_message_time": time.time(),
                "request_count": 0,
                "ttl_seconds": 86400
            }
            self.bot_client.chat_memory[conversation_id] = []
            self.bot_client.topic_memory[conversation_id] = []
            if initial_message:
                self.bot_client.chat_memory[conversation_id].append({"role": "user", "content": initial_message})
                if len(self.bot_client.chat_memory[conversation_id]) > self.MAX_MEMORY_SIZE:
                    self.bot_client.chat_memory[conversation_id] = self.bot_client.chat_memory[conversation_id][-self.MAX_MEMORY_SIZE:]
            await self._save_conversation(user_id, conversation_id)
