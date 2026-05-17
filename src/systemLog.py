"""Система логування українською мовою з красивим виводом."""

import logging
from logging.handlers import RotatingFileHandler
from colorama import init, Fore, Style, Back
from decouple import config
import os
import sys

init(autoreset=True)

SUCCESS = 25
logging.addLevelName(SUCCESS, "SUCCESS")

def success(self, message, *args, **kwargs):
    if self.isEnabledFor(SUCCESS):
        self._log(SUCCESS, message, args, **kwargs)
logging.Logger.success = success

COLORS = {
    "DEBUG": Fore.CYAN,
    "INFO": Fore.LIGHTWHITE_EX,
    "SUCCESS": Fore.LIGHTGREEN_EX,
    "WARNING": Fore.YELLOW,
    "ERROR": Fore.RED,
    "CRITICAL": Fore.RED + Back.WHITE,
}

EMOJI = {
    "DEBUG": "[D]",
    "INFO": "[I]",
    "SUCCESS": "[S]",
    "WARNING": "[W]",
    "ERROR": "[E]",
    "CRITICAL": "[C]",
}

def get_banner() -> str:
    """Красивий банер при старті."""
    return f"""
{Fore.MAGENTA}╔══════════════════════════════════════════════════════════╗
║   🤖 NOXI BOT - AI Chat System v2.0                     ║
║   Створено: Qiota | Система: Discord + g4f              ║
╚══════════════════════════════════════════════════════════╝{Style.RESET_ALL}
"""

def setup_logging():
    """Налаштування логування українською мовою."""
    log_level = getattr(logging, config("LOG_LEVEL", default="INFO").upper(), logging.INFO)
    
    logger = logging.getLogger()
    logger.setLevel(log_level)
    logger.handlers.clear()

    class UkrainianFormatter(logging.Formatter):
        """Форматтер з українськими повідомленнями та кольорами."""
        
        def __init__(self):
            super().__init__()
            self.level_translations = {
                "DEBUG": "ВІДЛАДКА",
                "INFO": "ІНФО",
                "SUCCESS": "УСПІХ",
                "WARNING": "ПОПЕРЕДЖЕННЯ",
                "ERROR": "ПОМИЛКА",
                "CRITICAL": "КРИТИЧНО",
            }
        
        def format(self, record):
            level = self.level_translations.get(record.levelname, record.levelname)
            emoji = EMOJI.get(record.levelname, "📝")
            color = COLORS.get(record.levelname, Fore.WHITE)
            
            msg = record.getMessage()
            
            return f"{color}{emoji} [{level:^12}] [{record.name:^8}] {msg}{Style.RESET_ALL}"

    class FileFormatter(logging.Formatter):
        """Форматтер для файлу без кольорів."""
        def __init__(self):
            super().__init__(fmt="%(asctime)s - %(levelname)s - [%(name)s] - %(message)s")
            self.level_translations = {
                "DEBUG": "DEBUG",
                "INFO": "INFO", 
                "SUCCESS": "SUCCESS",
                "WARNING": "WARNING",
                "ERROR": "ERROR",
                "CRITICAL": "CRITICAL",
            }
        
        def format(self, record):
            record.levelname = self.level_translations.get(record.levelname, record.levelname)
            return super().format(record)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(UkrainianFormatter())
    logger.addHandler(console_handler)

    os.makedirs("logs", exist_ok=True)
    file_handler = RotatingFileHandler(
        filename="logs/bot.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(FileFormatter())
    logger.addHandler(file_handler)

    return logger

logger = setup_logging()

def print_banner():
    """Друкувати банер при старті бота."""
    try:
        print(get_banner())
    except Exception:
        pass