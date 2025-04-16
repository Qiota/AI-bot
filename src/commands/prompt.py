import discord
from discord import app_commands
from firebase_admin import db
from ..aichat import BotClient
from ..systemLog import logger

description = "Управление системным промптом бота"

async def load_user_prompt(user_id: str, guild_id: str, bot_client: BotClient) -> str:
    """Загружает промпт пользователя из Firebase или возвращает стандартный."""
    default_prompt = "Ответь на запрос максимально точно, полно и развернуто. Используй четкую структуру, включай все релевантные детали, примеры и пояснения. Если есть неоднозначности, уточни их и предложи несколько вариантов интерпретации. Обеспечь логичность и последовательность изложения, избегая лишних отступлений. Формат: Discord Markdown."
    try:
        # Проверяем наличие prompt_cache и используем его
        cache_key = f"{guild_id}_{user_id}"
        if hasattr(bot_client, 'prompt_cache') and cache_key in bot_client.prompt_cache:
            return bot_client.prompt_cache[cache_key]
        
        from ..firebase.firebase_manager import FirebaseManager
        FirebaseManager.initialize()  # type: ignore
        ref = db.reference(f"/prompts/{guild_id}/{user_id}")
        data = ref.get()
        prompt = data.get("system_prompt", default_prompt) if data else default_prompt
        if hasattr(bot_client, 'prompt_cache'):
            bot_client.prompt_cache[cache_key] = prompt
        return prompt
    except Exception as e:
        logger.error(f"Ошибка загрузки промпта для {user_id} на сервере {guild_id}: {e}")
        return default_prompt

async def save_user_prompt(user_id: str, guild_id: str, prompt: str, bot_client: BotClient) -> None:
    """Сохраняет промпт пользователя в Firebase."""
    try:
        from ..firebase.firebase_manager import FirebaseManager
        FirebaseManager.initialize()  # type: ignore
        ref = db.reference(f"/prompts/{guild_id}/{user_id}")
        ref.set({"system_prompt": prompt})
        cache_key = f"{guild_id}_{user_id}"
        if hasattr(bot_client, 'prompt_cache'):
            bot_client.prompt_cache[cache_key] = prompt
        logger.info(f"Промпт сохранен для {user_id} на сервере {guild_id}")
    except Exception as e:
        logger.error(f"Ошибка сохранения промпта для {user_id} на сервере {guild_id}: {e}")
        raise

async def get_prompt(interaction: discord.Interaction, bot_client: BotClient) -> None:
    user_id = str(interaction.user.id)
    guild_id = str(interaction.guild.id) if interaction.guild else "DM"
    current_prompt = await load_user_prompt(user_id, guild_id, bot_client)
    await interaction.response.send_message(f"Текущий системный промпт: {current_prompt}", ephemeral=True)

async def set_prompt(interaction: discord.Interaction, bot_client: BotClient, prompt: str) -> None:
    user_id = str(interaction.user.id)
    guild_id = str(interaction.guild.id) if interaction.guild else "DM"
    await save_user_prompt(user_id, guild_id, prompt, bot_client)
    await interaction.response.send_message(f"Системный промпт обновлен: {prompt}", ephemeral=True)
    logger.info(f"Пользователь {user_id} обновил системный промпт на сервере {guild_id}")

async def reset_prompt(interaction: discord.Interaction, bot_client: BotClient) -> None:
    user_id = str(interaction.user.id)
    guild_id = str(interaction.guild.id) if interaction.guild else "DM"
    default_prompt = "Ты - дружелюбный чат-бот от Qiota. Отвечай кратко и точно."
    await save_user_prompt(user_id, guild_id, default_prompt, bot_client)
    await interaction.response.send_message("Системный промпт сброшен к стандартному", ephemeral=True)
    logger.info(f"Пользователь {user_id} сбросил системный промпт на сервере {guild_id}")

def create_command(bot_client: BotClient):
    prompt_group = app_commands.Group(name="prompt", description=description)

    @prompt_group.command(name="get", description="Показать текущий системный промпт")
    async def get_wrapper(interaction: discord.Interaction):  # type: ignore
        await get_prompt(interaction, bot_client)

    @prompt_group.command(name="set", description="Установить персональный системный промпт")
    @app_commands.describe(prompt="Новый системный промпт")
    async def set_wrapper(interaction: discord.Interaction, prompt: str):  # type: ignore
        await set_prompt(interaction, bot_client, prompt)

    @prompt_group.command(name="reset", description="Сбросить системный промпт к стандартному")
    async def reset_wrapper(interaction: discord.Interaction):  # type: ignore
        await reset_prompt(interaction, bot_client)

    return prompt_group