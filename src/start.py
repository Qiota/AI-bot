import asyncio
import importlib
from pathlib import Path
from threading import Thread
from typing import Optional, Union, Tuple, List
import discord
from discord import app_commands
from aiohttp import ClientSession
from .config import BotConfig
from .client import BotClient
from .aichat import AIChat
from .systemLog import logger
from .utils.server.flask import run_flask
from .commands.restrict import check_bot_access, restrict_command_execution
from .events.activity import set_bot_activity
from .utils.checker import checker
import time
import traceback

async def precheck_command_execution(interaction: discord.Interaction, command_name: str, bot_client: BotClient) -> Tuple[bool, str]:
    """Предварительная проверка выполнения команды."""
    guild_id = str(interaction.guild.id) if interaction.guild else "DM"
    channel_id = str(interaction.channel.id) if interaction.channel else "DM"
    
    # Проверка готовности бота
    if not bot_client.bot or not bot_client.bot.is_ready():
        logger.error(f"Бот не готов для команды {command_name} в гильдии {guild_id}, канал {channel_id}: bot_client.bot отсутствует или не инициализирован")
        return False, "Бот ещё не готов."
    
    # Проверка конфигурации сервера
    if not await restrict_command_execution(interaction, bot_client):
        logger.error(f"Конфигурация сервера не найдена для команды {command_name} в гильдии {guild_id}, канал {channel_id}")
        return False, "Конфигурация сервера не найдена! Настройте через /restrict."
    
    # Проверка команды /restrict в DM
    if command_name == "restrict" and not interaction.guild:
        logger.error(f"Команда /restrict вызвана в DM (канал {channel_id})")
        return False, "Команда только для серверов!"
    
    # Проверка NSFW-канала для команды /aidhentai
    if command_name == "aidhentai" and interaction.guild and not interaction.channel.nsfw:
        logger.error(f"Команда /aidhentai вызвана в не-NSFW канале {channel_id} для гильдии {guild_id}")
        return False, "Эта команда доступна только в NSFW-каналах или ЛС."
    
    # Проверки для гильдий (кроме команды /restrict)
    if interaction.guild and command_name != "restrict":
        # Проверка доступа к каналу
        access_result, access_reason = await check_bot_access(interaction, bot_client)
        if not access_result:
            logger.error(f"Бот не имеет доступа к каналу {channel_id} для команды {command_name} в гильдии {guild_id}: {access_reason}")
            return False, access_reason or f"Команда заблокирована: бот не имеет доступа к каналу {channel_id}! Добавьте канал через /restrict или проверьте права бота."
        
        # Проверка ограничений пользователя
        restriction, restriction_reason = await checker.check_user_restriction(interaction)
        if not restriction:
            logger.error(f"Пользователь {interaction.user.id} ограничен для команды {command_name} в гильдии {guild_id}, канал {channel_id}: {restriction_reason}")
            return False, restriction_reason or "Ваш доступ к боту ограничен."
    
    logger.debug(f"Все проверки пройдены для команды {command_name} в гильдии {guild_id}, канал {channel_id}")
    return True, "Проверки пройдены."

async def should_execute_command(interaction: discord.Interaction, command_name: str, bot_client: BotClient) -> bool:
    """Проверка выполнения команды с отправкой сообщений об ошибках."""
    can_execute, reason = await precheck_command_execution(interaction, command_name, bot_client)
    if not can_execute and reason:
        guild_id = interaction.guild_id or "DM"
        channel_id = interaction.channel_id or "DM"
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(reason, ephemeral=True)
            else:
                await interaction.followup.send(reason, ephemeral=True)
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения для команды {command_name} в гильдии {guild_id}, канал {channel_id}: {e}\n{traceback.format_exc()}")
        return False
    return True

async def load_command_module(file_path: Path, commands_dir: Path, bot_client: BotClient) -> Optional[List[Tuple[Union[app_commands.Command, app_commands.Group], str]]]:
    """Загрузка модуля команд."""
    if not bot_client:
        logger.error(f"BotClient не инициализирован для загрузки модуля {file_path.stem}")
        return None

    try:
        relative_path = file_path.relative_to(commands_dir)
        module_name = f"src.commands.{str(relative_path.with_suffix('')).replace('/', '.').replace('\\', '.')}"
        module = importlib.import_module(module_name)
        create_command = getattr(module, "create_command", None)
        if not create_command:
            logger.warning(f"create_command не найден в {module_name}")
            return None

        # Обработка модулей
        cog = bot_client
        if module_name == "src.commands.google":
            try:
                cog = module.GoogleSearch(bot_client)  # Убрана передача session
            except TypeError as e:
                logger.error(f"Ошибка создания GoogleSearch для {module_name}: {e}\n{traceback.format_exc()}")
                return None

        logger.debug(f"Загрузка команды из модуля {module_name} с cog={cog}")
        command = await create_command(cog) if asyncio.iscoroutinefunction(create_command) else create_command(cog)
        commands = command if isinstance(command, tuple) else (command,)
        result = []
        loaded_commands = []

        for cmd in commands:
            dm_only = getattr(cmd, "dm_only", False)
            guild_only = getattr(cmd, "guild_only", False)
            if dm_only and guild_only:
                logger.warning(f"Команда {cmd.name} не может быть одновременно dm_only и guild_only")
                continue

            context = f"[{'ЛС' if dm_only else 'серверов' if guild_only else 'ЛС и серверов'}]"
            settings = {
                "name": cmd.name,
                "type": "group" if isinstance(command, app_commands.Group) else "command",
                "dm_only": dm_only,
                "guild_only": guild_only
            }
            if settings["type"] == "group":
                settings["subcommands"] = [sub.name for sub in cmd.commands]
            loaded_commands.append(
                f"/{cmd.name} ({settings['type']}"
                + (f", подкоманды: {', '.join(settings['subcommands'])}" if settings["type"] == "group" else "")
                + f") для {context}"
            )
            result.append((cmd, context))

        if loaded_commands:
            logger.info(f"Загружены команды: {', '.join(loaded_commands)}")
        return result
    except ImportError as e:
        logger.error(f"Ошибка импорта модуля {file_path.stem}: {e}\n{traceback.format_exc()}")
        return None
    except Exception as e:
        logger.error(f"Ошибка загрузки модуля {file_path}: {e}\n{traceback.format_exc()}")
        return None

async def register_commands(tree: app_commands.CommandTree, bot_client: BotClient) -> None:
    """Регистрация команд в CommandTree."""
    commands_dir = Path(__file__).parent / "commands"
    async def scan_commands(directory: Path) -> None:
        for item in directory.iterdir():
            if item.is_dir():
                await scan_commands(item)
            elif item.suffix == ".py" and item.stem != "__init__":
                commands = await load_command_module(item, commands_dir, bot_client)
                if commands:
                    for command, _ in commands:
                        tree.add_command(command)

    try:
        tree.clear_commands(guild=None)
        await scan_commands(commands_dir)
        synced = await tree.sync(guild=None)
        logger.success(f"Синхронизировано {len(synced)} глобальных команд")
    except Exception as e:
        logger.error(f"Ошибка регистрации команд: {e}\n{traceback.format_exc()}")
        raise

async def run_bot() -> None:
    """Запуск Discord-бота."""
    config = BotConfig()
    bot_client = BotClient(config)
    bot_client.start_time = time.time()
    ai_chat = AIChat(bot_client)
    session = None

    try:
        config.validate()
        
        @bot_client.tree.error
        async def on_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
            """Обработка ошибок команд."""
            command_name = interaction.command.name if interaction.command else "неизвестная команда"
            guild_id = interaction.guild_id or "DM"
            channel_id = interaction.channel_id or "DM"
            user_id = interaction.user.id

            try:
                if isinstance(error, app_commands.CheckFailure):
                    # Проверяем причину ошибки
                    can_execute, reason = await precheck_command_execution(interaction, command_name, bot_client)
                    if not can_execute and reason:
                        logger.error(f"CheckFailure для команды {command_name} в гильдии {guild_id}, канал {channel_id}: {reason}")
                        try:
                            if not interaction.response.is_done():
                                await interaction.response.send_message(reason, ephemeral=True)
                            else:
                                await interaction.followup.send(reason, ephemeral=True)
                        except Exception as e:
                            logger.error(f"Ошибка отправки сообщения об ошибке для команды {command_name} в гильдии {guild_id}, канал {channel_id}: {e}\n{traceback.format_exc()}")
                        return

                    # Проверка ограничений пользователя
                    if interaction.guild:
                        restriction, restriction_reason = await checker.check_user_restriction(interaction)
                        if not restriction:
                            logger.error(f"CheckFailure для команды {command_name} в гильдии {guild_id}, канал {channel_id}: Пользователь {user_id} ограничен - {restriction_reason}")
                            try:
                                if not interaction.response.is_done():
                                    await interaction.response.send_message(
                                        restriction_reason or "Ваш доступ к боту ограничен.",
                                        ephemeral=True
                                    )
                                else:
                                    await interaction.followup.send(
                                        restriction_reason or "Ваш доступ к боту ограничен.",
                                        ephemeral=True
                                    )
                            except Exception as e:
                                logger.error(f"Ошибка отправки сообщения об ошибке команды {command_name} в гильдии {guild_id}, канал {channel_id}: {e}\n{traceback.format_exc()}")
                            return
                    
                    # Неизвестная причина CheckFailure
                    logger.error(f"CheckFailure для команды {command_name} в гильдии {guild_id}, канал {channel_id}: Неизвестная причина\n{traceback.format_exc()}")
                    try:
                        if not interaction.response.is_done():
                            await interaction.response.send_message(
                                "Ошибка выполнения команды. Пожалуйста, попробуйте снова.",
                                ephemeral=True
                            )
                        else:
                            await interaction.followup.send(
                                "Ошибка выполнения команды. Пожалуйста, попробуйте снова.",
                                ephemeral=True
                            )
                    except Exception as e:
                        logger.error(f"Ошибка отправки сообщения об ошибке команды {command_name} в гильдии {guild_id}, канал {channel_id}: {e}\n{traceback.format_exc()}")
                else:
                    # Другие ошибки
                    logger.error(f"Ошибка команды {command_name} для пользователя {user_id} в гильдии {guild_id}, канал {channel_id}: {error}\n{traceback.format_exc()}")
                    try:
                        if not interaction.response.is_done():
                            await interaction.response.send_message("Произошла ошибка при выполнении команды.", ephemeral=True)
                        else:
                            await interaction.followup.send("Произошла ошибка при выполнении команды.", ephemeral=True)
                    except Exception as e:
                        logger.error(f"Ошибка отправки сообщения об ошибке команды {command_name} в гильдии {guild_id}, канал {channel_id}: {e}\n{traceback.format_exc()}")
            except Exception as e:
                logger.error(f"Критическая ошибка обработки ошибки команды {command_name} в гильдии {guild_id}, канал {channel_id}: {e}\n{traceback.format_exc()}")

        @bot_client.bot.event
        async def on_ready() -> None:
            """Обработчик события готовности бота."""
            logger.debug("Событие on_ready вызвано")
            try:
                # Запуск активности как фоновой задачи
                bot_client.bot.loop.create_task(set_bot_activity(bot_client.bot))
                logger.debug("set_bot_activity запущена как фоновая задача")
                
                # Регистрация команд
                await register_commands(bot_client.tree, bot_client)
                logger.success(f"Бот {bot_client.bot.user} запущен и готов к работе!")
            except Exception as e:
                logger.error(f"Ошибка в on_ready: {e}\n{traceback.format_exc()}")
                raise

        # Запуск Flask-сервера в отдельном потоке
        logger.debug(f"Запуск Flask-сервера на порту {config.FLASK_PORT}")
        Thread(target=run_flask, daemon=True).start()

        # Запуск бота
        session = ClientSession()
        bot_client.bot.session = session
        await bot_client.bot.start(config.TOKEN)
    except Exception as e:
        logger.error(f"Критическая ошибка запуска бота: {e}\n{traceback.format_exc()}")
        raise
    finally:
        if session and not session.closed:
            await session.close()
        await bot_client.close()
        logger.info("Бот остановлен")

def start_bot() -> None:
    """Старт бота с управлением асинхронным циклом."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(run_bot())
        else:
            loop.run_until_complete(run_bot())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}\n{traceback.format_exc()}")
        exit(1)

if __name__ == "__main__":
    start_bot()