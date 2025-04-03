import discord
from discord import app_commands
from ..logging_config import logger

async def clearmemory(interaction: discord.Interaction, bot_client) -> None:
    """Команда /clearmemory: Удаляет память пользователя (контекст и историю)."""
    user_id = str(interaction.user.id)
    try:
        await bot_client.db.clear_user_data(user_id)
        await interaction.response.send_message("Ваша память успешно очищена!", ephemeral=True)
    except Exception as e:
        logger.error(f"Ошибка при очистке памяти пользователя {user_id}: {e}")
        await interaction.response.send_message("Произошла ошибка при очистке памяти. Попробуйте позже.", ephemeral=True)