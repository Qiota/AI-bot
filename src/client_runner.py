import asyncio
from threading import Thread
from .config import BotConfig, logger
from .client import BotClient
from .server import run_flask
from .command_registry import register_commands

async def run_bot():
    """Запускает бота и Flask-сервер."""
    config = BotConfig()
    bot_client = BotClient(config, shard_count=config.get("SHARD_COUNT", 2))
    
    try:
        config.validate()
        bot_client.bot.event(bot_client.on_message)
        bot_client.bot.event(bot_client.on_message_edit)
        register_commands(bot_client.tree, bot_client)

        Thread(target=run_flask, daemon=True).start()

        bot_task = asyncio.create_task(bot_client.bot.start(config.TOKEN))

        while not all(shard.is_ready() for shard in bot_client.bot.shards.values()):
            await asyncio.sleep(1)

        while not bot_client.bot.activity_set:
            await asyncio.sleep(1)

        logger.info("Бот успешно запущен!")

        await bot_task

    except Exception as e:
        logger.error(f"Ошибка запуска: {e}")
        raise
    finally:
        await bot_client.bot.close()
        logger.info("Бот закрыт")

def start_bot():
    """Запускает бота в асинхронном режиме."""
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Остановка бота пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        exit(1)