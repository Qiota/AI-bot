"""Command middleware / decorators for common access checks.

Usage:
    @app_commands.command(name="info", description="...")
    @require_bot_access
    async def info_cmd(interaction: discord.Interaction):
        ...
"""

import functools
from typing import Callable, Optional

import discord
from discord import app_commands

from ..systemLog import logger
from ..utils.checker import checker
from .constants import (
    ERROR_ACCESS_DENIED,
    ERROR_ADMIN_ONLY,
    ERROR_CONFIG,
    ERROR_GENERIC,
    ERROR_GUILD_ONLY,
    ERROR_NOT_READY,
    ERROR_NSFW_ONLY,
    ERROR_RESTRICTED,
)


async def _send_or_followup(
    interaction: discord.Interaction, message: str, ephemeral: bool = True
) -> None:
    """Sends a message, handling both response and followup states."""
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=ephemeral)
        else:
            await interaction.followup.send(message, ephemeral=ephemeral)
    except Exception as e:
        logger.error(f"Failed to send error message: {e}")


def require_bot_ready(func: Callable) -> Callable:
    """Ensures the bot is ready before executing the command."""
    @functools.wraps(func)
    async def wrapper(interaction: discord.Interaction, *args, **kwargs):
        # bot_client is expected to be injected or available via interaction.client
        bot = getattr(interaction, "_bot_client", None) or getattr(
            interaction.client, "bot_client", None
        )
        if bot is None or not hasattr(bot, "bot") or not bot.bot.is_ready():
            logger.warning(f"Command {interaction.command.name} blocked: bot not ready")
            await _send_or_followup(interaction, ERROR_NOT_READY)
            return
        return await func(interaction, *args, **kwargs)

    return wrapper


def require_guild(func: Callable) -> Callable:
    """Restricts command to guild channels only."""
    @functools.wraps(func)
    async def wrapper(interaction: discord.Interaction, *args, **kwargs):
        if interaction.guild is None:
            await _send_or_followup(interaction, ERROR_GUILD_ONLY)
            return
        return await func(interaction, *args, **kwargs)

    return wrapper


def require_nsfw(func: Callable) -> Callable:
    """Ensures the command is used in an NSFW channel or DM."""
    @functools.wraps(func)
    async def wrapper(interaction: discord.Interaction, *args, **kwargs):
        if interaction.guild is not None and not interaction.channel.nsfw:
            await _send_or_followup(interaction, ERROR_NSFW_ONLY)
            return
        return await func(interaction, *args, **kwargs)

    return wrapper


def require_bot_access(func: Callable) -> Callable:
    """Checks bot channel access and user restrictions."""
    @functools.wraps(func)
    async def wrapper(interaction: discord.Interaction, *args, **kwargs):
        bot = getattr(interaction, "_bot_client", None) or getattr(
            interaction.client, "bot_client", None
        )
        if bot is None:
            logger.error("BotClient not available for access check")
            await _send_or_followup(interaction, ERROR_CONFIG)
            return

        # DM always allowed
        if interaction.guild is None:
            return await func(interaction, *args, **kwargs)

        # Bot permissions in channel
        permissions = interaction.channel.permissions_for(interaction.guild.me)
        if not (permissions.read_messages and permissions.send_messages and permissions.embed_links):
            await _send_or_followup(interaction, ERROR_ACCESS_DENIED)
            return

        # Guild config existence (quick check via restrict_command_execution logic)
        from ..commands.restrict import restrict_command_execution

        ok, reason = await restrict_command_execution(interaction, bot)
        if not ok:
            await _send_or_followup(interaction, reason or ERROR_CONFIG)
            return

        # User restriction check
        restriction_ok, restriction_reason = await checker.check_user_restriction(
            interaction
        )
        if not restriction_ok:
            await _send_or_followup(
                interaction, restriction_reason or ERROR_RESTRICTED
            )
            return

        return await func(interaction, *args, **kwargs)

    return wrapper


def require_admin_or_developer(func: Callable) -> Callable:
    """Restricts command to guild administrators or the configured developer."""
    @functools.wraps(func)
    async def wrapper(interaction: discord.Interaction, *args, **kwargs):
        from decouple import config

        dev_id = config("DEVELOPER_ID", cast=int, default=None)
        is_admin = (
            interaction.guild is not None
            and interaction.user.guild_permissions.administrator
        )
        is_dev = dev_id is not None and interaction.user.id == dev_id

        if not is_admin and not is_dev:
            await _send_or_followup(interaction, ERROR_ADMIN_ONLY)
            return
        return await func(interaction, *args, **kwargs)

    return wrapper


def require_permissions(**perms: bool) -> Callable:
    """Factory for permission-based checks (e.g., manage_messages=True)."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(interaction: discord.Interaction, *args, **kwargs):
            if interaction.guild is None:
                # In DMs, allow only developer
                from decouple import config
                dev_id = config("DEVELOPER_ID", cast=int, default=None)
                if dev_id and interaction.user.id == dev_id:
                    return await func(interaction, *args, **kwargs)
                await _send_or_followup(interaction, ERROR_GUILD_ONLY)
                return

            missing = [
                name
                for name, value in perms.items()
                if not getattr(interaction.user.guild_permissions, name, False)
            ]
            if missing:
                await _send_or_followup(
                    interaction,
                    f"Требуются права: {', '.join(missing)}",
                )
                return
            return await func(interaction, *args, **kwargs)

        return wrapper

    return decorator

