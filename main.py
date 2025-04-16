from src.start import start_bot
from src.systemLog import logger

def main():
    logger.info("Инициализация бота")
    try:
        start_bot()
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    main()