import logging
import os
import sys
from typing import NoReturn
from src.start import start_bot
from src.systemLog import logger

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log')
    ]
)

def main() -> NoReturn:
    """Инициализация и запуск бота."""
    logger.info(f"Запуск бота на Python {sys.version}")
    logger.info(f"Окружение: {os.environ.get('ENV', 'production')}")
    
    try:
        if asyncio.iscoroutinefunction(start_bot):
            asyncio.run(start_bot())
        else:
            start_bot()
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    import asyncio
    main()