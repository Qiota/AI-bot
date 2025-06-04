import logging
import os
import sys
import asyncio
import gc
import psutil
from typing import NoReturn
from decouple import config
from src.start import start_bot
from src.systemLog import logger

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log')
    ]
)

# Конфигурация службы очистки памяти
MEMORY_CHECK_INTERVAL = 300  # Интервал проверки памяти (секунды)
MEMORY_THRESHOLD = 0.7  # Порог использования памяти (70%)

async def memory_cleanup_service():
    """
    Асинхронная служба мониторинга и очистки памяти.
    Периодически проверяет использование памяти и выполняет сборку мусора при необходимости.
    """
    process = psutil.Process(os.getpid())
    logger.info("Запуск службы мониторинга памяти")

    while True:
        try:
            # Получаем статистику памяти
            mem_info = process.memory_info()
            total_memory = psutil.virtual_memory().total
            memory_percent = mem_info.rss / total_memory

            logger.debug(
                f"Использование памяти: {mem_info.rss / 1024**2:.2f} MB "
                f"({memory_percent*100:.1f}% от {total_memory / 1024**2:.2f} MB)"
            )

            # Проверка порога использования памяти
            if memory_percent > MEMORY_THRESHOLD:
                logger.warning(
                    f"Высокое использование памяти ({memory_percent*100:.1f}%). "
                    f"Запуск сборки мусора."
                )
                # Принудительный вызов сборщика мусора
                collected = gc.collect()
                logger.info(f"Сборка мусора завершена. Освобождено объектов: {collected}")

                # Повторная проверка памяти после очистки
                mem_info = process.memory_info()
                memory_percent = mem_info.rss / total_memory
                logger.info(
                    f"После очистки: {mem_info.rss / 1024**2:.2f} MB "
                    f"({memory_percent*100:.1f}% от {total_memory / 1024**2:.2f} MB)"
                )

        except Exception as e:
            logger.error(f"Ошибка в службе очистки памяти: {e}")
            await asyncio.sleep(MEMORY_CHECK_INTERVAL)
            continue

        # Ожидание следующей проверки
        await asyncio.sleep(MEMORY_CHECK_INTERVAL)

def main() -> NoReturn:
    """
    Инициализация и запуск бота с фоновой службой очистки памяти.
    """
    logger.info(f"Запуск бота на Python {sys.version}")
    logger.info(f"Окружение: {os.environ.get('ENV', 'production')}")

    # Загрузка токена из .env
    bot_token = config('BOT_TOKEN')

    # Создаём новый цикл событий
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        # Инициализация бота
        bot_client = start_bot()

        # Запуск службы очистки памяти
        loop.create_task(memory_cleanup_service())

        # Запуск бота
        loop.run_until_complete(bot_client.bot.start(bot_token))

    except KeyboardInterrupt:
        logger.info("Получен сигнал завершения. Остановка бота.")
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}")
        sys.exit(1)
    finally:
        # Очистка и закрытие цикла событий
        logger.info("Закрытие бота и ресурсов")
        loop.run_until_complete(bot_client.bot.close())
        logger.info("Клиент Discord закрыт")
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
        logger.info("Цикл событий закрыт")

if __name__ == "__main__":
    main()
