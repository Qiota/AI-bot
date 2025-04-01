# Базовый образ с Python
FROM python:3.9-slim

# Установка рабочей директории
WORKDIR /app

# Копирование файла зависимостей
COPY requirements.txt .

# Установка зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода бота
COPY bot.py .

# Команда для запуска бота
CMD ["python", "bot.py"]