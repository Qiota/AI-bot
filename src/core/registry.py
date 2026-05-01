"""Auto-discovery and registration of slash commands.

Extracted from start.py to separate command loading logic from bot startup.
"""

import asyncio
import importlib
from pathlib import Path
from typing import List, Optional, Tuple, Union

import discord
from discord import app_commands

from ..systemLog import logger


async def load_command_module(
    file_path: Path,
    commands_dir: Path,
    bot_client,
    tree: app_commands.CommandTree,
) -> Optional[List[Tuple[Union[app_commands.Command, app_commands.Group], str]]]:
    """Dynamically loads a command module and extracts its create_command factory."""
    if bot_client is None:
        logger.error(f"BotClient не инициализирован для модуля {file_path.stem}")
        return None

    try:
        relative_path = file_path.relative_to(commands_dir)
        module_name = (
            f"src.commands.{str(relative_path.with_suffix('')).replace('/', '.').replace('\\', '.')}"
        )
        module = importlib.import_module(module_name)
        create_command = getattr(module, "create_command", None)
        if not create_command:
            logger.warning(f"create_command не найден в {module_name}")
            return None

        # Determine the cog/object to pass
        cog = bot_client
        if module_name == "src.commands.google":
            try:
                cog = module.GoogleSearch(bot_client)
            except (TypeError, AttributeError) as e:
                logger.error(f"Ошибка инициализации GoogleSearch в {module_name}: {e}")
                return None

        logger.debug(f"Загрузка команды из {module_name} с cog={type(cog).__name__}")
        command = (
            await create_command(cog)
            if asyncio.iscoroutinefunction(create_command)
            else create_command(cog)
        )
        commands = command if isinstance(command, tuple) else (command,)
        result = []
        loaded = []
        existing = {cmd.name for cmd in tree.get_commands()}

        for cmd in commands:
            if cmd.name in existing:
                logger.warning(f"Команда {cmd.name} уже зарегистрирована, пропускаем")
                continue

            dm_only = getattr(cmd, "dm_only", False)
            guild_only = getattr(cmd, "guild_only", False)
            if dm_only and guild_only:
                logger.warning(f"Команда {cmd.name} не может быть одновременно dm_only и guild_only")
                continue

            ctx = f"[{'DM' if dm_only else 'guild' if guild_only else 'DM & guild'}]"
            loaded.append(f"/{cmd.name} ({ctx})")
            result.append((cmd, ctx))

        if loaded:
            logger.info(f"Загружены команды из {module_name}: {', '.join(loaded)}")
        return result
    except ImportError as e:
        logger.error(f"Ошибка импорта в {file_path.stem}: {e}")
        return None
    except Exception as e:
        logger.error(f"Ошибка загрузки модуля {file_path}: {e}")
        return None


async def register_commands(
    tree: app_commands.CommandTree, bot_client
) -> None:
    """Recursively scans src/commands/ and registers all discovered commands."""
    commands_dir = Path(__file__).parent.parent / "commands"

    async def scan(dirs: Path) -> None:
        for item in dirs.iterdir():
            if item.is_dir():
                await scan(item)
            elif item.suffix == ".py" and item.stem != "__init__":
                is_top_level = dirs == commands_dir
                is_command_entry = item.stem == "command"
                if not (is_top_level or is_command_entry):
                    logger.debug(f"Пропускаем некомандный хелпер: {item}")
                    continue

                logger.debug(f"Сканирование файла: {item}")
                loaded = await load_command_module(
                    item, commands_dir, bot_client, tree
                )
                if loaded:
                    for command, context in loaded:
                        logger.info(f"Добавление команды {command.name} {context}")
                        tree.add_command(command)

    try:
        tree.clear_commands(guild=None)
        logger.info("Глобальные команды очищены")
        await scan(commands_dir)
        synced = await tree.sync(guild=None)
        logger.success(f"Синхронизировано {len(synced)} глобальных команд")
    except Exception as e:
        logger.error(f"Ошибка регистрации команд: {e}")
        raise
