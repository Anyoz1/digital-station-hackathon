# Цифровая станция — backend v1.0

Учебный digital twin: 12 путей, 6 грузовых и 1 пассажирский поезд, 60 вагонов,
19 ресурсов, 33 операции. Реальный simulator, full-State SSE, independent
validator, FCFS и deterministic heuristic planner в отдельном процессе,
инциденты и validated automatic apply. Backend v1.0 завершён и заморожен;
[отчёт](docs/BACKEND_V1_REPORT.md), [freeze hashes](contracts/v1.0-freeze.json),
[честный benchmark](artifacts/benchmark/summary.md).

## Requirements и архитектура

Python3.12, uv, Docker Compose v2. PostgreSQL16, SQLAlchemy2/Psycopg3/Alembic,
FastAPI с одним station actor; PostgreSQL JSONB state/events/checkpoints/config/
plans/receipts; отдельный planner process. Kafka/Redis не нужны.
Node22.12+ и npm нужны только для frontend и JS/TypeScript tests.

## Quick start с нуля

В новой копии `cp .env.example .env`, задать локальные POSTGRES_PASSWORD,
DEMO_*_PASSWORD и тот же DB-пароль в DATABASE_URL/TEST_DATABASE_URL.
**Существующую .env не перезаписывать.** URI-символы пароля percent-encode в URL.
Секреты не коммитить/не передавать frontend. COOKIE_SECURE=false для HTTP demo.

```sh
uv sync --frozen --python 3.12
docker compose up -d --wait postgres
uv run alembic upgrade head
uv run alembic check
uv run python scripts/setup_backend.py
uv run uvicorn digital_station.main:app --host 0.0.0.0 --port 8000 --workers 1 --timeout-graceful-shutdown 5
```

Setup при остановленном API идемпотентен и не сбрасывает current run. Создаёт
users/config/main и approved manual-control-v1 с теми же12/7, одним ручным осмотром.
Таблицы создаёт Alembic, ручное исправление БД не нужно.

- Рабочее место: http://127.0.0.1:8000/tech/station
- Debug: http://127.0.0.1:8000/tech/debug
- Swagger: http://127.0.0.1:8000/docs; JSON: /openapi.json
- Health: /health/live и /health/ready

Логины viewer/operator/dispatcher/admin, пароли — соответствующие локальные
DEMO_*_PASSWORD. RBAC не равен профессиям железной дороги; dispatcher отображается
как «Дежурный по станции». `/tech/station` — reference/резервное demo того же API,
не отдельный клиентский simulator и не замена основному frontend друга.

БД только127.0.0.1:55432. `docker compose stop/restart` сохраняет volume;
не использовать `down -v`. Только один API worker, защищён advisory lock.
Restart сохраняет физическое состояние/history/config, ставит PAUSED; downtime
не увеличивает sim-time. Startup validated replan может заменить active_plan_id.

## Demo

Admin → новый main/seed42 →10× → Пуск. T2 прибывает R3, осмотр, манёвр R3→H→C1.
Околоsim720 P1 ждёт западную горловину, занятую начатым манёвром T2. Опоздание
P1 +5 учебных минут →conflicts→2 validated alternatives→autoapply/diff.
Actual KPI из State, forecast в планах; история→replay→LIVE→CSV.
Manual: admin выбирает manual-control-v1, operator подтверждает только собственный
осмотр после backend can_complete; движение завершает только engine.

## Frontend integration

Начать с [BACKEND_HANDOFF.md](docs/BACKEND_HANDOFF.md).
API base /api/v1, relative fetch/native EventSource/session cookie. Vite на машине
frontend проксирует /api на backend LAN IP с changeOrigin:true и SSE timeout0.
Точный frontend Origin добавить в backend ALLOWED_ORIGINS и перезапустить API.
Это CSRF allowlist, не CORS; не переходить на absolute cross-origin API.

```sh
cd integration/lan-probe
npm ci
BACKEND_URL=http://BACKEND_LAN_IP:8000 npm run start
```

На одном host BACKEND_URL можно не задавать. Основной frontend использует
[тот же proxy](integration/lan-probe/vite.config.mjs).
[Core TS](contracts/api-v1.ts), [все HTTP DTO/SSE](contracts/http-v1.ts),
[OpenAPI](contracts/openapi.json), [captured fixtures](fixtures/api/v1/live/manifest.json),
[static fixtures](fixtures/api/v1/manifest.json). Одноимённые типы двух TS modules
импортировать с aliases. Layout/labels/reference — [FRONTEND_HANDOFF](docs/FRONTEND_HANDOFF.md).

## Tests

```sh
uv run python scripts/create_test_database.py
uv run pytest -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src
npm ci --prefix integration/lan-probe
node tests/test_railway.mjs
node tests/test_render_telemetry.mjs
node tests/test_contract_ast.mjs
integration/lan-probe/node_modules/.bin/tsc --strict --noEmit --target ES2022 --moduleResolution bundler --module ESNext contracts/api-v1.ts contracts/http-v1.ts contracts/railway-display.ts
uv run python scripts/verify_backend_contract.py
```

TEST_DATABASE_URL обязан именовать отдельную digital_station_h0_test, не demo.
Pytest мигрирует её автоматически. Benchmark:
`uv run python scripts/benchmark_backend.py`, затем
`uv run python scripts/benchmark_live_timings.py` (отдельная добавочная DB).
Качество/offline compute и отдельные live timing имеют явно разные checkpoints.
Heuristic не везде лучше FCFS — результаты не скрыты.

## Ограничения

Синтетическая станция и учебные длительности/категории, не настоящие КТЖ нормы,
СЦБ или команды инфраструктуре. Feasible ≠ доказанно optimal. Нет внешних датчиков:
noisy observations — mock над настоящим engine, discrete State не сглаживается.
History durable/retention24h, replay15wall-мин; render metrics bounded/in-memory
и сбрасываются после restart/new-run. Foreground SLA не обещает скрытые вкладки.
Основной frontend/две физические машины ещё требуют совместного smoke.
Gemini/signals/новые пути/поезда/production scaling в v1.0 не добавляются.

## Project status

This repository contains the backend and technical demo of the Digital Station
hackathon prototype.

Implemented:
- real-time railway station simulator;
- station digital twin;
- conflict detection and independent plan validation;
- FCFS baseline and heuristic planner;
- automatic replanning after disruptions;
- alternative plan comparison;
- KPI / efficiency index;
- history and replay;
- CSV export;
- authentication and roles;
- PostgreSQL persistence;
- technical station UI at `/tech/station`.

The planned production-oriented frontend was not completed.

The team was unable to attend the final hackathon pitch due to university
coursework scheduled on the same day, so development was stopped after the
backend v1.0 milestone.

The current repository should be treated as a working technical prototype,
not a production railway control system.

> **Status:** Backend v1.0 / technical prototype. Development paused after the hackathon.
