import discord
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..client import BotClient

# Константы
INFO = "Информация"
BOT_DESCRIPTION = "Бот на базе gpt-4o-mini."
MODELS = "gpt-4o-mini (резерв: gpt-4o, qwen-2.5-coder-32b и другие.)"
EMBED_COLOR = discord.Color.blue()

async def info(interaction: discord.Interaction, bot_client: "BotClient") -> None:
    """Команда /info: отображает информацию о боте."""
    await interaction.response.defer(thinking=True, ephemeral=True)

    embed = discord.Embed(
        title=INFO,
        description=BOT_DESCRIPTION,
        color=EMBED_COLOR,
        timestamp=discord.utils.utcnow()
    )

    embed.set_thumbnail(url=bot_client.bot.user.avatar.url if bot_client.bot.user.avatar else discord.Embed.Empty)

    embed.add_field(name="Имя", value=bot_client.bot.user.name, inline=True)
    embed.add_field(name="ID", value=str(bot_client.bot.user.id), inline=True)
    embed.add_field(name="Модель", value=MODELS, inline=True)
    embed.add_field(name="Пинг", value=f"{round(bot_client.bot.latency * 1000)}мс", inline=True)
    embed.add_field(name="Серверы", value=str(len(bot_client.bot.guilds)), inline=True)

    await interaction.followup.send(embed=embed)