import discord

async def info(interaction: discord.Interaction, bot_client) -> None:
    """Команда /info."""
    await interaction.response.defer(thinking=True, ephemeral=True)
    embed = discord.Embed(
        title="DeepSeek-R1 Бот",
        description="Бот на базе gpt-4o-mini (резерв: gpt-4o, o1-mini, qwen-2.5-coder-32b, llama-3.3-70b, mistral-nemo, llama-3.1-8b, deepseek-r1, phi-4)",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=bot_client.bot.user.avatar.url if bot_client.bot.user.avatar else discord.Embed.Empty)
    embed.add_field(name="Имя", value=bot_client.bot.user.name, inline=True)
    embed.add_field(name="ID", value=str(bot_client.bot.user.id), inline=True)
    embed.add_field(name="Модель", value="gpt-4o-mini (резерв: deepseek-v3)", inline=True)
    embed.add_field(name="Пинг", value=f"{round(bot_client.bot.latency * 1000)}мс", inline=True)
    embed.add_field(name="Серверы", value=str(len(bot_client.bot.guilds)), inline=True)
    await interaction.followup.send(embed=embed)
