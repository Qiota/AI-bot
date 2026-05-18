import os
import sys
import asyncio
import gc
import psutil
import logging
from typing import NoReturn
from src.start import start_bot
from src.systemLog import logger

# Suppress noisy third-party logs completely
logging.getLogger("zendriver").setLevel(logging.CRITICAL)
logging.getLogger("uc.connection").setLevel(logging.CRITICAL)
logging.getLogger("undetected_chromedriver").setLevel(logging.CRITICAL)
logging.getLogger("g4f.Provider").setLevel(logging.CRITICAL)
# Disable all asyncio task exception warnings
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

MEMORY_CHECK_INTERVAL = 300
MEMORY_THRESHOLD = 0.7

async def memory_cleanup_service():
    process = psutil.Process(os.getpid())
    logger.info("Запуск службы мониторинга памяти")

    while True:
        try:
            mem_info = process.memory_info()
            total_memory = psutil.virtual_memory().total
            memory_percent = mem_info.rss / total_memory

            logger.debug(
                f"Использование памяти: {mem_info.rss / 1024**2:.2f} MB "
                f"({memory_percent*100:.1f}% от {total_memory / 1024**2:.2f} MB)"
            )

            if memory_percent > MEMORY_THRESHOLD:
                logger.warning(
                    f"Высокое использование памяти ({memory_percent*100:.1f}%). "
                    f"Запуск сборки мусора."
                )
                collected = gc.collect()
                logger.info(f"Сборка мусора завершена. Освобождено объектов: {collected}")

                mem_info = process.memory_info()
                memory_percent = mem_info.rss / total_memory
                logger.info(
                    f"После очистки: {mem_info.rss / 1024**2:.2f} MB "
                    f"({memory_percent*100:.1f}% от {total_memory / 1024**2:.2f} MB)"
                )

        except Exception as e:
            logger.error(f"Ошибка в службе очистки памяти: {e}", exc_info=True)

        await asyncio.sleep(MEMORY_CHECK_INTERVAL)

def main() -> None:
    logger.info(f"Запуск бота на Python {sys.version}")
    logger.info(f"Окружение: {os.environ.get('ENV', 'production')}")

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("Получен сигнал завершения. Остановка бота.")
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
        sys.exit(1)

async def _run():
    cleanup_task = asyncio.create_task(memory_cleanup_service())
    
    from src.core.memory.firebase_memory import get_memory, MemoryConsolidationScheduler
    from src.core.model_manager import ModelManager
    
    memory = get_memory()
    model_manager = ModelManager()
    consolidation_scheduler = MemoryConsolidationScheduler(memory, model_manager)
    
    consolidation_task = asyncio.create_task(consolidation_scheduler.start())
    
    try:
        await start_bot()
    finally:
        cleanup_task.cancel()
        consolidation_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        try:
            await consolidation_task
        except asyncio.CancelledError:
            pass
        await consolidation_scheduler.stop()
        logger.info("Закрытие цикла событий")

if __name__ == "__main__":
    main()
