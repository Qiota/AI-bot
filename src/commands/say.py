import discord
from discord import app_commands
from ..config import logger

description = "Сказать что-то от имени бота"

async def say(interaction: discord.Interaction, message: str, bot_client) -> None:
    """Команда /say: Отправляет сообщение от имени бота."""
    if interaction.guild is None and interaction.user.id != bot_client.config.DEVELOPER_ID:
        await interaction.response.send_message("Команда доступна только на сервере или разработчику.", ephemeral=True)
        logger.warning(f"Пользователь {interaction.user.id} попытался использовать /say вне сервера без прав разработчика.")
        return

    if interaction.guild and not (interaction.user.id == bot_client.config.DEVELOPER_ID or (
            isinstance(interaction.user, discord.Member) and (
                interaction.user.guild_permissions.administrator or
                interaction.user.guild_permissions.manage_channels or
                interaction.user.guild_permissions.manage_messages
            )
    )):
        await interaction.response.send_message("Нет прав для выполнения команды.", ephemeral=True)
        logger.warning(f"Пользователь {interaction.user.id} попытался использовать /say без необходимых прав.")
        return

    await interaction.response.defer(ephemeral=True)

    try:
        await interaction.channel.send(content=message)
        await interaction.delete_original_response()
        logger.info(f"Команда /say выполнена пользователем {interaction.user.id}: сообщение '{message}' отправлено.")
    except discord.HTTPException as e:
        if e.code == 50035:
            await interaction.edit_original_response(content="Сообщение слишком длинное (максимум 2000 символов).")
            logger.error(f"Ошибка в /say для {interaction.user.id}: сообщение слишком длинное.")
        elif e.code == 50006:
            await interaction.edit_original_response(content="Нельзя отправить пустое сообщение.")
            logger.error(f"Ошибка в /say для {interaction.user.id}: пустое сообщение.")
        else:
            await interaction.edit_original_response(content=f"Ошибка отправки: {e}")
            logger.error(f"Ошибка HTTP в /say для {interaction.user.id}: {e}")
    except Exception as e:
        await interaction.edit_original_response(content=f"Неизвестная ошибка: {e}")
        logger.error(f"Неизвестная ошибка в /say для {interaction.user.id}: {e}")

def create_command(bot_client):
    @app_commands.command(name="say", description=description)
    @app_commands.describe(message="Текст, который бот должен отправить")
    async def wrapper(interaction: discord.Interaction, message: str) -> None:
        await say(interaction, message, bot_client)
    return wrapper