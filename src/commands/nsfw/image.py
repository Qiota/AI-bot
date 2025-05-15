import discord
from discord import app_commands, File, ButtonStyle, Embed
from discord.ui import Modal, TextInput, Select, Button, View
from g4f.client import AsyncClient
from g4f.Provider import ImageLabs, Websim
from io import BytesIO
import aiohttp
from asyncio import Lock, Queue
import asyncio
from typing import Tuple
import PIL.Image
import PIL.ImageEnhance
import io
import os
import re
from ...systemLog import logger
from ..restrict import check_bot_access, restrict_command_execution

# Конфигурация параметров
CONFIG = {
    "max_file_size": 8 * 1024 * 1024,  # Максимальный размер файла (8 МБ)
    "temp_dir": os.path.join("src", "temp_images"),
    "default_prompt": "A serene landscape with mountains and a clear sky, vibrant colors",
    "default_negative_prompt": (
        "blurry, low quality, distorted, extra limbs, artifacts, noise, low resolution, oversaturated, "
        "grainy, unnatural colors, deformed, missing limbs, text, watermark, logo, cropped"
    ),
    "default_settings": {
        "model": "sdxl-turbo",
        "aspect_ratio": "4:3",
        "steps": 30,
        "cfg_scale": 7.5,
        "improve_prompt": False,
        "brightness": 0,
        "contrast": 0
    },
    "aspect_ratios": {
        "1:1": (512, 512), "16:9": (896, 512), "9:16": (512, 896),
        "21:9": (1072, 512), "9:21": (512, 1072), "4:3": (672, 512),
        "3:4": (512, 672), "3:2": (768, 512), "2:3": (512, 768)
    },
    "models": {
        "sdxl-turbo": "SDXL Turbo",
        "sdxl": "SDXL",
        "dall-e-3": "DALL-E 3",
        "stable-diffusion-v1-5": "Stable Diffusion v1.5"
    },
    "forbidden_words": ["loli"],
    "embed_char_limit": 1024,  # Лимит символов для поля Embed в Discord
    "prompt_max_length": 1000,
    "negative_prompt_max_length": 500
}

# Создание временной директории
if not os.path.exists(CONFIG["temp_dir"]):
    os.makedirs(CONFIG["temp_dir"])

description = "Генерирует изображение, вдохновлённое editor.imagelabs.net"
command_lock = Lock()
generation_queue = Queue(maxsize=5)

def create_progress_bar(progress: float, length: int = 20) -> str:
    """Создает текстовую шкалу прогресса."""
    filled = int(length * progress)
    return "█" * filled + "░" * (length - filled)

async def update_progress(interaction: discord.Interaction, progress: float, message: discord.Message, ephemeral: bool):
    """Обновляет сообщение с прогресс-баром."""
    embed = Embed(title="⏳ Генерация", color=0x3498DB)
    embed.add_field(name="Прогресс", value=f"```{create_progress_bar(progress)} {int(progress * 100)}%```", inline=False)
    try:
        await message.edit(embed=embed)
    except discord.errors.NotFound:
        pass

async def generate_initial_prompt() -> str:
    """Генерирует начальный промпт с помощью модели, используя максимальный лимит символов."""
    client = AsyncClient(provider=Websim)
    for model in ["gemini-1.5-pro", "gemini-1.5-flash"]:
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Generate a detailed, vivid, and expressive image generation prompt with rich descriptions of colors, lighting, textures, environment, emotional tone, and artistic style. "
                            "Include nuanced details such as specific weather conditions, time of day, material properties, and composition to create a visually compelling scene. "
                            "Ensure the prompt is safe, coherent, and suitable for image generation. Avoid NSFW content. "
                            f"Target a length close to {CONFIG['embed_char_limit']} characters, but do not exceed it. "
                            "Return only the prompt, no Markdown or additional formatting."
                        )
                    },
                    {"role": "user", "content": "Generate a prompt."}
                ],
                max_tokens=900,  # Достаточно для ~1024 символов
                temperature=0.7
            )
            prompt = response.choices[0].message.content.strip()[:CONFIG["embed_char_limit"]]
            cleaned = re.sub(r'\[.*?\]\(.*?\)|<!--.*?-->|https?://\S+', '', prompt).strip()
            return cleaned if cleaned else CONFIG["default_prompt"]
        except Exception as e:
            logger.error(f"Ошибка генерации промпта с {model}: {e}")
            continue
    return CONFIG["default_prompt"]

async def improve_prompt(prompt: str, nsfw_allowed: bool = False) -> str:
    """Улучшает промпт, добавляя детали и выразительность, используя максимальный лимит символов."""
    client = AsyncClient(provider=Websim)
    for model in ["gemini-1.5-pro", "gemini-1.5-flash"]:
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Enhance the provided image generation prompt by adding vivid, multi-layered details, including nuanced color palettes, dramatic or natural lighting effects, realistic or stylized textures, immersive environmental settings, a clear emotional tone, distinct artistic styles, and thoughtful composition and perspective. "
                            f"{'Allow tasteful NSFW elements if present in the original prompt.' if nsfw_allowed else 'Avoid NSFW content.'} "
                            f"Target a length close to {CONFIG['embed_char_limit']} characters, but do not exceed it. "
                            "Ensure the prompt remains coherent, expressive, and suitable for image generation. "
                            "Return only the improved prompt, no Markdown or additional formatting."
                        )
                    },
                    {"role": "user", "content": f"Enhance: {prompt}."}
                ],
                max_tokens=900,  # Достаточно для ~1024 символов
                temperature=0.6
            )
            improved = response.choices[0].message.content.strip()[:CONFIG["embed_char_limit"]]
            cleaned = re.sub(r'\[.*?\]\(.*?\)|<!--.*?-->|https?://\S+', '', improved).strip()
            return cleaned if cleaned else prompt
        except Exception as e:
            logger.error(f"Ошибка улучшения с {model}: {e}")
            continue
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
    ephemeral: bool,
    view: View,
    message: discord.Message
) -> None:
    """Генерирует изображение на основе параметров."""
    await generation_queue.put(interaction)
    client = AsyncClient(provider=ImageLabs)
    success = False
    try:
        original_prompt = prompt
        final_prompt = prompt
        if improve_prompt_flag:
            await update_progress(interaction, 0.1, message, ephemeral)
            final_prompt = await improve_prompt(prompt, nsfw_allowed=True)
            if not final_prompt or not isinstance(final_prompt, str):
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

        await update_progress(interaction, 0.3, message, ephemeral)
        response = await client.images.async_generate(**params)
        if not response or not hasattr(response, "data") or not response.data:
            raise ValueError("API не вернул данных.")

        image_url = response.data[0].url
        if not image_url:
            raise ValueError("URL изображения отсутствует.")

        async with aiohttp.ClientSession() as session:
            headers = {
                "accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "accept-encoding": "gzip, deflate, br, zstd",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "origin": "https://editor.imagelabs.net",
                "referer": "https://editor.imagelabs.net/"
            }
            await update_progress(interaction, 0.6, message, ephemeral)
            async with session.get(image_url, headers=headers) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP ошибка: {resp.status}")
                image_data = await resp.read()
                if len(image_data) > CONFIG["max_file_size"]:
                    raise ValueError("Изображение превышает лимит размера.")

        await update_progress(interaction, 0.8, message, ephemeral)
        img = PIL.Image.open(BytesIO(image_data)).convert("RGB")
        img = img.resize(aspect_ratio, PIL.Image.LANCZOS)
        if CONFIG["default_settings"]["brightness"] != 0:
            img = PIL.ImageEnhance.Brightness(img).enhance(1 + CONFIG["default_settings"]["brightness"] / 100)
        if CONFIG["default_settings"]["contrast"] != 0:
            img = PIL.ImageEnhance.Contrast(img).enhance(1 + CONFIG["default_settings"]["contrast"] / 100)
        output = io.BytesIO()
        img.save(output, format="PNG")
        adjusted_data = output.getvalue()

        file = File(BytesIO(adjusted_data), filename="generated_image.png")
        embed = Embed(title="🎨 Готово!", color=0x1ABC9C)
        embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/3659/3659898.png")
        if improve_prompt_flag and final_prompt != original_prompt:
            embed.add_field(
                name="📝 Исходный",
                value=f"```{original_prompt[:CONFIG['embed_char_limit']-3]}{'...' if len(original_prompt) > CONFIG['embed_char_limit']-3 else ''}```",
                inline=False
            )
            embed.add_field(
                name="✨ Улучшенный",
                value=f"```{final_prompt[:CONFIG['embed_char_limit']-3]}{'...' if len(final_prompt) > CONFIG['embed_char_limit']-3 else ''}```",
                inline=False
            )
        else:
            embed.add_field(
                name="📝 Промпт",
                value=f"```{final_prompt[:CONFIG['embed_char_limit']-3]}{'...' if len(final_prompt) > CONFIG['embed_char_limit']-3 else ''}```",
                inline=False
            )
        embed.add_field(name="🤖 Модель", value=f"**{CONFIG['models'][model]}**", inline=True)
        embed.add_field(name="📏 Размеры", value=f"**{aspect_ratio[0]}x{aspect_ratio[1]}**", inline=True)
        embed.add_field(name="🔄 Шаги", value=f"**{steps}**", inline=True)
        embed.add_field(name="⚖️ CFG", value=f"**{cfg_scale}**", inline=True)
        embed.set_footer(text="ImageLabs")

        await update_progress(interaction, 1.0, message, ephemeral)
        response_view = ImageResponseView(interaction.user.id, ephemeral)
        await message.edit(
            content=f"{interaction.user.mention}",
            embed=embed,
            attachments=[file],
            view=response_view
        )
        response_view.message = message
        success = True

    except Exception as e:
        view.enable_all_buttons()
        error_str = str(e)
        if "400 Bad Request (error code: 20009)" in error_str or "20009" in error_str:
            embed = Embed(title="❌ Ошибка", description="Обнаружен явный контент.", color=0xE74C3C)
            await message.edit(embed=embed, view=None)
        else:
            embed = Embed(title="❌ Ошибка", description=error_str[:CONFIG["embed_char_limit"]], color=0xE74C3C)
            await message.edit(embed=embed, view=None)
        logger.error(f"Ошибка /image для {interaction.user.id}: {error_str}")

    finally:
        await generation_queue.get()
        generation_queue.task_done()

class PromptModal(Modal):
    """Модальное окно для ввода промпта и отрицательного промпта."""
    def __init__(self, bot_client, view: 'SettingsView', user_id: int):
        super().__init__(title="Укажите Промпт")
        self.bot_client = bot_client
        self.view = view
        self.user_id = user_id

        self.prompt_input = TextInput(
            label="Промпт",
            placeholder="Опишите изображение (или оставьте пустым для автогенерации)",
            required=False,
            max_length=CONFIG["prompt_max_length"]
        )
        self.negative_prompt_input = TextInput(
            label="Отрицательный промпт",
            placeholder="Что исключить из изображения",
            required=False,
            default=CONFIG["default_negative_prompt"],
            max_length=CONFIG["negative_prompt_max_length"]
        )

        self.add_item(self.prompt_input)
        self.add_item(self.negative_prompt_input)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Вы не автор команды.", ephemeral=True)
            return

        async with self.view.view_lock:
            await interaction.response.defer(ephemeral=self.view.ephemeral)
            self.view.prompt = self.prompt_input.value or await generate_initial_prompt()
            self.view.negative_prompt = self.negative_prompt_input.value or CONFIG["default_negative_prompt"]
            embed = Embed(title="⚙️ Настройки", color=0x3498DB)
            embed.add_field(
                name="📝 Промпт",
                value=f"```{self.view.prompt[:CONFIG['embed_char_limit']-3]}{'...' if len(self.view.prompt) > CONFIG['embed_char_limit']-3 else ''}```",
                inline=False
            )
            embed.add_field(name="🤖 Модель", value=f"> `{CONFIG['models'][self.view.model]}`", inline=True)
            embed.add_field(name="📏 Соотношение", value=f"> `{self.view.aspect_ratio}`", inline=True)
            embed.add_field(name="🔄 Шаги", value=f"> `{self.view.steps}`", inline=True)
            embed.add_field(name="⚖️ CFG", value=f"> `{self.view.cfg_scale}`", inline=True)
            self.view.enable_all_buttons()
            await interaction.message.edit(embed=embed, view=self.view)

class SettingsModal(Modal):
    """Модальное окно для настройки технических параметров."""
    def __init__(self, bot_client, view: 'SettingsView', user_id: int):
        super().__init__(title="Настройки")
        self.bot_client = bot_client
        self.view = view
        self.user_id = user_id

        self.steps_input = TextInput(
            label="Шаги (1-100)",
            placeholder="Количество шагов генерации",
            required=True,
            max_length=3
        )
        self.cfg_scale_input = TextInput(
            label="CFG Scale (1.0-20.0)",
            placeholder="Влияние промпта на результат",
            required=True,
            max_length=4
        )

        self.add_item(self.steps_input)
        self.add_item(self.cfg_scale_input)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Вы не автор команды.", ephemeral=True)
            return

        async with self.view.view_lock:
            await interaction.response.defer(ephemeral=self.view.ephemeral)
            try:
                self.view.steps = int(self.steps_input.value)
                self.view.cfg_scale = float(self.cfg_scale_input.value)
                if not (1 <= self.view.steps <= 100):
                    raise ValueError("Шаги должны быть в диапазоне 1–100.")
                if not (1.0 <= self.view.cfg_scale <= 20.0):
                    raise ValueError("CFG Scale должен быть в диапазоне 1.0–20.0.")
            except ValueError as e:
                embed = Embed(title="❌ Ошибка", description=str(e), color=0xE74C3C)
                await interaction.followup.send(embed=embed, ephemeral=self.view.ephemeral)
                return
            embed = Embed(title="⚙️ Настройки", color=0x3498DB)
            embed.add_field(
                name="📝 Промпт",
                value=f"```{self.view.prompt[:CONFIG['embed_char_limit']-3]}{'...' if len(self.view.prompt) > CONFIG['embed_char_limit']-3 else ''}```",
                inline=False
            )
            embed.add_field(name="🤖 Модель", value=f"> `{CONFIG['models'][self.view.model]}`", inline=True)
            embed.add_field(name="📏 Соотношение", value=f"> `{self.view.aspect_ratio}`", inline=True)
            embed.add_field(name="🔄 Шаги", value=f"> `{self.view.steps}`", inline=True)
            embed.add_field(name="⚖️ CFG", value=f"> `{self.view.cfg_scale}`", inline=True)
            self.view.enable_all_buttons()
            await interaction.message.edit(embed=embed, view=self.view)

class ImageResponseView(View):
    """View для взаимодействия с готовым изображением."""
    def __init__(self, user_id: int, ephemeral: bool):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.ephemeral = ephemeral
        self.message = None

        if not ephemeral:
            self.delete_button = Button(label="🗑️ Удалить", style=ButtonStyle.danger, custom_id="delete_image")
            self.delete_button.callback = self.delete_message_callback
            self.add_item(self.delete_button)
            asyncio.create_task(self.disable_delete_button())

    async def disable_delete_button(self):
        """Отключает кнопку удаления через 60 секунд."""
        await asyncio.sleep(60)
        self.delete_button.disabled = True
        try:
            if self.message:
                await self.message.edit(view=self)
        except discord.errors.NotFound:
            pass
        except Exception as e:
            logger.error(f"Ошибка отключения кнопки: {e}")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Вы не автор команды.", ephemeral=True)
            return False
        return True

    async def delete_message_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=self.ephemeral)
        try:
            await interaction.message.delete()
        except discord.errors.NotFound:
            pass
        except Exception as e:
            embed = Embed(title="❌ Ошибка", description="Не удалось удалить сообщение.", color=0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=self.ephemeral)
            logger.error(f"Ошибка удаления: {e}")

class SettingsView(View):
    """View для настройки параметров генерации."""
    def __init__(self, bot_client, ephemeral: bool, user_id: int, channel_id: int, message_id: int):
        super().__init__(timeout=300)
        self.bot_client = bot_client
        self.ephemeral = ephemeral
        self.user_id = user_id
        self.channel_id = channel_id
        self.message_id = message_id
        self.prompt = CONFIG["default_prompt"]
        self.negative_prompt = CONFIG["default_negative_prompt"]
        self.model = CONFIG["default_settings"]["model"]
        self.aspect_ratio = CONFIG["default_settings"]["aspect_ratio"]
        self.steps = CONFIG["default_settings"]["steps"]
        self.cfg_scale = CONFIG["default_settings"]["cfg_scale"]
        self.improve_prompt_flag = CONFIG["default_settings"]["improve_prompt"]
        self.view_lock = Lock()

        self.model_select = Select(
            placeholder="Выберите модель",
            options=[discord.SelectOption(label=v, value=k) for k, v in CONFIG["models"].items()],
            custom_id="model_select",
            row=0
        )
        self.model_select.callback = self.model_select_callback
        self.add_item(self.model_select)

        self.aspect_ratio_select = Select(
            placeholder="Выберите соотношение сторон",
            options=[discord.SelectOption(label=f"{k}", value=k) for k, v in CONFIG["aspect_ratios"].items()],
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

        self.prompt_button = Button(label="Укажите Промпт", style=ButtonStyle.green, custom_id="open_prompt", row=3)
        self.prompt_button.callback = self.open_prompt_button
        self.add_item(self.prompt_button)

        self.settings_button = Button(label="Настройки", style=ButtonStyle.secondary, custom_id="open_settings", row=3)
        self.settings_button.callback = self.open_settings_button
        self.add_item(self.settings_button)

    def disable_all_buttons(self):
        """Отключает все кнопки и селекты."""
        self.model_select.disabled = True
        self.aspect_ratio_select.disabled = True
        self.improve_prompt_button.disabled = True
        self.generate_button.disabled = True
        self.prompt_button.disabled = True
        self.settings_button.disabled = True

    def enable_all_buttons(self):
        """Включает все кнопки и селекты."""
        self.model_select.disabled = False
        self.aspect_ratio_select.disabled = False
        self.improve_prompt_button.disabled = False
        self.improve_prompt_button.label = "Улучшить промпт"
        self.generate_button.disabled = False
        self.generate_button.label = "Генерировать"
        self.prompt_button.disabled = False
        self.settings_button.disabled = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Вы не автор команды.", ephemeral=True)
            return False
        return True

    async def model_select_callback(self, interaction: discord.Interaction):
        async with self.view_lock:
            self.disable_all_buttons()
            await interaction.response.edit_message(view=self)
            self.model = self.model_select.values[0]
            embed = Embed(title="⚙️ Настройки", color=0x3498DB)
            embed.add_field(
                name="📝 Промпт",
                value=f"```{self.prompt[:CONFIG['embed_char_limit']-3]}{'...' if len(self.prompt) > CONFIG['embed_char_limit']-3 else ''}```",
                inline=False
            )
            embed.add_field(name="🤖 Модель", value=f"> `{CONFIG['models'][self.model]}`", inline=True)
            embed.add_field(name="📏 Соотношение", value=f"> `{self.aspect_ratio}`", inline=True)
            embed.add_field(name="🔄 Шаги", value=f"> `{self.steps}`", inline=True)
            embed.add_field(name="⚖️ CFG", value=f"> `{self.cfg_scale}`", inline=True)
            self.enable_all_buttons()
            await interaction.message.edit(embed=embed, view=self)

    async def aspect_ratio_select_callback(self, interaction: discord.Interaction):
        async with self.view_lock:
            self.disable_all_buttons()
            await interaction.response.edit_message(view=self)
            self.aspect_ratio = self.aspect_ratio_select.values[0]
            embed = Embed(title="⚙️ Настройки", color=0x3498DB)
            embed.add_field(
                name="📝 Промпт",
                value=f"```{self.prompt[:CONFIG['embed_char_limit']-3]}{'...' if len(self.prompt) > CONFIG['embed_char_limit']-3 else ''}```",
                inline=False
            )
            embed.add_field(name="🤖 Модель", value=f"> `{CONFIG['models'][self.model]}`", inline=True)
            embed.add_field(name="📏 Соотношение", value=f"> `{self.aspect_ratio}`", inline=True)
            embed.add_field(name="🔄 Шаги", value=f"> `{self.steps}`", inline=True)
            embed.add_field(name="⚖️ CFG", value=f"> `{self.cfg_scale}`", inline=True)
            self.enable_all_buttons()
            await interaction.message.edit(embed=embed, view=self)

    async def open_prompt_button(self, interaction: discord.Interaction):
        async with self.view_lock:
            modal = PromptModal(self.bot_client, self, self.user_id)
            await interaction.response.send_modal(modal)

    async def open_settings_button(self, interaction: discord.Interaction):
        async with self.view_lock:
            modal = SettingsModal(self.bot_client, self, self.user_id)
            await interaction.response.send_modal(modal)

    async def improve_prompt_button_callback(self, interaction: discord.Interaction):
        async with self.view_lock:
            self.disable_all_buttons()
            self.improve_prompt_button.label = "⌛ Улучшение..."
            await interaction.response.edit_message(view=self)
            improved = await improve_prompt(self.prompt, nsfw_allowed=True)
            self.prompt = improved
            embed = Embed(title="⚙️ Настройки", color=0x3498DB)
            embed.add_field(
                name="📝 Промпт",
                value=f"```{self.prompt[:CONFIG['embed_char_limit']-3]}{'...' if len(self.prompt) > CONFIG['embed_char_limit']-3 else ''}```",
                inline=False
            )
            embed.add_field(name="🤖 Модель", value=f"> `{CONFIG['models'][self.model]}`", inline=True)
            embed.add_field(name="📏 Соотношение", value=f"> `{self.aspect_ratio}`", inline=True)
            embed.add_field(name="🔄 Шаги", value=f"> `{self.steps}`", inline=True)
            embed.add_field(name="⚖️ CFG", value=f"> `{self.cfg_scale}`", inline=True)
            self.enable_all_buttons()
            await interaction.message.edit(embed=embed, view=self)

    async def generate_button_callback(self, interaction: discord.Interaction):
        async with self.view_lock:
            self.disable_all_buttons()
            self.generate_button.label = "⌛ Генерация..."
            await interaction.response.edit_message(view=self)

            if interaction.message is None:
                embed = Embed(title="❌ Ошибка", description="Сообщение недоступно.", color=0xE74C3C)
                await interaction.response.send_message(embed=embed, ephemeral=self.ephemeral)
                return

            if any(word in self.prompt.lower() for word in CONFIG["forbidden_words"]) or \
               (self.negative_prompt and any(word in self.negative_prompt.lower() for word in CONFIG["forbidden_words"])):
                embed = Embed(title="❌ Ошибка", description="Обнаружены запрещённые слова.", color=0xE74C3C)
                self.enable_all_buttons()
                await interaction.message.edit(embed=embed, view=self)
                return

            if self.steps < 1 or self.steps > 100:
                embed = Embed(title="❌ Ошибка", description="Шаги должны быть в диапазоне 1–100.", color=0xE74C3C)
                self.enable_all_buttons()
                await interaction.message.edit(embed=embed, view=self)
                return

            if self.cfg_scale < 1.0 or self.cfg_scale > 20.0:
                embed = Embed(title="❌ Ошибка", description="CFG Scale должен быть в диапазоне 1.0–20.0.", color=0xE74C3C)
                self.enable_all_buttons()
                await interaction.message.edit(embed=embed, view=self)
                return

            if self.model not in CONFIG["models"]:
                embed = Embed(title="❌ Ошибка", description=f"Модель должна быть одной из: {', '.join(CONFIG['models'].keys())}.", color=0xE74C3C)
                self.enable_all_buttons()
                await interaction.message.edit(embed=embed, view=self)
                return

            try:
                if generation_queue.full():
                    embed = Embed(title="⌛ Очередь", description="Очередь заполнена, пожалуйста, подождите.", color=0x3498DB)
                    await interaction.message.edit(embed=embed, view=self)
                    await generation_queue.put(interaction)

                async with command_lock:
                    await generate_image(
                        interaction,
                        self.prompt,
                        CONFIG["aspect_ratios"][self.aspect_ratio],
                        self.negative_prompt,
                        self.model,
                        self.steps,
                        self.cfg_scale,
                        self.improve_prompt_flag,
                        self.ephemeral,
                        self,
                        interaction.message
                    )
            except asyncio.QueueFull:
                embed = Embed(title="❌ Ошибка", description="Очередь заполнена.", color=0xE74C3C)
                self.enable_all_buttons()
                await interaction.message.edit(embed=embed, view=self)

def create_command(bot_client):
    """Создает группу команд для генерации изображений."""
    group = app_commands.Group(name="image", description="Команды для генерации изображений")
    group.dm_only = False

    @group.command(name="generate", description="Генерирует изображение на основе параметров")
    @app_commands.describe(ephemeral="Скрыть сообщения от других пользователей")
    async def generate(interaction: discord.Interaction, ephemeral: bool = False):
        if bot_client is None:
            logger.error("bot_client не предоставлен")
            await interaction.response.send_message("Внутренняя ошибка бота.", ephemeral=True)
            return

        if not await restrict_command_execution(interaction, bot_client):
            return

        access_result, access_reason = await check_bot_access(interaction, bot_client)
        if not access_result:
            await interaction.response.send_message(access_reason or "У вас нет доступа к этой команде.", ephemeral=True)
            return

        if interaction.guild is not None and not interaction.channel.nsfw:
            embed = Embed(title="❌ Ошибка", description="Эта команда доступна только в личных сообщениях или в NSFW-каналах.", color=0xE74C3C)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=ephemeral)
        view = SettingsView(bot_client, ephemeral, interaction.user.id, interaction.channel_id, None)
        embed = Embed(title="⚙️ Настройки", color=0x3498DB)
        embed.add_field(
            name="📝 Промпт",
            value=f"```{view.prompt[:CONFIG['embed_char_limit']-3]}{'...' if len(view.prompt) > CONFIG['embed_char_limit']-3 else ''}```",
            inline=False
        )
        embed.add_field(name="🤖 Модель", value=f"> `{CONFIG['models'][view.model]}`", inline=True)
        embed.add_field(name="📏 Соотношение", value=f"> `{view.aspect_ratio}`", inline=True)
        embed.add_field(name="🔄 Шаги", value=f"> `{view.steps}`", inline=True)
        embed.add_field(name="⚖️ CFG", value=f"> `{view.cfg_scale}`", inline=True)
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=ephemeral)
        view.message_id = message.id

    return group