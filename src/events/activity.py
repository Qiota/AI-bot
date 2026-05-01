import discord
import asyncio
from typing import List, Optional
from ..systemLog import logger

async def set_bot_activity(
    bot: discord.Client,
    texts: Optional[List[str]] = None,
    stream_url: str = "https://twitch.tv/discord",
    interval: int = 60
) -> None:
    """Устанавливает активность бота с периодической сменой текста и статусом 'стримит'."""
    await bot.wait_until_ready()

    if texts is None:
        texts = ["v1.7"]

    index = 0
    while not bot.is_closed():
        try:
            # Проверка состояния бота перед изменением активности
            if bot.is_closed():
                break
                
            current_text = texts[index]() if callable(texts[index]) else texts[index]
            activity = discord.Streaming(
                name=current_text,
                url=stream_url
            )
            
            # Используем asyncio.wait_for с таймаутом для предотвращения зависания
            await asyncio.wait_for(
                bot.change_presence(activity=activity),
                timeout=10.0
            )
            logger.debug(f"Активность установлена: {current_text}")
            index = (index + 1) % len(texts)
            await asyncio.sleep(interval)
        except asyncio.TimeoutError:
            logger.warning("Таймаут при установке активности, пропускаем этот цикл")
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            logger.info("Задача установки активности отменена (завершение бота)")
            break
        except discord.HTTPException as e:
            logger.error(f"Временная ошибка API при установке активности: {e}")
            await asyncio.sleep(5)
        except OSError as e:
            # Обработка ошибок сети/транспорта
            logger.error(f"Ошибка сети при установке активности: {e}")
            if bot.is_closed():
                break
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Неизвестная ошибка установки активности: {e}")
            # Проверяем, закрыт ли бот перед выходом
            if bot.is_closed():
                break
            # Для других ошибок пробуем продолжить после паузы
            await asyncio.sleep(5)
