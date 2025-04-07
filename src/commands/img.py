import discord
from discord import app_commands, File
from g4f.client import AsyncClient
from g4f.Provider import ImageLabs
from io import BytesIO
import aiohttp
from asyncio import Lock
from ..config import logger

description = "Генерирует изображение по описанию"
command_lock = Lock()
COOLDOWN = 5
MAX_FILE_SIZE = 8 * 1024 * 1024

ASPECT_RATIOS = {
    "1:1": (512, 512), "16:9": (896, 512), "9:16": (512, 896),
    "21:9": (1072, 512), "9:21": (512, 1072), "4:3": (672, 512),
    "3:4": (512, 672), "3:2": (768, 512), "2:3": (512, 768)
}

FORBIDDEN_WORDS = ["loli"]

async def img(interaction: discord.Interaction, prompt: str, bot_client, aspect_ratio: tuple[int, int]) -> None:
    """Команда /img: Генерирует изображение по описанию."""
    deferred = True
    try:
        await interaction.response.defer(ephemeral=True)
    except discord.errors.InteractionResponded:
        deferred = False

    remaining = bot_client.cooldown_manager.check_cooldown("img", interaction.user.id, COOLDOWN)
    if remaining > 0:
        await interaction.followup.send(f"Ожидание: {remaining:.1f} секунд.", ephemeral=True)
        return

    if not prompt.strip():
        await interaction.followup.send("Укажите описание для генерации.", ephemeral=True)
        return

    if any(word in prompt.lower() for word in FORBIDDEN_WORDS):
        await interaction.followup.send("Запрос нарушает условия использования.", ephemeral=True)
        return
    
    if not isinstance(interaction.channel, discord.DMChannel):
        if not interaction.channel.permissions_for(interaction.guild.me).attach_files:
            await interaction.followup.send("Отсутствуют права на отправку файлов.", ephemeral=True)
            return

    try:
        response = await AsyncClient(provider=ImageLabs).images.async_generate(
            model="sdxl-turbo", prompt=prompt, response_format="url",
            width=aspect_ratio[0], height=aspect_ratio[1]
        )
        image_url = response.data[0].url if response and response.data else None
        if not image_url:
            raise ValueError("URL изображения отсутствует.")

        async with aiohttp.ClientSession() as session, session.get(image_url) as resp:
            if resp.status != 200:
                raise Exception(f"Ошибка HTTP: {resp.status}")
            image_data = await resp.read()
            if len(image_data) > MAX_FILE_SIZE:
                raise ValueError("Изображение превышает допустимый размер (8 МБ).")

        image_file = File(BytesIO(image_data), filename="image.png")
        await interaction.followup.send("Изображение сгенерировано.", file=image_file, ephemeral=True)
        logger.info(f"Команда /img выполнена для пользователя {interaction.user.id}: prompt='{prompt}', aspect_ratio={aspect_ratio}")

    except Exception as e:
        await interaction.followup.send(f"Ошибка генерации изображения: {e}", ephemeral=True)
        logger.error(f"Ошибка команды /img для пользователя {interaction.user.id}: {e}")

def create_command(bot_client):
    choices = [app_commands.Choice(name=f"{k} {'Горизонтальный' if v[0] > v[1] else 'Вертикальный' if v[0] < v[1] else 'Квадратный'}", value=k) for k, v in ASPECT_RATIOS.items()]
    
    @app_commands.command(name="img", description=description)
    @app_commands.describe(prompt="Описание для генерации изображения", aspect_ratio="Соотношение сторон (по умолчанию 4:3)")
    @app_commands.choices(aspect_ratio=choices)
    async def wrapper(interaction: discord.Interaction, prompt: str, aspect_ratio: str = "4:3") -> None:
        await img(interaction, prompt, bot_client, ASPECT_RATIOS[aspect_ratio])
    return wrapper