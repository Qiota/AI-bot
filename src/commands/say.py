"""Команда /say — отправка сообщений от имени бота."""

from typing import List, Optional, Tuple
import discord
from discord import app_commands
import aiohttp
import io
import re
import asyncio

from ..systemLog import logger
from ..core.middleware import require_bot_access, require_permissions
from ..core.constants import ERROR_GENERIC

DESCRIPTION = "Говорить от имени бота, с файлами или ответом на сообщение"


async def _get_message_reference(
    interaction: discord.Interaction, reply: Optional[str]
) -> Tuple[Optional[discord.MessageReference], Optional[discord.Message]]:
    """Получает ссылку на сообщение для ответа (только в том же канале)."""
    if not reply:
        return None, None

    url_pattern = r"https?://discord\.com/channels/(?:@me|\d+)/(\d+)/(\d+)"
    match = re.match(url_pattern, reply.strip())

    if match:
        channel_id, message_id = map(int, match.groups())
        try:
            channel = interaction.client.get_channel(channel_id) or await interaction.client.fetch_channel(channel_id)
            if channel.id != interaction.channel.id:
                raise ValueError(
                    "Сообщение для реплая должно находиться в том же канале."
                )
            if (
                not isinstance(channel, (discord.TextChannel, discord.Thread))
                or not channel.permissions_for(interaction.guild.me).read_messages
            ):
                raise ValueError("Бот не имеет доступа к указанному каналу.")
            target = await channel.fetch_message(message_id)
            return target.to_reference(fail_if_not_exists=False), target
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"Ошибка доступа к сообщению {message_id}: {e}")
            raise ValueError("Бот не может получить доступ к сообщению или каналу.")
        except discord.NotFound:
            raise ValueError("Указанное сообщение не найдено в этом канале.")

    try:
        message_id = int(reply)
        target = await interaction.channel.fetch_message(message_id)
        return target.to_reference(fail_if_not_exists=False), target
    except ValueError:
        raise ValueError("reply должен быть числовым ID или ссылкой на сообщение в этом канале.")
    except discord.NotFound:
        raise ValueError(f"Сообщение с ID {reply} не найдено в этом канале.")


async def _download_attachment(
    session: aiohttp.ClientSession, attachment: discord.Attachment, max_size: int
) -> Optional[discord.File]:
    """Загружает одно вложение."""
    try:
        if attachment.size > max_size:
            logger.warning(f"Файл {attachment.filename} слишком большой")
            return None
        async with session.get(attachment.url) as resp:
            if resp.status != 200:
                logger.warning(f"Ошибка загрузки {attachment.filename}: HTTP {resp.status}")
                return None
            return discord.File(fp=io.BytesIO(await resp.read()), filename=attachment.filename)
    except Exception as e:
        logger.error(f"Ошибка загрузки {attachment.filename}: {e}")
        return None


async def _process_attachments(attachments: List[discord.Attachment]) -> List[discord.File]:
    """Параллельная загрузка вложений."""
    max_size = 25 * 1024 * 1024
    max_files = 10
    async with aiohttp.ClientSession() as session:
        tasks = [
            _download_attachment(session, att, max_size)
            for att in attachments[:max_files]
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        files = [f for f in results if f]
    if not files and attachments:
        raise ValueError("Не удалось загрузить ни один файл.")
    return files


async def _delete_after(msg: discord.Message, delay: int = 10) -> None:
    """Deletes a message after a delay, silently ignoring errors."""
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except discord.DiscordException:
        pass


async def _send_error(interaction: discord.Interaction, text: str) -> None:
    """Sends an ephemeral error message that auto-deletes."""
    if interaction.is_expired():
        return
    try:
        msg = await interaction.followup.send(text, ephemeral=True)
        asyncio.create_task(_delete_after(msg))
    except discord.DiscordException:
        pass


@require_permissions(administrator=True, manage_channels=True, manage_messages=True)
@require_bot_access
async def say(
    interaction: discord.Interaction,
    bot_client,
    message: Optional[str] = None,
    reply: Optional[str] = None,
    attachment: Optional[discord.Attachment] = None,
) -> None:
    """Команда /say: Отправляет сообщение от имени бота."""
    await interaction.response.defer(ephemeral=True)

    try:
        attachments = [attachment] if attachment else []
        additional = interaction.data.get("resolved", {}).get("attachments", {}).values()
        for att_data in additional:
            if not attachment or str(att_data.get("id")) != str(attachment.id):
                attachments.append(discord.Attachment(data=att_data, state=interaction._state))

        if not message and not attachments and not reply:
            raise ValueError("Сообщение не может быть пустым.")

        reference, target_message = await _get_message_reference(interaction, reply)
        discord_files = await _process_attachments(attachments) if attachments else None

        try:
            await interaction.channel.send(
                content=message[:2000] if message else None,
                files=discord_files or None,
                reference=reference,
            )
            await interaction.delete_original_response()
        except discord.HTTPException as e:
            if e.code == 50035 and reference and target_message:
                await target_message.reply(
                    content=message[:2000] if message else None,
                    files=discord_files or None,
                )
                await interaction.delete_original_response()
            else:
                raise

    except ValueError as e:
        await _send_error(interaction, str(e))
        logger.error(f"ValueError in /say for {interaction.user.id}: {e}")
    except discord.HTTPException as e:
        error_map = {
            400: "Некорректный запрос к Discord API.",
            50035: "Сообщение слишком длинное (макс. 2000 символов).",
            50006: "Нельзя отправить пустое сообщение.",
            50013: "У бота нет прав для отправки.",
            10062: "Взаимодействие устарело.",
        }
        await _send_error(interaction, error_map.get(e.code, f"Ошибка отправки: {e}"))
        logger.error(f"HTTP error in /say for {interaction.user.id}: {e}")
    except Exception as e:
        await _send_error(interaction, f"Неизвестная ошибка: {e}")
        logger.error(f"Unknown error in /say for {interaction.user.id}: {e}")


@require_bot_access
async def get_message_id(
    interaction: discord.Interaction, message: discord.Message, bot_client
) -> None:
    """Контекстное меню: получает ID сообщения."""
    try:
        await interaction.response.send_message(f"```{message.id}```", ephemeral=True)
    except discord.HTTPException as e:
        logger.error(f"Error in get_message_id for {interaction.user.id}: {e}")
        await _send_error(interaction, "Ошибка получения ID.")


def create_command(bot_client):
    """Создаёт команду /say и контекстное меню."""

    @app_commands.command(name="say", description=DESCRIPTION)
    @app_commands.describe(
        message="Текст сообщения",
        reply="ID или ссылка на сообщение для ответа",
        attachment="Файл для отправки",
    )
    async def say_command(
        interaction: discord.Interaction,
        message: Optional[str] = None,
        reply: Optional[str] = None,
        attachment: Optional[discord.Attachment] = None,
    ) -> None:
        await say(interaction, bot_client, message, reply, attachment)

    @app_commands.context_menu(name="ID сообщения")
    async def get_message_id_command(
        interaction: discord.Interaction, message: discord.Message
    ) -> None:
        await get_message_id(interaction, message, bot_client)

    return say_command, get_message_id_command

