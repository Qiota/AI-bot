import discord
from discord import app_commands, Embed
from datetime import datetime
from typing import Optional, Tuple
import time
from ..systemLog import logger
from .restrict import check_bot_access, restrict_command_execution

# Константы
COLOR_DEFAULT = discord.Color.dark_grey()
ERROR_MESSAGE = "Произошла ошибка при выполнении команды."
CONFIG_ERROR = "Ошибка конфигурации бота."
ACCESS_DENIED = "Бот не имеет доступа к этому каналу."
COMMAND_DESCRIPTION = "Отображает информацию о боте и время работы"

async def check_access(
    interaction: discord.Interaction, 
    bot_client: Optional[object]
) -> Tuple[bool, Optional[str]]:
    """Проверяет доступ бота и ограничения команды.

    Args:
        interaction: Взаимодействие с пользователем.
        bot_client: Клиент бота.

    Returns:
        Tuple[bool, Optional[str]]: Результат проверки и причина отказа (если есть).
    """
    if bot_client is None:
        logger.error("bot_client не предоставлен")
        return False, CONFIG_ERROR
    
    if not await restrict_command_execution(interaction, bot_client):
        return False, None
    
    access_result, access_reason = await check_bot_access(interaction, bot_client)
    if not access_result:
        return False, access_reason or ACCESS_DENIED
    
    return True, None

async def info(interaction: discord.Interaction, bot_client: Optional[object]) -> None:
    """Команда /info: Отображает информацию о боте, включая время работы, в виде Embed.

    Args:
        interaction: Взаимодействие с пользователем.
        bot_client: Клиент бота.
    """
    access_result, access_reason = await check_access(interaction, bot_client)
    if not access_result:
        await interaction.response.send_message(access_reason, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        embed = Embed(
            title="Информация о боте",
            description="Основные системные данные о боте:",
            color=COLOR_DEFAULT
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

        # Установка аватара
        if bot_client.bot.user.avatar:
            try:
                embed.set_thumbnail(url=bot_client.bot.user.avatar.url)
            except Exception as e:
                logger.warning(f"Ошибка установки аватара: {e}")

        # Футер и временная метка
        embed.set_footer(text=f"Запрос от {interaction.user.name} (ID: {interaction.user.id})")
        embed.timestamp = datetime.utcnow()

        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Ошибка команды /info для пользователя {interaction.user.id}: {e}")
        await interaction.followup.send(ERROR_MESSAGE, ephemeral=True)

def create_command(bot_client: object) -> app_commands.Command:
    """Создаёт команду /info.

    Args:
        bot_client: Клиент бота.

    Returns:
        app_commands.Command: Объект команды /info.
    """
    @app_commands.command(name="info", description=COMMAND_DESCRIPTION)
    async def info_command(interaction: discord.Interaction) -> None:
        await info(interaction, bot_client)
    return info_command