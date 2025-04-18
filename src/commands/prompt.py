import discord
from discord import app_commands
from firebase_admin import db
from ..systemLog import logger

description = "Управление системным промптом бота"

default_prompt = (
    "**Системные инструкции**:\n"
    "- Ты интеллектуальный ассистент, отвечающий на русском языке, если пользователь пишет на русском, или на языке запроса, если он другой.\n"
    "- Пользовательский промт: {user_prompt}\n"
    "- Текущее время (UTC): {now}\n"
    "- Формат ответа: Markdown для Discord (используй заголовки, списки, выделение текста, если уместно).\n"
    "- Для общих вопросов: предоставляй структурированный ответ с кратким введением и основными пунктами.\n"
    "- Для вопросов о программировании: включай примеры кода в блоках ```python``` (или другой язык, если указан), проверяй синтаксис и актуальность библиотек.\n"
    "- Если запрос неясен, уточняй детали в ответе или предлагай возможные интерпретации.\n"
)

class BotClient:
    def __init__(self):
        self.prompt_cache = {}

async def load_user_prompt(user_id: str, guild_id: str, bot_client: BotClient) -> str:
    cache_key = f"{guild_id}_{user_id}"
    
    if cache_key in bot_client.prompt_cache:
        return bot_client.prompt_cache[cache_key]
    
    try:
        from ..firebase.firebase_manager import FirebaseManager
        FirebaseManager.initialize()
        ref = db.reference(f"/prompts/{guild_id}/{user_id}")
        data = ref.get()
        prompt = data.get("system_prompt", default_prompt) if data else default_prompt
        bot_client.prompt_cache[cache_key] = prompt
        return prompt
    except Exception as e:
        logger.error(f"Ошибка загрузки промпта для {user_id} на сервере {guild_id}: {e}")
        return default_prompt

async def save_user_prompt(user_id: str, guild_id: str, prompt: str, bot_client: BotClient) -> None:
    cache_key = f"{guild_id}_{user_id}"
    if len(prompt) > 1000:
        raise ValueError("Промпт слишком длинный. Максимум 1000 символов.")
    try:
        from ..firebase.firebase_manager import FirebaseManager
        FirebaseManager.initialize()
        ref = db.reference(f"/prompts/{guild_id}/{user_id}")
        ref.set({"system_prompt": prompt})
        bot_client.prompt_cache[cache_key] = prompt
        logger.info(f"Промпт сохранен для {user_id} на сервере {guild_id}")
    except Exception as e:
        logger.error(f"Ошибка сохранения промпта для {user_id} на сервере {guild_id}: {e}")
        raise

async def get_prompt(interaction: discord.Interaction, bot_client: BotClient) -> None:
    user_id = str(interaction.user.id)
    guild_id = str(interaction.guild.id) if interaction.guild else "DM"
    try:
        prompt = await load_user_prompt(user_id, guild_id, bot_client)
        await interaction.response.send_message(f"Текущий промпт:\n{prompt}", ephemeral=True)
    except Exception as e:
        logger.error(f"Ошибка получения промпта для {user_id}: {e}")
        await interaction.response.send_message("Не удалось получить промпт.", ephemeral=True)

async def set_prompt(interaction: discord.Interaction, bot_client: BotClient, prompt: str) -> None:
    user_id = str(interaction.user.id)
    guild_id = str(interaction.guild.id) if interaction.guild else "DM"
    try:
        await save_user_prompt(user_id, guild_id, prompt, bot_client)
        await interaction.response.send_message("Промпт успешно обновлен.", ephemeral=True)
        logger.info(f"Пользователь {user_id} обновил промпт на сервере {guild_id}")
    except ValueError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
    except Exception as e:
        logger.error(f"Ошибка установки промпта для {user_id}: {e}")
        await interaction.response.send_message("Не удалось обновить промпт.", ephemeral=True)

async def reset_prompt(interaction: discord.Interaction, bot_client: BotClient) -> None:
    user_id = str(interaction.user.id)
    guild_id = str(interaction.guild.id) if interaction.guild else "DM"
    try:
        await save_user_prompt(user_id, guild_id, default_prompt, bot_client)
        cache_key = f"{guild_id}_{user_id}"
        bot_client.prompt_cache.pop(cache_key, None)
        await interaction.response.send_message("Промпт сброшен к стандартному.", ephemeral=True)
        logger.info(f"Пользователь {user_id} сбросил промпт на сервере {guild_id}")
    except Exception as e:
        logger.error(f"Ошибка сброса промпта для {user_id}: {e}")
        await interaction.response.send_message("Не удалось сбросить промпт.", ephemeral=True)

def create_command(bot_client: BotClient):
    prompt_group = app_commands.Group(name="prompt", description=description)

    @prompt_group.command(name="get", description="Показать текущий системный промпт")
    async def get_wrapper(interaction: discord.Interaction):
        await get_prompt(interaction, bot_client)

    @prompt_group.command(name="set", description="Установить новый системный промпт")
    @app_commands.describe(prompt="Новый системный промпт (до 1000 символов)")
    async def set_wrapper(interaction: discord.Interaction, prompt: str):
        await set_prompt(interaction, bot_client, prompt)

    @prompt_group.command(name="reset", description="Сбросить системный промпт к стандартному")
    async def reset_wrapper(interaction: discord.Interaction):
        await reset_prompt(interaction, bot_client)

    return prompt_group