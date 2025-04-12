import discord
from discord import app_commands, ui
from discord.ui import Button
from ..config import logger
import random
import time
import asyncio
import aiofiles
import json
import os
import string
from datetime import datetime

description = "Создать розыгрыш через слеш-команду с кнопкой участия"

GIVEAWAYS_FILE = "giveaways.json"
MAX_PRIZE_LENGTH = 100
MAX_DESC_LENGTH = 500
MIN_DURATION = 1
MAX_DURATION = 10080
DEFAULT_DESCRIPTION = "Нажмите на кнопку ниже, чтобы принять участие в розыгрыше!"
GIVEAWAY_IMAGE = "https://i.postimg.cc/vHtwYT81/giveaway.png"
WINNER_IMAGE = "https://i.postimg.cc/jjSrDb3s/winner.jpg"

def format_end_time(end_time):
    """Форматирует время окончания в ДД.ММ.ГГГГ в ЧЧ:ММ"""
    dt = datetime.fromtimestamp(end_time)
    return dt.strftime("%d.%m.%Y в %H:%M")

def parse_duration(duration_str):
    """Парсит строку длительности в формате 'd-дни m-минуты s-секунды' и возвращает минуты"""
    if not duration_str:
        raise ValueError("Длительность не указана")

    total_seconds = 0
    parts = duration_str.lower().replace(" ", "").replace("-", "")
    
    current_number = ""
    for char in parts:
        if char.isdigit():
            current_number += char
        elif char in "dms":
            if not current_number:
                raise ValueError("Неверный формат длительности. Используйте d-дни m-минуты s-секунды")
            value = int(current_number)
            if char == "d":
                total_seconds += value * 24 * 60 * 60  # дни в секунды
            elif char == "m":
                total_seconds += value * 60  # минуты в секунды
            elif char == "s":
                total_seconds += value  # секунды
            current_number = ""
        else:
            raise ValueError("Неверный формат длительности. Используйте d-дни m-минуты s-секунды")

    if current_number:
        raise ValueError("Неверный формат длительности. Используйте d-дни m-минуты s-секунды")

    total_minutes = total_seconds // 60
    if total_minutes < MIN_DURATION or total_minutes > MAX_DURATION:
        raise ValueError(f"Длительность должна быть от {MIN_DURATION} до {MAX_DURATION} минут")
    return total_minutes

class GiveawayView(ui.View):
    def __init__(self, bot_client, custom_id, duration):
        super().__init__(timeout=duration * 60)
        self.bot_client = bot_client
        self.custom_id = custom_id

    @ui.button(label="Участвовать", style=discord.ButtonStyle.green, emoji="🎉")
    async def participate(self, interaction, button):
        giveaway = self.bot_client.giveaways.get(self.custom_id)
        if not giveaway:
            embed = discord.Embed(description="Розыгрыш завершён или не существует.", color=discord.Color.red())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        current_time = int(time.time())
        if current_time >= giveaway.end_time:
            embed = discord.Embed(description="Розыгрыш уже завершён!", color=discord.Color.red())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if interaction.user in giveaway.participants:
            embed = discord.Embed(description="Вы уже участвуете!", color=discord.Color.orange())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        giveaway.participants.add(interaction.user)
        try:
            await save_giveaways(self.bot_client.giveaways)
            embed = discord.Embed(
                title="🎉 Новый розыгрыш!",
                description=f"**Приз:** {giveaway.prize}\n**Описание:** {giveaway.description}",
                color=discord.Color.gold()
            )
            embed.add_field(name="Длительность", value=f"{giveaway.duration} минут", inline=True)
            embed.add_field(name="Организатор", value=giveaway.host.name, inline=True)
            embed.add_field(name="Заканчивается", value=format_end_time(giveaway.end_time), inline=True)
            embed.set_image(url=GIVEAWAY_IMAGE)
            embed.set_footer(text=f"ID ивента: {giveaway.custom_id} | Участвуют: {len(giveaway.participants)}")
            message = await giveaway.channel.fetch_message(giveaway.message_id)
            await message.edit(embed=embed)
        except Exception as e:
            logger.error(f"Ошибка при участии в розыгрыше {self.custom_id}: {e}")
            embed = discord.Embed(description="Ошибка при записи участия.", color=discord.Color.red())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(description="Вы успешно участвуете в розыгрыше!", color=discord.Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.info(f"Пользователь {interaction.user.id} участвует в розыгрыше {self.custom_id}")

class Giveaway:
    def __init__(self, prize, duration, host, channel, custom_id, description, message_id=None, end_time=None, participants=None):
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

    def to_dict(self):
        return {
            "prize": self.prize,
            "duration": self.duration,
            "host_id": self.host.id,
            "channel_id": self.channel.id,
            "custom_id": self.custom_id,
            "description": self.description,
            "message_id": self.message_id,
            "end_time": self.end_time,
            "participant_ids": [user.id for user in self.participants]
        }

    @classmethod
    def from_dict(cls, data, bot_client):
        try:
            guild = bot_client.bot.get_guild(data["channel_id"] // 1000000)
            if not guild:
                logger.warning(f"Гильдия для канала {data['channel_id']} не найдена")
                return None
            channel = guild.get_channel(data["channel_id"])
            host = guild.get_member(data["host_id"])
            if not channel or not host:
                logger.warning(f"Канал {data['channel_id']} или хост {data['host_id']} не найдены")
                return None
            participants = {guild.get_member(user_id) for user_id in data.get("participant_ids", [])}
            participants.discard(None)
            return cls(
                prize=data["prize"],
                duration=data["duration"],
                host=host,
                channel=channel,
                custom_id=data["custom_id"],
                description=data["description"],
                message_id=data["message_id"],
                end_time=data["end_time"],
                participants=participants
            )
        except Exception as e:
            logger.error(f"Ошибка восстановления розыгрыша {data.get('custom_id', 'unknown')}: {e}")
            return None

async def save_giveaways(giveaways):
    try:
        data = {custom_id: giveaway.to_dict() for custom_id, giveaway in giveaways.items()}
        async with aiofiles.open(GIVEAWAYS_FILE, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=2))
        logger.debug(f"Розыгрыши сохранены в {GIVEAWAYS_FILE}")
    except Exception as e:
        logger.error(f"Ошибка сохранения розыгрышей в {GIVEAWAYS_FILE}: {e}")

async def load_giveaways(bot_client):
    if not os.path.exists(GIVEAWAYS_FILE):
        logger.info(f"Файл {GIVEAWAYS_FILE} не найден, начинаем с пустого списка")
        return {}
    try:
        async with aiofiles.open(GIVEAWAYS_FILE, "r", encoding="utf-8") as f:
            data = json.loads(await f.read())
        giveaways = {}
        for custom_id, giveaway_data in data.items():
            giveaway = Giveaway.from_dict(giveaway_data, bot_client)
            if giveaway:
                giveaways[custom_id] = giveaway
            else:
                logger.warning(f"Пропущен невалидный розыгрыш {custom_id}")
        logger.info(f"Загружено {len(giveaways)} розыгрышей из {GIVEAWAYS_FILE}")
        return giveaways
    except Exception as e:
        logger.error(f"Ошибка загрузки розыгрышей из {GIVEAWAYS_FILE}: {e}")
        return {}

async def resume_giveaways(bot_client):
    bot_client.giveaways = await load_giveaways(bot_client)
    current_time = int(time.time())
    for custom_id, giveaway in list(bot_client.giveaways.items()):
        if giveaway.end_time > current_time:
            remaining_time = giveaway.end_time - current_time
            logger.debug(f"Возобновление розыгрыша {custom_id} с оставшимся временем {remaining_time} секунд")
            asyncio.create_task(run_giveaway_timer(bot_client, giveaway, remaining_time))
        else:
            logger.info(f"Розыгрыш {custom_id} истёк, завершаем")
            asyncio.create_task(end_giveaway(bot_client, giveaway))

async def run_giveaway_timer(bot_client, giveaway, remaining_time):
    try:
        await asyncio.sleep(remaining_time)
        await end_giveaway(bot_client, giveaway)
    except asyncio.CancelledError:
        logger.info(f"Таймер розыгрыша {giveaway.custom_id} отменён")
    except Exception as e:
        logger.error(f"Ошибка таймера розыгрыша {giveaway.custom_id}: {e}")

def generate_custom_id():
    characters = string.ascii_lowercase + string.digits
    random_part = ''.join(random.choice(characters) for _ in range(5))
    return random_part

async def start_giveaway(interaction, prize, duration_str, custom_id, description, bot_client):
    if interaction.guild is None:
        embed = discord.Embed(description="Эту команду нельзя использовать в личных сообщениях.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    user_id = str(interaction.user.id)
    developer_ids = bot_client.config.DEVELOPER_ID
    if not isinstance(developer_ids, list):
        developer_ids = [str(developer_ids)] if developer_ids else []
    else:
        developer_ids = [str(did) for did in developer_ids]

    is_developer = user_id in developer_ids
    is_admin = interaction.user.guild_permissions.administrator
    is_moderator = interaction.user.guild_permissions.manage_guild

    if not (is_developer or is_admin or is_moderator):
        embed = discord.Embed(description="Требуются права разработчика, администратора или модератора.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.warning(f"Пользователь {user_id} попытался создать розыгрыш без прав")
        return

    try:
        duration = parse_duration(duration_str)
    except ValueError as e:
        embed = discord.Embed(description=str(e), color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if len(prize) > MAX_PRIZE_LENGTH:
        embed = discord.Embed(description=f"Приз не должен превышать {MAX_PRIZE_LENGTH} символов.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    if description and len(description) > MAX_DESC_LENGTH:
        embed = discord.Embed(description=f"Описание не должно превышать {MAX_DESC_LENGTH} символов.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    final_custom_id = custom_id or generate_custom_id()
    while final_custom_id in bot_client.giveaways:
        final_custom_id = generate_custom_id()

    giveaway = Giveaway(
        prize=prize,
        duration=duration,
        host=interaction.user,
        channel=interaction.channel,
        custom_id=final_custom_id,
        description=description or DEFAULT_DESCRIPTION
    )
    bot_client.giveaways[final_custom_id] = giveaway

    try:
        embed = discord.Embed(
            title="🎉 Новый розыгрыш!",
            description=f"**Приз:** {giveaway.prize}\n**Описание:** {giveaway.description}",
            color=discord.Color.gold()
        )
        embed.add_field(name="Длительность", value=f"{duration} минут", inline=True)
        embed.add_field(name="Организатор", value=interaction.user.name, inline=True)
        embed.add_field(name="Заканчивается", value=format_end_time(giveaway.end_time), inline=True)
        embed.set_image(url=GIVEAWAY_IMAGE)
        embed.set_footer(text=f"ID ивента: {final_custom_id} | Участвуют: 0")
        view = GiveawayView(bot_client, final_custom_id, duration)
        message = await interaction.channel.send(embed=embed, view=view)
        giveaway.message_id = message.id
        bot_client.giveaways[final_custom_id] = giveaway
        await save_giveaways(bot_client.giveaways)
    except discord.HTTPException as e:
        logger.error(f"Ошибка отправки сообщения розыгрыша {final_custom_id}: {e}")
        embed = discord.Embed(description="Ошибка при создания розыгрыша.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    embed = discord.Embed(description=f"Розыгрыш `{final_custom_id}` начат!", color=discord.Color.green())
    await interaction.response.send_message(embed=embed, ephemeral=True)
    logger.info(f"Розыгрыш {final_custom_id} начат пользователем {interaction.user.id}: {prize}")

    asyncio.create_task(run_giveaway_timer(bot_client, giveaway, duration * 60))

async def end_giveaway(bot_client, giveaway):
    try:
        message = await giveaway.channel.fetch_message(giveaway.message_id)
        if not giveaway.participants:
            embed = discord.Embed(
                title="Розыгрыш завершён",
                description=f"**Приз:** {giveaway.prize}\n**Никто не участвовал!**",
                color=discord.Color.red()
            )
            embed.add_field(name="Длительность", value=f"{giveaway.duration} минут", inline=True)
            embed.add_field(name="Организатор", value=giveaway.host.name, inline=True)
            embed.set_image(url=GIVEAWAY_IMAGE)
            embed.set_footer(text=f"ID ивента: {giveaway.custom_id} | Участвуют: 0")
            view = discord.ui.View()
            view.clear_items()
            await message.edit(embed=embed, view=view)
            logger.info(f"Розыгрыш {giveaway.custom_id} завершён без участников")
        else:
            giveaway.winner = random.choice(list(giveaway.participants))
            embed = discord.Embed(
                title="🎉 Розыгрыш завершён!",
                color=discord.Color.green()
            )
            embed.add_field(name="Приз", value=giveaway.prize, inline=True)
            embed.add_field(
                name="Победитель",
                value=giveaway.winner.mention if giveaway.winner else "Неизвестный победитель",
                inline=True
            )
            embed.add_field(
                name="Организатор",
                value=giveaway.host.name if giveaway.host else "Неизвестный организатор",
                inline=True
            )
            embed.set_image(url=WINNER_IMAGE)
            embed.set_footer(text=f"ID ивента: {giveaway.custom_id} | Участвовали: {len(giveaway.participants)}")
            view = discord.ui.View()
            view.clear_items()

            host_mention = giveaway.host.mention if giveaway.host else "Организатор"
            winner_mention = giveaway.winner.mention if giveaway.winner else "Победитель"
            content = (
                f"{host_mention} {winner_mention}\n"
            )
            await message.edit(content=content, embed=embed, view=view)
            logger.info(f"Розыгрыш {giveaway.custom_id} завершён. Победитель: {getattr(giveaway.winner, 'id', 'unknown')}")
    except discord.NotFound:
        logger.error(f"Сообщение розыгрыша {giveaway.message_id} не найдено")
    except Exception as e:
        logger.error(f"Ошибка завершения розыгрыша {giveaway.custom_id}: {e}")
    finally:
        bot_client.giveaways.pop(giveaway.custom_id, None)
        await save_giveaways(bot_client.giveaways)

async def reroll_giveaway(interaction, custom_id, bot_client):
    if interaction.guild is None:
        embed = discord.Embed(description="Эту команду нельзя использовать в личных сообщениях.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild):
        embed = discord.Embed(description="Требуются права администратора или модератора.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.warning(f"Пользователь {interaction.user.id} попытался реролл без прав")
        return

    giveaway = bot_client.giveaways.get(custom_id)
    if not giveaway:
        embed = discord.Embed(description="Розыгрыш не найден или завершён.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if not giveaway.participants:
        embed = discord.Embed(description="Нет участников для реролла.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    try:
        old_winner = giveaway.winner
        giveaway.winner = random.choice(list(giveaway.participants - {old_winner}))
        await save_giveaways(bot_client.giveaways)
        embed = discord.Embed(
            title="🎉 Реролл розыгрыша!",
            color=discord.Color.blue()
        )
        embed.add_field(name="Приз", value=giveaway.prize, inline=True)
        embed.add_field(
            name="Новый победитель",
            value=giveaway.winner.mention if giveaway.winner else "Неизвестный победитель",
            inline=True
        )
        embed.add_field(
            name="Организатор",
            value=giveaway.host.name if giveaway.host else "Неизвестный организатор",
            inline=True
        )
        embed.set_image(url=WINNER_IMAGE)
        embed.set_footer(text=f"ID ивента: {custom_id} | Участвовали: {len(giveaway.participants)}")
        host_mention = giveaway.host.mention if giveaway.host else "Организатор"
        winner_mention = giveaway.winner.mention if giveaway.winner else "Победитель"
        await interaction.response.send_message(
            f"{host_mention} {winner_mention}\n"
            f"Реролл! Новый победитель для **{giveaway.prize}** (`{custom_id}`)!",
            embed=embed
        )
        logger.info(f"Реролл розыгрыша {custom_id} пользователем {interaction.user.id}. Новый победитель: {getattr(giveaway.winner, 'id', 'unknown')}")
    except Exception as e:
        logger.error(f"Ошибка реролла розыгрыша {custom_id}: {e}")
        embed = discord.Embed(description="Ошибка при реролле.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def edit_giveaway(interaction, custom_id, prize, duration_str, description, bot_client):
    if interaction.guild is None:
        embed = discord.Embed(description="Эту команду нельзя использовать в личных сообщениях.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild):
        embed = discord.Embed(description="Требуются права администратора или модератора.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.warning(f"Пользователь {interaction.user.id} попытался редактировать без прав")
        return

    giveaway = bot_client.giveaways.get(custom_id)
    if not giveaway:
        embed = discord.Embed(description="Розыгрыш не найден или завершён.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    try:
        duration = parse_duration(duration_str)
    except ValueError as e:
        embed = discord.Embed(description=str(e), color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if len(prize) > MAX_PRIZE_LENGTH:
        embed = discord.Embed(description=f"Приз не должен превышать {MAX_PRIZE_LENGTH} символов.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    if description and len(description) > MAX_DESC_LENGTH:
        embed = discord.Embed(description=f"Описание не должно превышать {MAX_DESC_LENGTH} символов.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    try:
        giveaway.prize = prize
        giveaway.duration = duration
        giveaway.description = description or DEFAULT_DESCRIPTION
        giveaway.end_time = int(time.time()) + duration * 60
        bot_client.giveaways[custom_id] = giveaway

        embed = discord.Embed(
            title="🎉 Розыгрыш обновлён!",
            description=f"**Приз:** {giveaway.prize}\n**Описание:** {giveaway.description}",
            color=discord.Color.gold()
        )
        embed.add_field(name="Длительность", value=f"{giveaway.duration} минут", inline=True)
        embed.add_field(name="Организатор", value=giveaway.host.name, inline=True)
        embed.add_field(name="Заканчивается", value=format_end_time(giveaway.end_time), inline=True)
        embed.set_image(url=GIVEAWAY_IMAGE)
        embed.set_footer(text=f"ID ивента: {giveaway.custom_id} | Участвуют: {len(giveaway.participants)}")
        message = await giveaway.channel.fetch_message(giveaway.message_id)
        await message.edit(embed=embed)

        await save_giveaways(bot_client.giveaways)
        embed = discord.Embed(description=f"Розыгрыш `{custom_id}` обновлён!", color=discord.Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.info(f"Розыгрыш {custom_id} обновлён пользователем {interaction.user.id}")
    except discord.NotFound:
        logger.error(f"Сообщение розыгрыша {giveaway.message_id} не найдено")
        embed = discord.Embed(description="Сообщение розыгрыша не найдено.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        logger.error(f"Ошибка редактирования розыгрыша {custom_id}: {e}")
        embed = discord.Embed(description="Ошибка при редактировании.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

def create_command(bot_client):
    @app_commands.command(name="giveaway", description="Создать новый розыгрыш")
    @app_commands.describe(
        prize="Приз розыгрыша",
        duration="Длительность (d-дни m-минуты s-секунды, например: 1d 30m 15s)",
        custom_id="Уникальный ID ивента (опционально)",
        description="Описание розыгрыша (опционально)"
    )
    async def giveaway(interaction: discord.Interaction, prize: str, duration: str, custom_id: str = None, description: str = None):
        await start_giveaway(interaction, prize, duration, custom_id, description, bot_client)

    @app_commands.command(name="reroll", description="Перевыбрать победителя розыгрыша")
    @app_commands.describe(custom_id="Кастомный ID ивента")
    async def reroll(interaction: discord.Interaction, custom_id: str):
        await reroll_giveaway(interaction, custom_id, bot_client)

    @app_commands.command(name="edit_giveaway", description="Редактировать розыгрыш")
    @app_commands.describe(
        custom_id="Кастомный ID ивента",
        prize="Новый приз",
        duration="Новая длительность (d-дни m-минуты s-секунды, например: 1d 30m 15s)",
        description="Новое описание (опционально)"
    )
    async def edit(interaction: discord.Interaction, custom_id: str, prize: str, duration: str, description: str = None):
        await edit_giveaway(interaction, custom_id, prize, duration, description, bot_client)

    return giveaway, reroll, edit