import asyncio
from typing import Optional

import discord
from discord import ButtonStyle, Interaction, Embed
from discord.ui import Modal, TextInput, Button, View

from ....systemLog import logger
from .models import SearchResult


class PageSelectModal(Modal, title="Перейти к странице и тайтлу"):
    """Модальное окно для ввода номера страницы и тайтла."""
    page_input = TextInput(
        label="Номер страницы",
        placeholder="Введите номер страницы (например, 1)",
        default="1",
        required=True,
        min_length=1,
        max_length=5
    )
    title_input = TextInput(
        label="Номер тайтла",
        placeholder="Введите номер тайтла на странице",
        default="1",
        required=True,
        min_length=1,
        max_length=3
    )

    def __init__(self, view: 'NavigationView'):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: Interaction) -> None:
        """Обрабатывает отправку данных из модального окна."""
        if interaction.user != self.view.original_user:
            await interaction.response.send_message(
                "Только пользователь, вызвавший команду, может её использовать.",
                ephemeral=True
            )
            return

        import re
        page_str, title_str = self.page_input.value, self.title_input.value
        if not (re.match(r'^\d+$', page_str) and re.match(r'^\d+$', title_str)):
            await interaction.response.send_message(
                "Введите только числа для страницы и тайтла.",
                ephemeral=True
            )
            return

        page, title_index = int(page_str), int(title_str) - 1
        if page < 1 or title_index < 0:
            await interaction.response.send_message(
                "Номер страницы и тайтла должны быть положительными.",
                ephemeral=True
            )
            return

        await self.view.navigate_to_page_and_title(interaction, page, title_index)


class NavigationView(View):
    """Упрощённый View для навигации по результатам поиска."""
    def __init__(
        self,
        results: list[SearchResult],
        original_user: discord.User,
        query: Optional[str],
        current_page: int,
        total_pages: int,
        current_index: int = 0,
        timeout: int = 300
    ):
        super().__init__(timeout=timeout)
        self.results = results
        self.original_user = original_user
        self.query = query
        self.current_page = current_page
        self.total_pages = total_pages
        self.current_index = current_index
        self.is_loading = False
        self.loading_button: Optional[str] = None
        self.message: Optional[discord.Message] = None
        self.last_interaction = asyncio.get_event_loop().time()
        self.inactivity_timeout = 60
        self.inactivity_task: Optional[asyncio.Task] = None
        self.update_buttons()
        self.start_inactivity_timer()

    def start_inactivity_timer(self) -> None:
        """Запускает таймер бездействия."""
        self.inactivity_task = asyncio.create_task(self.check_inactivity())

    async def check_inactivity(self) -> None:
        """Проверяет бездействие с интервалом 5 секунд."""
        try:
            while True:
                elapsed = asyncio.get_event_loop().time() - self.last_interaction
                if elapsed >= self.inactivity_timeout:
                    self.disable_navigation_buttons()
                    if self.message:
                        await self.message.edit(view=self)
                    break
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass

    def disable_navigation_buttons(self) -> None:
        """Отключает навигационные кнопки."""
        for item in self.children:
            if isinstance(item, Button) and item.label in ["⬅️", "➡️", "⌛", "🔢"]:
                item.disabled = True

    def _create_button(self, label: str, style: ButtonStyle, disabled: bool, callback=None, url: Optional[str] = None):
        """Создаёт кнопку с заданными параметрами."""
        button = Button(label=label, style=style, disabled=disabled, url=url)
        if callback:
            button.callback = callback
        return button

    def update_buttons(self) -> None:
        """Обновляет состояние кнопок."""
        self.clear_items()

        back_label = "⌛" if self.is_loading and self.loading_button == "back" else "⬅️"
        self.add_item(self._create_button(
            back_label, ButtonStyle.gray,
            disabled=(self.current_index == 0 and self.current_page == 1) or self.is_loading,
            callback=lambda i: self.navigate(i, -1, "back")
        ))

        video_link = self.results[self.current_index].video_link
        self.add_item(self._create_button(
            "📺 Смотреть онлайн", ButtonStyle.link,
            disabled=not video_link or not self.is_valid_url(video_link),
            url=video_link
        ))

        next_label = "⌛" if self.is_loading and self.loading_button == "next" else "➡️"
        self.add_item(self._create_button(
            next_label, ButtonStyle.gray,
            disabled=self.is_loading or (self.current_page >= self.total_pages and self.current_index >= len(self.results) - 1),
            callback=lambda i: self.navigate(i, 1, "next")
        ))

        self.add_item(self._create_button(
            "🔢", ButtonStyle.gray,
            disabled=self.is_loading,
            callback=self.show_page_select_modal
        ))

    async def show_page_select_modal(self, interaction: Interaction) -> None:
        """Открывает модальное окно для выбора страницы и тайтла."""
        if interaction.user != self.original_user:
            await interaction.response.send_message(
                "Только пользователь, вызвавший команду, может её использовать.",
                ephemeral=True
            )
            return

        modal = PageSelectModal(self)
        try:
            await interaction.response.send_modal(modal)
        except discord.DiscordException as e:
            logger.error(f"Ошибка при открытии модального окна: {e}")
            await interaction.followup.send(
                "Не удалось открыть окно ввода. Попробуйте снова.",
                ephemeral=True
            )

    def is_valid_url(self, url: Optional[str]) -> bool:
        """Проверяет валидность URL с помощью regex."""
        if not url:
            return False
        import re
        return bool(re.match(r'^https?://', url))

    async def load_page(self, target_page: int) -> bool:
        """Загружает результаты для указанной страницы."""
        from .parser import construct_url, fetch_html, parse_search_results
        from bs4 import BeautifulSoup
        try:
            import aiohttp
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def aiohttp_session():
                timeout = aiohttp.ClientTimeout(total=15, connect=5)
                connector = aiohttp.TCPConnector(limit=5, force_close=False)
                async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                    yield session

            async with aiohttp_session() as session:
                url = construct_url(self.query, target_page)
                html = await fetch_html(session, url)
                soup = BeautifulSoup(html, 'html.parser')
                new_results = await parse_search_results(session, soup)
                if not new_results:
                    return False
                self.results = new_results
                self.current_page = target_page
                self.current_index = 0
                return True
        except Exception as e:
            logger.error(f"Ошибка при загрузке страницы {target_page}: {e}")
            return False

    async def navigate(self, interaction: Interaction, direction: int, button: str) -> None:
        """Обрабатывает навигацию по результатам."""
        if interaction.user != self.original_user:
            await interaction.response.send_message(
                "Только пользователь, вызвавший команду, может её использовать.",
                ephemeral=True
            )
            return

        if self.is_loading:
            return

        self.is_loading = True
        self.loading_button = button
        self.last_interaction = asyncio.get_event_loop().time()
        self.update_buttons()

        try:
            await interaction.response.defer()
        except discord.errors.InteractionResponded:
            pass
        except discord.errors.NotFound as e:
            logger.error(f"Взаимодействие не найдено: {e}")
            self.is_loading = False
            self.loading_button = None
            self.update_buttons()
            await interaction.followup.send("Взаимодействие устарело.", ephemeral=True)
            return

        new_index = self.current_index + direction
        if new_index < 0 and self.current_page > 1:
            if await self.load_page(self.current_page - 1):
                self.current_index = len(self.results) - 1
            else:
                self.current_index = 0
        elif new_index >= len(self.results) and self.current_page < self.total_pages:
            if await self.load_page(self.current_page + 1):
                self.current_index = 0
            else:
                self.current_index = len(self.results) - 1
        else:
            self.current_index = max(0, min(new_index, len(self.results) - 1))

        self.is_loading = False
        self.loading_button = None
        self.update_buttons()
        try:
            await interaction.edit_original_response(embed=self.create_embed(), view=self)
        except discord.DiscordException as e:
            logger.error(f"Ошибка при обновлении сообщения: {e}")
            await interaction.followup.send("Не удалось обновить сообщение.", ephemeral=True)

    async def navigate_to_page_and_title(self, interaction: Interaction, page: int, title_index: int) -> None:
        """Переходит к указанной странице и тайтлу."""
        if self.is_loading:
            return

        self.is_loading = True
        self.loading_button = "page_select"
        self.last_interaction = asyncio.get_event_loop().time()
        self.update_buttons()

        try:
            await interaction.response.defer()
        except discord.errors.NotFound as e:
            logger.error(f"Взаимодействие не найдено: {e}")
            self.is_loading = False
            self.loading_button = None
            self.update_buttons()
            await interaction.followup.send("Взаимодействие устарело.", ephemeral=True)
            return
        except discord.errors.InteractionResponded:
            pass

        if page != self.current_page:
            if not await self.load_page(page):
                self.is_loading = False
                self.loading_button = None
                self.update_buttons()
                await interaction.followup.send(
                    f"Не удалось загрузить страницу {page}.",
                    ephemeral=True
                )
                return

        if title_index >= len(self.results):
            self.is_loading = False
            self.loading_button = None
            self.update_buttons()
            await interaction.followup.send(
                f"На странице {page} только {len(self.results)} тайтлов.",
                ephemeral=True
            )
            return

        self.current_index = title_index
        self.is_loading = False
        self.loading_button = None
        self.update_buttons()

        try:
            if self.message:
                await self.message.edit(embed=self.create_embed(), view=self)
        except discord.DiscordException as e:
            logger.error(f"Ошибка при редактировании сообщения: {e}")
            await interaction.followup.send("Не удалось обновить сообщение.", ephemeral=True)

    def create_embed(self) -> Embed:
        """Создаёт Embed для текущего результата."""
        result = self.results[self.current_index]
        description = result.description[:200] + ("..." if len(result.description) > 200 else "")
        if not result.image_url:
            description += "\n⚠️ Изображение отсутствует."

        if result.tags:
            tags_text = f"\n\n🏷 Теги: {', '.join(f'[{tag['name']}]({tag['url']})' for tag in result.tags[:5])}"
            if len(description) + len(tags_text) > 2000:
                description = description[:2000 - len(tags_text) - 3] + "..."
            description += tags_text

        embed = Embed(
            title=f"🎬 {result.title}"[:256],
            url=result.url,
            description=description[:2048],
            color=0xFF5733
        )

        embed.set_thumbnail(url=result.banner_url or "https://via.placeholder.com/100")
        if result.image_url:
            embed.set_image(url=result.image_url)

        for field in result.additional_info[:5]:
            embed.add_field(
                name=f"🔹 {field['name']}"[:256],
                value=field['value'][:512],
                inline=True
            )

        embed.set_footer(text=f"{self.current_index + 1}/{len(self.results)} | Страница {self.current_page}/{self.total_pages}")
        return embed

    async def on_timeout(self) -> None:
        """Обрабатывает таймаут view."""
        self.disable_navigation_buttons()
        if self.inactivity_task:
            self.inactivity_task.cancel()
        try:
            if self.message:
                await self.message.edit(view=self)
        except discord.DiscordException as e:
            logger.error(f"Ошибка при отключении view: {e}")

