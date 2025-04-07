import discord
from discord import app_commands
from ..config import logger

description = "Закрывает текущую приватную ветку"

async def closethread(interaction: discord.Interaction, bot_client) -> None:
    """Команда /closethread: Закрывает текущую приватную ветку."""
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message("Команда доступна только в ветках.", ephemeral=True)
        logger.warning(f"Команда /closethread вызвана вне ветки пользователем {interaction.user.id} в канале {interaction.channel.id}.")
        return

    if not interaction.channel.permissions_for(interaction.guild.me).manage_threads:
        await interaction.response.send_message("Отсутствуют права на закрытие веток.", ephemeral=True)
        logger.warning(f"Бот не имеет прав на закрытие веток в канале {interaction.channel.id} для команды /closethread.")
        return

    try:
        await interaction.response.send_message("Закрытие ветки.", ephemeral=True)
        await interaction.channel.edit(archived=True, locked=True)
        await interaction.channel.send("Приватная ветка закрыта.")
        logger.info(f"Команда /closethread выполнена пользователем {interaction.user.id}: приватная ветка {interaction.channel.id} закрыта.")
    except discord.HTTPException as e:
        await interaction.response.send_message(f"Ошибка закрытия ветки: {e}", ephemeral=True)
        logger.error(f"Ошибка HTTP в /closethread для пользователя {interaction.user.id}: {e}")

def create_command(bot_client):
    @app_commands.command(name="closethread", description=description)
    async def wrapper(interaction: discord.Interaction) -> None:
        await closethread(interaction, bot_client)
    return wrapper