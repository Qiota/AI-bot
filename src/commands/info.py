"""Команда /info — информация о боте."""

import discord
from discord import app_commands, Embed
from datetime import datetime
import time

from ..systemLog import logger
from ..core.middleware import require_bot_access
from ..core.constants import COLOR_DEFAULT, ERROR_GENERIC

COMMAND_DESCRIPTION = "Отображает информацию о боте и время работы"


@require_bot_access
async def info(interaction: discord.Interaction, bot_client) -> None:
    """Команда /info: Отображает информацию о боте, включая время работы, в виде Embed."""
    await interaction.response.defer(ephemeral=True)

    try:
        embed = Embed(
            title="Информация о боте",
            description="Основные системные данные о боте:",
            color=COLOR_DEFAULT,
        )

        # Основные поля
        embed.add_field(name="Имя", value=bot_client.bot.user.name, inline=True)
        embed.add_field(name="ID", value=str(bot_client.bot.user.id), inline=True)
        embed.add_field(name="Серверов", value=str(len(bot_client.bot.guilds)), inline=True)

        # Задержка
        try:
            latency = round(bot_client.bot.latency * 1000)
            embed.add_field(name="Задержка", value=f"{latency} мс", inline=True)
        except Exception as e:
            embed.add_field(name="Задержка", value="Недоступно", inline=True)
            logger.warning(f"Ошибка получения задержки: {e}")

        # Дата создания
        try:
            created_at = bot_client.bot.user.created_at.strftime("%Y-%m-%d %H:%M:%S")
            embed.add_field(name="Дата создания", value=created_at, inline=True)
        except Exception as e:
            embed.add_field(name="Дата создания", value="Недоступно", inline=True)
            logger.warning(f"Ошибка получения даты создания: {e}")

        # Информация о шардах
        shard_count = bot_client.bot.shard_count or 1
        shard_id = interaction.guild.shard_id if interaction.guild else 0
        embed.add_field(name="Шарды", value=f"{shard_id + 1}/{shard_count}", inline=True)

        # Время работы
        try:
            uptime_seconds = int(time.time() - bot_client.start_time)
            days, remainder = divmod(uptime_seconds, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            parts = []
            if days:
                parts.append(f"{days} дн")
            if hours or days:
                parts.append(f"{hours} ч")
            if minutes or hours or days:
                parts.append(f"{minutes} мин")
            parts.append(f"{seconds} сек")
            uptime_str = " ".join(parts)
            embed.add_field(name="Время работы", value=uptime_str, inline=True)
        except AttributeError as e:
            embed.add_field(name="Время работы", value="Недоступно", inline=True)
            logger.warning(f"Ошибка доступа к start_time: {e}")

        # Аватар
        if bot_client.bot.user.avatar:
            try:
                embed.set_thumbnail(url=bot_client.bot.user.avatar.url)
            except Exception as e:
                logger.warning(f"Ошибка установки аватара: {e}")

        embed.set_footer(text=f"Запрос от {interaction.user.name} (ID: {interaction.user.id})")
        embed.timestamp = datetime.utcnow()

        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Ошибка команды /info для пользователя {interaction.user.id}: {e}")
        await interaction.followup.send(ERROR_GENERIC, ephemeral=True)


def create_command(bot_client) -> app_commands.Command:
    """Создаёт команду /info."""
    @app_commands.command(name="info", description=COMMAND_DESCRIPTION)
    async def info_command(interaction: discord.Interaction) -> None:
        await info(interaction, bot_client)

    return info_command

