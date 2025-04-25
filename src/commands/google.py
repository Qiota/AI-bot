import discord
from discord import app_commands, ui, Embed, Colour, File, ButtonStyle
from urllib.parse import quote_plus, urlparse
from aiohttp import ClientSession, ClientError, ClientResponseError, ClientTimeout
from decouple import config
from io import BytesIO
from mimetypes import guess_extension
from pathlib import Path
from typing import Optional, List, Tuple, TYPE_CHECKING
from ..systemLog import logger
from ..commands.restrict import check_bot_access, restrict_command_execution
import traceback
import asyncio

if TYPE_CHECKING:
    from ..client import BotClient

description = "Показать результаты поиска Google или изображения (10 результатов на страницу)"

class SearchView(ui.View):
    """View для управления результатами поиска Google."""
    def __init__(self, cog: 'GoogleSearch', query: str, image: bool, total_results: int, current_page: int = 1) -> None:
        super().__init__(timeout=600)  # Кнопки активны 10 минут
        self.cog: GoogleSearch = cog
        self.query: str = query
        self.image: bool = image
        self.total_results: int = total_results
        self.current_page: int = current_page
        self.max_page: int = (total_results + 9) // 10 if total_results > 0 else 1
        self.image_cache: List[Tuple[File, str]] = []
        self.button_state: Optional[Tuple[str, ButtonStyle, bool, int]] = None
        self.disabled_states: List[bool] = []
        
        # Кнопка "Источник"
        source_url = f"https://google.com/search?tbm=isch&q={quote_plus(query)}&hl=ru" if image else f"https://google.com/search?q={quote_plus(query)}&hl=ru"
        self.add_item(ui.Button(label="Источник", style=ButtonStyle.link, url=source_url, emoji="🔗", row=0))
        
        # Кнопка переключения типа поиска
        toggle_label = "Поиск изображений" if not image else "Поиск Google"
        self.add_item(ui.Button(label=toggle_label, style=ButtonStyle.green, row=0))
        self.children[1].callback = self.toggle_search_type_callback
        
        # Кнопки для текстового или визуального поиска
        if not self.image:
            self.add_item(ui.Button(label="⬅️", style=ButtonStyle.primary, row=0))
            self.add_item(ui.Button(label="➡️", style=ButtonStyle.primary, row=0))
            self.children[2].callback = self.previous_button_callback
            self.children[3].callback = self.next_button_callback
            self.update_buttons()
        else:
            self.add_item(ui.Button(label="🔄", style=ButtonStyle.primary, row=0))
            self.children[2].callback = self.refresh_button_callback

    def update_buttons(self) -> None:
        """Обновляет состояние кнопок пагинации."""
        if len(self.children) > 3:
            self.children[2].disabled = self.current_page == 1
            self.children[3].disabled = self.current_page >= self.max_page

    async def toggle_search_type_callback(self, interaction: discord.Interaction) -> None:
        """Переключает тип поиска (текст/изображения)."""
        await interaction.response.defer()
        toggle_button = self.children[1]
        self.button_state = (toggle_button.label, toggle_button.style, toggle_button.disabled, 1)
        toggle_button.label = "⏳"
        toggle_button.style = ButtonStyle.grey
        toggle_button.disabled = True

        self.disabled_states = []
        for i, child in enumerate(self.children):
            if i == 0 or i == 1:
                continue
            self.disabled_states.append(child.disabled)
            child.disabled = True

        await interaction.edit_original_response(view=self)

        try:
            self.image = not self.image
            self.image_cache.clear()
            self.current_page = 1
            self.children[1].label = "Поиск изображений" if not self.image else "Поиск Google"
            self.children[1].style = ButtonStyle.green
            self.children[1].disabled = False
            self.children[1].callback = self.toggle_search_type_callback

            for i in range(len(self.children) - 1, 1, -1):
                self.remove_item(self.children[i])

            if not self.image:
                prev_button = ui.Button(label="⬅️", style=ButtonStyle.primary, row=0)
                next_button = ui.Button(label="➡️", style=ButtonStyle.primary, row=0)
                prev_button.callback = self.previous_button_callback
                next_button.callback = self.next_button_callback
                self.add_item(prev_button)
                self.add_item(next_button)
                self.update_buttons()
            else:
                refresh_button = ui.Button(label="🔄", style=ButtonStyle.primary, row=0)
                refresh_button.callback = self.refresh_button_callback
                self.add_item(refresh_button)

            await self.update_message(interaction)
        except Exception as e:
            logger.error(f"Ошибка при переключении типа поиска: {e}\n{traceback.format_exc()}")
            await self.update_message(interaction)

    async def previous_button_callback(self, interaction: discord.Interaction) -> None:
        """Переходит на предыдущую страницу результатов."""
        await interaction.response.defer()
        prev_button = self.children[2]
        self.button_state = (prev_button.label, prev_button.style, prev_button.disabled, 2)
        prev_button.label = "⏳"
        prev_button.style = ButtonStyle.grey
        prev_button.disabled = True

        self.disabled_states = []
        for i, child in enumerate(self.children):
            if i == 0 or i == 2:
                continue
            self.disabled_states.append(child.disabled)
            child.disabled = True

        await interaction.edit_original_response(view=self)

        try:
            self.current_page -= 1
            self.update_buttons()
            await self.update_message(interaction)
        except Exception as e:
            logger.error(f"Ошибка при переходе на предыдущую страницу: {e}\n{traceback.format_exc()}")
            await self.update_message(interaction)

    async def next_button_callback(self, interaction: discord.Interaction) -> None:
        """Переходит на следующую страницу результатов."""
        await interaction.response.defer()
        next_button = self.children[3]
        self.button_state = (next_button.label, next_button.style, next_button.disabled, 3)
        next_button.label = "⏳"
        next_button.style = ButtonStyle.grey
        next_button.disabled = True

        self.disabled_states = []
        for i, child in enumerate(self.children):
            if i == 0 or i == 3:
                continue
            self.disabled_states.append(child.disabled)
            child.disabled = True

        await interaction.edit_original_response(view=self)

        try:
            self.current_page += 1
            self.update_buttons()
            await self.update_message(interaction)
        except Exception as e:
            logger.error(f"Ошибка при переходе на следующую страницу: {e}\n{traceback.format_exc()}")
            await self.update_message(interaction)

    async def refresh_button_callback(self, interaction: discord.Interaction) -> None:
        """Обновляет изображения для текущего запроса."""
        await interaction.response.defer()
        refresh_button = self.children[2]
        self.button_state = (refresh_button.label, refresh_button.style, refresh_button.disabled, 2)
        refresh_button.label = "⏳"
        refresh_button.style = ButtonStyle.grey
        refresh_button.disabled = True

        self.disabled_states = []
        for i, child in enumerate(self.children):
            if i == 0 or i == 2:
                continue
            self.disabled_states.append(child.disabled)
            child.disabled = True

        await interaction.edit_original_response(view=self)

        try:
            current_files = interaction.message.attachments if interaction.message else []
            for attachment in current_files:
                url = attachment.url
                self.image_cache.append((File(
                    fp=BytesIO(await attachment.read()),
                    filename=attachment.filename
                ), url))
            await self.update_message(interaction)
        except Exception as e:
            logger.error(f"Ошибка при обновлении изображений: {e}\n{traceback.format_exc()}")
            await self.update_message(interaction)

    async def update_message(self, interaction: discord.Interaction) -> None:
        """Обновляет сообщение с результатами поиска."""
        try:
            if self.image:
                image_files = []
                cached_urls = {url for _, url in self.image_cache}
                start_index = len(self.image_cache) + 1

                while len(image_files) < 10 and start_index <= self.total_results:
                    data = await self.cog._fetch_google_results(self.query, search_type="image", start=start_index)
                    items = data.get("items", [])
                    if not items:
                        break

                    for item in items:
                        image_url = item["link"]
                        if image_url in cached_urls:
                            continue
                        image_file = await self.cog._fetch_image(image_url)
                        if image_file:
                            image_files.append(image_file)
                            cached_urls.add(image_url)
                        if len(image_files) >= 10:
                            break

                    start_index += len(items)

                if self.button_state:
                    label, style, disabled, index = self.button_state
                    self.children[index].label = label
                    self.children[index].style = style
                    self.children[index].disabled = disabled
                    self.button_state = None
                else:
                    index = None

                disabled_index = 0
                for i, child in enumerate(self.children):
                    if i == 0 or (index is not None and i == index):
                        continue
                    if disabled_index < len(self.disabled_states):
                        child.disabled = self.disabled_states[disabled_index]
                        disabled_index += 1
                    else:
                        child.disabled = False
                self.disabled_states = []

                self.children[1].label = "Поиск Google" if self.image else "Поиск изображений"

                if not image_files:
                    self.children[2].style = ButtonStyle.grey
                    self.children[2].label = "🔄"
                    self.children[2].disabled = True
                    await interaction.edit_original_response(content="Не удалось загрузить изображения", embed=None, attachments=[], view=self)
                    return

                if len(image_files) < 10:
                    content = "Больше изображений не найдено" if len(self.image_cache) + len(image_files) < self.total_results else "Все изображения просмотрены"
                    embed = Embed(
                        title="Google Images",
                        description=f"Найдено: {self.total_results} изображений\nЗапрос: `{self.query}`\nПоказано: {len(self.image_cache) + len(image_files)} из {self.total_results}",
                        colour=Colour.blue(),
                    )
                    embed.set_thumbnail(url="https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_92x30dp.png")
                    self.children[2].style = ButtonStyle.grey
                    self.children[2].label = "🔄"
                    self.children[2].disabled = True
                    await interaction.edit_original_response(content=content, embed=embed, attachments=image_files, view=self)
                    return

                embed = Embed(
                    title="Google Images",
                    description=f"Найдено: {self.total_results} изображений\nЗапрос: `{self.query}`\nПоказано: {len(self.image_cache) + len(image_files)} из {self.total_results}",
                    colour=Colour.blue(),
                )
                embed.set_thumbnail(url="https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_92x30dp.png")
                self.children[2].style = ButtonStyle.primary
                self.children[2].label = "🔄"
                self.children[2].disabled = False
                await interaction.edit_original_response(embed=embed, attachments=image_files, view=self)

            else:
                start_index = (self.current_page - 1) * 10 + 1
                data = await self.cog._fetch_google_results(self.query, start=start_index)
                items = data.get("items", [])

                if self.button_state:
                    label, style, disabled, index = self.button_state
                    self.children[index].label = label
                    self.children[index].style = style
                    self.children[index].disabled = disabled
                    self.button_state = None
                else:
                    index = None

                disabled_index = 0
                for i, child in enumerate(self.children):
                    if i == 0 or (index is not None and i == index):
                        continue
                    if disabled_index < len(self.disabled_states):
                        child.disabled = self.disabled_states[disabled_index]
                        disabled_index += 1
                    else:
                        child.disabled = False
                self.disabled_states = []

                self.children[1].label = "Поиск Google" if self.image else "Поиск изображений"

                if not items:
                    self.update_buttons()
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

                self.update_buttons()
                await interaction.edit_original_response(embed=embed, attachments=[], view=self)
        except Exception as e:
            logger.error(f"Ошибка при обновлении страницы: {e}\n{traceback.format_exc()}")
            if self.button_state:
                label, style, disabled, index = self.button_state
                self.children[index].label = label
                self.children[index].style = style
                self.children[index].disabled = disabled
                self.button_state = None
            else:
                index = None

            disabled_index = 0
            for i, child in enumerate(self.children):
                if i == 0 or (index is not None and i == index):
                    continue
                if disabled_index < len(self.disabled_states):
                    child.disabled = self.disabled_states[disabled_index]
                    disabled_index += 1
                else:
                    child.disabled = False
            self.disabled_states = []

            self.children[1].label = "Поиск Google" if self.image else "Поиск изображений"
            if self.image and len(self.children) > 2:
                self.children[2].style = ButtonStyle.grey
                self.children[2].label = "🔄"
                self.children[2].disabled = True
            else:
                self.update_buttons()
            await interaction.edit_original_response(content="Произошла ошибка при загрузке страницы", embed=None, attachments=[], view=self)

    async def on_timeout(self) -> None:
        """Очищает кэш изображений при истечении таймаута."""
        self.image_cache.clear()
        await super().on_timeout()

class GoogleSearch:
    """Класс для обработки поисковых запросов Google."""
    GSEARCH_BASE_URL: str = "https://www.googleapis.com/customsearch/v1"
    MAX_TITLE_LENGTH: int = 256
    MAX_SNIPPET_LENGTH: int = 1024
    SVG_MIME: str = "image/svg+xml"
    MAX_FILE_SIZE: int = 8 * 1024 * 1024  # 8 МБ

    def __init__(self, bot_client: 'BotClient', session: ClientSession) -> None:
        self.bot_client: BotClient = bot_client
        self.session: ClientSession = session
        self.G_SEARCH_KEY: Optional[str] = config("G_SEARCH_KEY", default=None)
        self.G_CSE: Optional[str] = config("G_CSE", default=None)
        if not (self.G_SEARCH_KEY and self.G_CSE):
            logger.error("Отсутствуют ключи Google API (G_SEARCH_KEY, G_CSE)")
            raise ValueError("Требуются ключи Google API (G_SEARCH_KEY, G_CSE).")

    async def _fetch_google_results(self, query: str, search_type: Optional[str] = None, start: int = 1) -> dict:
        """Запрашивает результаты поиска через Google Custom Search API."""
        if not query.strip():
            raise ValueError("Запрос не может быть пустым")
        if len(query) > 100:
            raise ValueError("Запрос слишком длинный (максимум 100 символов)")

        params = {
            "key": self.G_SEARCH_KEY,
            "cx": self.G_CSE,
            "q": f"{query} -filetype:svg" if search_type == "image" else query,
            "num": 10,
            "safe": "off",
            "start": start,
            "hl": "ru",
        }
        if search_type:
            params["searchType"] = search_type

        try:
            async with self.session.get(self.GSEARCH_BASE_URL, params=params, timeout=ClientTimeout(total=10)) as response:
                response.raise_for_status()
                return await response.json()
        except ClientResponseError as e:
            logger.error(f"Ошибка HTTP при запросе к Google API: {e}\n{traceback.format_exc()}")
            if e.status == 403:
                raise ValueError("Ошибка: Неверные ключи API или доступ запрещён") from e
            elif e.status == 429:
                raise ValueError("Ошибка: Превышена квота запросов Google API") from e
            raise ValueError(f"Ошибка HTTP: {e}") from e
        except ClientError as e:
            logger.error(f"Ошибка соединения с Google API: {e}\n{traceback.format_exc()}")
            raise ValueError(f"Ошибка соединения с Google API: {e}") from e

    async def _fetch_image(self, image_url: str) -> Optional[File]:
        """Загружает изображение по URL."""
        timeouts = [3.0, 4.0, 5.0]
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
            except (asyncio.TimeoutError, ClientError) as e:
                if attempt == len(timeouts):
                    logger.error(f"Не удалось загрузить изображение {image_url} после {len(timeouts)} попыток: {e}")
                return None
            except UnicodeEncodeError as e:
                logger.error(f"Ошибка кодирования IDNA для URL {image_url}: {e}")
                return None
        return None

    @staticmethod
    def _extract_filename(response) -> str:
        """Извлекает имя файла из ответа."""
        fn = Path(getattr(response.content_disposition, "filename", response.url.path) or "image").stem
        ext = guess_extension(response.headers.get("Content-Type", ""), strict=False) or ".png"
        return fn + ext

    @staticmethod
    def generate_breadcrumbs(url: str, num_parts: int = 4) -> str:
        """Генерирует хлебные крошки из URL."""
        parsed = urlparse(url)
        path_parts = parsed.path.strip("/").split("/")[:num_parts]
        domain = parsed.netloc.removeprefix("www.").split(".")[0].capitalize()
        parts = [domain] + [p.replace("_", " ").replace("-", " ").capitalize() for p in path_parts]
        return " > ".join(parts[:num_parts])

    @staticmethod
    def truncate_text(text: str, max_length: int) -> str:
        """Обрезает текст до указанной длины."""
        return text[:max_length - 3] + "..." if len(text) > max_length else text

async def google(interaction: discord.Interaction, cog: 'GoogleSearch', query: str, image: bool = False) -> None:
    """Команда /google: Выполняет поиск в Google и отображает результаты."""
    if not hasattr(cog, 'bot_client'):
        logger.error("bot_client не предоставлен в объекте cog для команды /google")
        await interaction.response.send_message("Ошибка конфигурации бота.", ephemeral=True)
        return

    if not await restrict_command_execution(interaction, cog.bot_client):
        return

    access_result, access_reason = await check_bot_access(interaction, cog.bot_client)
    if not access_result:
        await interaction.response.send_message(
            access_reason or "Бот не имеет доступа к этому каналу.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        if image:
            data = await cog._fetch_google_results(query, search_type="image")
            items = data.get("items", [])
            total_results = int(data.get("searchInformation", {}).get("totalResults", "0"))
            if not items:
                await interaction.followup.send("Изображения не найдены", ephemeral=True)
                return

            image_files = []
            view = SearchView(cog, query, image, total_results)
            cached_urls = {url for _, url in view.image_cache}
            start_index = len(view.image_cache) + 1

            while len(image_files) < 10 and start_index <= total_results:
                if start_index != 1:
                    data = await cog._fetch_google_results(query, search_type="image", start=start_index)
                    items = data.get("items", [])
                    if not items:
                        break

                for item in items:
                    image_url = item["link"]
                    if image_url in cached_urls:
                        continue
                    image_file = await cog._fetch_image(image_url)
                    if image_file:
                        image_files.append(image_file)
                        cached_urls.add(image_url)
                    if len(image_files) >= 10:
                        break

                start_index += len(items)

            if not image_files:
                await interaction.followup.send("Не удалось загрузить изображения", ephemeral=True)
                return

            if len(image_files) < 10:
                content = "Больше изображений не найдено" if len(view.image_cache) + len(image_files) < total_results else "Все изображения просмотрены"
                embed = Embed(
                    title="Google Images",
                    description=f"Найдено: {total_results} изображений\nЗапрос: `{query}`\nПоказано: {len(view.image_cache) + len(image_files)} из {total_results}",
                    colour=Colour.blue(),
                )
                embed.set_thumbnail(url="https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_92x30dp.png")
                view.children[2].style = ButtonStyle.grey
                view.children[2].label = "🔄"
                view.children[2].disabled = True
                view.children[1].label = "Поиск Google" if view.image else "Поиск изображений"
                await interaction.followup.send(content=content, embed=embed, files=image_files, view=view, ephemeral=True)
                return

            embed = Embed(
                title="Google Images",
                description=f"Найдено: {total_results} изображений\nЗапрос: `{query}`\nПоказано: {len(view.image_cache) + len(image_files)} из {total_results}",
                colour=Colour.blue(),
            )
            embed.set_thumbnail(url="https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_92x30dp.png")
            view.children[1].label = "Поиск Google" if view.image else "Поиск изображений"
            await interaction.followup.send(embed=embed, files=image_files, view=view, ephemeral=True)

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

            view = SearchView(cog, query, image, total_results)
            view.children[1].label = "Поиск Google" if view.image else "Поиск изображений"
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except ValueError as e:
        logger.error(f"Ошибка при выполнении /google: {e}\n{traceback.format_exc()}")
        await interaction.followup.send(str(e), ephemeral=True)
    except Exception as e:
        logger.error(f"Необработанная ошибка в /google: {e}\n{traceback.format_exc()}")
        await interaction.followup.send("Произошла неизвестная ошибка", ephemeral=True)

def create_command(cog: 'GoogleSearch') -> app_commands.Command:
    """Создаёт команду /google."""
    @app_commands.command(name="google", description=description)
    @app_commands.describe(
        query="Поисковый запрос",
        image="Искать только изображения"
    )
    async def wrapper(interaction: discord.Interaction, query: str, image: bool = False) -> None:
        await google(interaction, cog, query, image)
    return wrapper