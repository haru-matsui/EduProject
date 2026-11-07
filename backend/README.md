# Backend EduProject, Sprint 1

Модульный FastAPI + SQLAlchemy 2 + PostgreSQL. Реализованы проекты, локальные роли, приглашение студента на семь суток, повторное принятие без дубликатов/повышения роли, задачи, назначение/снятие исполнителя и начало выполнения. API: `/api/v1`; актуальная исполняемая спецификация: `/api/openapi.json`.

US-12 реализована срезом **без доставки Telegram-уведомлений**. Проверка/сдача результатов и поздние переходы статусов не реализованы. Дополнительные столбцы доски остаются пустыми, пока соответствующих результатов нет.

## Запуск

Из этой папки, Python 3.12:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

`DATABASE_URL` должен указывать на PostgreSQL: `postgresql+psycopg://USER:PASSWORD@HOST:5432/DB`. Пароль задаётся окружением/локальным `.env`; его нельзя сохранять в Git. Миграции применяются явно. Приложение не создаёт таблицы автоматически.

| Переменная | Назначение |
|---|---|
| `DATABASE_URL` | Подключение к PostgreSQL |
| `APP_ENV` | `production` по умолчанию; локально `development` |
| `PUBLIC_ORIGIN` | Полный origin без завершающего `/`, например `http://localhost:8000` локально |
| `COOKIE_SECURE` | По умолчанию `true`; `false` разрешено только для development/test |
| `SESSION_TTL_HOURS` | Время серверной сессии, по умолчанию 24 часа |
| `TELEGRAM_CLIENT_ID` | Client ID из BotFather |
| `TELEGRAM_CLIENT_SECRET` | Client Secret из BotFather |
| `FRONTEND_DIR` | Необязательный путь к frontend; по умолчанию `../frontend` |

В production обязательны HTTPS и Secure cookies. Callback вычисляется как `PUBLIC_ORIGIN + /api/v1/auth/telegram/callback`; этот точный HTTPS URL нужно зарегистрировать в BotFather. Реализован Authorization Code Flow с S256 PKCE, browser-bound одноразовым state, nonce и проверкой подписи RS256/JWKS, issuer, audience, iat, exp. Используются [официальные endpoints Telegram](https://core.telegram.org/bots/telegram-login). Отдельный UserInfo endpoint не вызывается.

Без Client ID/Secret `/api/v1/auth/telegram/start` возвращает ясный `503 auth_not_configured`. Публичный `/api/v1/auth/configuration` раскрывает только флаг готовности. Нет production/dev-login обхода; тестовые личности создаются исключительно в `tests/`.

На изменяющих запросах обязательны `Origin: PUBLIC_ORIGIN` и `X-CSRF-Token` из `/auth/session`. Назначение/начало задачи требуют `If-Match: "VERSION"`: отсутствие — 428, устаревшая версия — 412. Доступ к объектам проверяется через активное членство в конкретном проекте.

Не включайте access/query/body-логи для callback и ссылок приглашений; Uvicorn запускается с `--no-access-log`. Не включайте SQL echo или HTTPX debug. SQL-параметры скрыты; API ошибок не возвращает исходный ввод/секреты.

## Проверка

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-lock-dev.txt
.\.venv\Scripts\python.exe scripts\verify.py --postgres --require-postgres
```

Helper пытается создать **только** собственный `eduproject-test-db` (PostgreSQL 17) на `127.0.0.1:55432`, генерирует пароль в памяти и удаляет созданный им контейнер после проверки. Уже существующий контейнер с этим именем не меняется. Образ `postgres:17` при необходимости загружается Docker. Никакие службы или настройки Docker/Windows helper не меняет.

Если Docker Engine недоступен, независимые тесты выполняются; PostgreSQL-тесты отмечаются skipped, `--require-postgres` возвращает код 2. Артефакты без секретов: `qa/latest/result.json`, `verify.log`, `pytest.xml`. Прямой запуск `pytest` с `TEST_DATABASE_URL` тоже возможен: PG fixture использует отдельную случайную схему и удаляет только её.

Тесты проверяют подписанный fixture JWT и mocked JWKS/token endpoints. Они **не подтверждают** настоящий Telegram-вход. Он остаётся внешней приёмкой до появления бота, Client ID/Secret, зарегистрированного callback и двух реальных браузерных сессий.
