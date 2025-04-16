import discord
from discord import app_commands, Forbidden, HTTPException
import asyncio
from typing import List, Dict, Optional
import aiohttp
from g4f.client import Client
from g4f.Provider import PollinationsAI
from .logging_config import logger 
from .cooldown_manager import CooldownManager
import time
import re
from collections import OrderedDict, defaultdict
import json
import os
from .commands.restrict import check_bot_access, check_user_restriction

class BotClient:
    def __init__(self, config):
        logger.info("Создание BotClient")
        self.config = config
        self.client = Client(provider=PollinationsAI)
        self.models_file = "models.json"
        self.settings_file = "temp/restrict_settings.json"
        intents = discord.Intents.default()
        intents.message_content = intents.dm_messages = intents.members = True
        self.bot = discord.Client(intents=intents)
        self.tree = app_commands.CommandTree(self.bot)
        self.processed_messages = set()
        self.message_to_response = {}
        self.link_cache = OrderedDict(maxlen=20)
        self.chat_memory = defaultdict(lambda: OrderedDict())
        self.topic_memory = defaultdict(list)
        self.cache_limits = {"messages": 100}
        self.request_settings = {
            "vision_headers": {"Content-Type": "application/json"},
            "rate_limit_delay": 2.0,
            "max_retries": 5,
            "retry_delay_base": 5.0
        }
        self.models = {"text": [], "vision": [], "last_update": None}
        self.user_settings = defaultdict(lambda: {"max_response_length": 2000})
        self.cooldown_manager = CooldownManager()
        self.ignored_settings = {"ignored_users": [], "ignored_channels": []}
        self.load_models()
        self.load_ignored_settings()
        asyncio.create_task(self.update_models_periodically())
        asyncio.create_task(self.auto_trim_memory())
        from .commands.set_prompt import create_command, load_user_prompt
        self.load_user_prompt = load_user_prompt
        self.tree.add_command(create_command(self))
        from .commands.img import create_command as create_img_command
        self.tree.add_command(create_img_command(self))
        from .commands.restrict import create_command as create_restrict_command
        self.tree.add_command(create_restrict_command(self))
        from .commands.giveaway import create_command as create_giveaway_command
        self.giveaways = {}
        self.completed_giveaways = {}
        self.tree.add_command(create_giveaway_command(self)[0])
        self.tree.add_command(create_giveaway_command(self)[1])
        self.tree.add_command(create_giveaway_command(self)[2])

    def load_models(self):
        default = {"text": ["gpt-4o-mini", "gpt-4o", "o1-mini"], "vision": ["openai", "openai-large"], "last_update": None}
        try:
            if os.path.exists(self.models_file):
                with open(self.models_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.models.update({
                    "text": data.get("text_models", default["text"]),
                    "vision": data.get("vision_models", default["vision"]),
                    "last_update": data.get("last_update")
                })
                logger.info(f"Модели: text={self.models['text']}, vision={self.models['vision']}")
            else:
                raise FileNotFoundError
        except Exception as e:
            logger.error(f"Ошибка загрузки моделей: {e}")
            self.models.update(default)
            self.save_models()

    def load_ignored_settings(self):
        try:
            self.ignored_settings = {"ignored_users": [], "ignored_channels": []}
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                # Собираем всех restricted_users из всех гильдий
                for guild_id, settings in data.items():
                    if isinstance(settings, dict) and "restricted_users" in settings:
                        self.ignored_settings["ignored_users"].extend(settings["restricted_users"])
                # Удаляем дубликаты
                self.ignored_settings["ignored_users"] = list(set(self.ignored_settings["ignored_users"]))
                logger.info(f"Настройки игнорирования загружены: {self.ignored_settings}")
        except Exception as e:
            logger.error(f"Ошибка загрузки настроек игнорирования: {e}")
            self.ignored_settings = {"ignored_users": [], "ignored_channels": []}

    def save_ignored_settings(self):
        try:
            os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(self.ignored_settings, f, indent=4, ensure_ascii=False)
            logger.success("Настройки игнорирования сохранены")
        except Exception as e:
            logger.error(f"Ошибка сохранения настроек игнорирования: {e}")

    def save_models(self):
        try:
            with open(self.models_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "text_models": self.models["text"],
                    "vision_models": self.models["vision"],
                    "last_update": time.time()
                }, f, ensure_ascii=False)
            logger.success("Модели сохранены")
        except Exception as e:
            logger.error(f"Ошибка сохранения моделей: {e}")

    async def update_models_periodically(self):
        await self.fetch_available_models()
        while True:
            try:
                if not self.models["last_update"] or (time.time() - self.models["last_update"]) > 1800:
                    await self.fetch_available_models()
                await asyncio.sleep(600)
            except Exception as e:
                logger.error(f"Ошибка обновления моделей: {e}")
                await asyncio.sleep(300)

    async def fetch_available_models(self):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
            try:
                async with session.get("https://text.pollinations.ai/models") as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    self.models["last_update"] = time.time()
                    new_models = {"text": [], "vision": []}
                    if isinstance(data, list):
                        for m in data:
                            if m.get("id"):
                                (new_models["vision"] if m.get("supports_vision", False) else new_models["text"]).append(m["id"])
                    else:
                        new_models["text"], new_models["vision"] = data.get("text_models", []), data.get("vision_models", [])
                    for mt in ["text", "vision"]:
                        valid = await asyncio.gather(*[self.check_model_availability(m, mt == "vision") for m in new_models[mt]])
                        self.models[mt] = [m for m, (v, lat, acc) in sorted(zip(new_models[mt], valid), key=lambda x: 0.2 * x[1][1] + 0.8 * (1 - x[1][2])) if v] or self.models[mt]
                    self.save_models()
                    logger.success(f"Модели обновлены: text={self.models['text']}, vision={self.models['vision']}")
            except Exception as e:
                logger.error(f"Ошибка получения моделей: {e}")

    async def check_model_availability(self, model: str, is_vision: bool) -> tuple[bool, float, float]:
        try:
            async with aiohttp.ClientSession() as session:
                start_time = time.time()
                if is_vision:
                    async with session.post(f"https://text.pollinations.ai/{model}", json={
                        "messages": [{"role": "user", "content": [{"type": "text", "text": "Identify scene elements"}]}],
                        "max_tokens": 10
                    }, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                        latency = time.time() - start_time
                        content = (await resp.json()).get("choices", [{}])[0].get("message", {}).get("content", "")
                        return resp.status == 200, latency, 1.0 if "identify" in content.lower() else 0.85
                resp = await asyncio.to_thread(self.client.chat.completions.create, model=model, messages=[{"role": "user", "content": "ping"}], max_tokens=1)
                return bool(resp.choices[0].message.content), time.time() - start_time, 1.0
        except Exception:
            return False, float('inf'), 0.0

    async def analyze_image_context(self, messages: List[Dict], text: str) -> Optional[str]:
        try:
            last_message = messages[-1]["content"]
            text_query = text or next((item["text"] for item in last_message if item.get("text")), "")
            image_urls = [item["image_url"]["url"] for item in last_message if item.get("image_url")]
            current_date = time.strftime("%Y-%m-%d", time.localtime(time.time()))
            context = f"🌟 **Анализ изображения ({current_date})**: "
            if text_query:
                context += f"Ты спрашиваешь: '{text_query}'. "
            if image_urls:
                context += f"Я вижу {len(image_urls)} изображение(й). Давай рассмотрим элементы: персонажи, локации, предметы. "
            keywords = re.findall(r"\b(актер|персонаж|место|локация|фильм|сцена|объект|город|здание|костюм|новый|недавно|202)\b", text_query.lower())
            if keywords:
                context += f"Ключевые слова: {', '.join(keywords)}. "
            if any(kw in keywords for kw in ["новый", "недавно", "202"]):
                context += "Смотрим на новые медиа (2024–2025 годы). "
            elif "актер" in keywords or "персонаж" in keywords:
                context += "Сфокусируемся на людях и их ролях. "
            elif "место" in keywords or "локация" in keywords:
                context += "Давай разберем локации и фон. "
            return context + "Я использую текущую дату и детали изображения, чтобы дать точный ответ. 🖼️"
        except Exception as e:
            logger.error(f"Ошибка анализа контекста: {e}")
            return None

    async def process_response(self, model_type: str, model: str, messages: List[Dict], max_tokens: int, text: str) -> Optional[str]:
        url = f"https://pollinations.ai/{model}" if model_type == "vision" else None
        if model_type == "vision":
            try:
                context = await self.analyze_image_context(messages, text)
                messages[-1]["content"].append({"type": "text", "text": context or "Контекст изображения не определен, но я попробую помочь!"})
            except Exception as e:
                logger.error(f"Ошибка дополнения контекста: {e}")
                messages[-1]["content"].append({"type": "text", "text": "Контекст изображения не определен, но я попробую помочь!"})
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
                    delay = self.request_settings["retry_delay_base"] * (2 ** attempt)
                    await asyncio.sleep(delay)
                continue
            except Exception as e:
                logger.error(f"Ошибка модели {model}: {e}")
                break
        logger.error(f"Все попытки для модели {model} не удались")
        return None

    async def get_context(self, user_id: str, channel: discord.abc.Messageable, limit: int = None) -> List[Dict]:
        limit = limit or self.cache_limits["messages"]
        user_memory = self.chat_memory[user_id]
        recent_topics = set(self.topic_memory[user_id][-3:])
        now = time.time()
        filtered_messages = [
            {
                "role": msg["role"],
                "content": [{"type": "text", "text": msg["content"]}],
                "metadata": {"timestamp": msg["timestamp"], "author": msg["author"], "topic": msg.get("topic", "общее")}
            }
            for msg in list(user_memory.values())[-limit:]
            if now - msg["timestamp"] <= 7 * 24 * 3600 or msg.get("topic") in recent_topics
        ]
        return filtered_messages[-limit:]

    def detect_topic(self, text: str) -> str:
        text_lower = text.lower()
        if "персонаж" in text_lower or "актер" in text_lower:
            return "персонажи"
        elif "место" in text_lower or "локация" in text_lower:
            return "локации"
        elif "объект" in text_lower or "предмет" in text_lower:
            return "предметы"
        elif "новый" in text_lower or "202" in text_lower:
            return "новые_медиа"
        return "общее"

    async def add_to_memory(self, user_id: str, message_id: str, role: str, content: str, author: str, image_url: Optional[str] = None):
        topic = self.detect_topic(content)
        msg = {
            "role": role,
            "content": content,
            "author": author,
            "timestamp": time.time(),
            "expires": time.time() + 30 * 24 * 3600,
            "topic": topic
        }
        if image_url:
            msg["image"] = image_url
        self.chat_memory[user_id][message_id] = msg
        if topic not in self.topic_memory[user_id]:
            self.topic_memory[user_id].append(topic)
        while len(self.topic_memory[user_id]) > 10:
            self.topic_memory[user_id].pop(0)
        while len(self.chat_memory[user_id]) > self.cache_limits["messages"]:
            self.chat_memory[user_id].popitem(last=False)

    async def auto_trim_memory(self):
        while True:
            now = time.time()
            for user_id, messages in list(self.chat_memory.items()):
                for msg_id, msg in list(messages.items()):
                    if "expires" in msg and msg["expires"] < now:
                        del messages[msg_id]
            await asyncio.sleep(3600)

    async def check_link_validity(self, url: str) -> bool:
        if url in self.link_cache:
            return self.link_cache[url]
        async with aiohttp.ClientSession() as session:
            try:
                async with session.head(url, timeout=aiohttp.ClientTimeout(total=4), allow_redirects=True) as resp:
                    valid = resp.status == 200
                    self.link_cache[url] = valid
                    return valid
            except Exception:
                self.link_cache[url] = False
                return False

    def needs_web_search(self, text: str, context: List[Dict], is_vision: bool = False) -> bool:
        if is_vision:
            return True
        keywords = {"найди", "сегодня", "сейчас", "новости", "новый", "недавно", "202"}
        vision_keywords = {"изображение", "фото", "актер", "персонаж", "место", "локация", "фильм", "сцена", "объект"}
        time_phrases = r"\b(сегодня|вчера|завтра|на этой неделе|в этом месяце|в этом году|последние|текущие|недавно|202[4-5])\b"
        question_phrases = r"\b(кто|где|что|когда|как|какой|какая|какие)\b"
        text_lower = text.lower() if isinstance(text, str) else " ".join(str(item) for item in text).lower()
        context_lower = " ".join(str(msg.get("content", "")).lower() for msg in context[-3:] if msg.get("role") == "user")
        return (any(kw in text_lower for kw in vision_keywords) or
                any(kw in context_lower for kw in vision_keywords) or
                bool(re.search(r"\b(опиши фото|кто на картинке|где это|распознай|что за фильм)\b", text_lower)) or
                any(kw in text_lower for kw in keywords) or
                bool(re.search(time_phrases, text_lower)) or
                bool(re.search(question_phrases, text_lower)))

    async def check_restrictions(self, message: discord.Message) -> bool:
        """
        Проверяет ограничения для сообщений в гильдиях.
        Возвращает True, если сообщение разрешено обрабатывать, иначе False.
        Для ЛС ограничения не применяются.
        """
        # Если сообщение в ЛС, пропускаем все проверки
        if isinstance(message.channel, discord.DMChannel):
            return True
        
        # Проверка ограничений пользователя
        if not await check_user_restriction(message):
            return False
        
        # Проверка доступа бота к каналу
        if not await check_bot_access(message):
            return False
        
        return True

    async def on_message(self, message: discord.Message):
        msg_key = f"{message.id}-{message.channel.id}"
        if message.author.bot or msg_key in self.processed_messages:
            return
        
        # Проверка ограничений (игнор, пользователь, канал)
        if self.ignored_settings.get("ignored_users") and str(message.author.id) in self.ignored_settings["ignored_users"]:
            logger.info(f"Сообщение от {message.author.id} проигнорировано (пользователь в списке игнора)")
            return
        if self.ignored_settings.get("ignored_channels") and str(message.channel.id) in self.ignored_settings["ignored_channels"] and not isinstance(message.channel, discord.DMChannel):
            logger.info(f"Сообщение в канале {message.channel.id} проигнорировано (канал в списке игнора)")
            return
        if not await self.check_restrictions(message):
            return
        
        # Проверка упоминания бота (для гильдий)
        if self.bot.user not in message.mentions and not isinstance(message.channel, discord.DMChannel):
            return
        
        self.processed_messages.add(msg_key)
        async with message.channel.typing():
            text = message.content.replace(f"<@{self.bot.user.id}>", "").strip()
            response = await self.generate_response(str(message.author.id), str(message.id), text, message)
            if response:
                await self._send_split_message(message, response)

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        msg_key = f"{after.id}-{after.channel.id}"
        if before.content == after.content or after.author.bot or msg_key not in self.processed_messages:
            return
        
        # Проверка ограничений (игнор, пользователь, канал)
        if self.ignored_settings.get("ignored_users") and str(after.author.id) in self.ignored_settings["ignored_users"]:
            logger.info(f"Редактирование сообщения от {after.author.id} проигнорировано (пользователь в списке игнора)")
            return
        if self.ignored_settings.get("ignored_channels") and str(after.channel.id) in self.ignored_settings["ignored_channels"] and not isinstance(after.channel, discord.DMChannel):
            logger.info(f"Редактирование сообщения в канале {after.channel.id} проигнорировано (канал в списке игнора)")
            return
        if not await self.check_restrictions(after):
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
        async def on_ready(self):
            from .commands.giveaway import resume_giveaways
            await resume_giveaways(self)
            logger.success(f"Бот {self.bot.user} готов")

        @self.bot.event
        async def on_message(message):
            await self.on_message(message)

        @self.bot.event
        async def on_message_edit(before, after):
            await self.on_message_edit(before, after)

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
            now = time.time()
            guild_id = str(message.guild.id) if message.guild else "DM"
            vision_prompt = """
Привет! Я помогу тебе разобраться с изображением. Давай посмотрим, что интересного можно найти:

- **Персонажи**: Кто это может быть? Может, герой или злодей из какого-то произведения?
  Например: "Это, кажется, Пол Атридес, герой из 'Дюна'."
- **Места**: Где это происходит? Реальное место или вымышленное?
  Например: "Похоже на пустынную планету Арракис."
- **Предметы**: Какие интересные вещи есть на картинке?
  Например: "Вижу крис-нож, это оружие из 'Дюна'."

**Что я сделаю**:
- Дам мягкий и дружелюбный ответ, как будто мы вместе рассматриваем картинку.
- Если есть слова вроде "новый", "2024", "2025", посмотрю на свежие медиа.
- Использую детали изображения (одежда, фон) и твой запрос для точности.
- Если не совсем уверена, скажу: "Мне кажется (~80%)...".
- Ответ будет в формате Discord Markdown, чтобы было удобно читать.

Давай начнем! 🎨
""".format(time.strftime("%Y-%m-%d", time.localtime(now)))
            system_prompt = f"{self.load_user_prompt(user_id, guild_id)}\n📅 Дата: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}. Формат: Discord Markdown."
            if message.attachments:
                system_prompt += f"\n{vision_prompt}"
            user_content = [{"type": "text", "text": text}] if text else []
            attachments = [a.url for a in message.attachments if a.content_type and a.content_type.startswith("image/")]
            user_content.extend({"type": "image_url", "image_url": {"url": url}} for url in attachments)
            messages = context + [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}]
            response_text = await self._try_generate_response(messages, self.needs_web_search(text, context, bool(attachments)), bool(attachments), 6000, text)
            if not response_text:
                return "Хм, не получилось найти ответ. Может, попробуем еще раз с другим изображением или запросом?"
            final_response = response_text[:self.user_settings[user_id]["max_response_length"]]
            await self.add_to_memory(user_id, message_id, "user", text, message.author.name, attachments[0] if attachments else None)
            await self.add_to_memory(user_id, f"{message_id}_resp", "assistant", final_response, self.bot.user.name)
            return final_response
        except Exception as e:
            logger.error(f"Ошибка генерации для {user_id}: {e}")
            await self._send_temp_message(message.channel, "Упс, что-то пошло не так. Давай попробуем еще раз?", user_id)
            return None

    async def _try_generate_response(self, messages: List[Dict], needs_web: bool, has_image: bool, max_tokens: int, text: str) -> Optional[str]:
        model_type = "vision" if has_image else "text"
        needs_web = needs_web or has_image
        for model in self.models[model_type] or []:
            try:
                if response := await self.process_response(model_type, model, messages, max_tokens, text):
                    return response
            except Exception as e:
                logger.error(f"Модель {model} ({model_type}) не сработала: {e}")
                continue
        if has_image and self.models["vision"]:
            for fallback_model in self.models["vision"]:
                logger.warning(f"Повторная попытка с моделью vision: {fallback_model}")
                try:
                    return await self.process_response("vision", fallback_model, messages, max_tokens, text)
                except Exception as e:
                    logger.error(f"Резервная модель vision {fallback_model} не сработала: {e}")
                    continue
        logger.error(f"Нет ответа от моделей типа {model_type}")
        return None

    async def _send_temp_message(self, channel, content: str, user_id: str, duration: int = 5):
        try:
            msg = await channel.send(content)
            await asyncio.sleep(duration)
            await msg.delete()
        except Exception as e:
            logger.error(f"Ошибка временного сообщения для {user_id}: {e}")

    async def clear_user_memory(self, user_id: str):
        self.chat_memory[user_id].clear()
        self.topic_memory[user_id].clear()
        logger.info(f"Память очищена для {user_id}")

    async def get_user_stats(self, user_id: str) -> Dict[str, int]:
        msgs = self.chat_memory[user_id]
        return {
            "total": len(msgs),
            "user": sum(1 for m in msgs.values() if m["role"] == "user"),
            "bot": sum(1 for m in msgs.values() if m["role"] == "assistant"),
            "topics": len(self.topic_memory[user_id])
        }

    async def trim_old_messages(self, user_id: str, days: int = 7):
        threshold = time.time() - days * 24 * 3600
        self.chat_memory[user_id] = OrderedDict(
            (k, v) for k, v in self.chat_memory[user_id].items() if v["timestamp"] >= threshold
        )
        logger.info(f"Старые сообщения удалены для {user_id}")

    async def export_memory_to_json(self, user_id: str, filename: str) -> bool:
        try:
            memory_data = {
                "messages": dict(self.chat_memory[user_id]),
                "topics": self.topic_memory[user_id]
            }
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(memory_data, f, ensure_ascii=False)
            logger.success(f"Память экспортирована для {user_id} в {filename}")
            return True
        except Exception as e:
            logger.error(f"Ошибка экспорта для {user_id}: {e}")
            return False