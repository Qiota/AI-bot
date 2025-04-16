import discord
from discord import app_commands, File, ButtonStyle, Embed
from discord.ui import Modal, TextInput, Select, Button, View
from g4f.client import AsyncClient
from g4f.Provider import ImageLabs, PollinationsAI
from io import BytesIO
import aiohttp
from asyncio import Lock
from typing import Tuple
import PIL.Image
import PIL.ImageEnhance
import io
import os
import re
from ..logging_config import logger

description = "Генерирует изображение, вдохновлённое editor.imagelabs.net"
command_lock = Lock()
COOLDOWN = 5
MAX_FILE_SIZE = 8 * 1024 * 1024
TEMP_DIR = os.path.join("src", "temp_images")

if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

DEFAULT_PROMPT = "A serene landscape with mountains and a clear sky, vibrant colors"
DEFAULT_SETTINGS = {
    "model": "sdxl-turbo",
    "aspect_ratio": "4:3",
    "steps": 30,
    "cfg_scale": 7.5,
    "improve_prompt": False,
    "brightness": 0,
    "contrast": 0
}

ASPECT_RATIOS = {
    "1:1": (512, 512), "16:9": (896, 512), "9:16": (512, 896),
    "21:9": (1072, 512), "9:21": (512, 1072), "4:3": (672, 512),
    "3:4": (512, 672), "3:2": (768, 512), "2:3": (512, 768)
}

MODELS = {
    "sdxl-turbo": "SDXL Turbo",
    "sdxl": "SDXL",
    "dall-e-3": "DALL-E 3",
    "stable-diffusion-v1-5": "Stable Diffusion v1.5"
}

FORBIDDEN_WORDS = ["loli"]

async def improve_prompt(prompt: str) -> str:
    logger.info(f"Эмуляция ImageLabs 'aiImprove': '{prompt[:50]}...'")
    client = AsyncClient(provider=PollinationsAI)
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are replicating the 'aiImprove' feature from ImageLabs Editor (https://editor.imagelabs.net). "
                        "Your task is to enhance the given prompt for image generation by adding vivid, specific details. "
                        "Focus on colors, lighting, textures, environmental elements, and emotional atmosphere to make the scene immersive. "
                        "Ensure the description is clear, visually rich, and optimized for high-quality image generation. "
                        "Do not apply NSFW filters unless explicitly requested; allow mature themes if present in the prompt. "
                        "Return only the improved prompt as plain text, no Markdown, no images, no extra formatting."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        f"Enhance this prompt for image generation with detailed visual descriptions: {prompt}. "
                        "Add specific details about the scene's colors, lighting (e.g., golden hour, soft moonlight), textures (e.g., rough, silky), "
                        "environmental elements (e.g., misty air, scattered leaves), and emotional tone (e.g., serene, dramatic). "
                        "Keep the description concise but highly visual, suitable for generating a high-quality image."
                    )
                }
            ]
        )
        if not response or not response.choices or not response.choices[0].message.content:
            logger.warning("PollinationsAI вернул пустой ответ.")
            return prompt

        improved = response.choices[0].message.content
        if not isinstance(improved, str):
            logger.error(f"Улучшенный промпт не строка: {type(improved)}")
            return prompt

        cleaned = re.sub(r'\[.*?\]\(.*?\)|<!--.*?-->|https?://\S+', '', improved).strip()
        if not cleaned:
            logger.warning("Улучшенный промпт после очистки пустой.")
            return prompt

        logger.info(f"Улучшенный промпт (ImageLabs стиль): '{cleaned[:50]}...'")
        return cleaned
    except Exception as e:
        logger.error(f"Ошибка улучшения (ImageLabs стиль): {e}")
        return prompt

async def generate_image(
    interaction: discord.Interaction,
    prompt: str,
    aspect_ratio: Tuple[int, int],
    negative_prompt: str,
    model: str,
    steps: int,
    cfg_scale: float,
    improve_prompt_flag: bool,
    ephemeral: bool
) -> None:
    client = AsyncClient(provider=ImageLabs)
    try:
        original_prompt = prompt
        final_prompt = prompt
        if improve_prompt_flag:
            final_prompt = await improve_prompt(prompt)
            if not final_prompt or not isinstance(final_prompt, str):
                logger.warning("Улучшенный промпт некорректный.")
                final_prompt = prompt

        params = {
            "model": model,
            "prompt": final_prompt,
            "negative_prompt": negative_prompt,
            "response_format": "url",
            "width": aspect_ratio[0],
            "height": aspect_ratio[1],
            "sampling_steps": steps,
            "cfg_scale": cfg_scale
        }

        logger.info(f"Запрос к ImageLabs: model={model}, size={aspect_ratio}, steps={steps}, cfg={cfg_scale}")
        response = await client.images.async_generate(**params)
        logger.info(f"Ответ API: url={response.data[0].url if response.data else 'None'}")

        if not response or not hasattr(response, "data") or not response.data:
            raise ValueError("API не вернул данных изображения.")

        image_url = response.data[0].url
        if not image_url:
            raise ValueError("URL изображения отсутствует.")

        async with aiohttp.ClientSession() as session:
            headers = {
                "accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "accept-encoding": "gzip, deflate, br, zstd",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
                "origin": "https://editor.imagelabs.net",
                "referer": "https://editor.imagelabs.net/"
            }
            async with session.get(image_url, headers=headers) as resp:
                if resp.status != 200:
                    raise Exception(f"Ошибка HTTP: {resp.status} {resp.reason}")
                image_data = await resp.read()
                if len(image_data) > MAX_FILE_SIZE:
                    raise ValueError("Изображение превышает 8 МБ.")

        img = PIL.Image.open(BytesIO(image_data)).convert("RGB")
        img = img.resize(aspect_ratio, PIL.Image.LANCZOS)
        if DEFAULT_SETTINGS["brightness"] != 0:
            img = PIL.ImageEnhance.Brightness(img).enhance(1 + DEFAULT_SETTINGS["brightness"] / 100)
        if DEFAULT_SETTINGS["contrast"] != 0:
            img = PIL.ImageEnhance.Contrast(img).enhance(1 + DEFAULT_SETTINGS["contrast"] / 100)
        output = io.BytesIO()
        img.save(output, format="PNG")
        adjusted_data = output.getvalue()

        file = File(BytesIO(adjusted_data), filename="generated_image.png")
        embed = Embed(title="🎨 Изображение готово!", color=0x1ABC9C)
        embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/3659/3659898.png")
        if improve_prompt_flag and final_prompt != original_prompt:
            embed.add_field(name="📝 Исходный промпт", value=f"```{original_prompt[:1018]}[...]```" if len(original_prompt) > 1018 else f"```{original_prompt}```", inline=False)
            embed.add_field(name="✨ Улучшенный промпт", value=f"```{final_prompt[:1018]}[...]```" if len(final_prompt) > 1018 else f"```{final_prompt}```", inline=False)
        else:
            embed.add_field(name="📝 Промпт", value=f"```{final_prompt[:1018]}[...]```" if len(final_prompt) > 1018 else f"```{final_prompt}```", inline=False)
        embed.add_field(name="🤖 Модель", value=f"**{MODELS[model]}**", inline=True)
        embed.add_field(name="📏 Размеры", value=f"**{aspect_ratio[0]}x{aspect_ratio[1]}**", inline=True)
        embed.add_field(name="🔄 Шаги", value=f"**{steps}**", inline=True)
        embed.add_field(name="⚖️ CFG Scale", value=f"**{cfg_scale}**", inline=True)
        embed.set_footer(text="Сгенерировано с помощью ImageLabs")
        await interaction.followup.send(embed=embed, file=file, ephemeral=ephemeral)

        logger.info(
            f"Команда /img выполнена: user={interaction.user.id}, "
            f"model={model}, size={aspect_ratio}, steps={steps}, cfg={cfg_scale}"
        )
    except Exception as e:
        embed = Embed(title="❌ Ошибка генерации", description=str(e), color=0xE74C3C)
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)
        logger.error(f"Ошибка /img для {interaction.user.id}: {e}")

class SettingsModal(Modal):
    def __init__(self, bot_client, view: 'SettingsView'):
        super().__init__(title="Настройки генерации изображения")
        self.bot_client = bot_client
        self.view = view

        self.prompt_input = TextInput(
            label="Промпт",
            placeholder=DEFAULT_PROMPT,
            default=self.view.prompt,
            required=True,
            max_length=1000
        )
        self.negative_prompt_input = TextInput(
            label="Отрицательный промпт",
            placeholder="Оставьте пустым, если не нужно",
            required=False,
            max_length=500
        )
        self.steps_input = TextInput(
            label="Шаги (1-100)",
            placeholder=str(DEFAULT_SETTINGS["steps"]),
            default=str(self.view.steps),
            required=True,
            max_length=3
        )
        self.cfg_scale_input = TextInput(
            label="CFG Scale (1.0-20.0)",
            placeholder=str(DEFAULT_SETTINGS["cfg_scale"]),
            default=str(self.view.cfg_scale),
            required=True,
            max_length=4
        )

        self.add_item(self.prompt_input)
        self.add_item(self.negative_prompt_input)
        self.add_item(self.steps_input)
        self.add_item(self.cfg_scale_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=self.view.ephemeral)
        self.view.prompt = self.prompt_input.value
        self.view.negative_prompt = self.negative_prompt_input.value or ""
        try:
            self.view.steps = int(self.steps_input.value)
            self.view.cfg_scale = float(self.cfg_scale_input.value)
        except ValueError:
            embed = Embed(title="❌ Ошибка", description="Шаги и CFG Scale должны быть числами.", color=0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=self.view.ephemeral)
            return
        embed = Embed(title="⚙️ Текущие настройки", color=0x3498DB)
        embed.add_field(name="📝 Промпт", value=f"```{self.view.prompt[:1018]}[...]```" if len(self.view.prompt) > 1018 else f"```{self.view.prompt}```", inline=False)
        embed.add_field(name="🤖 Модель", value=f"> `{MODELS[self.view.model]}`", inline=True)
        embed.add_field(name="📏 Соотношение сторон", value=f"> `{self.view.aspect_ratio} ({ASPECT_RATIOS[self.view.aspect_ratio][0]}x{ASPECT_RATIOS[self.view.aspect_ratio][1]})`", inline=True)
        embed.add_field(name="🔄 Шаги", value=f"> `{self.view.steps}`", inline=True)
        embed.add_field(name="⚖️ CFG Scale", value=f"> `{self.view.cfg_scale}`", inline=True)
        self.view.generate_button.disabled = False
        self.view.generate_button.label = "Генерировать"
        self.view.improve_prompt_button.disabled = False
        self.view.improve_prompt_button.label = "Улучшить промпт"
        await interaction.followup.edit_message(interaction.message.id, embed=embed, view=self.view)

class SettingsView(View):
    def __init__(self, bot_client, ephemeral: bool):
        super().__init__(timeout=300)
        self.bot_client = bot_client
        self.ephemeral = ephemeral
        self.prompt = DEFAULT_PROMPT
        self.negative_prompt = ""
        self.model = DEFAULT_SETTINGS["model"]
        self.aspect_ratio = DEFAULT_SETTINGS["aspect_ratio"]
        self.steps = DEFAULT_SETTINGS["steps"]
        self.cfg_scale = DEFAULT_SETTINGS["cfg_scale"]
        self.improve_prompt_flag = DEFAULT_SETTINGS["improve_prompt"]

        self.model_select = Select(
            placeholder="Выберите модель",
            options=[discord.SelectOption(label=v, value=k) for k, v in MODELS.items()],
            custom_id="model_select",
            row=0
        )
        self.model_select.callback = self.model_select_callback
        self.add_item(self.model_select)

        self.aspect_ratio_select = Select(
            placeholder="Выберите соотношение сторон",
            options=[discord.SelectOption(label=f"{k} ({v[0]}x{v[1]})", value=k) for k, v in ASPECT_RATIOS.items()],
            custom_id="aspect_ratio_select",
            row=1
        )
        self.aspect_ratio_select.callback = self.aspect_ratio_select_callback
        self.add_item(self.aspect_ratio_select)

        self.improve_prompt_button = Button(label="Улучшить промпт", style=ButtonStyle.primary, custom_id="improve_prompt", row=2)
        self.improve_prompt_button.callback = self.improve_prompt_button_callback
        self.add_item(self.improve_prompt_button)

        self.generate_button = Button(label="Генерировать", style=ButtonStyle.success, custom_id="generate_image", row=2)
        self.generate_button.callback = self.generate_button_callback
        self.add_item(self.generate_button)

    async def model_select_callback(self, interaction: discord.Interaction):
        self.model = self.model_select.values[0]
        embed = Embed(title="⚙️ Текущие настройки", color=0x3498DB)
        embed.add_field(name="📝 Промпт", value=f"```{self.prompt[:1018]}[...]```" if len(self.prompt) > 1018 else f"```{self.prompt}```", inline=False)
        embed.add_field(name="🤖 Модель", value=f"> `{MODELS[self.model]}`", inline=True)
        embed.add_field(name="📏 Соотношение сторон", value=f"> `{self.aspect_ratio} ({ASPECT_RATIOS[self.aspect_ratio][0]}x{ASPECT_RATIOS[self.aspect_ratio][1]})`", inline=True)
        embed.add_field(name="🔄 Шаги", value=f"> `{self.steps}`", inline=True)
        embed.add_field(name="⚖️ CFG Scale", value=f"> `{self.cfg_scale}`", inline=True)
        await interaction.response.edit_message(embed=embed, view=self)

    async def aspect_ratio_select_callback(self, interaction: discord.Interaction):
        self.aspect_ratio = self.aspect_ratio_select.values[0]
        embed = Embed(title="⚙️ Текущие настройки", color=0x3498DB)
        embed.add_field(name="📝 Промпт", value=f"```{self.prompt[:1018]}[...]```" if len(self.prompt) > 1018 else f"```{self.prompt}```", inline=False)
        embed.add_field(name="🤖 Модель", value=f"> `{MODELS[self.model]}`", inline=True)
        embed.add_field(name="📏 Соотношение сторон", value=f"> `{self.aspect_ratio} ({ASPECT_RATIOS[self.aspect_ratio][0]}x{ASPECT_RATIOS[self.aspect_ratio][1]})`", inline=True)
        embed.add_field(name="🔄 Шаги", value=f"> `{self.steps}`", inline=True)
        embed.add_field(name="⚖️ CFG Scale", value=f"> `{self.cfg_scale}`", inline=True)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Настройки", style=ButtonStyle.green, custom_id="open_settings", row=2)
    async def open_settings_button(self, interaction: discord.Interaction, button: Button):
        modal = SettingsModal(self.bot_client, self)
        await interaction.response.send_modal(modal)

    async def improve_prompt_button_callback(self, interaction: discord.Interaction):
        self.improve_prompt_button.disabled = True
        self.improve_prompt_button.label = "Улучшение..."
        await interaction.response.edit_message(view=self)
        improved = await improve_prompt(self.prompt)
        self.prompt = improved
        embed = Embed(title="⚙️ Текущие настройки", color=0x3498DB)
        embed.add_field(name="📝 Промпт", value=f"```{self.prompt[:1018]}[...]```" if len(self.prompt) > 1018 else f"```{self.prompt}```", inline=False)
        embed.add_field(name="🤖 Модель", value=f"> `{MODELS[self.model]}`", inline=True)
        embed.add_field(name="📏 Соотношение сторон", value=f"> `{self.aspect_ratio} ({ASPECT_RATIOS[self.aspect_ratio][0]}x{ASPECT_RATIOS[self.aspect_ratio][1]})`", inline=True)
        embed.add_field(name="🔄 Шаги", value=f"> `{self.steps}`", inline=True)
        embed.add_field(name="⚖️ CFG Scale", value=f"> `{self.cfg_scale}`", inline=True)
        self.improve_prompt_button.disabled = False
        self.improve_prompt_button.label = "Улучшить промпт"
        await interaction.followup.edit_message(interaction.message.id, embed=embed, view=self)

    async def generate_button_callback(self, interaction: discord.Interaction):
        self.generate_button.disabled = True
        self.generate_button.label = "Загрузка изображения..."
        await interaction.response.edit_message(view=self)

        if any(word in self.prompt.lower() for word in FORBIDDEN_WORDS) or \
           (self.negative_prompt and any(word in self.negative_prompt.lower() for word in FORBIDDEN_WORDS)):
            embed = Embed(title="❌ Ошибка", description="Запрос содержит запрещённые слова.", color=0xE74C3C)
            self.generate_button.disabled = False
            self.generate_button.label = "Генерировать"
            await interaction.followup.send(embed=embed, ephemeral=self.ephemeral)
            await interaction.followup.edit_message(interaction.message.id, view=self)
            return

        if self.steps < 1 or self.steps > 100:
            embed = Embed(title="❌ Ошибка", description="Шаги: 1–100.", color=0xE74C3C)
            self.generate_button.disabled = False
            self.generate_button.label = "Генерировать"
            await interaction.followup.send(embed=embed, ephemeral=self.ephemeral)
            await interaction.followup.edit_message(interaction.message.id, view=self)
            return
        if self.cfg_scale < 1.0 or self.cfg_scale > 20.0:
            embed = Embed(title="❌ Ошибка", description="CFG Scale: 1.0–20.0.", color=0xE74C3C)
            self.generate_button.disabled = False
            self.generate_button.label = "Генерировать"
            await interaction.followup.send(embed=embed, ephemeral=self.ephemeral)
            await interaction.followup.edit_message(interaction.message.id, view=self)
            return
        if self.model not in MODELS:
            embed = Embed(title="❌ Ошибка", description=f"Модель не поддерживается: {', '.join(MODELS.keys())}.", color=0xE74C3C)
            self.generate_button.disabled = False
            self.generate_button.label = "Генерировать"
            await interaction.followup.send(embed=embed, ephemeral=self.ephemeral)
            await interaction.followup.edit_message(interaction.message.id, view=self)
            return

        async with command_lock:
            await generate_image(
                interaction,
                self.prompt,
                ASPECT_RATIOS[self.aspect_ratio],
                self.negative_prompt,
                self.model,
                self.steps,
                self.cfg_scale,
                self.improve_prompt_flag,
                self.ephemeral
            )
            self.generate_button.disabled = False
            self.generate_button.label = "Генерировать"
            await interaction.followup.edit_message(interaction.message.id, view=self)

def create_command(bot_client):
    group = app_commands.Group(name="image", description="Работа с изображениями")
    group.dm_only = False

    @group.command(name="generate", description="Создаёт изображение с настройками")
    @app_commands.describe(ephemeral="Скрыть сообщения (True/False)")
    async def generate(interaction: discord.Interaction, ephemeral: bool = False):
        view = SettingsView(bot_client, ephemeral)
        embed = Embed(title="⚙️ Текущие настройки", color=0x3498DB)
        embed.add_field(name="📝 Промпт", value=f"```{view.prompt[:1018]}[...]```" if len(view.prompt) > 1018 else f"```{view.prompt}```", inline=False)
        embed.add_field(name="🤖 Модель", value=f"> `{MODELS[view.model]}`", inline=True)
        embed.add_field(name="📏 Соотношение сторон", value=f"> `{view.aspect_ratio} ({ASPECT_RATIOS[view.aspect_ratio][0]}x{ASPECT_RATIOS[view.aspect_ratio][1]})`", inline=True)
        embed.add_field(name="🔄 Шаги", value=f"> `{view.steps}`", inline=True)
        embed.add_field(name="⚖️ CFG Scale", value=f"> `{view.cfg_scale}`", inline=True)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=ephemeral)

    return group