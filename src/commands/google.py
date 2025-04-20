import discord
from urllib.parse import quote_plus, urlparse
from aiohttp import ClientSession, ClientError, ClientResponseError
from discord import app_commands, Embed, Colour
from decouple import config
from ..systemLog import logger

description = "Показать результаты поиска Google"

class GoogleSearch:
    GSEARCH_BASE_URL = "https://www.googleapis.com/customsearch/v1"
    MAX_TITLE_LENGTH = 256
    MAX_SNIPPET_LENGTH = 1024

    def __init__(self, session: ClientSession):
        self.session = session
        self.G_SEARCH_KEY = config("G_SEARCH_KEY", default=None)
        self.G_CSE = config("G_CSE", default=None)
        if not (self.G_SEARCH_KEY and self.G_CSE):
            logger.error("Отсутствуют ключи Google API (G_SEARCH_KEY, G_CSE)")
            raise ValueError("Требуются ключи Google API (G_SEARCH_KEY, G_CSE). Убедитесь, что они заданы в .env файле.")

    async def _fetch_google_results(self, query: str, num: int = 1) -> dict:
        if not query.strip():
            raise ValueError("Запрос не может быть пустым")
        if len(query) > 100:
            raise ValueError("Запрос слишком длинный (максимум 100 символов)")
        
        try:
            async with self.session.get(
                self.GSEARCH_BASE_URL,
                params={
                    "key": self.G_SEARCH_KEY,
                    "cx": self.G_CSE,
                    "q": query,
                    "num": num,
                },
                timeout=10
            ) as response:
                response.raise_for_status()
                return await response.json()
        except ClientResponseError as e:
            logger.error(f"Ошибка HTTP при запросе к Google API: {str(e)}", exc_info=True)
            if e.status == 403:
                raise ValueError("Ошибка: Неверные ключи API или доступ запрещён") from e
            elif e.status == 429:
                raise ValueError("Ошибка: Превышена квота запросов Google API") from e
            raise ValueError(f"Ошибка HTTP: {str(e)}") from e
        except ClientError as e:
            logger.error(f"Ошибка соединения с Google API: {str(e)}", exc_info=True)
            raise ValueError(f"Ошибка соединения с Google API: {str(e)}") from e

    @staticmethod
    def generate_breadcrumbs(url: str, num_parts: int = 4) -> str:
        parsed = urlparse(url)
        path_parts = parsed.path.strip("/").split("/")[:num_parts]
        domain = parsed.netloc.removeprefix("www.").split(".")[0].capitalize()
        parts = [domain] + [p.replace("_", " ").replace("-", " ").capitalize() for p in path_parts]
        return " > ".join(parts[:num_parts])

    @staticmethod
    def truncate_text(text: str, max_length: int) -> str:
        """Обрезает текст до указанной длины, добавляя '...'."""
        return text[:max_length - 3] + "..." if len(text) > max_length else text

async def google(interaction: discord.Interaction, cog: GoogleSearch, query: str, num: int = 1) -> None:
    """Команда /google: Показывает результаты поиска Google."""
    await interaction.response.defer(ephemeral=True)
    if num < 1 or num > 10:
        await interaction.followup.send("Количество результатов должно быть от 1 до 10", ephemeral=True)
        return

    try:
        data = await cog._fetch_google_results(query, num)
        items = data.get("items", [])
        total_results = data.get("searchInformation", {}).get("totalResults", "0")

        if not items:
            await interaction.followup.send("Результаты не найдены", ephemeral=True)
            return

        if num == 1:
            await interaction.followup.send(items[0]["link"])
        else:
            embed = Embed(
                title="Результаты поиска Google",
                description=f"Найдено результатов: {total_results}\nЗапрос: `{query}`",
                url=f"https://google.com/search?q={quote_plus(query)}",
                colour=Colour.blue(),
            )
            embed.set_thumbnail(url="https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_92x30dp.png")
            embed.set_footer(text="Поиск через Google Custom Search API")

            for i, hit in enumerate(items, 1):
                url = hit["link"]
                title = cog.truncate_text(hit.get("title", "Без заголовка"), cog.MAX_TITLE_LENGTH)
                snippet = cog.truncate_text(hit.get("snippet", "Без описания"), cog.MAX_SNIPPET_LENGTH)
                breadcrumbs = cog.generate_breadcrumbs(url)
                embed.add_field(
                    name=f"{i}. {title}",
                    value=f"[{breadcrumbs}]({url})\n{snippet}",
                    inline=False
                )

            await interaction.followup.send(embed=embed)
    except ValueError as e:
        logger.error(f"Ошибка при выполнении /google: {str(e)}", exc_info=True)
        await interaction.followup.send(str(e), ephemeral=True)
    except Exception as e:
        logger.error(f"Необработанная ошибка в /google: {str(e)}", exc_info=True)
        await interaction.followup.send("Произошла неизвестная ошибка", ephemeral=True)

def create_command(cog: GoogleSearch):
    @app_commands.command(name="google", description=description)
    @app_commands.describe(query="Поисковый запрос", num="Количество результатов (1-10, по умолчанию 1)")
    async def wrapper(interaction: discord.Interaction, query: str, num: int = 1) -> None:
        await google(interaction, cog, query, num)
    return wrapper