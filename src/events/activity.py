import discord
import asyncio
import time
from typing import List, Optional, Union, Callable

from ..systemLog import logger


TextItem = Union[str, Callable[[], str]]


async def set_bot_activity(
    bot: discord.Client,
    texts: Optional[List[TextItem]] = None,
    stream_url: str = "https://twitch.tv/discord",
    interval: int = 60,
) -> None:
    """Устанавливает активность бота с периодической сменой текста и статусом 'стримит'."""

    await bot.wait_until_ready()

    start_time: float = getattr(bot, "start_time", 0.0) or time.time()

    def uptime_text() -> str:
        elapsed = int(time.time() - start_time)
        minutes_total = elapsed // 60
        minutes = minutes_total % 60
        hours_total = minutes_total // 60
        hours = hours_total % 24
        days = hours_total // 24

        if days > 0:
            return f"Uptime: {days}д {hours}ч"
        if hours > 0:
            return f"Uptime: {hours}ч {minutes}м"
        return f"Uptime: {minutes}м"

    if texts is None:
        # Интересное, но без подсчета guild/member.
        # Можно задавать свои texts при вызове set_bot_activity(...).
        texts = [
            "v2.0",
            uptime_text,
            "На зв'язку Noxi!",
        ]

    index = 0
    while not bot.is_closed():
        try:
            if bot.is_closed():
                break

            item = texts[index]
            current_text = item() if callable(item) else item
            current_text = str(current_text)

            activity = discord.Streaming(
                name=current_text,
                url=stream_url,
            )

            await asyncio.wait_for(
                bot.change_presence(activity=activity),
                timeout=10.0,
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
            logger.error(f"Ошибка сети при установке активности: {e}")
            if bot.is_closed():
                break
            await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"Неизвестная ошибка установки активности: {e}")
            if bot.is_closed():
                break
            await asyncio.sleep(5)

