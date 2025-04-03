import os
from flask import Flask
from .config import BotConfig, logger

app = Flask(__name__)

@app.route('/')
def home():
    return "Бот активен!"

def run_flask():
    """Запуск Flask-сервера."""
    config = BotConfig()
    host = '127.0.0.1' if os.getenv('FLASK_ENV') == 'production' else '0.0.0.0'
    logger.info(f"Запуск Flask на порту {config.FLASK_PORT} (host: {host})")
    app.run(host=host, port=config.FLASK_PORT, debug=False, use_reloader=False)