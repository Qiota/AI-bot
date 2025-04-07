import discord
from discord import app_commands
from ..config import logger

description = "Закрывает текущую ветку"

async def closethread(interaction: discord.Interaction, bot_client) -> None:
    """Команда /closethread: Закрывает текущую ветку."""
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message("Команда доступна только в ветках.", ephemeral=True)
        return

    try:
        await interaction.channel.edit(archived=True, locked=True)
        await interaction.response.send_message("Ветка закрыта.", ephemeral=True)
    except discord.HTTPException as e:
        if "403 Forbidden (error code: 50001): Missing Access" in str(e):
            await interaction.response.send_message("Ошибка: Боту не хватает прав доступа для закрытия ветки.", ephemeral=True)
            logger.warning(f"Боту не хватает прав доступа для закрытия ветки {interaction.channel.id}.")
        else:
            await interaction.response.send_message(f"Ошибка закрытия ветки: {e}", ephemeral=True)
            logger.error(f"Ошибка HTTP в /closethread для пользователя {interaction.user.id}: {e}")

def create_command(bot_client):
    @app_commands.command(name="closethread", description=description)
    async def wrapper(interaction: discord.Interaction) -> None:
        await closethread(interaction, bot_client)
    return wrapper