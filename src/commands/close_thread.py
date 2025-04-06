import discord
from discord import app_commands
from ..config import logger

description = "Закрыть текущую ветку"

async def close_thread(interaction: discord.Interaction, bot_client) -> None:
    """Команда /close_thread: Закрывает текущую ветку."""
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message("Эта команда работает только в ветках! 😓", ephemeral=True)
        logger.warning(f"Команда /close_thread вызвана вне ветки пользователем {interaction.user.id} в канале {interaction.channel.id}.")
        return
    if not interaction.channel.permissions_for(interaction.guild.me).manage_threads:
        await interaction.response.send_message("У меня нет прав на закрытие веток! 😓", ephemeral=True)
        logger.warning(f"Бот не имеет прав на закрытие веток в канале {interaction.channel.id} для команды /close_thread.")
        return
    try:
        await interaction.response.send_message("Закрываю ветку... 🔒", ephemeral=True)
        await interaction.channel.edit(archived=True, locked=True)
        await interaction.channel.send("Ветка закрыта! 🔒")
        logger.info(f"Команда /close_thread выполнена пользователем {interaction.user.id}: ветка {interaction.channel.id} закрыта.")
    except discord.HTTPException as e:
        await interaction.response.send_message(f"Ошибка закрытия ветки: {e}", ephemeral=True)
        logger.error(f"Ошибка HTTP в /close_thread для {interaction.user.id}: {e}")

def create_command(bot_client):
    @app_commands.command(name="close_thread", description=description)
    async def wrapper(interaction: discord.Interaction) -> None:
        await close_thread(interaction, bot_client)
    return wrapper