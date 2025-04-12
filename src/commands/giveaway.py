import discord
from discord import app_commands, ui
from ..config import logger
import random
import time
import asyncio
import aiofiles
import json
import os
import string
from datetime import datetime, timedelta

description = "Создать розыгрыш через слеш-команду с кнопкой участия"

GIVEAWAYS_FILE = "giveaways.json"
COMPLETED_GIVEAWAYS_FILE = "completed_giveaways.json"
COMPLETED_GIVEAWAY_RETENTION = 7 * 24 * 60 * 60  # 7 days in seconds
MAX_PRIZE_LENGTH = 100
MAX_DESC_LENGTH = 500
MIN_DURATION = 1
MAX_DURATION = 10080
DEFAULT_DESCRIPTION = "Нажмите на кнопку ниже, чтобы принять участие в розыгрыше!"
GIVEAWAY_IMAGE = "https://i.postimg.cc/vHtwYT81/giveaway.png"
WINNER_IMAGE = "https://i.postimg.cc/jjSrDb3s/winner.jpg"

def format_end_time(end_time):
    dt = datetime.fromtimestamp(end_time)
    return dt.strftime("%d.%m.%Y в %H:%M")

def parse_duration(duration_str):
    if not duration_str:
        raise ValueError("Длительность не указана")
    total_minutes = 0
    parts = duration_str.lower().replace(" ", "").replace("-", "")
    current_number = ""
    for char in parts:
        if char.isdigit():
            current_number += char
        elif char in "dm":
            if not current_number:
                raise ValueError("Неверный формат длительности. Используйте d-дни m-минуты")
            value = int(current_number)
            if char == "d":
                total_minutes += value * 24 * 60
            elif char == "m":
                total_minutes += value
            current_number = ""
        else:
            raise ValueError("Неверный формат длительности. Используйте d-дни m-минуты")
    if current_number:
        raise ValueError("Неверный формат длительности. Используйте d-дни m-минуты")
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
        await interaction.response.defer(ephemeral=True)
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
            embed = discord.Embed(description="Вы уже участвуете!", color=discord.Color.orange())
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        giveaway.participants.add(interaction.user)
        try:
            await save_giveaways(self.bot_client.giveaways, self.bot_client.completed_giveaways)
            embed = discord.Embed(
                title="🎉 Новый розыгрыш!",
                description=f"**Приз:** {giveaway.prize}\n**Описание:** {giveaway.description}",
                color=discord.Color.gold()
            )
            embed.add_field(name="Длительность", value=f"{giveaway.duration} минут", inline=True)
            embed.add_field(name="Организатор", value=giveaway.host.name, inline=True)
            embed.add_field(name="Заканчивается", value=format_end_time(giveaway.end_time), inline=True)
            embed.set_image(url=GIVEAWAY_IMAGE)
            embed.set_footer(text=f"ID ивента: {self.custom_id} | Участвуют: {len(giveaway.participants)}")
            message = await giveaway.channel.fetch_message(giveaway.message_id)
            await message.edit(embed=embed)
        except Exception as e:
            logger.error(f"Ошибка при участии в розыгрыше {self.custom_id}: {e}")
            embed = discord.Embed(description="Ошибка при записи участия.", color=discord.Color.red())
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        embed = discord.Embed(description="Вы успешно участвуете в розыгрыше!", color=discord.Color.green())
        await interaction.followup.send(embed=embed, ephemeral=True)
        logger.info(f"Пользователь {interaction.user.id} участвует в розыгрыше {self.custom_id}")

    @ui.button(label="Список участников", style=discord.ButtonStyle.blurple)
    async def show_participants(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
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
        participants = sorted([user.name for user in giveaway.participants])
        if not participants:
            embed = discord.Embed(description="Нет участников.", color=discord.Color.orange())
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        chunk_size = 25
        participant_chunks = [participants[i:i + chunk_size] for i in range(0, len(participants), chunk_size)]
        for i, chunk in enumerate(participant_chunks, 1):
            embed = discord.Embed(
                title=f"Участники розыгрыша ID: `{self.custom_id}` ({i})",
                description="\n".join(f"{idx + 1}. {name}" for idx, name in enumerate(chunk, (i-1)*chunk_size)),
                color=discord.Color.blue()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

class Giveaway:
    def __init__(self, prize, duration, host, channel, custom_id, description, message_id=None, end_time=None, participants=None, completed_at=None):
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
            "participant_ids": [user.id for user in self.participants],
            "winner_id": self.winner.id if self.winner else None,
            "completed_at": self.completed_at
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
                completed_at=data.get("completed_at")
            )
            if data.get("winner_id"):
                giveaway.winner = guild.get_member(data["winner_id"])
            return giveaway
        except Exception as e:
            logger.error(f"Ошибка восстановления розыгрыша {data.get('custom_id', 'unknown')}: {e}")
            return None

async def save_giveaways(giveaways, completed_giveaways):
    try:
        data = {custom_id: giveaway.to_dict() for custom_id, giveaway in giveaways.items()}
        async with aiofiles.open(GIVEAWAYS_FILE, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=2))
        logger.debug(f"Активные розыгрыши сохранены в {GIVEAWAYS_FILE}")
        completed_data = {custom_id: giveaway.to_dict() for custom_id, giveaway in completed_giveaways.items()}
        async with aiofiles.open(COMPLETED_GIVEAWAYS_FILE, "w", encoding="utf-8") as f:
            await f.write(json.dumps(completed_data, ensure_ascii=False, indent=2))
        logger.debug(f"Завершённые розыгрыши сохранены в {COMPLETED_GIVEAWAYS_FILE}")
    except Exception as e:
        logger.error(f"Ошибка сохранения розыгрышей: {e}")

async def load_giveaways(bot_client):
    active_giveaways = {}
    completed_giveaways = {}
    if os.path.exists(GIVEAWAYS_FILE):
        try:
            async with aiofiles.open(GIVEAWAYS_FILE, "r", encoding="utf-8") as f:
                data = json.loads(await f.read())
            for custom_id, giveaway_data in data.items():
                giveaway = Giveaway.from_dict(giveaway_data, bot_client)
                if giveaway:
                    active_giveaways[custom_id] = giveaway
                else:
                    logger.warning(f"Пропущен невалидный розыгрыш {custom_id}")
        except Exception as e:
            logger.error(f"Ошибка загрузки активных розыгрышей: {e}")
    if os.path.exists(COMPLETED_GIVEAWAYS_FILE):
        try:
            async with aiofiles.open(COMPLETED_GIVEAWAYS_FILE, "r", encoding="utf-8") as f:
                data = json.loads(await f.read())
            current_time = int(time.time())
            for custom_id, giveaway_data in data.items():
                completed_at = giveaway_data.get("completed_at", 0)
                if current_time - completed_at <= COMPLETED_GIVEAWAY_RETENTION:
                    giveaway = Giveaway.from_dict(giveaway_data, bot_client)
                    if giveaway:
                        completed_giveaways[custom_id] = giveaway
                    else:
                        logger.warning(f"Пропущен невалидный завершённый розыгрыш {custom_id}")
        except Exception as e:
            logger.error(f"Ошибка загрузки завершённых розыгрышей: {e}")
    logger.info(f"Загружено {len(active_giveaways)} активных и {len(completed_giveaways)} завершённых розыгрышей")
    return active_giveaways, completed_giveaways

async def resume_giveaways(bot_client):
    bot_client.giveaways, bot_client.completed_giveaways = await load_giveaways(bot_client)
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
    await interaction.response.defer(ephemeral=True)
    if interaction.guild is None:
        await interaction.followup.send("Команда не работает в ЛС.", ephemeral=True)
        logger.warning(f"Пользователь {interaction.user.id} попытался вызвать команду в DM")
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
        await interaction.followup.send(embed=embed, ephemeral=True)
        logger.warning(f"Пользователь {user_id} попытался создать розыгрыш без прав")
        return
    try:
        duration = parse_duration(duration_str)
    except ValueError as e:
        embed = discord.Embed(description=str(e), color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    if len(prize) > MAX_PRIZE_LENGTH:
        embed = discord.Embed(description=f"Приз не должен превышать {MAX_PRIZE_LENGTH} символов.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    if description and len(description) > MAX_DESC_LENGTH:
        embed = discord.Embed(description=f"Описание не должно превышать {MAX_DESC_LENGTH} символов.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    final_custom_id = custom_id or generate_custom_id()
    while final_custom_id in bot_client.giveaways or final_custom_id in bot_client.completed_giveaways:
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
        await save_giveaways(bot_client.giveaways, bot_client.completed_giveaways)
    except discord.HTTPException as e:
        logger.error(f"Ошибка отправки сообщения розыгрыша {final_custom_id}: {e}")
        embed = discord.Embed(description="Ошибка при создании розыгрыша.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    embed = discord.Embed(description=f"Розыгрыш `{final_custom_id}` начат!", color=discord.Color.green())
    await interaction.followup.send(embed=embed, ephemeral=True)
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
            content = f"{host_mention} {winner_mention}\n"
            await message.edit(content=content, embed=embed, view=view)
            logger.info(f"Розыгрыш {giveaway.custom_id} завершён. Победитель: {getattr(giveaway.winner, 'id', 'unknown')}")
        giveaway.completed_at = int(time.time())
        bot_client.completed_giveaways[giveaway.custom_id] = giveaway
    except discord.NotFound:
        logger.error(f"Сообщение розыгрыша {giveaway.message_id} не найдено")
    except Exception as e:
        logger.error(f"Ошибка завершения розыгрыша {giveaway.custom_id}: {e}")
    finally:
        bot_client.giveaways.pop(giveaway.custom_id, None)
        await save_giveaways(bot_client.giveaways, bot_client.completed_giveaways)

async def reroll_giveaway(interaction, custom_id, bot_client):
    await interaction.response.defer(ephemeral=True)
    if interaction.guild is None:
        await interaction.followup.send("Команда не работает в ЛС.", ephemeral=True)
        logger.warning(f"Пользователь {interaction.user.id} попытался вызвать команду в DM")
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
        await interaction.followup.send(embed=embed, ephemeral=True)
        logger.warning(f"Пользователь {interaction.user.id} попытался реролл без прав")
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
            return
        giveaway.winner = random.choice(list(available_participants))
        giveaway.completed_at = int(time.time())
        bot_client.completed_giveaways[custom_id] = giveaway
        await save_giveaways(bot_client.giveaways, bot_client.completed_giveaways)
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
        await interaction.followup.send(
            f"{host_mention} {winner_mention}\n"
            f"Реролл! Новый победитель для **{giveaway.prize}** (`{custom_id}`)!",
            embed=embed
        )
        try:
            message = await giveaway.channel.fetch_message(giveaway.message_id)
            await message.edit(content=f"{host_mention} {winner_mention}\n", embed=embed)
        except discord.NotFound:
            logger.warning(f"Сообщение розыгрыша {giveaway.message_id} не найдено при реролле")
        logger.info(f"Реролл розыгрыша {custom_id} пользователем {interaction.user.id}. Новый победитель: {getattr(giveaway.winner, 'id', 'unknown')}")
    except Exception as e:
        logger.error(f"Ошибка реролла розыгрыша {custom_id}: {e}")
        embed = discord.Embed(description="Ошибка при реролле.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)

async def edit_giveaway(interaction, custom_id, prize, duration_str, description, bot_client):
    await interaction.response.defer(ephemeral=True)
    if interaction.guild is None:
        await interaction.followup.send("Команда не работает в ЛС.", ephemeral=True)
        logger.warning(f"Пользователь {interaction.user.id} попытался вызвать команду в DM")
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
        await interaction.followup.send(embed=embed, ephemeral=True)
        logger.warning(f"Пользователь {interaction.user.id} попытался редактировать без прав")
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
        if len(new_prize) > MAX_PRIZE_LENGTH:
            embed = discord.Embed(description=f"Приз не должен превышать {MAX_PRIZE_LENGTH} символов.", color=discord.Color.red())
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        if new_description and len(new_description) > MAX_DESC_LENGTH:
            embed = discord.Embed(description=f"Описание не должно превышать {MAX_DESC_LENGTH} символов.", color=discord.Color.red())
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        giveaway.prize = new_prize
        giveaway.duration = new_duration
        giveaway.description = new_description
        giveaway.end_time = int(time.time()) + new_duration * 60
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
        view = GiveawayView(bot_client, custom_id, new_duration)
        await message.edit(embed=embed, view=view)
        await save_giveaways(bot_client.giveaways, bot_client.completed_giveaways)
        embed = discord.Embed(description=f"Розыгрыш `{custom_id}` обновлён!", color=discord.Color.green())
        await interaction.followup.send(embed=embed, ephemeral=True)
        logger.info(f"Розыгрыш {custom_id} обновлён пользователем {interaction.user.id}")
    except ValueError as e:
        embed = discord.Embed(description=str(e), color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
    except discord.NotFound:
        logger.error(f"Сообщение розыгрыша {giveaway.message_id} не найдено")
        embed = discord.Embed(description="Сообщение розыгрыша не найдено.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        logger.error(f"Ошибка редактирования розыгрыша {custom_id}: {e}")
        embed = discord.Embed(description="Ошибка при редактировании.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)

def create_command(bot_client):
    @app_commands.command(name="giveaway", description="Создать новый розыгрыш")
    @app_commands.describe(
        prize="Приз розыгрыша",
        duration="Длительность (d-дни m-минуты, например: 1d 30m)",
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
        prize="Новый приз (опционально)",
        duration="Новая длительность (опционально, d-дни m-минуты)",
        description="Новое описание (опционально)"
    )
    async def edit(interaction: discord.Interaction, custom_id: str, prize: str = None, duration: str = None, description: str = None):
        await edit_giveaway(interaction, custom_id, prize, duration, description, bot_client)

    return giveaway, reroll, edit