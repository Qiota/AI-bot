"""Bot startup orchestration.

Handles Discord client initialization, command registration via core.registry,
and background services (Flask, activity).
"""

import asyncio
from threading import Thread
from typing import Optional

import discord
from discord import app_commands
from aiohttp import ClientSession

from .config import BotConfig
from .client import BotClient
from .aichat import AIChat
from .systemLog import logger
from .utils.server.flask import run_flask
from .events.activity import set_bot_activity
from .core.registry import register_commands
from .core.session import close_connector
import time
import traceback


async def _on_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    """Global command error handler."""
    command_name = interaction.command.name if interaction.command else "unknown"
    guild_id = interaction.guild_id or "DM"
    channel_id = interaction.channel_id or "DM"

    logger.error(
        f"Command error /{command_name} for {interaction.user.id} "
        f"in {guild_id}/{channel_id}: {error}"
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
        logger.error(f"Failed to send error message: {e}")


async def run_bot() -> None:
    """Main bot runtime."""
    config = BotConfig()
    bot_client = BotClient(config)
    bot_client.start_time = time.time()
    ai_chat = AIChat(bot_client)
    session: Optional[ClientSession] = None

    try:
        config.validate()

        bot_client.tree.error(_on_command_error)

        @bot_client.bot.event
        async def on_ready() -> None:
            logger.debug("on_ready fired")
            try:
                bot_client.bot.loop.create_task(set_bot_activity(bot_client.bot))
                await register_commands(bot_client.tree, bot_client)
                logger.success(f"Bot {bot_client.bot.user} is ready!")
            except Exception as e:
                logger.error(f"Error in on_ready: {e}\n{traceback.format_exc()}")
                raise

        # Start Flask health server
        Thread(target=run_flask, daemon=True).start()

        # Attach shared aiohttp session to bot for legacy compatibility
        session = ClientSession()
        bot_client.bot.session = session

        # Inject bot_client reference for middleware access
        bot_client.bot.bot_client = bot_client

        await bot_client.bot.start(config.TOKEN)
    except Exception as e:
        logger.error(f"Critical startup error: {e}\n{traceback.format_exc()}")
        raise
    finally:
        if session and not session.closed:
            await session.close()
        await close_connector()
        await bot_client.close()
        logger.info("Bot stopped")


def start_bot() -> None:
    """Entry point — starts the async event loop."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(run_bot())
        else:
            loop.run_until_complete(run_bot())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Critical error: {e}\n{traceback.format_exc()}")
        exit(1)


if __name__ == "__main__":
    start_bot()

