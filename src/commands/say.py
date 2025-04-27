from typing import List, Optional, Tuple
import discord
from discord import app_commands
import aiohttp
import io
import re
import asyncio
from ..systemLog import logger
from .restrict import check_bot_access, restrict_command_execution

DESCRIPTION = "Говорить от имени бота, с файлами или ответом на сообщение"

async def check_permissions(interaction: discord.Interaction, bot_client) -> bool:
    """Проверяет права пользователя для выполнения команды."""
    if bot_client is None or not hasattr(bot_client, 'config'):
        logger.error("bot_client или config отсутствует")
        return False
    if interaction.guild is None:
        return interaction.user.id == bot_client.config.DEVELOPER_ID
    permissions = (
        interaction.user.guild_permissions.administrator or
        interaction.user.guild_permissions.manage_channels or
        interaction.user.guild_permissions.manage_messages
    )
    return interaction.user.id == bot_client.config.DEVELOPER_ID or permissions

async def get_message_reference(interaction: discord.Interaction, reply: Optional[str]) -> Tuple[Optional[discord.MessageReference], Optional[discord.Message]]:
    """Получает ссылку на сообщение для ответа, проверяя, что сообщение в том же канале."""
    if not reply:
        return None, None

    url_pattern = r"https?://discord\.com/channels/(?:@me|\d+)/(\d+)/(\d+)"
    match = re.match(url_pattern, reply.strip())
    
    if match:
        channel_id, message_id = map(int, match.groups())
        try:
            channel = interaction.client.get_channel(channel_id) or await interaction.client.fetch_channel(channel_id)
            # Проверка, что сообщение в том же канале
            if channel.id != interaction.channel.id:
                raise ValueError("Сообщение, на которое хотите ответить реплаем, должно находиться в том же канале, где вызывается команда.")
            # Проверка, что бот имеет доступ к каналу
            if not isinstance(channel, (discord.TextChannel, discord.Thread)) or not channel.permissions_for(interaction.guild.me).read_messages:
                logger.error(f"Бот не имеет доступа к каналу {channel_id}")
                raise ValueError("Бот не имеет доступа к указанному каналу.")
            target_message = await channel.fetch_message(message_id)
            return target_message.to_reference(fail_if_not_exists=False), target_message
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"Ошибка доступа к каналу {channel_id} или сообщению {message_id}: {e}")
            raise ValueError("Бот не может получить доступ к сообщению или каналу.")
        except discord.NotFound:
            logger.error(f"Сообщение {message_id} в канале {channel_id} не найдено")
            raise ValueError("Указанное сообщение не найдено в этом канале.")

    try:
        message_id = int(reply)
        # Проверка, что сообщение в текущем канале
        target_message = await interaction.channel.fetch_message(message_id)
        return target_message.to_reference(fail_if_not_exists=False), target_message
    except ValueError:
        logger.error(f"Reply {reply} не является числовым ID")
        raise ValueError("reply должен быть числовым ID сообщения или ссылкой на сообщение в этом канале.")
    except discord.NotFound:
        logger.error(f"Сообщение с ID {reply} не найдено в канале {interaction.channel.id}")
        raise ValueError(f"Сообщение с ID {reply} не найдено в этом канале.")
    except discord.HTTPException as e:
        logger.error(f"Ошибка обработки reply {reply}: {e}")
        raise ValueError("Ошибка при получении сообщения. Убедитесь, что ID корректен и сообщение находится в этом канале.")

async def download_attachment(session: aiohttp.ClientSession, attachment: discord.Attachment, max_size: int) -> Optional[discord.File]:
    """Загружает одно вложение."""
    try:
        if attachment.size > max_size:
            logger.warning(f"Файл {attachment.filename} слишком большой (> {max_size // 1024 // 1024} МБ)")
            return None
        async with session.get(attachment.url) as resp:
            if resp.status != 200:
                logger.warning(f"Ошибка загрузки {attachment.filename}: HTTP {resp.status}")
                return None
            return discord.File(fp=io.BytesIO(await resp.read()), filename=attachment.filename)
    except Exception as e:
        logger.error(f"Ошибка загрузки {attachment.filename}: {e}")
        return None

async def process_attachments(attachments: List[discord.Attachment]) -> List[discord.File]:
    """Обрабатывает вложения параллельно."""
    max_size = 25 * 1024 * 1024  # 25 МБ
    max_files = 10  # Максимум 10 вложений
    async with aiohttp.ClientSession() as session:
        tasks = [download_attachment(session, att, max_size) for att in attachments[:max_files]]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        discord_files = [file for file in results if file]
    if not discord_files and attachments:
        raise ValueError("Не удалось загрузить ни один файл.")
    return discord_files

async def say(interaction: discord.Interaction, message: Optional[str] = None, bot_client=None, reply: Optional[str] = None, attachment: Optional[discord.Attachment] = None) -> None:
    """Команда /say: Отправляет сообщение от имени бота."""
    if bot_client is None:
        logger.error("bot_client не предоставлен")
        await interaction.response.send_message("Ошибка конфигурации бота.", ephemeral=True)
        return

    if not await restrict_command_execution(interaction, bot_client):
        return

    access_result, access_reason = await check_bot_access(interaction, bot_client)
    if not access_result:
        await interaction.response.send_message(access_reason or "Бот не имеет доступа.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        if not await check_permissions(interaction, bot_client):
            raise ValueError("Нет прав для выполнения команды.")

        attachments = [attachment] if attachment else []
        additional_attachments = [
            discord.Attachment(data=att, state=interaction._state)
            for att in interaction.data.get('resolved', {}).get('attachments', {}).values()
            if not attachment or str(att.get('id')) != str(attachment.id)
        ]
        attachments.extend(additional_attachments)

        if not message and not attachments and not reply:
            raise ValueError("Сообщение не может быть пустым.")

        reference, target_message = await get_message_reference(interaction, reply)

        discord_files = await process_attachments(attachments) if attachments else None

        try:
            sent_message = await interaction.channel.send(
                content=message[:2000] if message else None,
                files=discord_files or None,
                reference=reference
            )
            await interaction.delete_original_response()
        except discord.HTTPException as e:
            if e.code == 50035 and reference and target_message:
                sent_message = await target_message.reply(
                    content=message[:2000] if message else None,
                    files=discord_files or None
                )
                await interaction.delete_original_response()
            else:
                raise

    except ValueError as e:
        if not interaction.is_expired():
            followup = await interaction.followup.send(str(e), ephemeral=True)
            await asyncio.sleep(10)
            await followup.delete()
        logger.error(f"ValueError в /say для {interaction.user.id}: {e}")
    except discord.HTTPException as e:
        error_messages = {
            400: "Некорректный запрос к Discord API.",
            50035: "Сообщение слишком длинное (макс. 2000 символов).",
            50006: "Нельзя отправить пустое сообщение.",
            50013: "У бота нет прав для отправки.",
            10062: "Взаимодействие устарело."
        }
        if not interaction.is_expired() and e.code != 10062:
            followup = await interaction.followup.send(
                error_messages.get(e.code, f"Ошибка отправки: {e}"), ephemeral=True
            )
            await asyncio.sleep(10)
            await followup.delete()
        logger.error(f"HTTP ошибка в /say для {interaction.user.id}: {e}")
    except Exception as e:
        if not interaction.is_expired():
            followup = await interaction.followup.send(f"Неизвестная ошибка: {e}", ephemeral=True)
            await asyncio.sleep(10)
            await followup.delete()
        logger.error(f"Неизвестная ошибка в /say для {interaction.user.id}: {e}")

async def get_message_id(interaction: discord.Interaction, message: discord.Message, bot_client) -> None:
    """Контекстное меню: Получает ID сообщения."""
    if bot_client is None:
        logger.error("bot_client не предоставлен для get_message_id")
        await interaction.response.send_message("Ошибка конфигурации бота.", ephemeral=True)
        return

    if not await restrict_command_execution(interaction, bot_client):
        return

    access_result, access_reason = await check_bot_access(interaction, bot_client)
    if not access_result:
        await interaction.response.send_message(access_reason or "Бот не имеет доступа.", ephemeral=True)
        return

    try:
        followup = await interaction.response.send_message(f"```{message.id}```", ephemeral=True)
        await asyncio.sleep(10)
        await followup.delete()
    except discord.HTTPException as e:
        logger.error(f"Ошибка в get_message_id для {interaction.user.id}: {e}")
        if not interaction.is_expired():
            followup = await interaction.followup.send("Ошибка получения ID.", ephemeral=True)
            await asyncio.sleep(10)
            await followup.delete()

def create_command(bot_client):
    """Создаёт команду /say и контекстное меню."""
    @app_commands.command(name="say", description=DESCRIPTION)
    @app_commands.describe(
        message="Текст сообщения",
        reply="ID или ссылка на сообщение для ответа",
        attachment="Файл для отправки"
    )
    async def say_command(interaction: discord.Interaction, message: Optional[str] = None, reply: Optional[str] = None, attachment: Optional[discord.Attachment] = None) -> None:
        await say(interaction, message, bot_client, reply, attachment)

    @app_commands.context_menu(name="ID сообщения")
    async def get_message_id_command(interaction: discord.Interaction, message: discord.Message) -> None:
        await get_message_id(interaction, message, bot_client)

    return say_command, get_message_id_command