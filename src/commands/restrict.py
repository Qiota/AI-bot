import discord
import asyncio
from discord import app_commands
from discord.ui import Select, View, Button, Modal, TextInput
from decouple import config
from ..systemLog import logger
from ..utils.firebase.firebase_manager import FirebaseManager

DEVELOPER_ID = config("DEVELOPER_ID", cast=int)

description = "Настройка бота: каналы (whitelist), пользователи (blacklist). Только для админов."

active_views = {}

class BaseView(View):
    def __init__(self, guild_id: int, user_id: int, timeout: int = 300):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.user_id = user_id
        self.messages = []

    async def on_timeout(self):
        if self.user_id in active_views:
            del active_views[self.user_id]
        logger.info(f"Меню {self.__class__.__name__} истекло для пользователя {self.user_id}")

    async def restrict_interaction(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            if not interaction.response.is_done():
                await interaction.response.send_message("Это меню только для вас!", ephemeral=True)
            return True
        return False

class SearchModal(Modal, title="Поиск пользователей"):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.query = TextInput(
            label="Имена пользователей (через запятую)",
            placeholder="Пример: John, Alex",
            custom_id="search_query",
            min_length=1,
            max_length=100
        )
        self.add_item(self.query)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        queries = [q.strip().lower() for q in self.query.value.split(",")]
        self.view.search_queries = queries
        self.view.current_page = 0
        await self.view.update_view(interaction)

class SelectView(BaseView):
    ACTION_CONFIG = {
        "bot_access": {
            "title": "Настройка каналов 📡",
            "description": (
                "Выберите каналы, где бот будет отвечать.\n"
                "• Используйте ⬅️ ➡️ для переключения страниц.\n"
                "• Нажмите ✅, чтобы сохранить."
            ),
            "placeholder": "Выберите каналы"
        },
        "restrict_users": {
            "title": "Ограничение пользователей 🚫",
            "description": (
                "Выберите пользователей для запрета доступа к боту.\n"
                "• Используйте 🔍 для поиска по имени.\n"
                "• Нажмите ✅, чтобы добавить в blacklist."
            ),
            "placeholder": "Выберите пользователей"
        },
        "unrestrict_users": {
            "title": "Снятие ограничений ✅",
            "description": (
                "Выберите пользователей для восстановления доступа.\n"
                "• Используйте 🔍 для поиска по имени.\n"
                "• Нажмите ✅, чтобы убрать из blacklist."
            ),
            "placeholder": "Выберите пользователей"
        }
    }

    def __init__(self, guild: discord.Guild, user_id: int, guild_id: int, action: str, main_view, bot_client):
        super().__init__(guild_id, user_id)
        self.guild = guild
        self.action = action
        self.main_view = main_view
        self.bot_client = bot_client
        self.selected_values = []
        self.current_page = 0
        self.items_per_page = 25
        self.search_queries = None
        self.all_items = []
        self.config = None

    async def initialize(self):
        """Асинхронная инициализация SelectView."""
        await self.setup_items()
        if not self.is_finished():
            self.setup_select()

    async def load_config(self):
        """Загрузка конфигурации гильдии асинхронно."""
        try:
            firebase_manager = await self.bot_client._ensure_firebase_initialized()
            config = await firebase_manager.load_guild_config(str(self.guild_id))
            if config is None:
                logger.warning(f"Конфигурация для гильдии {self.guild_id} не найдена")
                return {}
            return config
        except Exception as e:
            logger.error(f"Ошибка загрузки конфигурации гильдии {self.guild_id}: {e}")
            return {}

    async def setup_items(self):
        """Асинхронная настройка элементов для выбора."""
        self.config = await self.load_config()
        if self.action == "bot_access":
            self.all_items = [
                {"label": channel.name, "value": str(channel.id), "default": str(channel.id) in self.config.get("bot_allowed_channels", [])}
                for channel in self.guild.text_channels
            ]
            self.selected_values = self.config.get("bot_allowed_channels", [])
        else:
            current_users = self.config.get("restricted_users", [])
            members = [
                member for member in self.guild.members
                if not member.bot and (self.action != "unrestrict_users" or str(member.id) in current_users)
            ]
            self.all_items = [
                {"label": member.display_name, "value": str(member.id), "default": str(member.id) in current_users, "true_name": member.name}
                for member in members
            ]
            self.selected_values = current_users

        if not self.all_items and self.action == "unrestrict_users":
            logger.info(f"Blacklist пуст для гильдии {self.guild_id}, завершение SelectView")
            self.stop()

    def get_paginated_items(self):
        """Получение элементов для текущей страницы."""
        items = self.all_items
        if self.search_queries and self.action != "bot_access":
            items = [
                item for item in items
                if any(query in item["label"].lower() or query in item["true_name"].lower() for query in self.search_queries)
            ]
        start = self.current_page * self.items_per_page
        return items[start:start + self.items_per_page]

    def setup_select(self):
        """Настройка выпадающего меню и кнопок."""
        self.clear_items()
        paginated_items = self.get_paginated_items()
        if not paginated_items:
            return

        total_pages = max(1, (len(self.all_items) + self.items_per_page - 1) // self.items_per_page)
        select = Select(
            custom_id=f"{self.action}_select",
            placeholder=f"{self.ACTION_CONFIG[self.action]['placeholder']} ({self.current_page + 1}/{total_pages})",
            options=[
                discord.SelectOption(
                    label=item["label"][:100],
                    value=item["value"],
                    default=item["value"] in self.selected_values
                )
                for item in paginated_items
            ],
            min_values=0,
            max_values=len(paginated_items)
        )
        select.callback = self.select_callback
        self.add_item(select)

        prev_btn = Button(label="⬅️", style=discord.ButtonStyle.grey, custom_id=f"{self.action}_prev", disabled=self.current_page == 0)
        next_btn = Button(label="➡️", style=discord.ButtonStyle.grey, custom_id=f"{self.action}_next", disabled=self.current_page >= total_pages - 1)
        prev_btn.callback = self.prev_page
        next_btn.callback = self.next_page
        self.add_item(prev_btn)
        self.add_item(next_btn)

        if self.action != "bot_access":
            search_btn = Button(label="Поиск 🔍", style=discord.ButtonStyle.grey, custom_id=f"{self.action}_search")
            search_btn.callback = self.search_callback
            self.add_item(search_btn)

        confirm_btn = Button(label="Принять ✅", style=discord.ButtonStyle.green, custom_id=f"{self.action}_confirm")
        confirm_btn.callback = self.confirm_callback
        self.add_item(confirm_btn)

    async def update_view(self, interaction: discord.Interaction):
        """Обновление представления с текущими выбранными элементами."""
        self.setup_select()
        if not self.children:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="Нет данных",
                    description="Нет каналов или пользователей для настройки.",
                    color=discord.Color.red()
                ),
                ephemeral=True
            )
            await self.return_to_main_menu(interaction)
            return

        selected_text = (
            "\n".join(f"- {item['label']}" for item in self.all_items if item["value"] in self.selected_values)
            if self.action == "bot_access"
            else "\n".join(f"<@{value}>" for value in self.selected_values)
        ) or "- Ничего не выбрано"
        embed = discord.Embed(
            title=self.ACTION_CONFIG[self.action]["title"],
            description=(
                self.ACTION_CONFIG[self.action]["description"]
                + f"\n\n**Выбрано ({len(self.selected_values)}):**"
                + f"\n{selected_text[:1000]}"
                + (f"\n\n**На странице ({len(self.get_paginated_items())}):**"
                   + f"\n{'\n'.join(f'<@{item['value']}>' for item in self.get_paginated_items()) or '- Пусто'}"
                   if self.search_queries and self.action != "bot_access" else "")
            ),
            color=discord.Color.blue()
        )
        try:
            await interaction.edit_original_response(embed=embed, view=self)
        except discord.errors.HTTPException as e:
            logger.error(f"Ошибка обновления меню для сервера {self.guild_id}: {e}")
            await interaction.followup.send("Ошибка отображения меню. Попробуйте снова.", ephemeral=True)

    async def select_callback(self, interaction: discord.Interaction):
        if await self.restrict_interaction(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        paginated_items = self.get_paginated_items()
        current_page_values = {item["value"] for item in paginated_items}
        preserved_values = [value for value in self.selected_values if value not in current_page_values]
        new_values = self.children[0].values
        self.selected_values = list(set(preserved_values + new_values))
        await self.update_view(interaction)

    async def prev_page(self, interaction: discord.Interaction):
        if await self.restrict_interaction(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        if self.current_page > 0:
            self.current_page -= 1
        await self.update_view(interaction)

    async def next_page(self, interaction: discord.Interaction):
        if await self.restrict_interaction(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        total_pages = (len(self.all_items) + self.items_per_page - 1) // self.items_per_page
        if self.current_page < total_pages - 1:
            self.current_page += 1
        await self.update_view(interaction)

    async def search_callback(self, interaction: discord.Interaction):
        if await self.restrict_interaction(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        modal = SearchModal(self)
        await interaction.followup.send_modal(modal)

    async def confirm_callback(self, interaction: discord.Interaction):
        if await self.restrict_interaction(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        self.children[-1].label = "⏳ Сохранение..."
        self.children[-1].style = discord.ButtonStyle.blurple
        self.children[-1].disabled = True
        await interaction.edit_original_response(view=self)

        try:
            if self.guild.id not in [guild.id for guild in self.bot_client.bot.guilds]:
                logger.warning(f"Бот отсутствует на сервере {self.guild.id}, сохранение отменено")
                await interaction.followup.send("❌ Бот отсутствует на сервере, настройки не сохранены.", ephemeral=True)
                await self.return_to_main_menu(interaction)
                return

            update_data = {}
            if self.action == "bot_access":
                update_data["bot_allowed_channels"] = self.selected_values
                action_log = f"Каналы очищены" if not self.selected_values else f"Каналов: {len(self.selected_values)}"
            elif self.action == "restrict_users":
                update_data["restricted_users"] = list(set(self.config.get("restricted_users", []) + self.selected_values))
                action_log = f"Ограничено: {len(self.selected_values)} пользователей"
            elif self.action == "unrestrict_users":
                update_data["restricted_users"] = [
                    uid for uid in self.config.get("restricted_users", []) if uid not in self.selected_values
                ]
                action_log = f"Снято ограничений: {len(self.selected_values)}"

            firebase_manager = await self.bot_client._ensure_firebase_initialized()
            await firebase_manager.update_guild_fields(str(self.guild.id), update_data)
            logger.info(f"Настройки сервера {self.guild.id}: {action_log}")
            await interaction.followup.send("✅ Настройки сохранены!", ephemeral=True)
        except Exception as e:
            logger.error(f"Ошибка сохранения настроек сервера {self.guild.id}: {e}")
            await interaction.followup.send("❌ Ошибка сохранения. Попробуйте снова.", ephemeral=True)

        try:
            await interaction.delete_original_response()
        except (discord.errors.NotFound, discord.errors.Forbidden):
            pass
        await self.return_to_main_menu(interaction)

    async def return_to_main_menu(self, interaction: discord.Interaction):
        if self.user_id not in active_views:
            return
        main_view = active_views[self.user_id]
        embed = discord.Embed(
            title="Настройка доступа бота 🛠️",
            description=(
                "Выберите действие для настройки бота:\n"
                "• **Каналы**: Укажите текстовые каналы, где бот будет работать.\n"
                "• **Ограничить пользователей**: Запретите доступ к боту.\n"
                "• **Снять ограничения**: Верните доступ пользователям.\n"
                "\nНажмите кнопку, чтобы начать."
            ),
            color=discord.Color.blue()
        )
        try:
            await main_view.message.edit(embed=embed, view=main_view)
        except (discord.errors.NotFound, discord.errors.Forbidden):
            new_main_view = ActionSelectView(self.user_id, self.guild_id, self.bot_client)
            msg = await interaction.followup.send(embed=embed, view=new_main_view, ephemeral=True)
            new_main_view.message = msg
            active_views[self.user_id] = new_main_view

class ActionSelectView(BaseView):
    ACTION_CONFIG = {
        "bot_access": {"label": "Каналы 📡", "description": "Настройте каналы для бота."},
        "restrict_users": {"label": "Ограничить 🚫", "description": "Запретите доступ пользователям."},
        "unrestrict_users": {"label": "Снять ограничения ✅", "description": "Верните доступ пользователям."}
    }

    def __init__(self, user_id: int, guild_id: int, bot_client):
        super().__init__(guild_id, user_id)
        self.bot_client = bot_client
        self.setup_buttons()

    def setup_buttons(self):
        for action, config in self.ACTION_CONFIG.items():
            button = Button(
                label=config["label"],
                style=discord.ButtonStyle.primary,
                custom_id=f"{action}_btn"
            )
            button.callback = lambda i, a=action: self.handle_action(i, a)
            self.add_item(button)

    async def handle_action(self, interaction: discord.Interaction, action: str):
        if await self.restrict_interaction(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        view = SelectView(interaction.guild, self.user_id, self.guild_id, action, self, self.bot_client)
        await view.initialize()
        if view.is_finished():
            await interaction.followup.send(
                embed=discord.Embed(
                    title="Нет данных",
                    description="Blacklist пуст." if action == "unrestrict_users" else "Нет пользователей или каналов.",
                    color=discord.Color.red()
                ),
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title=SelectView.ACTION_CONFIG[action]["title"],
            description=SelectView.ACTION_CONFIG[action]["description"],
            color=discord.Color.blue()
        )
        msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        view.messages.append(msg)

async def notify_restricted_channel(message: discord.Message, reason: str = "бот не работает в этом канале"):
    try:
        msg = await message.channel.send(
            f"{message.author.mention}, {reason}. Используйте /restrict.",
            reference=message
        )
        await asyncio.sleep(5)
        try:
            await msg.delete()
        except (discord.errors.NotFound, discord.errors.Forbidden):
            pass
    except discord.errors.Forbidden:
        logger.warning(f"Нет прав для отправки в канал {message.channel.id}")

async def check_channels_setup(obj):
    """Проверяет, настроены ли каналы в Firebase для гильдии."""
    try:
        if isinstance(obj, discord.Message) and isinstance(obj.channel, discord.DMChannel):
            logger.debug("DM-канал, проверки каналов не требуются")
            return True, None
        if isinstance(obj, discord.Interaction) and obj.guild is None:
            logger.debug("Интеракция в DM, проверки каналов не требуются")
            return True, None
        if isinstance(obj, discord.Interaction) and obj.command.name == "restrict":
            logger.debug("Команда /restrict, пропуск проверки каналов")
            return True, None

        firebase_manager = await FirebaseManager.initialize()
        config = await firebase_manager.load_guild_config(str(obj.guild.id))
        if config is None:
            logger.warning(f"Конфигурация для гильдии {obj.guild.id} не найдена в Firebase")
            return False, "конфигурация сервера не найдена"

        allowed_channels = config.get("bot_allowed_channels", [])
        if not allowed_channels:
            logger.debug(f"Каналы не настроены для гильдии {obj.guild.id}")
            return False, "каналы не настроены"

        return True, None
    except Exception as e:
        logger.error(f"Ошибка в check_channels_setup для гильдии {obj.guild.id if obj.guild else 'DM'}: {e}")
        return False, "ошибка при проверке конфигурации"

async def check_bot_access(obj):
    """Проверяет, имеет ли бот доступ к каналу на основе Firebase."""
    try:
        if isinstance(obj, discord.Message) and isinstance(obj.channel, discord.DMChannel):
            logger.debug("DM-канал, доступ разрешён")
            return True, None
        if isinstance(obj, discord.Interaction) and obj.guild is None:
            logger.debug("Интеракция в DM, доступ разрешён")
            return True, None

        result, reason = await check_channels_setup(obj)
        if not result:
            logger.debug(f"check_channels_setup вернул False: {reason}")
            return False, reason

        firebase_manager = await FirebaseManager.initialize()
        config = await firebase_manager.load_guild_config(str(obj.guild.id))
        if config is None:
            logger.warning(f"Конфигурация для гильдии {obj.guild.id} не найдена в Firebase")
            return False, "конфигурация сервера не найдена"

        allowed_channels = config.get("bot_allowed_channels", [])
        channel_id = str(obj.channel_id if isinstance(obj, discord.Interaction) else obj.channel.id)
        if channel_id not in allowed_channels:
            logger.debug(f"Канал {channel_id} не в списке разрешённых для гильдии {obj.guild.id}")
            return False, "бот не работает в этом канале"

        logger.debug(f"Доступ к каналу {channel_id} разрешён для гильдии {obj.guild.id}")
        return True, None
    except Exception as e:
        logger.error(f"Ошибка в check_bot_access для гильдии {obj.guild.id if obj.guild else 'DM'}: {e}")
        return False, "ошибка при проверке доступа"

async def check_user_restriction(obj):
    """Проверяет, ограничен ли пользователь в Firebase."""
    try:
        if isinstance(obj, discord.Interaction) and obj.command.name == "restrict":
            logger.debug("Команда /restrict, пропуск проверки ограничений пользователя")
            return True, None

        guild_id = str(obj.guild.id) if obj.guild else "DM"
        firebase_manager = await FirebaseManager.initialize()
        config = await firebase_manager.load_guild_config(guild_id)
        if config is None and obj.guild:
            logger.warning(f"Конфигурация для гильдии {guild_id} не найдена в Firebase")
            return False, "конфигурация сервера не найдена"

        restricted_users = config.get("restricted_users", []) if obj.guild else []
        user_id = str(obj.user.id if isinstance(obj, discord.Interaction) else obj.author.id)
        if user_id in restricted_users:
            logger.debug(f"Пользователь {user_id} ограничен в гильдии {guild_id}")
            return False, "ваш доступ ограничен"

        logger.debug(f"Пользователь {user_id} не ограничен в гильдии {guild_id}")
        return True, None
    except Exception as e:
        logger.error(f"Ошибка в check_user_restriction для гильдии {guild_id}: {e}")
        return False, "ошибка при проверке ограничений"

async def restrict_command_execution(obj, bot_client):
    """Проверяет, может ли бот выполнить команду на сервере, читая Firebase."""
    try:
        if isinstance(obj, discord.Interaction) and obj.guild:
            if not bot_client.bot.is_ready():
                logger.debug("Бот не готов")
                if not obj.response.is_done():
                    await obj.response.send_message("Бот еще не готов. Пожалуйста, попробуйте позже.", ephemeral=True)
                return False

            firebase_manager = await FirebaseManager.initialize()
            config = await firebase_manager.load_guild_config(str(obj.guild.id))
            if config is None:
                logger.warning(f"Конфигурация для гильдии {obj.guild.id} не найдена в Firebase")
                if not obj.response.is_done():
                    await obj.response.send_message("Конфигурация сервера не найдена!", ephemeral=True)
                return False

            guild_ids = [guild.id for guild in bot_client.bot.guilds]
            if obj.guild.id not in guild_ids:
                logger.warning(f"Гильдия {obj.guild.id} не найдена в списке гильдий бота")
                if not obj.response.is_done():
                    await obj.response.send_message("Бот отсутствует на этом сервере!", ephemeral=True)
                return False

        logger.debug(f"restrict_command_execution разрешил выполнение для гильдии {obj.guild.id if obj.guild else 'DM'}")
        return True
    except Exception as e:
        logger.error(f"Ошибка в restrict_command_execution: {e}")
        if not obj.response.is_done() and isinstance(obj, discord.Interaction):
            await obj.response.send_message("Произошла ошибка при проверке доступа.", ephemeral=True)
        return False

async def handle_mention(message: discord.Message, bot_client):
    """Обрабатывает упоминания бота, проверяя доступ через Firebase."""
    try:
        if isinstance(message.channel, discord.DMChannel):
            logger.debug("Упоминание в DM, доступ разрешён")
            return True
        if bot_client.bot.user not in message.mentions:
            logger.debug("Бот не упомянут в сообщении")
            return False

        firebase_manager = await bot_client._ensure_firebase_initialized()
        config = await firebase_manager.load_guild_config(str(message.guild.id))
        if config is None:
            logger.warning(f"Конфигурация для гильдии {message.guild.id} не найдена в Firebase")
            await notify_restricted_channel(message, "конфигурация сервера не найдена")
            return False

        allowed_channels = config.get("bot_allowed_channels", [])
        channel_id = str(message.channel.id)
        if not allowed_channels:
            logger.debug(f"Каналы не настроены для гильдии {message.guild.id}")
            await notify_restricted_channel(message, "каналы не настроены")
            return False
        if channel_id not in allowed_channels:
            logger.debug(f"Канал {channel_id} не разрешён для гильдии {message.guild.id}")
            await notify_restricted_channel(message)
            return False

        logger.debug(f"Упоминание в разрешённом канале {channel_id} для гильдии {message.guild.id}")
        return True
    except Exception as e:
        logger.error(f"Ошибка в handle_mention для гильдии {message.guild.id}: {e}")
        await notify_restricted_channel(message, "ошибка при проверке доступа")
        return False

async def restrict(interaction: discord.Interaction, bot_client):
    """Команда /restrict: Настройка доступа бота."""
    if not await restrict_command_execution(interaction, bot_client):
        return

    if not interaction.guild:
        if not interaction.response.is_done():
            await interaction.response.send_message("Команда только для серверов!", ephemeral=True)
        return

    if not interaction.user.guild_permissions.administrator and str(interaction.user.id) != str(DEVELOPER_ID):
        if not interaction.response.is_done():
            await interaction.response.send_message("Требуются права администратора.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    view = ActionSelectView(interaction.user.id, interaction.guild.id, bot_client)
    embed = discord.Embed(
        title="Настройка доступа бота 🛠️",
        description=(
            "Выберите действие для настройки бота:\n"
            "• **Каналы**: Укажите текстовые каналы, где бот будет работать.\n"
            "• **Ограничить пользователей**: Запретите доступ к боту.\n"
            "• **Снять ограничения**: Верните доступ пользователям.\n"
            "\nНажмите кнопку, чтобы начать."
        ),
        color=discord.Color.blue()
    )
    msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    view.message = msg
    active_views[interaction.user.id] = view
    logger.info(f"Меню restrict открыто для пользователя {interaction.user.id} на сервере {interaction.guild.id}")

def create_command(bot_client):
    @app_commands.command(name="restrict", description=description)
    @app_commands.check(lambda i: i.user.guild_permissions.administrator or str(i.user.id) == str(DEVELOPER_ID))
    async def wrapper(interaction: discord.Interaction):
        await restrict(interaction, bot_client)
    wrapper.guild_only = True
    return wrapper