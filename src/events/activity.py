import discord
from datetime import datetime
from ..systemLog import logger

async def set_bot_activity(bot: discord.Client):
    """Устанавливает активность бота."""
    try:
        activity = discord.Activity(
            type=discord.ActivityType.watching,
            name=f"версия от {datetime.now().strftime('%d.%m')}"
        )
        await bot.change_presence(activity=activity)
        logger.success(f"Бот {bot.user.name} готов, активность установлена")
        logger.info(f"Подключено к {len(bot.guilds)} серверам")
    except Exception as e:
        logger.error(f"Ошибка установки активности: {e}")