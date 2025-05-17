# Этап 1: Сборка зависимостей
FROM python:3.9.20-slim-bullseye AS builder

# Установка рабочей директории
WORKDIR /app

# Копирование файла зависимостей
COPY requirements.txt .

# Установка зависимостей с минимальным кэшем и очисткой
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    pip install --no-cache-dir -r requirements.txt && \
    apt-get remove -y gcc && \
    apt-get autoremove -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Этап 2: Финальный образ
FROM python:3.9.20-slim-bullseye

# Установка рабочей директории
WORKDIR /app

# Копирование установленных зависимостей из этапа сборки
COPY --from=builder /usr/local/lib/python3.9/site-packages /usr/local/lib/python3.9/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Копирование кода бота
COPY bot.py .
COPY entrypoint.sh .

# Делаем entrypoint-скрипт исполняемым
RUN chmod +x entrypoint.sh

# Указание порта (если бот использует веб-сервер, настройте нужный)
EXPOSE 8000

# Установка entrypoint для очистки и запуска
ENTRYPOINT ["./entrypoint.sh"]
CMD ["python", "bot.py"]