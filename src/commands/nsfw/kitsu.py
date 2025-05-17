import discord
from discord import app_commands, Embed, ButtonStyle, Interaction
from discord.ui import Button, View, Select
import aiohttp
from urllib.parse import urlencode, quote
from typing import Optional, Dict
from dataclasses import dataclass
import asyncio
import backoff
from contextlib import asynccontextmanager
import os
import traceback

# Описание команды
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
    timeout = aiohttp.ClientTimeout(total=30, connect=15)
    connector = aiohttp.TCPConnector(limit=10)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        yield session

async def translate_text(text: str, target_lang: str) -> str:
    """Переводит текст на указанный язык с помощью LibreTranslate."""
    url = "https://libretranslate.com/translate"
    payload = {
        "q": text,
        "source": "auto",
        "target": target_lang,
    }
    headers = {"Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as response:
            if response.status != 200:
                return text  # Возвращаем оригинальный текст при ошибке
            data = await response.json()
            return data.get("translatedText", text)

class LanguageSelect(Select):
    """Селект-меню для выбора языка перевода."""
    def __init__(self, anime: AnimeInfo, view: 'KitsuView'):
        super().__init__(
            placeholder="Выберите язык перевода",
            options=[
                discord.SelectOption(label="Русский", value="ru"),
                discord.SelectOption(label="Українська", value="uk"),
                discord.SelectOption(label="Polski", value="pl"),
            ],
        )
        self.anime = anime
        self.kitsu_view = view

    async def callback(self, interaction: Interaction):
        if interaction.user != self.kitsu_view.original_user:
            await interaction.response.send_message(
                "Только пользователь, вызвавший команду, может использовать это меню.",
                ephemeral=True
            )
            return

        selected_lang = self.values[0]
        try:
            translated_synopsis = await translate_text(self.anime.synopsis, selected_lang)
            self.anime.synopsis = translated_synopsis
            embed = create_anime_embed(self.anime)
            await interaction.response.edit_message(embed=embed)
        except Exception as e:
            await interaction.response.send_message(
                "Ошибка при переводе текста. Попробуйте позже.",
                ephemeral=True
            )

class KitsuView(View):
    """Кастомный View для отображения кнопок ссылок и селект-меню."""
    def __init__(self, kitsu_url: str, youtube_id: Optional[str], original_user: discord.User, anime: AnimeInfo):
        super().__init__(timeout=300)
        self.original_user = original_user
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
        self.add_item(LanguageSelect(anime, self))

    async def interaction_check(self, interaction: Interaction) -> bool:
        """Проверяет, что взаимодействие выполняется оригинальным пользователем."""
        if interaction.user != self.original_user:
            await interaction.response.send_message(
                "Только пользователь, вызвавший команду, может использовать кнопки.",
                ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        """Обрабатывает таймаут view."""
        self.clear_items()
        try:
            if hasattr(self, "message") and self.message:
                await self.message.edit(view=self)
        except discord.DiscordException:
            pass

@backoff.on_exception(
    backoff.expo,
    (aiohttp.ClientError, asyncio.TimeoutError),
    max_tries=3,
    max_time=10,
    jitter=backoff.full_jitter
)
async def fetch_kitsu_token(session: aiohttp.ClientSession) -> str:
    """Получает токен авторизации Kitsu API."""
    auth_url = "https://kitsu.io/api/oauth/token"
    payload = {
        "grant_type": "password",
        "username": os.getenv("KITSU_USERNAME"),
        "password": os.getenv("KITSU_PASSWORD")
    }
    async with session.post(auth_url, data=urlencode(payload), headers={"Content-Type": "application/x-www-form-urlencoded"}) as response:
        if response.status != 200:
            raise KitsuApiError(f"Не удалось авторизоваться: HTTP {response.status}")
        data = await response.json()
        token = data.get("access_token")
        if not token:
            raise KitsuApiError("Токен авторизации не получен")
        return token

@backoff.on_exception(
    backoff.expo,
    (aiohttp.ClientError, asyncio.TimeoutError),
    max_tries=3,
    max_time=10,
    jitter=backoff.full_jitter
)
async def search_anime(session: aiohttp.ClientSession, token: str, query: str) -> Dict:
    """Ищет аниме по запросу через Kitsu API."""
    search_url = f"https://kitsu.io/api/edge/anime?filter[text]={quote(query)}"
    headers = {"Authorization": f"Bearer {token}"}
    async with session.get(search_url, headers=headers) as response:
        if response.status != 200:
            raise KitsuApiError(f"Ошибка поиска аниме: HTTP {response.status}")
        return await response.json()

def create_anime_embed(anime: AnimeInfo) -> Embed:
    """Создает Embed для отображения информации об аниме."""
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

@backoff.on_exception(
    backoff.expo,
    (aiohttp.ClientError, asyncio.TimeoutError, KitsuApiError),
    max_tries=3,
    max_time=15,
    jitter=backoff.full_jitter
)
async def kitsu(interaction: Interaction, bot_client, query: str) -> None:
    """Команда /kitsu: Поиск информации об аниме на Kitsu."""
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
            anime = AnimeInfo(
                title=attributes.get("titles", {}).get("en") or
                      attributes.get("titles", {}).get("en_jp") or
                      attributes.get("canonicalTitle", "Без названия"),
                synopsis=synopsis,
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
            view = KitsuView(anime.kitsu_url, anime.youtube_id, interaction.user, anime)

            # Отправка ответа
            message = await interaction.followup.send(embed=embed, view=view, ephemeral=False)
            view.message = message

    except KitsuApiError:
        await interaction.followup.send("Ошибка при запросе к Kitsu API. Попробуйте позже.", ephemeral=False)
    except discord.errors.NotFound:
        await interaction.followup.send("Взаимодействие устарело. Попробуйте снова.", ephemeral=True)
    except Exception:
        await interaction.followup.send("Произошла неизвестная ошибка. Обратитесь к администратору.", ephemeral=False)

def create_command(bot_client) -> app_commands.Command:
    """Создает слеш-команду /kitsu."""
    @app_commands.command(name="kitsu", description=description)
    @app_commands.describe(query="Название аниме для поиска")
    async def wrapper(interaction: Interaction, query: str) -> None:
        await kitsu(interaction, bot_client, query)

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