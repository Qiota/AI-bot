import discord
from discord import app_commands
from discord.ui import Select, View, Button, Modal, TextInput
import json
import os
import asyncio
from decouple import config
from ..logging_config import logger

DEVELOPER_ID = int(config("DEVELOPER_ID", default=0))
CONFIG_FILE = "temp/restrict_settings.json"

description = "Настройка доступа бота: whitelist каналов и blacklist пользователей"

active_views = {}

class ConfigManager:
    _cache = None

    @classmethod
    def load(cls):
        if cls._cache is not None:
            return cls._cache
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    cls._cache = json.load(f)
            else:
                cls._cache = {}
            return cls._cache
        except Exception as e:
            logger.error(f"Ошибка загрузки конфигурации: {e}")
            return {}

    @classmethod
    def save(cls, config):
        cls._cache = config
        try:
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            logger.info("Конфигурация сохранена")
        except Exception as e:
            logger.error(f"Ошибка сохранения конфигурации: {e}")

class BaseView(View):
    def __init__(self, guild_id, user_id, timeout=300):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.user_id = user_id
        self.messages = []

    async def on_timeout(self):
        for msg in self.messages:
            try:
                await msg.delete()
            except discord.errors.NotFound:
                pass
        if self.user_id in active_views:
            del active_views[self.user_id]
        logger.debug(f"View {self.__class__.__name__} timed out for user {self.user_id}")

    async def restrict_interaction(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Это меню только для вас!", ephemeral=True)
            return True
        return False

class SearchModal(Modal, title="Поиск пользователей"):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.query = TextInput(
            label="Введите имена пользователей (через запятую)",
            placeholder="Например, John, Alex",
            custom_id="search_query",
            min_length=1,
            max_length=100
        )
        self.add_item(self.query)

    async def on_submit(self, interaction: discord.Interaction):
        queries = [q.strip().lower() for q in self.query.value.split(",")]
        logger.debug(f"SearchModal: Поиск по запросам '{queries}' пользователем {interaction.user.id}")
        self.view.search_queries = queries
        self.view.current_page = 0
        await self.view.update_view(interaction)

class SelectView(BaseView):
    def __init__(self, guild, user_id, guild_id, action, main_view):
        super().__init__(guild_id, user_id)
        self.guild = guild
        self.action = action
        self.selected_values = []
        self.current_page = 0
        self.items_per_page = 25
        self.search_queries = None
        self.all_items = []
        self.main_view = main_view
        self.setup_items()
        if not self.is_finished():
            self.setup_select()

    def setup_items(self):
        config = ConfigManager.load()
        guild_id = str(self.guild_id)

        if self.action == "bot_access":
            self.all_items = [
                {"label": channel.name, "value": str(channel.id), "default": str(channel.id) in config.get(guild_id, {}).get("bot_allowed_channels", [])}
                for channel in self.guild.text_channels
            ]
            self.placeholder = "Выберите каналы для доступа бота"
        else:
            current_users = config.get(guild_id, {}).get("restricted_users", [])
            members = [
                member for member in self.guild.members
                if not member.bot and (self.action != "unrestrict_users" or str(member.id) in current_users)
            ]
            if not members and self.action == "unrestrict_users":
                self.stop()
                return
            self.all_items = [
                {"label": member.display_name, "value": str(member.id), "default": str(member.id) in current_users, "true_name": member.name, "member": member}
                for member in members
            ]
            self.placeholder = "Выберите пользователей для " + ("ограничения" if self.action == "restrict_users" else "разблокировки")

    def get_paginated_items(self):
        items = self.all_items
        if self.search_queries and self.action != "bot_access":
            filtered_items = []
            for item in items:
                for query in self.search_queries:
                    if query in item["label"].lower() or query in item["true_name"].lower():
                        filtered_items.append(item)
                        break
            items = filtered_items

        start = self.current_page * self.items_per_page
        end = start + self.items_per_page
        return items[start:end]

    def setup_select(self):
        self.clear_items()

        paginated_items = self.get_paginated_items()
        if not paginated_items:
            return

        total_pages = max(1, (len(self.all_items) + self.items_per_page - 1) // self.items_per_page)

        select = Select(
            custom_id=f"{self.action}_select",
            placeholder=f"{self.placeholder} (Страница {self.current_page + 1}/{total_pages})",
            options=[
                discord.SelectOption(label=item["label"], value=item["value"], default=item["default"])
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

        confirm_btn = Button(label="Принять", style=discord.ButtonStyle.green, custom_id=f"{self.action}_confirm")
        confirm_btn.callback = self.confirm_callback
        self.add_item(confirm_btn)

    async def update_view(self, interaction: discord.Interaction):
        self.setup_select()
        if not self.children:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Нет элементов",
                    description="Нет доступных каналов или пользователей для настройки.",
                    color=discord.Color.red()
                ),
                ephemeral=True
            )
            msg = await interaction.original_response()
            self.messages.append(msg)
            await asyncio.sleep(3)
            try:
                await msg.delete()
            except discord.errors.NotFound:
                pass
            await self.return_to_main_menu(interaction)
            return

        title = {
            "bot_access": "Настройка каналов",
            "restrict_users": "Ограничение пользователей",
            "unrestrict_users": "Снятие ограничений"
        }[self.action]
        description = {
            "bot_access": "Выберите каналы, где бот будет работать (текущие отмечены).",
            "restrict_users": "Выберите пользователей для ограничения доступа (текущие отмечены).",
            "unrestrict_users": "Выберите пользователей для снятия ограничений (текущие отмечены)."
        }[self.action]
        if self.search_queries and self.action != "bot_access":
            paginated_items = self.get_paginated_items()
            if paginated_items:
                description += "\nНайденные пользователи:\n" + "\n".join(f"<@{item['value']}>" for item in paginated_items)
            else:
                description += "\nПользователи не найдены."
        await interaction.response.edit_message(
            embed=discord.Embed(
                title=title,
                description=description,
                color=discord.Color.blue()
            ),
            view=self
        )

    async def select_callback(self, interaction: discord.Interaction):
        if await self.restrict_interaction(interaction):
            return
        self.selected_values = self.children[0].values
        logger.debug(f"SelectView: {self.action}, выбрано {len(self.selected_values)} элементов пользователем {self.user_id}")
        await interaction.response.defer()

    async def prev_page(self, interaction: discord.Interaction):
        if await self.restrict_interaction(interaction):
            return
        if self.current_page > 0:
            self.current_page -= 1
        await self.update_view(interaction)

    async def next_page(self, interaction: discord.Interaction):
        if await self.restrict_interaction(interaction):
            return
        total_pages = (len(self.all_items) + self.items_per_page - 1) // self.items_per_page
        if self.current_page < total_pages - 1:
            self.current_page += 1
        await self.update_view(interaction)

    async def search_callback(self, interaction: discord.Interaction):
        if await self.restrict_interaction(interaction):
            return
        modal = SearchModal(self)
        await interaction.response.send_modal(modal)

    async def confirm_callback(self, interaction: discord.Interaction):
        if await self.restrict_interaction(interaction):
            return
        logger.debug(f"SelectView: Подтверждение {self.action} для {len(self.selected_values)} элементов")

        self.children[-1].label = "Loading..."
        self.children[-1].style = discord.ButtonStyle.blurple
        await interaction.response.edit_message(view=self)

        config = ConfigManager.load()
        guild_id = str(self.guild.id)
        if guild_id not in config:
            config[guild_id] = {"bot_allowed_channels": [], "restricted_users": []}

        try:
            if self.action == "bot_access":
                config[guild_id]["bot_allowed_channels"] = self.selected_values
                for channel in self.guild.text_channels:
                    is_allowed = str(channel.id) in self.selected_values
                    try:
                        await channel.set_permissions(
                            interaction.client.user,
                            read_messages=is_allowed,
                            send_messages=is_allowed,
                            reason="Настройка whitelist каналов"
                        )
                    except discord.errors.Forbidden:
                        logger.warning(f"Нет прав для изменения канала {channel.name} (ID: {channel.id})")
                logger.info(f"Whitelist каналов обновлен: {self.selected_values}")
            elif self.action == "restrict_users":
                config[guild_id]["restricted_users"] = list(set(config[guild_id].get("restricted_users", []) + self.selected_values))
                logger.info(f"Blacklist пользователей обновлен: {self.selected_values}")
            elif self.action == "unrestrict_users":
                config[guild_id]["restricted_users"] = [
                    uid for uid in config[guild_id].get("restricted_users", []) if uid not in self.selected_values
                ]
                logger.info(f"Пользователи удалены из blacklist: {self.selected_values}")

            ConfigManager.save(config)
            msg = await interaction.followup.send(
                content="Настройки успешно сохранены!",
                ephemeral=True
            )
            await asyncio.sleep(3)
            try:
                await msg.delete()
            except discord.errors.NotFound:
                pass
        except discord.errors.HTTPException as e:
            logger.error(f"Ошибка при сохранении настроек: {e}")
            msg = await interaction.followup.send(
                content="Ошибка при сохранении настроек. Попробуйте снова.",
                ephemeral=True
            )
            await asyncio.sleep(3)
            try:
                await msg.delete()
            except discord.errors.NotFound:
                pass

        try:
            current_msg = await interaction.original_response()
            await current_msg.delete()
        except discord.errors.NotFound:
            pass
        await self.return_to_main_menu(interaction)

    async def return_to_main_menu(self, interaction: discord.Interaction):
        if self.user_id in active_views:
            main_view = active_views[self.user_id]
            try:
                await main_view.message.edit(
                    embed=discord.Embed(
                        title="Настройка доступа бота",
                        description="Выберите действие:\n"
                                    "• Настроить каналы: Разрешить боту работать в выбранных каналах.\n"
                                    "• Ограничить пользователей: Запретить доступ к боту.\n"
                                    "• Снять ограничения: Разрешить доступ к боту.",
                        color=discord.Color.blue()
                    ),
                    view=main_view
                )
            except discord.errors.NotFound:
                new_main_view = ActionSelectView(self.user_id, self.guild_id)
                msg = await interaction.followup.send(
                    embed=discord.Embed(
                        title="Настройка доступа бота",
                        description="Выберите действие:\n"
                                    "• Настроить каналы: Разрешить боту работать в выбранных каналах.\n"
                                    "• Ограничить пользователей: Запретить доступ к боту.\n"
                                    "• Снять ограничения: Разрешить доступ к боту.",
                        color=discord.Color.blue()
                    ),
                    view=new_main_view,
                    ephemeral=True
                )
                new_main_view.message = msg
                active_views[self.user_id] = new_main_view

class ActionSelectView(BaseView):
    def __init__(self, user_id, guild_id):
        super().__init__(guild_id, user_id)

    async def handle_action(self, interaction: discord.Interaction, action: str):
        if await self.restrict_interaction(interaction):
            return
        logger.debug(f"ActionSelectView: Действие {action} выбрано пользователем {self.user_id}")
        view = SelectView(interaction.guild, self.user_id, self.guild_id, action, self)
        if view.is_finished():
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Нет элементов",
                    description="Blacklist пуст." if action == "unrestrict_users" else "Нет пользователей для добавления.",
                    color=discord.Color.red()
                ),
                ephemeral=True
            )
            msg = await interaction.original_response()
            view.messages.append(msg)
            await asyncio.sleep(3)
            try:
                await msg.delete()
            except discord.errors.NotFound:
                pass
            return

        title = {
            "bot_access": "Настройка каналов",
            "restrict_users": "Ограничение пользователей",
            "unrestrict_users": "Снятие ограничений"
        }[action]
        description = {
            "bot_access": "Выберите каналы, где бот будет работать (текущие отмечены).",
            "restrict_users": "Выберите пользователей для ограничения доступа (текущие отмечены).",
            "unrestrict_users": "Выберите пользователей для снятия ограничений (текущие отмечены)."
        }[action]

        await interaction.response.send_message(
            embed=discord.Embed(
                title=title,
                description=description,
                color=discord.Color.blue()
            ),
            view=view,
            ephemeral=True
        )
        msg = await interaction.original_response()
        view.messages.append(msg)

    @discord.ui.button(label="Настроить каналы", style=discord.ButtonStyle.primary, custom_id="bot_access_btn")
    async def bot_access(self, interaction: discord.Interaction, button: Button):
        await self.handle_action(interaction, "bot_access")

    @discord.ui.button(label="Ограничить пользователей", style=discord.ButtonStyle.primary, custom_id="restrict_users_btn")
    async def restrict_users(self, interaction: discord.Interaction, button: Button):
        await self.handle_action(interaction, "restrict_users")

    @discord.ui.button(label="Снять ограничения", style=discord.ButtonStyle.primary, custom_id="unrestrict_users_btn")
    async def unrestrict_users(self, interaction: discord.Interaction, button: Button):
        await self.handle_action(interaction, "unrestrict_users")

async def check_channels_setup(obj):
    if isinstance(obj, discord.Message) and isinstance(obj.channel, discord.DMChannel):
        return True
    if isinstance(obj, discord.Interaction) and obj.guild is None:
        return True

    config = ConfigManager.load()
    guild_id = str(obj.guild.id)
    allowed_channels = config.get(guild_id, {}).get("bot_allowed_channels", [])
    if isinstance(obj, discord.Interaction) and obj.command.name == "restrict":
        return True
    if not allowed_channels:
        if isinstance(obj, discord.Interaction):
            await obj.response.send_message(
                "Сначала настройте каналы для бота через /restrict.", ephemeral=True
            )
        elif isinstance(obj, discord.Message):
            msg = await obj.channel.send(
                f"{obj.author.mention}, настройте каналы через /restrict.",
                reference=obj
            )
            await asyncio.sleep(5)
            try:
                await msg.delete()
            except discord.errors.NotFound:
                pass
        return False
    return True

async def check_bot_access(obj):
    if isinstance(obj, discord.Message) and isinstance(obj.channel, discord.DMChannel):
        return True
    if isinstance(obj, discord.Interaction) and obj.guild is None:
        return True

    if not await check_channels_setup(obj):
        return False
    config = ConfigManager.load()
    guild_id = str(obj.guild.id)
    allowed_channels = config.get(guild_id, {}).get("bot_allowed_channels", [])
    channel_id = str(obj.channel_id if isinstance(obj, discord.Interaction) else obj.channel.id)
    if channel_id not in allowed_channels:
        if isinstance(obj, discord.Interaction):
            await obj.response.send_message(
                "Бот не работает в этом канале.", ephemeral=True
            )
        return False
    return True

async def check_user_restriction(obj):
    if isinstance(obj, discord.Message) and isinstance(obj.channel, discord.DMChannel):
        return True
    if isinstance(obj, discord.Interaction) and obj.guild is None:
        return True

    if not await check_channels_setup(obj):
        return False
    config = ConfigManager.load()
    guild_id = str(obj.guild.id)
    restricted_users = config.get(guild_id, {}).get("restricted_users", [])
    user_id = str(obj.user.id if isinstance(obj, discord.Interaction) else obj.author.id)
    if user_id in restricted_users:
        if isinstance(obj, discord.Interaction):
            await obj.response.send_message(
                "Ваш доступ к боту ограничен.", ephemeral=True
            )
        return False
    return True

async def handle_mention(message: discord.Message, bot_client):
    if bot_client.user in message.mentions:
        config = ConfigManager.load()
        guild_id = str(message.guild.id) if message.guild else None
        if guild_id:
            allowed_channels = config.get(guild_id, {}).get("bot_allowed_channels", [])
            channel_id = str(message.channel.id)

            if not allowed_channels:
                msg = await message.channel.send(
                    f"{message.author.mention}, настройте каналы через /restrict.",
                    reference=message
                )
                await asyncio.sleep(5)
                try:
                    await msg.delete()
                except discord.errors.NotFound:
                    pass
                return False
            if channel_id not in allowed_channels:
                msg = await message.channel.send(
                    f"{message.author.mention}, бот не работает в этом канале.",
                    reference=message
                )
                await asyncio.sleep(5)
                try:
                    await msg.delete()
                except discord.errors.NotFound:
                    pass
                return False
        return True
    return False

async def restrict(interaction: discord.Interaction, bot_client) -> None:
    if not interaction.guild:
        await interaction.response.send_message(
            "Эта команда доступна только на серверах!", ephemeral=True
        )
        logger.debug(f"restrict: Команда вызвана в ЛС пользователем {interaction.user.id}")
        return

    if not interaction.user.guild_permissions.administrator and interaction.user.id != DEVELOPER_ID:
        await interaction.response.send_message(
            "У вас недостаточно прав для этой команды.", ephemeral=True
        )
        logger.debug(f"restrict: Доступ запрещен для пользователя {interaction.user.id}")
        return

    view = ActionSelectView(interaction.user.id, interaction.guild.id)
    await interaction.response.send_message(
        embed=discord.Embed(
            title="Настройка доступа бота",
            description="Выберите действие:\n"
                        "• Настроить каналы: Разрешить боту работать в выбранных каналах.\n"
                        "• Ограничить пользователей: Запретить доступ к боту.\n"
                        "• Снять ограничения: Разрешить доступ к боту.",
            color=discord.Color.blue()
        ),
        view=view,
        ephemeral=True
    )
    view.message = await interaction.original_response()
    active_views[interaction.user.id] = view
    logger.debug(f"restrict: Меню открыто для пользователя {interaction.user.id}")

def create_command(bot_client):
    @app_commands.command(name="restrict", description=description)
    async def wrapper(interaction: discord.Interaction):
        await restrict(interaction, bot_client)
    wrapper.guild_only = True
    return wrapper