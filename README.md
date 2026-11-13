# RIPE DB Audit

Фронтенд и API для проверки ресурсов RIPE по regID. Введите `ru.ecode`:
система собирает allocations, дочерние IPv4/IPv6, собственные и спонсируемые
ASN, BGP-анонсы и объяснимые замечания.

## Быстрый запуск

Нужны Python 3.11+ и Node.js 22.12+ (проверено на Python 3.14, Node.js 24).

```sh
git clone https://github.com/mraliscoder/ripe-db-audit.git
cd ripe-db-audit
./start.sh
```

Откройте **http://127.0.0.1:8090**. Приложение выполняет только анализ и не
создаёт задачи, не отправляет письма и не изменяет записи RIPE.

Ручной запуск:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8090
```

Для разработки: `npm --prefix frontend run dev` (Vite проксирует `/api` на 8090).
В собранном варианте фронтенд и API обслуживает один FastAPI-сервер.

Интерфейс: React + TypeScript, Tailwind CSS и shadcn/ui (Radix, neutral).
Все элементы интерфейса и сообщения анализа — на английском.

## Что реализовано

- Поиск regID через официальный `alloclist.txt` RIPE NCC.
- Точная загрузка корневых объектов, обратный поиск `org`/`sponsoring-org`,
  все дочерние inetnum/inet6num, ORG и собственные/спонсируемые aut-num.
- Рекурсивный сбор RIPE RIS без усечения на 50 more-specifics; единый момент
  снимка, анонсы агрегатов, более специфичные и покрывающие маршруты.
- IPv4-диапазоны с не-CIDR границами сохраняются как настоящие inetnum.
- Правила IPv6 из прежнего аудита и ASN-активность за три календарных месяца.
- IPv4 на основании [RIPE-826](https://www.ripe.net/publications/docs/ripe-826/):
  статусы PA/PI, допустимые субаллокации, вложенные назначения, учёт
  `ALLOCATED-ASSIGNED PA`, необязательный IPv4 `assignment-size` у агрегатов.
- Отсутствие IPv4-назначения, предполагаемое по BGP, помечается предупреждением
  по правилу аудита. Политика, схема БД и внутренние правила имеют разные метки.
- Ресурсы с серверной пагинацией, дерево с подгрузкой веток и замечания.
  Карточки ресурсов, правила и источники открываются в боковом окне; экспорт CSV.
- Progress SSE с Event ID, heartbeat и восстановлением; резервный опрос статуса.
  Перезагрузка страницы продолжает проверку, серверная задача не прерывается.
- SQLite: задачи, события, снимки и кэш на **3600 секунд от завершения**.
  Повторный запрос в течение часа возвращает тот же результат. Одновременные
  запросы одного regID объединяются. Сбой/неполный результат не кэшируются как успех.
- Опциональное подписанное уведомление внешнему сервису о завершении.

## Границы публичного анализа

Один regID не даёт доступ к закрытому LIR Portal. Публичный режим использует
официальный каталог allocations и связи ORG в RIPE Database. Общая ORG может
использоваться несколькими LIR; основание связи каждого ресурса показывается.
Каталог RIPE обновляется отдельно от базы, поэтому новые transfers могут
появляться с задержкой. Ошибки источников явно дают `partial`, а не «всё чисто».

Лимит `/24` из §5.1 RIPE-826 относится к новым выдачам из пула RIPE NCC,
а не к любым существующим allocations. Приложение не считает исторический
большой allocation нарушением только из-за длины префикса. Контракты, цель
назначений, статистика агрегатов и применимость истории выдач требуют
ручной проверки; это видно в UI.

## Точный список LIR Portal (опционально)

Ключи [My Resources API](https://www.ripe.net/publications/documentation/developer-documentation/lir-portal-api-keys/)
задаются **только на сервере**:

```sh
export RIPE_API_KEYS_JSON='{"ru.ecode":"YOUR_MY_RESOURCES_KEY"}'
export AUDIT_ACCESS_TOKEN='YOUR_LONG_RANDOM_INSTANCE_ACCESS_TOKEN'
./start.sh
```

При наличии Portal-ключей токен доступа обязателен: приватная инвентаризация
не становится доступна всем посетителям. В UI появится вход по токену.
Сессия — подписанная HttpOnly SameSite=Strict cookie на восемь часов;
токены не попадают в URL, localStorage или результаты. Проверяется соответствие
regID ключу. Ключи хранятся в окружении, не в SQLite.

Этот режим реализован, но живой запрос к закрытому API не выполнялся:
в рабочей сессии нет ключа My Resources. Публичный `ru.ecode` проверен целиком.

## API и прогресс

```text
POST /api/analyses                         {"regid":"ru.ecode"}
GET  /api/analyses                         последние 20 проверок
GET  /api/analyses/{id}                    состояние и текущий этап
GET  /api/analyses/{id}/events              SSE; Last-Event-ID или ?after=N
GET  /api/analyses/{id}/result              метаданные и сводка
GET  /api/analyses/{id}/result?full=true    полный JSON
GET  /api/analyses/{id}/resources           family,q,org,bad,parent,offset,limit
GET  /api/analyses/{id}/resources/{id}      карточка, замечания и анонсы
GET  /api/analyses/{id}/findings            family,q,offset,limit
GET  /api/analyses/{id}/export.csv          CSV замечаний
GET  /api/health
```

OpenAPI: **http://127.0.0.1:8090/docs**. Не передавайте токены в URL.
Если включён доступ по токену, API также принимает `Authorization: Bearer …`.

Новые задачи отвечают `202`; `cache_hit` сообщает о повторном использовании.
Статусы: queued, running, complete, partial, failed, interrupted. После
перезапуска незавершённая задача становится interrupted, готовый кэш сохраняется.
Каждое событие содержит stage, message, done/total (если известны), время,
число объектов и запросов. Прогресс относится к текущему этапу, а не к
придуманному проценту всей работы. Для nginx отключите buffering на SSE
и задайте read timeout выше интервала heartbeat; ответ также содержит
`X-Accel-Buffering: no`.

## Webhook завершения (опционально)

Для фронтенда достаточно SSE. Webhook нужен только внешнему серверу:

```sh
export AUDIT_WEBHOOK_URL='https://receiver.example/audit-completed'
export AUDIT_WEBHOOK_SECRET='YOUR_LONG_RANDOM_SIGNING_SECRET'
```

URL задаёт администратор окружением; пользователи не могут отправить
произвольный callback URL. По умолчанию webhook выключен и ничего не отправляет.
Payload: event `analysis.finished`, стабильные event_id/job_id, regid, status,
completed_at, summary. Подпись: `X-ARC-Signature: sha256=<hex HMAC-SHA256>`
над точными байтами тела; `X-ARC-Event-ID` позволяет удалить дубли.
Максимум три попытки; получатель должен обеспечивать идемпотентность по event ID.
Сбой уведомления не инвалидирует результат и не запускает анализ повторно.

## Эксплуатация

Переменные: AUDIT_DB (по умолчанию `data/audit.sqlite3`), AUDIT_MAX_JOBS=2,
AUDIT_MAX_OBJECTS=20000, AUDIT_MAX_ROUTING_QUERIES=4096, AUDIT_JOB_TIMEOUT=1800.
Сервер и каталог `data/` сохраняйте между перезапусками. Час — срок повторного
использования результата; сохранённая локальная история остаётся в SQLite,
пока оператор не удалит БД. API возвращает последние 20 проверок.

Запускайте **один uvicorn worker**: очередь и объединение задач находятся
в памяти одного процесса, снимки и события — в SQLite/WAL. Для масштабирования
на несколько инстансов понадобится общая очередь/блокировки (например, Redis).
Локальный запуск привязан к 127.0.0.1. Для внешнего размещения настройте HTTPS,
доступ и лимиты на своём reverse proxy; секреты остаются на сервере.

## Docker

Из каталога репозитория:

```sh
docker compose up -d --build --wait
```

Откройте **http://127.0.0.1:8090**. Если этот порт занят локальным запуском,
используйте другой: `AUDIT_PORT=8092 docker compose up -d --build --wait`.
Сборка включает React/shadcn и API; Node.js и Python на хосте не нужны.
Контейнер работает от непривилегированного пользователя, с healthcheck и одним
worker. Файлы приложения доступны только для чтения; SQLite хранится отдельно.

Конфигурация необязательна: `cp .env.example .env`, затем задайте порт,
лимиты и при необходимости токен доступа. По умолчанию используются публичные
данные RIPE. Порт привязан к `127.0.0.1`; для доступа извне задайте
`AUDIT_BIND=0.0.0.0` и настройте HTTPS/reverse proxy. Секреты не входят в образ.

```sh
docker compose ps
docker compose logs -f analyzer
docker compose down
```

Именованный volume `analysis-data` сохраняет задания, события, результаты и
постоянные job ID между перезапусками и пересборками. Истечение часового кэша
не удаляет сохранённые результаты. Команда `docker compose down` сохраняет
volume; `docker compose down -v` удаляет его вместе с историей.

### Перенос существующих job ID из локального запуска

Новый Docker volume изначально пуст. Чтобы перенести текущую локальную базу,
сначала создайте согласованную копию SQLite (она учитывает WAL):

```sh
python3 - <<'PYBACKUP'
import sqlite3
with sqlite3.connect('data/audit.sqlite3') as source:
    with sqlite3.connect('/tmp/ripe-db-audit.sqlite3') as target:
        source.backup(target)
PYBACKUP
```

Импортируйте её **до первого запуска Docker**, в пустой volume:

```sh
docker compose build
docker compose run --rm --no-deps -T analyzer python -c 'import sys; from pathlib import Path; p = Path("/app/data/audit.sqlite3"); p.exists() and sys.exit("Database already exists; import cancelled"); p.write_bytes(sys.stdin.buffer.read())' < /tmp/ripe-db-audit.sqlite3
docker compose up -d --wait
```

Сохранённые ссылки продолжат работать на том же домене. Импорт не перезаписывает
существующую базу контейнера. Завершённые задания сохраняются; незавершённые
после перезапуска требуют повторного анализа.

## Проверки

```sh
.venv/bin/python -m pytest backend/tests -q
npm --prefix frontend run build
node scripts/verify_ui.mjs
python3 scripts/verify_docker.py
```

UI-тест использует изолированный тестовый API на 8091 и headless Chrome.
Он воспроизводит сохранённый публичный результат из backend/tests/fixtures/, а не вызывает RIPE повторно; проверяет
загрузку, SSE, кэш, таблицы, дерево, поиск, ошибки/неполноту, опрос при обрыве,
клавиатуру, мобильную ширину и axe. На другой ОС задайте CHROME_PATH.
Артефакты: `artifacts/ui-report.json`, accessibility.json и PNG.
Строгая проверка дизайн-контракта: `premium-audit.json`.

Docker-проверка использует отдельный временный Compose-проект и volume,
проверяет healthcheck, собранный UI, SSE и доступ к прежнему job ID после
пересоздания контейнера. Она не вызывает RIPE и не меняет рабочую базу.

## GitHub releases and GHCR

Repository: https://github.com/mraliscoder/ripe-db-audit

Checks run on pushes to `main` and pull requests. A version tag runs the same
checks, then publishes the Docker image for `linux/amd64` and `linux/arm64` to
`ghcr.io/mraliscoder/ripe-db-audit` and creates a GitHub release. Official Actions
are pinned to immutable commits. Authentication uses the built-in `GITHUB_TOKEN`;
no personal access token or Docker Hub credentials are required.

Create a release after merging the code:

```sh
git tag v1.0.0
git push origin v1.0.0
```

Stable releases publish version, minor, major (except `0`) and `latest` image
tags. Prereleases such as `v1.1.0-rc.1` publish only their exact version tag and
never replace `latest`. The workflow can also be rerun manually for an existing
version tag. Existing GitHub release notes are preserved.

To run a published image using Compose:

```sh
export AUDIT_IMAGE=ghcr.io/mraliscoder/ripe-db-audit:latest
docker compose pull
docker compose up -d --no-build --wait
```

For reproducible deployments, choose a version tag or the immutable image digest
from the release notes. The data volume and permanent job IDs are unchanged.
If anonymous image pulls are needed, set the package visibility to Public in
GitHub Packages after its first publication.
