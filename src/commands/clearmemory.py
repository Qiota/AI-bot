import discord
from typing import TYPE_CHECKING
from ..logging_config import logger

if TYPE_CHECKING:
    from ..client import BotClient

SUCCESS_MESSAGE = "Память вашего разговора успешно удалена!"
ERROR_MESSAGE = "Ошибка при очистке памяти. Попробуйте позже."

async def clearmemory(interaction: discord.Interaction, bot_client: "BotClient") -> None:
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