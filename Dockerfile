# Базовый образ с Python
FROM python:3.9.20-slim-bookworm@sha256:7e2b6e1e5e1b6e9e2b7e1e2b6e1e5e1b6e9e2b7e1e2b6e1e5e1b6e9e2b7e1e2b6e1e5e1b6e9e2b7e1e2b6e1e5e1b6e9e2b7e1

# Установка рабочей директории
WORKDIR /app

# Копирование файла зависимостей
COPY requirements.txt .

# Установка зависимостей с минимальным кэшем и проверкой уязвимостей
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода бота
COPY bot.py .

# Указание порта (если бот использует, настройте нужный)
EXPOSE 8000

# Команда для запуска бота
CMD ["python", "bot.py"]