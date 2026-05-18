"""Portrait command - Noxi selfie generation via freegen.app."""

from __future__ import annotations

import io
import asyncio
import random

import discord
from discord import app_commands

from ...systemLog import logger
from ...core.middleware import require_bot_access


DESCRIPTION = "Generate Noxi selfie (freegen.app)"

SELFIE_STYLES = ["shy", "proud", "playful", "seductive", "cute", "mysterious", "smug", "sweet"]
_selfie_style_counter: dict[str, int] = {}


def _get_next_style(user_id: str) -> str:
    if user_id not in _selfie_style_counter:
        _selfie_style_counter[user_id] = 0
    idx = _selfie_style_counter[user_id] % len(SELFIE_STYLES)
    style = SELFIE_STYLES[idx]
    _selfie_style_counter[user_id] += 1
    return style


def _is_compliment_request(text: str) -> bool:
    return any(c in text for c in ["красуня", "красунчик", "гарнюня", "милашка", "pretty", "beautiful", "gorgeous", "cute", "hot"])


def _is_cheer_up_request(text: str) -> bool:
    return any(c in text for c in ["підбадьор", "підбадьори", "здивуй", "cheer", "bored", "скучно", "support"])


def _is_posing_request(text: str) -> bool:
    return any(c in text for c in ["pose", "поза", "позуй", "зроби позу"])


async def _noxi_selfie_caption(user_message: str = "") -> str:
    """Generate adaptive selfie caption."""
    try:
        from src.core.model_manager import model_manager

        style = _get_next_style(user_message)
        is_compliment = _is_compliment_request(user_message)
        is_cheering = _is_cheer_up_request(user_message)
        is_posing = _is_posing_request(user_message)

        style_map = {
            "shy": "bashfully looks away, nervously twirls hair",
            "proud": "proudly lifts chin, looks down at you",
            "playful": "winks, sticks out tongue, raises eyebrow",
            "seductive": "slowly traces finger along lips, looks through lashes",
            "cute": "tilts head, pouts, blinks frequently",
            "mysterious": "looks into distance, slightly turns head",
            "smug": "smirks with corner of lips, one eyebrow raised",
            "sweet": "warmly smiles, presses palm to cheek",
        }

        hints = ""
        if is_compliment:
            hints = "User gave a compliment - show you appreciate it, but don't boast!"
        elif is_cheering:
            hints = "User wants to be cheered up - make selfie fun and positive!"
        elif is_posing:
            hints = "User asked to pose - show you're a model!"

        system = f"""You are Noxi. Anime girl, 30 years old. You just took a selfie and showing it.
Current mood: {style_map.get(style, style)}
{hints}

Rules:
1. Think you're beautiful and show off - but cute, not arrogant
2. Use kaomoji: (◕‿◕), (≧◡≦), (´∀`), (－ω－), (・ω・), (｡>ω<｡)
3. 1-2 sentences max
4. In Ukrainian language
5. DON'T start with "Oсь" or "Дивись" - immediately compliment or react
6. Don't say "я красуня", "могла б бути моделлю" - compliment indirectly
7. You can add light flirting or coquetry"""

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "Write a short caption for your selfie."}
        ]

        for _ in range(2):
            try:
                caption = await model_manager.chat(
                    messages=messages,
                    category="fast",
                    max_tokens=100,
                    system_prompt=system
                )
                if caption and len(caption.strip()) > 5:
                    return caption.strip()
            except Exception:
                await asyncio.sleep(1)
    except Exception:
        pass

    fallbacks = [
        "Як тобі? (◕‿◕)",
        "Непогано вийшло, еге ж? (≧◡≦)",
        "Щось сьогодні добре виглядаю... (´∀`)",
        "Може й не ідеально, але мені подобається~ (｡>ω<｡)",
        "Бачиш як я вмію? (・ω・)",
    ]
    return random.choice(fallbacks)


@require_bot_access
async def portrait(interaction: discord.Interaction, bot_client, reply_to=None) -> None:
    await interaction.response.defer(thinking=True)

    try:
        from ...utils.freegen_client import freegen_client

        img_bytes = await freegen_client.generate(timeout=180)

        if img_bytes:
            caption = await _noxi_selfie_caption()
            file = discord.File(io.BytesIO(img_bytes), filename="noxi-selfie.png")

            if reply_to is not None:
                await reply_to.reply(content=caption, file=file)
            else:
                await interaction.followup.send(caption, file=file)
            return

        if reply_to is not None:
            await reply_to.reply(content="(－ω－) Не вдалося згенерувати...")
        else:
            await interaction.followup.send(
                "(－ω－) Не вдалося згенерувати... Спробуй пізніше.",
                ephemeral=True,
            )
    except Exception as e:
        logger.error(f"[PORTRAIT] Error: {e}")
        if reply_to is not None:
            await reply_to.reply(content=f"(－ω－) Помилка: {e}")
        else:
            await interaction.followup.send(f"(－ω－) Помилка: {e}", ephemeral=True)


def create_command(bot_client):
    @app_commands.command(name="portrait", description=DESCRIPTION)
    async def wrapper(interaction: discord.Interaction) -> None:
        await portrait(interaction, bot_client)

    return wrapper