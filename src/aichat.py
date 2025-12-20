import discord
import asyncio
from typing import List, Dict, Optional, Tuple
from .systemLog import logger
import time
import json
import hashlib
import uuid
from aiohttp import ClientSession, ClientTimeout
from g4f.errors import ProviderNotFoundError, ModelNotSupportedError, ResponseError, RateLimitError
from g4f.client import Client
from g4f.Provider import PollinationsAI
import base64
from datetime import datetime, timezone
from .commands.restrict import check_bot_access
from .utils.checker import checker
from .client import BotClient
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
    """Класс для обработки сообщений и генерации AI-ответов в Discord-боте с поддержкой g4f[search]."""
    MAX_MEMORY_SIZE = 10 

    def __init__(self, bot_client: BotClient) -> None:
        """Инициализация AIChat с привязкой к BotClient."""
        self.bot_client: BotClient = bot_client
        logger.info("Инициализация AIChat с поддержкой веб-поиска")
        self.bot_client.bot.event(self.on_message)
        self.bot_client.bot.event(self.on_message_edit)

        # Используем временную директорию в памяти для cookies
        self.cookies_dir = tempfile.TemporaryDirectory(prefix="g4f_cookies_")
        try:
            g4f.cookies.set_cookies_dir(self.cookies_dir.name)
            g4f.cookies.read_cookie_files(self.cookies_dir.name)
            logger.info("Cookies настроены во временной директории в памяти")
        except Exception as e:
            logger.error(f"Ошибка настройки cookies: {e}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            self.cookies_dir.cleanup()
            logger.info("Временная директория cookies очищена")
        except Exception as e:
            logger.error(f"Ошибка очистки cookies: {e}")

    def normalize_message_content(self, content: Optional[str], default: str = "Произошла ошибка.") -> str:
        content = content.strip() if content and content.strip() else default
        if len(content) > 2000:
            content = content[:1997] + "..."
        return content
    
    def _truncate_messages(self, messages: List[Dict], max_chars: int = 3800) -> List[Dict]:
        if not messages:
            return messages
        system_msg = messages[0] if messages[0]["role"] == "system" else None
        current_msgs = messages[1:] if system_msg else messages
        
        while len(json.dumps(messages, ensure_ascii=False)) > max_chars and len(current_msgs) > 1:
            current_msgs.pop(0)
            messages = [system_msg] + current_msgs if system_msg else current_msgs
        return messages

    async def on_message(self, message: discord.Message) -> None:
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
                result, reason = await checker.check_user_restriction(message)
                if result: await self._process_message(message)
                else: await self._send_temp_message(message.channel, message.author, self.normalize_message_content(reason))
            else:
                acc_res, acc_reaz = await check_bot_access(message, self.bot_client)
                restr_res, restr_reaz = await checker.check_user_restriction(message)
                if acc_res and restr_res:
                    await self._process_message(message)
                else:
                    reason = acc_reaz if not acc_res else restr_reaz
                    await self._send_temp_message(message.channel, message.author, self.normalize_message_content(reason))
        except Exception as e:
            logger.error(f"Ошибка on_message: {e}")
            await self._send_temp_message(message.channel, message.author, "Ошибка обработки.")

    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        msg_key = f"{after.id}-{after.channel.id}"
        if before.content == after.content or after.author.bot or msg_key not in self.bot_client.processed_messages:
            return
        try:
            user_id = str(after.author.id)
            channel_id = str(after.channel.id)
            if not await self.bot_client.is_bot_mentioned(after): return
            if not self.bot_client.models_loaded: return

            text = after.content.replace(f"<@{self.bot_client.bot.user.id}>", "").strip()
            await self.start_new_conversation(user_id, channel_id, text)
            
            if isinstance(after.channel, discord.DMChannel):
                result, reason = await checker.check_user_restriction(after)
                if result: await self._process_edit(after)
            else:
                acc_res, _ = await check_bot_access(after, self.bot_client)
                restr_res, _ = await checker.check_user_restriction(after)
                if acc_res and restr_res: await self._process_edit(after)
        except Exception as e:
            logger.error(f"Ошибка on_message_edit: {e}")

    async def _handle_model_command(self, message: discord.Message, text: str, user_id: str) -> None:
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
            model_type, model_name = parts[2].lower(), parts[3]
            if model_type not in ["text", "vision"] or model_name not in self.bot_client.models[model_type]:
                await self._send_temp_message(message.channel, message.author, "Некорректный тип или название модели.")
                return
            
            old_conv_id = self.bot_client.current_conversation[user_id]["id"]
            for target in [self.bot_client.chat_memory, self.bot_client.topic_memory]:
                if old_conv_id in target: del target[old_conv_id]
            
            new_id = str(uuid.uuid4())
            self.bot_client.current_conversation[user_id] = {"id": new_id, "last_message_time": time.time(), "request_count": 0, "ttl_seconds": 86400}
            self.bot_client.chat_memory[new_id], self.bot_client.topic_memory[new_id] = [], []
            self.bot_client.user_settings[user_id][f"selected_{model_type}_model"] = model_name
            await self._save_user_settings(user_id)
            await message.reply(f"Модель {model_name} установлена. Контекст очищен.")

    async def _send_temp_message(self, channel: discord.abc.Messageable, user: discord.User, content: str) -> None:
        content = self.normalize_message_content(content)
        try:
            if isinstance(channel, discord.TextChannel):
                perms = channel.permissions_for(channel.guild.me)
                if not perms.send_messages: raise discord.Forbidden(None, "No send perms")
                if not perms.manage_messages:
                    await channel.send(content)
                    return
            msg = await channel.send(content)
            await asyncio.sleep(10)
            await msg.delete()
        except Exception:
            try:
                dm = user.dm_channel or await user.create_dm()
                msg = await dm.send(content)
                await asyncio.sleep(10)
                await msg.delete()
            except: pass

    async def _process_message(self, message: discord.Message) -> None:
        async with message.channel.typing():
            text = message.content.replace(f"<@{self.bot_client.bot.user.id}>", "").strip()
            use_search, _ = self._check_trigger_words(text)
            parts = await self.generate_response(str(message.author.id), str(message.id), text, message, use_search=use_search)
            if parts: await self._send_split_message(message, parts)

    async def _process_edit(self, after: discord.Message) -> None:
        if after.id in self.bot_client.message_to_response: return
        async with after.channel.typing():
            text = after.content.replace(f"<@{self.bot_client.bot.user.id}>", "").strip()
            use_search, _ = self._check_trigger_words(text)
            parts = await self.generate_response(str(after.author.id), str(after.id), text, after, is_edit=True, use_search=use_search)
            if parts: await self._send_split_message(after, parts)

    def _check_trigger_words(self, text: str) -> Tuple[bool, str]:
        """Проверка триггеров. Для g4f[search] лучше передавать оригинальный текст целиком."""
        text_lower = text.lower().strip()
        for trigger in SEARCH_TRIGGER_WORDS:
            if trigger in text_lower:
                logger.debug(f"Обнаружен триггер поиска: '{trigger}'")
                return True, text
        return False, text

    def _split_response(self, response: str, max_length: int = 2000) -> List[str]:
        parts, remaining = [], response
        while remaining:
            if len(remaining) <= max_length:
                if remaining.strip(): parts.append(remaining)
                break
            split_index = -1
            for sep in [". ", "! ", "? ", "; "]:
                idx = remaining[:max_length].rfind(sep)
                if idx != -1 and idx > split_index: split_index = idx + len(sep)
            if split_index == -1: split_index = max_length
            part = remaining[:split_index]
            if part.strip(): parts.append(part)
            remaining = remaining[split_index:]
        return parts if parts else ["Ответ пуст."]

    async def _send_split_message(self, message: discord.Message, parts: List[str]) -> None:
        for i, part in enumerate(parts):
            try:
                sent_msg = await (message.reply(part) if i == 0 else message.channel.send(part))
                self.bot_client.message_to_response[f"{message.id}_{i}" if i > 0 else message.id] = sent_msg.id
                conv_id = self.bot_client.current_conversation[str(message.author.id)]["id"]
                self.bot_client.chat_memory[conv_id].append({"role": "assistant", "content": part})
                self.bot_client.chat_memory[conv_id] = self.bot_client.chat_memory[conv_id][-self.MAX_MEMORY_SIZE:]
                await self._save_conversation(str(message.author.id), conv_id)
            except Exception as e:
                logger.error(f"Ошибка отправки: {e}")

    async def vision(self, prompt: str, images: List[Tuple[bytes, str]], user_id: str, channel_type: str, channel_id: str, use_search: bool = False, query: str = "") -> Optional[str]:
        """Обработка изображений с поддержкой веб-поиска g4f."""
        if PSUTIL_AVAILABLE:
            process = psutil.Process()
            mem_before = process.memory_info().rss

        async with ClientSession(timeout=ClientTimeout(total=10)) as session:
            @backoff.on_exception(
                backoff.expo,
                (ProviderNotFoundError, ModelNotSupportedError, ResponseError, RateLimitError, Exception),
                max_tries=3,
                max_time=30,
                jitter=backoff.full_jitter
            )
            def call_vision_api():
                client = Client(provider=PollinationsAI)
                return client.chat.completions.create(
                    model=selected_model,
                    messages=messages,
                    images=formatted_images,
                    web_search=use_search,
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
                response = call_vision_api()

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

            except (ProviderNotFoundError, ModelNotSupportedError) as e:
                logger.error(f"Ошибка провайдера/модели: {e}")
                return self.normalize_message_content(None, "Модель или провайдер недоступны.")
            except RateLimitError as e:
                logger.error(f"Превышен лимит запросов: {e}")
                return self.normalize_message_content(None, "Превышен лимит запросов, попробуйте позже.")
            except ResponseError as e:
                logger.error(f"Ошибка ответа API: {e}")
                return self.normalize_message_content(None, "Ошибка обработки изображения.")
            except Exception as e:
                logger.error(f"Vision error: {e}")
                return None
            finally:
                import gc
                gc.collect()

    async def generate_response(self, user_id: str, message_id: str, text: str, message: discord.Message, is_edit: bool = False, use_search: bool = False) -> Optional[List[str]]:
        if PSUTIL_AVAILABLE:
            process = psutil.Process()
            mem_before = process.memory_info().rss

        try:
            if not (text or message.attachments):
                return [self.normalize_message_content("Введите текст или прикрепите фото.")]

            c_type = "DM" if isinstance(message.channel, discord.DMChannel) else "guild"
            c_id = str(message.channel.id)

            has_image = any(a.content_type and a.content_type.startswith("image/") for a in message.attachments)
            if has_image:
                attachments = [(await a.read(), a.filename) for a in message.attachments if a.content_type.startswith("image/")]
                resp = await self.vision(text or "Опиши фото", attachments, user_id, c_type, c_id, use_search, text)
                if not resp: return ["Не удалось обработать изображение."]
                return self._split_response(resp, self.bot_client.user_settings[user_id]["max_response_length"])

            context = await self.get_context(user_id, message.channel)
            messages = [{"role": "system", "content": await self._build_system_prompt(False, user_id)}] + context
            if text: messages.append({"role": "user", "content": text})

            resp = await self._generate_response_internal(messages, False, 1000, user_id, c_type, c_id, use_search, text)
            if not resp: return ["Не удалось сгенерировать ответ."]
            return self._split_response(resp, self.bot_client.user_settings[user_id]["max_response_length"])

        except Exception as e:
            logger.error(f"Error in generate_response: {e}")
            return ["Ошибка генерации."]
        finally:
            import gc
            gc.collect()

    def _generate_cache_key(self, messages: List[Dict], model_type: str, user_id: str, channel_type: str, channel_id: str) -> str:
        data = json.dumps(messages, sort_keys=True)
        return f"{user_id}:{channel_type}:{channel_id}:{model_type}:{hashlib.sha256(data.encode()).hexdigest()}"

    @backoff.on_exception(
        backoff.expo,
        (ProviderNotFoundError, ModelNotSupportedError, ResponseError, RateLimitError, ConnectionError, TimeoutError),
        max_tries=3,
        max_time=30,
        jitter=backoff.full_jitter
    )
    async def _generate_response_internal(self, messages: List[Dict], has_image: bool, max_tokens: int, user_id: str, channel_type: str, channel_id: str, use_search: bool, query: str = "") -> Optional[str]:
        """Внутренняя генерация с автоматическим веб-поиском через g4f."""
        if PSUTIL_AVAILABLE:
            process = psutil.Process()
            mem_before = process.memory_info().rss

        async with ClientSession(timeout=ClientTimeout(total=30)) as session:
            try:
                model_type = "vision" if has_image else "text"
                selected_model = self.bot_client.user_settings[user_id].get(f"selected_{model_type}_model", "openai-fast")
                
                model_stats = self.bot_client.models["model_stats"][model_type]
                available_models = sorted(
                    [m for m in self.bot_client.models[model_type] if m not in self.bot_client.models["unavailable"][model_type]],
                    key=lambda m: model_stats.get(m, {"success": 0, "failure": 0})["success"] / (model_stats.get(m, {"success": 0, "failure": 0})["failure"] + 1),
                    reverse=True
                )
                if selected_model in available_models:
                    available_models.remove(selected_model); available_models.insert(0, selected_model)

                if not available_models: return None

                cache_key = self._generate_cache_key(messages, model_type, user_id, channel_type, channel_id)
                if self.bot_client.firebase_manager:
                    cached = await self.bot_client.firebase_manager.load_cache(user_id, channel_type, channel_id, cache_key)
                    if cached and cached.get("timestamp", 0) + self.bot_client.cache_limits["cache_ttl_seconds"] > time.time():
                        return cached["response"]

                for model in available_models:
                    queue = self.bot_client.model_queues.get(model)
                    if not queue or queue.qsize() > 5: continue

                    for attempt in range(self.bot_client.request_settings["max_retries"]):
                        try:
                            await queue.put((messages, max_tokens, session))
                            async with self.bot_client.model_semaphores[model]:
                                curr_messages, curr_max_tokens, curr_session = await queue.get()
                                
                                # Использование web_search библиотеки g4f
                                response = await self.bot_client.g4f_client.chat.completions.create(
                                    model=model,
                                    messages=curr_messages,
                                    max_tokens=curr_max_tokens,
                                    web_search=use_search,
                                    session=curr_session,
                                    stream=False
                                )

                                if hasattr(response, "choices"):
                                    response_text = response.choices[0].message.content.strip()
                                else:
                                    response_text = response['choices'][0]['message']['content'].strip()
                                
                                if not response_text: continue

                                self.bot_client.models["model_stats"][model_type][model]["success"] += 1
                                self.bot_client.models["last_successful"][model_type] = model
                                if self.bot_client.firebase_manager:
                                    await self.bot_client.firebase_manager.save_models({"timestamp": time.time(), **self.bot_client.models})
                                    await self.bot_client.firebase_manager.save_cache(user_id, channel_type, channel_id, cache_key, {"response": response_text, "timestamp": time.time()})
                                return response_text

                        except Exception as e:
                            logger.error(f"Ошибка модели {model}: {e}")
                            # Если поиск вызвал ошибку, пробуем этот же запрос без поиска
                            if use_search:
                                logger.info("Отключение поиска из-за ошибки и повтор попытки...")
                                use_search = False
                                continue
                            await asyncio.sleep(self.bot_client.request_settings["retry_delay_base"])
                        finally:
                            queue.task_done()

                return None
            finally:
                import gc
                gc.collect()

    async def _build_system_prompt(self, has_image: bool, user_id: str) -> str:
        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        prompt = DEFAULT_VISION_PROMPT if has_image else DEFAULT_PROMPT
        return prompt.format(now=current_date)

    async def get_context(self, user_id: str, channel: discord.abc.Messageable) -> List[Dict]:
        conv_id = self.bot_client.current_conversation[user_id]["id"]
        return self.bot_client.chat_memory.get(conv_id, [])[-self.MAX_MEMORY_SIZE:]

    async def _save_user_settings(self, user_id: str) -> None:
        if self.bot_client.firebase_manager:
            await self.bot_client.firebase_manager.save_user_settings(user_id, self.bot_client.user_settings[user_id])

    async def _save_conversation(self, user_id: str, conversation_id: str) -> None:
        if self.bot_client.firebase_manager:
            try:
                data = {
                    "chat_memory": self.bot_client.chat_memory[conversation_id][-self.MAX_MEMORY_SIZE:],
                    "topic_memory": self.bot_client.topic_memory[conversation_id][-self.MAX_MEMORY_SIZE:]
                }
                await self.bot_client.firebase_manager.save_conversation(user_id, conversation_id, data)
            except Exception as e:
                logger.error(f"Ошибка сохранения истории: {e}")

    async def start_new_conversation(self, user_id: str, channel_id: str, initial_message: str) -> None:
        if user_id not in self.bot_client.current_conversation:
            conversation_id = str(uuid.uuid4())
            self.bot_client.current_conversation[user_id] = {"id": conversation_id, "last_message_time": time.time(), "request_count": 0, "ttl_seconds": 86400}
            self.bot_client.chat_memory[conversation_id], self.bot_client.topic_memory[conversation_id] = [], []
            if initial_message:
                self.bot_client.chat_memory[conversation_id].append({"role": "user", "content": initial_message})
            await self._save_conversation(user_id, conversation_id)
