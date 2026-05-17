"""Bot startup orchestration.

Handles Discord client initialization, command registration via core.registry,
and background services (Flask, activity).
"""

import asyncio
import time
import traceback
from threading import Thread
from typing import Optional

import discord
from discord import app_commands
from aiohttp import ClientSession

from .config import BotConfig
from .client import BotClient
from .aichat import AIChat
from .systemLog import logger, print_banner
from .utils.server.flask import run_flask
from .events.activity import set_bot_activity
from .core.registry import register_commands
from .core.session import close_connector


async def _on_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    """Global command error handler."""
    command_name = interaction.command.name if interaction.command else "unknown"
    guild_id = interaction.guild_id or "DM"
    channel_id = interaction.channel_id or "DM"

    logger.error(
        f"Ошибка команды /{command_name} для {interaction.user.id} "
        f"в {guild_id}/{channel_id}: {error}"
    )

    try:
        msg = "Произошла ошибка при выполнении команды."
        if isinstance(error, app_commands.CheckFailure):
            msg = "Ошибка выполнения команды. Пожалуйста, попробуйте снова."

        if not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)
    except Exception as e:
        logger.error(f"Не удалось отправить сообщение об ошибке: {e}")


async def start_bot() -> None:
    """Main bot runtime."""
    print_banner()
    config = BotConfig()
    config.validate()

    bot_client = BotClient(config)
    bot_client.start_time = time.time()

    ai_chat = AIChat(bot_client)

    bot_client.tree.error(_on_command_error)

    @bot_client.bot.event
    async def on_ready() -> None:
        logger.debug("on_ready запущен")
        try:
            bot_client.bot.loop.create_task(set_bot_activity(bot_client.bot))
            await register_commands(bot_client.tree, bot_client)
            logger.success(f"Бот {bot_client.bot.user} готов!")  # type: ignore[attr-defined]
        except Exception as e:
            logger.error(f"Ошибка в on_ready: {e}\n{traceback.format_exc()}")
            raise

    Thread(target=run_flask, daemon=True).start()

    try:
        await bot_client.bot.start(str(config.TOKEN))
    finally:
        await close_connector()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    import asyncio
    asyncio.run(start_bot())
