import discord
from .logging_config import logger

class BotActivity:
    """Управление активностью бота с учетом шардирования."""

    @staticmethod
    async def set_shard_activity(bot: discord.AutoShardedClient) -> None:
        """Устанавливает активность бота с учетом шардирования."""
        shard_count = bot.shard_count or 1

        if shard_count == 0:
            activity = discord.Activity(
                type=discord.ActivityType.streaming,
                name="Shard 0"
            )
            await bot.change_presence(activity=activity)
        else:
            for shard_id in bot.shards:
                activity = discord.Activity(
                    type=discord.ActivityType.streaming,
                    name=f"Shard {shard_id}"
                )
                await bot.change_presence(activity=activity, shard_id=shard_id)
                logger.info(f"Активность установлена для шарда {shard_id}.")
