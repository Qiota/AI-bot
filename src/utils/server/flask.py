from flask import Flask, render_template, request, redirect, url_for, session, Response
from flask_socketio import SocketIO
import logging
from logging.handlers import QueueHandler
from queue import Queue
import functools
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secure_flask_socketio_key_123'  # Для сессий и SocketIO
socketio = SocketIO(app, cors_allowed_origins="*")
log_queue = Queue()
PASSWORD = "_2qgp5m_"

# Заглушка для BotConfig
class BotConfig:
    FLASK_HOST = '0.0.0.0'
    FLASK_PORT = int(os.getenv('PORT', 5000))  # Используем PORT из окружения
    ENV = os.getenv('ENV', 'development')

# Настройка логирования
logger = logging.getLogger('flask_app')
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(console_handler)

# Кастомный обработчик логов для WebSocket
class WebSocketHandler(logging.Handler):
    def emit(self, record):
        try:
            log_entry = self.format(record)
            socketio.emit('log', {'message': log_entry}, namespace='/console')
        except Exception as e:
            print(f"Ошибка отправки лога: {e}")

log_handler = WebSocketHandler()
log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(log_handler)

# Декоратор для проверки авторизации
def login_required(view):
    @functools.wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapped_view

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == PASSWORD:
            session['logged_in'] = True
            logger.info("Успешная авторизация")
            return redirect(url_for('home'))
        else:
            logger.warning("Попытка авторизации с неверным паролем")
            return render_template('login.html', error="Неверный пароль")
    return render_template('login.html', error=None)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    logger.info("Пользователь вышел")
    return redirect(url_for('login'))

@app.route('/')
@login_required
def home():
    return render_template('index.html')

@app.route('/health')
def health():
    return "OK", 200  # Health check endpoint для Heroku

@socketio.on('connect', namespace='/console')
def handle_connect():
    if not session.get('logged_in'):
        logger.warning("Попытка подключения к WebSocket без авторизации")
        return False  # Отклонить подключение
    logger.info("Клиент подключился к консоли")

@socketio.on('disconnect', namespace='/console')
def handle_disconnect():
    logger.info("Клиент отключился от консоли")

def run_flask():
    """Запуск Flask-сервера."""
    config = BotConfig()
    host = config.FLASK_HOST
    port = config.FLASK_PORT
    try:
        logger.info(f"Попытка запуска Flask на {host}:{port} в режиме {config.ENV}")
        if config.ENV == 'production':
            logger.info("Продакшен-режим: запуск через gunicorn/eventlet")
            # В продакшене gunicorn должен запускать приложение
            return  # Не запускаем socketio.run
        else:
            logger.info("Локальный режим: запуск через socketio.run")
            socketio.run(app, host=host, port=port, debug=True, use_reloader=False)
        logger.info(f"Flask-сервер успешно запущен на {host}:{port}")
    except Exception as e:
        logger.error(f"Ошибка запуска Flask: {e}")
        raise

if __name__ == '__main__':
    run_flask()