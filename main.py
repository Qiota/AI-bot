import discord
from discord import app_commands
from huggingface_hub import InferenceClient
import asyncio
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from dotenv import load_dotenv
import os
from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.route('/')
def home():
    return "Бот активен!"

def run_flask():
    app.run(host='0.0.0.0', port=8000)

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

class BotConfig:
    HF_API_TOKEN = os.getenv("HF_API_TOKEN")
    PROVIDER = os.getenv("PROVIDER", "novita")
    MODEL_NAME = os.getenv("MODEL_NAME", "deepseek-ai/DeepSeek-V3-0324")
    max_tokens = os.getenv("MAX_TOKENS", "2000")
    try:
        MAX_TOKENS = int(max_tokens)
    except ValueError:
        logger.error(f"Неверное значение MAX_TOKENS: {max_tokens}. Используется значение по умолчанию: 2000")
        MAX_TOKENS = 2000
    temperature = os.getenv("TEMPERATURE", "0.7")
    try:
        TEMPERATURE = float(temperature)
    except ValueError:
        logger.error(f"Неверное значение TEMPERATURE: {temperature}. Используется значение по умолчанию: 0.7")
        TEMPERATURE = 0.7
    TOKEN = os.getenv("DISCORD_TOKEN")
    developer_id = os.getenv("DEVELOPER_ID")
    try:
        DEVELOPER_ID = int(developer_id) if developer_id else None
    except (ValueError, TypeError):
        logger.error(f"Неверное значение DEVELOPER_ID: {developer_id}. Укажи корректный ID.")
        DEVELOPER_ID = None

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)
client = InferenceClient(provider=BotConfig.PROVIDER, api_key=BotConfig.HF_API_TOKEN)
executor = ThreadPoolExecutor(max_workers=1)

class MemoryManager:
    def __init__(self, max_size=10):
        self.memory = deque(maxlen=max_size)
    
    def add_message(self, role, content):
        self.memory.append({"role": role, "content": content})
    
    def get_context(self):
        return list(self.memory)

memory = MemoryManager()

def check_say_permissions():
    async def predicate(interaction):
        if isinstance(interaction.channel, discord.DMChannel):
            return True
        if interaction.user.id == BotConfig.DEVELOPER_ID:
            return True
        member = interaction.user
        if isinstance(member, discord.Member):
            has_admin = member.guild_permissions.administrator
            has_moderator = any(role.permissions.manage_channels or role.permissions.manage_messages for role in member.roles)
            return has_admin or has_moderator
        return False
    return app_commands.check(predicate)

async def update_presence():
    while True:
        ping = round(bot.latency * 1000)
        activity = discord.Activity(
            type=discord.ActivityType.streaming,
            name=f"пинг: {ping}мс"
        )
        await bot.change_presence(activity=activity)
        await asyncio.sleep(60)

async def generate_response(text):
    try:
        memory.add_message("user", text)
        start = time.time()
        completion = await asyncio.get_event_loop().run_in_executor(
            executor,
            lambda: client.chat.completions.create(
                model=BotConfig.MODEL_NAME,
                messages=memory.get_context(),
                max_tokens=BotConfig.MAX_TOKENS,
                temperature=BotConfig.TEMPERATURE
            )
        )
        response = completion.choices[0].message.content[:2000]
        memory.add_message("assistant", response)
        return response, round((time.time() - start) * 1000)
    except Exception as e:
        logger.error(f"API error: {e}")
        return f"Ошибка: {e}", 0

@bot.event
async def on_ready():
    logger.info(f"{bot.user} онлайн!")
    await tree.sync()
    logger.info("Команды синхронизированы")
    bot.loop.create_task(update_presence())

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    is_mentioned = bot.user in message.mentions
    is_reply_to_bot = message.reference and message.reference.resolved and message.reference.resolved.author == bot.user
    if is_mentioned or is_reply_to_bot or isinstance(message.channel, discord.DMChannel):
        async with message.channel.typing():
            content = message.content.replace(f"<@{bot.user.id}>", "").strip() if is_mentioned else message.content
            response, _ = await generate_response(content)
            if is_reply_to_bot:
                await message.reply(response)
            else:
                await message.channel.send(f"{message.author.mention} {response}")

@tree.command(name="info", description="Информация о боте")
async def bot_info(interaction):
    await interaction.response.defer(thinking=True, ephemeral=True)
    embed = discord.Embed(
        title="Информация о боте",
        description="Подробная информация о боте",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=bot.user.avatar.url if bot.user.avatar else discord.Embed.Empty)
    embed.add_field(name="Имя", value=bot.user.name, inline=True)
    embed.add_field(name="ID", value=str(bot.user.id), inline=True)
    embed.add_field(name="Модель", value=BotConfig.MODEL_NAME, inline=True)
    embed.add_field(name="Провайдер", value=BotConfig.PROVIDER, inline=True)
    embed.add_field(name="Пинг", value=f"{round(bot.latency * 1000)}мс", inline=True)
    embed.add_field(name="Серверы", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="Память", value=f"{len(memory.get_context())}/10", inline=True)
    embed.set_footer(text=f"Запросил {interaction.user.name}", icon_url=interaction.user.avatar.url if interaction.user.avatar else None)
    await interaction.followup.send(embed=embed)

@tree.command(name="say", description="Сказать что-то от имени бота")
@app_commands.describe(message="Сообщение, которое бот отправит")
@check_say_permissions()
async def say(interaction, message: str):
    await interaction.response.defer(ephemeral=True)
    await interaction.channel.send(message)
    await interaction.delete_original_response()

async def main():
    flask_thread = Thread(target=run_flask)
    flask_thread.start()
    await bot.start(BotConfig.TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Выключение пользователем")
    finally:
        executor.shutdown(wait=False)