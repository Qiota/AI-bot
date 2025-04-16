import logging
from logging.handlers import RotatingFileHandler
from colorama import init, Fore, Style
from decouple import config
import os
from discord import SyncWebhook, Embed
from collections import defaultdict
import time
from queue import Queue
import threading

init(autoreset=True)

SUCCESS = 25
logging.addLevelName(SUCCESS, "SUCCESS")

def success(self, message, *args, **kwargs):
    if self.isEnabledFor(SUCCESS):
        self._log(SUCCESS, message, args, **kwargs)

logging.Logger.success = success

webhook_error_logger = logging.getLogger("webhook_error")
webhook_error_logger.setLevel(logging.ERROR)
webhook_error_handler = logging.StreamHandler()
webhook_error_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
webhook_error_logger.addHandler(webhook_error_handler)

class DiscordWebhookHandler(logging.Handler):
    """Обработчик логов для отправки через Discord Webhook с использованием discord.py."""
    def __init__(self, webhook_url: str, mention: str = "<@1297639749162635358>"):
        super().__init__()
        self.webhook_url = webhook_url
        self.mention = mention
        self.log_counts = defaultdict(int)
        self.last_message = None
        self.webhook = SyncWebhook.from_url(webhook_url)
        self.log_queue = Queue()
        self._start_queue_processor()

    def _start_queue_processor(self):
        """Запуск обработчика очереди в отдельном потоке с задержкой."""
        def process_queue():
            while True:
                if not self.log_queue.empty():
                    message, level = self.log_queue.get()
                    self._send_log_to_discord(message, level)
                    time.sleep(2)
                else:
                    time.sleep(0.1)

        thread = threading.Thread(target=process_queue, daemon=True)
        thread.start()

    def _send_log_to_discord(self, message: str, level: str):
        """Отправка одного лога в Discord с использованием Embed и цветами."""
        if len(message) > 4096: 
            message = message[:4093] + "..."

        colors = {
            "INFO": 0xd3d3d3,     # Светло-серый
            "SUCCESS": 0x90ee90,  # Салатовый
            "WARNING": 0xffff00,  # Жёлтый
            "ERROR": 0xff0000,    # Красный
            "CRITICAL": 0x8b0000, # Тёмно-красный
            "DEBUG": 0x0000ff,    # Синий
        }
        color = colors.get(level, 0xffffff)

        embed = Embed(description=message, color=color)
        try:
            self.webhook.send(embed=embed, content=self.mention)
        except Exception as e:
            error_msg = f"Ошибка отправки Webhook: {str(e)}"
            webhook_error_logger.error(error_msg)

    def emit(self, record):
        try:
            message = self.format(record)
            level = record.levelname
            if message == self.last_message:
                self.log_counts[message] += 1
                return
            if self.last_message:
                if self.log_counts[self.last_message] > 0:
                    final_message = f"{self.last_message} ({self.log_counts[self.last_message] + 1})"
                else:
                    final_message = self.last_message
                self.log_queue.put((final_message, level))
            self.log_counts[self.last_message] = 0
            self.last_message = message
        except Exception as e:
            error_msg = f"Ошибка в DiscordWebhookHandler: {str(e)}"
            webhook_error_logger.error(error_msg)

    def flush(self):
        """Принудительная отправка последнего лога при завершении."""
        if self.last_message:
            if self.log_counts[self.last_message] > 0:
                final_message = f"{self.last_message} ({self.log_counts[self.last_message] + 1})"
            else:
                final_message = self.last_message
            self.log_queue.put((final_message, "INFO"))
        self.last_message = None
        self.log_counts.clear()

def setup_logging():
    """Настройка логирования с цветами, ротацией файлов и Discord Webhook."""
    log_level = getattr(logging, config("LOG_LEVEL", default="INFO").upper(), logging.INFO)
    log_format = "%(asctime)s - %(levelname)s - [%(name)s] - %(message)s"
    logger = logging.getLogger()
    logger.setLevel(log_level)
    logger.handlers.clear()

    class ColoredFormatter(logging.Formatter):
        COLORS = {
            'DEBUG': Fore.BLUE,           # Синий (#0000ff)
            'INFO': Fore.LIGHTBLACK_EX,   # Светло-серый (#d3d3d3)
            'SUCCESS': Fore.LIGHTGREEN_EX, # Салатовый (#90ee90)
            'WARNING': Fore.YELLOW,       # Жёлтый (#ffff00)
            'ERROR': Fore.RED,            # Красный (#ff0000)
            'CRITICAL': Fore.LIGHTRED_EX, # Тёмно-красный (#8b0000)
        }

        def format(self, record):
            color = self.COLORS.get(record.levelname, Fore.WHITE)
            message = super().format(record)
            return f"{color}{message}{Style.RESET_ALL}"

    class PlainFormatter(logging.Formatter):
        """Форматтер без цветовых кодов для Discord и файла."""
        def format(self, record):
            return super().format(record)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(ColoredFormatter(log_format))
    logger.addHandler(console_handler)

    os.makedirs("logs", exist_ok=True)
    file_handler = RotatingFileHandler(
        filename="logs/bot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(PlainFormatter(log_format))
    logger.addHandler(file_handler)

    webhook_url = config("DISCORD_WEBHOOK_URL", default=None)
    if webhook_url:
        discord_handler = DiscordWebhookHandler(webhook_url, mention="<@1297639749162635358>")
        discord_handler.setLevel(log_level)
        discord_handler.setFormatter(PlainFormatter(log_format))
        logger.addHandler(discord_handler)
    else:
        logger.warning("Webhook URL не указан, логи в Discord не отправляются.")

    return logger

logger = setup_logging()