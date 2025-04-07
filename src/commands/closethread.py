import discord
from discord import app_commands
from ..config import logger

description = "Закрывает текущую приватную ветку"

async def closethread(interaction: discord.Interaction, bot_client) -> None:
    """Команда /closethread: Закрывает текущую приватную ветку."""
    if not isinstance(interaction.channel, discord.Thread) or interaction.channel.type != discord.ChannelType.private_thread:
        await interaction.response.send_message("Команда доступна только в приватных ветках.", ephemeral=True)
        return

    try:
        await interaction.channel.edit(archived=True, locked=True)
        await interaction.response.send_message("Приватная ветка закрыта.", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.response.send_message(f"Ошибка закрытия ветки: {e}", ephemeral=True)
        logger.error(f"Ошибка HTTP в /closethread для пользователя {interaction.user.id}: {e}")

def create_command(bot_client):
    @app_commands.command(name="closethread", description=description)
    async def wrapper(interaction: discord.Interaction) -> None:
        await closethread(interaction, bot_client)
    return wrapper