import discord
from datetime import datetime
from .logging_config import logger

class BotActivity:
    @staticmethod
    async def set_shard_activity(bot: discord.AutoShardedClient, deploy_time: str) -> None:
        """Устанавливает активность бота с датой деплоя для каждого шарда."""
        await bot.wait_until_ready()
        shard_ids = bot.shard_ids or [0]
        activity = discord.Activity(type=discord.ActivityType.watching, name=f"обновление {deploy_time}")

        for shard_id in shard_ids:
            await bot.change_presence(activity=activity, shard_id=shard_id)
            logger.info(f"Активность для шарда {shard_id}: {activity.name}")

class ShardedBotClient(discord.AutoShardedClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.deploy_time = datetime.now().strftime("%d.%m")
        self.activity_set = False
        logger.debug(f"Клиент инициализирован с {self.shard_count or 1} шардами")

    async def on_ready(self):
        logger.info(f"Бот готов: {self.shard_count or 1} шардов")
        if not self.activity_set:
            await BotActivity.set_shard_activity(self, self.deploy_time)
            self.activity_set = True