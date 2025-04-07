import asyncio
from threading import Thread
from discord import app_commands
import importlib
from pathlib import Path
from .config import BotConfig, logger
from .aichat import BotClient
from .server import run_flask
from .sharding import BotActivity

def register_commands(tree: app_commands.CommandTree, bot_client: BotClient) -> None:
    commands_dir = Path(__file__).parent / "commands"
    
    for file in commands_dir.glob("*.py"):
        if file.stem == "__init__":
            continue
        
        try:
            module_name = f".commands.{file.stem}"
            module = importlib.import_module(module_name, package=__package__)
            
            create_command = getattr(module, "create_command", None)
            if not create_command:
                logger.warning(f"Функция create_command не найдена в модуле {module_name}")
                continue

            command = create_command(bot_client)
            tree.add_command(command)
            logger.info(f"Команда /{file.stem} успешно зарегистрирована")

        except Exception as e:
            logger.error(f"Ошибка регистрации команды {file.stem}: {e}")
            continue

def setup_sync(bot_client: BotClient, tree: app_commands.CommandTree) -> None:
    @bot_client.bot.event
    async def on_ready() -> None:
        try:
            await bot_client.bot.wait_until_ready()
            if not tree.get_commands():
                logger.warning("Нет команд для синхронизации")
                return

            synced = await tree.sync(guild=None)
            if synced is not None:
                logger.info(f"Глобально синхронизировано {len(synced)} команд")
            else:
                logger.warning("Синхронизация команд не вернула результат (возможно, недостаточно прав)")

            await BotActivity.set_shard_activity(bot_client.bot)
            
            if hasattr(bot_client, 'on_ready'):
                await bot_client.on_ready()
            
            logger.info("Бот успешно запущен!")
        except Exception as e:
            logger.error(f"Ошибка глобальной синхронизации: {e}")
            raise

async def run_bot():
    config = BotConfig()
    bot_client = BotClient(config)
    
    try:
        config.validate()
        bot_client.bot.event(bot_client.on_message)
        bot_client.bot.event(bot_client.on_message_edit)
        
        register_commands(bot_client.tree, bot_client)
        setup_sync(bot_client, bot_client.tree)

        Thread(target=run_flask, daemon=True).start()
        await bot_client.bot.start(config.TOKEN)

    except Exception as e:
        logger.error(f"Ошибка запуска: {e}")
        raise
    finally:
        if not bot_client.bot.is_closed():
            await bot_client.bot.close()
        logger.info("Бот закрыт")

def start_bot():
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Остановка бота пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        exit(1)