import discord
from discord import app_commands
from .commands import info, say, clearmemory

def register_commands(tree: app_commands.CommandTree, bot_client) -> None:
    """Регистрирует все команды бота."""
    async def info_wrapper(interaction: discord.Interaction) -> None:
        await info(interaction, bot_client=bot_client)

    async def say_wrapper(interaction: discord.Interaction, message: str) -> None:
        await say(interaction, message, bot_client=bot_client)

    async def clearmemory_wrapper(interaction: discord.Interaction) -> None:
        await clearmemory(interaction, bot_client=bot_client)

    tree.command(name="info", description="Информация о боте")(info_wrapper)
    tree.command(name="say", description="Сказать что-то от имени бота")(say_wrapper)
    tree.command(name="clearmemory", description="Очистить память бота для текущего пользователя")(clearmemory_wrapper)