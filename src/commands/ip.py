import discord
from discord import app_commands
import aiohttp
from typing import Dict, Any, Optional
from datetime import datetime
import urllib.parse
import ipaddress
import re
from pydantic import BaseModel, Field
from threading import Lock
from ..systemLog import logger

# Модель для данных ответа API
class IPData(BaseModel):
    status: str
    message: Optional[str] = None
    query: Optional[str] = None
    country: Optional[str] = None
    countryCode: Optional[str] = None
    region: Optional[str] = None
    regionName: Optional[str] = None
    city: Optional[str] = None
    zip: Optional[str] = Field(None, alias="zip")
    lat: Optional[float] = None
    lon: Optional[float] = None
    timezone: Optional[str] = None
    isp: Optional[str] = None
    org: Optional[str] = None
    mobile: Optional[bool] = False
    proxy: Optional[bool] = False
    hosting: Optional[bool] = False

# Класс для управления лимитами запросов
class RateLimiter:
    def __init__(self):
        self.remaining_requests = 45
        self.reset_time = datetime.now().timestamp() * 1000
        self.lock = Lock()

    def update_limits(self, headers: Dict[str, str]) -> None:
        """Обновляет лимиты запросов на основе заголовков ответа API."""
        with self.lock:
            self.remaining_requests = int(headers.get("X-Rl", 0))
            ttl = int(headers.get("X-Ttl", 0)) * 1000
            self.reset_time = datetime.now().timestamp() * 1000 + ttl

    def can_make_request(self) -> tuple[bool, Optional[int]]:
        """Проверяет, можно ли выполнить запрос."""
        with self.lock:
            current_time = datetime.now().timestamp() * 1000
            if self.remaining_requests <= 0 and current_time < self.reset_time:
                reset_in = int((self.reset_time - current_time) / 1000)
                return False, reset_in
            return True, None

# Глобальный объект для управления лимитами
rate_limiter = RateLimiter()

def validate_address(address: str) -> bool:
    """
    Проверяет, является ли строка валидным IP-адресом или доменом.

    Args:
        address (str): IP-адрес или доменное имя.

    Returns:
        bool: True, если адрес валиден, иначе False.
    """
    # Проверка IP-адреса
    try:
        ipaddress.ip_address(address)
        return True
    except ValueError:
        pass

    # Проверка домена
    domain_pattern = r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.[A-Za-z]{2,})+$"
    return bool(re.match(domain_pattern, address))

async def fetch_ip_data(ip_address: str) -> Dict[str, Any]:
    """
    Асинхронно запрашивает информацию об IP-адресе через ip-api.com.

    Args:
        ip_address (str): IP-адрес или доменное имя.

    Returns:
        Dict[str, Any]: Словарь с данными ответа API и заголовками.

    Raises:
        aiohttp.ClientError: Если произошла ошибка при выполнении запроса.
        ValueError: Если адрес невалиден.
    """
    if not validate_address(ip_address):
        raise ValueError("Невалидный IP-адрес или доменное имя.")

    api_url = f"{'http://ip-api.com/json/'}{urllib.parse.quote(ip_address)}?fields=status,message,continent,continentCode,country,countryCode,region,regionName,city,district,zip,lat,lon,timezone,offset,currency,isp,org,as,asname,reverse,mobile,proxy,hosting,query"

    async with aiohttp.ClientSession() as session:
        async with session.get(api_url) as response:
            data = await response.json()
            headers = response.headers
            rate_limiter.update_limits(headers)
            return {"data": IPData(**data), "headers": headers}

async def ip_command(interaction: discord.Interaction, address: str) -> None:
    """
    Слеш-команда /ip: Получает информацию об IP-адресе или домене.

    Args:
        interaction (discord.Interaction): Объект взаимодействия с Discord.
        address (str): IP-адрес или доменное имя.
    """
    try:
        # Проверка лимита запросов
        can_request, reset_in = rate_limiter.can_make_request()
        if not can_request:
            await interaction.response.send_message(
                f"Вы превысили лимит запросов. Пожалуйста, подождите {reset_in} секунд.",
                ephemeral=True
            )
            return

        # Откладываем ответ
        await interaction.response.defer()

        # Запрос данных
        result = await fetch_ip_data(address)
        ip_data: IPData = result["data"]

        if ip_data.status == "success":
            # Формирование ссылки на Google Maps
            map_link = f"https://www.google.com/maps?q={ip_data.lat},{ip_data.lon}" if ip_data.lat and ip_data.lon else "Не доступно"

            # Создание эмбеда
            embed = discord.Embed(
                title=f"Информация о IP-адресе: {ip_data.query or address}",
                description="### Информация может быть не точной или не совпадать вообще. Сделано для развлечения.",
                color=0x0099ff,
                timestamp=datetime.now()
            )
            embed.add_field(name="Страна", value=f"{ip_data.country or 'N/A'} ({ip_data.countryCode or 'N/A'})", inline=True)
            embed.add_field(name="Регион", value=f"{ip_data.region or 'N/A'} ({ip_data.regionName or 'N/A'})", inline=True)
            embed.add_field(name="Город", value=ip_data.city or "N/A", inline=True)
            embed.add_field(name="Почтовый индекс", value=ip_data.zip or "N/A", inline=True)
            embed.add_field(name="Координаты", value=f"[Ссылка]({map_link})" if map_link != "Не доступно" else "N/A", inline=True)
            embed.add_field(name="Часовой пояс", value=ip_data.timezone or "N/A", inline=True)
            embed.add_field(name="Провайдер", value=ip_data.isp or "N/A", inline=True)
            embed.add_field(name="Организация", value=ip_data.org or "N/A", inline=True)
            embed.add_field(name="Мобильная связь", value="Да" if ip_data.mobile else "Нет", inline=True)
            embed.add_field(name="Прокси", value="Да" if ip_data.proxy else "Нет", inline=True)
            embed.add_field(name="Хостинг", value="Да" if ip_data.hosting else "Нет", inline=True)
            embed.set_footer(text=f"Запрос выполнил: {interaction.user.name}")

            await interaction.followup.send(embed=embed)
            logger.debug(
                f"Команда /ip выполнена: user={interaction.user.id}, guild={interaction.guild.id if interaction.guild else 'DM'}, address={address}"
            )
        else:
            await interaction.followup.send(
                f"Не удалось получить информацию: {ip_data.message or 'Неизвестная ошибка'}",
                ephemeral=True
            )
    except ValueError as ve:
        logger.warning(f"Невалидный адрес: {address}, user={interaction.user.id}")
        await interaction.followup.send(str(ve), ephemeral=True)
    except aiohttp.ClientError as ce:
        logger.error(f"Ошибка API для {address}: {ce}, user={interaction.user.id}")
        await interaction.followup.send("Ошибка при запросе к API.", ephemeral=True)
    except Exception as e:
        logger.error(f"Необработанная ошибка команды /ip: {e}, user={interaction.user.id}")
        await interaction.followup.send("Произошла ошибка при выполнении команды.", ephemeral=True)

def create_command(bot_client: discord.Client) -> app_commands.Command:
    """
    Создаёт слеш-команду /ip.

    Args:
        bot_client (discord.Client): Клиент бота Discord.

    Returns:
        app_commands.Command: Объект команды.
    """
    @app_commands.command(name="ip", description="Получить информацию о заданном IP-адресе или домене.")
    @app_commands.describe(address="IP-адрес или доменное имя для получения информации.")
    async def wrapper(interaction: discord.Interaction, address: str) -> None:
        await ip_command(interaction, address)

    wrapper.dm_only = False
    return wrapper