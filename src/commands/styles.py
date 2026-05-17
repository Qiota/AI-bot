"""Уніфікований стиль ембедів для бота з Discord емодзі та кольорами."""

import discord
from typing import Optional

# Discord theme colors
COLORS = {
    "default": discord.Color.blurple(),
    "success": discord.Color.green(),
    "error": discord.Color.red(),
    "warning": discord.Color.yellow(),
    "info": discord.Color.blue(),
    "nsfw": discord.Color.dark_red(),
}

# Discord-style emojis
EMOJI = {
    # Status
    "loading": "⏳",
    "success": "✅",
    "error": "❌",
    "warning": "⚠️",
    "info": "ℹ️",
    
    # Actions
    "search": "🔍",
    "add": "➕",
    "remove": "➖",
    "edit": "✏️",
    "delete": "🗑️",
    "save": "💾",
    "cancel": "🚫",
    "back": "⬅️",
    "next": "➡️",
    "confirm": "✔️",
    
    # Categories
    "nsfw": "🔞",
    "sfw": "🎀",
    "image": "🖼️",
    "settings": "⚙️",
    "help": "❓",
    "stats": "📊",
    
    # Navigation
    "first": "⏮️",
    "prev": "◀️",
    "stop": "⏹️",
    "play": "▶️",
    "pause": "⏸️",
    "last": "⏭️",
    
    # Misc
    "lock": "🔒",
    "unlock": "🔓",
    "eye": "👁️",
    "person": "👤",
    "people": "👥",
    "channel": "#️⃣",
    "link": "🔗",
    "book": "📖",
    "fire": "🔥",
    "star": "⭐",
    "heart": "❤️",
    "thinking": "🤔",
}


def create_embed(
    title: str,
    description: str = "",
    color: str = "default",
    footer: Optional[str] = None,
    thumbnail: Optional[str] = None,
    fields: Optional[list] = None,
    emoji: Optional[str] = None,
) -> discord.Embed:
    """Створити ембед з уніфікованим стилем."""
    embed = discord.Embed(
        title=f"{EMOJI.get(emoji, '') and f'{EMOJI[emoji]} ' or ''}{title}" if emoji else title,
        description=description,
        color=COLORS.get(color, COLORS["default"])
    )
    
    if footer:
        embed.set_footer(text=footer)
    
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    
    if fields:
        for field in fields:
            embed.add_field(**field)
    
    return embed


def create_error_embed(description: str, title: str = "Помилка") -> discord.Embed:
    """Створити ембед помилки."""
    return create_embed(
        title=title,
        description=description,
        color="error",
        emoji="error"
    )


def create_success_embed(description: str, title: str = "Успіх") -> discord.Embed:
    """Створити ембед успіху."""
    return create_embed(
        title=title,
        description=description,
        color="success",
        emoji="success"
    )


def create_info_embed(description: str, title: str = "Інформація") -> discord.Embed:
    """Створити інфо ембед."""
    return create_embed(
        title=title,
        description=description,
        color="info",
        emoji="info"
    )


def create_nsfw_embed(title: str, description: str = "") -> discord.Embed:
    """Створити NSFW ембед."""
    return create_embed(
        title=title,
        description=description,
        color="nsfw",
        emoji="nsfw"
    )


def create_pagination_embed(
    title: str,
    current: int,
    total: int,
    description: str = "",
    color: str = "default",
) -> discord.Embed:
    """Створити ембед з пагінацією."""
    page_info = f"Сторінка {current + 1}/{total}" if total > 1 else ""
    full_desc = f"{description}\n\n{page_info}" if description else page_info
    
    return create_embed(
        title=title,
        description=full_desc,
        color=color,
        emoji="info"
    )


# Button styles using Discord style
from discord import ButtonStyle

BUTTON_STYLE = {
    "primary": ButtonStyle.primary,
    "secondary": ButtonStyle.secondary,
    "success": ButtonStyle.success,
    "danger": ButtonStyle.danger,
    "blurple": ButtonStyle.blurple,
    "gray": ButtonStyle.grey,
    "green": ButtonStyle.green,
    "red": ButtonStyle.red,
}


def create_command(bot_client):
    """Styles module - not a command, just utility functions."""
    return None