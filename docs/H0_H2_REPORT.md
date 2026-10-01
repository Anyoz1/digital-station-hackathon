# Отчёт H0–H2 — фактическая реализация

Дата: 1 октября 2026 года. Выполнен только backend-этап H0–H2 и дополнительно
разрешённая пользователем простая страница `/tech/smoke`. H2–H5 и последующие
этапы **не запускались**. PostgreSQL и FastAPI оставлены работающими для ручной
проверки. Этот отчёт не изменяет принятую проектную основу.

## 1. Зафиксированная v1.0

- Документы зафиксированы коммитом `2764a83`, локальным тегом `spec-v1.0`.
  Контрольные суммы четырёх документов — [V1.0.md](V1.0.md).
  Итоговый `git diff spec-v1.0 -- docs/SPEC.md docs/RESEARCH.md
  docs/PLAN_24H.md docs/FRONTEND_HANDOFF.md` пуст; тест проверяет все четыре SHA-256.
- Повторно полностью прочитана [расшифровка ментора](../hackathon_docs/mentor_hackathon_transcript.md).
  Конкретного примера «отправить сейчас или накопить вагоны» в ней нет.
  В [RESEARCH §7](RESEARCH.md#7-экономический-компромисс-и-приём-назначением-атрибуция-полезность-и-стоимость)
  пример отнесён к постановке пользователя; ментору приписывается только общий
  экономический компромисс из фрагмента3. Контекст следующей станции есть во
  фрагменте8, конкретная проверка её возможности приёма — также пользовательская постановка.
- `demo_main_v1` не расширен: **12 путей и 7 поездов**, из них 6 FREIGHT и
  1 PASSENGER. 60 грузовых вагонов, 19 ресурсов и 33 начальные pending-операции.
  SERVICE/OTHER поддерживаются в DTO/API enum, но новых поездов в основное демо
  не добавлено. P1 — неделимый состав без WagonGroup/CargoOperation; его профиль
  содержит только arrival/departure. Это наши прикладные категории, не классификация КТЖ.
- Код H0–H2 находится в рабочей копии; в коммит v1.0 входит документация,
  а не последующая реализация. Публикация в удалённый репозиторий не выполнялась.

## 2. Что действительно работает

Python3.12 + FastAPI + SQLAlchemy2 async/Psycopg3 + PostgreSQL16 + Alembic.
Зависимости зафиксированы в `uv.lock`. Единственный отдельный сервис — PostgreSQL;
Redis/Kafka/TimescaleDB/Kubernetes не добавлены. API работает одним worker.

| API | Фактическое поведение |
|---|---|
| GET `/health/live` | `200`, `alive` |
| GET `/health/ready` | Проверка БД, migration revision и начального сохранённого State; `200 ready`, stage `H0-H2` |
| POST `/api/v1/auth/login` | Настоящая проверка Argon2id password hash; session token в HttpOnly/SameSite=Lax cookie, в БД только hash token |
| GET `/api/v1/auth/me` | Пользователь, роль и привязанные ресурсы из PostgreSQL |
| POST `/api/v1/auth/logout` | Отзыв сессии в БД и удаление cookie; истёкшая/отозванная сессия больше не даёт snapshot |
| GET `/api/v1/snapshot` | Аутентифицированное чтение сохранённого State из JSONB, без чтения fixture-файлов |
| GET `/api/v1/config` | Сохранённая начальная конфигурация, только чтение |
| GET `/api/v1/scenarios` | Описание единственного начального сценария |
| POST `/api/v1/simulation/control` | **Только зарезервированный role guard**: viewer/operator → `403 FORBIDDEN`, dispatcher/admin → `503 SERVICE_NOT_READY`. Никакого исполнения, receipts или изменения State |
| `/tech/smoke`, `/docs`, `/openapi.json` | Реальный smoke-клиент, Swagger и текущая серверная схема |

Ошибки API унифицированы как `{error:{code,message,details,request_id}}`.
Проверка Origin применяется к изменяющим JSON-запросам. Учётные записи:
viewer/operator/dispatcher/admin; пароли находятся только в локальной `.env`,
не в fixture или репозитории. `.env` игнорируется Git и имеет права0600.
Сессии живут12часов; смена env-пароля при startup отзывает старые сессии.
Это локальная hackathon-аутентификация, не заявленная промышленная защита.

`ready` означает готовность **фундамента H0–H2**, не всего будущего продукта:
capabilities `auth=true`, `snapshot=true`, `simulation=false`, `sse=false`,
`optimization=false`. `server_time` — wall UTC transport timestamp; `sim_time_s`
остаётся0. Обновление страницы не запускает симуляцию и не изменяет доменные версии.

### PostgreSQL и миграции

- `docker compose up -d --wait postgres`: контейнер
  `alt-digital-station-postgres-1`, image `postgres:16-alpine`, состояние **healthy**.
- До первой миграции у основной БД было0 таблиц в public. `alembic upgrade head`
  успешно выполнен на этой чистой БД. `create_all` в startup отсутствует.
- Финальные проверки: `alembic current` → `0001_h0_foundation (head)`;
  `alembic check` → `No new upgrade operations detected.`
- Созданы8 таблиц фундамента: `app_user`, `auth_session`, `scenario`,
  `config_revision`, `run`, `run_state`, `domain_event`, `state_snapshot`.
  Вместе с `alembic_version` —9 public tables. State/event/checkpoint/config
  сохраняются с JSONB payload там, где это предусмотрено миграцией.
- В основной БД фактически4 пользователя,1 run,1 current State,1 событие
  `run_initialized` и1 начальный checkpoint. Это **не** работающий event engine
  или replay: пока есть только транзакционная инициализация.
- Run `run-cda48aa1-eb33-486e-96a0-b22b33eeeae8` сохранён после настоящего
  перезапуска FastAPI. `event_seq/state_version/input_revision/config_version=1`,
  `mode=paused`, `sim_time_s=0`.
- Данные PostgreSQL находятся в volume `alt-digital-station_postgres_data`;
  порт БД открыт только на `127.0.0.1:55432`. Тестовая БД
  `digital_station_h0_test` отдельная. Существующие данные не удалялись.

## 3. Приёмка H2 и fixtures

| Критерий H2 / дополнение пользователя | Результат и проверка |
|---|---|
| v1.0 моделей, ошибок, ID/enum/time units зафиксирована | DTO и импортируемый `contracts/api-v1.ts` согласованы с замороженным handoff; strict `tsc --noEmit` проходит. Времена симуляции — секунды, wall timestamps — ISO UTC, длины — метры |
| Initial и короткие normal/incident fixtures | Созданы11 JSON-файлов:10 артефактов и SHA-manifest. DTO/JSON Schema, ссылки, DAG, реестр60 wagon IDs и контрольные суммы проходят тесты |
| Чистая PostgreSQL БД мигрирует | Начальная миграция успешно применена; текущая revision=head; ORM diff отсутствует |
| Login/session/roles доступны | Настоящие DB users/session hashes, четыре роли, me/logout/revoke/expiry; pytest и browser login/logout/relogin |
| Snapshot доступен и имеет7 поездов/12 путей | Ответ из PostgreSQL, валидируется State DTO; тест меняет поле только в test DB и доказывает DB-source; после restart сохранён |
| Минимальный smoke-интерфейс без бизнес-логики | Health/ready, login/logout/me, роль, snapshot, версии/time/mode, таблицы12/7/19, raw JSON, читаемые HTTP/API errors; проверено Chromium |
| Mock-режим не выдаётся за готовый runtime | Manifest маркирует static fixtures; UI явно говорит, что simulator/SSE/planner отсутствуют; guard не исполняет play |
| Общее основание для frontend друга | Типы, samples и OpenAPI подготовлены и проверены. **Работающий frontend друга и соединение двух машин не проверялись** — это не часть выполненного backend-этапа |

`fixtures/api/v1/`: `snapshot.initial.json`, `stream.normal.json`,
`stream.incident.json`, `auth.{viewer,operator,dispatcher,admin}.json`,
`config.json`, `errors.json`, `state.schema.json`, `manifest.json`.

Последовательности normal/incident — **статические mock-примеры** для независимой
разработки frontend, не результат работающего симулятора и не записанный live SSE.
Manifest: `source=synthetic_static_fixture`, `runtime_transitions_implemented=false`,
`solver_validated=false`, `performance_measured=false`. Они не содержат выдуманных
успешных планов или цифр улучшения. DTO/структурная проверка не заменяет будущую
независимую физическую валидацию плана.

`contracts/api-v1.ts` описывает **принятую будущую v1.0**, включая ещё не выполненные
возможности; `contracts/openapi.json` описывает **только реальные endpoint-ы H0–H2**.
При генерации TypeScript не включён пример Vite-конфигурации из handoff.

## 4. Что проверено автоматически и в браузере

### Pytest/API и статические проверки

Финальный полный прогон: **33 passed, 1 warning in 11.33s**, exit0.
Все API-тесты подключаются к отдельной PostgreSQL test DB через реальную
Alembic migration; SQLite/mocked DB не используются.

- 21 API case: health/assets/auth required, неверный login, все четыре роли,
  cookie attributes, me/snapshot, logout/revoke/relogin, expiry, guard403/503
  без изменения State, ошибки422/404, Origin guard, config/scenarios,
  persisted state/session после нового app startup, JSONB/migration revision,
  доказательство чтения State из БД и OpenAPI enums/schema.
- 12 contract/fixture cases: размер основного сценария, pending operations,
  passenger fixed consist, все четыре Train.type без обязательных грузовых
  групп, некорректные fixed/group DTO, уникальность wagon IDs, fixture manifest,
  ссылки/DAG/маршрут через H, JSON Schema, TypeScript и SHA-256 v1.0.
- `ruff check src scripts migrations tests` → `All checks passed!`;
  `ruff format --check ...` → `18 files already formatted`.
- TypeScript: `tsc --noEmit --strict --target ES2022 --lib ES2022,DOM
  contracts/api-v1.ts` → exit0. Vite/React-приложение друга не собиралось.
- Единственный warning: Starlette TestClient сообщает deprecation использования
  httpx вместо httpx2. Ошибок тестов нет; зависимости не менялись ради подавления warning.

### Playwright MCP / настоящий Chromium

Использован уже настроенный Chromium **151.0.7922.137**, не только curl/TestClient.
Выполнены реальные действия на `http://127.0.0.1:8000/tech/smoke`:

1. Открыть страницу, проверить `live=alive`, `ready=ready`.
2. Войти dispatcher; увидеть роль и ID пользователя.
3. Нажать загрузку snapshot; проверить12 путей,7 поездов,19 ресурсов,
   совпадение raw JSON и DOM, пассажирский P1, paused/time/versions.
4. Визуально просмотреть полноэкранный screenshot: таблицы читаемы, данные
   приходят с backend, username/password не подменяют состояние станции.
5. Logout, ошибочный login → читаемый HTTP401, успешный повторный login/snapshot.
6. Войти viewer; отправить настоящий HTTP-запрос к зарезервированному
   `/simulation/control` →403 FORBIDDEN. Это проверка role guard, **не управления
   симуляцией**. Run/версии/время остаются прежними.
7. Снова dispatcher; после настоящего restart API повторить login/snapshot,
   убедиться в сохранении run. Дополнительные schema/scenario поля видны в UI.

Неожиданных `pageerror` и `requestfailed` нет. Обычный пользовательский путь
и финальная чистая загрузка — **0 console errors, 0 warnings**, ответы200/204.
Отрицательные проверки намеренно дали401 и403: соответствующие два сообщения
Chromium `Failed to load resource` сохранены отдельно, а не названы ошибкой приложения
или скрыты под утверждением «вообще никаких console errors».

Артефакты: [screenshot](../artifacts/h0-h2/smoke-chromium.png),
[browser results](../artifacts/h0-h2/browser-results.json),
[чистая console](../artifacts/h0-h2/browser-console-clean.log),
[чистая network](../artifacts/h0-h2/browser-network-clean.log),
[console с отрицательными проверками](../artifacts/h0-h2/browser-console.log),
[network с отрицательными проверками](../artifacts/h0-h2/browser-network.log).
Пароли и session cookies в артефактах не сохранены.

## 5. Фактическая структура репозитория

Ниже созданные файлы; существующие `hackathon_docs/` сохранены, виртуальная среда,
кэши и `.git` не развёрнуты. `.env` и browser logs локальны/игнорируются Git.

```text
ALT-hackathon/
├── README.md
├── .gitignore
├── .python-version
├── .env.example
├── .env                         # локальные секреты, ignored
├── pyproject.toml
├── uv.lock
├── compose.yaml
├── alembic.ini
├── hackathon_docs/              # исходные материалы, без изменений
├── docs/
│   ├── SPEC.md
│   ├── RESEARCH.md
│   ├── PLAN_24H.md
│   ├── FRONTEND_HANDOFF.md
│   ├── V1.0.md
│   └── H0_H2_REPORT.md
├── src/digital_station/
│   ├── __init__.py
│   ├── main.py
│   ├── settings.py
│   ├── contracts.py
│   ├── db.py
│   ├── auth.py
│   ├── bootstrap.py
│   ├── scenario.py
│   ├── fixtures.py
│   ├── api.py
│   └── static/
│       ├── smoke.html
│       ├── smoke.js
│       └── smoke.css
├── migrations/
│   ├── env.py
│   └── versions/0001_h0_foundation.py
├── scripts/
│   ├── create_test_database.py
│   ├── generate_fixtures.py
│   └── export_openapi.py
├── contracts/
│   ├── api-v1.ts
│   └── openapi.json
├── fixtures/api/v1/             # 11 JSON-файлов, перечислены выше
├── tests/
│   ├── conftest.py
│   ├── test_api.py
│   └── test_contracts_fixtures.py
└── artifacts/h0-h2/
    ├── README.md
    ├── smoke-chromium.png
    ├── browser-results.json
    ├── browser-snapshot.yml
    ├── browser-console.log
    ├── browser-network.log
    ├── browser-console-clean.log
    └── browser-network-clean.log
```

## 6. Mock / not implemented — полная граница текущего этапа

- **Mock/data-only:** topology, schedules, operation durations, group composition,
  ресурсы и начальный сценарий синтетические; initial State сохранён в БД, но
  ещё не исполняется. Normal/incident streams — статические samples.
- **Нет симулятора:** station actor, queue, ticks, clock advancement, физическое
  исполнение движений/осмотров/манёвров/грузовых работ/formation, runtime wagon
  conservation/resource locks, исполнение DAG, play/pause/speed/reset отсутствуют.
- **Нет realtime:** SSE, event stream, reconnect/reset, heartbeat/stale/offline,
  измерения1Гц/<500мс и browser receive→render ещё не реализованы. Smoke — ручной GET.
- **Нет планировщика:** FCFS, альтернативы, rollout/optimizer worker, независимый
  physical plan validator, conflict detection, replan/apply/CAS, timeout/no-feasible,
  объяснение изменений, KPI/index и SLA≤5с отсутствуют. Type-independent optimizer
  пока только требование принятого ТЗ; он вообще не реализован.
- **Нет incident API:** создание/закрытие инцидентов и burst5/10 отсутствуют;
  incident sample — fixture, а не реальный runtime effect.
- **Нет истории/replay/export:** один initial event/checkpoint — не15минут replay;
  history endpoints, retention, CSV, audit API, telemetry normalizer, metrics
  endpoint и сравнительный runner отсутствуют.
- **Неполное persistence/access scope:** отдельные таблицы incidents/plans/
  plan alternatives/optimization runs/command receipts ещё не созданы;
  readonly config работает, изменение config/admin UI и полная матрица прав
  будущих команд отсутствуют. В operator пока привязка к I1, а не ручное
  подтверждение технологической операции. Соседняя станция/её календарь приёма
  не исполняются. Полноценный auth/admin продукт, HTTPS deployment и rate limiting
  не заявляются выполненными.
- **Нет основного frontend:** схема digital twin, Gantt, KPI, full `/tech`,
  simulation controls, event log, incidents, replan/apply, replay UI отсутствуют.
  Не созданы будущие replan/history/reconnect/manual/SERVICE fixtures.
  LAN-интеграция с машиной друга не выполнена; инструкции подключения есть в README/handoff.

## 7. Как проверить вручную и точка остановки

Открыть **http://127.0.0.1:8000/tech/smoke**. Пользователь `dispatcher`,
пароль — `DEMO_DISPATCHER_PASSWORD` в локальной `.env`. Нажать «Войти», затем
«Загрузить snapshot»; проверить роль, таблицы и raw JSON. Для viewer использовать
его отдельный env-пароль. Swagger: http://127.0.0.1:8000/docs.

Запуск, тесты, генерация fixtures и proxy для двух машин — в [README](../README.md).
PostgreSQL/FastAPI оставлены запущенными. **Работа остановлена после H0–H2;
переход к H2–H5 возможен только по следующей команде пользователя.**
