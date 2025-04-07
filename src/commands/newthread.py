import discord
from discord import app_commands
from ..config import logger

description = "Создаёт новую приватную ветку для обсуждения"

async def newthread(interaction: discord.Interaction, name: str, bot_client) -> None:
    """Команда /newthread: Создаёт новую приватную ветку для обсуждения."""
    if not interaction.channel.permissions_for(interaction.guild.me).create_private_threads:
        await interaction.response.send_message("Отсутствуют права на создание приватных веток.", ephemeral=True)
        logger.warning(f"Бот не имеет прав на создание приватных веток в канале {interaction.channel.id} для команды /newthread.")
        return

    if len(name) > 100:
        await interaction.response.send_message("Имя ветки превышает 100 символов.", ephemeral=True)
        logger.warning(f"Пользователь {interaction.user.id} указал имя ветки длиной {len(name)} символов: {name}")
        return

    try:
        thread = await interaction.channel.create_thread(
            name=name,
            type=discord.ChannelType.private_thread,
            auto_archive_duration=60
        )
        await interaction.response.send_message(f"Приватная ветка {thread.mention} создана.", ephemeral=True)
        await thread.send(f"Приватная ветка {name} создана для обсуждения.")
        logger.info(f"Команда /newthread выполнена пользователем {interaction.user.id}: создана приватная ветка '{name}' (ID: {thread.id}).")
    except discord.HTTPException as e:
        await interaction.response.send_message(f"Ошибка создания ветки: {e}", ephemeral=True)
        logger.error(f"Ошибка HTTP в /newthread для пользователя {interaction.user.id}: {e}")

def create_command(bot_client):
    @app_commands.command(name="newthread", description=description)
    @app_commands.describe(name="Название новой приватной ветки")
    async def wrapper(interaction: discord.Interaction, name: str) -> None:
        await newthread(interaction, name, bot_client)
    return wrapper