# Этап 1: Сборка зависимостей
FROM python:3.13-alpine AS builder

WORKDIR /app
COPY requirements.txt .
RUN apk add --no-cache gcc musl-dev linux-headers ffmpeg && \
    pip install --no-cache-dir -r requirements.txt && \
    apk del gcc musl-dev linux-headers && \
    rm -rf /root/.cache/pip

# Этап 2: Финальный образ
FROM python:3.13-alpine
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /usr/bin/ffmpeg /usr/bin/ffmpeg
COPY bot.py .
COPY src/ src/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh && \
    apk add --no-cache ffmpeg
ENV PYTHONDONTWRITEBYTECODE=1
ENV ENV=production
EXPOSE 5000
ENTRYPOINT ["./entrypoint.sh"]
CMD ["python", "bot.py"]