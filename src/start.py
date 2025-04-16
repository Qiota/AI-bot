import asyncio
import importlib
from pathlib import Path
from threading import Thread
import discord
from .config import BotConfig
from .aichat import BotClient
from .logging_config import logger
from .server import run_flask
from .commands.giveaway import resume_giveaways
from .ready import set_activity
from .commands.restrict import check_bot_access, check_user_restriction, handle_mention
import time

def create_command_wrapper(command, bot_client):
    """Создаёт обёртку для команды с разделением логики для ЛС и серверов."""
    async def wrapper(interaction: discord.Interaction):
        if command.name == "restrict" and not interaction.guild:
            await interaction.response.send_message(
                "Эта команда доступна только на серверах!", ephemeral=True
            )
            return

        if interaction.guild:
            if command.name != "restrict":
                if not await check_bot_access(interaction):
                    return
                if not await check_user_restriction(interaction):
                    return
        
        await command.callback(interaction)

    return discord.app_commands.Command(
        name=command.name,
        description=command.description,
        callback=wrapper
    )

def load_command_module(file_path: Path, commands_dir: Path, bot_client: BotClient):
    """Загружает модуль команды и регистрирует её с учётом контекста."""
    try:
        relative_path = file_path.relative_to(commands_dir)
        module_name = f"src.commands.{str(relative_path.with_suffix('')).replace('/', '.').replace('\\', '.')}"
        module = importlib.import_module(module_name)
        
        create_command = getattr(module, "create_command", None)
        if not create_command:
            logger.warning(f"create_command не найден в {module_name}")
            return None, None
        
        command = create_command(bot_client)
        if isinstance(command, tuple):
            commands = command
        else:
            commands = (command,)

        result = []
        for cmd in commands:
            dm_only = getattr(cmd, "dm_only", False)
            guild_only = getattr(cmd, "guild_only", False)

            if dm_only and guild_only:
                logger.warning(f"Команда {cmd.name} не может быть одновременно dm_only и guild_only")
                continue
            
            wrapped_command = create_command_wrapper(cmd, bot_client)
            
            if dm_only:
                context = "[ЛС]"
            elif guild_only:
                context = "[серверов]"
            else:
                context = "[ЛС и серверов]"
            
            result.append((wrapped_command, context))
        return result
    except ImportError as e:
        logger.error(f"Ошибка импорта {file_path.stem}: {e}")
        return None, None

async def register_commands(tree: discord.app_commands.CommandTree, bot_client: BotClient) -> None:
    """Регистрирует команды из папки commands с учётом контекста."""
    commands_dir = Path(__file__).parent / "commands"
    
    def scan_commands(directory: Path) -> None:
        for item in directory.iterdir():
            if item.is_dir():
                scan_commands(item)
            elif item.suffix == '.py' and item.stem != "__init__":
                commands = load_command_module(item, commands_dir, bot_client)
                if commands:
                    for command, context in commands:
                        tree.add_command(command)
                        logger.success(f"Команда /{command.name} зарегистрирована для {context}")

    try:
        tree.clear_commands(guild=None)
        scan_commands(commands_dir)
        synced = await tree.sync(guild=None)
        logger.success(f"Синхронизировано {len(synced)} команд")
    except Exception as e:
        logger.error(f"Ошибка при регистрации команд: {e}")

async def run_bot():
    """Запускает бота."""
    config = BotConfig()
    bot_client = BotClient(config)
    bot_client.start_time = time.time()
    
    try:
        config.validate()
        bot_client.bot.event(bot_client.on_message)
        bot_client.bot.event(bot_client.on_message_edit)

        @bot_client.bot.event
        async def on_message(message: discord.Message):
            if message.author == bot_client.bot.user:
                return
            can_respond = await handle_mention(message, bot_client)
            if not can_respond:
                return
            await bot_client.on_message(message)

        @bot_client.bot.event
        async def on_ready():
            await bot_client.bot.wait_until_ready()
            await register_commands(bot_client.tree, bot_client)
            await resume_giveaways(bot_client)
            await set_activity(bot_client.bot)
            logger.success("Бот запущен!")

        Thread(target=run_flask, daemon=True).start()
        await bot_client.bot.start(config.TOKEN)
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")
        raise

def start_bot():
    """Инициирует запуск бота."""
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Остановка бота пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        exit(1)