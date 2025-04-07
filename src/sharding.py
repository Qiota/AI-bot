import discord
from .logging_config import logger

class BotActivity:
    @staticmethod
    async def set_shard_activity(bot: discord.AutoShardedClient) -> None:
        """Устанавливает активность бота с учетом шардирования."""
        await bot.wait_until_ready()

        shard_ids = bot.shard_ids if bot.shard_ids is not None else [0]
        shard_count = bot.shard_count or 1

        if shard_count == 1 and not bot.shard_ids:
            activity = discord.Activity(
                type=discord.ActivityType.streaming,
                name="Shard 0"
            )
            await bot.change_presence(activity=activity)
            logger.info("Активность установлена для единственного шарда (без шардирования).")
        else:
            for shard_id in shard_ids:
                activity = discord.Activity(
                    type=discord.ActivityType.streaming,
                    name=f"Shard {shard_id}"
                )
                await bot.change_presence(activity=activity, shard_id=shard_id)
                logger.info(f"Активность установлена для шарда {shard_id}.")

class ShardedBotClient(discord.AutoShardedClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        logger.debug(f"Инициализирован шардированный клиент с {self.shard_count} шардами")
        self.activity_set = False
        self.client = None

    async def on_ready(self):
        logger.info(f"Бот готов, {self.shard_count} шардов")
        await BotActivity.set_shard_activity(self)
        self.activity_set = True
        logger.debug("Активность установлена для всех шардов")
        if self.client:
            await self.client.on_ready()