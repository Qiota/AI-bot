import discord
from datetime import datetime
from .logging_config import logger

class BotActivity:
    @staticmethod
    async def set_shard_activity(bot: discord.AutoShardedClient, deploy_time: str) -> None:
        """Устанавливает активность бота с датой деплоя для каждого шарда."""
        await bot.wait_until_ready()
        activity = discord.Activity(type=discord.ActivityType.watching, name=f"обновление {deploy_time}")

        for shard_id in bot.shard_ids or [0]:
            await bot.change_presence(activity=activity, shard_id=shard_id)

class ShardedBotClient(discord.AutoShardedClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.deploy_time = datetime.now().strftime("%d.%m")
        self.activity_set = False

    async def on_connect(self):
        if not self.activity_set:
            await BotActivity.set_shard_activity(self, self.deploy_time)
            self.activity_set = True