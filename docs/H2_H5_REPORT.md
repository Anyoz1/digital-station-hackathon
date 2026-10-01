# Отчёт H2–H5 — исполняемый digital twin и realtime

Дата: 1 октября 2026 года. Выполнен **только разрешённый backend-этап H2–H5**
из [PLAN_24H.md](PLAN_24H.md), включая запрошенное минимальное расширение
`/tech/smoke`. H5–H7, независимый validator и планировщики **не запускались**.
Это отчёт о реализации, а не изменение принятого ТЗ и не приёмка всего проекта.

## 1. Границы и выполненные критерии H5

Четыре документа v1.0 сохранены без изменений: commit `2764a83`, tag
`spec-v1.0`, [SHA-256](V1.0.md). Проверка SHA в pytest проходит;
`git diff spec-v1.0 -- docs/SPEC.md docs/RESEARCH.md docs/PLAN_24H.md
docs/FRONTEND_HANDOFF.md` пуст. Поля State/команд и TypeScript-контракт
v1.0 не переименованы. OpenAPI отражает только фактически реализованные API.

| Критерий H5 / дополнение пользователя | Выполнение и проверка |
|---|---|
| Загрузка topology/scenario `demo_main_v1` | Actor читает сохранённый Scenario JSONB: topology, initial State, roster и smoke timetable. Проверены все12 путей R1–R4/S1–S4/C1–C2/H/D1, границы BW/BE и связность каждого маршрута; удаление связи C2 отвергается тестом |
| 7 поездов, без расширения scope | T1/T3/T6 — freight transit; T2/T5 — freight local; T4 — freight reclassify; P1 — passenger transit. Основное демо6 FREIGHT +1 PASSENGER, без дополнительного SERVICE |
| Wagon ledger, groups, traction/resources | 60 уникальных wagon IDs,10 групп,19 ресурсов; origin/assigned train и состав групп неизменны. Detached group имеет current_train_id=null. Тяга/бригада следуют своему поезду. Каждый полный прогон проверяет сохранность и принадлежность вагонов |
| Различные DAG, не единая цепочка | Исполняются33 операции трёх грузовых profiles; passenger содержит только arrival→departure, group_ids=[] и fixed120м. Тесты меняют Train.type без изменения profiles: исполнение не ветвится по FREIGHT |
| Фазы и физическая связность | Shunt420sim-секунд: семь фаз, шесть последовательных legs через H, сцепка/отцепка и возврат L1 в D1. Проверены все11 внутренних границ фаз/legs и остаточное ядро на home R |
| Factual occupancy/location | Track occupancy пересчитывается по физическим positions; route_progress меняется на сервере. Home reservation и operation locks показываются отдельно. У fixed P1 встроенная тяга не добавляет ещё20м; текущие body/total length freight пересчитываются при отцепке/сцепке |
| Station actor и clock | Один writer, PostgreSQL advisory lock, bounded queue128. Monotonic clock, pause/play, speed1/5/10; API step на паузе. Wall-time простоя после restart не прибавляется к sim clock |
| Runtime guards | До начала проверяются predecessors, ресурсы/готовность, track/zone locks, capacity, source/target, непрерывность маршрутов, тяга и порядок групп. Нарушение блокирует операцию с reason code, без перемещения/потери вагонов. Это **не независимая валидация плана** |
| SSE полного State ≥1wall-Гц | Nominal1,25Гц; measured1,249–1,560Гц на содержательных непрерывных участках, max gap912,5мс. Паузу сопровождают heartbeat с полным State; transitions публикуются после commit сразу по due deadline |
| Smoke-plan до FCFS | Hand-authored fixture из SPEC§4.1 покрывает33 операции, помечен optimizer_implemented=false / independent_validator_passed=false. Public active_plan_id=null, plans=[], last_replan=null |
| Persistence state/events | Current State, compact event effects/transitions и command receipt сохраняются атомарно; SSE только после commit. Dynamic checkpoints без повторения topology; восстановление реального run и реконструкция State из фактических событий проверены |
| Воспроизводимость | Два независимых engine исполняют один fixture до sim5940: большой event-jump и шаги60с дают одинаковый конечный State и одинаковую последовательность содержательных transitions |
| Минимальный ручной интерфейс | Health/login/logout/роль/snapshot/raw JSON плюс LIVE/PAUSED, clock/speed/seq, tracks/occupancy/locks, trains/location, resources, operations/progress/phases, SSE log и play/pause/speed. Нет отдельной domain logic или frontend-симуляции |

Максимально допустимые6 одновременно активных поездов — runtime limit, не6 новых
поездов в каждую секунду. Fixture не расширен; доступные C2/S3/S4 остаются частью
связной топологии, но hand-authored расписание не обязано занимать каждый путь.

## 2. Backend и фактические API

Стек прежний: Python3.12, FastAPI, PostgreSQL16, SQLAlchemy2 async/Psycopg3,
Alembic. Добавлен только **dev-tool mypy**, не новый runtime-сервис.
Actor обрабатывает команды и clock последовательно; медленный SSE-клиент
не блокирует исполнение. Queue клиента100 кадров; ring до900wall-секунд,
с дополнительным лимитом32MiB wire payload. Старый/gapped cursor получает reset,
не обещание бесконечного catch-up. Redis/Kafka/TimescaleDB/Kubernetes отсутствуют.

| Endpoint | Реальное поведение |
|---|---|
| GET `/health/live`, `/health/ready` | Ready проверяет DB/schema/initial State/scenario/actor; stage H2-H5, simulation=true, sse=true, optimization=false |
| POST `/api/v1/auth/login`, `/api/v1/auth/logout`; GET `/api/v1/auth/me` | Сохранённая аутентификация H0–H2, роли, cookie sessions, expiry/revoke |
| GET `/api/v1/snapshot` | Полный актуальный State из PostgreSQL; не API-fixture файл |
| GET `/api/v1/stream?after=run_id:seq` | EventSource, полные State, cause, UTC timestamps; Last-Event-ID, catch-up/reset, heartbeat на паузе |
| POST `/api/v1/simulation/control` | play/pause/step/set_speed; dispatcher/admin200, viewer/operator403; UUID request_id, run/input revision, durable idempotent receipts |
| GET `/api/v1/scenarios`, `/api/v1/config` | Существующий readonly сценарий и config |
| `/tech/smoke`, `/tech`, `/`, `/docs`, `/openapi.json` | Минимальный клиент, его aliases, Swagger и фактическая схема |

Тело control не изменено: `{request_id, run_id, expected_input_revision,
action, speed?}`. Speed требуется только для set_speed. Повтор одинакового
user/request_id/body возвращает сохранённый receipt; другой body →409
IDEMPOTENCY_MISMATCH. Устаревший input revision/run →409. Step в running →409;
переполненная command queue →429. HTTP/API ошибки отображаются в smoke UI.

`sim_time_s` — целые секунды; actor сохраняет дробную monotonic anchor для
pause/resume/speed. Seq меняется при durable commit, не при heartbeat;
input_revision меняется при control/recovery, не при каждом tick.
Каждый heartbeat содержит свежий server_time, даже когда физическое State не меняется.
UI использует backend State; render не инициирует domain transitions.

При потере связи UI показывает STALE после3с, OFFLINE после10с; reconnect
имеет capped backoff с jitter. При logout streams закрываются; на сервере
сессия проверяется при открытии и перед кадрами. При DB/CAS/runtime failure
actor останавливается, streams закрываются, ready/snapshot дают503 — без fake fresh State.

## 3. PostgreSQL, миграции и восстановление

- Контейнер `alt-digital-station-postgres-1`, `postgres:16-alpine`, **healthy**;
  volume `alt-digital-station_postgres_data`, loopback `127.0.0.1:55432`.
- Основная БД обновлена с0001 до `0002_simulation_runtime (head)` без удаления run.
  `alembic check` → `No new upgrade operations detected.`
- Обе миграции отдельно применены к новой чистой БД
  `digital_station_h2_clean_test`: head0002,10 public tables,0 runs.
  БД оставлена как артефакт проверки; данные существующих БД не удалялись.
- Миграция0002 добавляет `run.execution_schedule_id` и `command_receipt`.
  Всего9 domain tables плюс alembic_version. State/scenario/event/checkpoint/receipt
  используют JSONB, users/auth sessions остаются реляционными.
- Изменение current State выполняется SQL CAS по state_version. Та же транзакция
  содержит event effects/transitions и receipt команды. Тесты принудительного
  PostgreSQL unique-key failure доказывают rollback и отсутствие SSE публикации
  как для play, так и для step.
- После initial event topology не повторяется в каждом event/checkpoint.
  Checkpoints создаются при init/recovery и каждые30wall-секунд, если есть commit;
  paused heartbeat не создаёт journal rows.
- Реальные restart сохраняют run/sim time, started operations, фазы, группы,
  тягу и locks; mode становится paused, появляется recovery с новой версией.
  Старый SSE cursor после потери memory ring получает reset(cursor_expired).
- На финальной сверке устранено расхождение с SPEC§3.2: Train.body/total length
  теперь отражают **текущие присоединённые группы**, а не исходный состав.
  Это исправление реализации v1.0, не изменение контракта. Recovery пересчитывает
  эти производные поля; roster/target_group_ids/физические positions не изменяются.

Финальный сохранённый run: `run-cda48aa1-eb33-486e-96a0-b22b33eeeae8`;
**paused, speed1, sim1290, event_seq=state_version=220, input_revision17,
config_version1**. В основной БД: **220 events,14 checkpoints,12 command receipts**.
Реконструкция из initial event + всех actual compact effects **в точности совпала**
с current State. [Фактическая DB-проверка](../artifacts/h2-h5/database-final-results.json).
History API/replay UI/retention24ч из этого не следуют: они ещё не реализованы.

## 4. Автоматические проверки

Финальный прогон: **70 passed,1 warning in30,46s**, exit0.
Тесты API/actor используют отдельный PostgreSQL `digital_station_h0_test`
с Alembic, не SQLite и не mocked DB. Каждому тесту создаётся новый additive run;
тестовые данные не удаляются, основное demo не сбрасывается.

| Проверка | Результат |
|---|---|
| 26 API cases | Health/assets, real login/session/4 roles/logout/relogin/expiry, snapshot DB-source,401/403/409/422/Origin, actual controls, restart, migration/JSONB/OpenAPI |
| 12 contract/fixture cases | 12/7/60/19/33 scope,4 Train.type, fixed без групп, IDs/DAG/routes, все11 static API samples/manifest/schema, TS и SHA v1.0 |
| 8 actor cases | Clock/start0/speed/pause/step, heartbeat без durable writes, receipts/version checks, compact replay, atomic subscribe/catch-up/reset/slow client, one writer, rollback play/step, recovery started prefix |
| 24 simulator cases | Все33 операции до sim5940,60 wagon IDs/принадлежность/порядок T4, reproducibility,11 phase/leg boundaries, residual occupancy, length recovery, fixed footprint/type independence,6 guard failures, progress/half-open resource handover |
| `ruff check src scripts migrations tests` | All checks passed |
| `ruff format --check src scripts migrations tests` | 25 files already formatted |
| `mypy src/digital_station` | Success: no issues found in13 source files; check_untyped_defs=true, Pydantic plugin. Это не strict/no-Any режим |
| `tsc --noEmit --strict --target ES2022 --lib ES2022,DOM contracts/api-v1.ts` | exit0; проверен общий контракт, не сборка frontend друга |
| `node --check src/digital_station/static/smoke.js` | exit0 |
| `uv sync --frozen --python 3.12`, Alembic current/check, git diff --check | exit0; lock согласован, ORM diff и whitespace errors отсутствуют |

Единственный warning — deprecation Starlette TestClient/httpx; тесты не падают,
warning не скрывается. Все определения/фикстуры остаются в утверждённом scope.

## 5. Playwright MCP / реальный Chromium и замеры SSE

Использован системный Chromium **151.0.7922.137** через настроенный Playwright MCP,
не только curl/pytest. Реальный адрес: `http://127.0.0.1:8000/tech/smoke`.

1. Открыть страницу, проверить health/ready, войти dispatcher, увидеть роль,
   загрузить настоящий snapshot и установить SSE.
2. Play1×: sim0→12, arrival progress0→0,1. Pause сохраняет sim/progress,
   но heartbeat продолжает поступать. Перейти на10×.
3. Без reload увидеть arrival→inspection: T1 физически занимает R1 на188м.
4. T2 shunt-out: на sim1050 phase=reverse, progress0,5;
   L1+G2L находятся на H (**76м**), G2K+TL2 остаются на R3 (**76м**),
   home R3 закреплён за T2; G2L detached. Визуально проверен screenshot.
5. Продолжить: shunt завершён в sim1260; на sim1275 G2L на C1 (**56м**),
   L1 уже available на D1, cargo running; residual R3 занят76м.
   P1 уже departed, actual arrival720/departure960, без WagonGroup.
6. Второй настоящий EventSource клиент получает то же backend State/seq.
   Оба клиента находились в одной Chromium page: **это не тест двух машин**.
7. Перезапустить API и проверить сохранение started prefix; старый cursor
   получает reset, snapshot и DOM сходятся. Отдельно проверена коррекция длины T2.
8. Logout → viewer login: Play disabled, прямой запрос control →403 FORBIDDEN,
   без изменения State; logout → dispatcher login снова успешен.
   Проверен CSPRNG UUID fallback для LAN HTTP без crypto.randomUUID.
9. На окончательном коде повторить настоящий Play/Pause: sim1280→1290,
   cargo progress0,02778→0,04167, T2 body56/total76; всё **без reload**.
   Финальный backend оставлен paused.

Обычный финальный путь: **0 pageerrors,0 console errors,0 warnings**,
ожидаемые ответы200/204. Отрицательный role test намеренно даёт403 и соответствующее
сообщение Chromium Failed to load resource — сохранён отдельно, не скрыт как «0 ошибок».

### Реальные частоты получения полных State

Используется browser performance.now, непрерывные участки одного mode/speed;
`rate_hz=(N−1)/(last_received−first_received)` в wall-секундах.
Измерение включает heartbeat и immediate transition frames, не только новые seq.
Короткие окна с двумя почти одновременными command frames не используются для
заявления регулярной частоты. На основном прогоне сохранены1147 frames и440 peer frames.

| Участок | N | Wall span, с | Частота, Гц | Max gap, мс |
|---|---:|---:|---:|---:|
| Running1×, sim0→12 | 18 | 12,6031 | 1,3489 | 835,5 |
| Running10×, sim12→1050 | 144 | 103,7186 | 1,3787 | 905,3 |
| Running10×, sim1050→1275 | 36 | 22,4289 | 1,5605 | 912,5 |
| Paused1×, sim12 | 85 | 67,1293 | 1,2513 | 804,8 |
| Paused10×, sim1050 | 457 | 364,6735 | 1,2504 | 907,6 |
| Paused10×, sim1275 | 401 | 320,2488 | 1,2490 | 900,7 |
| Финальный код, running1×, sim1280→1290 | 15 | 10,0777 | 1,3892 | 832,4 |

На этих измеренных участках частота >1Гц, максимальный inter-frame gap <1с.
Это **reception frequency**, не ingress→paint<500мс, не нагрузочная гарантия,
не проверка burst5–10 и не замер перепланирования≤5с. Эти финальные SLA-проверки
остаются на назначенных этапах PLAN; solver ещё отсутствует.

Артефакты:

- [Основной realtime-прогон и timestamps](../artifacts/h2-h5/browser-results.json),
  [финальный код: frames, clock/progress, noReload](../artifacts/h2-h5/final-backend-browser-results.json).
- [Манёвр на H](../artifacts/h2-h5/shunt-chromium.png),
  [cargo после полного манёвра](../artifacts/h2-h5/cargo-chromium.png),
  [финальный paused State](../artifacts/h2-h5/final-paused-chromium.png).
- [Recovery/reset](../artifacts/h2-h5/recovery-results.json),
  [length invariant](../artifacts/h2-h5/current-length-results.json),
  [role403/повторный login](../artifacts/h2-h5/browser-role-results.json).
- [Финальная console](../artifacts/h2-h5/browser-console-final.log),
  [финальный network](../artifacts/h2-h5/browser-network-final.log),
  [console role test](../artifacts/h2-h5/browser-console-role.log),
  [network role test](../artifacts/h2-h5/browser-network-role.log).

Пароли/cookies не сохранены. `.log` локальны и игнорируются Git; screenshots/JSON
доступны в рабочей копии. Более ранние JSON — факты соответствующего момента,
а не подмена их финальными версиями состояния.

## 6. Что ещё fixture/mock/not implemented

- **Synthetic station/scenario:** учебные длительности, составы, инфраструктура,
  ресурсы и исходные availability; это не live-подключение к железной дороге.
  Метры track occupancy относятся к объектам целиком на track. Для движущихся
  объектов location=route/route_progress и эксклюзивные route/track locks;
  частичный footprint по track sections не моделируется.
- **Smoke timetable — hand-authored fixture, не planner.** Нет FCFS, альтернатив,
  оптимизации, independent validator, apply/replan или проверки stale solver result.
  Runtime guards не заменяют будущую независимую проверку плана.
- `fixtures/api/v1/stream.normal.json` и `stream.incident.json` — старые static
  mock samples для frontend; backend не использует их как источник State.
- **KPI/index не реализованы:** raw/norm/contribution/value=null с явной причиной.
  Нет fake улучшений, сравнительного baseline/algorithm runner или solver timings.
- Нет incident/manual confirmation/config mutation/new run/reset API,
  изменения ETA/calendar через UI, destination acceptance/send-or-accumulate policy.
  Unit guard tests закрывают ошибки исполнения, не рабочий incident workflow.
- Нет history/replay API/UI, retention24ч, CSV/экспорта, complete metrics/paint
  telemetry, noisy input normalization. Journal/checkpoints/reconstruction уже
  настоящие, но не объявлены готовым пользовательским replay последних15минут.
- Нет полноценного `/tech` будущих этапов, SVG/Gantt frontend друга, проверки
  двух машин/LAN, passenger service workflow или отдельного SERVICE fixture.
- AI Explanation Assistant — только P2-документ, без внешнего LLM/runtime dependencies.
- Не проведены финальные SLA<500мс / solver≤5с / burst5–10 /24h retention;
  H5–H7 и последующие этапы не выполнялись.

## 7. Фактическая структура и остановка

```text
ALT-hackathon/
├── README.md / pyproject.toml / uv.lock / compose.yaml / alembic.ini
├── .env.example                 # .env локальный, ignored,0600
├── docs/
│   ├── SPEC.md / RESEARCH.md / PLAN_24H.md / FRONTEND_HANDOFF.md / V1.0.md
│   ├── H0_H2_REPORT.md           # историческая приёмка фундамента
│   ├── H2_H5_REPORT.md           # текущая приёмка
│   └── ADDENDUM_P2_AI_EXPLANATION.md
├── src/digital_station/
│   ├── main.py / api.py / auth.py / settings.py / contracts.py
│   ├── db.py / bootstrap.py / scenario.py / fixtures.py / __init__.py
│   ├── smoke_plan.py            # фиксированное расписание, не solver
│   ├── simulator.py             # domain executor/runtime guards
│   ├── runtime.py               # actor/clock/transactions/SSE
│   └── static/smoke.html / smoke.js / smoke.css
├── migrations/
│   ├── env.py
│   └── versions/0001_h0_foundation.py / 0002_simulation_runtime.py
├── scripts/
│   ├── create_test_database.py / generate_fixtures.py / export_openapi.py
│   └── generate_smoke_plan.py
├── contracts/api-v1.ts / openapi.json
├── fixtures/
│   ├── api/v1/                  # 11 static JSON samples/manifest/schema
│   └── scenarios/demo_main_v1.smoke_plan.json
├── tests/
│   ├── conftest.py / test_api.py / test_contracts_fixtures.py
│   └── test_simulator.py / test_runtime.py
├── artifacts/h0-h2/             # сохранённые предыдущие артефакты
└── artifacts/h2-h5/             # screenshots/JSON/локальные console/network logs
```

Исходные hackathon_docs сохранены без изменений. Реализация остаётся в рабочей
копии; публикации/нового Git-коммита не было. Тег spec-v1.0 фиксирует документы.

PostgreSQL и FastAPI оставлены запущенными. Открыть
**http://127.0.0.1:8000/tech/smoke**, войти `dispatcher`; пароль из
`DEMO_DISPATCHER_PASSWORD` существующего `.env`, не перезаписывать файл.
Нажать Play для продолжения с sim1290. Запуск/тестовые команды — в [README](../README.md).
Для API нужен один worker; не удалять PostgreSQL volume ради restart.

**H2–H5 завершён. H5–H7 не начат. Работа остановлена до следующей команды.**
