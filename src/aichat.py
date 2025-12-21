import discord
import asyncio
from typing import List, Dict, Optional
from .systemLog import logger
import time
import uuid
import base64
from datetime import datetime
from .client import BotClient
import tempfile
import g4f.debug
import warnings
import re

# Подавление предупреждений pydub
warnings.filterwarnings("ignore", category=RuntimeWarning, module="pydub.utils")

# Отключение debug-режима
g4f.debug.logging = False

DEFAULT_PROMPT = """Ты — Кудере~, поддерживай беседу для более живого общения используя все людские качества.
Текущее время: {now}.
ИНСТРУКЦИЯ ПО ИСПОЛЬЗОВАНИЮ ВЕБ-ПОИСКА:
1. Если тебе предоставлены результаты поиска, используй ТОЛЬКО достоверные факты из них.
2. Если результаты поиска содержат нерелевантные ссылки (соцсети, профили, мусор), ИГНОРИРУЙ ИХ.
3. НЕ ВЫДУМЫВАЙ ссылки и факты, если их нет в результатах."""

SEARCH_TRIGGER_WORDS = ["найди", "отыщи", "поищи", "разыщи", "ищи", "поиск", "информация о", "что такое"]

# Локальный словарь запрещенных корней и слов (регулярные выражения для гибкости)
FORBIDDEN_PATTERN = re.compile(
    r"(хуй|пизд|еба|ебл|бля|сук|гандон|чмо|уеб|шлюх|даун|ниггер|хохол|кацап|терроризм|суицид|наркотик)",
    re.IGNORECASE
)

class AIChat:
    """Класс для обработки сообщений с авто-очисткой памяти и защитой от переполнения/токсичности."""
    MAX_MEMORY_SIZE = 10 
    SAFE_CHAR_LIMIT = 3500 
    INACTIVITY_TIMEOUT = 3600

    def __init__(self, bot_client: BotClient) -> None:
        self.bot_client: BotClient = bot_client
        self.bot_client.bot.event(self.on_message)
        self.bot_client.bot.event(self.on_message_edit)

        self.cookies_dir = tempfile.TemporaryDirectory(prefix="g4f_cookies_")
        try:
            g4f.cookies.set_cookies_dir(self.cookies_dir.name)
        except Exception as e:
            logger.error(f"Ошибка cookies: {e}")

        asyncio.create_task(self._cleanup_inactive_conversations())
        logger.info("AIChat инициализирован: Защита и очистка активны.")

    async def _cleanup_inactive_conversations(self):
        while not self.bot_client.bot.is_closed():
            try:
                if not self.bot_client.bot.is_ready():
                    await asyncio.sleep(5)
                    continue
                now = time.time()
                inactive_users = []
                for user_id, session in list(self.bot_client.current_conversation.items()):
                    if now - session.get("last_message_time", 0) > self.INACTIVITY_TIMEOUT:
                        inactive_users.append(user_id)
                for user_id in inactive_users:
                    conv_id = self.bot_client.current_conversation[user_id]["id"]
                    self.bot_client.chat_memory.pop(conv_id, None)
                    self.bot_client.current_conversation.pop(user_id, None)
                    logger.info(f"Память пользователя {user_id} очищена.")
            except Exception as e:
                logger.error(f"Ошибка в цикле очистки: {e}")
            await asyncio.sleep(300)

    def _add_to_memory(self, user_id: str, role: str, content: str) -> None:
        if user_id not in self.bot_client.current_conversation: return
        conv_id = self.bot_client.current_conversation[user_id]["id"]
        self.bot_client.current_conversation[user_id]["last_message_time"] = time.time()
        
        if conv_id not in self.bot_client.chat_memory:
            self.bot_client.chat_memory[conv_id] = []
        
        clean_content = " ".join(content.split())[:1500]
        self.bot_client.chat_memory[conv_id].append({"role": role, "content": clean_content})
        
        if len(self.bot_client.chat_memory[conv_id]) > self.MAX_MEMORY_SIZE:
            self.bot_client.chat_memory[conv_id] = self.bot_client.chat_memory[conv_id][-self.MAX_MEMORY_SIZE:]

    def _get_safe_context(self, user_id: str, sys_prompt: str) -> List[Dict]:
        conv_id = self.bot_client.current_conversation[user_id]["id"]
        history = self.bot_client.chat_memory.get(conv_id, [])
        safe_history, chars = [], len(sys_prompt)
        for msg in reversed(history):
            if chars + len(msg['content']) > self.SAFE_CHAR_LIMIT: break
            safe_history.insert(0, msg)
            chars += len(msg['content'])
        return safe_history

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot: return
        await self._process_any_message(message)

    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.content == after.content or after.author.bot: return
        await self._process_any_message(after, is_edit=True)

    async def _process_any_message(self, message: discord.Message, is_edit: bool = False):
        user_id = str(message.author.id)
        if not await self.bot_client.is_bot_mentioned(message): return

        text = message.content.replace(f"<@{self.bot_client.bot.user.id}>", "").strip()
        
        # 1. Локальная проверка на запрещенные слова
        if FORBIDDEN_PATTERN.search(text):
            await message.reply("🔔 В сообщении есть тема, которую я не могу обсуждать.\nДавай продолжим разговор в вежливом формате.🤔")
            return

        if text.lower() == ".reset":
            await self._handle_reset(message, user_id)
            return

        await self.start_new_conversation(user_id)
        
        async with message.channel.typing():
            input_text = f"[Изменено]: {text}" if is_edit else text
            self._add_to_memory(user_id, "user", input_text)
            
            use_search = any(w in text.lower() for w in SEARCH_TRIGGER_WORDS)
            parts = await self.generate_response(user_id, text, message, use_search)
            
            if parts: await self._send_split_message(message, parts)

    async def _handle_reset(self, message: discord.Message, user_id: str):
        new_id = str(uuid.uuid4())
        self.bot_client.current_conversation[user_id] = {"id": new_id, "last_message_time": time.time()}
        self.bot_client.chat_memory[new_id] = []
        await message.reply("🧹 Память беседы успешно очищена.")

    async def generate_response(self, user_id: str, text: str, message: discord.Message, use_search: bool) -> List[str]:
        if any(a.content_type and "image" in a.content_type for a in message.attachments):
            return await self._process_vision(user_id, text, message)

        sys_prompt = DEFAULT_PROMPT.format(now=datetime.now().strftime("%H:%M"))
        messages = [{"role": "system", "content": sys_prompt}] + self._get_safe_context(user_id, sys_prompt)

        resp = await self._call_ai(messages, user_id, use_search)
        return self._split_text(resp or "Ошибка API. Попробуйте ещё раз.")

    async def _call_ai(self, messages: List[Dict], user_id: str, use_search: bool) -> Optional[str]:
        model = self.bot_client.user_settings[user_id].get("selected_text_model", "openai-fast")
        try:
            actual_search = use_search if len(str(messages)) < 3000 else False
            response = await self.bot_client.g4f_client.chat.completions.create(
                model=model, messages=messages, web_search=actual_search
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            err_msg = str(e).lower()
            # 2. Коллектор ошибки 400 (Bad Request / Policy Violation)
            if "400" in err_msg or "safety" in err_msg or "content_filter" in err_msg:
                return "🔔 В сообщении есть тема, которую я не могу обсуждать.\nДавай продолжим разговор в вежливом формате.🤔"
            
            if "500" in err_msg or "length" in err_msg:
                try:
                    res = await self.bot_client.g4f_client.chat.completions.create(
                        model=model, messages=[messages[0], messages[-1]], web_search=False
                    )
                    return "[Контекст сжат] " + res.choices[0].message.content.strip()
                except: return None
            logger.error(f"AI Error: {e}")
            return None

    def _split_text(self, text: str) -> List[str]:
        return [text[i:i+2000] for i in range(0, len(text), 2000)]

    async def _send_split_message(self, message: discord.Message, parts: List[str]) -> None:
        user_id = str(message.author.id)
        for i, part in enumerate(parts):
            try:
                if i == 0: await message.reply(part)
                else: await message.channel.send(part)
                self._add_to_memory(user_id, "assistant", part)
            except: pass

    async def start_new_conversation(self, user_id: str):
        if user_id not in self.bot_client.current_conversation:
            cid = str(uuid.uuid4())
            self.bot_client.current_conversation[user_id] = {"id": cid, "last_message_time": time.time()}

    async def _process_vision(self, user_id: str, text: str, message: discord.Message) -> List[str]:
        try:
            att = [a for a in message.attachments if a.content_type and "image" in a.content_type][0]
            img_b64 = base64.b64encode(await att.read()).decode()
            res = await self.bot_client.g4f_client.chat.completions.create(
                model=self.bot_client.user_settings[user_id].get("selected_vision_model", "openai-fast"),
                messages=[{"role": "user", "content": text or "Опиши это фото"}],
                images=[[f"data:image/jpeg;base64,{img_b64}", "img.jpg"]]
            )
            return self._split_text(res.choices[0].message.content)
        except Exception as e:
            if "400" in str(e):
                return ["🔔 В этом изображении есть контент, который я не могу принять.\nПожалуйста, выбери другое изображение.🥱"]
            return ["Ошибка при анализе фото."]