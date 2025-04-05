import discord
from .logging_config import logger
from .activity import BotActivity

class ShardedBotClient(discord.AutoShardedClient):
    """Шардированный клиент бота Discord."""
    def __init__(self, shard_count: int, *args, **kwargs):
        super().__init__(shard_count=shard_count, *args, **kwargs)
        logger.debug(f"Инициализирован шардированный клиент с {shard_count} шардами")
        self.activity_set = False

    async def on_ready(self):
        """Событие готовности бота."""
        logger.info(f"Бот готов, {self.shard_count} шардов")
        await BotActivity.set_shard_activity(self)
        self.activity_set = True
        logger.debug("Активность установлена для всех шардов")