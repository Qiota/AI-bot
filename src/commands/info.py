import discord
from discord import app_commands, Embed
from ..config import logger

description = "Информация о боте"

async def info(interaction: discord.Interaction, bot_client) -> None:
    """Команда /info: Показывает информацию о боте в виде Embed."""
    await interaction.response.defer(ephemeral=True)

    try:
        embed = Embed(
            title="Информация о боте",
            color=discord.Color.blue(),
            description="Основные данные о боте:"
        )
        
        embed.add_field(name="Бот", value=bot_client.bot.user.name, inline=True)
        embed.add_field(name="ID", value=str(bot_client.bot.user.id), inline=True)
        embed.add_field(name="Серверов", value=str(len(bot_client.bot.guilds)), inline=True)
        embed.add_field(name="Задержка", value=f"{round(bot_client.bot.latency * 1000)} мс", inline=True)

        if bot_client.bot.user.avatar:
            embed.set_thumbnail(url=bot_client.bot.user.avatar.url)

        await interaction.followup.send(embed=embed, ephemeral=True)
        logger.info(f"Команда /info выполнена для {interaction.user.id}")

    except Exception as e:
        logger.error(f"Ошибка команды /info для {interaction.user.id}: {e}")
        await interaction.followup.send("Ошибка при получении информации.", ephemeral=True)

def create_command(bot_client):
    @app_commands.command(name="info", description=description)
    async def wrapper(interaction: discord.Interaction) -> None:
        await info(interaction, bot_client)
    return wrapper