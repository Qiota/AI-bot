# bot_commands/say.py
from typing import List, Optional, Tuple
import discord
from discord import app_commands
import aiohttp
import io
import re
import traceback
import asyncio
from ..systemLog import logger

DESCRIPTION = "Говорить от имени бота, с файлами или ответом на сообщение"

async def check_permissions(interaction: discord.Interaction, bot_client) -> bool:
    """Проверяет, имеет ли пользователь права для выполнения команды."""
    if interaction.guild is None:
        return interaction.user.id == bot_client.config.DEVELOPER_ID

    permissions = (
        interaction.user.guild_permissions.administrator,
        interaction.user.guild_permissions.manage_channels,
        interaction.user.guild_permissions.manage_messages
    )
    return interaction.user.id == bot_client.config.DEVELOPER_ID or (
        isinstance(interaction.user, discord.Member) and any(permissions)
    )

async def get_message_reference(interaction: discord.Interaction, reply: Optional[str]) -> Tuple[Optional[discord.MessageReference], Optional[discord.Message]]:
    """Получает ссылку на сообщение для ответа и само сообщение."""
    if not reply:
        return None, None

    url_pattern = r"https?://discord\.com/channels/(?:@me|\d+)/(\d+)/(\d+)"
    match = re.match(url_pattern, reply.strip())
    
    if match:
        channel_id, message_id = map(int, match.groups())
        try:
            channel = interaction.client.get_channel(channel_id) or await interaction.client.fetch_channel(channel_id)
            target_message = await channel.fetch_message(message_id)
            return target_message.to_reference(fail_if_not_exists=False), target_message
        except (discord.NotFound, discord.HTTPException) as e:
            logger.error(f"Ошибка получения сообщения {message_id} для {interaction.user.id}: {e}")
            return None, None

    try:
        message_id = int(reply)
        target_message = await interaction.channel.fetch_message(message_id)
        return target_message.to_reference(fail_if_not_exists=False), target_message
    except ValueError:
        logger.error(f"Неверный reply от {interaction.user.id}: {reply}")
        return None, None
    except (discord.NotFound, discord.HTTPException) as e:
        logger.error(f"Ошибка получения сообщения {reply} для {interaction.user.id}: {e}")
        return None, None

async def download_attachment(session: aiohttp.ClientSession, attachment: discord.Attachment, max_size: int) -> Optional[discord.File]:
    """Асинхронно загружает одно вложение, возвращает discord.File или None при ошибке."""
    try:
        if attachment.size > max_size:
            logger.warning(f"Файл {attachment.filename} слишком большой (макс. {max_size // 1024 // 1024} МБ).")
            return None
        async with session.get(attachment.url) as resp:
            if resp.status != 200:
                logger.warning(f"Ошибка загрузки файла {attachment.filename}: HTTP {resp.status}")
                return None
            data = await resp.read()
            return discord.File(fp=io.BytesIO(data), filename=attachment.filename)
    except Exception as e:
        logger.error(f"Ошибка при загрузке {attachment.filename}: {e}")
        return None

async def process_attachments(attachments: List[discord.Attachment]) -> List[discord.File]:
    """Параллельно загружает вложения и возвращает список discord.File, игнорируя некритичные ошибки."""
    max_size = 25 * 1024 * 1024  # 25 МБ
    max_files = 10  # Максимум 10 вложений

    async with aiohttp.ClientSession() as session:
        tasks = [
            download_attachment(session, att, max_size)
            for att in attachments[:max_files]
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        discord_files = [file for file in results if file is not None]

    if not discord_files and attachments:
        raise ValueError("Не удалось загрузить ни один файл.")
    
    return discord_files

async def say(interaction: discord.Interaction, message: Optional[str] = None, bot_client=None, reply: Optional[str] = None, attachment: Optional[discord.Attachment] = None) -> None:
    """Команда /say: Отправляет сообщение от имени бота с опциональными файлами и ответом."""
    # Собираем все вложения
    attachments = [attachment] if attachment else []
    additional_attachments = [
        discord.Attachment(data=att, state=interaction._state)
        for att in interaction.data.get('resolved', {}).get('attachments', {}).values()
        if not attachment or str(att.get('id')) != str(attachment.id)
    ]
    attachments.extend(additional_attachments)

    # Определяем, нужно ли делать команду эфемерной
    is_ephemeral = bool(attachments)

    # Немедленный defer
    await interaction.response.defer(ephemeral=is_ephemeral)

    try:
        # Проверка прав
        if not await check_permissions(interaction, bot_client):
            await interaction.followup.send(
                "Нет прав для выполнения команды.", ephemeral=True, delete_after=10
            )
            return

        # Проверяем наличие контента
        if not message and not attachments and not reply:
            raise ValueError("Нельзя отправить полностью пустое сообщение.")

        # Получаем ссылку на сообщение
        reference, target_message = await get_message_reference(interaction, reply)
        if reference is None and reply:
            raise ValueError("Ошибка: reply должен быть числовым ID или ссылкой на сообщение.")

        # Обрабатываем вложения
        discord_files = await process_attachments(attachments) if attachments else None

        # Отправляем сообщение публично
        try:
            sent_message = await interaction.channel.send(
                content=message[:2000] if message else None,
                files=discord_files or None,
                reference=reference
            )
        except discord.HTTPException as e:
            if e.code == 50035 and reference and target_message:  # Слишком длинное сообщение
                sent_message = await target_message.reply(
                    content=message[:2000] if message else None,
                    files=discord_files or None
                )
            else:
                raise e

        # Удаляем исходное взаимодействие, если оно было эфемерным
        if is_ephemeral:
            await interaction.delete_original_response()

    except ValueError as e:
        await interaction.followup.send(str(e), ephemeral=True, delete_after=10)
        logger.error(f"Ошибка ValueError в /say для {interaction.user.id}: {e}")
    except discord.HTTPException as e:
        error_messages = {
            400: "Некорректный запрос к Discord API.",
            50035: "Сообщение слишком длинное (макс. 2000 символов).",
            50006: "Нельзя отправить пустое сообщение.",
            50013: "У бота нет прав для отправки сообщения или файлов.",
            10062: "Взаимодействие устарело или не существует."
        }
        await interaction.followup.send(
            error_messages.get(e.code, f"Ошибка отправки: {e}"), ephemeral=True, delete_after=10
        )
        logger.error(f"Ошибка HTTP в /say для {interaction.user.id}: {e}\n{traceback.format_exc()}")
    except Exception as e:
        await interaction.followup.send(
            f"Неизвестная ошибка: {e}", ephemeral=True, delete_after=10
        )
        logger.error(f"Неизвестная ошибка в /say для {interaction.user.id}: {e}\n{traceback.format_exc()}")

def create_command(bot_client):
    """Создаёт команду /say и контекстное меню."""
    @app_commands.command(name="say", description=DESCRIPTION)
    @app_commands.describe(
        message="Текст, который бот должен отправить",
        reply="ID сообщения, на которое нужно ответить",
        attachment="Файл для отправки"
    )
    async def say_command(interaction: discord.Interaction, message: Optional[str] = None, reply: Optional[str] = None, attachment: Optional[discord.Attachment] = None) -> None:
        """Команда /say для отправки сообщения от имени бота."""
        await say(interaction, message, bot_client, reply, attachment)

    @app_commands.context_menu(name="ID сообщения")
    async def get_message_id(interaction: discord.Interaction, message: discord.Message) -> None:
        """Получает и отправляет ID сообщения, автоматически удаляя ответ через 10 секунд."""
        try:
            await interaction.response.send_message(f"```{message.id}```", ephemeral=True, delete_after=10)
        except discord.HTTPException as e:
            logger.error(f"Ошибка в get_message_id для {interaction.user.id}: {e}\n{traceback.format_exc()}")

    return say_command, get_message_id