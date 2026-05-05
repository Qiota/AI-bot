"""Команда /danbooru — поиск постов на Danbooru."""

import asyncio
import math
from typing import Optional

import discord
from discord import app_commands

from ....systemLog import logger
from ....core.middleware import require_bot_access, require_nsfw
from ....core.constants import (
    COOLDOWN_RATE,
    COOLDOWN_TIME,
    MAX_FILE_SIZE_DEFAULT,
    MAX_FILE_SIZE_TIER_2,
    MAX_FILE_SIZE_TIER_3,
    POSTS_PER_PAGE,
)
from .api import (
    aiohttp_session,
    fetch_danbooru_posts,
    fetch_post_count,
    fetch_tag_suggestions,
    filter_duplicates,
    used_post_ids,
    autocomplete_cache,
    tag_suggestions_cache,
)
from .models import DanbooruAPIError
from .views import NavigationView

DESCRIPTION = "Поиск постов на Danbooru по тегам"
MAX_QUERY_LENGTH = 100


async def tags_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Автодополнение тегов Danbooru."""
    try:
        if len(current) > MAX_QUERY_LENGTH or interaction.response.is_done():
            return []

        tags = current.strip().split()
        query = tags[-1] if tags else ""
        cache_key = query.lower() or "__all_tags__"

        async with asyncio.Lock():
            if cache_key in autocomplete_cache:
                suggestions = autocomplete_cache[cache_key]
            elif cache_key in tag_suggestions_cache:
                suggestions = tag_suggestions_cache[cache_key]
            else:
                async with aiohttp_session() as session:
                    suggestions = await fetch_tag_suggestions(session, query)
                autocomplete_cache[cache_key] = suggestions

        prefix = " ".join(tags[:-1]) + " " if tags[:-1] else ""
        choices = [
            app_commands.Choice(name=formatted_count, value=f"{prefix}{tag}".strip())
            for tag, _, formatted_count in suggestions[:25]
        ]

        try:
            await interaction.response.autocomplete(choices)
        except (discord.errors.InteractionResponded, discord.errors.NotFound):
            pass
        return choices
    except Exception as e:
        logger.error(f"Ошибка автодополнения тегов для '{current}': {e}")
        return []


async def disable_previous_view(user_id: int) -> None:
    """Отключает предыдущее активное представление пользователя."""
    from .views import active_views

    view = active_views.get(user_id)
    if not view:
        return
    view.disable_navigation_buttons()
    if view.message:
        try:
            await view.message.edit(view=None)
        except discord.HTTPException:
            pass
    active_views.pop(user_id, None)


@require_nsfw
@require_bot_access
async def danbooru(
    interaction: discord.Interaction, bot_client, tags: Optional[str] = None
) -> None:
    """Слеш-команда для поиска постов на Danbooru."""
    guild_id = str(interaction.guild.id) if interaction.guild else "DM"
    channel_id = str(interaction.channel.id) if interaction.channel else "DM"
    logger.debug(
        f"/danbooru by {interaction.user.id} in {guild_id}/{channel_id}, tags={tags}"
    )

    if tags and any(t.strip() == "" for t in tags.split()):
        await interaction.response.send_message("Теги содержат пустые значения.", ephemeral=True)
        return

    max_file_size = MAX_FILE_SIZE_DEFAULT
    if interaction.guild:
        if interaction.guild.premium_tier == 2:
            max_file_size = MAX_FILE_SIZE_TIER_2
        elif interaction.guild.premium_tier == 3:
            max_file_size = MAX_FILE_SIZE_TIER_3

    await disable_previous_view(interaction.user.id)

    try:
        await interaction.response.defer(ephemeral=False)
    except discord.errors.NotFound:
        logger.error("Interaction expired during defer")
        return

    try:
        async with aiohttp_session() as session:
            total_posts_task = fetch_post_count(session, tags)
            posts_task = fetch_danbooru_posts(session, tags, page=1)
            total_posts_result, posts_result = await asyncio.gather(
                total_posts_task, posts_task
            )

            total_pages = min(1000, math.ceil(total_posts_result / POSTS_PER_PAGE)) if total_posts_result > 0 else 1
            if not posts_result:
                await interaction.followup.send(
                    f"Посты по тегам '{tags or 'без тегов'}' не найдены.", ephemeral=False
                )
                return

            posts_result = filter_duplicates(posts_result)
            for post in posts_result:
                used_post_ids.add(post.id)

            from typing import cast
            view = NavigationView(
                posts_result, cast(discord.User, interaction.user), tags, 1, total_pages, max_file_size
            )
            view.page_cache[1] = posts_result
            content, image_urls, skipped_posts, chunk_posts = await view.create_message()
            files = await view.fetch_images(
                image_urls, interaction, skipped_posts, chunk_posts
            )
            message = await interaction.followup.send(
                content=content, view=view, files=files, ephemeral=False
            )
            view.message = message

    except DanbooruAPIError as e:
        logger.error(f"Danbooru API error for tags '{tags}': {e}")
        await interaction.followup.send(f"Не удалось получить посты: {e}", ephemeral=False)
    except Exception as e:
        logger.error(f"Unknown error in /danbooru for tags '{tags}': {e}")
        await interaction.followup.send("Произошла ошибка команды.", ephemeral=False)


def create_command(bot_client) -> app_commands.Command:
    """Создаёт слеш-команду /danbooru с автодополнением тегов."""
    @app_commands.command(name="danbooru", description=DESCRIPTION)
    @app_commands.describe(tags="Теги для поиска (например, 'blue_archive')")
    @app_commands.autocomplete(tags=tags_autocomplete)
    @app_commands.checks.cooldown(rate=COOLDOWN_RATE, per=COOLDOWN_TIME)
    async def wrapper(interaction: discord.Interaction, tags: Optional[str] = None) -> None:
        await danbooru(interaction, bot_client, tags)

    @wrapper.error
    async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        guild_id = str(interaction.guild.id) if interaction.guild else "DM"
        channel_id = str(interaction.channel.id) if interaction.channel else "DM"
        tags = getattr(interaction.namespace, "tags", None)

        if isinstance(error, app_commands.CommandOnCooldown):
            retry_after = int(error.retry_after)
            logger.debug(
                f"Cooldown /danbooru for {interaction.user.id} ({guild_id}/{channel_id}, tags={tags}, retry={retry_after}s)"
            )
            await interaction.response.send_message(
                f"Команда на кулдауне. Попробуйте снова через {retry_after} секунд.", ephemeral=True
            )
            return

        logger.error(
            f"Error in /danbooru for {interaction.user.id} ({guild_id}/{channel_id}, tags={tags}): {error}"
        )
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("Ошибка команды.", ephemeral=True)
            else:
                await interaction.followup.send("Ошибка команды.", ephemeral=True)
        except discord.DiscordException as e:
            logger.error(f"Failed to send error message: {e}")

    return wrapper

