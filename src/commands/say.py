from typing import List, Optional
import discord
from discord import app_commands
import aiohttp
import io
import re
import asyncio
from ..systemLog import logger

DESCRIPTION = "Говорить от имени бота, с файлами или ответом на сообщение"

async def check_permissions(interaction: discord.Interaction, bot_client) -> bool:
    """Проверяет права пользователя для выполнения команды."""
    if interaction.guild is None:
        if interaction.user.id != bot_client.config.DEVELOPER_ID:
            await interaction.response.send_message(
                "Команда доступна только на сервере или разработчику.", ephemeral=True
            )
            logger.warning(f"Пользователь {interaction.user.id} попытался использовать /say вне сервера без прав.")
            return False
        return True

    if not (interaction.user.id == bot_client.config.DEVELOPER_ID or (
            isinstance(interaction.user, discord.Member) and (
                interaction.user.guild_permissions.administrator or
                interaction.user.guild_permissions.manage_channels or
                interaction.user.guild_permissions.manage_messages
            )
    )):
        await interaction.response.send_message("Нет прав для выполнения команды.", ephemeral=True)
        logger.warning(f"Пользователь {interaction.user.id} попытался использовать /say без прав.")
        return False
    return True

async def get_message_reference(interaction: discord.Interaction, reply: Optional[str]) -> tuple[Optional[discord.MessageReference], Optional[discord.Message]]:
    """Получает ссылку на сообщение для ответа и само сообщение для резервного метода."""
    if not reply:
        return None, None

    # Обработка URL (включая @me для прямых сообщений)
    url_pattern = r"https?://discord\.com/channels/(?:@me|\d+)/(\d+)/(\d+)"
    match = re.match(url_pattern, reply.strip())
    
    if match:
        channel_id, message_id = map(int, match.groups())
        try:
            # Для прямых сообщений (@me) или сервера
            channel = interaction.client.get_channel(channel_id) or await interaction.client.fetch_channel(channel_id)
            target_message = await channel.fetch_message(message_id)
            return target_message.to_reference(fail_if_not_exists=False), target_message
        except (discord.NotFound, discord.HTTPException) as e:
            await interaction.edit_original_response(content=f"Ошибка получения сообщения по ссылке: {str(e)}")
            logger.error(f"Ошибка получения сообщения {message_id} для {interaction.user.id}: {e}")
            return None, None

    # Обработка ID сообщения
    try:
        message_id = int(reply)
        target_message = await interaction.channel.fetch_message(message_id)
        return target_message.to_reference(fail_if_not_exists=False), target_message
    except ValueError:
        await interaction.edit_original_response(content="Ошибка: reply должен быть числовым ID или ссылкой на сообщение.")
        logger.error(f"Неверный reply от {interaction.user.id}: {reply}")
        return None, None
    except (discord.NotFound, discord.HTTPException) as e:
        await interaction.edit_original_response(content=f"Ошибка получения сообщения: {str(e)}")
        logger.error(f"Ошибка получения сообщения {reply} для {interaction.user.id}: {e}")
        return None, None

async def process_attachments(attachment: Optional[discord.Attachment], additional_attachments: List[discord.Attachment]) -> List[discord.File]:
    """Загружает вложения (основное и дополнительные) и возвращает список discord.File."""
    discord_files = []
    
    # Обработка основного вложения (attachment)
    if attachment:
        if attachment.size > 25 * 1024 * 1024:
            raise ValueError(f"Файл {attachment.filename} слишком большой (макс. 25 МБ).")
        async with aiohttp.ClientSession() as session:
            async with session.get(attachment.url) as resp:
                if resp.status != 200:
                    raise ValueError(f"Ошибка загрузки файла {attachment.filename}: HTTP {resp.status}")
                data = await resp.read()
                discord_files.append(discord.File(fp=io.BytesIO(data), filename=attachment.filename))

    # Обработка дополнительных вложений (до 10 файлов в сумме)
    for att in additional_attachments[:10 - len(discord_files)]:
        if att.size > 25 * 1024 * 1024:
            raise ValueError(f"Файл {att.filename} слишком большой (макс. 25 МБ).")
        async with aiohttp.ClientSession() as session:
            async with session.get(att.url) as resp:
                if resp.status != 200:
                    raise ValueError(f"Ошибка загрузки файла {att.filename}: HTTP {resp.status}")
                data = await resp.read()
                discord_files.append(discord.File(fp=io.BytesIO(data), filename=att.filename))
    
    return discord_files

async def say(interaction: discord.Interaction, message: Optional[str] = None, bot_client=None, reply: Optional[str] = None, attachment: Optional[discord.Attachment] = None) -> None:
    """Команда /say: Отправляет сообщение от имени бота с опциональными файлами и ответом."""
    if not await check_permissions(interaction, bot_client):
        return

    await interaction.response.defer(ephemeral=True)

    try:
        # Получение дополнительных вложений
        additional_attachments = interaction.data.get('resolved', {}).get('attachments', {}).values()
        additional_attachments = [discord.Attachment(data=att, state=interaction._state) for att in additional_attachments]
        # Удаляем основное вложение из списка дополнительных, чтобы избежать дублирования
        if attachment and str(attachment.id) in [str(att.id) for att in additional_attachments]:
            additional_attachments = [att for att in additional_attachments if str(att.id) != str(attachment.id)]

        # Проверка: если есть reply, то должен быть message или файлы
        if reply and not (message or attachment or additional_attachments):
            await interaction.edit_original_response(content="Для ответа на сообщение нужен текст или хотя бы один файл.")
            logger.warning(f"Пользователь {interaction.user.id} указал reply без message и файлов.")
            return

        reference, target_message = await get_message_reference(interaction, reply)
        if reference is None and reply:
            return  # Ошибка уже обработана в get_message_reference

        discord_files = await process_attachments(attachment, additional_attachments)

        # Если нет ни сообщения, ни файлов, отправка невозможна
        if not message and not discord_files:
            await interaction.edit_original_response(content="Нельзя отправить пустое сообщение без файлов.")
            logger.warning(f"Пользователь {interaction.user.id} попытался отправить пустое сообщение без файлов.")
            return

        # Попробуем отправить с использованием reference
        try:
            await interaction.channel.send(
                content=message[:2000] if message else None,
                files=discord_files or None,
                reference=reference
            )
        except discord.HTTPException as e:
            if e.code == 50035 and reference and target_message:
                await target_message.reply(
                    content=message[:2000] if message else None,
                    files=discord_files or None
                )
            else:
                raise e

        await interaction.delete_original_response()

    except ValueError as e:
        await interaction.edit_original_response(content=str(e))
        logger.error(f"Ошибка ValueError в /say для {interaction.user.id}: {e}")
    except discord.HTTPException as e:
        error_messages = {
            50035: "Сообщение слишком длинное (макс. 2000 символов).",
            50006: "Нельзя отправить пустое сообщение.",
            50013: "У бота нет прав для отправки сообщения или файлов."
        }
        await interaction.edit_original_response(content=error_messages.get(e.code, f"Ошибка отправки: {e}"))
        logger.error(f"Ошибка HTTP в /say для {interaction.user.id}: {e}")
    except Exception as e:
        await interaction.edit_original_response(content=f"Неизвестная ошибка: {e}")
        logger.error(f"Неизвестная ошибка в /say для {interaction.user.id}: {e}")

def create_command(bot_client):
    """Создаёт команду /say и контекстное меню."""

    @app_commands.command(name="say", description=DESCRIPTION)
    @app_commands.describe(
        message="Текст, который бот должен отправить",
        reply="ID сообщения на которое нужно ответить",
        attachment="Файл для отправки"
    )
    async def say_command(interaction: discord.Interaction, message: Optional[str] = None, reply: Optional[str] = None, attachment: Optional[discord.Attachment] = None) -> None:
        await say(interaction, message, bot_client, reply, attachment)

    @app_commands.context_menu(name="ID сообщения")
    async def get_message_id(interaction: discord.Interaction, message: discord.Message) -> None:
        await interaction.response.send_message(f"```{message.id}```", ephemeral=True)
        
        await asyncio.sleep(10)
        await interaction.delete_original_response()

    return say_command, get_message_id