# Этап 1: Сборка зависимостей
FROM python:3.9.20-alpine AS builder

# Установка рабочей директории
WORKDIR /app

# Копирование файла зависимостей
COPY requirements.txt .

# Установка зависимостей с минимальным кэшем
RUN apk add --no-cache gcc musl-dev linux-headers && \
    pip install --no-cache-dir -r requirements.txt && \
    apk del gcc musl-dev linux-headers && \
    rm -rf /root/.cache/pip

# Этап 2: Финальный образ
FROM python:3.9.20-alpine

# Установка рабочей директории
WORKDIR /app

# Копирование установленных зависимостей
COPY --from=builder /usr/local/lib/python3.9/site-packages /usr/local/lib/python3.9/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Копирование проекта
COPY bot.py .
COPY src/ src/
COPY entrypoint.sh .

# Делаем entrypoint-скрипт исполняемым
RUN chmod +x entrypoint.sh

# Отключение создания pyc-файлов
ENV PYTHONDONTWRITEBYTECODE=1

# Указание порта
EXPOSE 8000

# Установка entrypoint
ENTRYPOINT ["./entrypoint.sh"]
CMD ["python", "main.py"]