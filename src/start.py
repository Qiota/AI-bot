import asyncio
import importlib
from pathlib import Path
from threading import Thread
import discord
from discord import app_commands
from .config import BotConfig
from .aichat import BotClient
from .logging_config import logger
from .server import run_flask
from .commands.restrict import check_bot_access, check_user_restriction
import time
from typing import Callable, Optional, Any

def create_command_wrapper(
    command: app_commands.Command | app_commands.Group | Callable,
    bot_client: BotClient
) -> app_commands.Command | app_commands.Group:
    """Создаёт обёртку для команды или группы команд."""
    if isinstance(command, app_commands.Group):
        wrapped_group = app_commands.Group(
            name=command.name,
            description=command.description or "Группа команд"
        )
        for subcommand in command.commands:
            wrapped_subcommand = create_command_wrapper(subcommand, bot_client)
            wrapped_group.add_command(wrapped_subcommand)
        return wrapped_group

    # Если команда уже является app_commands.Command (например, через декоратор)
    if isinstance(command, app_commands.Command):
        async def command_wrapper(interaction: discord.Interaction) -> None:
            """Обёртка для выполнения команды с проверками."""
            if command.name == "restrict" and not interaction.guild:
                await interaction.response.send_message("Команда только для серверов!", ephemeral=True)
                return

            if interaction.guild and command.name != "restrict":
                if not await check_bot_access(interaction):
                    return
                if not await check_user_restriction(interaction):
                    return

            try:
                await command.callback(interaction)
            except Exception as e:
                logger.error(f"Ошибка в команде {command.name}: {e}")
                await interaction.response.send_message("Ошибка выполнения команды!", ephemeral=True)

        return app_commands.Command(
            name=command.name,
            description=command.description or "Команда бота",
            callback=command_wrapper
        )

    # Если команда - это callable (функция, ещё не обёрнутая)
    async def command_wrapper(interaction: discord.Interaction) -> None:
        """Обёртка для выполнения команды с проверками."""
        command_name = command.__name__
        if command_name == "restrict" and not interaction.guild:
            await interaction.response.send_message("Команда только для серверов!", ephemeral=True)
            return

        if interaction.guild and command_name != "restrict":
            if not await check_bot_access(interaction):
                return
            if not await check_user_restriction(interaction):
                return

        try:
            await command(interaction, bot_client)
        except Exception as e:
            logger.error(f"Ошибка в команде {command_name}: {e}")
            await interaction.response.send_message("Ошибка выполнения команды!", ephemeral=True)

    return app_commands.Command(
        name=command.__name__,
        description=getattr(command, "description", "Команда бота"),
        callback=command_wrapper
    )

def load_command_module(
    file_path: Path,
    commands_dir: Path,
    bot_client: BotClient
) -> Optional[list[tuple[app_commands.Command | app_commands.Group, str]]]:
    """Загружает модуль команды из файла."""
    try:
        relative_path = file_path.relative_to(commands_dir)
        module_name = f"src.commands.{str(relative_path.with_suffix('')).replace('/', '.').replace('\\', '.')}"
        module = importlib.import_module(module_name)

        create_command: Optional[Callable[[BotClient], Any]] = getattr(module, "create_command", None)
        if not create_command:
            logger.warning(f"create_command не найден в {module_name}")
            return None

        command = create_command(bot_client)
        commands = command if isinstance(command, tuple) else (command,)
        result = []

        for cmd in commands:
            dm_only = getattr(cmd, "dm_only", False)
            guild_only = getattr(cmd, "guild_only", False)
            if dm_only and guild_only:
                logger.warning(f"Команда {getattr(cmd, 'name', cmd.__name__)} не может быть dm_only и guild_only")
                continue

            wrapped_command = create_command_wrapper(cmd, bot_client)
            context = f"[{'ЛС' if dm_only else 'серверов' if guild_only else 'ЛС и серверов'}]"
            settings = {
                "name": wrapped_command.name,
                "type": "group" if isinstance(wrapped_command, app_commands.Group) else "command",
                "dm_only": dm_only,
                "guild_only": guild_only
            }
            if settings["type"] == "group":
                settings["subcommands"] = [sub.name for sub in wrapped_command.commands]

            logger.info(f"Загружается команда/группа: {settings}")
            result.append((wrapped_command, context))

        return result

    except ImportError as e:
        logger.error(f"Ошибка импорта {file_path.stem}: {e}")
        return None

async def register_commands(tree: app_commands.CommandTree, bot_client: BotClient) -> None:
    """Регистрирует команды из папки commands с рекурсивным сканированием."""
    commands_dir = Path(__file__).parent / "commands"

    def scan_commands(directory: Path) -> None:
        """Рекурсивно сканирует папку для поиска команд."""
        for item in directory.iterdir():
            if item.is_dir():
                scan_commands(item)
            elif item.suffix == ".py" and item.stem != "__init__":
                commands = load_command_module(item, commands_dir, bot_client)
                if commands:
                    for command, context in commands:
                        tree.add_command(command)
                        logger.success(f"Команда/группа /{command.name} зарегистрирована для {context}")

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

        @bot_client.bot.event
        async def on_ready():
            await bot_client.bot.wait_until_ready()
            await register_commands(bot_client.tree, bot_client)
            logger.success("Бот запущен!")

        Thread(target=run_flask, daemon=True).start()
        await bot_client.bot.start(config.TOKEN)
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")
        raise

def start_bot() -> None:
    """Инициирует запуск бота."""
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Остановка бота пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        exit(1)