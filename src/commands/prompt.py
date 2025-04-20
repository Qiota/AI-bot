from typing import Dict, Optional
from discord import app_commands, Interaction
from ..systemLog import logger
from ..utils.firebase.firebase_manager import FirebaseManager
import functools
import time
import asyncio

DEFAULT_PROMPT = "Ты полезный и дружелюбный ассистент. Отвечай кратко, по делу, на русском языке. Учитывай контекст и предоставляй точные ответы. Время: {now}"
DEFAULT_VISION_PROMPT = "Ты эксперт по анализу изображений. Опиши изображение кратко и точно, отвечая на запрос пользователя. Время: {now}"

def ensure_firebase_initialized(func):
    """Декоратор для инициализации FirebaseManager перед выполнением функции."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        bot_client = args[-1]
        firebase_manager = await bot_client._ensure_firebase_initialized()
        kwargs["firebase_manager"] = firebase_manager
        return await func(*args, **kwargs)
    return wrapper

def validate_prompt(prompt: Optional[str], max_length: int = 500) -> None:
    """Проверяет валидность промпта."""
    if prompt:
        if len(prompt) > max_length:
            raise ValueError(f"Промпт слишком длинный (макс. {max_length} символов).")
        if "{now}" not in prompt:
            logger.warning("Промпт не содержит {now}. Рекомендуется включить для указания времени.")
        if "{user_prompt}" in prompt:
            raise ValueError("Промпт не должен содержать {user_prompt}.")

@ensure_firebase_initialized
async def save_user_prompt(
    user_id: str,
    guild_id: str,
    text_prompt: Optional[str],
    vision_prompt: Optional[str],
    bot_client,
    *,
    firebase_manager: FirebaseManager
) -> None:
    """Сохраняет кастомные промпты в Firebase и кэш, сбрасывая неуказанные промпты до стандартных."""
    if not text_prompt and not vision_prompt:
        raise ValueError("Необходимо указать хотя бы один промпт.")

    validate_prompt(text_prompt)
    validate_prompt(vision_prompt)

    cache_key = f"{guild_id}_{user_id}"
    try:
        prompt_data = {
            "text_prompt": text_prompt if text_prompt is not None else DEFAULT_PROMPT,
            "vision_prompt": vision_prompt if vision_prompt is not None else DEFAULT_VISION_PROMPT,
            "timestamp": time.time()
        }

        await firebase_manager.save_cache(f"prompts/{guild_id}/{user_id}", prompt_data)
        bot_client.prompt_cache[cache_key] = prompt_data
        logger.debug(f"Промпты сохранены для {cache_key}: {prompt_data}")
    except Exception as e:
        logger.error(f"Ошибка сохранения промптов для {cache_key}: {e}")
        raise Exception(f"Не удалось сохранить промпты: {e}")

@ensure_firebase_initialized
async def reset_user_prompt(
    user_id: str,
    guild_id: str,
    bot_client,
    *,
    firebase_manager: FirebaseManager
) -> None:
    """Сбрасывает кастомные промпты, устанавливая стандартные в Firebase и кэше."""
    cache_key = f"{guild_id}_{user_id}"
    try:
        prompt_data = {
            "text_prompt": DEFAULT_PROMPT,
            "vision_prompt": DEFAULT_VISION_PROMPT,
            "timestamp": time.time()
        }
        await firebase_manager.save_cache(f"prompts/{guild_id}/{user_id}", prompt_data)
        bot_client.prompt_cache[cache_key] = prompt_data
        logger.debug(f"Промпты сброшены для {cache_key}: {prompt_data}")
    except Exception as e:
        logger.error(f"Ошибка сброса промптов для {cache_key}: {e}")
        raise Exception(f"Не удалось сбросить промпты: {e}")

@ensure_firebase_initialized
async def load_user_prompt(
    user_id: str,
    guild_id: str,
    bot_client,
    *,
    firebase_manager: FirebaseManager
) -> Dict[str, str]:
    """Загружает кастомные промпты из Firebase или возвращает стандартные."""
    cache_key = f"{guild_id}_{user_id}"
    cache_ttl = bot_client.cache_limits.get("cache_ttl_seconds", 3600)

    cached = bot_client.prompt_cache.get(cache_key)
    if cached and cached.get("timestamp", 0) + cache_ttl > time.time():
        logger.debug(f"Промпты загружены из кэша для {cache_key}")
        return {"text_prompt": cached["text_prompt"], "vision_prompt": cached["vision_prompt"]}

    try:
        prompt_data = await firebase_manager.load_cache(f"prompts/{guild_id}/{user_id}")
        if not prompt_data:
            prompt_data = {
                "text_prompt": DEFAULT_PROMPT,
                "vision_prompt": DEFAULT_VISION_PROMPT,
                "timestamp": time.time()
            }
            await firebase_manager.save_cache(f"prompts/{guild_id}/{user_id}", prompt_data)
            logger.debug(f"Стандартные промпты сохранены в Firebase для {cache_key}")
        bot_client.prompt_cache[cache_key] = prompt_data
        logger.debug(f"Промпты загружены из Firebase для {cache_key}: {prompt_data}")
        return {"text_prompt": prompt_data["text_prompt"], "vision_prompt": prompt_data["vision_prompt"]}
    except Exception as e:
        logger.error(f"Ошибка загрузки промптов для {cache_key}: {e}")
        return {"text_prompt": DEFAULT_PROMPT, "vision_prompt": DEFAULT_VISION_PROMPT}

@ensure_firebase_initialized
async def cleanup_expired_prompts(
    bot_client,
    *,
    firebase_manager: FirebaseManager
) -> None:
    """Очищает устаревшие промпты из Firebase."""
    try:
        current_time = time.time()
        ttl_seconds = bot_client.cache_limits.get("cache_ttl_seconds", 3600) * 24 * 30
        expired_paths = []

        prompts_data = await firebase_manager.load_cache("prompts")
        if not prompts_data:
            logger.debug("Нет промптов для очистки")
            return

        for guild_id, users in prompts_data.items():
            for user_id, prompt_data in users.items():
                if current_time - prompt_data.get("timestamp", 0) > ttl_seconds:
                    expired_paths.append(f"prompts/{guild_id}/{user_id}")
                    cache_key = f"{guild_id}_{user_id}"
                    bot_client.prompt_cache.pop(cache_key, None)

        if expired_paths:
            def sync_remove_expired():
                updates = {path: None for path in expired_paths}
                firebase_manager._db.update(updates)
            await bot_client._run_sync_in_executor(sync_remove_expired)
            logger.info(f"Удалено {len(expired_paths)} устаревших промптов")
        else:
            logger.debug("Нет устаревших промптов для удаления")
    except Exception as e:
        logger.error(f"Ошибка очистки устаревших промптов: {e}")

def create_command(bot_client):
    """Создаёт команду /prompt для управления промптами."""
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
    async def prompt(interaction: Interaction, action: str, text_prompt: Optional[str] = None, vision_prompt: Optional[str] = None):
        """Обработчик команды /prompt."""
        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild.id) if interaction.guild else "DM"

        try:
            await interaction.response.defer(ephemeral=True)

            if action == "set":
                if not text_prompt and not vision_prompt:
                    message = await interaction.followup.send(
                        "Укажите хотя бы один промпт.", ephemeral=True
                    )
                    await asyncio.sleep(10)
                    await message.delete()
                    return
                await save_user_prompt(user_id, guild_id, text_prompt, vision_prompt, bot_client)
                message = await interaction.followup.send(
                    "Промпты установлены. Используйте {now} для времени.", ephemeral=True
                )
                await asyncio.sleep(10)
                await message.delete()

            elif action == "reset":
                await reset_user_prompt(user_id, guild_id, bot_client)
                message = await interaction.followup.send(
                    "Личность сброшена до стандартных промптов.", ephemeral=True
                )
                await asyncio.sleep(10)
                await message.delete()

            elif action == "view":
                prompt_data = await load_user_prompt(user_id, guild_id, bot_client)
                message = await interaction.followup.send(
                    f"Текстовый промпт: {prompt_data['text_prompt']}\nVision промпт: {prompt_data['vision_prompt']}",
                    ephemeral=True
                )
                await asyncio.sleep(10)
                await message.delete()

        except ValueError as e:
            message = await interaction.followup.send(f"Ошибка: {str(e)}", ephemeral=True)
            logger.error(f"Ошибка команды /prompt для {user_id}: {e}")
            await asyncio.sleep(10)
            await message.delete()
        except Exception as e:
            message = await interaction.followup.send("Ошибка выполнения команды. Попробуйте позже.", ephemeral=True)
            logger.error(f"Неизвестная ошибка команды /prompt для {user_id}: {e}")
            await asyncio.sleep(10)
            await message.delete()

    return prompt