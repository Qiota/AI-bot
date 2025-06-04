import logging
import discord
from discord.ext import commands
from decouple import config
from src.systemLog import logger

class InviteUtility(commands.Cog):
    """Утилита для обработки команд управления приглашениями на серверах."""

    def __init__(self, bot: commands.Bot):
        """
        Инициализация утилиты.

        Args:
            bot: Объект бота Discord (commands.Bot).
        """
        self.bot = bot
        self.developer_id = int(config('DEVELOPER_ID'))  # Загружаем ID разработчика из .env
        logger.info("Инициализация утилиты приглашений для серверов")

    @commands.command(name="s")
    async def server_list(self, ctx: commands.Context) -> None:
        """
        Команда !s list для вывода списка серверов, на которых находится бот.

        Args:
            ctx: Контекст команды.
        """
        if ctx.author.bot:
            return

        if len(ctx.message.content.split()) < 2 or ctx.message.content.split()[1].lower() != "list":
            await ctx.send("Использование: `!s list`")
            return

        logger.info("Список серверов, на которых находится бот:")
        server_list = "\n".join(f"- {guild.name} (ID: {guild.id})" for guild in self.bot.guilds)
        if server_list:
            logger.info(server_list)
            await ctx.send(f"Сервера, на которых находится бот:\n{server_list}")
        else:
            logger.info("Бот не находится на серверах")
            await ctx.send("Бот не находится на серверах")

    @commands.command(name="csl")
    async def create_server_link(self, ctx: commands.Context, *, server_name_or_id: str) -> None:
        """
        Команда !csl <server_name_or_id> для создания ссылки-приглашения (только для разработчика).

        Args:
            ctx: Контекст команды.
            server_name_or_id: Название или ID сервера.
        """
        if ctx.author.bot:
            return

        # Проверяем, что команда вызвана разработчиком
        if ctx.author.id != self.developer_id:
            logger.warning(f"Пользователь {ctx.author.id} попытался выполнить !csl, но не является разработчиком")
            await ctx.send("Эта команда доступна только разработчику.")
            return

        # Пытаемся найти сервер по ID или имени
        try:
            server_id = int(server_name_or_id)
            guild = discord.utils.get(self.bot.guilds, id=server_id)
        except ValueError:
            guild = discord.utils.get(self.bot.guilds, name=server_name_or_id)

        if not guild:
            logger.warning(f"Сервер с названием или ID '{server_name_or_id}' не найден")
            await ctx.send(f"Сервер с названием или ID '{server_name_or_id}' не найден")
            return

        try:
            invite_url = await self._create_invite(guild)
            logger.info(f"Ссылка приглашения для сервера '{guild.name}' (ID: {guild.id}): {invite_url}")
            await ctx.send("Ссылка-приглашение создана и выведена в консоль.")
        except discord.errors.Forbidden:
            logger.warning(f"Недостаточно прав для создания ссылки на сервере: {guild.name} (ID: {guild.id})")
            await ctx.send(f"Недостаточно прав для создания ссылки на сервере: {guild.name}")
        except Exception as e:
            logger.error(f"Ошибка при создании ссылки для сервера {guild.name}: {e}")
            await ctx.send(f"Ошибка при создании ссылки для сервера {guild.name}: {e}")

    async def _create_invite(self, guild: discord.Guild) -> str:
        """
        Создаёт ссылку-приглашение для указанного сервера.

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
            reason="Генерация ссылки приглашения через команду !csl"
        )
        return str(invite.url)

async def setup(bot: commands.Bot) -> None:
    """
    Регистрирует ког InviteUtility в боте.

    Args:
        bot: Объект бота Discord.
    """
    await bot.add_cog(InviteUtility(bot))
    logger.info("Ког InviteUtility зарегистрирован")
