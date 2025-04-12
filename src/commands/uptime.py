import discord
from discord import app_commands
from ..config import logger
import time

description = "Показать время работы бота"

async def uptime(interaction: discord.Interaction, bot_client) -> None:
    """Команда /uptime: Показывает время работы бота."""
    uptime_seconds = int(time.time() - bot_client.start_time)
    days, remainder = divmod(uptime_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"
    await interaction.response.send_message(f"Бот работает: {uptime_str}", ephemeral=True)

def create_command(bot_client):
    @app_commands.command(name="uptime", description=description)
    async def wrapper(interaction: discord.Interaction) -> None:
        await uptime(interaction, bot_client)
    return wrapper