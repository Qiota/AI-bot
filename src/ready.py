import discord
from datetime import datetime
from .logging_config import logger

async def set_activity(client: discord.Client):
    """Устанавливает активность бота."""
    try:
        activity = discord.Activity(
            type=discord.ActivityType.watching,
            name=f"обновление {datetime.now().strftime('%d.%m')}"
        )
        await client.change_presence(activity=activity)
        logger.success("Активность бота установлена")
    except Exception as e:
        logger.error(f"Ошибка установки активности: {e}")
        raise