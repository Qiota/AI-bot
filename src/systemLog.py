import logging
from logging.handlers import RotatingFileHandler
from colorama import init, Fore, Style
from decouple import config
import os

init(autoreset=True)

# Добавляем уровень SUCCESS
SUCCESS = 25
logging.addLevelName(SUCCESS, "SUCCESS")
def success(self, message, *args, **kwargs):
    if self.isEnabledFor(SUCCESS):
        self._log(SUCCESS, message, args, **kwargs)
logging.Logger.success = success

def setup_logging():
    """Настройка логирования с цветами в консоли и ротацией файлов."""
    log_level = getattr(logging, config("LOG_LEVEL", default="INFO").upper(), logging.INFO)
    log_format = "%(asctime)s - %(levelname)s - [%(name)s] - %(message)s"
    logger = logging.getLogger()
    logger.setLevel(log_level)
    logger.handlers.clear()

    # Форматтер с цветами для консоли
    class ColoredFormatter(logging.Formatter):
        COLORS = {
            "DEBUG": Fore.BLUE,
            "INFO": Fore.LIGHTBLACK_EX,
            "SUCCESS": Fore.LIGHTGREEN_EX,
            "WARNING": Fore.YELLOW,
            "ERROR": Fore.RED,
            "CRITICAL": Fore.LIGHTRED_EX,
        }

        def format(self, record):
            color = self.COLORS.get(record.levelname, Fore.WHITE)
            message = super().format(record)
            return f"{color}{message}{Style.RESET_ALL}"

    # Форматтер без цветов для файла
    class PlainFormatter(logging.Formatter):
        def format(self, record):
            return super().format(record)

    # Консольный обработчик
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(ColoredFormatter(log_format))
    logger.addHandler(console_handler)

    # Файловый обработчик с ротацией
    os.makedirs("logs", exist_ok=True)
    file_handler = RotatingFileHandler(
        filename="logs/bot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(PlainFormatter(log_format))
    logger.addHandler(file_handler)

    return logger

logger = setup_logging()