import discord
from discord import app_commands, File
import io
import requests
import urllib.parse
from ..systemLog import logger
from .restrict import check_bot_access, restrict_command_execution

description = "Сгенерировать аудио-ответ из запроса"

FILE_SIZE_LIMIT = 8 * 1024 * 1024

class VoiceChoices(app_commands.Choice[str]):
    """Выбор голосов для озвучки."""
    VOICES = [
        app_commands.Choice(name="Alloy", value="alloy"),
        app_commands.Choice(name="Echo", value="echo"),
        app_commands.Choice(name="Fable", value="fable"),
        app_commands.Choice(name="Onyx", value="onyx"),
        app_commands.Choice(name="Nova", value="nova"),
        app_commands.Choice(name="Shimmer", value="shimmer"),
    ]

async def audio(interaction: discord.Interaction, request: str, voice: str, bot_client=None) -> None:
    """Команда /audio: Генерирует аудио-ответ из запроса через Pollinations API."""
    if bot_client is None:
        logger.error("bot_client не предоставлен для команды /audio")
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

    await interaction.response.defer()
    try:
        encoded_request = urllib.parse.quote(request)
        url = f"https://text.pollinations.ai/{encoded_request}"
        params = {
            "model": "openai-audio",
            "voice": voice
        }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()

        if 'audio/mpeg' in response.headers.get('Content-Type', ''):
            audio_data = response.content
            audio_size = len(audio_data)

            if audio_size > FILE_SIZE_LIMIT:
                raise ValueError(f"Аудиофайл слишком большой ({audio_size / 1024 / 1024:.2f} МБ). Максимум: 8 МБ.")

            audio_buffer = io.BytesIO(audio_data)
            await interaction.followup.send(
                file=File(audio_buffer, filename="ответ.mp3")
            )
            logger.info(f"Команда /audio выполнена для {interaction.user.id} (сервер: {interaction.guild.id if interaction.guild else 'DM'})")
        else:
            raise ValueError("Получен некорректный тип ответа, ожидался audio/mpeg")
    except Exception as e:
        logger.error(f"Ошибка команды /audio для {interaction.user.id}: {e}")
        await interaction.followup.send(f"Ошибка: {str(e)}", ephemeral=True)

def create_command(bot_client):
    """Создаёт команду /audio."""
    @app_commands.command(name="audio", description=description)
    @app_commands.describe(
        request="Запрос для получения аудио-ответа Ai",
        voice="Голос для озвучки"
    )
    @app_commands.choices(voice=VoiceChoices.VOICES)
    async def wrapper(interaction: discord.Interaction, request: str, voice: str = "alloy") -> None:
        await audio(interaction, request, voice, bot_client)
    wrapper.dm_only = False
    return wrapper