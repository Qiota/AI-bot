import asyncio
import importlib
from pathlib import Path
from threading import Thread
import discord
from discord import app_commands
from aiohttp import ClientSession
from .config import BotConfig
from .aichat import BotClient
from .systemLog import logger
from .utils.server.flask import run_flask
from .commands.restrict import check_bot_access, restrict_command_execution
from .events.activity import set_bot_activity
from .utils.checker import checker
import time
from typing import Optional, Union, Tuple, List

async def precheck_command_execution(interaction: discord.Interaction, command_name: str, bot_client: BotClient) -> Tuple[bool, str]:
    """Предварительная проверка выполнения команды."""
    guild_id = str(interaction.guild.id) if interaction.guild else "DM"
    logger.debug(f"Предпроверка команды {command_name} для пользователя {interaction.user.id} в {guild_id}")
    
    # Проверка готовности бота
    if not bot_client.bot.is_ready():
        logger.debug("Бот не готов")
        return False, "Бот еще не готов."
    
    # Проверка конфигурации сервера
    if not await restrict_command_execution(interaction, bot_client):
        logger.debug("restrict_command_execution вернул False")
        return False, "Конфигурация сервера не найдена."
    
    # Проверка команды /restrict в DM
    if command_name == "restrict" and not interaction.guild:
        logger.debug("Команда /restrict вызвана в DM")
        return False, "Команда только для серверов!"
    
    # Проверки для гильдий (кроме команды /restrict)
    if interaction.guild and command_name != "restrict":
        # Проверка доступа к каналу
        access, access_reason = await check_bot_access(interaction, bot_client)
        if not access:
            logger.debug(f"check_bot_access вернул False для канала {interaction.channel_id}: {access_reason or 'нет доступа'}")
            return False, f"Бот не имеет доступа! Причина: {access_reason}" if access_reason else "Бот не имеет доступа."
        
        # Проверка ограничений пользователя
        restriction, restriction_reason = await checker.check_user_restriction(interaction)
        if not restriction:
            logger.debug(f"Пользователь {interaction.user.id} ограничен")
            return False, restriction_reason or "Ваш доступ к боту ограничен."
    
    logger.debug(f"Все проверки пройдены для команды {command_name}")
    return True, "Проверки пройдены."

async def should_execute_command(interaction: discord.Interaction, command_name: str, bot_client: BotClient) -> bool:
    """Проверка выполнения команды с отправкой сообщений об ошибках."""
    can_execute, reason = await precheck_command_execution(interaction, command_name, bot_client)
    if not can_execute:
        logger.debug(f"Команда {command_name} не выполняется: {reason}")
        if reason:  # Отправка сообщения при любой ошибке
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(reason, ephemeral=True)
                else:
                    await interaction.followup.send(reason, ephemeral=True)
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения для команды {command_name}: {e}")
        return False
    return True

async def apply_command_checks(interaction: discord.Interaction, command_name: str, bot_client: BotClient) -> bool:
    """Применение проверок для команды."""
    return await should_execute_command(interaction, command_name, bot_client)

def add_checks_to_command(command: Union[app_commands.Command, app_commands.Group], bot_client: BotClient) -> None:
    """Добавление проверок к команде или группе команд."""
    if isinstance(command, app_commands.Group):
        for subcommand in command.commands:
            add_checks_to_command(subcommand, bot_client)
    else:
        async def check(interaction: discord.Interaction) -> bool:
            return await should_execute_command(interaction, command.name, bot_client)
        command.add_check(check)
        logger.debug(f"Добавлены проверки для команды {command.name}")

async def load_command_module(file_path: Path, commands_dir: Path, bot_client: BotClient) -> Optional[List[Tuple[Union[app_commands.Command, app_commands.Group], str]]]:
    """Загрузка модуля команд."""
    try:
        relative_path = file_path.relative_to(commands_dir)
        module_name = f"src.commands.{str(relative_path.with_suffix('')).replace('/', '.').replace('\\', '.')}"
        module = importlib.import_module(module_name)
        create_command = getattr(module, "create_command", None)
        if not create_command:
            logger.warning(f"create_command не найден в {module_name}")
            return None

        cog = bot_client
        if module_name == "src.commands.google":
            cog = module.GoogleSearch(ClientSession())

        # Поддержка асинхронного create_command
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

            add_checks_to_command(cmd, bot_client)
            context = f"[{'ЛС' if dm_only else 'серверов' if guild_only else 'ЛС и серверов'}]"
            settings = {
                "name": cmd.name,
                "type": "group" if isinstance(cmd, app_commands.Group) else "command",
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
        logger.error(f"Ошибка импорта модуля {file_path.stem}: {e}")
        return None
    except Exception as e:
        logger.error(f"Ошибка загрузки модуля {file_path}: {e}")
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
                        logger.debug(f"Добавлена команда {command.name} в CommandTree")

    try:
        tree.clear_commands(guild=None)
        await scan_commands(commands_dir)
        synced = await tree.sync(guild=None)
        logger.info(f"Синхронизировано {len(synced)} глобальных команд")
    except Exception as e:
        logger.error(f"Ошибка регистрации команд: {e}")
        raise

async def run_bot() -> None:
    """Запуск Discord-бота."""
    config = BotConfig()
    bot_client = BotClient(config)
    bot_client.start_time = time.time()

    try:
        config.validate()
        bot_client.bot.event(bot_client.on_message)
        bot_client.bot.event(bot_client.on_message_edit)

        @bot_client.tree.error
        async def on_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
            """Обработка ошибок команд."""
            try:
                if isinstance(error, app_commands.CheckFailure):
                    restriction, restriction_reason = await checker.check_user_restriction(interaction)
                    if not restriction:
                        logger.debug(f"Команда {interaction.command.name} отклонена для {interaction.user.id}: ограничен")
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
                            logger.error(f"Ошибка отправки сообщения об ошибке команды {interaction.command.name}: {e}")
                        return
                    
                    can_execute, reason = await precheck_command_execution(interaction, interaction.command.name, bot_client)
                    if not can_execute and reason:
                        try:
                            if not interaction.response.is_done():
                                await interaction.response.send_message(
                                    reason if "Причина:" in reason else f"{interaction.user.mention}, бот не работает в этом канале.",
                                    ephemeral=True
                                )
                            else:
                                await interaction.followup.send(
                                    reason if "Причина:" in reason else f"{interaction.user.mention}, бот не работает в этом канале.",
                                    ephemeral=True
                                )
                        except Exception as e:
                            logger.error(f"Ошибка отправки сообщения об ошибке команды {interaction.command.name}: {e}")
                else:
                    logger.error(f"Ошибка команды {interaction.command.name} для {interaction.user.id}: {error}")
                    try:
                        if not interaction.response.is_done():
                            await interaction.response.send_message("Ошибка выполнения команды.", ephemeral=True)
                        else:
                            await interaction.followup.send("Ошибка выполнения команды.", ephemeral=True)
                    except Exception as e:
                        logger.error(f"Ошибка отправки сообщения об ошибке команды {interaction.command.name}: {e}")
            except Exception as e:
                logger.error(f"Критическая ошибка обработки ошибки команды {interaction.command.name}: {e}")

        @bot_client.bot.event
        async def on_ready():
            """Обработчик события готовности бота."""
            await bot_client.bot.wait_until_ready()
            await set_bot_activity(bot_client.bot)
            await register_commands(bot_client.tree, bot_client)
            logger.info(f"Бот {bot_client.bot.user} запущен и готов к работе!")

        # Запуск Flask-сервера в отдельном потоке
        logger.debug(f"Запуск Flask-сервера на порту {config.FLASK_PORT}")
        Thread(target=run_flask, daemon=True).start()

        # Запуск бота
        async with ClientSession() as session:
            bot_client.bot.session = session
            await bot_client.bot.start(config.TOKEN)
    except Exception as e:
        logger.error(f"Критическая ошибка запуска бота: {e}")
        raise
    finally:
        if hasattr(bot_client, 'client') and hasattr(bot_client.client, '_session'):
            await bot_client.client._session.close()
        await bot_client.bot.close()
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
        logger.error(f"Критическая ошибка: {e}")
        exit(1)

if __name__ == "__main__":
    start_bot()