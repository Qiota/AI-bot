import discord
from discord import app_commands

@app_commands.describe(message="Сообщение, которое бот отправит")
async def say(interaction: discord.Interaction, message: str, bot_client) -> None:
    """Команда /say."""
    if not (interaction.user.id == bot_client.config.DEVELOPER_ID or (
            isinstance(interaction.user, discord.Member) and (
                interaction.user.guild_permissions.administrator or
                any(role.permissions.manage_channels or role.permissions.manage_messages for role in interaction.user.roles)
            )
    )):
        await interaction.response.send_message("У вас нет прав для выполнения этой команды.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    await interaction.channel.send(message)
    await interaction.delete_original_response()