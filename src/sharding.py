import discord
from datetime import datetime
from .logging_config import logger

class BotActivity:
    @staticmethod
    async def set_shard_activity(bot: discord.AutoShardedClient, deploy_time: str) -> None:
        """Устанавливает активность бота с датой деплоя и шардом."""
        await bot.wait_until_ready()
        shard_ids = bot.shard_ids or [0]

        for shard_id in shard_ids:
            activity = discord.Activity(
                type=discord.ActivityType.watching,
                name=f"обновление {deploy_time}"
            )
            await bot.change_presence(activity=activity, shard_id=shard_id)
            logger.info(f"Активность установлена для шарда {shard_id}: {activity.name}")

class ShardedBotClient(discord.AutoShardedClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.deploy_time = datetime.now().strftime("%d.%m")
        logger.debug(f"Инициализирован клиент с {self.shard_count} шардами")
        self.activity_set = False
        self.client = None

    async def on_ready(self):
        logger.info(f"Бот готов, {self.shard_count} шардов")
        if not self.activity_set:
            await BotActivity.set_shard_activity(self, self.deploy_time)
            self.activity_set = True
        if self.client:
            await self.client.on_ready()