import discord
from discord import app_commands, File
from g4f.client import AsyncClient
from g4f.Provider import ImageLabs
from io import BytesIO
import aiohttp
from asyncio import Lock
from time import time
from ..config import logger

description = "Генерирует изображение по описанию"
command_lock = Lock()
last_execution = 0
COOLDOWN = 5

ASPECT_RATIOS = {
    "1:1": (512, 512), "16:9": (896, 512), "9:16": (512, 896),
    "21:9": (1072, 512), "9:21": (512, 1072), "4:3": (672, 512),
    "3:4": (512, 672), "3:2": (768, 512), "2:3": (512, 768)
}

FORBIDDEN_WORDS = ["loli"]

async def generate_image(interaction: discord.Interaction, prompt: str, bot_client, aspect_ratio: tuple[int, int]) -> None:
    global last_execution
    deferred = True
    try:
        await interaction.response.defer(ephemeral=True)
    except discord.errors.InteractionResponded:
        deferred = False

    async with command_lock:
        if (delay := COOLDOWN - (time() - last_execution)) > 0:
            if deferred:
                await interaction.followup.send(f"Подождите {delay:.1f} сек.", ephemeral=True)
            else:
                await interaction.followup.send(f"{interaction.user.mention}, подождите {delay:.1f} сек.", ephemeral=True)
            return
        last_execution = time()

    if any(word in prompt.lower() for word in FORBIDDEN_WORDS):
        if deferred:
            await interaction.followup.send(f"Этот запрос противоречит нашему TOS!", ephemeral=True)
        else:
            await interaction.followup.send(f"{interaction.user.mention}, этот запрос противоречит нашему TOS!", ephemeral=True)
        return

    try:
        response = await AsyncClient(provider=ImageLabs).images.async_generate(
            model="sdxl-turbo", prompt=prompt, response_format="url",
            width=aspect_ratio[0], height=aspect_ratio[1]
        )
        image_url = response.data[0].url if response and response.data else None
        if not image_url:
            raise ValueError("Нет URL")

        async with aiohttp.ClientSession() as session, session.get(image_url) as resp:
            if resp.status != 200:
                raise Exception(f"Ошибка: {resp.status}")
            image_file = File(BytesIO(await resp.read()), filename="image.png")

        try:
            if deferred:
                await interaction.followup.send("Сгенерировано:", file=image_file, ephemeral=True)
            else:
                await interaction.followup.send(f"{interaction.user.mention}, сгенерировано:", file=image_file, ephemeral=True)
        except discord.errors.HTTPException as e:
            await interaction.followup.send(f"{interaction.user.mention}, изображение сгенерировано, но не удалось отправить: {e}", ephemeral=True)
        logger.info(f"Изображение для {interaction.user.id}: {prompt}, {aspect_ratio}")

    except Exception as e:
        if deferred:
            await interaction.followup.send(f"Ошибка: {e}", ephemeral=True)
        else:
            await interaction.followup.send(f"{interaction.user.mention}, ошибка: {e}", ephemeral=True)
        logger.error(f"Ошибка для {interaction.user.id}: {e}")

def create_command(bot_client):
    choices = [app_commands.Choice(name=f"{k} {'Horizontal' if v[0] > v[1] else 'Vertical' if v[0] < v[1] else 'Square'}", value=k) for k, v in ASPECT_RATIOS.items()]
    
    @app_commands.command(name="generate_image", description=description)
    @app_commands.describe(prompt="Описание изображения", aspect_ratio="Соотношение сторон (по умолчанию 4:3)")
    @app_commands.choices(aspect_ratio=choices)
    async def wrapper(interaction: discord.Interaction, prompt: str, aspect_ratio: str = "4:3") -> None:
        await generate_image(interaction, prompt, bot_client, ASPECT_RATIOS[aspect_ratio])
    return wrapper