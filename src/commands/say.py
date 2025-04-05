import discord

async def say(interaction: discord.Interaction, message: str, bot_client) -> None:
    """Команда /say: Отправляет сообщение от имени бота."""
    
    if interaction.guild is None and interaction.user.id != bot_client.config.DEVELOPER_ID:
        await interaction.response.send_message("Команда доступна только на сервере или разработчику.", ephemeral=True)
        return

    if interaction.guild and not (interaction.user.id == bot_client.config.DEVELOPER_ID or (
            isinstance(interaction.user, discord.Member) and (
                interaction.user.guild_permissions.administrator or
                interaction.user.guild_permissions.manage_channels or
                interaction.user.guild_permissions.manage_messages
            )
    )):
        await interaction.response.send_message("Нет прав для выполнения команды.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        await interaction.channel.send(content=message)
        await interaction.delete_original_response()
    except discord.HTTPException as e:
        if e.code == 50035:
            await interaction.edit_original_response(content="Сообщение слишком длинное (максимум 2000 символов).")
        elif e.code == 50006:
            await interaction.edit_original_response(content="Нельзя отправить пустое сообщение.")
        else:
            await interaction.edit_original_response(content=f"Ошибка отправки: {e}")
    except Exception as e:
        await interaction.edit_original_response(content=f"Неизвестная ошибка: {e}")