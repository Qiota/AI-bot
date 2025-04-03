import sys
import os
import asyncio
from threading import Thread
from src.config import BotConfig, logger

sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from src.client import BotClient
from src.server import run_flask

BOT_VERSION = "1.0.0"

async def main():
    """Основная функция запуска бота."""
    logger.info(f"Запуск бота версии {BOT_VERSION}")
    config = BotConfig()
    bot_client = BotClient(config)
    await bot_client.setup()
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    try:
        await bot_client.bot.start(config.TOKEN)
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")
        raise
    finally:
        await bot_client.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем.")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        sys.exit(1)