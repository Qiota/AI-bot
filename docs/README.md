# Ai-бот

Ai-бот — это Discord-бот с открытым исходным кодом, предназначенный для выполнения различных задач в Discord-серверах и личных сообщениях. Код бота частично сгенерирован с помощью искусственного интеллекта, но был доработан человеком для обеспечения большей надёжности и функциональности.

## Установка и настройка

Для работы бота необходимо настроить переменные окружения в файле `.env`. Создайте файл `.env` в корне проекта и добавьте следующие параметры:

```plaintext
DISCORD_TOKEN=ваш_токен_discord_бота
DEVELOPER_ID=ваш_id_разработчика
FLASK_PORT=порт_для_flask_сервера
G_SEARCH_KEY=ключ_для_google_search_api
G_CSE=идентификатор_поисковой_системы_google
FIREBASE_CREDENTIALS=ваш_json_ключ_firebase
FIREBASE_DATABASE_URL=ваш_url_firebase_database
FIREBASE_CREDENTIALS_URL=опциональный_url_для_загрузки_ключа
FIREBASE_CREDENTIALS_HEADERS=опциональные_заголовки_для_запроса_ключа
```

### Описание параметров

- `DISCORD_TOKEN`: Токен вашего Discord-бота, полученный через [Discord Developer Portal](https://discord.com/developers/applications).
- `DEVELOPER_ID`: Ваш Discord ID (для административных функций).
- `FLASK_PORT`: Порт для запуска Flask-сервера (например, `5000`).
- `G_SEARCH_KEY`: API-ключ для Google Custom Search (для поиска).
- `G_CSE`: Идентификатор поисковой системы Google Custom Search.
- `FIREBASE_CREDENTIALS`: JSON-объект с учетными данными Firebase для работы с базой данных (в формате `{"type": "service_account", ...}`).
- `FIREBASE_DATABASE_URL`: URL вашей Firebase Realtime Database (например, `https://your-project-id.firebaseio.com/`).
- `FIREBASE_CREDENTIALS_URL`: Опциональный URL для загрузки учетных данных Firebase через HTTP (например, хранилище секретов).
- `FIREBASE_CREDENTIALS_HEADERS`: Опциональные HTTP-заголовки в формате JSON для запроса учетных данных (например, `{"Authorization": "Bearer token"}`).

## Установка зависимостей

Убедитесь, что у вас установлен Python 3.8 или выше. Установите зависимости, выполнив:

```bash
pip install -r requirements.txt
```

## Запуск бота

Запустите бота с помощью команды:

```bash
python start.py
```

## Условия использования

Пожалуйста, ознакомьтесь с условиями использования в файле [TERMS.md](TERMS.md) перед использованием бота или его исходного кода.
