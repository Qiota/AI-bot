import asyncio
import importlib
from pathlib import Path
from threading import Thread
import discord
from discord import app_commands
from .config import BotConfig
from .aichat import BotClient
from .systemLog import logger
from .server import run_flask
from .commands.restrict import check_bot_access, check_user_restriction, restrict_command_execution
from .events.activity import set_bot_activity
import time
from typing import Optional, Union, Tuple

async def precheck_command_execution(interaction: discord.Interaction, command_name: str, bot_client: BotClient) -> Tuple[bool, str]:
    """
    Проверяет, можно ли выполнить команду, без выброса исключений.
    Возвращает (можно ли выполнить, причина отказа).
    """
    logger.debug(f"Предпроверка команды {command_name} для пользователя {interaction.user.id} на сервере {interaction.guild.id if interaction.guild else 'DM'}")

    # Проверка готовности бота
    if not bot_client.bot.is_ready():
        logger.debug("Бот не готов")
        return False, "Бот еще не готов. Пожалуйста, попробуйте позже."

    # Проверка доступа бота на сервере
    logger.debug("Проверка restrict_command_execution")
    if not await restrict_command_execution(interaction, bot_client):
        logger.debug("restrict_command_execution вернул False")
        return False, "Конфигурация сервера не найдена или бот отсутствует на сервере."

    # Проверка, если команда /restrict вызвана в ЛС
    if command_name == "restrict" and not interaction.guild:
        logger.debug("Команда restrict в ЛС")
        return False, "Команда только для серверов!"

    # Проверки доступа и ограничений для команд на сервере (кроме /restrict)
    if interaction.guild and command_name != "restrict":
        logger.debug("Проверка check_bot_access")
        access, access_reason = await check_bot_access(interaction)
        if not access:
            logger.debug(f"check_bot_access вернул False: {access_reason}")
            return False, f"Бот не имеет доступа на этом сервере! Причина: {access_reason}"

        logger.debug("Проверка check_user_restriction")
        restriction, restriction_reason = await check_user_restriction(interaction)
        if not restriction:
            logger.debug(f"check_user_restriction вернул False: {restriction_reason}")
            return False, f"У вас нет доступа к этой команде! Причина: {restriction_reason}"

    logger.debug(f"Все предпроверки пройдены для команды {command_name}")
    return True, "Все проверки пройдены."

async def should_execute_command(interaction: discord.Interaction, command_name: str, bot_client: BotClient) -> bool:
    """Определяет, можно ли выполнить команду, используя предпроверку."""
    can_execute, reason = await precheck_command_execution(interaction, command_name, bot_client)
    if not can_execute:
        logger.debug(f"Команда {command_name} не выполняется: {reason}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(reason, ephemeral=True)
            elif not interaction.followup.is_done():
                await interaction.followup.send(reason, ephemeral=True)
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения: {e}")
        return False
    return True

async def apply_command_checks(interaction: discord.Interaction, command_name: str, bot_client: BotClient) -> bool:
    """Проверяет доступ к команде (устаревшая, оставлена для совместимости)."""
    return await should_execute_command(interaction, command_name, bot_client)

def add_checks_to_command(command: Union[app_commands.Command, app_commands.Group], bot_client: BotClient) -> None:
    """Добавляет проверки к команде или группе."""
    if isinstance(command, app_commands.Group):
        for subcommand in command.commands:
            add_checks_to_command(subcommand, bot_client)
    else:
        async def check(interaction: discord.Interaction) -> bool:
            return await should_execute_command(interaction, command.name, bot_client)
        command.add_check(check)

def load_command_module(file_path: Path, commands_dir: Path, bot_client: BotClient) -> Optional[list[tuple[Union[app_commands.Command, app_commands.Group], str]]]:
    """Загружает модуль команды."""
    try:
        relative_path = file_path.relative_to(commands_dir)
        module_name = f"src.commands.{str(relative_path.with_suffix('')).replace('/', '.').replace('\\', '.')}"
        module = importlib.import_module(module_name)
        create_command = getattr(module, "create_command", None)
        if not create_command:
            logger.warning(f"create_command не найден в {module_name}")
            return None

        command = create_command(bot_client)
        commands = command if isinstance(command, tuple) else (command,)
        result = []
        loaded_commands = []

        for cmd in commands:
            dm_only = getattr(cmd, "dm_only", False)
            guild_only = getattr(cmd, "guild_only", False)
            if dm_only and guild_only:
                logger.warning(f"Команда {cmd.name} не может быть dm_only и guild_only")
                continue

            add_checks_to_command(cmd, bot_client)
            context = f"[{'ЛС' if dm_only else 'серверов' if guild_only else 'ЛС и серверов'}]"
            settings = {"name": cmd.name, "type": "group" if isinstance(cmd, app_commands.Group) else "command", "dm_only": dm_only, "guild_only": guild_only}
            if settings["type"] == "group":
                settings["subcommands"] = [sub.name for sub in cmd.commands]
            loaded_commands.append(f"/{cmd.name} ({settings['type']}{', подкоманды: ' + ', '.join(settings['subcommands']) if settings['type'] == 'group' else ''}) для {context}")
            result.append((cmd, context))

        if loaded_commands:
            logger.info(f"Загружены команды: {', '.join(loaded_commands)}")
        return result
    except ImportError as e:
        logger.error(f"Ошибка импорта {file_path.stem}: {e}")
        return None

async def register_commands(tree: app_commands.CommandTree, bot_client: BotClient) -> None:
    """Регистрирует команды из папки commands."""
    commands_dir = Path(__file__).parent / "commands"
    def scan_commands(directory: Path) -> None:
        """Рекурсивно сканирует команды."""
        for item in directory.iterdir():
            if item.is_dir():
                scan_commands(item)
            elif item.suffix == ".py" and item.stem != "__init__":
                commands = load_command_module(item, commands_dir, bot_client)
                if commands:
                    for command, _ in commands:
                        tree.add_command(command)

    try:
        tree.clear_commands(guild=None)
        scan_commands(commands_dir)
        synced = await tree.sync(guild=None)
        logger.success(f"Синхронизировано {len(synced)} команд")
    except Exception as e:
        logger.error(f"Ошибка регистрации команд: {e}")
        raise

async def run_bot() -> None:
    """Запускает бота."""
    config = BotConfig()
    bot_client = BotClient(config)
    bot_client.start_time = time.time()

    try:
        config.validate()
        bot_client.bot.event(bot_client.on_message)
        bot_client.bot.event(bot_client.on_message_edit)

        # Custom error handler for command tree
        @bot_client.tree.error
        async def on_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
            if isinstance(error, app_commands.CheckFailure):
                # Skip if interaction was already handled by should_execute_command
                if interaction.response.is_done():
                    return
                # Use precheck_command_execution to get the reason
                can_execute, reason = await precheck_command_execution(interaction, interaction.command.name, bot_client)
                if not can_execute:
                    try:
                        await interaction.response.send_message(
                            reason if "Причина:" in reason else f"{interaction.user.mention}, бот не работает в этом канале. Используйте /restrict.",
                            ephemeral=True
                        )
                    except Exception as e:
                        logger.error(f"Ошибка при отправке сообщения об ошибке: {e}")
            else:
                logger.error(f"Ошибка команды {interaction.command.name}: {error}")
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message("Произошла ошибка при выполнении команды.", ephemeral=True)
                except Exception as e:
                    logger.error(f"Ошибка при отправке сообщения об ошибке: {e}")

        @bot_client.bot.event
        async def on_ready():
            await bot_client.bot.wait_until_ready()
            await set_bot_activity(bot_client.bot)
            await register_commands(bot_client.tree, bot_client)
            logger.success("Бот запущен!")

        Thread(target=run_flask, daemon=True).start()
        await bot_client.bot.start(config.TOKEN)
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")
        raise
    finally:
        if hasattr(bot_client, 'client') and hasattr(bot_client.client, '_session'):
            await bot_client.client._session.close()
        await bot_client.bot.close()

def start_bot() -> None:
    """Инициирует запуск бота."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(run_bot())
        else:
            loop.run_until_complete(run_bot())
    except KeyboardInterrupt:
        logger.info("Остановка бота пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        exit(1)