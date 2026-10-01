# Цифровая станция — H15 и railway UI reference

Реальный backend-симулятор и полный State по SSE поверх H0–H2. PostgreSQL16,
SQLAlchemy2 async/Psycopg3, Alembic, FastAPI, Python3.12. `demo_main_v1`:
**12 путей, 6 FREIGHT + 1 PASSENGER, 60 вагонов, 19 ресурсов, 33 операции**.
Работают independent validator, безопасный FCFS и ограниченный deterministic
multi-strategy planner в отдельном процессе, incidents/batch1..10 и automatic
validated apply с frozen prefix/barrier/CAS. Добавлен предметный `/tech/station`;
actual KPI/config/history/replay/CSV/manual/RBAC по последнему scope H15 реализованы.

Принятые документы: commit `2764a83`, tag `spec-v1.0`, [SHA-256](docs/V1.0.md).
Исходный текст четырёх файлов v1.0 сохранён, handoff/план получили явно отделённое
post-H12 дополнение. [Отчёт H0–H2](docs/H0_H2_REPORT.md) — исторический;
[отчёт H2–H5](docs/H2_H5_REPORT.md) — исторический;
[актуальная приёмка H5–H12](docs/H5_H12_REPORT.md) — реализация, замеры и ограничения.
[Предметная проверка](docs/RAILWAY_DOMAIN_REVIEW.md),
[приёмка railway UI](docs/RAILWAY_UI_REPORT.md) и новый раздел
[handoff](docs/FRONTEND_HANDOFF.md#дополнение-после-h12-railway-oriented-ui-reference).
[Приёмка H12–H15](docs/H12_H15_REPORT.md):217pytest, реальные API/Chromium/restart,
actual sample, CSV и manual DAG; остальные mandatory хвосты перечислены отдельно.
[Приложение P2 AI Explanation](docs/ADDENDUM_P2_AI_EXPLANATION.md) только описано:
никакого LLM/API key/provider dependency в runtime нет.

## Запуск и ручная проверка

```sh
cp .env.example .env
# В новой копии задать локальные POSTGRES_PASSWORD и DEMO_*_PASSWORD.
# DATABASE_URL / TEST_DATABASE_URL используют тот же пароль БД.
uv sync --frozen --python 3.12
docker compose up -d --wait postgres
uv run alembic upgrade head
uv run alembic current
uv run alembic check
uv run uvicorn digital_station.main:app --host 0.0.0.0 --port 8000 --workers 1 --timeout-graceful-shutdown 5
```

В текущей рабочей копии `.env` уже существует: **не перезаписывайте её** командой cp.
Пароль `dispatcher` — `DEMO_DISPATCHER_PASSWORD`, `viewer` — `DEMO_VIEWER_PASSWORD`.
Секреты игнорируются Git, `.env` имеет права0600. PostgreSQL/API оставлены запущенными.

Открыть **http://127.0.0.1:8000/tech/station** → login → snapshot/SSE подключаются
автоматически. Здесь схема всех12 путей, поездное положение, фазы манёвра,
цепочки операций, ресурсы, инциденты и два настоящих варианта с backend diff.
Добавлены actual5факторов, история15wall-мин/list/slider/ReturnLIVE, CSV,
folded config JSON (admin) и подтверждение назначенных manual service операций.
Все реальные positions/route_progress/occupancy приходят из State, не из анимации.
`/tech` и `/` ведут сюда. `/tech/debug` открывает прежний `/tech/smoke`:
login → snapshot/SSE подключаются
автоматически → Play → Pause / speed1/5/10. Таблицы показывают locations, occupancy,
locks, операции/progress/phase; raw JSON и event log раскрывают настоящие ответы API.
Никакой клиентской симуляции. Есть conflicts,
raw incident batch/resolve, replan/result/validator/forecast/diff и выбор свежей
альтернативы на паузе. Во время LIVE расчёт не останавливает clock/running prefix/SSE.

Новая БД начинает paused sim0; текущая БД сохраняет уже выполненный live-прогон.
При restart сохраняется тот же run/phase/groups/resources, clock не прибавляет
время простоя; recovery записывает новые seq/state/input revisions и ставит paused.
Нажать Play для продолжения. **Reset/new run API пока отсутствует** — не удаляйте
volume для обычного перезапуска. При смене env-пароля bootstrap обновляет hash
и отзывает старые сессии. Таблицы создаёт только Alembic, не create_all.

PostgreSQL: `127.0.0.1:55432`, volume `alt-digital-station_postgres_data`.
`docker compose stop` сохраняет данные. Не применять `down -v` для restart.
Один station writer защищён PostgreSQL advisory lock: только один API worker.
Redis/Kafka/TimescaleDB/Kubernetes не добавлены. `/tech/station` — небольшой
vanilla-JS reference/fallback, не замена основному frontend друга.

## Фактические API

Все пути `/auth`, `/snapshot` и т.д. ниже имеют префикс `/api/v1`.

| Endpoint | Реализация |
|---|---|
| GET `/health/live`, `/health/ready` | DB/schema/scenario/actor/worker readiness H15; simulation/SSE/optimization и H15 capabilities |
| POST `/auth/login`, `/auth/logout`; GET `/auth/me` | DB users, Argon2id, HttpOnly/SameSite=Lax cookie,12ч, revoke/expiry |
| GET `/snapshot` | Полный State v1.0 из PostgreSQL, authenticated |
| GET `/stream?after=run_id:seq` | Полные State, nominal1.25wall-Hz, immediate transitions, paused heartbeat без DB writes, catch-up/reset |
| POST `/simulation/control` | `{request_id,run_id,expected_input_revision,action,speed?}`; play/pause/step/set_speed. Dispatcher/admin200, viewer/operator403 |
| GET `/scenarios`, `/config` | Описание fixed demo и current run config, все роли |
| PATCH `/config` | Envelope + validated patch, только admin; version/event/receipt + automatic replan |
| GET `/history`, `/history/snapshot` | PostgreSQL pagination/window/anchor, exact seq readonly State; без повторного engine/planner |
| GET `/reports.csv` | Настоящий UTF-8 export run/window raw KPI/incidents/plans/timings, units/formula version |
| POST `/operations/{id}/complete` | Назначенный operator/admin, только готовая manual service; durable event/replan/continued DAG |
| POST `/incidents` | Envelope + items1..10; четыре kind из v1.0;201 receipt + automatic replan; dispatcher/admin |
| POST `/incidents/{id}/resolve` | Envelope;200 receipt; ETA после delay не отматывается; dispatcher/admin |
| POST `/replans` | Envelope + reason=manual;202 receipt; dispatcher/admin |
| GET `/replans/{id}` | Job и сохранённые PlanDetail/validator/explanations; authenticated |
| GET `/replans/{id}/explanation` | Read-only diff от сохранённого входа и задержки отправления; отдельный additive display endpoint, State v1.0 не меняет |
| POST `/plans/{id}/apply` | Envelope;200 только для свежей feasible sibling-альтернативы на паузе; dispatcher/admin |
| `/docs`, `/openapi.json` | Реальная текущая схема |

Команды имеют durable receipts: повтор одинакового user/request_id/body возвращает
тот же результат; иной body409. Step на паузе продвигает1sim-секунду атомарно с receipt;
в running возвращает409. speed требуется только для set_speed. UUID/schema bounds,
версии, Origin и роли проверяются сервером. Errors: `{error:{code,message,details,request_id}}`.

State/API schema v1.0 не переименована. Init создаёт настоящий validated active plan;
smoke timetable не выдаётся за certified optimization plan. Live actual индекс
рассчитан по recorded engine transitions; forecast пяти факторов/J/diff — отдельно
по проверенным rollout фактам. Пустое окно/нетactivity дают null.
БД содержит optimization_run, plan и incident; Alembic revision0005_history_indexes.

## Модель исполнения и сохранение

Topology/initial State/ledger/smoke schedule загружаются из scenario JSONB.
В runtime — три freight DAG и passenger arrival→departure, фиксированные исходные
группы, собственная тяга и crew. Семь фаз манёвра исполняют шесть связных route legs
через H; L1 каждый раз возвращается в D1. Resource/track/zone locks и home R reservation
не равны физической стоянке. У travelling объекта location=route и route_progress;
метры на track показывают находящиеся целиком на track объекты, не частичный track-section
footprint. Пока объект в route, маршрут/связанные пути остаются эксклюзивно locked.
Fixed120m не считается вторично как отдельный локомотив; отцеп не освобождает home R.

Actor serializes bounded command queue and monotonic clock. Важные старты/фазы/концы
будят actor по due deadline, не ждут регулярный wall-tick. Time speed не меняет частоту
SSE. PostgreSQL transaction содержит current State + compact effects/transitions
+ changed incidents/plans/jobs + receipt (для команды); SSE только после commit. Checkpoints без topology каждые
30wall-секунд при новом seq и на init/recovery. Paused heartbeat не создаёт events.
При DB/CAS/runtime failure actor останавливается, streams закрываются, ready/snapshot503.

Memory ring: до900wall-секунд, дополнительно ограничен32MiB wire payload; queue клиента100.
Slow client отключается, старый/gapped cursor получает reset. Это live recovery,
не substitute durable history: отдельные H15 history/snapshot endpoints и reference
replay используют PostgreSQL checkpoints/effects. Retention24h оставляет anchor chain
и защищает sim KPI-anchor открытого paused run; read-only CSV/reduction не блокируют actor.

## Тесты и fixtures

```sh
uv run python scripts/create_test_database.py
uv run pytest -q
uv run ruff check src scripts migrations tests
uv run ruff format --check src scripts migrations tests
uv run mypy src/digital_station
uv run python scripts/generate_fixtures.py
uv run python scripts/generate_smoke_plan.py
uv run python scripts/export_openapi.py
node --check src/digital_station/static/smoke.js
node --check src/digital_station/static/station.js
node tests/test_railway.mjs
uv run python scripts/verify_railway_stories.py --pretty
# Отдельная проверка общего TypeScript-контракта, без сборки frontend друга:
npm exec --yes --package typescript -- tsc --noEmit --strict --target ES2022 --lib ES2022,DOM contracts/api-v1.ts contracts/railway-display.ts
```

Тесты используют только `digital_station_h0_test`, не demo DB и не SQLite.
Каждый API test создаёт отдельный initial run, не удаляет существующие данные;
Alembic upgrade применяется автоматически. Проверены весь smoke до sim5940,
60 IDs/принадлежность/целевой порядок T4, все phases/legs, resource/track/precedence/
capacity guards, fixed footprint/type independence, реальный clock/controls,
receipts/CAS/rollback, compact-effect reconstruction, recovery и SSE queues/cursors.

`fixtures/api/v1/` — старые **static mock samples** initial/normal/incident/auth/config/errors
с manifest provenance, не live запись. `fixtures/scenarios/demo_main_v1.smoke_plan.json`
— фиксированное расписание из SPEC4.1; independent_validator_passed=false и
optimizer_implemented=false. Backend не читает API-fixture-файлы вместо живого State.
Static fixtures остаются исходными mocks. `manual-control-v1` создаётся отдельно в БД
через `scripts/prepare_h15_manual.py --confirm-api-stopped` после полной остановки API:
те же topology/поезда, T1 inspection manual/u-operator, paused sim120, baseline config1.
До human confirmation полный поиск не предполагает неизвестное время ответа человека;
после подтверждения validator-checked replan продолжает auto-хвост. Старые runs не удаляются.

Планировщик не использует `Train.type` для выбора workflow: DAG задаётся profile.
До12 event-jump rollout: FCFS/earliest due/urgency/release-R × ascending/descending/
least-scarce (число конкурирующих незавершённых операций). Каждый candidate отдельно
проверяется validator; жёсткие ограничения не входят в штраф J. Оптимальность не
доказана; `no_feasible_plan` означает отсутствие найденного полного допустимого
варианта в ограниченном поиске, не математическое доказательство несовместимости.

150ms coalescing, общий deadline5wall-s от первого ingress; cutover/barrier новых
стартов, неизменные running/completed работы. Worker получает immutable input без
DB session. Применение проверяет run/input/config/base plan/digest/actual prefix,
затем PostgreSQL CAS. Timeout отличается от no_feasible; watchdog убивает зависший
worker, следующий расчёт создаёт замену. До замены readiness503, но actor/SSE живы.

Для нового контрольного **run sim480** без удаления истории остановить API, выполнить
`uv run python scripts/prepare_h12_demo.py --confirm-api-stopped` и снова запустить API.
Это offline fixture preparation реальным executor, не новый публичный reset API и
не доказательство live timing. Read-only аудит: `uv run python scripts/h12_status.py`.
`scripts/check_clean_h12.py` — одноразовая проверка на пустой additive тестовой БД;
повторный запуск на уже заполненной БД откажет, а не очистит её.

`contracts/api-v1.ts` — принятая полная будущая v1.0 из handoff;
`contracts/openapi.json` — фактически реализованная часть H15 с аддитивным read-only
explanation endpoint. Друг уже может подключать snapshot/SSE, simulation,
incidents/replan/apply, actual/config/history/replay/CSV/manual и брать `/tech/station` как reference; основной React/Гант
frontend остаётся отдельной дорожкой.

## Две машины и браузерная проверка

Backend слушает0.0.0.0:8000; `/tech/station` и `/tech/smoke` можно открыть через LAN-IP backend.
Для Vite проксировать `/api` на `http://<backend-ip>:8000`, changeOrigin:true;
в ALLOWED_ORIGINS добавить origin друга и перезапустить backend. Cookie same-origin;
PostgreSQL в LAN не открыт. Для HTTPS COOKIE_SECURE=true. LAN HTTP UUID fallback
использует CSPRNG, когда [randomUUID требует secure context](https://developer.mozilla.org/en-US/docs/Web/API/Crypto/randomUUID);
[getRandomValues доступен и без secure context](https://developer.mozilla.org/en-US/docs/Web/API/Crypto/getRandomValues).
Фактическая проверка машины друга/LAN пока не проведена.

Историческая приёмка H12 — Playwright MCP + системный Chromium151: реальные LIVE changes без reload, manual
replan/sibling apply, single/pending-loss, batch5/10, два feasible варианта с validator,
autoapply, logout/login/viewer403, disconnect/reconnect. На том этапе **132 pytest passed**,
Ruff/mypy/TypeScript strict passed. Steady SSE1.248Hz; batch10 replan919.9ms; финальный
manual replan959.6ms, SSE2.273Hz в окне с transitions. [Артефакты H12](artifacts/h12/).
Это reception frequency и отдельные elapsed samples, не финальное доказательство
ingress→paint<500ms/120s нагрузочной приёмки. В отрицательных connection/role тестах
403/aborted requests/connection refused ожидаемы; nominal прогоны без ошибок.

Post-H12 railway UI: **178 pytest +9 JavaScript tests passed**, Ruff/mypy и TypeScript
strict contracts passed. Chromium проверил12 путей/7 поездов, фактический манёвр,
погрузку, R2 closure/I1 loss, два feasible варианта/autoapply, роли и очистку logout.
Номинальный браузерный прогон без console/page/network errors. SSE steady1,248Hz,
LIVE1,725–1,904Hz; два incident replan1635,9/1736,6ms. [Отчёт и screenshots](docs/RAILWAY_UI_REPORT.md).
Это короткие измерения receive/elapsed, не замена полной paint/120s приёмке.

H15:217pytest +9JS passed, Ruff/mypy/TS strict и чистый Alembic upgrade/check passed.
Chromium подтвердил actual/history/replay/ReturnLIVE/CSV/config roles/manual+departure.
При полном CSV export SSE1,292Hz/maxgap950,4ms; actual/history/config физически
сохранены после PostgreSQL/API restart. [Факты, screenshots и ограничения](docs/H12_H15_REPORT.md).

Mock/учебные: сценарий, технологические длительности, next-station calendar/600s
travel, manual smoke-bootstrap и static API samples. Не реализованы публичный new-run,
time/render telemetry/metrics, полная noisy normalizer pipeline и AI. Основной frontend
друга и полная измеримая SLA/benchmark/защита не объявлены готовыми.
Railway-oriented UI реализован по отдельной post-H12 команде. Оставшаяся работа
MUST/SHOULD/DROP описана в дополнении PLAN_24H; наличие UI не закрывает эти хвосты.

Основания API: [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/),
[SQLAlchemy async sessions](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html),
[Alembic async cookbook](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic).
