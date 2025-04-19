import discord
from discord import app_commands
from ..systemLog import logger
from typing import Dict
from ..firebase.firebase_manager import FirebaseManager
import traceback

default_prompt = (
    "Ты полезный и дружелюбный ассистент. Отвечай кратко, по делу, на русском языке. "
    "Учитывай контекст и предоставляй точные ответы. Время: {now}"
)

default_vision_prompt = (
    "Ты эксперт по анализу изображений. Опиши изображение кратко и точно, отвечая на запрос пользователя. "
    "Время: {now}"
)

async def save_user_prompt(user_id: str, guild_id: str, text_prompt: str = None, vision_prompt: str = None, bot_client=None) -> None:
    """Сохранение кастомных промптов в Firebase для применения к каждому запросу."""
    cache_key = f"{guild_id}_{user_id}"
    
    # Валидация: нужен хотя бы один промпт
    if not text_prompt and not vision_prompt:
        raise ValueError("Необходимо указать хотя бы один промпт (текстовый или vision).")
    
    if text_prompt and (len(text_prompt) > 500 or "{user_prompt}" in text_prompt):
        raise ValueError("Текстовый промпт слишком длинный (макс. 500 символов) или содержит {user_prompt}.")
    
    if vision_prompt and (len(vision_prompt) > 500 or "{user_prompt}" in vision_prompt):
        raise ValueError("Vision промпт слишком длинный (макс. 500 символов) или содержит {user_prompt}.")
    
    # Загружаем текущие промпты, чтобы сохранить существующие, если новый не указан
    current_prompts = await load_user_prompt(user_id, guild_id, bot_client)
    prompt_data = {
        "text_prompt": text_prompt if text_prompt is not None else current_prompts["text_prompt"],
        "vision_prompt": vision_prompt if vision_prompt is not None else current_prompts["vision_prompt"]
    }
    
    try:
        firebase_manager = await bot_client._ensure_firebase_initialized()
        await firebase_manager.save_cache(f"prompts/{guild_id}/{user_id}", prompt_data)
        bot_client.prompt_cache[cache_key] = prompt_data
        logger.debug(f"Промпты сохранены для {cache_key}: {prompt_data}")
    except Exception as e:
        logger.error(f"Ошибка сохранения промптов для {cache_key}: {e}\n{traceback.format_exc()}")
        raise

async def load_user_prompt(user_id: str, guild_id: str, bot_client) -> Dict:
    """Загрузка кастомных промптов из Firebase для каждого запроса."""
    cache_key = f"{guild_id}_{user_id}"
    try:
        firebase_manager = await bot_client._ensure_firebase_initialized()
        prompt_data = await firebase_manager.load_cache(f"prompts/{guild_id}/{user_id}")
        if not prompt_data:
            prompt_data = {"text_prompt": default_prompt, "vision_prompt": default_vision_prompt}
            await firebase_manager.save_cache(f"prompts/{guild_id}/{user_id}", prompt_data)
        bot_client.prompt_cache[cache_key] = prompt_data
        logger.debug(f"Промпты загружены для {cache_key}: {prompt_data}")
        return prompt_data
    except Exception as e:
        logger.error(f"Ошибка загрузки промптов для {cache_key}: {e}\n{traceback.format_exc()}")
        return {"text_prompt": default_prompt, "vision_prompt": default_vision_prompt}

def create_command(bot_client):
    """Создание команды /prompt для управления промптами."""
    @app_commands.command(name="prompt", description="Управление кастомными промптами для сохранения личности")
    @app_commands.describe(
        action="Действие: установить, сбросить или посмотреть промпт",
        text_prompt="Кастомный промпт для текстовых запросов (определяет стиль ответа)",
        vision_prompt="Кастомный промпт для vision запросов (определяет стиль описания изображений)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="set", value="set"),
        app_commands.Choice(name="reset", value="reset"),
        app_commands.Choice(name="view", value="view")
    ])
    async def prompt(interaction: discord.Interaction, action: str, text_prompt: str = None, vision_prompt: str = None):
        """Обработчик команды /prompt."""
        await interaction.response.defer(ephemeral=True)
        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild.id) if interaction.guild else "DM"

        try:
            if action == "set":
                if not text_prompt and not vision_prompt:
                    await interaction.followup.send("Укажите хотя бы один промпт.", ephemeral=True)
                    return
                await save_user_prompt(user_id, guild_id, text_prompt, vision_prompt, bot_client)
                await interaction.followup.send("Промпты установлены.", ephemeral=True)
            
            elif action == "reset":
                await save_user_prompt(user_id, guild_id, default_prompt, default_vision_prompt, bot_client)
                await interaction.followup.send("Промпты сброшены.", ephemeral=True)
            
            elif action == "view":
                prompt_data = await load_user_prompt(user_id, guild_id, bot_client)
                await interaction.followup.send(
                    f"Текстовый промпт: {prompt_data['text_prompt']}\nVision промпт: {prompt_data['vision_prompt']}",
                    ephemeral=True
                )
        
        except Exception as e:
            logger.error(f"Ошибка команды /prompt для {user_id}: {e}\n{traceback.format_exc()}")
            await interaction.followup.send("Ошибка выполнения команды.", ephemeral=True)

    return prompt