import discord
from datetime import datetime

class ShardedBotClient(discord.AutoShardedClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.deploy_time = datetime.now().strftime("%d.%m")
        self.activity_set = False

    async def on_ready(self):
        if self.activity_set:
            return
        try:
            activity = discord.Activity(type=discord.ActivityType.watching, name=f"обновление {self.deploy_time}")
            for shard_id in self.shard_ids or [0]:
                await self.change_presence(activity=activity, shard_id=shard_id)
            self.activity_set = True
        except Exception as e:
            print(f"Ошибка установки активности: {e}")