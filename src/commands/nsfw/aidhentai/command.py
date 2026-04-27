"""Команда /aidhentai — поиск по AnimeIdHentai."""

from typing import Optional

import discord
from bs4 import BeautifulSoup
from discord import app_commands

from ....systemLog import logger
from ....core.middleware import require_bot_access, require_nsfw
from ....core.session import aiohttp_session
from .models import HttpError, ParseError
from .parser import construct_url, fetch_html, parse_search_results, parse_total_pages
from .views import NavigationView

description = "Поиск по AnimeIdHentai"


@require_nsfw
@require_bot_access
async def aidhentai(
    interaction: discord.Interaction, bot_client, query: Optional[str] = None
) -> None:
    """Команда /aidhentai: Поиск по AnimeIdHentai."""
    try:
        await interaction.response.defer(ephemeral=True)
    except discord.errors.NotFound:
        logger.error("Interaction expired early in aidhentai")
        return

    try:
        async with aiohttp_session(timeout=15) as session:
            url = construct_url(query, page=1)
            html = await fetch_html(session, url)
            soup = BeautifulSoup(html, "html.parser")
            results = await parse_search_results(session, soup)
            total_pages = parse_total_pages(soup)

            if not results:
                await interaction.followup.send("Ничего не найдено.", ephemeral=False)
                return

            view = NavigationView(
                results, interaction.user, query, current_page=1, total_pages=total_pages
            )
            embed = view.create_embed()
            message = await interaction.followup.send(embed=embed, view=view, ephemeral=False)
            view.message = message

    except HttpError as e:
        logger.error(f"HTTP ошибка для URL {url}: {e}")
        await interaction.followup.send("Сайт не отвечает.", ephemeral=False)
    except ParseError as e:
        logger.error(f"Ошибка парсинга: {e}")
        await interaction.followup.send("Ошибка обработки данных.", ephemeral=False)
    except Exception as e:
        logger.error(f"Неизвестная ошибка /aidhentai: {e}")
        await interaction.followup.send("Произошла ошибка.", ephemeral=False)


def create_command(bot_client) -> app_commands.Command:
    """Создаёт слеш-команду /aidhentai."""
    @app_commands.command(name="aidhentai", description=description)
    @app_commands.describe(query="Поисковый запрос")
    async def wrapper(interaction: discord.Interaction, query: Optional[str] = None) -> None:
        await aidhentai(interaction, bot_client, query)

    @wrapper.error
    async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        guild_id = str(interaction.guild.id) if interaction.guild else "DM"
        channel_id = str(interaction.channel.id) if interaction.channel else "DM"
        logger.error(
            f"Ошибка /aidhentai для {interaction.user.id} в гильдии {guild_id}, канал {channel_id}: {error}"
        )
        try:
            await interaction.followup.send("Ошибка при выполнении команды.", ephemeral=True)
        except (discord.NotFound, discord.InteractionResponded):
            pass
        except discord.DiscordException as e:
            logger.error(f"Не удалось отправить сообщение об ошибке: {e}")

    return wrapper

