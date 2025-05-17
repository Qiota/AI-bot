import discord
from discord import app_commands, Embed, ButtonStyle, Interaction
from discord.ui import Button, View, Select
import aiohttp
from urllib.parse import urlencode, quote
from typing import Optional, Dict
from dataclasses import dataclass
import asyncio
from backoff import on_exception, expo
from contextlib import asynccontextmanager
import os
from ...systemLog import logger
from translatepy import Translator
from translatepy.exceptions import TranslatepyException
from translatepy.translators.google import GoogleTranslate
from functools import lru_cache
import time
import psutil

# Описание команды (не переводится, так как указано переводить synopsis)
description = "Поиск информации об аниме на Kitsu"


# Кастомные исключения
class KitsuApiError(Exception):
    """Исключение для ошибок API Kitsu."""
    pass


# Структурированные данные
@dataclass
class AnimeInfo:
    """Класс для хранения информации об аниме."""
    title: str
    synopsis: str
    poster_url: str
    episode_count: Optional[int]
    episode_length: Optional[int]
    show_type: str
    start_date: str
    age_rating: str
    average_rating: Optional[float]
    youtube_id: Optional[str]
    kitsu_url: str


@asynccontextmanager
async def aiohttp_session():
    """Контекстный менеджер для aiohttp сессии."""
    timeout = aiohttp.ClientTimeout(total=60, connect=20)
    connector = aiohttp.TCPConnector(limit=10)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        yield session


@lru_cache(maxsize=100)
async def translate_text(text: str, target_lang: str) -> str:
    """Переводит текст на указанный язык с помощью translatepy (только Google Translate).

    Args:
        text: Текст для перевода.
        target_lang: Целевой язык (например, 'ru', 'uk', 'pl').

    Returns:
        Переведенный текст или оригинальный при ошибке.
    """
    if not text or text == "Описание отсутствует":
        logger.info("Пустой текст или отсутствует описание, перевод не требуется")
        return text

    # Ограничение длины текста
    max_length = 2000
    if len(text) > max_length:
        logger.warning(f"Текст обрезан до {max_length} символов (оригинальная длина: {len(text)})")
        text = text[:max_length]

    # Логирование потребления памяти
    process = psutil.Process()
    mem_before = process.memory_info().rss / 1024 / 1024  # МБ
    logger.info(f"Память перед переводом: {mem_before:.2f} МБ")

    translator = Translator(services_list=[GoogleTranslate()])
    try:
        result = translator.translate(text, destination_language=target_lang)
        translated = result.result
        mem_after = process.memory_info().rss / 1024 / 1024  # МБ
        logger.info(f"Перевод успешен: {text[:50]}... -> {translated[:50]}... (сервис: {result.service})")
        logger.info(f"Память после перевода: {mem_after:.2f} МБ")
        return translated
    except TranslatepyException as e:
        logger.error(f"Ошибка translatepy: {str(e)}")
        return text
    except Exception as e:
        logger.error(f"Неизвестная ошибка при переводе: {str(e)}", exc_info=True)
        return text


def create_anime_embed(anime: AnimeInfo) -> Embed:
    """Создает Embed для отображения информации об аниме.

    Args:
        anime: Объект AnimeInfo с данными аниме.

    Returns:
        Объект Embed с информацией об аниме.
    """
    embed = Embed(
        title=anime.title,
        description=anime.synopsis[:4096],
        color=0x00FF99,
        timestamp=discord.utils.utcnow()
    )
    embed.set_image(url=anime.poster_url)
    embed.add_field(
        name="Количество серий",
        value=f"{anime.episode_count} серий" if anime.episode_count else "Неизвестно",
        inline=True
    )
    embed.add_field(
        name="Длительность серии",
        value=f"~{anime.episode_length} минут" if anime.episode_length else "Неизвестно",
        inline=True
    )
    embed.add_field(
        name="Тип проекта",
        value=anime.show_type or "Неизвестно",
        inline=True
    )
    embed.add_field(
        name="Дата релиза",
        value=anime.start_date or "Неизвестно",
        inline=True
    )
    embed.add_field(
        name="Возрастной рейтинг",
        value=anime.age_rating or "Неизвестно",
        inline=True
    )
    embed.add_field(
        name="Оценка пользователей",
        value=f"{anime.average_rating}/100" if anime.average_rating else "Неизвестно",
        inline=True
    )
    embed.set_footer(text="Данные предоставлены Kitsu API")
    return embed


class LanguageSelect(Select):
    """Селект-меню для выбора языка перевода."""
    def __init__(self, anime: AnimeInfo):
        super().__init__(
            placeholder="Выберите язык перевода",
            options=[
                discord.SelectOption(label="Русский", value="ru", emoji="🇷🇺"),
                discord.SelectOption(label="Українська", value="uk", emoji="🇺🇦"),
                discord.SelectOption(label="Polski", value="pl", emoji="🇵🇱"),
            ],
        )
        self.anime = anime

    async def callback(self, interaction: Interaction):
        """Обрабатывает выбор языка и переводит синопсис аниме."""
        start_time = time.time()
        logger.info(f"Начало обработки LanguageSelect.callback для языка {self.values[0]}")
        selected_lang = self.values[0]
        try:
            translated_synopsis = await translate_text(self.anime.synopsis, selected_lang)
            self.anime.synopsis = translated_synopsis
            embed = create_anime_embed(self.anime)
            try:
                await interaction.response.edit_message(embed=embed)
            except discord.errors.NotFound:
                logger.warning("Взаимодействие устарело, отправка через followup")
                await interaction.followup.send(embed=embed, ephemeral=False)
            logger.info(f"Обработка LanguageSelect.callback завершена за {time.time() - start_time:.2f} сек")
        except Exception as e:
            logger.error(f"Ошибка в LanguageSelect.callback: {str(e)}", exc_info=True)
            try:
                await interaction.response.send_message(
                    "Ошибка при переводе текста. Попробуйте снова.",
                    ephemeral=True
                )
            except discord.errors.NotFound:
                logger.warning("Взаимодействие устарело для отправки ошибки, использование followup")
                await interaction.followup.send(
                    "Ошибка при переводе текста. Попробуйте снова.",
                    ephemeral=True
                )
            logger.info(f"Обработка LanguageSelect.callback завершена с ошибкой за {time.time() - start_time:.2f} сек")


class KitsuView(View):
    """Кастомный View для отображения кнопок ссылок и селект-меню."""
    def __init__(self, kitsu_url: str, youtube_id: Optional[str], anime: AnimeInfo):
        super().__init__(timeout=300)
        self.anime = anime
        self.add_item(Button(
            label="Ссылка на Kitsu",
            style=ButtonStyle.link,
            url=kitsu_url
        ))
        if youtube_id:
            self.add_item(Button(
                label="Ссылка на трейлер",
                style=ButtonStyle.link,
                url=f"https://www.youtube.com/watch?v={youtube_id}"
            ))
        self.add_item(LanguageSelect(anime))


@on_exception(
    expo,
    (aiohttp.ClientError, asyncio.TimeoutError),
    max_tries=3,
    max_time=10,
    jitter="full"
)
async def fetch_kitsu_token(session: aiohttp.ClientSession) -> str:
    """Получает токен авторизации Kitsu API.

    Args:
        session: Сессия aiohttp.

    Returns:
        Токен авторизации.

    Raises:
        KitsuApiError: Если авторизация не удалась.
    """
    auth_url = "https://kitsu.io/api/oauth/token"
    payload = {
        "grant_type": "password",
        "username": os.getenv("KITSU_USERNAME"),
        "password": os.getenv("KITSU_PASSWORD")
    }
    async with session.post(auth_url, data=urlencode(payload),
                           headers={"Content-Type": "application/x-www-form-urlencoded"}) as response:
        if response.status != 200:
            raise KitsuApiError(f"Не удалось авторизоваться: HTTP {response.status}")
        data = await response.json()
        token = data.get("access_token")
        if not token:
            raise KitsuApiError("Токен авторизации не получен")
        return token


@on_exception(
    expo,
    (aiohttp.ClientError, asyncio.TimeoutError),
    max_tries=3,
    max_time=10,
    jitter="full"
)
async def search_anime(session: aiohttp.ClientSession, token: str, query: str) -> Dict:
    """Ищет аниме по запросу через Kitsu API.

    Args:
        session: Сессия aiohttp.
        token: Токен авторизации.
        query: Поисковый запрос.

    Returns:
        Данные аниме в формате JSON.

    Raises:
        KitsuApiError: Если запрос не удался.
    """
    search_url = f"https://kitsu.io/api/edge/anime?filter[text]={quote(query)}"
    headers = {"Authorization": f"Bearer {token}"}
    async with session.get(search_url, headers=headers) as response:
        if response.status != 200:
            raise KitsuApiError(f"Ошибка поиска аниме: HTTP {response.status}")
        return await response.json()


@on_exception(
    expo,
    (aiohttp.ClientError, asyncio.TimeoutError, KitsuApiError),
    max_tries=3,
    max_time=15,
    jitter="full"
)
async def kitsu(interaction: Interaction, bot_client, query: str, target_lang: str = "en") -> None:
    """Команда /kitsu: Поиск информации об аниме на Kitsu.

    Args:
        interaction: Взаимодействие с Discord.
        bot_client: Клиент бота.
        query: Название аниме для поиска.
        target_lang: Целевой язык для перевода synopsis (по умолчанию 'en' для английского).
    """
    # Проверка NSFW-канала
    if interaction.guild and not interaction.channel.nsfw:
        await interaction.response.send_message(
            "Эта команда доступна только в NSFW-каналах или ЛС.",
            ephemeral=True
        )
        return

    try:
        await interaction.response.defer(ephemeral=False)
    except discord.errors.NotFound:
        await interaction.followup.send("Взаимодействие устарело. Попробуйте снова.", ephemeral=True)
        return

    try:
        async with aiohttp_session() as session:
            # Получение токена авторизации
            token = await fetch_kitsu_token(session)

            # Поиск аниме
            anime_data = await search_anime(session, token, query)

            if not anime_data.get("data") or len(anime_data["data"]) == 0:
                await interaction.followup.send(
                    "Аниме не найдено. Попробуйте уточнить название.",
                    ephemeral=False
                )
                return

            # Обработка первого результата
            data = anime_data["data"][0]
            attributes = data["attributes"]

            # Формирование объекта аниме
            synopsis = attributes.get("synopsis", "Описание отсутствует") or "Описание отсутствует"
            # Перевод synopsis только если target_lang != "en"
            translated_synopsis = synopsis if target_lang == "en" else await translate_text(synopsis, target_lang)
            if target_lang == "en":
                logger.info("Используется оригинальный synopsis (английский)")
            anime = AnimeInfo(
                title=attributes.get("titles", {}).get("en") or
                      attributes.get("titles", {}).get("en_jp") or
                      attributes.get("canonicalTitle", "Без названия"),
                synopsis=translated_synopsis,
                poster_url=attributes.get("posterImage", {}).get("original", "https://via.placeholder.com/300"),
                episode_count=attributes.get("episodeCount"),
                episode_length=attributes.get("episodeLength"),
                show_type=attributes.get("showType", "Неизвестно"),
                start_date=attributes.get("startDate", "Неизвестно"),
                age_rating=attributes.get("ageRatingGuide", "Неизвестно"),
                average_rating=attributes.get("averageRating"),
                youtube_id=attributes.get("youtubeVideoId"),
                kitsu_url=f"https://kitsu.io/anime/{data['id']}"
            )

            # Создание Embed и View
            embed = create_anime_embed(anime)
            view = KitsuView(anime.kitsu_url, anime.youtube_id, anime)

            # Отправка ответа
            await interaction.followup.send(embed=embed, view=view, ephemeral=False)

    except KitsuApiError as e:
        logger.error(f"Ошибка Kitsu API: {str(e)}")
        await interaction.followup.send("Ошибка при запросе к Kitsu API. Попробуйте позже.", ephemeral=False)
    except discord.errors.NotFound:
        logger.error("Взаимодействие устарело в команде kitsu")
        await interaction.followup.send("Взаимодействие устарело. Попробуйте снова.", ephemeral=True)
    except Exception as e:
        logger.error(f"Неизвестная ошибка в команде kitsu: {str(e)}", exc_info=True)
        await interaction.followup.send("Произошла неизвестная ошибка. Обратитесь к администратору.", ephemeral=False)


async def create_command(bot_client, target_lang: str = "en") -> app_commands.Command:
    """Создает слеш-команду /kitsu.

    Args:
        bot_client: Клиент бота.
        target_lang: Целевой язык для перевода synopsis (по умолчанию 'en').

    Returns:
        Объект команды Discord.
    """
    @app_commands.command(name="kitsu", description=description)
    @app_commands.describe(query="Название аниме для поиска")
    async def wrapper(interaction: Interaction, query: str) -> None:
        await kitsu(interaction, bot_client, query, target_lang)

    @wrapper.error
    async def command_error(interaction: Interaction, error: app_commands.AppCommandError):
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("Ошибка при выполнении команды. Попробуйте снова.", ephemeral=True)
            else:
                await interaction.followup.send("Ошибка при выполнении команды. Попробуйте снова.", ephemeral=True)
        except discord.DiscordException:
            pass

    return wrapper