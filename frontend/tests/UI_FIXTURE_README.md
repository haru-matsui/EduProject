# QA-only real DOM fixture

**QA fixture authentication; synthetic users; not real Telegram/team acceptance.**

Это отдельный локальный стенд для проверки настоящих HTML/JS-форм и бизнес-API, когда внешний TLS-туннель нестабилен. Он не подтверждает US-01, настоящий Telegram JWT, работу PostgreSQL, командную приёмку или production-готовность. Обычный OIDC и production-auth не изменяются.

Из корня `EduProject_App`, используя уже установленный backend venv:

```powershell
backend/.venv/Scripts/python.exe frontend/tests/ui_fixture_server.py --check
backend/.venv/Scripts/python.exe -u frontend/tests/ui_fixture_server.py
```

`--check` проверяет расположение и свободные loopback-порты, не импортирует приложение, не создаёт БД/личности/сессии и не запускает серверы. Полный запуск создаёт новый каталог `qa/ui-fixture-<unique>/` и новую SQLite БД; старые каталоги и базы не удаляются. Alembic применяется только к этой новой БД. Production `.env`, пользовательские cookies/секреты, Docker и существующая PostgreSQL БД не читаются и не изменяются. Если обнаружен `backend/.env`, harness отказывается от импорта вместо чтения файла. Root `.env` не используется.

Один собственный процесс запускает два экземпляра существующей фабрики приложения с `app_env=test`, `cookie_secure=false`, явными SQLite/origin/frontend-настройками и `_env_file=None`:

- [Lead fixture](http://127.0.0.1:8011/_qa/enter): синтетический «Тестовый тимлид (QA)».
- [Student fixture](http://localhost:8012/_qa/enter): синтетический «Тестовый студент (QA)».

Оба сервера привязаны исключительно к `127.0.0.1`. Разные **hostname**, а не только порты, изолируют host-only cookies. Только QA-экземпляры добавляют `GET /_qa/enter`: он выдаёт обычную серверную HttpOnly/SameSite=Lax cookie через существующий `issue_session` и перенаправляет на `/projects`. Telegram ID у обеих синтетических записей отсутствует; issuer — `https://qa.example.invalid`. Пользователи не являются реальными людьми.

Все ответы отмечены `X-EduProject-QA-Fixture`; страницы получают видимый QA-баннер без изменения исходного frontend. CSRF/Origin, membership, роли, ETag и бизнес-переходы остаются штатными. Uvicorn access logs отключены, чтобы не писать ссылки приглашений. stdout содержит только URL, собственный PID и путь новой БД; эти же безопасные сведения записываются в `fixture.json`. Сырые cookie/session/CSRF-токены не выводятся.

Для UI-сценария сначала откройте обе bootstrap-ссылки, затем создайте **явно тестовый** проект и задачу в lead-окне. При создании приглашения ссылка будет иметь lead-origin. Для приёмки студентом перенесите только её путь `/invitations/<token>` в student-origin `http://localhost:8012`; не открывайте lead-host в student-сценарии, иначе браузер использует lead-cookie. Сам токен не публикуйте в отчётах/логах.

Проверьте создание, приглашение и повторное принятие, назначение/снятие, начало текущим исполнителем, конфликт старого ETag, валидацию полей и deadline flags. Это свидетельства **synthetic local UI QA**, а не настоящего Telegram E2E. Учётки и БД fixture нельзя смешивать с данными внешнего стенда.

Остановка: Ctrl+C в том процессе, который запускал harness; общий handler завершает оба сервера. PID сохранён в `qa/ui-fixture-<unique>/fixture.json`. Никакие чужие процессы harness не останавливает; файлы QA остаются для последующей проверки. `frontend/tests` исключён из Docker context и не копируется в production image.
