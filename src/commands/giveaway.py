import discord
from discord import app_commands, ui
from typing import Optional, Set, Dict, Tuple
from ..systemLog import logger
import random
import time
import asyncio
import string
from .restrict import check_bot_access, restrict_command_execution

description = "Создать розыгрыш через слеш-команду с кнопкой участия"

COMPLETED_GIVEAWAY_RETENTION = 7 * 24 * 60 * 60  # 7 дней
MAX_PRIZE_LENGTH = 100
MAX_DESC_LENGTH = 500
MIN_DURATION = 1
MAX_DURATION = 10080
DEFAULT_DESCRIPTION = "Нажмите на кнопку ниже, чтобы принять участие в розыгрыше!"
GIVEAWAY_IMAGE = "https://i.postimg.cc/vHtwYT81/giveaway.png"
WINNER_IMAGE = "https://i.postimg.cc/jjSrDb3s/winner.jpg"

def parse_duration(duration_str: str) -> int:
    """Парсит строку длительности в минуты."""
    if not duration_str:
        raise ValueError("Длительность не указана")
    total_minutes = 0
    parts = duration_str.lower().replace(" ", "").replace("-", "")
    current_number = ""
    for char in parts:
        if char.isdigit():
            current_number += char
        elif char in "dhm":
            if not current_number:
                raise ValueError("Неверный формат длительности. Используйте d-дни h-часы m-минуты")
            value = int(current_number)
            if char == "d":
                total_minutes += value * 24 * 60
            elif char == "h":
                total_minutes += value * 60
            elif char == "m":
                total_minutes += value
            current_number = ""
        else:
            raise ValueError("Неверный формат длительности. Используйте d-дни h-часы m-минуты")
    if current_number:
        raise ValueError("Неверный формат длительности. Используйте d-дни h-часы m-минуты")
    if total_minutes < MIN_DURATION or total_minutes > MAX_DURATION:
        raise ValueError(f"Длительность должна быть от {MIN_DURATION} до {MAX_DURATION} минут")
    return total_minutes

async def save_giveaways(giveaways: Dict[str, 'Giveaway'], completed_giveaways: Dict[str, 'Giveaway'], bot_client) -> None:
    """Сохраняет розыгрыши в Firebase."""
    try:
        firebase_manager = await bot_client._ensure_firebase_initialized()
        active_data = {cid: g.to_dict() for cid, g in giveaways.items()}
        completed_data = {cid: g.to_dict() for cid, g in completed_giveaways.items()}
        await firebase_manager.save_giveaways(active_data, completed_data)
        logger.debug(f"Розыгрыши сохранены в Firebase: {len(active_data)} активных, {len(completed_data)} завершённых")
    except Exception as e:
        logger.error(f"Ошибка сохранения розыгрышей: {e}")

async def load_giveaways(bot_client) -> Tuple[Dict[str, 'Giveaway'], Dict[str, 'Giveaway']]:
    """Загружает розыгрыши из Firebase."""
    active_giveaways = {}
    completed_giveaways = {}
    try:
        firebase_manager = await bot_client._ensure_firebase_initialized()
        data = await firebase_manager.load_giveaways()
        current_time = int(time.time())
        expired_giveaways = []

        for custom_id, giveaway_data in data.get("active", {}).items():
            giveaway = Giveaway.from_dict(giveaway_data, bot_client)
            if not giveaway:
                logger.debug(f"Удаление недоступного активного розыгрыша {custom_id}")
                expired_giveaways.append(f"giveaway/active/{custom_id}")
                continue
            if current_time >= giveaway.end_time:
                giveaway.completed_at = current_time
                completed_giveaways[custom_id] = giveaway
                expired_giveaways.append(f"giveaway/active/{custom_id}")
                logger.debug(f"Активный розыгрыш {custom_id} истёк, перемещён в завершённые")
            else:
                active_giveaways[custom_id] = giveaway

        for custom_id, giveaway_data in data.get("ended", {}).items():
            completed_at = giveaway_data.get("completed_at", 0)
            if current_time - completed_at <= COMPLETED_GIVEAWAY_RETENTION:
                giveaway = Giveaway.from_dict(giveaway_data, bot_client)
                if giveaway:
                    completed_giveaways[custom_id] = giveaway
                else:
                    logger.debug(f"Удаление недоступного завершённого розыгрыша {custom_id}")
                    expired_giveaways.append(f"giveaway/ended/{custom_id}")
            else:
                logger.debug(f"Завершённый розыгрыш {custom_id} устарел, будет удалён")
                expired_giveaways.append(f"giveaway/ended/{custom_id}")

        if expired_giveaways:
            updates = {path: None for path in expired_giveaways}
            await asyncio.get_event_loop().run_in_executor(None, lambda: firebase_manager._db.update(updates))
            logger.debug(f"Удалено {len(expired_giveaways)} недоступных или устаревших розыгрышей из Firebase")

        logger.info(f"Загружено {len(active_giveaways)} активных и {len(completed_giveaways)} завершённых розыгрышей")
    except Exception as e:
        logger.error(f"Ошибка загрузки розыгрышей: {e}")
    return active_giveaways, completed_giveaways

class Giveaway:
    """Класс для управления розыгрышем."""
    def __init__(
        self,
        prize: str,
        duration: int,
        host: discord.User,
        channel: discord.TextChannel,
        custom_id: str,
        description: str,
        message_id: Optional[int] = None,
        end_time: Optional[int] = None,
        participants: Optional[Set[discord.User]] = None,
        completed_at: Optional[int] = None,
        giveaway_image: Optional[str] = None,
        winner_image: Optional[str] = None
    ):
        self.prize = prize
        self.duration = duration
        self.host = host
        self.channel = channel
        self.custom_id = custom_id
        self.description = description
        self.participants = participants or set()
        self.message_id = message_id
        self.end_time = end_time or int(time.time()) + duration * 60
        self.winner = None
        self.completed_at = completed_at
        self.giveaway_image = giveaway_image
        self.winner_image = winner_image

    def to_dict(self) -> dict:
        """Сериализует розыгрыш в словарь для Firebase."""
        return {
            "prize": self.prize,
            "duration": self.duration,
            "host_id": self.host.id,
            "channel_id": self.channel.id,
            "custom_id": self.custom_id,
            "description": self.description,
            "message_id": self.message_id,
            "end_time": self.end_time,
            "participant_ids": [user.id for user in self.participants],
            "winner_id": self.winner.id if self.winner else None,
            "completed_at": self.completed_at,
            "giveaway_image": self.giveaway_image,
            "winner_image": self.winner_image
        }

    @classmethod
    def from_dict(cls, data: dict, bot_client) -> Optional['Giveaway']:
        """Десериализует розыгрыш из данных Firebase."""
        try:
            channel_id = data.get("channel_id")
            host_id = data.get("host_id")
            
            channel = bot_client.bot.get_channel(channel_id) if channel_id else None
            if not channel:
                logger.warning(f"Канал {channel_id} недоступен для розыгрыша {data.get('custom_id', 'unknown')}")
                return None
            
            host = bot_client.bot.get_user(host_id)
            if not host:
                logger.warning(f"Хост {host_id} не найден для розыгрыша {data.get('custom_id', 'unknown')}")
                return None
            
            participants = set()
            guild = channel.guild if hasattr(channel, 'guild') else None
            for user_id in data.get("participant_ids", []):
                user = bot_client.bot.get_user(user_id) or (guild.get_member(user_id) if guild else None)
                if user:
                    participants.add(user)
            
            giveaway = cls(
                prize=data["prize"],
                duration=data["duration"],
                host=host,
                channel=channel,
                custom_id=data["custom_id"],
                description=data["description"],
                message_id=data["message_id"],
                end_time=data["end_time"],
                participants=participants,
                completed_at=data.get("completed_at"),
                giveaway_image=data.get("giveaway_image"),
                winner_image=data.get("winner_image")
            )
            
            if data.get("winner_id"):
                giveaway.winner = bot_client.bot.get_user(data["winner_id"]) or (guild.get_member(data["winner_id"]) if guild else None)
                if not giveaway.winner:
                    logger.warning(f"Победитель {data['winner_id']} не найден для розыгрыша {data.get('custom_id', 'unknown')}")
            
            logger.debug(f"Успешно восстановлен розыгрыш {data['custom_id']}")
            return giveaway
        except Exception as e:
            logger.error(f"Ошибка восстановления розыгрыша {data.get('custom_id', 'unknown')}: {e}")
            return None

class ParticipantsView(ui.View):
    """View для отображения списка участников розыгрыша с пагинацией."""
    def __init__(self, participants: list, custom_id: str, timeout: int):
        super().__init__(timeout=timeout)
        self.participants = list(participants)
        self.custom_id = custom_id
        self.page = 0
        self.page_size = 25
        self.total_pages = (len(self.participants) + self.page_size - 1) // self.page_size
        self.update_buttons()

    def get_current_page(self) -> list:
        """Возвращает участников для текущей страницы."""
        start = self.page * self.page_size
        end = start + self.page_size
        return self.participants[start:end]

    def update_buttons(self) -> None:
        """Обновляет состояние кнопок пагинации."""
        self.children[0].disabled = self.page == 0
        self.children[1].disabled = self.page == self.total_pages - 1

    @ui.button(label="⬅️", style=discord.ButtonStyle.gray)
    async def previous_page(self, interaction: discord.Interaction, button: ui.Button):
        """Переходит на предыдущую страницу."""
        await interaction.response.defer(ephemeral=True)
        self.page = max(0, self.page - 1)
        self.update_buttons()
        embed = self.create_embed()
        await interaction.followup.send(embed=embed, view=self, ephemeral=True)

    @ui.button(label="➡️", style=discord.ButtonStyle.gray)
    async def next_page(self, interaction: discord.Interaction, button: ui.Button):
        """Переходит на следующую страницу."""
        await interaction.response.defer(ephemeral=True)
        self.page = min(self.total_pages - 1, self.page + 1)
        self.update_buttons()
        embed = self.create_embed()
        await interaction.followup.send(embed=embed, view=self, ephemeral=True)

    def create_embed(self) -> discord.Embed:
        """Создаёт embed для текущей страницы участников."""
        page_participants = self.get_current_page()
        embed = discord.Embed(
            title=f"Участники розыгрыша ID: `{self.custom_id}` ({self.page + 1}/{self.total_pages})",
            description="\n".join(f"{idx + 1}. <@{user.id}>" for idx, user in enumerate(page_participants, self.page * self.page_size)),
            color=discord.Color.blue()
        )
        return embed

class GiveawayView(ui.View):
    """View для взаимодействия с розыгрышем (участие, список участников)."""
    def __init__(self, bot_client, custom_id: str, duration: int):
        super().__init__(timeout=duration * 60)
        self.bot_client = bot_client
        self.custom_id = custom_id

    @ui.button(label="Участвовать", style=discord.ButtonStyle.green, emoji="🎉", custom_id="participate")
    async def participate(self, interaction: discord.Interaction, button: ui.Button):
        """Обрабатывает участие или выход из розыгрыша."""
        await interaction.response.defer(ephemeral=True)
        if not hasattr(self.bot_client, 'giveaways') or not hasattr(self.bot_client, 'completed_giveaways'):
            logger.error("BotClient не имеет атрибутов giveaways или completed_giveaways")
            embed = discord.Embed(description="Ошибка: функционал розыгрышей недоступен.", color=discord.Color.red())
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        giveaway = self.bot_client.giveaways.get(self.custom_id)
        if not giveaway:
            embed = discord.Embed(description="Розыгрыш завершён или не существует.", color=discord.Color.red())
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        current_time = int(time.time())
        if current_time >= giveaway.end_time:
            embed = discord.Embed(description="Розыгрыш уже завершён!", color=discord.Color.red())
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        if interaction.user in giveaway.participants:
            giveaway.participants.remove(interaction.user)
            await save_giveaways(self.bot_client.giveaways, self.bot_client.completed_giveaways, self.bot_client)
            embed = discord.Embed(description="Вы вышли из розыгрыша!", color=discord.Color.red())
            await interaction.followup.send(embed=embed, ephemeral=True)
            message = await giveaway.channel.fetch_message(giveaway.message_id)
            new_view = GiveawayView(self.bot_client, self.custom_id, (giveaway.end_time - current_time) // 60)
            embed = discord.Embed(
                title="🎉 Новый розыгрыш!",
                description=f"**Приз:** {giveaway.prize}\n**Описание:** {giveaway.description}",
                color=discord.Color.gold()
            )
            embed.add_field(name="Организатор:", value=f"<@{giveaway.host.id}>", inline=True)
            embed.add_field(name="Заканчивается:", value=f"> <t:{giveaway.end_time}:R>", inline=True)
            embed.add_field(name="Дата окончания:", value=f"> <t:{giveaway.end_time}:F>", inline=True)
            embed.set_image(url=giveaway.giveaway_image or GIVEAWAY_IMAGE)
            embed.set_footer(text=f"ID ивента: {self.custom_id} | Участвуют: {len(giveaway.participants)}")
            await message.edit(embed=embed, view=new_view)
        else:
            giveaway.participants.add(interaction.user)
            await save_giveaways(self.bot_client.giveaways, self.bot_client.completed_giveaways, self.bot_client)
            embed = discord.Embed(description="Вы успешно участвуете в розыгрыше!", color=discord.Color.green())
            await interaction.followup.send(embed=embed, ephemeral=True)
            message = await giveaway.channel.fetch_message(giveaway.message_id)
            new_view = GiveawayView(self.bot_client, self.custom_id, (giveaway.end_time - current_time) // 60)
            new_view.children[0].label = "Выйти"
            new_view.children[0].style = discord.ButtonStyle.red
            new_view.children[0].emoji = "🚪"
            embed = discord.Embed(
                title="🎉 Новый розыгрыш!",
                description=f"**Приз:** {giveaway.prize}\n**Описание:** {giveaway.description}",
                color=discord.Color.gold()
            )
            embed.add_field(name="Организатор:", value=f"<@{giveaway.host.id}>", inline=True)
            embed.add_field(name="Заканчивается:", value=f"> <t:{giveaway.end_time}:R>", inline=True)
            embed.add_field(name="Дата окончания:", value=f"> <t:{giveaway.end_time}:F>", inline=True)
            embed.set_image(url=giveaway.giveaway_image or GIVEAWAY_IMAGE)
            embed.set_footer(text=f"ID ивента: {self.custom_id} | Участвуют: {len(giveaway.participants)}")
            await message.edit(embed=embed, view=new_view)

    @ui.button(label="Список участников", style=discord.ButtonStyle.blurple)
    async def show_participants(self, interaction: discord.Interaction, button: ui.Button):
        """Показывает список участников розыгрыша."""
        await interaction.response.defer(ephemeral=True)
        if not hasattr(self.bot_client, 'giveaways'):
            logger.error("BotClient не имеет атрибута giveaways")
            embed = discord.Embed(description="Ошибка: функционал розыгрышей недоступен.", color=discord.Color.red())
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        giveaway = self.bot_client.giveaways.get(self.custom_id)
        if not giveaway:
            embed = discord.Embed(description="Розыгрыш завершён или не существует.", color=discord.Color.red())
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        current_time = int(time.time())
        if current_time >= giveaway.end_time:
            embed = discord.Embed(description="Розыгрыш уже завершён!", color=discord.Color.red())
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        participants = list(giveaway.participants)
        if not participants:
            embed = discord.Embed(description="Нет участников.", color=discord.Color.orange())
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        view = ParticipantsView(participants, self.custom_id, timeout=(giveaway.end_time - current_time))
        embed = view.create_embed()
        if view.total_pages > 1:
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

async def update_giveaway_message(bot_client, giveaway: 'Giveaway') -> None:
    """Периодически обновляет сообщение розыгрыша."""
    try:
        while True:
            current_time = int(time.time())
            if current_time >= giveaway.end_time:
                break
            message = await giveaway.channel.fetch_message(giveaway.message_id)
            embed = discord.Embed(
                title="🎉 Новый розыгрыш!",
                description=f"**Приз:** {giveaway.prize}\n**Описание:** {giveaway.description}",
                color=discord.Color.gold()
            )
            embed.add_field(name="Организатор:", value=f"<@{giveaway.host.id}>", inline=True)
            embed.add_field(name="Заканчивается:", value=f"> <t:{giveaway.end_time}:R>", inline=True)
            embed.add_field(name="Дата окончания:", value=f"> <t:{giveaway.end_time}:F>", inline=True)
            embed.set_image(url=giveaway.giveaway_image or GIVEAWAY_IMAGE)
            embed.set_footer(text=f"ID ивента: {giveaway.custom_id} | Участвуют: {len(giveaway.participants)}")
            await message.edit(embed=embed)
            await asyncio.sleep(300)
    except discord.HTTPException as e:
        logger.error(f"Ошибка обновления сообщения розыгрыша {giveaway.custom_id}: {e}")
    except Exception as e:
        logger.error(f"Неизвестная ошибка при обновлении сообщения розыгрыша {giveaway.custom_id}: {e}")

async def resume_giveaways(bot_client) -> None:
    """Возобновляет активные розыгрыши при старте бота."""
    if not hasattr(bot_client, 'giveaways') or not hasattr(bot_client, 'completed_giveaways'):
        logger.error("BotClient не имеет атрибутов giveaways или completed_giveaways")
        return
    try:
        bot_client.giveaways, bot_client.completed_giveaways = await load_giveaways(bot_client)
        firebase_manager = await bot_client._ensure_firebase_initialized()
        await firebase_manager.cleanup_expired_giveaways(current_time=int(time.time()))

        for custom_id, giveaway in list(bot_client.giveaways.items()):
            current_time = int(time.time())
            if giveaway.end_time <= current_time:
                logger.info(f"Розыгрыш {custom_id} истёк, завершаем")
                asyncio.create_task(end_giveaway(bot_client, giveaway))
                continue

            # Проверяем и восстанавливаем сообщение розыгрыша
            remaining_time = giveaway.end_time - current_time
            try:
                message = await giveaway.channel.fetch_message(giveaway.message_id)
                # Обновляем сообщение с новым View для восстановления кнопок
                embed = discord.Embed(
                    title="🎉 Новый розыгрыш!",
                    description=f"**Приз:** {giveaway.prize}\n**Описание:** {giveaway.description}",
                    color=discord.Color.gold()
                )
                embed.add_field(name="Организатор:", value=f"<@{giveaway.host.id}>", inline=True)
                embed.add_field(name="Заканчивается:", value=f"> <t:{giveaway.end_time}:R>", inline=True)
                embed.add_field(name="Дата окончания:", value=f"> <t:{giveaway.end_time}:F>", inline=True)
                embed.set_image(url=giveaway.giveaway_image or GIVEAWAY_IMAGE)
                embed.set_footer(text=f"ID ивента: {custom_id} | Участвуют: {len(giveaway.participants)}")
                view = GiveawayView(bot_client, custom_id, remaining_time // 60)
                await message.edit(embed=embed, view=view)
                logger.debug(f"Сообщение розыгрыша {custom_id} обновлено с новым View")
            except discord.NotFound:
                # Если сообщение не найдено, создаём новое
                logger.warning(f"Сообщение розыгрыша {giveaway.message_id} не найдено, создаём новое для {custom_id}")
                embed = discord.Embed(
                    title="🎉 Новый розыгрыш!",
                    description=f"**Приз:** {giveaway.prize}\n**Описание:** {giveaway.description}",
                    color=discord.Color.gold()
                )
                embed.add_field(name="Организатор:", value=f"<@{giveaway.host.id}>", inline=True)
                embed.add_field(name="Заканчивается:", value=f"> <t:{giveaway.end_time}:R>", inline=True)
                embed.add_field(name="Дата окончания:", value=f"> <t:{giveaway.end_time}:F>", inline=True)
                embed.set_image(url=giveaway.giveaway_image or GIVEAWAY_IMAGE)
                embed.set_footer(text=f"ID ивента: {custom_id} | Участвуют: {len(giveaway.participants)}")
                view = GiveawayView(bot_client, custom_id, remaining_time // 60)
                new_message = await giveaway.channel.send(embed=embed, view=view)
                giveaway.message_id = new_message.id
                await save_giveaways(bot_client.giveaways, bot_client.completed_giveaways, bot_client)
                logger.debug(f"Создано новое сообщение для розыгрыша {custom_id} с ID {new_message.id}")
            except discord.Forbidden:
                logger.error(f"Нет прав для отправки/редактирования сообщения в канале {giveaway.channel.id} для розыгрыша {custom_id}")
                del bot_client.giveaways[custom_id]
                await firebase_manager._db.update({f"giveaway/active/{custom_id}": None})
                logger.debug(f"Розыгрыш {custom_id} удалён из Firebase из-за отсутствия прав")
                continue
            except Exception as e:
                logger.error(f"Ошибка при восстановлении сообщения розыгрыша {custom_id}: {e}")
                continue

            # Запускаем задачи для активного розыгрыша
            logger.debug(f"Возобновление розыгрыша {custom_id} с оставшимся временем {remaining_time} секунд")
            asyncio.create_task(run_giveaway_timer(bot_client, giveaway, remaining_time))
            asyncio.create_task(update_giveaway_message(bot_client, giveaway))

        logger.info(f"Возобновлено {len(bot_client.giveaways)} активных розыгрышей")
    except Exception as e:
        logger.error(f"Ошибка в resume_giveaways: {e}")

async def run_giveaway_timer(bot_client, giveaway: 'Giveaway', remaining_time: int) -> None:
    """Запускает таймер для завершения розыгрыша."""
    try:
        await asyncio.sleep(remaining_time)
        if giveaway.custom_id in bot_client.giveaways:
            await end_giveaway(bot_client, giveaway)
    except Exception as e:
        logger.error(f"Ошибка таймера розыгрыша {giveaway.custom_id}: {e}")

async def end_giveaway(bot_client, giveaway: 'Giveaway') -> None:
    """Завершает розыгрыш и объявляет победителя."""
    if not hasattr(bot_client, 'giveaways') or not hasattr(bot_client, 'completed_giveaways'):
        logger.error("BotClient не имеет атрибутов giveaways или completed_giveaways")
        return
    try:
        try:
            message = await giveaway.channel.fetch_message(giveaway.message_id)
            await message.delete()
        except discord.NotFound:
            pass  # Сообщение уже удалено или не найдено, продолжаем

        view = discord.ui.View()
        view.clear_items()
        if not giveaway.participants:
            embed = discord.Embed(
                title="Розыгрыш завершён",
                description=f"**Приз:** {giveaway.prize}\n**Никто не участвовал!**",
                color=discord.Color.red()
            )
            embed.add_field(name="Организатор:", value=f"<@{giveaway.host.id}>", inline=True)
            embed.set_image(url=giveaway.giveaway_image or GIVEAWAY_IMAGE)
            embed.set_footer(text=f"ID ивента: {giveaway.custom_id} | Участвуют: 0")
            await giveaway.channel.send(embed=embed, view=view)
        else:
            giveaway.winner = random.choice(list(giveaway.participants))
            embed = discord.Embed(
                title="🎉 Розыгрыш завершён!",
                color=discord.Color.green()
            )
            embed.add_field(name="Приз:", value=f"> `{giveaway.prize}`", inline=True)
            embed.add_field(name="Победитель:", value=f"> {giveaway.winner.mention}", inline=True)
            embed.add_field(name="Организатор:", value=f"<@{giveaway.host.id}>", inline=True)
            embed.set_image(url=giveaway.winner_image or WINNER_IMAGE)
            embed.set_footer(text=f"ID ивента: {giveaway.custom_id} | Участвовали: {len(giveaway.participants)}")
            await giveaway.channel.send(content=f"{giveaway.host.mention} {giveaway.winner.mention}\n", embed=embed, view=view)
        
        giveaway.completed_at = int(time.time())
        bot_client.completed_giveaways[giveaway.custom_id] = giveaway
        bot_client.giveaways.pop(giveaway.custom_id, None)
        await save_giveaways(bot_client.giveaways, bot_client.completed_giveaways, bot_client)
    except Exception as e:
        logger.error(f"Ошибка завершения розыгрыша {giveaway.custom_id}: {e}")
        # Пытаемся отправить сообщение даже при ошибке
        try:
            view = discord.ui.View()
            view.clear_items()
            if not giveaway.participants:
                embed = discord.Embed(
                    title="Розыгрыш завершён",
                    description=f"**Приз:** {giveaway.prize}\n**Никто не участвовал!**",
                    color=discord.Color.red()
                )
                embed.add_field(name="Организатор:", value=f"<@{giveaway.host.id}>", inline=True)
                embed.set_image(url=giveaway.giveaway_image or GIVEAWAY_IMAGE)
                embed.set_footer(text=f"ID ивента: {giveaway.custom_id} | Участвуют: 0")
                await giveaway.channel.send(embed=embed, view=view)
            else:
                giveaway.winner = random.choice(list(giveaway.participants))
                embed = discord.Embed(
                    title="🎉 Розыгрыш завершён!",
                    color=discord.Color.green()
                )
                embed.add_field(name="Приз:", value=f"> `{giveaway.prize}`", inline=True)
                embed.add_field(name="Победитель:", value=f"> {giveaway.winner.mention}", inline=True)
                embed.add_field(name="Организатор:", value=f"<@{giveaway.host.id}>", inline=True)
                embed.set_image(url=giveaway.winner_image or WINNER_IMAGE)
                embed.set_footer(text=f"ID ивента: {giveaway.custom_id} | Участвовали: {len(giveaway.participants)}")
                await giveaway.channel.send(content=f"{giveaway.host.mention} {giveaway.winner.mention}\n", embed=embed, view=view)
            giveaway.completed_at = int(time.time())
            bot_client.completed_giveaways[giveaway.custom_id] = giveaway
            bot_client.giveaways.pop(giveaway.custom_id, None)
            await save_giveaways(bot_client.giveaways, bot_client.completed_giveaways, bot_client)
        except Exception as e2:
            logger.error(f"Критическая ошибка при попытке отправки сообщения о завершении {giveaway.custom_id}: {e2}")

async def start_giveaway(
    interaction: discord.Interaction,
    prize: str,
    duration_str: str,
    description: Optional[str],
    bot_client,
    giveaway_image: Optional[str] = None,
    winner_image: Optional[str] = None
) -> None:
    """Запускает новый розыгрыш."""
    if bot_client is None:
        logger.error("bot_client не предоставлен для команды /giveaway")
        await interaction.response.send_message("Ошибка конфигурации бота.", ephemeral=True)
        return

    # Проверка выполнения команды
    if not await restrict_command_execution(interaction, bot_client):
        return

    # Проверка доступа к каналу
    access_result, access_reason = await check_bot_access(interaction, bot_client)
    if not access_result:
        await interaction.response.send_message(
            access_reason or "Бот не имеет доступа к этому каналу.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    if interaction.guild is None:
        await interaction.followup.send("Команда не работает в ЛС.", ephemeral=True)
        return
    user_id = str(interaction.user.id)
    developer_ids = bot_client.config.DEVELOPER_ID
    developer_ids = [str(did) for did in (developer_ids if isinstance(developer_ids, list) else [developer_ids]) if did]
    if not (user_id in developer_ids or interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild):
        embed = discord.Embed(description="Требуются права разработчика, администратора или модератора.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    if not hasattr(bot_client, 'giveaways') or not hasattr(bot_client, 'completed_giveaways'):
        logger.error("BotClient не имеет атрибутов giveaways или completed_giveaways")
        embed = discord.Embed(description="Ошибка: функционал розыгрышей недоступен.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    try:
        duration = parse_duration(duration_str)
        if len(prize) > MAX_PRIZE_LENGTH:
            raise ValueError(f"Приз не должен превышать {MAX_PRIZE_LENGTH} символов.")
        if description and len(description) > MAX_DESC_LENGTH:
            raise ValueError(f"Описание не должно превышать {MAX_DESC_LENGTH} символов.")
        final_custom_id = generate_custom_id()
        while final_custom_id in bot_client.giveaways or final_custom_id in bot_client.completed_giveaways:
            final_custom_id = generate_custom_id()
        giveaway = Giveaway(
            prize=prize,
            duration=duration,
            host=interaction.user,
            channel=interaction.channel,
            custom_id=final_custom_id,
            description=description or DEFAULT_DESCRIPTION,
            giveaway_image=giveaway_image,
            winner_image=winner_image
        )
        bot_client.giveaways[final_custom_id] = giveaway
        embed = discord.Embed(
            title="🎉 Новый розыгрыш!",
            description=f"**Приз:** {giveaway.prize}\n**Описание:** {giveaway.description}",
            color=discord.Color.gold()
        )
        embed.add_field(name="Организатор:", value=f"<@{interaction.user.id}>", inline=True)
        embed.add_field(name="Заканчивается:", value=f"> <t:{giveaway.end_time}:R>", inline=True)
        embed.add_field(name="Дата окончания:", value=f"> <t:{giveaway.end_time}:F>", inline=True)
        embed.set_image(url=giveaway.giveaway_image or GIVEAWAY_IMAGE)
        embed.set_footer(text=f"ID ивента: {final_custom_id} | Участвуют: 0")
        view = GiveawayView(bot_client, final_custom_id, duration)
        message = await interaction.channel.send(embed=embed, view=view)
        giveaway.message_id = message.id
        await save_giveaways(bot_client.giveaways, bot_client.completed_giveaways, bot_client)
        embed = discord.Embed(description=f"Розыгрыш `{final_custom_id}` начат!", color=discord.Color.green())
        await interaction.followup.send(embed=embed, ephemeral=True)
        asyncio.create_task(run_giveaway_timer(bot_client, giveaway, duration * 60))
        asyncio.create_task(update_giveaway_message(bot_client, giveaway))
    except ValueError as e:
        embed = discord.Embed(description=str(e), color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        logger.error(f"Ошибка создания розыгрыша: {e}")
        embed = discord.Embed(description="Ошибка при создании розыгрыша.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)

async def reroll_giveaway(interaction: discord.Interaction, custom_id: str, bot_client) -> None:
    """Перевыбирает победителя розыгрыша."""
    if bot_client is None:
        logger.error("bot_client не предоставлен для команды /reroll")
        await interaction.response.send_message("Ошибка конфигурации бота.", ephemeral=True)
        return

    # Проверка выполнения команды
    if not await restrict_command_execution(interaction, bot_client):
        return

    # Проверка доступа к каналу
    access_result, access_reason = await check_bot_access(interaction, bot_client)
    if not access_result:
        await interaction.response.send_message(
            access_reason or "Бот не имеет доступа к этому каналу.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    if interaction.guild is None:
        await interaction.followup.send("Команда не работает в ЛС.", ephemeral=True)
        return
    user_id = str(interaction.user.id)
    developer_ids = bot_client.config.DEVELOPER_ID

    developer_ids = [str(did) for did in (developer_ids if isinstance(developer_ids, list) else [developer_ids]) if did]
    if not (user_id in developer_ids or interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild):
        embed = discord.Embed(description="Требуются права разработчика, администратора или модератора.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    if not hasattr(bot_client, 'giveaways') or not hasattr(bot_client, 'completed_giveaways'):
        logger.error("BotClient не имеет атрибутов giveaways или completed_giveaways")
        embed = discord.Embed(description="Ошибка: функционал розыгрышей недоступен.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    giveaway = bot_client.giveaways.get(custom_id) or bot_client.completed_giveaways.get(custom_id)
    if not giveaway:
        embed = discord.Embed(description="Розыгрыш не найден.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    if not giveaway.participants:
        embed = discord.Embed(description="Нет участников для реролла.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    try:
        old_winner = giveaway.winner
        available_participants = giveaway.participants - {old_winner} if old_winner else giveaway.participants
        if not available_participants:
            embed = discord.Embed(description="Нет доступных участников для реролла.", color=discord.Color.red())
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            giveaway.winner = random.choice(list(available_participants))
            giveaway.completed_at = int(time.time())
            bot_client.completed_giveaways[custom_id] = giveaway
            bot_client.giveaways.pop(custom_id, None)
            await save_giveaways(bot_client.giveaways, bot_client.completed_giveaways, bot_client)
            embed = discord.Embed(
                title="🎉 Реролл розыгрыша!",
                color=discord.Color.blue()
            )
            embed.add_field(name="Приз:", value=f"> `{giveaway.prize}`", inline=True)
            embed.add_field(name="Новый победитель:", value=f"> {giveaway.winner.mention}", inline=True)
            embed.add_field(name="Организатор:", value=f"<@{giveaway.host.id}>", inline=True)
            embed.set_image(url=giveaway.winner_image or WINNER_IMAGE)
            embed.set_footer(text=f"ID ивента: {custom_id} | Участвовали: {len(giveaway.participants)}")
            await interaction.followup.send(
                f"{giveaway.host.mention} {giveaway.winner.mention}\nРеролл! Новый победитель для **{giveaway.prize}** (`{custom_id}`)!",
                embed=embed
            )
            try:
                message = await giveaway.channel.fetch_message(giveaway.message_id)
                await message.delete()
                await giveaway.channel.send(content=f"{giveaway.host.mention} {giveaway.winner.mention}\n", embed=embed)
            except discord.NotFound:
                logger.warning(f"Сообщение розыгрыша {giveaway.message_id} не найдено при реролле")
                await giveaway.channel.send(content=f"{giveaway.host.mention} {giveaway.winner.mention}\n", embed=embed)
    except Exception as e:
        logger.error(f"Ошибка реролла розыгрыша {custom_id}: {e}")
        embed = discord.Embed(description="Ошибка при реролле.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)

async def edit_giveaway(
    interaction: discord.Interaction,
    custom_id: str,
    prize: Optional[str],
    duration_str: Optional[str],
    description: Optional[str],
    bot_client,
    giveaway_image: Optional[str] = None,
    winner_image: Optional[str] = None
) -> None:
    """Редактирует существующий розыгрыш."""
    if bot_client is None:
        logger.error("bot_client не предоставлен для команды /edit_giveaway")
        await interaction.response.send_message("Ошибка конфигурации бота.", ephemeral=True)
        return

    # Проверка выполнения команды
    if not await restrict_command_execution(interaction, bot_client):
        return

    # Проверка доступа к каналу
    access_result, access_reason = await check_bot_access(interaction, bot_client)
    if not access_result:
        await interaction.response.send_message(
            access_reason or "Бот не имеет доступа к этому каналу.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    if not custom_id:
        embed = discord.Embed(description="Кастомный ID обязателен.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    if interaction.guild is None:
        await interaction.followup.send("Команда не работает в ЛС.", ephemeral=True)
        return
    user_id = str(interaction.user.id)
    developer_ids = bot_client.config.DEVELOPER_ID
    developer_ids = [str(did) for did in (developer_ids if isinstance(developer_ids, list) else [developer_ids]) if did]
    if not (user_id in developer_ids or interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild):
        embed = discord.Embed(description="Требуются права разработчика, администратора или модератора.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    if not hasattr(bot_client, 'giveaways'):
        logger.error("BotClient не имеет атрибута giveaways")
        embed = discord.Embed(description="Ошибка: функционал розыгрышей недоступен.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    giveaway = bot_client.giveaways.get(custom_id)
    if not giveaway:
        embed = discord.Embed(description="Розыгрыш не найден или завершён.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    try:
        new_prize = prize if prize else giveaway.prize
        new_duration = parse_duration(duration_str) if duration_str else giveaway.duration
        new_description = description if description is not None else giveaway.description
        new_giveaway_image = giveaway_image if giveaway_image else giveaway.giveaway_image
        new_winner_image = winner_image if winner_image else giveaway.winner_image
        if len(new_prize) > MAX_PRIZE_LENGTH:
            raise ValueError(f"Приз не должен превышать {MAX_PRIZE_LENGTH} символов.")
        if new_description and len(new_description) > MAX_DESC_LENGTH:
            raise ValueError(f"Описание не должно превышать {MAX_DESC_LENGTH} символов.")
        giveaway.prize = new_prize
        giveaway.duration = new_duration
        giveaway.description = new_description
        giveaway.giveaway_image = new_giveaway_image
        giveaway.winner_image = new_winner_image
        giveaway.end_time = int(time.time()) + new_duration * 60
        embed = discord.Embed(
            title="🎉 Новый розыгрыш!",
            description=f"**Приз:** {giveaway.prize}\n**Описание:** {giveaway.description}",
            color=discord.Color.gold()
        )
        embed.add_field(name="Организатор:", value=f"<@{giveaway.host.id}>", inline=True)
        embed.add_field(name="Заканчивается:", value=f"> <t:{giveaway.end_time}:R>", inline=True)
        embed.add_field(name="Дата окончания:", value=f"> <t:{giveaway.end_time}:F>", inline=True)
        embed.set_image(url=giveaway.giveaway_image or GIVEAWAY_IMAGE)
        embed.set_footer(text=f"ID ивента: {giveaway.custom_id} | Участвуют: {len(giveaway.participants)}")
        message = await giveaway.channel.fetch_message(giveaway.message_id)
        view = GiveawayView(bot_client, custom_id, new_duration)
        await message.edit(embed=embed, view=view)
        await save_giveaways(bot_client.giveaways, bot_client.completed_giveaways, bot_client)
        embed = discord.Embed(description=f"Розыгрыш `{custom_id}` обновлён!", color=discord.Color.green())
        await interaction.followup.send(embed=embed, ephemeral=True)
    except ValueError as e:
        embed = discord.Embed(description=str(e), color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
    except discord.NotFound:
        embed = discord.Embed(description="Сообщение розыгрыша не найдено.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        logger.error(f"Ошибка редактирования розыгрыша {custom_id}: {e}")
        embed = discord.Embed(description="Ошибка при редактировании.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)

def generate_custom_id() -> str:
    """Генерирует уникальный кастомный ID."""
    characters = string.ascii_lowercase + string.digits
    return ''.join(random.choice(characters) for _ in range(5))

def create_command(bot_client):
    """Создаёт команды /giveaway, /reroll и /edit_giveaway."""
    @app_commands.command(name="giveaway", description="Создать новый розыгрыш")
    @app_commands.describe(
        prize="Приз розыгрыша",
        duration="Длительность (d-дни h-часы m-минуты, например: 1d 1h 30m)",
        description="Описание розыгрыша (опционально)",
        giveaway_image="Ссылка на изображение для розыгрыша (опционально)",
        winner_image="Ссылка на изображение для победителя (опционально)"
    )
    async def giveaway(
        interaction: discord.Interaction,
        prize: str,
        duration: str,
        description: Optional[str] = None,
        giveaway_image: Optional[str] = None,
        winner_image: Optional[str] = None
    ):
        await start_giveaway(interaction, prize, duration, description, bot_client, giveaway_image, winner_image)

    @app_commands.command(name="reroll", description="Перевыбрать победителя розыгрыша")
    @app_commands.describe(custom_id="Кастомный ID ивента")
    async def reroll(interaction: discord.Interaction, custom_id: str):
        await reroll_giveaway(interaction, custom_id, bot_client)

    @app_commands.command(name="edit_giveaway", description="Редактировать розыгрыш")
    @app_commands.describe(
        custom_id="Кастомный ID ивента",
        prize="Новый приз (опционально)",
        duration="Новая длительность (опционально, d-дни h-часы m-минуты)",
        description="Новое описание (опционально)",
        giveaway_image="Ссылка на новое изображение для розыгрыша (опционально)",
        winner_image="Ссылка на новое изображение для победителя (опционально)"
    )
    async def edit(
        interaction: discord.Interaction,
        custom_id: str,
        prize: Optional[str] = None,
        duration: Optional[str] = None,
        description: Optional[str] = None,
        giveaway_image: Optional[str] = None,
        winner_image: Optional[str] = None
    ):
        await edit_giveaway(interaction, custom_id, prize, duration, description, bot_client, giveaway_image, winner_image)

    return giveaway, reroll, edit