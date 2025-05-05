import discord
from discord import app_commands
from gtts import gTTS, gTTSError
import os
import tempfile
from ..systemLog import logger
from .restrict import check_bot_access, restrict_command_execution

description = "Озвучивает указанный текст в виде аудиофайла"

async def voice(interaction: discord.Interaction, bot_client, text: str):
    """Команда /voice: Озвучивает указанный текст и отправляет аудиофайл."""
    if bot_client is None:
        logger.error("bot_client не предоставлен для команды /voice")
        await interaction.response.send_message("Ошибка конфигурации бота.", ephemeral=True)
        return

    # Проверка выполнения команды
    if not await restrict_command_execution(interaction, bot_client):
        return

    # Проверка доступа к каналу
    access_result, access_reason = await check_bot_access(interaction, bot_client)
    if not access_result:
        await interaction.response.send_message(
            access_reason or "Бот не имеет доступа к этому каналу.",
            ephemeral=True
        )
        return

    # Проверка длины текста (максимум 2000 символов)
    if len(text) > 2000:
        await interaction.response.send_message(
            "Текст слишком длинный. Максимальная длина - 2000 символов.",
            ephemeral=True
        )
        return

    await interaction.response.defer()  # Откладываем ответ

    tmp_file_path = None
    try:
        # Генерация аудиофайла с помощью gTTS
        tts = gTTS(text=text, lang='ru')
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp_file:
            tts.save(tmp_file.name)
            tmp_file_path = tmp_file.name

        # Отправка аудиофайла как вложения
        with open(tmp_file_path, 'rb') as fp:
            audio_file = discord.File(fp, filename='voice.mp3')
            await interaction.followup.send(file=audio_file)

    except gTTSError as e:
        logger.error(f"Ошибка gTTS для пользователя {interaction.user.id}: {e}")
        await interaction.followup.send(f"Ошибка при генерации аудио: {e}")
    except Exception as e:
        logger.error(f"Неизвестная ошибка команды /voice для пользователя {interaction.user.id}: {e}")
        await interaction.followup.send(f"Неизвестная ошибка при генерации аудио: {e}")
    finally:
        # Гарантированное удаление временного файла
        if tmp_file_path and os.path.exists(tmp_file_path):
            os.remove(tmp_file_path)

def create_command(bot_client):
    """Создаёт команду /voice с кулдауном."""
    @app_commands.command(name="voice", description=description)
    @app_commands.describe(text="Текст для озвучивания")
    @app_commands.checks.cooldown(1, 5)
    async def wrapper(interaction: discord.Interaction, text: str) -> None:
        await voice(interaction, bot_client, text)
    return wrapper