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
    
    def load_commands(directory: Path) -> None:
        for item in directory.iterdir():
            if item.is_dir():
                load_commands(item)
            elif item.suffix == '.py' and item.stem != "__init__":
                try:
                    relative_path = item.relative_to(commands_dir)
                    module_name = f"src.commands.{str(relative_path.with_suffix('')).replace('/', '.').replace('\\', '.')}"
                    module = importlib.import_module(module_name)
                    module = importlib.reload(module)
                    
                    create_command = getattr(module, "create_command", None)
                    if not create_command:
                        logger.warning(f"create_command не найден в {module_name}")
                        continue

                    command = create_command(bot_client)
                    tree.add_command(command)
                    logger.info(f"Команда /{item.stem} зарегистрирована")

                except ImportError as e:
                    logger.error(f"Ошибка импорта {item.stem}: {e}")
                except Exception as e:
                    logger.error(f"Ошибка регистрации {item.stem}: {e}")

    tree.clear_commands(guild=None)
    load_commands(commands_dir)

async def run_bot():
    config = BotConfig()
    bot_client = BotClient(config)
    
    try:
        config.validate()
        bot_client.bot.event(bot_client.on_message)
        bot_client.bot.event(bot_client.on_message_edit)
        
        @bot_client.bot.event
        async def on_ready():
            await bot_client.bot.wait_until_ready()
            register_commands(bot_client.tree, bot_client)
            synced = await bot_client.tree.sync(guild=None)
            logger.info(f"Синхронизировано {len(synced)} команд при запуске")
            
            await BotActivity.set_shard_activity(bot_client.bot, bot_client.bot.deploy_time)
            if hasattr(bot_client, 'on_ready'):
                await bot_client.on_ready()
            logger.info("Бот запущен!")

        Thread(target=run_flask, daemon=True).start()
        await bot_client.bot.start(config.TOKEN)

    except Exception as e:
        logger.error(f"Ошибка запуска: {e}")
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