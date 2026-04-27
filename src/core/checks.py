import discord
from typing import Tuple, Optional
from ..systemLog import logger


async def check_nsfw(interaction: discord.Interaction) -> Tuple[bool, Optional[str]]:
    """
    Проверяет, что команда NSFW вызвана в ЛС или в NSFW-канале на сервере.

    Returns:
        Tuple[bool, Optional[str]]: (True, None) если доступ разрешён,
        (False, reason) если доступ запрещён.
    """
    if interaction.guild is None:
        # Личные сообщения — разрешено
        return True, None

    if not interaction.channel.nsfw:
        return False, "Команда доступна только в NSFW-каналах или ЛС."

    return True, None


async def check_guild_access(interaction: discord.Interaction, bot_client) -> Tuple[bool, Optional[str]]:
    """
    Проверяет, что бот присутствует на сервере (если команда вызвана на сервере).

    Returns:
        Tuple[bool, Optional[str]]: (True, None) если доступ разрешён,
        (False, reason) если доступ запрещён.
    """
    if interaction.guild is None:
        return True, None

    if interaction.guild not in [g for g in bot_client.bot.guilds]:
        return False, "Бот отсутствует на сервере!"

    return True, None

