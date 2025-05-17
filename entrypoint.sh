#!/bin/bash
# Очистка pyc-файлов и __pycache__ директорий
find /app -type f -name '*.pyc' -delete
find /app -type d -name '__pycache__' -exec rm -rf {} +

# Очистка временных файлов, если они есть
rm -rf /tmp/* /var/tmp/*

# Запуск переданной команды (например, python bot.py)
exec "$@"