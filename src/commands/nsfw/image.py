import discord
from discord import app_commands, File, ButtonStyle, Embed
from discord.ui import Modal, TextInput, Select, Button, View
from g4f.client import AsyncClient
from g4f.Provider import ImageLabs, PollinationsAI
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
import time
from ...systemLog import logger
from ..restrict import check_bot_access, restrict_command_execution

# Конфигурация параметров
CONFIG = {
    "max_file_size": 10 * 1024 * 1024,  # Максимальный размер файла (10 МБ)
    "temp_dir": os.path.join("src", "temp_images"),
    "default_prompt": "A serene landscape with mountains and a clear sky, vibrant colors, Studio Ghibli style, ultra high quality",
    "default_negative_prompt": (
        "blurry, low quality, distorted, extra limbs, artifacts, noise, low resolution, oversaturated, "
        "grainy, unnatural colors, deformed, missing limbs, text, watermark, logo, cropped"
    ),
    "default_settings": {
        "model": "sdxl-turbo",
        "aspect_ratio": "4:3",
        "steps": 50,  # Увеличено для повышения качества
        "cfg_scale": 8.0,  # Оптимизировано для баланса детализации
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
    "prompt_max_length": 1000,
    "negative_prompt_max_length": 500,
    "discord_embed_limits": {
        "description": 4096,  # Максимум для description в Embed
        "total": 6000  # Общий лимит символов для Embed
    }
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
        await message.edit(embed=embed, view=None, attachments=[])
    except discord.errors.NotFound:
        pass

async def generate_initial_prompt() -> str:
    """Генерирует начальный промпт с выразительными деталями."""
    client = AsyncClient(provider=PollinationsAI)
    for model in ["unity"]:
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Generate a vivid, structured image generation prompt in the style of: 'Summer trip in Tokyo, sakura lover, sweetheart couple, t-shirt design, balance harmony space, pixel art, Studio Ghibli style, white background, ultra high quality.' "
                            "Include a clear scene description, emotional tone, specific subjects or characters, artistic style, color palette, and quality details. "
                            "Ensure the prompt is safe, coherent, and suitable for image generation. Avoid NSFW content. "
                            "Use concise, comma-separated phrases to describe elements. "
                            "Return only the prompt, no Markdown or additional formatting."
                        )
                    },
                    {"role": "user", "content": "Generate a prompt."}
                ],
                max_tokens=300,
                temperature=0.7
            )
            prompt = response.choices[0].message.content.strip()
            cleaned = re.sub(r'\[.*?\]\(.*?\)|<!--.*?-->|https?://\S+', '', prompt).strip()
            return cleaned if cleaned else CONFIG["default_prompt"]
        except Exception as e:
            logger.error(f"Ошибка генерации промпта с {model}: {str(e)}")
            continue
    return CONFIG["default_prompt"]

async def improve_prompt(prompt: str, nsfw_allowed: bool = False) -> str:
    """Улучшает промпт, добавляя детали в структурированном стиле."""
    client = AsyncClient(provider=PollinationsAI)
    for model in ["unity"]:
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Enhance the provided image generation prompt to match the style of: 'Summer trip in Tokyo, sakura lover, sweetheart couple, t-shirt design, balance harmony space, pixel art, Studio Ghibli style, white background, ultra high quality.' "
                            "Add vivid details including scene, emotions, characters, colors, textures, lighting, artistic style, and quality. "
                            "Use concise, comma-separated phrases for clarity. "
                            f"{'Allow tasteful NSFW elements if present.' if nsfw_allowed else 'Avoid NSFW content.'} "
                            "Ensure the prompt is coherent and suitable for image generation. "
                            "Return only the enhanced prompt, no Markdown or additional formatting."
                        )
                    },
                    {"role": "user", "content": f"Enhance: {prompt}."}
                ],
                max_tokens=300,
                temperature=0.6
            )
            improved = response.choices[0].message.content.strip()
            cleaned = re.sub(r'\[.*?\]\(.*?\)|<!--.*?-->|https?://\S+', '', improved).strip()
            return cleaned if cleaned else prompt
        except Exception as e:
            logger.error(f"Ошибка улучшения с {model}: {str(e)}")
            continue
    return prompt

async def truncate_embed(embed: discord.Embed) -> discord.Embed:
    """Обрезает содержимое Embed, чтобы уложиться в лимиты Discord."""
    max_description_length = CONFIG["discord_embed_limits"]["description"]
    total_limit = CONFIG["discord_embed_limits"]["total"]

    if embed.description and len(embed.description) > max_description_length:
        embed.description = embed.description[:max_description_length - 3] + "..."

    total_chars = (
        len(embed.title or "") +
        len(embed.description or "") +
        sum(len(field.name) + len(field.value) for field in embed.fields) +
        len(embed.footer.text or "")
    )

    if total_chars > total_limit:
        excess = total_chars - total_limit + 3
        if embed.description:
            embed.description = embed.description[:-excess] + "..."
        else:
            embed.description = "Содержимое слишком длинное и было урезано."

    return embed

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
                "origin": "https://editor.imagelabs.com",
                "referer": "https://editor.imagelabs.com/"
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
        embed.description = f"**📝 Промпт**:\n```{final_prompt}```"

        embed.add_field(name="🤖 Модель", value=f"**{CONFIG['models'][model]}**", inline=True)
        embed.add_field(name="📏 Размеры", value=f"**{aspect_ratio[0]}x{aspect_ratio[1]}**", inline=True)
        embed.add_field(name="🔄 Шаги", value=f"**{steps}**", inline=True)
        embed.add_field(name="⚖️ CFG", value=f"**{cfg_scale}**", inline=True)
        embed.set_footer(text="ImageLabs")

        embed = await truncate_embed(embed)

        await update_progress(interaction, 1.0, message, ephemeral)
        response_view = ImageResponseView(
            user_id=interaction.user.id,
            ephemeral=ephemeral,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            negative_prompt=negative_prompt,
            model=model,
            steps=steps,
            cfg_scale=cfg_scale,
            improve_prompt_flag=improve_prompt_flag
        )
        await message.edit(
            content=f"{interaction.user.mention}",
            embed=embed,
            attachments=[file],
            view=response_view
        )
        response_view.message = message
        success = True

    except Exception as e:
        view.enable_all()
        error_str = str(e)
        if "400 Bad Request (error code: 20009)" in error_str or "20009" in error_str:
            embed = Embed(title="❌ Ошибка", description="Обнаружен явный контент.", color=0xE74C3C)
            await message.edit(embed=embed, view=None, attachments=[])
        else:
            embed = Embed(title="❌ Ошибка", description=error_str[:CONFIG["discord_embed_limits"]["description"]], color=0xE74C3C)
            await message.edit(embed=embed, view=None, attachments=[])
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
            max_length=CONFIG["prompt_max_length"],
            default=self.view.prompt if self.view.prompt != CONFIG["default_prompt"] else ""
        )
        self.negative_prompt_input = TextInput(
            label="Отрицательный промпт",
            placeholder="Что исключить из изображения",
            required=False,
            default=self.view.negative_prompt if self.view.negative_prompt != CONFIG["default_negative_prompt"] else "",
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
            self.view.is_prompt_improved = False
            embed = Embed(title="⚙️ Настройки", color=0x3498DB)
            embed.description = f"**📝 Промпт**:\n```{self.view.prompt}```"
            embed = await truncate_embed(embed)
            embed.add_field(name="🤖 Модель", value=f"> `{CONFIG['models'][self.view.model]}`", inline=True)
            embed.add_field(name="📏 Соотношение", value=f"> `{self.view.aspect_ratio}`", inline=True)
            embed.add_field(name="🔄 Шаги", value=f"> `{self.view.steps}`", inline=True)
            embed.add_field(name="⚖️ CFG", value=f"> `{self.view.cfg_scale}`", inline=True)
            self.view.enable_all()
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
            default=str(self.view.steps),
            max_length=3
        )
        self.cfg_scale_input = TextInput(
            label="CFG Scale (1.0-20.0)",
            placeholder="Влияние промпта на результат",
            required=True,
            default=str(self.view.cfg_scale),
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
            embed.description = f"**📝 Промпт**:\n```{self.view.prompt}```"
            embed = await truncate_embed(embed)
            embed.add_field(name="🤖 Модель", value=f"> `{CONFIG['models'][self.view.model]}`", inline=True)
            embed.add_field(name="📏 Соотношение", value=f"> `{self.view.aspect_ratio}`", inline=True)
            embed.add_field(name="🔄 Шаги", value=f"> `{self.view.steps}`", inline=True)
            embed.add_field(name="⚖️ CFG", value=f"> `{self.view.cfg_scale}`", inline=True)
            self.view.enable_all()
            await interaction.message.edit(embed=embed, view=self.view)

class ImageResponseView(View):
    """View для взаимодействия с готовым изображением."""
    def __init__(
        self,
        user_id: int,
        ephemeral: bool,
        prompt: str,
        aspect_ratio: Tuple[int, int],
        negative_prompt: str,
        model: str,
        steps: int,
        cfg_scale: float,
        improve_prompt_flag: bool
    ):
        super().__init__(timeout=600)  # 10 минут
        self.user_id = user_id
        self.ephemeral = ephemeral
        self.prompt = prompt
        self.aspect_ratio = aspect_ratio
        self.negative_prompt = negative_prompt
        self.model = model
        self.steps = steps
        self.cfg_scale = cfg_scale
        self.improve_prompt_flag = improve_prompt_flag
        self.message = None
        self.last_regenerate_time = 0
        self.cooldown = 60  # 1 минута

        if not ephemeral:
            self.delete_button = Button(label="🗑️ Удалить", style=ButtonStyle.danger, custom_id="delete_image")
            self.delete_button.callback = self.delete_message_callback
            self.add_item(self.delete_button)
            asyncio.create_task(self.disable_delete_button())

        self.regenerate_button = Button(label="🔄 Перегенерировать", style=ButtonStyle.primary, custom_id="regenerate_image")
        self.regenerate_button.callback = self.regenerate_button_callback
        self.add_item(self.regenerate_button)

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
            logger.error(f"Ошибка отключения кнопки удаления: {e}")

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

    async def regenerate_button_callback(self, interaction: discord.Interaction):
        current_time = time.time()
        if current_time - self.last_regenerate_time < self.cooldown:
            remaining = int(self.cooldown - (current_time - self.last_regenerate_time))
            embed = Embed(title="⏳ Кулдаун", description=f"Подождите {remaining} секунд перед повторной генерацией.", color=0x3498DB)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        self.regenerate_button.disabled = True
        self.regenerate_button.label = "⌛ Генерация..."
        await interaction.response.edit_message(view=self)

        try:
            embed = Embed(title="⏳ Генерация", color=0x3498DB)
            embed.add_field(name="Прогресс", value=f"```{create_progress_bar(0)} 0%```", inline=False)
            await interaction.message.edit(embed=embed, view=None, attachments=[])

            await generate_image(
                interaction=interaction,
                prompt=self.prompt,
                aspect_ratio=self.aspect_ratio,
                negative_prompt=self.negative_prompt,
                model=self.model,
                steps=self.steps,
                cfg_scale=self.cfg_scale,
                improve_prompt_flag=self.improve_prompt_flag,
                ephemeral=self.ephemeral,
                view=self,
                message=interaction.message
            )
            self.last_regenerate_time = time.time()
            self.regenerate_button.disabled = False
            self.regenerate_button.label = "🔄 Перегенерировать"
        except Exception as e:
            self.regenerate_button.disabled = False
            self.regenerate_button.label = "🔄 Перегенерировать"
            embed = Embed(title="❌ Ошибка", description=str(e)[:CONFIG["discord_embed_limits"]["description"]], color=0xE74C3C)
            await interaction.message.edit(embed=embed, view=self, attachments=[])
            logger.error(f"Ошибка перегенерации для {interaction.user.id}: {str(e)}")

class SettingsView(View):
    """View для настройки параметров генерации."""
    def __init__(self, bot_client, ephemeral: bool, user_id: int, channel_id: int, message_id: str = None):
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
        self.is_prompt_improved = False
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
            options=[discord.SelectOption(label=k, value=k) for k in CONFIG["aspect_ratios"]],
            custom_id="aspect_ratio_select",
            row=1
        )
        self.aspect_ratio_select.callback = self.aspect_ratio_select_callback
        self.add_item(self.aspect_ratio_select)

        self.improve_button = Button(label="Улучшить промпт", style=ButtonStyle.primary, custom_id="improve_prompt", row=2)
        self.improve_button.callback = self.improve_button_callback
        self.add_item(self.improve_button)

        self.generate_button = Button(label="Генерировать", style=ButtonStyle.success, custom_id="generate_image", row=2)
        self.generate_button.callback = self.generate_button_callback
        self.add_item(self.generate_button)

        self.prompt_button = Button(label="Укажите промпт", style=ButtonStyle.primary, custom_id="open_prompt", row=3)
        self.prompt_button.callback = self.open_prompt_button
        self.add_item(self.prompt_button)

        self.settings_button = Button(label="Настройки", style=ButtonStyle.secondary, custom_id="open_settings", row=3)
        self.settings_button.callback = self.open_settings_button
        self.add_item(self.settings_button)

    def disable_all(self):
        """Отключает все элементы управления."""
        for item in self.children:
            item.disabled = True

    def enable_all(self):
        """Включает все элементы управления, кроме улучшенного промпта, если он обработан."""
        for item in self.children:
            item.disabled = False
        self.improve_button.disabled = self.is_prompt_improved

    def disable_permanently(self):
        """Полностью отключает и останавливает View."""
        self.disable_all()
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Вы не автор команды.", ephemeral=True)
            return False
        return True

    async def model_select_callback(self, interaction: discord.Interaction):
        async with self.view_lock:
            self.disable_all()
            await interaction.response.edit_message(view=self)
            self.model = self.model_select.values[0]
            embed = Embed(title="⚙️ Настройки", color=0x3498DB)
            embed.description = f"**📝 Промпт**:\n```{self.prompt}```"
            embed = await truncate_embed(embed)
            embed.add_field(name="🤖 Модель", value=f"> `{CONFIG['models'][self.model]}`", inline=True)
            embed.add_field(name="📏 Соотношение", value=f"> `{self.aspect_ratio}`", inline=True)
            embed.add_field(name="🔄 Шаги", value=f"> `{self.steps}`", inline=True)
            embed.add_field(name="⚖️ CFG", value=f"> `{self.cfg_scale}`", inline=True)
            self.enable_all()
            await interaction.message.edit(embed=embed, view=self)

    async def aspect_ratio_select_callback(self, interaction: discord.Interaction):
        async with self.view_lock:
            self.disable_all()
            await interaction.response.edit_message(view=self)
            self.aspect_ratio = self.aspect_ratio_select.values[0]
            embed = Embed(title="⚙️ Настройки", color=0x3498DB)
            embed.description = f"**📝 Промпт**:\n```{self.prompt}```"
            embed = await truncate_embed(embed)
            embed.add_field(name="🤖 Модель", value=f"> `{CONFIG['models'][self.model]}`", inline=True)
            embed.add_field(name="📏 Соотношение", value=f"> `{self.aspect_ratio}`", inline=True)
            embed.add_field(name="🔄 Шаги", value=f"> `{self.steps}`", inline=True)
            embed.add_field(name="⚖️ CFG", value=f"> `{self.cfg_scale}`", inline=True)
            self.enable_all()
            await interaction.message.edit(embed=embed, view=self)

    async def open_prompt_button(self, interaction: discord.Interaction):
        async with self.view_lock:
            modal = PromptModal(self.bot_client, self, self.user_id)
            await interaction.response.send_modal(modal)

    async def open_settings_button(self, interaction: discord.Interaction):
        async with self.view_lock:
            modal = SettingsModal(self.bot_client, self, self.user_id)
            await interaction.response.send_modal(modal)

    async def improve_button_callback(self, interaction: discord.Interaction):
        async with self.view_lock:
            if self.is_prompt_improved:
                await interaction.response.send_message("❌ Промпт уже улучшен.", ephemeral=True)
                return

            self.disable_all()
            self.improve_button.label = "⌛ Улучшение..."
            await interaction.response.edit_message(view=self)

            try:
                improved = await improve_prompt(self.prompt, nsfw_allowed=True)
                self.prompt = improved
                self.is_prompt_improved = True
                self.improve_prompt_flag = True
                self.improve_button.label = "Улучшить промпт"
                self.improve_button.disabled = True
                embed = Embed(title="⚙️ Настройки", color=0x3498DB)
                embed.description = f"**📝 Улучшенный промпт**:\n```{self.prompt}```"
                embed = await truncate_embed(embed)
                embed.add_field(name="🤖 Модель", value=f"> `{CONFIG['models'][self.model]}`", inline=True)
                embed.add_field(name="📏 Соотношение", value=f"> `{self.aspect_ratio}`", inline=True)
                embed.add_field(name="🔄 Шаги", value=f"> `{self.steps}`", inline=True)
                embed.add_field(name="⚖️ CFG", value=f"> `{self.cfg_scale}`", inline=True)
                self.enable_all()
                await interaction.message.edit(embed=embed, view=self)
            except Exception as e:
                self.improve_button.label = "Улучшить промпт"
                self.enable_all()
                embed = Embed(title="❌ Ошибка", description="Не удалось улучшить промпт.", color=0xE74C3C)
                await interaction.message.edit(embed=embed, view=self)
                logger.error(f"Ошибка улучшения промпта для {interaction.user.id}: {str(e)}")

    async def generate_button_callback(self, interaction: discord.Interaction):
        async with self.view_lock:
            self.disable_permanently()
            embed = Embed(title="⏳ Генерация", color=0x3498DB)
            embed.add_field(name="Прогресс", value=f"```{create_progress_bar(0)} 0%```", inline=False)
            await interaction.response.edit_message(embed=embed, view=None, attachments=[])

            if interaction.message is None:
                embed = Embed(title="❌ Ошибка", description="Сообщение недоступно.", color=0xE74C3C)
                await interaction.followup.send(embed=embed, ephemeral=self.ephemeral)
                return

            if any(word in self.prompt.lower() for word in CONFIG["forbidden_words"]) or \
               (self.negative_prompt and any(word in self.negative_prompt.lower() for word in CONFIG["forbidden_words"])):
                embed = Embed(title="❌ Ошибка", description="Обнаружены запрещённые слова.", color=0xE74C3C)
                await interaction.message.edit(embed=embed, view=None, attachments=[])
                return

            if self.steps < 1 or self.steps > 100:
                embed = Embed(title="❌ Ошибка", description="Шаги должны быть в диапазоне 1–100.", color=0xE74C3C)
                await interaction.message.edit(embed=embed, view=None, attachments=[])
                return

            if self.cfg_scale < 1.0 or self.cfg_scale > 20.0:
                embed = Embed(title="❌ Ошибка", description="CFG Scale должен быть в диапазоне 1.0–20.0.", color=0xE74C3C)
                await interaction.message.edit(embed=embed, view=None, attachments=[])
                return

            if self.model not in CONFIG["models"]:
                embed = Embed(title="❌ Ошибка", description=f"Модель должна быть одной из: {', '.join(CONFIG['models'].keys())}.", color=0xE74C3C)
                await interaction.message.edit(embed=embed, view=None, attachments=[])
                return

            try:
                if generation_queue.full():
                    embed = Embed(title="⌛ Очередь", description="Очередь заполнена, пожалуйста, подождите.", color=0x3498DB)
                    await interaction.message.edit(embed=embed, view=None, attachments=[])
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
                await interaction.message.edit(embed=embed, view=None, attachments=[])

def create_command(bot_client):
    """Создает группу команд для генерации изображений."""
    group = app_commands.Group(name="image", description="Команды для генерации изображений")
    group.dm_only = False

    @group.command(name="generate", description="Генерирует изображение на основе параметров")
    @app_commands.describe(ephemeral="Скрыть сообщения от других пользователей")
    async def generate(interaction: discord.Interaction, ephemeral: bool = False):
        await interaction.response.defer(ephemeral=ephemeral)

        if bot_client is None:
            logger.error("bot_client не предоставлен")
            embed = Embed(title="❌ Ошибка", description="Внутренняя ошибка бота.", color=0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        if not await restrict_command_execution(interaction, bot_client):
            return

        access_result, access_reason = await check_bot_access(interaction, bot_client)
        if not access_result:
            embed = Embed(title="❌ Ошибка", description=access_reason or "У вас нет доступа к этой команде.", color=0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        if interaction.guild is not None and not interaction.channel.nsfw:
            embed = Embed(title="❌ Ошибка", description="Эта команда доступна только в личных сообщениях или в NSFW-каналах.", color=0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        view = SettingsView(bot_client, ephemeral, interaction.user.id, interaction.channel_id)
        embed = Embed(title="⚙️ Настройки", color=0x3498DB)
        embed.description = f"**📝 Промпт**:\n```{view.prompt}```"
        embed = await truncate_embed(embed)
        embed.add_field(name="🤖 Модель", value=f"> `{CONFIG['models'][view.model]}`", inline=True)
        embed.add_field(name="📏 Соотношение", value=f"> `{view.aspect_ratio}`", inline=True)
        embed.add_field(name="🔄 Шаги", value=f"> `{view.steps}`", inline=True)
        embed.add_field(name="⚖️ CFG", value=f"> `{view.cfg_scale}`", inline=True)
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=ephemeral)
        view.message_id = message.id

    return group
