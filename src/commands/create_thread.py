import discord
from discord import app_commands
from ..config import logger

description = "Создать новую ветку для обсуждения"

async def create_thread(interaction: discord.Interaction, name: str, bot_client) -> None:
    """Команда /create_thread: Создает новую ветку для обсуждения."""
    if not interaction.channel.permissions_for(interaction.guild.me).create_public_threads:
        await interaction.response.send_message("У меня нет прав на создание веток! 😓", ephemeral=True)
        logger.warning(f"Бот не имеет прав на создание веток в канале {interaction.channel.id} для команды /create_thread.")
        return
    try:
        thread = await interaction.channel.create_thread(
            name=name,
            type=discord.ChannelType.public_thread,
            auto_archive_duration=60
        )
        await interaction.response.send_message(f"Ветка {thread.mention} создана! 🎉", ephemeral=True)
        await thread.send(f"Ветка **{name}** создана! Давай обсуждать. 😊")
        logger.info(f"Команда /create_thread выполнена пользователем {interaction.user.id}: создана ветка '{name}'.")
    except discord.HTTPException as e:
        await interaction.response.send_message(f"Ошибка создания ветки: {e}", ephemeral=True)
        logger.error(f"Ошибка HTTP в /create_thread для {interaction.user.id}: {e}")

def create_command(bot_client):
    @app_commands.command(name="create_thread", description=description)
    @app_commands.describe(name="Название ветки")
    async def wrapper(interaction: discord.Interaction, name: str) -> None:
        await create_thread(interaction, name, bot_client)
    return wrapper