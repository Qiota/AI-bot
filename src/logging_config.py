import logging
from logging.handlers import RotatingFileHandler
from colorama import init, Fore, Style
from decouple import config
import os
import sys

init(autoreset=True)

def setup_logging():
    """Настройка логирования с цветами, ротацией файлов и гибкостью."""
    log_level = getattr(logging, config("LOG_LEVEL", default="INFO").upper(), logging.INFO)
    logger = logging.getLogger()
    logger.setLevel(log_level)
    log_format = "%(asctime)s - %(levelname)s - [%(name)s] - %(message)s"

    class ColoredFormatter(logging.Formatter):
        COLORS = {
            'DEBUG': Fore.BLUE,
            'INFO': Fore.GREEN,
            'WARNING': Fore.YELLOW,
            'ERROR': Fore.RED,
            'CRITICAL': Fore.RED + Style.BRIGHT,
        }

        def format(self, record):
            levelname = record.levelname
            color = self.COLORS.get(levelname, Fore.WHITE)
            message = super().format(record)
            return f"{color}{message}{Style.RESET_ALL}"

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.stream = sys.stdout
    console_handler.stream.reconfigure(encoding='utf-8', errors='replace') 
    console_handler.setFormatter(ColoredFormatter(log_format))

    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    file_handler = RotatingFileHandler(
        filename=os.path.join(log_dir, "bot.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter(log_format))

    logger.handlers.clear()
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger

logger = setup_logging()