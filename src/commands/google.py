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
        self.image_cache = []  # Массив для хранения кортежей (discord.File, url)
        self.button_state = None  # Для хранения состояния нажатой кнопки (label, style, disabled, index)
        self.disabled_states = []  # Для хранения состояния активности остальных кнопок
        # Добавляем кнопку "Источник" с URL и параметром hl=ru
        source_url = f"https://google.com/search?tbm=isch&q={quote_plus(query)}&hl=ru" if image else f"https://google.com/search?q={quote_plus(query)}&hl=ru"
        self.add_item(ui.Button(label="Источник", style=ButtonStyle.link, url=source_url, emoji="🔗", row=0))
        # Добавляем кнопку переключения типа поиска
        toggle_label = "Поиск изображений" if not image else "Поиск Google"
        self.add_item(ui.Button(label=toggle_label, style=ButtonStyle.green, row=0))
        self.children[1].callback = self.toggle_search_type_callback
        # Добавляем кнопки в зависимости от типа поиска
        if not self.image:
            self.add_item(ui.Button(label="⬅️", style=ButtonStyle.primary, row=0))
            self.add_item(ui.Button(label="➡️", style=ButtonStyle.primary, row=0))
            self.children[2].callback = self.previous_button_callback
            self.children[3].callback = self.next_button_callback
            self.update_buttons()
        else:
            self.add_item(ui.Button(label="🔄", style=ButtonStyle.primary, row=0))
            self.children[2].callback = self.refresh_button_callback

    def update_buttons(self):
        # Индексы 2 и 3 соответствуют кнопкам "⬅️" и "➡️"
        if len(self.children) > 3:  # Проверяем, что кнопки существуют
            self.children[2].disabled = self.current_page == 1
            self.children[3].disabled = self.current_page >= self.max_page

    async def toggle_search_type_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        # Сохраняем состояние нажатой кнопки (индекс 1 - кнопка переключения)
        toggle_button = self.children[1]
        self.button_state = (toggle_button.label, toggle_button.style, toggle_button.disabled, 1)
        toggle_button.label = "⏳"
        toggle_button.style = ButtonStyle.grey
        toggle_button.disabled = True

        # Отключаем остальные кнопки, кроме "Источник"
        self.disabled_states = []
        for i, child in enumerate(self.children):
            if i == 0 or i == 1:  # Пропускаем "Источник" и нажатую кнопку
                continue
            self.disabled_states.append(child.disabled)
            child.disabled = True

        await interaction.edit_original_response(view=self)

        try:
            # Переключаем тип поиска
            self.image = not self.image
            # Очищаем кэш изображений при переключении
            self.image_cache.clear()
            # Сбрасываем страницу на первую
            self.current_page = 1
            # Обновляем метку кнопки переключения
            toggle_label = "Поиск изображений" if not self.image else "Поиск Google"
            # Обновляем существующую кнопку вместо создания новой
            self.children[1].label = toggle_label
            self.children[1].style = ButtonStyle.green
            self.children[1].disabled = False
            # Сохраняем callback
            self.children[1].callback = self.toggle_search_type_callback
            # Удаляем старые кнопки, кроме "Источник" и кнопки переключения
            for i in range(len(self.children) - 1, 1, -1):  # Удаляем с конца, кроме первых двух
                self.remove_item(self.children[i])
            # Добавляем новые кнопки в зависимости от типа поиска
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
            logger.error(f"Ошибка при переключении типа поиска: {str(e)}", exc_info=True)
            # Восстановление состояния будет выполнено в update_message
            await self.update_message(interaction)

    async def previous_button_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        # Сохраняем состояние нажатой кнопки (индекс 2 - кнопка "⬅️")
        prev_button = self.children[2]
        self.button_state = (prev_button.label, prev_button.style, prev_button.disabled, 2)
        prev_button.label = "⏳"
        prev_button.style = ButtonStyle.grey
        prev_button.disabled = True

        # Отключаем остальные кнопки, кроме "Источник"
        self.disabled_states = []
        for i, child in enumerate(self.children):
            if i == 0 or i == 2:  # Пропускаем "Источник" и нажатую кнопку
                continue
            self.disabled_states.append(child.disabled)
            child.disabled = True

        await interaction.edit_original_response(view=self)

        try:
            self.current_page -= 1
            self.update_buttons()
            await self.update_message(interaction)
        except Exception as e:
            logger.error(f"Ошибка при переходе на предыдущую страницу: {str(e)}", exc_info=True)
            await self.update_message(interaction)

    async def next_button_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        # Сохраняем состояние нажатой кнопки (индекс 3 - кнопка "➡️")
        next_button = self.children[3]
        self.button_state = (next_button.label, next_button.style, next_button.disabled, 3)
        next_button.label = "⏳"
        next_button.style = ButtonStyle.grey
        next_button.disabled = True

        # Отключаем остальные кнопки, кроме "Источник"
        self.disabled_states = []
        for i, child in enumerate(self.children):
            if i == 0 or i == 3:  # Пропускаем "Источник" и нажатую кнопку
                continue
            self.disabled_states.append(child.disabled)
            child.disabled = True

        await interaction.edit_original_response(view=self)

        try:
            self.current_page += 1
            self.update_buttons()
            await self.update_message(interaction)
        except Exception as e:
            logger.error(f"Ошибка при переходе на следующую страницу: {str(e)}", exc_info=True)
            await self.update_message(interaction)

    async def refresh_button_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        # Сохраняем состояние нажатой кнопки (индекс 2 - кнопка "🔄")
        refresh_button = self.children[2]
        self.button_state = (refresh_button.label, refresh_button.style, refresh_button.disabled, 2)
        refresh_button.label = "⏳"
        refresh_button.style = ButtonStyle.grey
        refresh_button.disabled = True

        # Отключаем остальные кнопки, кроме "Источник"
        self.disabled_states = []
        for i, child in enumerate(self.children):
            if i == 0 or i == 2:  # Пропускаем "Источник" и нажатую кнопку
                continue
            self.disabled_states.append(child.disabled)
            child.disabled = True

        await interaction.edit_original_response(view=self)

        try:
            # Сохраняем текущие изображения и их URL в кэш перед загрузкой новых
            current_files = interaction.message.attachments if interaction.message else []
            for attachment in current_files:
                url = attachment.url
                self.image_cache.append((File(
                    fp=BytesIO(await attachment.read()),
                    filename=attachment.filename
                ), url))
            await self.update_message(interaction)
        except Exception as e:
            logger.error(f"Ошибка при обновлении изображений: {str(e)}", exc_info=True)
            await self.update_message(interaction)

    async def update_message(self, interaction: discord.Interaction):
        try:
            if self.image:
                image_files = []
                cached_urls = {url for _, url in self.image_cache}  # Множество URL из кэша
                start_index = len(self.image_cache) + 1

                # Продолжаем запрашивать изображения, пока не наберём 10 или не закончатся результаты
                while len(image_files) < 10 and start_index <= self.total_results:
                    data = await self.cog._fetch_google_results(self.query, search_type="image", start=start_index)
                    items = data.get("items", [])
                    if not items:
                        break  # Больше нет результатов

                    for item in items:
                        image_url = item["link"]
                        # Пропускаем изображение, если его URL уже есть в кэше
                        if image_url in cached_urls:
                            continue
                        image_file = await self.cog._fetch_image(image_url)
                        if image_file:
                            image_file = File(
                                fp=image_file.fp,
                                filename=image_file.filename
                            )
                            image_files.append(image_file)
                            cached_urls.add(image_url)
                        if len(image_files) >= 10:
                            break  # Достигли 10 файлов, прерываем

                    # Увеличиваем start_index для следующего чанка
                    start_index += len(items)

                # Восстанавливаем состояние нажатой кнопки
                if self.button_state:
                    label, style, disabled, index = self.button_state
                    self.children[index].label = label
                    self.children[index].style = style
                    self.children[index].disabled = disabled
                    self.button_state = None
                else:
                    index = None

                # Восстанавливаем активность остальных кнопок
                disabled_index = 0
                for i, child in enumerate(self.children):
                    if i == 0 or (index is not None and i == index):  # Пропускаем "Источник" и нажатую кнопку
                        continue
                    if disabled_index < len(self.disabled_states):
                        child.disabled = self.disabled_states[disabled_index]
                        disabled_index += 1
                    else:
                        child.disabled = False  # Если не хватает сохранённых состояний, включаем кнопку
                self.disabled_states = []

                # Обновляем текст кнопки переключения типа поиска
                self.children[1].label = "Поиск Google" if self.image else "Поиск изображений"

                # Проверяем, сколько изображений удалось загрузить
                if not image_files:
                    self.children[2].style = ButtonStyle.grey
                    self.children[2].label = "🔄"
                    self.children[2].disabled = True
                    await interaction.edit_original_response(content="Не удалось загрузить изображения", embed=None, attachments=[], view=self)
                    return

                # Если меньше 10, но больше 0, сообщаем, что больше нет изображений
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

                # Если набрали 10 изображений
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
                # Для текстового поиска сохраняем переключение страниц
                start_index = (self.current_page - 1) * 10 + 1
                data = await self.cog._fetch_google_results(self.query, start=start_index)
                items = data.get("items", [])
                
                # Восстанавливаем состояние нажатой кнопки
                if self.button_state:
                    label, style, disabled, index = self.button_state
                    self.children[index].label = label
                    self.children[index].style = style
                    self.children[index].disabled = disabled
                    self.button_state = None
                else:
                    index = None

                # Восстанавливаем активность остальных кнопок
                disabled_index = 0
                for i, child in enumerate(self.children):
                    if i == 0 or (index is not None and i == index):  # Пропускаем "Источник" и нажатую кнопку
                        continue
                    if disabled_index < len(self.disabled_states):
                        child.disabled = self.disabled_states[disabled_index]
                        disabled_index += 1
                    else:
                        child.disabled = False  # Если не хватает сохранённых состояний, включаем кнопку
                self.disabled_states = []

                # Обновляем текст кнопки переключения типа поиска
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
            logger.error(f"Ошибка при обновлении страницы: {str(e)}", exc_info=True)
            # Восстанавливаем состояние нажатой кнопки
            if self.button_state:
                label, style, disabled, index = self.button_state
                self.children[index].label = label
                self.children[index].style = style
                self.children[index].disabled = disabled
                self.button_state = None
            else:
                index = None

            # Восстанавливаем активность остальных кнопок
            disabled_index = 0
            for i, child in enumerate(self.children):
                if i == 0 or (index is not None and i == index):  # Пропускаем "Источник" и нажатую кнопку
                    continue
                if disabled_index < len(self.disabled_states):
                    child.disabled = self.disabled_states[disabled_index]
                    disabled_index += 1
                else:
                    child.disabled = False  # Если не хватает сохранённых состояний, включаем кнопку
            self.disabled_states = []

            # Обновляем текст кнопки переключения типа поиска
            self.children[1].label = "Поиск Google" if self.image else "Поиск изображений"

            if self.image and len(self.children) > 2:
                self.children[2].style = ButtonStyle.grey
                self.children[2].label = "🔄"
                self.children[2].disabled = True
            else:
                self.update_buttons()
            await interaction.edit_original_response(content="Произошла ошибка при загрузке страницы", embed=None, attachments=[], view=self)

    async def on_timeout(self):
        # Очищаем кэш изображений при истечении таймаута
        self.image_cache.clear()
        await super().on_timeout()

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
            "hl": "ru",  # Локализация на русском языке
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
            except (asyncio.TimeoutError, ClientError) as e:
                if attempt == len(timeouts):
                    logger.error(f"Не удалось загрузить изображение {image_url} после {len(timeouts)} попыток: {str(e)}")
                return None
            except UnicodeEncodeError as e:
                logger.error(f"Ошибка кодирования IDNA для URL {image_url}: {str(e)}")
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
            # Поиск изображений
            data = await cog._fetch_google_results(query, search_type="image")
            items = data.get("items", [])
            total_results = int(data.get("searchInformation", {}).get("totalResults", "0"))
            if not items:
                await interaction.followup.send("Изображения не найдены", ephemeral=True)
                return

            image_files = []
            view = SearchView(cog, query, image, total_results)
            cached_urls = {url for _, url in view.image_cache}  # Множество URL из кэша
            start_index = len(view.image_cache) + 1

            # Продолжаем запрашивать изображения, пока не наберём 10 или не закончатся результаты
            while len(image_files) < 10 and start_index <= total_results:
                if start_index != 1:  # Первый запрос уже выполнен
                    data = await cog._fetch_google_results(query, search_type="image", start=start_index)
                    items = data.get("items", [])
                    if not items:
                        break  # Больше нет результатов

                for item in items:
                    image_url = item["link"]
                    # Пропускаем изображение, если его URL уже есть в кэше
                    if image_url in cached_urls:
                        continue
                    image_file = await cog._fetch_image(image_url)
                    if image_file:
                        image_file = File(
                            fp=image_file.fp,
                            filename=image_file.filename
                        )
                        image_files.append(image_file)
                        cached_urls.add(image_url)
                    if len(image_files) >= 10:
                        break  # Достигли 10 файлов, прерываем

                # Увеличиваем start_index для следующего чанка
                start_index += len(items)

            # Проверяем, сколько изображений удалось загрузить
            if not image_files:
                await interaction.followup.send("Не удалось загрузить изображения", ephemeral=True)
                return

            # Если меньше 10, сообщаем, что больше нет изображений
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
                # Обновляем текст кнопки переключения типа поиска
                view.children[1].label = "Поиск Google" if view.image else "Поиск изображений"
                await interaction.followup.send(content=content, embed=embed, files=image_files, view=view, ephemeral=True)
                return

            # Если набрали 10 изображений
            embed = Embed(
                title="Google Images",
                description=f"Найдено: {total_results} изображений\nЗапрос: `{query}`\nПоказано: {len(view.image_cache) + len(image_files)} из {total_results}",
                colour=Colour.blue(),
            )
            embed.set_thumbnail(url="https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_92x30dp.png")
            # Обновляем текст кнопки переключения типа поиска
            view.children[1].label = "Поиск Google" if view.image else "Поиск изображений"
            await interaction.followup.send(embed=embed, files=image_files, view=view, ephemeral=True)

        else:
            # Поиск текста
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
            # Обновляем текст кнопки переключения типа поиска
            view.children[1].label = "Поиск Google" if view.image else "Поиск изображений"
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
        image="Искать только изображения"
    )
    async def wrapper(interaction: discord.Interaction, query: str, image: bool = False) -> None:
        await google(interaction, cog, query, image)
    return wrapper