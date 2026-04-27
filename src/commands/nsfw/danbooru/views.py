import asyncio
import io
import logging
import math
import time
from typing import Dict, List, Optional, Tuple

import aiohttp
import discord
from discord import ButtonStyle, Interaction
from discord.ui import Button, Modal, TextInput, View

from ....systemLog import logger
from .api import (
    SUPPORTED_MIME_TYPES,
    POSTS_PER_CHUNK,
    POSTS_PER_PAGE,
    REQUEST_TIMEOUT,
    SEMAPHORE_LIMIT,
    file_info_cache,
    used_post_ids,
    aiohttp_session,
    fetch_danbooru_posts,
    filter_duplicates,
)
from .models import DanbooruAPIError, DanbooruPost
from ....core.constants import INACTIVITY_TIMEOUT

active_views: Dict[int, 'NavigationView'] = {}


class PageInputModal(Modal, title="Перейти к странице"):
    """Модальное окно для ввода номера страницы в навигации."""
    def __init__(self, view: 'NavigationView'):
        super().__init__()
        self.view = view
        self.page_input = TextInput(
            label="Номер страницы",
            placeholder="Введите номер страницы (например, 123)",
            required=True,
            min_length=1,
            max_length=10
        )
        self.add_item(self.page_input)

    async def on_submit(self, interaction: Interaction) -> None:
        """Обрабатывает ввод номера страницы."""
        if interaction.user != self.view.original_user:
            await interaction.response.send_message(
                "Только пользователь, вызвавший команду, может с ней взаимодействовать.",
                ephemeral=True
            )
            return

        if self.view.is_loading:
            await interaction.response.send_message("Подождите, идет загрузка.", ephemeral=True)
            return

        try:
            target_page = int(self.page_input.value)
            if target_page < 1 or target_page > self.view.total_pages:
                await interaction.response.send_message(
                    f"Введите число от 1 до {self.view.total_pages}.", ephemeral=True
                )
                return
        except ValueError:
            await interaction.response.send_message("Введите корректное число.", ephemeral=True)
            return

        self.view.is_loading = True
        self.view.loading_button = "page"
        self.view.last_interaction = asyncio.get_event_loop().time()
        self.view.start_inactivity_timer()
        self.view.update_buttons()
        try:
            await interaction.response.edit_message(view=self.view)
        except discord.errors.NotFound as e:
            logging.error(f"Взаимодействие не найдено при обновлении кнопок: {e}")
            self.view.is_loading = False
            self.view.loading_button = None
            self.view.update_buttons()
            await interaction.followup.send("Взаимодействие устарело. Попробуйте снова.", ephemeral=True)
            return

        try:
            await interaction.response.defer()
        except discord.errors.InteractionResponded:
            pass
        except discord.errors.NotFound as e:
            logging.error(f"Взаимодействие не найдено: {e}")
            self.view.is_loading = False
            self.view.loading_button = None
            self.view.update_buttons()
            await interaction.followup.send("Взаимодействие устарело. Попробуйте снова.", ephemeral=True)
            return

        if await self.view.load_page(target_page, reset_chunk=True):
            self.view.is_loading = False
            self.view.loading_button = None
            self.view.update_buttons()
            try:
                content, image_urls, skipped_posts, chunk_posts = await self.view.create_message()
                files = await self.view.fetch_images(image_urls, interaction, skipped_posts, chunk_posts)
                await interaction.edit_original_response(content=content, view=self.view, attachments=files)
            except discord.HTTPException as e:
                logging.error(f"Ошибка HTTP при обновлении сообщения: {e}")
                await interaction.followup.send(
                    "Не удалось обновить сообщение из-за слишком больших файлов. Попробуйте снова.",
                    ephemeral=True
                )
            except discord.DiscordException as e:
                logging.error(f"Ошибка при обновлении сообщения: {e}")
                await interaction.followup.send(
                    "Не удалось обновить сообщение. Попробуйте снова.",
                    ephemeral=True
                )
        else:
            self.view.is_loading = False
            self.view.loading_button = None
            self.view.update_buttons()
            await interaction.followup.send("Не удалось загрузить страницу. Попробуйте снова.", ephemeral=True)


class NavigationView(View):
    """View для навигации по чанкам и страницам Danbooru."""
    def __init__(
        self,
        results: List[DanbooruPost],
        original_user: discord.User,
        query: Optional[str],
        current_page: int,
        total_pages: int,
        max_file_size: int,
        current_chunk: int = 0,
        timeout: int = 3600
    ):
        super().__init__(timeout=timeout)
        self.results = results
        self.original_user = original_user
        self.query = query
        self.current_page = current_page
        self.total_pages = total_pages
        self.max_file_size = max_file_size
        self.current_chunk = current_chunk
        self.is_loading = False
        self.loading_button: Optional[str] = None
        self.page_cache: Dict[int, List[DanbooruPost]] = {}
        self.last_interaction = asyncio.get_event_loop().time()
        self.inactivity_timeout = INACTIVITY_TIMEOUT
        self.inactivity_task: Optional[asyncio.Task] = None
        self.message: Optional[discord.Message] = None
        self.update_buttons()
        self.start_inactivity_timer()
        active_views[original_user.id] = self
        logging.debug(f"Представление зарегистрировано для пользователя {original_user.id}")

    def start_inactivity_timer(self) -> None:
        """Запускает таймер бездействия для отключения кнопок."""
        if self.inactivity_task:
            self.inactivity_task.cancel()
        self.inactivity_task = asyncio.create_task(self.check_inactivity())
        logging.debug(f"Таймер бездействия запущен с таймаутом {self.inactivity_timeout} секунд")

    async def check_inactivity(self) -> None:
        """Отключает кнопки после 1 часа бездействия."""
        try:
            await asyncio.sleep(self.inactivity_timeout)
            current_time = time.time()
            if (current_time - self.last_interaction) >= self.inactivity_timeout:
                logging.debug("Таймер бездействия сработал: отключаем кнопки")
                self.disable_navigation_buttons()
                if self.message:
                    try:
                        await self.message.edit(view=self)
                        logging.debug("Кнопки успешно отключены в сообщении")
                    except discord.HTTPException as e:
                        logging.error(f"Ошибка при отключении кнопок в сообщении: {e}")
            else:
                logging.debug("Активность обнаружена, перезапуск таймера бездействия")
                self.start_inactivity_timer()
        except asyncio.CancelledError:
            logging.debug("Таймер бездействия отменён")
        except Exception as e:
            logging.error(f"Ошибка в таймере бездействия: {e}")

    def disable_navigation_buttons(self) -> None:
        """Отключает все кнопки навигации."""
        for item in self.children:
            if isinstance(item, Button):
                item.disabled = True
        logging.debug("Все кнопки навигации отключены")

    def _create_button(self, label: str, style: ButtonStyle, disabled: bool, callback=None, url: Optional[str] = None):
        """Создает кнопку с заданными параметрами."""
        button = Button(label=label, style=style, disabled=disabled, url=url)
        if callback:
            button.callback = callback
        return button

    def update_buttons(self) -> None:
        """Обновляет состояние кнопок навигации."""
        self.clear_items()
        max_chunks = math.ceil(len(self.results) / POSTS_PER_CHUNK) if self.results else 1
        if self.is_loading:
            back_label = "⌛" if self.loading_button == "back" else "⬅️"
            self.add_item(self._create_button(
                label=back_label,
                style=ButtonStyle.gray,
                disabled=True,
                callback=lambda i: self.navigate(i, -1, "back")
            ))
            next_label = "⌛" if self.loading_button == "next" else "➡️"
            self.add_item(self._create_button(
                label=next_label,
                style=ButtonStyle.gray,
                disabled=True,
                callback=lambda i: self.navigate(i, 1, "next")
            ))
            page_label = "⌛" if self.loading_button == "page" else "📄"
            self.add_item(self._create_button(
                label=page_label,
                style=ButtonStyle.blurple,
                disabled=True,
                callback=self.open_page_modal
            ))
        else:
            self.add_item(self._create_button(
                label="⬅️",
                style=ButtonStyle.gray,
                disabled=(self.current_chunk == 0 and self.current_page == 1),
                callback=lambda i: self.navigate(i, -1, "back")
            ))
            self.add_item(self._create_button(
                label="➡️",
                style=ButtonStyle.gray,
                disabled=(self.current_chunk >= max_chunks - 1 and self.current_page >= self.total_pages),
                callback=lambda i: self.navigate(i, 1, "next")
            ))
            self.add_item(self._create_button(
                label="📄",
                style=ButtonStyle.blurple,
                disabled=False,
                callback=self.open_page_modal
            ))

    async def open_page_modal(self, interaction: Interaction) -> None:
        """Открывает модальное окно для ввода номера страницы."""
        if interaction.user != self.original_user:
            await interaction.response.send_message(
                "Только пользователь, вызвавший команду, может с ней взаимодействовать.",
                ephemeral=True
            )
            return
        if self.is_loading:
            await interaction.response.send_message("Подождите, идет загрузка.", ephemeral=True)
            return
        self.last_interaction = asyncio.get_event_loop().time()
        self.start_inactivity_timer()
        modal = PageInputModal(self)
        await interaction.response.send_modal(modal)

    async def navigate(self, interaction: Interaction, direction: int, button: str) -> None:
        """Обрабатывает навигацию по чанкам и страницам."""
        if interaction.user != self.original_user:
            await interaction.response.send_message(
                "Только пользователь, вызвавший команду, может с ней взаимодействовать.",
                ephemeral=True
            )
            return
        if self.is_loading:
            return
        self.is_loading = True
        self.loading_button = button
        self.last_interaction = asyncio.get_event_loop().time()
        self.start_inactivity_timer()
        self.update_buttons()
        try:
            await interaction.response.edit_message(view=self)
        except discord.errors.NotFound as e:
            logging.error(f"Взаимодействие не найдено при обновлении кнопок: {e}")
            self.is_loading = False
            self.loading_button = None
            self.update_buttons()
            await interaction.followup.send("Взаимодействие устарело. Попробуйте снова.", ephemeral=True)
            return

        try:
            await interaction.response.defer()
        except discord.errors.InteractionResponded:
            pass
        except discord.errors.NotFound as e:
            logging.error(f"Взаимодействие не найдено: {e}")
            self.is_loading = False
            self.loading_button = None
            self.update_buttons()
            await interaction.followup.send("Взаимодействие устарело. Попробуйте снова.", ephemeral=True)
            return

        max_chunks = math.ceil(len(self.results) / POSTS_PER_CHUNK) if self.results else 1
        new_chunk = self.current_chunk + direction
        success = True
        if new_chunk < 0 and self.current_page > 1:
            success = await self.load_page(self.current_page - 1, reset_chunk=False)
            if success:
                self.current_chunk = math.ceil(len(self.results) / POSTS_PER_CHUNK) - 1 if self.results else 0
            else:
                self.current_chunk = 0
        elif new_chunk >= max_chunks and self.current_page < self.total_pages:
            success = await self.load_page(self.current_page + 1, reset_chunk=True)
            if success:
                self.current_chunk = 0
            else:
                self.current_chunk = max_chunks - 1
        else:
            self.current_chunk = max(0, min(new_chunk, max_chunks - 1))

        self.is_loading = False
        self.loading_button = None
        self.update_buttons()
        try:
            content, image_urls, skipped_posts, chunk_posts = await self.create_message()
            files = await self.fetch_images(image_urls, interaction, skipped_posts, chunk_posts)
            await interaction.edit_original_response(content=content, view=self, attachments=files)
        except discord.HTTPException as e:
            logging.error(f"Ошибка HTTP при обновлении сообщения: {e}")
            await interaction.followup.send(
                "Не удалось обновить сообщение из-за слишком больших файлов. Попробуйте снова.",
                ephemeral=True
            )
        except discord.DiscordException as e:
            logging.error(f"Ошибка при обновлении сообщения: {e}")
            await interaction.followup.send(
                "Не удалось обновить сообщение. Попробуйте снова.",
                ephemeral=True
            )

    async def fetch_images(
        self,
        image_urls: List[str],
        interaction: Interaction,
        skipped_posts: List[DanbooruPost],
        chunk_posts: List[DanbooruPost]
    ) -> List[discord.File]:
        """Загружает файлы по URL с проверкой размера и MIME-типа."""
        files = []
        new_skipped_posts = skipped_posts.copy()
        semaphore = asyncio.Semaphore(SEMAPHORE_LIMIT)

        async def fetch_single_image(idx: int, url: str) -> Tuple[Optional[discord.File], Optional[DanbooruPost]]:
            async with semaphore:
                if idx >= len(chunk_posts):
                    logging.error(f"Индекс {idx} выходит за пределы chunk_posts (длина: {len(chunk_posts)})")
                    return None, None

                post = chunk_posts[idx]
                try:
                    content_type, file_size = file_info_cache.get(url, ('unknown', None))
                    if content_type not in SUPPORTED_MIME_TYPES or (file_size is not None and file_size > self.max_file_size * 0.9):
                        logging.debug(f"Пропущен файл {url} из кэша: MIME={content_type}, размер={file_size}")
                        return None, post

                    start_time = asyncio.get_event_loop().time()
                    async with aiohttp_session() as session:
                        async with session.get(url, timeout=REQUEST_TIMEOUT) as response:
                            if response.status != 200:
                                logging.error(f"Ошибка загрузки файла {url}: HTTP {response.status} (elapsed: {asyncio.get_event_loop().time() - start_time:.1f}s)")
                                return None, post

                            content_type = response.headers.get('Content-Type', 'application/octet-stream')
                            if content_type not in SUPPORTED_MIME_TYPES:
                                logging.debug(f"Пропущен файл {url} (MIME: {content_type})")
                                return None, post

                            image_data = bytearray()
                            async for chunk in response.content.iter_chunked(8192):
                                image_data.extend(chunk)
                                if len(image_data) > self.max_file_size:
                                    logging.warning(f"Файл {url} превысил лимит {self.max_file_size} байт (actual: {len(image_data)}, elapsed: {asyncio.get_event_loop().time() - start_time:.1f}s)")
                                    return None, post

                            extension = SUPPORTED_MIME_TYPES[content_type]
                            file = discord.File(
                                fp=io.BytesIO(image_data),
                                filename=f"post_{post.id}{extension}"
                            )
                            elapsed = asyncio.get_event_loop().time() - start_time
                            logging.debug(f"Успешно загружен {url} ({len(image_data)} байт, {elapsed:.1f}s)")
                            return file, None
                except asyncio.TimeoutError:
                    logging.error(f"Таймаут загрузки {url} после {REQUEST_TIMEOUT}s")
                    return None, post
                except aiohttp.ClientError as e:
                    if "cdn.donmai.us" in url:
                        logging.warning(f"CDN ошибка для {url}: {type(e).__name__} - пропуск (возможно throttling)")
                    else:
                        logging.error(f"ClientError для {url}: {e}")
                    return None, post
                except Exception as e:
                    logging.error(f"Неожиданная ошибка для {url}: {e}")
                    return None, post

        tasks = [fetch_single_image(idx, url) for idx, url in enumerate(image_urls)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logging.error(f"Ошибка в задаче загрузки изображения: {result}")
                continue
            try:
                file, skipped_post = result
                if file:
                    files.append(file)
                elif skipped_post:
                    new_skipped_posts.append(skipped_post)
            except ValueError as e:
                logging.error(f"Некорректный результат задачи загрузки: {result}, ошибка: {e}")
                continue

        return files

    async def fill_chunk(self, chunk_posts: List[DanbooruPost]) -> List[DanbooruPost]:
        """Дозаполняет чанк до 10 постов, загружая посты с последующих страниц."""
        needed_posts = POSTS_PER_CHUNK - len(chunk_posts)
        if needed_posts <= 0:
            return chunk_posts

        current_page = self.current_page
        new_posts = []
        while needed_posts > 0 and current_page < self.total_pages:
            current_page += 1
            if current_page in self.page_cache:
                page_posts = self.page_cache[current_page]
            else:
                try:
                    async with aiohttp_session() as session:
                        page_posts = await fetch_danbooru_posts(session, self.query, current_page)
                        if not page_posts:
                            break
                        page_posts = filter_duplicates(page_posts)
                        self.page_cache[current_page] = page_posts
                except DanbooruAPIError as e:
                    logging.error(f"Ошибка дозаполнения чанка со страницы {current_page}: {e}")
                    break

            for post in page_posts:
                if post.id not in used_post_ids:
                    new_posts.append(post)
                    used_post_ids.add(post.id)
                    needed_posts -= 1
                    if needed_posts <= 0:
                        break

        logging.debug(f"Дозаполнено {len(new_posts)} постов для чанка, страница {self.current_page}, чанк {self.current_chunk + 1}")
        return chunk_posts + new_posts[:POSTS_PER_CHUNK - len(chunk_posts)]

    async def load_page(self, target_page: int, reset_chunk: bool = False) -> bool:
        """Загружает результаты для указанной страницы с использованием кэша."""
        if target_page in self.page_cache:
            self.results = self.page_cache[target_page]
            self.current_page = target_page
            if reset_chunk:
                self.current_chunk = 0
            return True

        try:
            async with aiohttp_session() as session:
                posts = await fetch_danbooru_posts(session, self.query, target_page)
                if not posts:
                    return False
                posts = filter_duplicates(posts)
                self.results = posts
                self.page_cache[target_page] = posts
                self.current_page = target_page
                if reset_chunk:
                    self.current_chunk = 0
                return True
        except DanbooruAPIError as e:
            logging.error(f"Ошибка загрузки страницы {target_page}: {e}")
            return False

    async def create_message(self) -> Tuple[str, List[str], List[DanbooruPost], List[DanbooruPost]]:
        """Создает сообщение, список URL файлов и список постов для текущего чанка."""
        start_idx = self.current_chunk * POSTS_PER_CHUNK
        end_idx = min(start_idx + POSTS_PER_CHUNK, len(self.results))
        chunk_posts = self.results[start_idx:end_idx]

        chunk_posts = await self.fill_chunk(chunk_posts)
        if len(chunk_posts) > POSTS_PER_CHUNK:
            chunk_posts = chunk_posts[:POSTS_PER_CHUNK]

        max_chunks = math.ceil(len(self.results) / POSTS_PER_CHUNK) if self.results else 1
        if len(chunk_posts) < POSTS_PER_CHUNK and self.current_page >= self.total_pages:
            max_chunks = self.current_chunk + 1

        message_lines = [f"**Чанк {self.current_chunk + 1}/{max_chunks} | Страница {self.current_page}/{self.total_pages}**"]
        image_urls = []
        skipped_posts = []

        for post in chunk_posts:
            used_post_ids.add(post.id)
            if not post.file_url:
                skipped_posts.append(post)
                continue
            content_type, file_size = file_info_cache.get(post.file_url, ('unknown', None))
            if content_type not in SUPPORTED_MIME_TYPES or (file_size is not None and file_size > self.max_file_size):
                skipped_posts.append(post)
            else:
                image_urls.append(post.file_url)

        if skipped_posts:
            skipped_message = "Файл слишком велик или формат не поддерживается: " + ", ".join(
                [f"[Пост #{post.id}]({post.file_url})" for post in skipped_posts if post.file_url]
            )
            message_lines.append(skipped_message)

        message = "\n".join(message_lines)
        logging.debug(f"Сформирован чанк {self.current_chunk + 1}/{max_chunks}: {len(image_urls)} файлов, {len(skipped_posts)} пропущено")
        return message, image_urls, skipped_posts, chunk_posts

    async def on_timeout(self) -> None:
        """Обрабатывает таймаут view, очищая кэш."""
        self.clear_items()
        self.page_cache.clear()
        if self.inactivity_task:
            self.inactivity_task.cancel()
        if self.message:
            try:
                await self.message.edit(view=None)
                logging.debug("Кнопки удалены из сообщения по таймауту")
            except discord.HTTPException as e:
                if e.code == 50027:
                    logging.debug("Не удалось отредактировать сообщение: недействительный токен вебхука")
                else:
                    logging.error(f"Ошибка при удалении кнопок по таймауту: {e}")

        if self.original_user.id in active_views:
            del active_views[self.original_user.id]
            logging.debug(f"Представление удалено из active_views для пользователя {self.original_user.id}")

        from .api import tag_suggestions_cache as api_tag_cache
        file_info_cache.clear()
        api_tag_cache.clear()
        used_post_ids.clear()
        logging.debug("Кэши очищены по таймауту view")
