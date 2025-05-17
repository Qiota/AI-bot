import time
import psutil
import tracemalloc
import asyncio
import os
from functools import wraps
from src.systemLog import logger

# Попытка импорта start_bot с обработкой ошибки Pydantic
try:
    from src.start import start_bot
except ImportError as e:
    logger.error(f"Ошибка импорта start_bot: {e}", exc_info=True)
    raise ImportError("Не удалось импортировать start_bot. Проверьте зависимости g4f и pydantic.")

def measure_time(func):
    """Декоратор для измерения времени выполнения функции."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        result = await func(*args, **kwargs)
        end_time = time.time()
        logger.info(f"Функция {func.__name__} выполнена за {end_time - start_time:.2f} секунд")
        return result
    return wrapper

async def monitor_resources(interval=5.0):
    """Асинхронный мониторинг потребления памяти и CPU."""
    process = psutil.Process()
    while True:
        try:
            mem_info = process.memory_info()
            rss = mem_info.rss / 1024 / 1024  # Resident Set Size в MB
            vms = mem_info.vms / 1024 / 1024  # Virtual Memory Size в MB
            cpu_percent = process.cpu_percent(interval=0.1)
            logger.info(
                f"Потребление ресурсов: "
                f"RSS={rss:.2f} MB, VMS={vms:.2f} MB, CPU={cpu_percent:.1f}%"
            )
        except Exception as e:
            logger.error(f"Ошибка при мониторинге ресурсов: {e}")
        await asyncio.sleep(interval)

async def trace_memory_leaks(interval=10.0, top_n=5, snapshot_dir="memory_snapshots"):
    """Асинхронная трассировка памяти для выявления утечек."""
    tracemalloc.start()
    snapshot1 = None
    os.makedirs(snapshot_dir, exist_ok=True)
    
    while True:
        try:
            snapshot2 = tracemalloc.take_snapshot()
            snapshot2 = snapshot2.filter_traces((
                tracemalloc.Filter(True, "**/src/**"),
                tracemalloc.Filter(False, "**/lib/**"),
                tracemalloc.Filter(False, "**/site-packages/**"),
            ))
            
            if snapshot1 is not None:
                stats = snapshot2.compare_to(snapshot1, 'lineno')
                logger.info("Топ-5 изменений в потреблении памяти:")
                for i, stat in enumerate(stats[:top_n], 1):
                    logger.info(
                        f"#{i}: {stat.traceback.format()[-1]}: "
                        f"{stat.size_diff / 1024:.1f} KiB ({stat.count_diff} объектов)"
                    )
            
            current, peak = tracemalloc.get_traced_memory()
            logger.info(
                f"Трассировка памяти: текущая={current / 1024 / 1024:.2f} MB, "
                f"пиковая={peak / 1024 / 1024:.2f} MB"
            )
            
            snapshot_path = os.path.join(snapshot_dir, f"snapshot_{int(time.time())}.snap")
            snapshot2.dump(snapshot_path)
            logger.info(f"Снимок памяти сохранён: {snapshot_path}")
            
            snapshot1 = snapshot2
        except Exception as e:
            logger.error(f"Ошибка при трассировке памяти: {e}")
        await asyncio.sleep(interval)

@measure_time
async def main():
    """Основная асинхронная функция запуска бота с мониторингом и трассировкой."""
    logger.info("Инициализация бота")
    
    asyncio.create_task(monitor_resources(interval=5.0))
    asyncio.create_task(trace_memory_leaks(interval=10.0, top_n=5))
    
    try:
        await start_bot()
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
        raise
    finally:
        tracemalloc.stop()

if __name__ == "__main__":
    asyncio.run(main())