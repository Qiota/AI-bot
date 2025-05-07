from flask import Flask
from ...systemLog import logger
import os
import logging

app = Flask(__name__)

werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.setLevel(logging.ERROR)

class BotConfig:
    FLASK_HOST = '0.0.0.0'
    FLASK_PORT = int(os.getenv('PORT', 5000))
    ENV = os.getenv('ENV', 'development')

@app.route('/')
def home():
    return "Бот активен!"

@app.route('/health')
def health():
    return "OK", 200

def run_flask():
    """Запуск Flask-сервера."""
    config = BotConfig()
    try:
        logger.info(f"Запуск Flask на {config.FLASK_HOST}:{config.FLASK_PORT} в режиме {config.ENV}")
        app.run(
            host=config.FLASK_HOST,
            port=config.FLASK_PORT,
            debug=config.ENV != 'production',
            use_reloader=False
        )
    except Exception as e:
        logger.error(f"Ошибка запуска Flask: {e}")
        raise

if __name__ == '__main__':
    run_flask()