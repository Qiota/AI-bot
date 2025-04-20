import discord
import logging
import asyncio
from urllib.parse import quote_plus, urlparse
from aiohttp import ClientSession, ClientError, ClientResponseError, ClientTimeout
from discord import app_commands, Embed, Colour, File, DMChannel, ButtonStyle, ui
from decouple import config
from io import BytesIO
from mimetypes import guess_extension
from pathlib import Path
from ..systemLog import logger

description = "Показать результаты поиска Google или изображения (10 результатов на страницу)"

class SearchView(ui.View):
    def __init__(self, cog: 'GoogleSearch', query: str, image: bool, total_results: int, current_page: int = 1):
        super().__init__(timeout=600)  # Кнопки активны 10 минут
        self.cog = cog
        self.query = query
        self.image = image
        self.total_results = total_results
        self.current_page = current_page
        self.max_page = (total_results + 9) // 10 if total_results > 0 else 1
        # Добавляем кнопку "Источник" с URL
        source_url = f"https://google.com/search?tbm=isch&q={quote_plus(query)}" if image else f"https://google.com/search?q={quote_plus(query)}"
        self.add_item(ui.Button(label="Источник", style=ButtonStyle.link, url=source_url, emoji="🔗", row=0))
        # Добавляем кнопки стрелок только для текстового поиска
        if not self.image:
            self.update_buttons()

    def update_buttons(self):
        # Индексы 0 и 2 соответствуют кнопкам "⬅️" и "➡️"
        self.children[0].disabled = self.current_page == 1
        self.children[2].disabled = self.current_page >= self.max_page

    @ui.button(label="⬅️", style=ButtonStyle.primary, row=0)
    async def previous_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        self.current_page -= 1
        self.update_buttons()
        await self.update_message(interaction)

    @ui.button(label="➡️", style=ButtonStyle.primary, row=0)
    async def next_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        self.current_page += 1
        self.update_buttons()
        await self.update_message(interaction)

    async def update_message(self, interaction: discord.Interaction):
        try:
            start_index = (self.current_page - 1) * 10 + 1
            if self.image:
                # Для изображений отображаем только первую страницу, так как кнопок стрелок нет
                data = await self.cog._fetch_google_results(self.query, search_type="image", start=1)
                items = data.get("items", [])
                if not items:
                    await interaction.edit_original_response(content="Изображения не найдены", embed=None, attachments=[], view=self)
                    return

                embed = Embed(
                    title="Google Images",
                    description=f"Найдено: {self.total_results} изображений\nЗапрос: `{self.query}`",
                    colour=Colour.blue(),
                )
                embed.set_thumbnail(url="https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_92x30dp.png")

                image_files = []
                for item in items:
                    image_file = await self.cog._fetch_image(item["link"])
                    if image_file:
                        # Формируем ALT-текст и добавляем ссылку на источник
                        alt_text = item.get("title", f"Изображение по запросу {self.query}")
                        alt_text = self.cog.truncate_text(alt_text, 100)
                        description = f"ALT: {alt_text} | Источник: {item['image']['contextLink']}"
                        image_file = File(
                            fp=image_file.fp,
                            filename=image_file.filename,
                            description=description
                        )
                        image_files.append(image_file)

                if image_files:
                    await interaction.edit_original_response(embed=embed, attachments=image_files, view=self)
                else:
                    await interaction.edit_original_response(content="Не удалось загрузить изображения", embed=embed, attachments=[], view=self)
            else:
                # Для текстового поиска сохраняем переключение страниц
                data = await self.cog._fetch_google_results(self.query, start=start_index)
                items = data.get("items", [])
                if not items:
                    await interaction.edit_original_response(content="Результаты не найдены на этой странице", embed=None, attachments=[], view=self)
                    return

                embed = Embed(
                    title=f"Google (Страница {self.current_page}/{self.max_page})",
                    description=f"Найдено результатов: {self.total_results}\nЗапрос: `{self.query}`",
                    colour=Colour.blue(),
                )
                embed.set_thumbnail(url="https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_92x30dp.png")
                embed.set_footer(text=f"Страница {self.current_page}/{self.max_page}")

                for i, hit in enumerate(items, 1):
                    url = hit["link"]
                    title = self.cog.truncate_text(hit.get("title", "Без заголовка"), self.cog.MAX_TITLE_LENGTH)
                    snippet = self.cog.truncate_text(hit.get("snippet", "Без описания"), self.cog.MAX_SNIPPET_LENGTH)
                    breadcrumbs = self.cog.generate_breadcrumbs(url)
                    embed.add_field(
                        name=f"{i + (self.current_page - 1) * 10}. {title}",
                        value=f"[{breadcrumbs}]({url})\n{snippet}",
                        inline=False
                    )

                await interaction.edit_original_response(embed=embed, attachments=[], view=self)
        except Exception as e:
            logger.error(f"Ошибка при обновлении страницы: {str(e)}", exc_info=True)
            await interaction.edit_original_response(content="Произошла ошибка при загрузке страницы", embed=None, attachments=[], view=self)

class GoogleSearch:
    GSEARCH_BASE_URL = "https://www.googleapis.com/customsearch/v1"
    MAX_TITLE_LENGTH = 256
    MAX_SNIPPET_LENGTH = 1024
    SVG_MIME = "image/svg+xml"
    MAX_FILE_SIZE = 8 * 1024 * 1024  # 8 МБ

    def __init__(self, session: ClientSession):
        self.session = session
        self.G_SEARCH_KEY = config("G_SEARCH_KEY", default=None)
        self.G_CSE = config("G_CSE", default=None)
        if not (self.G_SEARCH_KEY and self.G_CSE):
            logger.error("Отсутствуют ключи Google API (G_SEARCH_KEY, G_CSE)")
            raise ValueError("Требуются ключи Google API (G_SEARCH_KEY, G_CSE).")

    async def _fetch_google_results(self, query: str, search_type: str = None, start: int = 1) -> dict:
        if not query.strip():
            raise ValueError("Запрос не может быть пустым")
        if len(query) > 100:
            raise ValueError("Запрос слишком длинный (максимум 100 символов)")

        params = {
            "key": self.G_SEARCH_KEY,
            "cx": self.G_CSE,
            "q": f"{query} -filetype:svg" if search_type == "image" else query,
            "num": 10,  # Фиксированное количество результатов
            "safe": "off",  # Безопасный поиск отключен
            "start": start,
        }
        if search_type:
            params["searchType"] = search_type

        try:
            async with self.session.get(self.GSEARCH_BASE_URL, params=params, timeout=ClientTimeout(total=10)) as response:
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

    async def _fetch_image(self, image_url: str) -> File | None:
        timeouts = [3.0, 4.0, 5.0]  # Динамические таймауты
        for attempt, timeout in enumerate(timeouts, 1):
            try:
                async with self.session.get(image_url, timeout=ClientTimeout(total=timeout)) as resp:
                    if not resp.ok or not resp.content_type.startswith("image/") or resp.content_type == self.SVG_MIME:
                        return None
                    image = await resp.read()
                    if len(image) > self.MAX_FILE_SIZE:
                        logger.warning(f"Изображение {image_url} превышает 8 МБ, пропущено")
                        return None
                    filename = self._extract_filename(resp)
                    return File(BytesIO(image), filename=filename)
            except asyncio.TimeoutError:
                logger.error(f"Таймаут при загрузке изображения {image_url} (попытка {attempt}/{len(timeouts)}, таймаут {timeout}s)")
                if attempt == len(timeouts):
                    return None
            except ClientError as e:
                logger.error(f"Ошибка HTTP при загрузке изображения {image_url}: {str(e)}", exc_info=True)
                return None
        return None

    @staticmethod
    def _extract_filename(response) -> str:
        fn = Path(getattr(response.content_disposition, "filename", response.url.path) or "image").stem
        ext = guess_extension(response.headers.get("Content-Type", ""), strict=False) or ".png"
        return fn + ext

    @staticmethod
    def generate_breadcrumbs(url: str, num_parts: int = 4) -> str:
        parsed = urlparse(url)
        path_parts = parsed.path.strip("/").split("/")[:num_parts]
        domain = parsed.netloc.removeprefix("www.").split(".")[0].capitalize()
        parts = [domain] + [p.replace("_", " ").replace("-", " ").capitalize() for p in path_parts]
        return " > ".join(parts[:num_parts])

    @staticmethod
    def truncate_text(text: str, max_length: int) -> str:
        return text[:max_length - 3] + "..." if len(text) > max_length else text

async def google(interaction: discord.Interaction, cog: GoogleSearch, query: str, image: bool = False) -> None:
    await interaction.response.defer(ephemeral=True)

    try:
        if image:
            data = await cog._fetch_google_results(query, search_type="image")
            items = data.get("items", [])
            total_results = int(data.get("searchInformation", {}).get("totalResults", "0"))
            if not items:
                await interaction.followup.send("Изображения не найдены", ephemeral=True)
                return

            embed = Embed(
                title="Google Images",
                description=f"Найдено: {total_results} изображений\nЗапрос: `{query}`",
                colour=Colour.blue(),
            )
            embed.set_thumbnail(url="https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_92x30dp.png")

            image_files = []
            for item in items:
                image_file = await cog._fetch_image(item["link"])
                if image_file:
                    # Формируем ALT-текст и добавляем ссылку на источник
                    alt_text = item.get("title", f"Изображение по запросу {query}")
                    alt_text = cog.truncate_text(alt_text, 100)
                    description = f"ALT: {alt_text} | Источник: {item['image']['contextLink']}"
                    image_file = File(
                        fp=image_file.fp,
                        filename=image_file.filename,
                        description=description
                    )
                    image_files.append(image_file)

            # Создаем SearchView только для кнопки "Источник"
            view = SearchView(cog, query, image, total_results)
            if image_files:
                await interaction.followup.send(embed=embed, files=image_files, view=view, ephemeral=True)
            else:
                await interaction.followup.send("Не удалось загрузить изображения", embed=embed, view=view, ephemeral=True)
        else:
            data = await cog._fetch_google_results(query)
            items = data.get("items", [])
            total_results = int(data.get("searchInformation", {}).get("totalResults", "0"))

            if not items:
                await interaction.followup.send("Результаты не найдены", ephemeral=True)
                return

            embed = Embed(
                title="Google (Страница 1)",
                description=f"Найдено результатов: {total_results}\nЗапрос: `{query}`",
                colour=Colour.blue(),
            )
            embed.set_thumbnail(url="https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_92x30dp.png")
            embed.set_footer(text="Страница 1")

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

            view = SearchView(cog, query, image, total_results) if total_results > 10 else None
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except ValueError as e:
        logger.error(f"Ошибка при выполнении /google: {str(e)}", exc_info=True)
        await interaction.followup.send(str(e), ephemeral=True)
    except Exception as e:
        logger.error(f"Необработанная ошибка в /google: {str(e)}", exc_info=True)
        await interaction.followup.send("Произошла неизвестная ошибка", ephemeral=True)

def create_command(cog: GoogleSearch):
    @app_commands.command(name="google", description=description)
    @app_commands.describe(
        query="Поисковый запрос",
        image="Искать изображения вместо текста"
    )
    async def wrapper(interaction: discord.Interaction, query: str, image: bool = False) -> None:
        await google(interaction, cog, query, image)
    return wrapper