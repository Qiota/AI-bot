from flask import Flask
from .config import BotConfig
from .systemLog import logger

app = Flask(__name__)

@app.route('/')
def home():
    return "Бот активен!"

def run_flask():
    """Запуск Flask-сервера."""
    config = BotConfig()
    host = config.FLASK_HOST or ('127.0.0.1' if config.ENV == 'production' else '0.0.0.0')
    try:
        logger.info(f"Запуск Flask на {host}:{config.FLASK_PORT}")
        app.run(host=host, port=config.FLASK_PORT, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Ошибка запуска Flask: {e}")
        raise