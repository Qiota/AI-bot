import discord
import g4f
from typing import List, Dict, Optional
from .systemLog import logger
from .client import BotClient
import time
import uuid
import re

# Список имен провайдеров, которые вы хотите использовать
PROVIDER_NAMES = [
    "Yqcloud", "AItianhu", "AItianhuSpace", "AiAsk", "Aichat",
    "ChatBase", "ChatgptAi", "ChatgptFree", "ChatgptX",
    "FreeGpt", "GPTalk", "GptForLove", "GptGo", "Llama2", "NoowAi"
]

def get_available_providers():
    """Собирает только те провайдеры, которые реально есть в установленной версии g4f."""
    available = []
    for name in PROVIDER_NAMES:
        provider = getattr(g4f.Provider, name, None)
        if provider:
            available.append(provider)
    return available

# Список реально доступных провайдеров
WORKING_PROVIDERS = get_available_providers()

FORBIDDEN_PATTERN = re.compile(r"(хуй|пизд|еба|бля|сук|шлюх|наркотик)", re.IGNORECASE)

class AIChat:
    def __init__(self, bot_client: BotClient) -> None:
        self.bot_client = bot_client
        self.bot_client.bot.event(self.on_message)
        logger.info(f"AIChat: Загружено провайдеров: {len(WORKING_PROVIDERS)} из {len(PROVIDER_NAMES)}")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot: return
        user_id = str(message.author.id)
        
        if not await self.bot_client.is_bot_mentioned(message): return

        content = message.content.replace(f"<@{self.bot_client.bot.user.id}>", "").strip()
        
        if FORBIDDEN_PATTERN.search(content):
            return await message.reply("🌸 Пожалуйста, используй более вежливые слова.")

        if content.lower() == ".reset":
            self.bot_client.current_conversation.pop(user_id, None)
            return await message.reply("🧹 Память очищена!")

        # Инициализация сессии если её нет
        if user_id not in self.bot_client.current_conversation:
            self.bot_client.current_conversation[user_id] = {
                "id": str(uuid.uuid4()), 
                "last_message_time": time.time(), 
                "ttl_seconds": 86400
            }

        async with message.channel.typing():
            self._add_to_memory(user_id, "user", content)
            history = self._get_history(user_id)
            
            # Попытка получить ответ
            response = await self._call_ai_with_retry(history, user_id)
            
            if response:
                await self._send_single_message(message, response)
                self._add_to_memory(user_id, "assistant", response)
            else:
                await message.reply("💢 Извини, сейчас все нейросети заняты. Попробуй позже.")

    async def _call_ai_with_retry(self, messages: List[Dict], user_id: str) -> Optional[str]:
        settings = self.bot_client.user_settings[user_id]
        model = settings.get("selected_text_model", "gpt-4o")
        
        # Пробуем по очереди все рабочие провайдеры
        for provider in WORKING_PROVIDERS:
            try:
                # Добавляем системный промпт
                full_messages = [{"role": "system", "content": "Ты бот который поддерживает человеческий диалог."}] + messages
                
                response = await self.bot_client.g4f_client.chat.completions.create(
                    model=model,
                    messages=full_messages,
                    provider=provider
                )
                result = response.choices[0].message.content.strip()
                if len(result) > 1:
                    return result
            except Exception:
                continue # Если этот провайдер выдал ошибку, идем к следующему
        
        return None

    async def _send_single_message(self, message: discord.Message, text: str):
        """Send response as single editable message, splitting content progressively."""
        first_chunk = text[:1900]
        response_msg = await message.reply(first_chunk)
        
        pos = 1900
        while pos < len(text):
            chunk = text[pos:pos + 1900]
            try:
                await response_msg.edit(content=text[:pos + len(chunk)])
            except discord.HTTPException:
                break  # Can't edit, stop
            pos += 1900
        
        return response_msg

    def _add_to_memory(self, user_id: str, role: str, content: str):
        conv = self.bot_client.current_conversation[user_id]
        cid = conv["id"]
        conv["last_message_time"] = time.time()
        self.bot_client.chat_memory[cid].append({"role": role, "content": content})
        if len(self.bot_client.chat_memory[cid]) > 10:
            self.bot_client.chat_memory[cid].pop(0)

    def _get_history(self, user_id: str) -> List[Dict]:
        cid = self.bot_client.current_conversation[user_id]["id"]
        return self.bot_client.chat_memory.get(cid, [])

    async def _send_large_message(self, message: discord.Message, text: str):
        """Deprecated: use _send_single_message instead"""
        pass
