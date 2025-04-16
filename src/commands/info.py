import discord
from discord import app_commands, Embed
from datetime import datetime
from ..logging_config import logger

description = "Отображает информацию о боте"

async def info(interaction: discord.Interaction, bot_client) -> None:
    """Команда /info: Отображает информацию о боте в виде Embed."""
    await interaction.response.defer(ephemeral=True)

    try:
        embed = Embed(
            title="Информация о боте",
            color=discord.Color.dark_grey(),
            description="Системные данные о боте:"
        )

        embed.add_field(name="Имя", value=bot_client.bot.user.name, inline=True)
        embed.add_field(name="ID", value=str(bot_client.bot.user.id), inline=True)
        embed.add_field(name="Серверов", value=str(len(bot_client.bot.guilds)), inline=True)

        try:
            latency = round(bot_client.bot.latency * 1000)
            embed.add_field(name="Задержка", value=f"{latency} мс", inline=True)
        except Exception as e:
            embed.add_field(name="Задержка", value="Недоступно", inline=True)
            logger.warning(f"Ошибка получения задержки для команды /info: {e}")

        try:
            created_at = bot_client.bot.user.created_at.strftime("%Y-%m-%d %H:%M:%S")
            embed.add_field(name="Создан", value=created_at, inline=True)
        except Exception as e:
            embed.add_field(name="Создан", value="Недоступно", inline=True)
            logger.warning(f"Ошибка получения даты создания для команды /info: {e}")

        shard_count = bot_client.bot.shard_count or 1
        shard_id = interaction.guild.shard_id if interaction.guild else 0
        embed.add_field(name="Шарды", value=f"{shard_id + 1}/{shard_count}", inline=True)

        if bot_client.bot.user.avatar:
            try:
                embed.set_thumbnail(url=bot_client.bot.user.avatar.url)
            except Exception as e:
                logger.warning(f"Ошибка установки аватара для команды /info: {e}")

        embed.timestamp = datetime.utcnow()

        embed.set_footer(text=f"Запрос от {interaction.user.name} (ID: {interaction.user.id})")

        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Ошибка команды /info для пользователя {interaction.user.id}: {e}")
        await interaction.followup.send("Ошибка при получении информации.", ephemeral=True)

def create_command(bot_client):
    @app_commands.command(name="info", description=description)
    async def wrapper(interaction: discord.Interaction) -> None:
        await info(interaction, bot_client)
    return wrapper