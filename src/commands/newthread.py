import discord
from discord import app_commands
from ..config import logger

description = "Создаёт новую ветку для обсуждения."

async def newthread(interaction: discord.Interaction, name: str, bot_client, private: bool = False) -> None:
    """Команда /newthread: Создаёт новую ветку для обсуждения."""
    if isinstance(interaction.channel, discord.DMChannel):
        await interaction.response.send_message("Команда недоступна в личных сообщениях.", ephemeral=True)
        return

    if len(name) > 100:
        await interaction.response.send_message("Имя ветки превышает 100 символов.", ephemeral=True)
        return

    thread_type = discord.ChannelType.private_thread if private else discord.ChannelType.public_thread
    thread_type_str = "приватная" if private else "публичная"

    try:
        thread = await interaction.channel.create_thread(
            name=name,
            type=thread_type,
            auto_archive_duration=60
        )

        await thread.edit(creator_id=interaction.user.id)
        await interaction.response.send_message(f"{thread_type_str.capitalize()} ветка {thread.mention} создана.", ephemeral=True)
        await thread.send(f"{thread_type_str.capitalize()} ветка {name} создана для обсуждения пользователем {interaction.user.mention}.")
    except discord.HTTPException as e:
        if "403 Forbidden (error code: 50001): Missing Access" in str(e):
            await interaction.response.send_message("Ошибка: Боту не хватает прав доступа для создания ветки.", ephemeral=True)
            logger.warning(f"Боту не хватает прав доступа для создания {thread_type_str} ветки в канале {interaction.channel.id}.")
        else:
            await interaction.response.send_message(f"Ошибка создания ветки: {e}", ephemeral=True)
            logger.error(f"Ошибка HTTP в /newthread для пользователя {interaction.user.id}: {e}")

def create_command(bot_client):
    @app_commands.command(name="newthread", description=description)
    @app_commands.describe(
        name="Название новой ветки",
        private="Создать приватную ветку? (по умолчанию: публичная)"
    )
    async def wrapper(interaction: discord.Interaction, name: str, private: bool = False) -> None:
        await newthread(interaction, name, bot_client, private)
    return wrapper