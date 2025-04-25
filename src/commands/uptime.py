import discord
from discord import app_commands
import time
from ..systemLog import logger
from .restrict import check_bot_access, restrict_command_execution

description = "Показать время работы бота"

async def uptime(interaction: discord.Interaction, bot_client) -> None:
    """Команда /uptime: Показывает время работы бота."""
    if bot_client is None:
        logger.error("bot_client не предоставлен для команды /uptime")
        await interaction.response.send_message("Ошибка конфигурации бота.", ephemeral=True)
        return

    # Проверка выполнения команды
    if not await restrict_command_execution(interaction, bot_client):
        return

    # Проверка доступа к каналу
    access_result, access_reason = await check_bot_access(interaction, bot_client)
    if not access_result:
        await interaction.response.send_message(
            access_reason or "Бот не имеет доступа к этому каналу.",
            ephemeral=True
        )
        return

    try:
        uptime_seconds = int(time.time() - bot_client.start_time)
        days, remainder = divmod(uptime_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"
        await interaction.response.send_message(f"Бот работает: {uptime_str}", ephemeral=True)
    except AttributeError as e:
        logger.error(f"Ошибка доступа к start_time для команды /uptime: {e}")
        await interaction.response.send_message("Ошибка: Время запуска бота недоступно.", ephemeral=True)
    except Exception as e:
        logger.error(f"Неизвестная ошибка в /uptime для {interaction.user.id}: {e}")
        await interaction.response.send_message("Произошла неизвестная ошибка.", ephemeral=True)

def create_command(bot_client):
    """Создаёт команду /uptime."""
    @app_commands.command(name="uptime", description=description)
    async def wrapper(interaction: discord.Interaction) -> None:
        await uptime(interaction, bot_client)
    return wrapper