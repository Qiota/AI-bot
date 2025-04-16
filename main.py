from src.start import start_bot
from src.logging_config import logger

def main():
    logger.info("Инициализация бота")
    try:
        start_bot()
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        raise

if __name__ == "__main__":
    main()