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
        texts = ["🛠️-/restrict", "v1.5"]

    index = 0
    while not bot.is_closed():
        try:
            current_text = texts[index]() if callable(texts[index]) else texts[index]
            activity = discord.Streaming(
                name=current_text,
                url=stream_url
            )
            await bot.change_presence(activity=activity)
            logger.debug(f"Активность установлена: {current_text}")
            index = (index + 1) % len(texts)
            await asyncio.sleep(interval)
        except discord.HTTPException as e:
            logger.error(f"Временная ошибка API при установке активности: {e}")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Неизвестная ошибка установки активности: {e}")
            break