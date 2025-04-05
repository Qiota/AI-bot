import discord
from discord import app_commands
from ..config import logger

SUCCESS_MESSAGE = "Память вашего разговора успешно удалена!"
ERROR_MESSAGE = "Ошибка при очистке памяти. Попробуйте позже."
description = "Очищает контекст и историю"

async def clearmemory(interaction: discord.Interaction, bot_client) -> None:
    """Команда /clearmemory: Очищает контекст и историю."""
    await interaction.response.defer(ephemeral=True)

    try:
        if bot_client.db is None:
            raise ValueError("База данных не инициализирована")
        await bot_client.db.clear_user_data(str(interaction.user.id))
        await interaction.followup.send(SUCCESS_MESSAGE, ephemeral=True)
    except Exception as e:
        logger.error(f"Ошибка очистки памяти {interaction.user.id}: {e}")
        await interaction.followup.send(ERROR_MESSAGE, ephemeral=True)

def create_command(bot_client):
    @app_commands.command(name="clearmemory", description=description)
    async def wrapper(interaction: discord.Interaction) -> None:
        await clearmemory(interaction, bot_client)
    return wrapper