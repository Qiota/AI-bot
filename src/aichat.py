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
from g4f.errors import ProviderNotFoundError, ModelNotSupportedError, ResponseError, RateLimitError, ResponseStatusError
from g4f.client import Client
from g4f.Provider import PollinationsAI
import re
import base64
from datetime import datetime, timezone
import traceback
from .commands.restrict import check_bot_access
from .utils.checker import checker
from .client import BotClient

# Фиксированные промпты
DEFAULT_PROMPT = "Ты полезный и дружелюбный ассистент. Отвечай кратко, по делу, на русском языке. Учитывай контекст и предоставляй точные ответы. Время: {now}"
DEFAULT_VISION_PROMPT = "Ты эксперт по анализу изображений. Опиши изображение кратко и точно, отвечая на запрос пользователя. Время: {now}"

# Триггерные слова для веб-поиска
SEARCH_TRIGGER_WORDS = [
    "найди", "отыщи", "поищи", "поиск", "разыщи",
    "найди мне", "отыщи мне", "поищи мне",
    "ищи", "найди-ка", "отыщи-ка", "поищи-ка"
]

class AIChat:
    """Класс для обработки сообщений и генерации AI-ответов для BotClient."""

    def __init__(self, bot_client: BotClient) -> None:
        """Инициализация AIChat с ссылкой на BotClient."""
        self.bot_client: BotClient = bot_client
        logger.info("Инициализация AIChat")
        self.bot_client.bot.event(self.on_message)
        self.bot_client.bot.event(self.on_message_edit)

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

            await self.start_new_conversation(user_id, channel_id, message.content)
            if isinstance(message.channel, discord.DMChannel):
                result, restriction_reason = await checker.check_user_restriction(message)
                if result:
                    await self._process_message(message)
                else:
                    logger.debug(f"Пользователь {user_id} ограничен в DM")
                    await self._send_temp_message(message.channel, message.author, restriction_reason or "Ваш доступ к боту ограничен.")
            else:
                access_result, access_reason = await check_bot_access(message, self.bot_client)
                restriction_result, restriction_reason = await checker.check_user_restriction(message)
                if access_result and restriction_result:
                    await self._process_message(message)
                else:
                    reason = access_reason if not access_result else restriction_reason or "Ваш доступ к боту ограничен."
                    await self._send_temp_message(message.channel, message.author, reason)
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения {msg_key}: {e}\n{traceback.format_exc()}")
            await self._send_temp_message(message.channel, message.author, "Ошибка обработки.")

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

            await self.start_new_conversation(user_id, channel_id, after.content)
            if isinstance(after.channel, discord.DMChannel):
                result, restriction_reason = await checker.check_user_restriction(after)
                if result:
                    await self._process_edit(after)
                else:
                    await self._send_temp_message(after.channel, after.author, restriction_reason or "Ваш доступ к боту ограничен.")
            else:
                access_result, access_reason = await check_bot_access(after, self.bot_client)
                restriction_result, restriction_reason = await checker.check_user_restriction(after)
                if access_result and restriction_result:
                    await self._process_edit(after)
                else:
                    reason = access_reason if not access_result else restriction_reason or "Ваш доступ к боту ограничен."
                    await self._send_temp_message(after.channel, after.author, reason)
        except Exception as e:
            logger.error(f"Ошибка обработки редактирования {msg_key}: {e}\n{traceback.format_exc()}")
            await self._send_temp_message(after.channel, after.author, "Ошибка обработки.")

    async def _process_message(self, message: discord.Message) -> None:
        """Обработка сообщения с генерацией ответа."""
        async with message.channel.typing():
            text = message.content.replace(f"<@{self.bot_client.bot.user.id}>", "").strip()
            use_search, query = self._check_trigger_words(text)
            user_id = str(message.author.id)
            message_id = str(message.id)
            parts = await (self.generate_response(user_id, message_id, query, message) if use_search
                          else self.generate_response_no_search(user_id, message_id, text, message))
            if parts:
                await self._send_split_message(message, parts)

    async def _process_edit(self, after: discord.Message) -> None:
        """Обработка отредактированного сообщения."""
        if after.id in self.bot_client.message_to_response:
            return

        async with after.channel.typing():
            text = after.content.replace(f"<@{self.bot_client.bot.user.id}>", "").strip()
            use_search, query = self._check_trigger_words(text)
            user_id = str(after.author.id)
            message_id = str(after.id)
            parts = await (self.generate_response(user_id, message_id, query, after, is_edit=True) if use_search
                          else self.generate_response_no_search(user_id, message_id, text, after, is_edit=True))
            if parts:
                await self._send_split_message(after, parts)

    def _check_trigger_words(self, text: str) -> Tuple[bool, str]:
        """Проверка наличия триггерных слов для веб-поиска и извлечение запроса."""
        text_lower = text.lower().strip()
        for trigger in SEARCH_TRIGGER_WORDS:
            if text_lower.startswith(trigger):
                query = text[len(trigger):].strip()
                if not query and not text[len(trigger):]:
                    return False, text
                return True, query
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

        return parts if parts else ["Ответ пуст или некорректен."]

    async def _send_split_message(self, message: discord.Message, parts: List[str]) -> None:
        """Отправка частей ответа."""
        for i, part in enumerate(parts):
            try:
                sent_msg = await (message.reply(part) if i == 0 else message.channel.send(part))
                self.bot_client.message_to_response[f"{message.id}_{i}" if i > 0 else message.id] = sent_msg.id
                conversation_id = self.bot_client.current_conversation[str(message.author.id)]["id"]
                self.bot_client.chat_memory[conversation_id].append({"role": "assistant", "content": part})
                await self._save_conversation(str(message.author.id), conversation_id)
            except (Forbidden, HTTPException) as e:
                logger.error(f"Ошибка отправки части {i+1}: {e}\n{traceback.format_exc()}")
                await self._send_temp_message(message.channel, message.author, "Ошибка отправки.")

    async def _send_temp_message(self, channel: discord.abc.Messageable, user: discord.User, content: str) -> None:
        """Отправка временного сообщения в канал или в личные сообщения."""
        try:
            # Проверка прав, если канал текстовый
            if isinstance(channel, discord.TextChannel):
                permissions = channel.permissions_for(channel.guild.me)
                if not permissions.send_messages:
                    logger.warning(f"Нет прав на отправку сообщений в канал {channel.id}")
                    raise discord.Forbidden(None, "Отсутствуют права на отправку сообщений")
                if not permissions.manage_messages:
                    logger.warning(f"Нет прав на удаление сообщений в канал {channel.id}, отправка без удаления")
                    await channel.send(content)
                    return

            # Отправка сообщения с удалением через 10 секунд
            msg = await channel.send(content)
            await asyncio.sleep(10)
            await msg.delete()

        except discord.Forbidden as e:
            logger.error(f"Ошибка отправки сообщения в канал {getattr(channel, 'id', 'неизвестно')}: {e}")
            # Попытка отправки в личные сообщения
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

    async def vision(self, prompt: str, images: List[Tuple[bytes, str]], user_id: str, channel_type: str, channel_id: str) -> Optional[str]:
        """Обработка изображений с использованием PollinationsAI."""
        try:
            client = Client(provider=PollinationsAI)
            formatted_images = []
            for image_data, filename in images:
                mime_type = "image/jpeg" if filename.lower().endswith((".jpeg", ".jpg")) else "image/webp"
                base64_image = base64.b64encode(image_data).decode("utf-8")
                data_uri = f"data:{mime_type};base64,{base64_image}"
                formatted_images.append([data_uri, filename])

            current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            system_prompt = f"[PERSONALITY]\n{DEFAULT_VISION_PROMPT.format(now=current_date)}\n[INSTRUCTIONS]\nАнализируй изображение."
            messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]

            cache_key = self._generate_cache_key(messages, "vision", user_id, channel_type, channel_id)
            if self.bot_client.firebase_manager:
                try:
                    cached_response = await self.bot_client.firebase_manager.load_cache(user_id, channel_type, channel_id, cache_key)
                    if cached_response and cached_response.get("timestamp", 0) + self.bot_client.cache_limits["cache_ttl_seconds"] > time.time():
                        logger.debug(f"Кэш найден для {cache_key}")
                        return cached_response["response"]
                except Exception as e:
                    logger.error(f"Ошибка чтения кэша: {e}\n{traceback.format_exc()}")

            response = client.chat.completions.create(
                model="",  # PollinationsAI не требует модели
                messages=messages,
                images=formatted_images,
                max_tokens=2000
            )

            response_text = response.choices[0].message.content.strip()
            if not response_text:
                logger.warning("Пустой ответ от PollinationsAI")
                return None

            if self.bot_client.firebase_manager:
                try:
                    await self.bot_client.firebase_manager.save_cache(user_id, channel_type, channel_id, cache_key, {
                        "response": response_text,
                        "timestamp": time.time()
                    })
                except Exception as e:
                    logger.error(f"Ошибка сохранения кэша: {e}\n{traceback.format_exc()}")

            logger.debug(f"Успешный ответ от PollinationsAI: {response_text[:100]}...")
            return response_text

        except ResponseStatusError as e:
            status_match = re.search(r"Response (\d+):", str(e))
            status_code = int(status_match.group(1)) if status_match else None
            logger.error(f"Ошибка PollinationsAI: {e}, код состояния: {status_code}")
            return None
        except Exception as e:
            logger.error(f"Неизвестная ошибка при обработке изображений: {e}\n{traceback.format_exc()}")
            return None

    async def generate_response(self, user_id: str, message_id: str, text: str, message: discord.Message, is_edit: bool = False) -> Optional[List[str]]:
        """Генерация ответа с использованием search_tool."""
        try:
            if not (text or message.attachments):
                return ["Введите текст или прикрепите изображение."]

            channel_type = "DM" if isinstance(message.channel, discord.DMChannel) else "guild"
            channel_id = str(message.channel.id)

            has_image = any(a.content_type and a.content_type.startswith("image/") for a in message.attachments)
            if has_image:
                image_attachments = [(await a.read(), a.filename) for a in message.attachments if a.content_type and a.content_type.startswith("image/")]
                response = await self.vision(text or "Опиши, что на изображениях", image_attachments, user_id, channel_type, channel_id)
                if not response:
                    return ["Не удалось обработать изображение."]
                return self._split_response(response, self.bot_client.user_settings[user_id]["max_response_length"])

            context = await self.get_context(user_id, message.channel)
            system_prompt = await self._build_system_prompt(has_image)

            messages = [{"role": "system", "content": system_prompt}] + context
            if text:
                messages.append({"role": "user", "content": text})

            response = await self._generate_response_internal(messages, has_image, 2000, user_id, channel_type, channel_id, use_search=True, query=text)
            if not response:
                return ["Не удалось обработать текст."]

            return self._split_response(response, self.bot_client.user_settings[user_id]["max_response_length"])
        except Exception as e:
            logger.error(f"Ошибка генерации ответа для {message_id}: {e}\n{traceback.format_exc()}")
            return ["Ошибка генерации ответа."]

    async def generate_response_no_search(self, user_id: str, message_id: str, text: str, message: discord.Message, is_edit: bool = False) -> Optional[List[str]]:
        """Генерация ответа без использования веб-поиска."""
        try:
            if not (text or message.attachments):
                return ["Введите текст или прикрепите изображение."]

            channel_type = "DM" if isinstance(message.channel, discord.DMChannel) else "guild"
            channel_id = str(message.channel.id)

            has_image = any(a.content_type and a.content_type.startswith("image/") for a in message.attachments)
            if has_image:
                image_attachments = [(await a.read(), a.filename) for a in message.attachments if a.content_type and a.content_type.startswith("image/")]
                response = await self.vision(text or "Опиши, что на изображениях", image_attachments, user_id, channel_type, channel_id)
                if not response:
                    return ["Не удалось обработать изображение."]
                return self._split_response(response, self.bot_client.user_settings[user_id]["max_response_length"])

            context = await self.get_context(user_id, message.channel)
            system_prompt = await self._build_system_prompt(has_image)

            messages = [{"role": "system", "content": system_prompt}] + context
            if text:
                messages.append({"role": "user", "content": text})

            response = await self._generate_response_internal(messages, has_image, 2000, user_id, channel_type, channel_id, use_search=False)
            if not response:
                return ["Не удалось обработать текст."]

            return self._split_response(response, self.bot_client.user_settings[user_id]["max_response_length"])
        except Exception as e:
            logger.error(f"Ошибка генерации ответа без поиска для {message_id}: {e}\n{traceback.format_exc()}")
            return ["Ошибка генерации ответа."]

    def _generate_cache_key(self, messages: List[Dict], model_type: str, user_id: str, channel_type: str, channel_id: str) -> str:
        """Генерация ключа кэша."""
        message_data = json.dumps(messages, sort_keys=True)
        return f"{user_id}:{channel_type}:{channel_id}:{model_type}:{hashlib.sha256(message_data.encode()).hexdigest()}"

    @backoff.on_exception(
        backoff.expo,
        (ProviderNotFoundError, ModelNotSupportedError, ResponseError, RateLimitError, ConnectionError, TimeoutError),
        max_tries=5,
        max_time=60,
        factor=2,
        jitter=backoff.full_jitter
    )
    async def _generate_response_internal(self, messages: List[Dict], has_image: bool, max_tokens: int, user_id: str, channel_type: str, channel_id: str, use_search: bool, query: str = "") -> Optional[str]:
        """Внутренняя функция для генерации ответа с или без search_tool."""
        model_type = "vision" if has_image else "text"
        available_models = [m for m in self.bot_client.models[model_type] if m not in self.bot_client.models["unavailable"][model_type]]

        if not available_models:
            logger.error(f"Нет доступных моделей для {model_type}")
            return None

        model_stats = self.bot_client.models["model_stats"][model_type]
        sorted_models = sorted(
            available_models,
            key=lambda m: model_stats.get(m, {"success": 0, "failure": 0})["success"],
            reverse=True
        )

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
            search_query = query[:100].strip()
            tool_calls = [
                {
                    "function": {
                        "arguments": {
                            "query": search_query,
                            "max_results": 5,
                            "max_words": 2500,
                            "backend": "auto",
                            "add_text": True,
                            "timeout": 5
                        },
                        "name": "search_tool"
                    },
                    "type": "function"
                }
            ]

        timeout = ClientTimeout(total=60)
        headers = {"User-Agent": "BotClient/1.0 (DiscordBot; PollinationsAI)"}
        async with ClientSession(timeout=timeout, headers=headers) as session:
            for selected_model in sorted_models:
                queue = self.bot_client.model_queues.get(selected_model)
                if not queue:
                    logger.error(f"Очередь для модели {selected_model} не найдена")
                    continue

                for attempt in range(self.bot_client.request_settings["max_retries"]):
                    try:
                        await queue.put((messages, max_tokens, session))
                        logger.debug(f"Запрос добавлен в очередь {selected_model}, размер: {queue.qsize()}")
                        async with self.bot_client.model_semaphores[selected_model]:
                            messages, max_tokens, session = await queue.get()
                            try:
                                if not self.bot_client.g4f_client:
                                    logger.error("G4FClient не инициализирован")
                                    return None
                                response = await self.bot_client.g4f_client.chat.completions.create(
                                    model=selected_model,
                                    messages=messages,
                                    max_tokens=max_tokens,
                                    tool_calls=tool_calls if use_search else None,
                                    session=session
                                )
                                response_text = response.choices[0].message.content.strip()
                                if not response_text:
                                    logger.warning(f"Пустой ответ от {selected_model}, попытка {attempt + 1}")
                                    raise ValueError("Пустой ответ")

                                self.bot_client.models["model_stats"][model_type][selected_model]["success"] += 1
                                self.bot_client.models["last_successful"][model_type] = selected_model
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

                    except ResponseStatusError as e:
                        status_match = re.search(r"Response (\d+):", str(e))
                        status_code = int(status_match.group(1)) if status_match else None
                        if status_code and status_code >= 500 and attempt < self.bot_client.request_settings["max_retries"] - 1:
                            logger.warning(f"Ошибка сервера {status_code}, попытка {attempt + 1}")
                            await asyncio.sleep(self.bot_client.request_settings["retry_delay_base"])
                        else:
                            logger.error(f"Ошибка {selected_model} после {attempt + 1} попыток: {e}")
                            break

                    except (ProviderNotFoundError, ModelNotSupportedError, ResponseError, RateLimitError, ConnectionError, TimeoutError) as e:
                        logger.error(f"Ошибка G4F для {selected_model}, попытка {attempt + 1}: {e}\n{traceback.format_exc()}")
                        if attempt < self.bot_client.request_settings["max_retries"] - 1:
                            await asyncio.sleep(self.bot_client.request_settings["retry_delay_base"])
                        else:
                            break

                    except Exception as e:
                        logger.error(f"Неизвестная ошибка для {selected_model}: {e}\n{traceback.format_exc()}")
                        break

                logger.error(f"Все попытки для {selected_model} провалились")
                self.bot_client.models["model_stats"][model_type][selected_model]["failure"] += 1
                self.bot_client.models["unavailable"][model_type].append(selected_model)
                if self.bot_client.firebase_manager:
                    await self.bot_client.firebase_manager.save_models({"timestamp": time.time(), **self.bot_client.models})
                await asyncio.sleep(3)

        logger.error(f"Все модели ({model_type}) не смогли обработать запрос")
        return None

    async def get_context(self, user_id: str, channel: discord.abc.Messageable) -> List[Dict]:
        """Получение контекста разговора."""
        conversation_id = self.bot_client.current_conversation[user_id]["id"]

        if conversation_id in self.bot_client.chat_memory and self.bot_client.chat_memory[conversation_id]:
            messages = self.bot_client.chat_memory[conversation_id]
            context = [{"role": msg["role"], "content": msg["content"]} for msg in messages[-self.bot_client.cache_limits["messages"]:] if msg["content"]]
            return context

        if self.bot_client.firebase_manager:
            try:
                conversation_data = await self.bot_client.firebase_manager.load_conversation(user_id, conversation_id)
                if conversation_data:
                    self.bot_client.chat_memory[conversation_id] = conversation_data.get("messages", [])
                    self.bot_client.topic_memory[conversation_id] = conversation_data.get("topics", [])
                    context = [{"role": msg["role"], "content": msg["content"]} for msg in self.bot_client.chat_memory[conversation_id][-self.bot_client.cache_limits["messages"]:] if msg["content"]]
                    return context
            except Exception as e:
                logger.error(f"Ошибка загрузки контекста: {e}\n{traceback.format_exc()}")
        return []

    async def _build_system_prompt(self, has_image: bool) -> str:
        """Построение системного промпта."""
        try:
            current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            prompt = f"[PERSONALITY]\n{(DEFAULT_VISION_PROMPT if has_image else DEFAULT_PROMPT).format(now=current_date)}\n[INSTRUCTIONS]\n{'Анализируй изображение.' if has_image else 'Отвечай.'}"
            return prompt
        except Exception as e:
            logger.error(f"Ошибка построения промпта: {e}\n{traceback.format_exc()}")
            return f"[PERSONALITY]\n{(DEFAULT_VISION_PROMPT if has_image else DEFAULT_PROMPT).format(now=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'))}\n[INSTRUCTIONS]\n{'Анализируй изображение.' if has_image else 'Отвечай.'}"

    def _adjust_conversation_ttl(self, user_id: str) -> None:
        """Настройка TTL разговора."""
        conversation = self.bot_client.current_conversation[user_id]
        request_count = conversation["request_count"]

        if request_count < 5:
            conversation["ttl_seconds"] = self.bot_client.cache_limits["min_conversation_ttl"]
        elif request_count < 20:
            conversation["ttl_seconds"] = 86400
        else:
            conversation["ttl_seconds"] = self.bot_client.cache_limits["max_conversation_ttl"]

    async def start_new_conversation(self, user_id: str, channel_id: str, content: str) -> None:
        """Запуск новой беседы."""
        conversation = self.bot_client.current_conversation[user_id]
        conversation_id = conversation["id"]
        current_time = time.time()

        if (current_time - conversation["last_message_time"]) > conversation["ttl_seconds"]:
            conversation_id = str(uuid.uuid4())
            self.bot_client.current_conversation[user_id] = {
                "id": conversation_id,
                "last_message_time": current_time,
                "request_count": 0,
                "ttl_seconds": 86400
            }
            self.bot_client.chat_memory[conversation_id] = []
            self.bot_client.topic_memory[conversation_id] = []

        conversation = self.bot_client.current_conversation[user_id]
        conversation["last_message_time"] = current_time
        conversation["request_count"] += 1

        self._adjust_conversation_ttl(user_id)
        self.bot_client.chat_memory[conversation_id].append({"role": "user", "content": content})
        if len(self.bot_client.chat_memory[conversation_id]) > self.bot_client.cache_limits["messages"]:
            self.bot_client.chat_memory[conversation_id] = self.bot_client.chat_memory[conversation_id][-self.bot_client.cache_limits["messages"]:]

        await self._save_conversation(user_id, conversation_id)

    async def _save_conversation(self, user_id: str, conversation_id: str) -> None:
        """Сохранение разговора."""
        if self.bot_client.firebase_manager:
            try:
                conversation_data = {
                    "messages": self.bot_client.chat_memory[conversation_id],
                    "topics": self.bot_client.topic_memory[conversation_id],
                    "last_message_time": self.bot_client.current_conversation[user_id]["last_message_time"],
                    "ttl_seconds": self.bot_client.current_conversation[user_id]["ttl_seconds"]
                }
                await self.bot_client.firebase_manager.save_conversation(user_id, conversation_id, conversation_data)
            except Exception as e:
                logger.error(f"Ошибка сохранения разговора: {e}\n{traceback.format_exc()}")