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

description = "Генерирует изображение, вдохновлённое editor.imagelabs.net"
command_lock = Lock()
generation_queue = Queue(maxsize=5)
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

def create_progress_bar(progress: float, length: int = 20) -> str:
    filled = int(length * progress)
    return "█" * filled + "░" * (length - filled)

async def update_progress(interaction: discord.Interaction, progress: float, message: discord.Message, ephemeral: bool):
    embed = Embed(title="⏳ Генерация", color=0x3498DB)
    embed.add_field(name="Прогресс", value=f"```{create_progress_bar(progress)} {int(progress * 100)}%```", inline=False)
    try:
        await message.edit(embed=embed)
    except discord.errors.NotFound:
        pass

async def improve_prompt(prompt: str, nsfw_allowed: bool = False) -> str:
    client = AsyncClient(provider=Websim)
    for model in ["gemini-1.5-pro", "gemini-1.5-flash"]:
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Enhance prompt for image generation with vivid details: colors, lighting, textures, environment, tone. "
                            f"{'Allow tasteful NSFW if present.' if nsfw_allowed else 'Avoid NSFW.'} "
                            "Return only improved prompt, max 900 chars, no Markdown."
                        )
                    },
                    {"role": "user", "content": f"Enhance: {prompt}."}
                ],
                max_tokens=900,
                temperature=0.6
            )
            improved = response.choices[0].message.content.strip()[:200]
            cleaned = re.sub(r'\[.*?\]\(.*?\)|<!--.*?-->|https?://\S+', '', improved).strip()
            if cleaned:
                return cleaned
        except Exception as e:
            logger.error(f"Ошибка улучшения с {model}: {e}")
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
                if len(image_data) > MAX_FILE_SIZE:
                    raise ValueError("Изображение > 8 МБ.")

        await update_progress(interaction, 0.8, message, ephemeral)
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
        embed = Embed(title="🎨 Готово!", color=0x1ABC9C)
        embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/3659/3659898.png")
        if improve_prompt_flag and final_prompt != original_prompt:
            embed.add_field(name="📝 Исходный", value=f"```{original_prompt[:1000]}[...]```", inline=False)
            embed.add_field(name="✨ Улучшенный", value=f"```{final_prompt[:1000]}[...]```", inline=False)
        else:
            embed.add_field(name="📝 Промпт", value=f"```{final_prompt[:1000]}[...]```", inline=False)
        embed.add_field(name="🤖 Модель", value=f"**{MODELS[model]}**", inline=True)
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
            embed = Embed(title="❌ Ошибка", description="Явный контент.", color=0xE74C3C)
            await message.edit(embed=embed, view=None)
        else:
            embed = Embed(title="❌ Ошибка", description=error_str[:1000], color=0xE74C3C)
            await message.edit(embed=embed, view=None)
        logger.error(f"Ошибка /image для {interaction.user.id}: {error_str}")

    finally:
        await generation_queue.get()
        generation_queue.task_done()

class SettingsModal(Modal):
    def __init__(self, bot_client, view: 'SettingsView', user_id: int):
        super().__init__(title="Настройки")
        self.bot_client = bot_client
        self.view = view
        self.user_id = user_id

        self.prompt_input = TextInput(
            label="Промпт",
            placeholder=DEFAULT_PROMPT,
            default=self.view.prompt,
            required=True,
            max_length=1000
        )
        self.negative_prompt_input = TextInput(
            label="Отрицательный промпт",
            placeholder="Оставьте пустым",
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
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Не автор.", ephemeral=True)
            return

        async with self.view.view_lock:
            await interaction.response.defer(ephemeral=self.view.ephemeral)
            self.view.prompt = self.prompt_input.value
            self.view.negative_prompt = self.negative_prompt_input.value or ""
            try:
                self.view.steps = int(self.steps_input.value)
                self.view.cfg_scale = float(self.cfg_scale_input.value)
            except ValueError:
                embed = Embed(title="❌ Ошибка", description="Шаги и CFG — числа.", color=0xE74C3C)
                await interaction.followup.send(embed=embed, ephemeral=self.view.ephemeral)
                return
            embed = Embed(title="⚙️ Настройки", color=0x3498DB)
            embed.add_field(name="📝 Промпт", value=f"```{self.view.prompt[:1000]}[...]```", inline=False)
            embed.add_field(name="🤖 Модель", value=f"> `{MODELS[self.view.model]}`", inline=True)
            embed.add_field(name="📏 Соотношение", value=f"> `{self.view.aspect_ratio}`", inline=True)
            embed.add_field(name="🔄 Шаги", value=f"> `{self.view.steps}`", inline=True)
            embed.add_field(name="⚖️ CFG", value=f"> `{self.view.cfg_scale}`", inline=True)
            self.view.enable_all_buttons()
            await interaction.message.edit(embed=embed, view=self.view)

class ImageResponseView(View):
    def __init__(self, user_id: int, ephemeral: bool):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.ephemeral = ephemeral
        self.message = None

        if not ephemeral:
            self.delete_button = Button(label="🗑️", style=ButtonStyle.danger, custom_id="delete_image")
            self.delete_button.callback = self.delete_message_callback
            self.add_item(self.delete_button)
            asyncio.create_task(self.disable_delete_button())

    async def disable_delete_button(self):
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
            await interaction.response.send_message("❌ Не автор.", ephemeral=True)
            return False
        return True

    async def delete_message_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=self.ephemeral)
        try:
            await interaction.message.delete()
        except discord.errors.NotFound:
            pass
        except Exception as e:
            embed = Embed(title="❌ Ошибка", description="Не удалось удалить.", color=0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=self.ephemeral)
            logger.error(f"Ошибка удаления: {e}")

class SettingsView(View):
    def __init__(self, bot_client, ephemeral: bool, user_id: int, channel_id: int, message_id: int):
        super().__init__(timeout=300)
        self.bot_client = bot_client
        self.ephemeral = ephemeral
        self.user_id = user_id
        self.channel_id = channel_id
        self.message_id = message_id
        self.prompt = DEFAULT_PROMPT
        self.negative_prompt = ""
        self.model = DEFAULT_SETTINGS["model"]
        self.aspect_ratio = DEFAULT_SETTINGS["aspect_ratio"]
        self.steps = DEFAULT_SETTINGS["steps"]
        self.cfg_scale = DEFAULT_SETTINGS["cfg_scale"]
        self.improve_prompt_flag = DEFAULT_SETTINGS["improve_prompt"]
        self.view_lock = Lock()

        self.model_select = Select(
            placeholder="Модель",
            options=[discord.SelectOption(label=v, value=k) for k, v in MODELS.items()],
            custom_id="model_select",
            row=0
        )
        self.model_select.callback = self.model_select_callback
        self.add_item(self.model_select)

        self.aspect_ratio_select = Select(
            placeholder="Соотношение",
            options=[discord.SelectOption(label=f"{k}", value=k) for k, v in ASPECT_RATIOS.items()],
            custom_id="aspect_ratio_select",
            row=1
        )
        self.aspect_ratio_select.callback = self.aspect_ratio_select_callback
        self.add_item(self.aspect_ratio_select)

        self.improve_prompt_button = Button(label="Улучшить", style=ButtonStyle.primary, custom_id="improve_prompt", row=2)
        self.improve_prompt_button.callback = self.improve_prompt_button_callback
        self.add_item(self.improve_prompt_button)

        self.generate_button = Button(label="Генерировать", style=ButtonStyle.success, custom_id="generate_image", row=2)
        self.generate_button.callback = self.generate_button_callback
        self.add_item(self.generate_button)

        self.settings_button = Button(label="Настройки", style=ButtonStyle.green, custom_id="open_settings", row=2)
        self.settings_button.callback = self.open_settings_button
        self.add_item(self.settings_button)

    def disable_all_buttons(self):
        self.model_select.disabled = True
        self.aspect_ratio_select.disabled = True
        self.improve_prompt_button.disabled = True
        self.generate_button.disabled = True
        self.settings_button.disabled = True

    def enable_all_buttons(self):
        self.model_select.disabled = False
        self.aspect_ratio_select.disabled = False
        self.improve_prompt_button.disabled = False
        self.improve_prompt_button.label = "Улучшить"
        self.generate_button.disabled = False
        self.generate_button.label = "Генерировать"
        self.settings_button.disabled = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Не автор.", ephemeral=True)
            return False
        return True

    async def model_select_callback(self, interaction: discord.Interaction):
        async with self.view_lock:
            self.disable_all_buttons()
            await interaction.response.edit_message(view=self)
            self.model = self.model_select.values[0]
            embed = Embed(title="⚙️ Настройки", color=0x3498DB)
            embed.add_field(name="📝 Промпт", value=f"```{self.prompt[:1000]}[...]```", inline=False)
            embed.add_field(name="🤖 Модель", value=f"> `{MODELS[self.model]}`", inline=True)
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
            embed.add_field(name="📝 Промпт", value=f"```{self.prompt[:1000]}[...]```", inline=False)
            embed.add_field(name="🤖 Модель", value=f"> `{MODELS[self.model]}`", inline=True)
            embed.add_field(name="📏 Соотношение", value=f"> `{self.aspect_ratio}`", inline=True)
            embed.add_field(name="🔄 Шаги", value=f"> `{self.steps}`", inline=True)
            embed.add_field(name="⚖️ CFG", value=f"> `{self.cfg_scale}`", inline=True)
            self.enable_all_buttons()
            await interaction.message.edit(embed=embed, view=self)

    async def open_settings_button(self, interaction: discord.Interaction):
        async with self.view_lock:
            modal = SettingsModal(self.bot_client, self, self.user_id)
            await interaction.response.send_modal(modal)

    async def improve_prompt_button_callback(self, interaction: discord.Interaction):
        async with self.view_lock:
            self.disable_all_buttons()
            self.improve_prompt_button.label = "⌛"
            await interaction.response.edit_message(view=self)
            improved = await improve_prompt(self.prompt, nsfw_allowed=True)
            self.prompt = improved
            embed = Embed(title="⚙️ Настройки", color=0x3498DB)
            embed.add_field(name="📝 Промпт", value=f"```{self.prompt[:1000]}[...]```", inline=False)
            embed.add_field(name="🤖 Модель", value=f"> `{MODELS[self.model]}`", inline=True)
            embed.add_field(name="📏 Соотношение", value=f"> `{self.aspect_ratio}`", inline=True)
            embed.add_field(name="🔄 Шаги", value=f"> `{self.steps}`", inline=True)
            embed.add_field(name="⚖️ CFG", value=f"> `{self.cfg_scale}`", inline=True)
            self.enable_all_buttons()
            await interaction.message.edit(embed=embed, view=self)

    async def generate_button_callback(self, interaction: discord.Interaction):
        async with self.view_lock:
            self.disable_all_buttons()
            self.generate_button.label = "⌛"
            await interaction.response.edit_message(view=self)

            if interaction.message is None:
                embed = Embed(title="❌ Ошибка", description="Сообщение недоступно.", color=0xE74C3C)
                await interaction.response.send_message(embed=embed, ephemeral=self.ephemeral)
                return

            if any(word in self.prompt.lower() for word in FORBIDDEN_WORDS) or \
               (self.negative_prompt and any(word in self.negative_prompt.lower() for word in FORBIDDEN_WORDS)):
                embed = Embed(title="❌ Ошибка", description="Запрещённые слова.", color=0xE74C3C)
                self.enable_all_buttons()
                await interaction.message.edit(embed=embed, view=self)
                return

            if self.steps < 1 or self.steps > 100:
                embed = Embed(title="❌ Ошибка", description="Шаги: 1–100.", color=0xE74C3C)
                self.enable_all_buttons()
                await interaction.message.edit(embed=embed, view=self)
                return

            if self.cfg_scale < 1.0 or self.cfg_scale > 20.0:
                embed = Embed(title="❌ Ошибка", description="CFG: 1.0–20.0.", color=0xE74C3C)
                self.enable_all_buttons()
                await interaction.message.edit(embed=embed, view=self)
                return

            if self.model not in MODELS:
                embed = Embed(title="❌ Ошибка", description=f"Модель: {', '.join(MODELS.keys())}.", color=0xE74C3C)
                self.enable_all_buttons()
                await interaction.message.edit(embed=embed, view=self)
                return

            try:
                if generation_queue.full():
                    embed = Embed(title="⌛ Очередь", description="Подождите.", color=0x3498DB)
                    await interaction.message.edit(embed=embed, view=self)
                    await generation_queue.put(interaction)

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
                        self.ephemeral,
                        self,
                        interaction.message
                    )
            except asyncio.QueueFull:
                embed = Embed(title="❌ Ошибка", description="Очередь заполнена.", color=0xE74C3C)
                self.enable_all_buttons()
                await interaction.message.edit(embed=embed, view=self)

def create_command(bot_client):
    group = app_commands.Group(name="image", description="Изображения")
    group.dm_only = False

    @group.command(name="generate", description="Генерация изображения")
    @app_commands.describe(ephemeral="Скрыть сообщения")
    async def generate(interaction: discord.Interaction, ephemeral: bool = False):
        if bot_client is None:
            logger.error("bot_client не предоставлен")
            await interaction.response.send_message("Ошибка бота.", ephemeral=True)
            return

        if not await restrict_command_execution(interaction, bot_client):
            return

        access_result, access_reason = await check_bot_access(interaction, bot_client)
        if not access_result:
            await interaction.response.send_message(access_reason or "Нет доступа.", ephemeral=True)
            return

        if interaction.guild is not None and not interaction.channel.nsfw:
            embed = Embed(title="❌ Ошибка", description="Только ЛС или NSFW.", color=0xE74C3C)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=ephemeral)
        view = SettingsView(bot_client, ephemeral, interaction.user.id, interaction.channel_id, None)
        embed = Embed(title="⚙️ Настройки", color=0x3498DB)
        embed.add_field(name="📝 Промпт", value=f"```{view.prompt[:1000]}[...]```", inline=False)
        embed.add_field(name="🤖 Модель", value=f"> `{MODELS[view.model]}`", inline=True)
        embed.add_field(name="📏 Соотношение", value=f"> `{view.aspect_ratio}`", inline=True)
        embed.add_field(name="🔄 Шаги", value=f"> `{view.steps}`", inline=True)
        embed.add_field(name="⚖️ CFG", value=f"> `{view.cfg_scale}`", inline=True)
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=ephemeral)
        view.message_id = message.id

    return group