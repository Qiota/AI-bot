import discord
from discord import app_commands
from ..aichat import BotClient
from ..config import logger
import os
import json

description = "Управление системным промптом бота"

BASE_DIR = os.path.join(os.path.dirname(__file__), "prompts", "servers")
os.makedirs(BASE_DIR, exist_ok=True)

def load_user_prompt(user_id: str, guild_id: str) -> str:
    """Загружает промпт пользователя из JSON-файла или возвращает стандартный."""
    default_prompt = "Ты - дружелюбный чат-бот от Qiota. Отвечай кратко и точно. Формат: Discord Markdown."
    file_path = os.path.join(BASE_DIR, f"{guild_id}_{user_id}.json")
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("system_prompt", default_prompt)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Ошибка загрузки промпта для {user_id} на сервере {guild_id}: {e}")
    return default_prompt

def save_user_prompt(user_id: str, guild_id: str, prompt: str) -> None:
    """Сохраняет промпт пользователя в JSON-файл."""
    file_path = os.path.join(BASE_DIR, f"{guild_id}_{user_id}.json")
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump({"system_prompt": prompt}, f, ensure_ascii=False)
    except IOError as e:
        logger.error(f"Ошибка сохранения промпта для {user_id} на сервере {guild_id}: {e}")

async def get_prompt(interaction: discord.Interaction, bot_client: BotClient) -> None:
    user_id = str(interaction.user.id)
    guild_id = str(interaction.guild.id) if interaction.guild else "DM"
    current_prompt = load_user_prompt(user_id, guild_id)
    await interaction.response.send_message(f"Текущий системный промпт: {current_prompt}", ephemeral=True)

async def set_prompt(interaction: discord.Interaction, bot_client: BotClient, prompt: str) -> None:
    user_id = str(interaction.user.id)
    guild_id = str(interaction.guild.id) if interaction.guild else "DM"
    save_user_prompt(user_id, guild_id, prompt)
    await interaction.response.send_message(f"Системный промпт обновлен: {prompt}", ephemeral=True)
    logger.info(f"Пользователь {user_id} обновил системный промпт на сервере {guild_id}")

async def reset_prompt(interaction: discord.Interaction, bot_client: BotClient) -> None:
    user_id = str(interaction.user.id)
    guild_id = str(interaction.guild.id) if interaction.guild else "DM"
    default_prompt = "Ты - дружелюбный чат-бот от Qiota. Отвечай кратко и точно."
    save_user_prompt(user_id, guild_id, default_prompt)
    await interaction.response.send_message("Системный промпт сброшен к стандартному", ephemeral=True)
    logger.info(f"Пользователь {user_id} сбросил системный промпт на сервере {guild_id}")

def create_command(bot_client: BotClient):
    prompt_group = app_commands.Group(name="prompt", description=description)

    @prompt_group.command(name="get", description="Показать текущий системный промпт")
    async def get_wrapper(interaction: discord.Interaction):
        await get_prompt(interaction, bot_client)

    @prompt_group.command(name="set", description="Установить персональный системный промпт")
    @app_commands.describe(prompt="Новый системный промпт")
    async def set_wrapper(interaction: discord.Interaction, prompt: str):
        await set_prompt(interaction, bot_client, prompt)

    @prompt_group.command(name="reset", description="Сбросить системный промпт к стандартному")
    async def reset_wrapper(interaction: discord.Interaction):
        await reset_prompt(interaction, bot_client)

    return prompt_group