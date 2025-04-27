import discord
from discord import app_commands, ButtonStyle
from discord.ui import Select, View, Button, Modal, TextInput
from decouple import config
from ..systemLog import logger
from ..utils.firebase.firebase_manager import FirebaseManager
from ..utils.checker import checker
from typing import Dict, List, Optional
import asyncio
import traceback

DEVELOPER_ID = config("DEVELOPER_ID", cast=int)
DESCRIPTION = "Настройка бота: каналы (whitelist), пользователи (blacklist). Только для админов."

active_views: Dict[int, View] = {}
guild_config_cache: Dict[str, tuple[Dict, float]] = {}  # {guild_id: (config, timestamp)}
CONFIG_CACHE_TTL = 300  # 5 минут

class BaseView(View):
    """Базовый класс для интерактивных представлений."""
    
    def __init__(self, guild_id: int, user_id: int, timeout: int = 300):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.user_id = user_id
        self.messages: List[discord.Message] = []

    async def on_timeout(self):
        """Обработка таймаута представления."""
        if self.user_id in active_views:
            del active_views[self.user_id]

    async def restrict_interaction(self, interaction: discord.Interaction) -> bool:
        """Ограничение взаимодействия для других пользователей."""
        if interaction.user.id != self.user_id:
            if not interaction.response.is_done():
                await interaction.response.send_message("Это меню только для вас!", ephemeral=True)
            return True
        return False

class SearchModal(Modal, title="Поиск пользователей"):
    """Модальное окно для поиска пользователей."""
    
    def __init__(self, view: 'SelectView'):
        super().__init__()
        self.view = view
        self.name_query = TextInput(
            label="Имена пользователей (через запятую)",
            placeholder="Пример: John, Alex",
            custom_id="name_query",
            min_length=1,
            max_length=100,
            required=False
        )
        self.id_query = TextInput(
            label="ID пользователей (через запятую)",
            placeholder="Пример: 123456789, 987654321",
            custom_id="id_query",
            min_length=1,
            max_length=100,
            required=False
        )
        self.add_item(self.name_query)
        self.add_item(self.id_query)

    async def on_submit(self, interaction: discord.Interaction):
        """Обработка отправки поискового запроса."""
        await interaction.response.defer(ephemeral=True)
        queries = []
        
        if self.name_query.value:
            name_queries = [q.strip().lower() for q in self.name_query.value.split(",") if q.strip()]
            queries.extend(name_queries)
        
        if self.id_query.value:
            id_queries = [q.strip() for q in self.id_query.value.split(",") if q.strip() and q.isdigit()]
            queries.extend(id_queries)
        
        if not queries:
            await interaction.followup.send("Ошибка: укажите хотя бы одно имя или ID.", ephemeral=True)
            return

        self.view.search_queries = queries
        self.view.current_page = 0
        await self.view.update_view(interaction)

class SelectView(BaseView):
    """Представление для выбора каналов или пользователей."""
    
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
                "• Используйте 🔍 для поиска по имени или ID.\n"
                "• Нажмите ✅, чтобы добавить в blacklist."
            ),
            "placeholder": "Выберите пользователей"
        },
        "unrestrict_users": {
            "title": "Снятие ограничений ✅",
            "description": (
                "Выберите пользователей для восстановления доступа.\n"
                "• Используйте 🔍 для поиска по имени или ID.\n"
                "• Нажмите ✅, чтобы убрать из blacklist."
            ),
            "placeholder": "Выберите пользователей"
        }
    }

    def __init__(self, guild: discord.Guild, user_id: int, guild_id: int, action: str, main_view: 'ActionSelectView', bot_client):
        super().__init__(guild_id, user_id)
        self.guild = guild
        self.action = action
        self.main_view = main_view
        self.bot_client = bot_client
        self.selected_values: List[str] = []
        self.current_page = 0
        self.items_per_page = 25
        self.search_queries: Optional[List[str]] = None
        self.all_items: List[Dict] = []
        self.config: Optional[Dict] = None

    async def initialize(self):
        """Инициализация представления."""
        await self.setup_items()
        if not self.is_finished():
            self.setup_select()

    async def load_config(self) -> Dict:
        """Загрузка конфигурации гильдии с кэшированием."""
        current_time = asyncio.get_event_loop().time()
        guild_id_str = str(self.guild_id)
        if guild_id_str in guild_config_cache:
            config, timestamp = guild_config_cache[guild_id_str]
            if current_time - timestamp < CONFIG_CACHE_TTL:
                return config

        try:
            firebase_manager = await self.bot_client._ensure_firebase_initialized()
            config = await firebase_manager.load_guild_config(guild_id_str)
            if config is None:
                logger.warning(f"Конфигурация для гильдии {self.guild_id} не найдена")
                config = {}
            guild_config_cache[guild_id_str] = (config, current_time)
            return config
        except Exception as e:
            logger.error(f"Ошибка загрузки конфигурации гильдии {self.guild_id}: {e}\n{traceback.format_exc()}")
            return {}

    async def setup_items(self):
        """Настройка элементов для выбора."""
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
                {
                    "label": member.display_name,
                    "value": str(member.id),
                    "default": str(member.id) in current_users,
                    "true_name": member.name,
                    "id": str(member.id)
                }
                for member in members
            ]
            self.selected_values = current_users

        if not self.all_items and self.action == "unrestrict_users":
            self.stop()

    def get_paginated_items(self) -> List[Dict]:
        """Получение элементов для текущей страницы."""
        items = self.all_items
        if self.search_queries and self.action != "bot_access":
            items = [
                item for item in items
                if any(
                    query in item["label"].lower() or
                    query in item["true_name"].lower() or
                    query == item["id"]
                    for query in self.search_queries
                )
            ]
        start = self.current_page * self.items_per_page
        return items[start:start + self.items_per_page]

    def setup_select(self):
        """Настройка меню выбора."""
        self.clear_items()
        paginated_items = self.get_paginated_items()
        if not paginated_items:
            return

        total_pages = max(1, (len(self.all_items if not self.search_queries else [
            i for i in self.all_items if any(
                query in i["label"].lower() or
                query in i["true_name"].lower() or
                query == i["id"]
                for query in self.search_queries
            )
        ]) + self.items_per_page - 1) // self.items_per_page)
        select = Select(
            custom_id=f"{self.action}_select",
            placeholder=f"{self.ACTION_CONFIG[self.action]['placeholder']} ({self.current_page + 1}/{total_pages})",
            options=[
                discord.SelectOption(
                    label=item["label"][:80],
                    value=item["value"],
                    default=item["value"] in self.selected_values,
                    description=f"ID: {item['id']}" if self.action != "bot_access" else None
                )
                for item in paginated_items
            ],
            min_values=0,
            max_values=len(paginated_items)
        )
        select.callback = self.select_callback
        self.add_item(select)

        prev_btn = Button(label="⬅️", style=ButtonStyle.grey, custom_id=f"{self.action}_prev", disabled=self.current_page == 0)
        next_btn = Button(label="➡️", style=ButtonStyle.grey, custom_id=f"{self.action}_next", disabled=self.current_page >= total_pages - 1)
        prev_btn.callback = self.prev_page
        next_btn.callback = self.next_page
        self.add_item(prev_btn)
        self.add_item(next_btn)

        if self.action != "bot_access":
            search_btn = Button(label="Поиск 🔍", style=ButtonStyle.grey, custom_id=f"{self.action}_search")
            search_btn.callback = self.search_callback
            self.add_item(search_btn)

        confirm_btn = Button(label="Принять ✅", style=ButtonStyle.green, custom_id=f"{self.action}_confirm")
        confirm_btn.callback = self.confirm_callback   
        self.add_item(confirm_btn)

    async def update_view(self, interaction: discord.Interaction):
        """Обновление представления."""
        self.setup_select()
        if not self.children:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="Нет данных",
                    description="Нет каналов или пользователей для настройки. Проверьте поисковый запрос.",
                    color=discord.Color.red()
                ),
                ephemeral=True
            )
            await self.return_to_main_menu(interaction)
            return

        selected_text = (
            "\n".join(f"- {item['label']}" for item in self.all_items if item["value"] in self.selected_values)
            if self.action == "bot_access"
            else "\n".join(f"<@{value}> (ID: {value})" for value in self.selected_values)
        ) or "- Ничего не выбрано"
        embed = discord.Embed(
            title=self.ACTION_CONFIG[self.action]["title"],
            description=(
                self.ACTION_CONFIG[self.action]["description"]
                + f"\n\n**Выбрано ({len(self.selected_values)}):**"
                + f"\n{selected_text[:1000]}"
                + (f"\n\n**На странице ({len(self.get_paginated_items())}):**"
                   + f"\n{'\n'.join(f'<@{item['value']}> (ID: {item['value']})' for item in self.get_paginated_items()) or '- Пусто'}"
                   if self.search_queries and self.action != "bot_access" else "")
            ),
            color=discord.Color.blue()
        )
        try:
            await interaction.edit_original_response(embed=embed, view=self)
        except discord.HTTPException as e:
            logger.error(f"Ошибка обновления меню для гильдии {self.guild_id}: {e}\n{traceback.format_exc()}")
            await interaction.followup.send("Ошибка отображения меню. Попробуйте снова.", ephemeral=True)

    async def select_callback(self, interaction: discord.Interaction):
        """Обработка выбора элементов."""
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
        """Переход на предыдущую страницу."""
        if await self.restrict_interaction(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        if self.current_page > 0:
            self.current_page -= 1
        await self.update_view(interaction)

    async def next_page(self, interaction: discord.Interaction):
        """Переход на следующую страницу."""
        if await self.restrict_interaction(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        total_pages = (len(self.all_items if not self.search_queries else [
            i for i in self.all_items if any(
                query in i["label"].lower() or
                query in i["true_name"].lower() or
                query == i["id"]
                for query in self.search_queries
            )
        ]) + self.items_per_page - 1) // self.items_per_page
        if self.current_page < total_pages - 1:
            self.current_page += 1
        await self.update_view(interaction)

    async def search_callback(self, interaction: discord.Interaction):
        """Открытие модального окна поиска."""
        if await self.restrict_interaction(interaction):
            return
        try:
            modal = SearchModal(self)
            await interaction.response.send_modal(modal)
        except discord.InteractionResponded:
            await interaction.followup.send("Ошибка: взаимодействие уже обработано.", ephemeral=True)
        except Exception as e:
            logger.error(f"Ошибка отправки модального окна для {interaction.user.id}: {e}\n{traceback.format_exc()}")
            await interaction.followup.send("Ошибка открытия окна поиска.", ephemeral=True)

    async def confirm_callback(self, interaction: discord.Interaction):
        """Подтверждение и сохранение настроек."""
        if await self.restrict_interaction(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        self.children[-1].label = "⏳ Сохранение..."
        self.children[-1].style = ButtonStyle.blurple
        self.children[-1].disabled = True
        await interaction.edit_original_response(view=self)

        try:
            if self.guild.id not in [guild.id for guild in self.bot_client.bot.guilds]:
                logger.warning(f"Бот отсутствует на сервере {self.guild.id}")
                await interaction.followup.send("❌ Бот отсутствует на сервере.", ephemeral=True)
                await self.return_to_main_menu(interaction)
                return

            update_data = {}
            if self.action == "bot_access":
                update_data["bot_allowed_channels"] = self.selected_values
            elif self.action == "restrict_users":
                current_restricted = set(self.config.get("restricted_users", []))
                new_restricted = set(self.selected_values)
                update_data["restricted_users"] = list(current_restricted | new_restricted)
            elif self.action == "unrestrict_users":
                update_data["restricted_users"] = [
                    uid for uid in self.config.get("restricted_users", []) if uid not in self.selected_values
                ]
                for uid in self.selected_values:
                    checker.clear_cache(user_id=uid)

            if "restricted_users" in update_data:
                update_data["restricted_users"] = [uid for uid in update_data["restricted_users"] if uid.isdigit()]
                if len(update_data["restricted_users"]) > 1000:
                    raise ValueError("Слишком много пользователей в blacklist (максимум 1000)")

            firebase_manager = await self.bot_client._ensure_firebase_initialized()
            await firebase_manager.update_guild_fields(str(self.guild.id), update_data)
            guild_config_cache.pop(str(self.guild.id), None)
            await interaction.followup.send("✅ Настройки сохранены!", ephemeral=True)
        except ValueError as ve:
            logger.error(f"Ошибка валидации гильдии {self.guild.id}: {ve}\n{traceback.format_exc()}")
            await interaction.followup.send(f"❌ Ошибка: {ve}", ephemeral=True)
        except Exception as e:
            logger.error(f"Ошибка сохранения настроек гильдии {self.guild.id}: {e}\n{traceback.format_exc()}")
            await interaction.followup.send("❌ Ошибка сохранения.", ephemeral=True)

        try:
            await interaction.delete_original_response()
        except (discord.NotFound, discord.Forbidden):
            pass
        await self.return_to_main_menu(interaction)

    async def return_to_main_menu(self, interaction: discord.Interaction):
        """Возврат к главному меню."""
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
        except (discord.NotFound, discord.Forbidden):
            new_main_view = ActionSelectView(self.user_id, self.guild_id, self.bot_client)
            msg = await interaction.followup.send(embed=embed, view=new_main_view, ephemeral=True)
            new_main_view.message = msg
            active_views[self.user_id] = new_main_view

class ActionSelectView(BaseView):
    """Главное меню выбора действий."""
    
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
        """Настройка кнопок главного меню."""
        for action, config in self.ACTION_CONFIG.items():
            button = Button(
                label=config["label"],
                style=ButtonStyle.primary,
                custom_id=f"{action}_btn"
            )
            button.callback = lambda i, a=action: self.handle_action(i, a)
            self.add_item(button)

    async def handle_action(self, interaction: discord.Interaction, action: str):
        """Обработка выбора действия."""
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

async def check_channels_setup(obj: discord.Interaction | discord.Message) -> tuple[bool, Optional[str]]:
    """Проверка настройки каналов для гильдии."""
    try:
        if isinstance(obj, discord.Message) and isinstance(obj.channel, discord.DMChannel):
            return True, None
        if isinstance(obj, discord.Interaction) and obj.guild is None:
            return True, None
        if isinstance(obj, discord.Interaction) and obj.command.name == "restrict":
            return True, None

        guild_id_str = str(obj.guild.id)
        current_time = asyncio.get_event_loop().time()
        if guild_id_str in guild_config_cache:
            config, timestamp = guild_config_cache[guild_id_str]
            if current_time - timestamp < CONFIG_CACHE_TTL:
                return config, None
            config = None
        else:
            config = None

        if config is None:
            firebase_manager = await FirebaseManager.initialize()
            config = await firebase_manager.load_guild_config(guild_id_str)
            guild_config_cache[guild_id_str] = (config or {}, current_time)

        if config is None:
            logger.warning(f"Конфигурация для гильдии {guild_id_str} не найдена")
            return False, "Конфигурация сервера не найдена."

        allowed_channels = config.get("bot_allowed_channels", [])
        if not allowed_channels:
            return True, None
        return True, None
    except Exception as e:
        logger.error(f"Ошибка в check_channels_setup для гильдии {obj.guild.id if obj.guild else 'DM'}: {e}\n{traceback.format_exc()}")
        return False, "Ошибка при проверке конфигурации."

async def check_bot_access(obj: discord.Interaction | discord.Message, bot_client) -> tuple[bool, Optional[str]]:
    """Проверка доступа бота к каналу."""
    try:
        if isinstance(obj, discord.Message) and isinstance(obj.channel, discord.DMChannel):
            return True, None
        if isinstance(obj, discord.Interaction) and obj.guild is None:
            return True, None

        channel = obj.channel if isinstance(obj, discord.Message) else obj.channel
        guild_id_str = str(obj.guild.id)
        channel_id = str(obj.channel_id if isinstance(obj, discord.Interaction) else obj.channel.id)

        # Проверка прав бота в канале
        permissions = channel.permissions_for(obj.guild.me)
        required_permissions = (
            permissions.read_messages and
            permissions.send_messages and
            permissions.embed_links
        )
        if not required_permissions:
            logger.error(f"Бот не имеет необходимых прав в канале {channel_id} гильдии {guild_id_str}: "
                        f"read_messages={permissions.read_messages}, "
                        f"send_messages={permissions.send_messages}, "
                        f"embed_links={permissions.embed_links}")
            return False, "Бот не имеет необходимых прав в этом канале! Проверьте настройки разрешений в Discord."

        # Проверка конфигурации
        result, reason = await check_channels_setup(obj)
        if not result:
            return False, reason

        config = (guild_config_cache.get(guild_id_str) or (None, 0))[0]
        if config is None:
            firebase_manager = await bot_client._ensure_firebase_initialized()
            config = await firebase_manager.load_guild_config(guild_id_str)
            guild_config_cache[guild_id_str] = (config or {}, asyncio.get_event_loop().time())

        if config is None:
            logger.warning(f"Конфигурация для гильдии {guild_id_str} не найдена")
            return False, "Конфигурация сервера не найдена! Настройте через /restrict."

        allowed_channels = config.get("bot_allowed_channels", [])

        if allowed_channels and channel_id not in allowed_channels:
            return False, f"Бот не имеет доступа к этому каналу! Добавьте канал через /restrict."

        return True, None
    except Exception as e:
        logger.error(f"Ошибка в check_bot_access для гильдии {obj.guild.id if obj.guild else 'DM'}: {e}\n{traceback.format_exc()}")
        return False, "Ошибка при проверке доступа! Обратитесь к администратору."

async def restrict_command_execution(obj: discord.Interaction, bot_client) -> tuple[bool, Optional[str]]:
    """Проверка выполнения команды /restrict."""
    try:
        if not bot_client.bot.is_ready():
            return False, "Бот еще не готов."

        if obj.guild:
            guild_ids = [guild.id for guild in bot_client.bot.guilds]
            if obj.guild.id not in guild_ids:
                logger.warning(f"Бот отсутствует на сервере {obj.guild.id}")
                try:
                    dm_channel = await obj.user.create_dm()
                    await dm_channel.send("❌ Бот отсутствует на этом сервере. Пригласите бота на сервер, чтобы использовать команды.")
                except (discord.Forbidden, discord.HTTPException) as e:
                    logger.error(f"Не удалось отправить сообщение в ЛС пользователю {obj.user.id}: {e}")
                return False, None

            config = (guild_config_cache.get(str(obj.guild.id)) or (None, 0))[0]
            if config is None:
                firebase_manager = await bot_client._ensure_firebase_initialized()
                config = await firebase_manager.load_guild_config(str(obj.guild.id))
                guild_config_cache[str(obj.guild.id)] = (config or {}, asyncio.get_event_loop().time())

            if config is None:
                logger.warning(f"Конфигурация для гильдии {obj.guild.id} не найдена")
                return False, "Конфигурация сервера не найдена! Настройте через /restrict."

        return True, None
    except Exception as e:
        logger.error(f"Ошибка в restrict_command_execution: {e}\n{traceback.format_exc()}")
        return False, "Ошибка при проверке доступа."

async def handle_mention(message: discord.Message, bot_client) -> bool:
    """Обработка упоминания бота."""
    try:
        if isinstance(message.channel, discord.DMChannel):
            return True
        if bot_client.bot.user not in message.mentions:
            return False

        result, reason = await check_bot_access(message, bot_client)
        if not result:
            if reason:
                await message.channel.send(
                    f"{message.author.mention}, {reason}",
                    ephemeral=True
                )
            return False

        return True
    except Exception as e:
        logger.error(f"Ошибка в handle_mention для гильдии {message.guild.id}: {e}\n{traceback.format_exc()}")
        return False

async def restrict(interaction: discord.Interaction, bot_client):
    """Логика команды /restrict."""
    # Check permissions first
    if not interaction.user.guild_permissions.administrator and str(interaction.user.id) != str(DEVELOPER_ID):
        if not interaction.response.is_done():
            await interaction.response.send_message("Требуются права администратора или статус разработчика.", ephemeral=True)
        return

    # Validate command execution
    result, reason = await restrict_command_execution(interaction, bot_client)
    if not result:
        if reason and not interaction.response.is_done():
            await interaction.response.send_message(reason, ephemeral=True)
        return

    if not interaction.guild:
        if not interaction.response.is_done():
            await interaction.response.send_message("Команда только для серверов!", ephemeral=True)
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

def create_command(bot_client):
    """Создание команды /restrict."""
    @app_commands.command(name="restrict", description=DESCRIPTION)
    async def wrapper(interaction: discord.Interaction):
        await restrict(interaction, bot_client)
    wrapper.guild_only = True
    return wrapper