#!/bin/sh
# Очистка pyc-файлов и __pycache__ директорий
find /app -type f -name '*.pyc' -delete 2>/dev/null
find /app -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null

# Очистка временных директорий
rm -rf /tmp/* /var/tmp/* /root/.cache 2>/dev/null

# Запуск команды
exec "$@"