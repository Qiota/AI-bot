import asyncio
from threading import Thread
import discord
from discord import app_commands
import os
import importlib
from pathlib import Path
from .config import BotConfig, logger
from .client import BotClient
from .server import run_flask
from .sharding import BotActivity  # Импортируем BotActivity

def register_commands(tree: app_commands.CommandTree, bot_client: BotClient) -> None:
    """Динамически регистрирует все команды из папки commands."""
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
    """Настраивает глобальную синхронизацию команд и активность бота."""
    @bot_client.bot.event
    async def on_ready() -> None:  # type: ignore
        try:
            await bot_client.bot.wait_until_ready()
            synced = await tree.sync(guild=None)
            logger.info(f"Глобально синхронизировано {len(synced)} команд")
            
            # Устанавливаем активность бота с учётом шардирования
            await BotActivity.set_shard_activity(bot_client.bot)
            
            logger.info("Бот успешно запущен!")
        except Exception as e:
            logger.error(f"Ошибка глобальной синхронизации: {e}")

async def run_bot():
    """Запускает бота и Flask-сервер."""
    config = BotConfig()
    bot_client = BotClient(config, shard_count=config.get("SHARD_COUNT", 2))
    
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
        await bot_client.bot.close()
        logger.info("Бот закрыт")

def start_bot():
    """Запускает бота в асинхронном режиме."""
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Остановка бота пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        exit(1)