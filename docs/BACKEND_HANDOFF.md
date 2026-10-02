# Backend handoff — v1.0 FROZEN

Дата: 2026-10-02. Ниже реализованные API, не roadmap.
12 путей/7 поездов/33 операции, прежний core domain/API v1.0.
[Отчёт](BACKEND_V1_REPORT.md), [freeze hashes](../contracts/v1.0-freeze.json).
Этот документ заменяет исторические «ещё не реализовано» H0–H18.

## 1. Один путь запуска с нуля

Python3.12, uv, Docker Compose. В новой копии создать `.env` из
[.env.example](../.env.example); задать локальные секреты. Существующий env не перезаписывать.

```sh
uv sync --frozen --python 3.12
docker compose up -d --wait postgres
uv run alembic upgrade head
uv run alembic check
uv run python scripts/setup_backend.py
uv run uvicorn digital_station.main:app --host 0.0.0.0 --port 8000 --workers 1 --timeout-graceful-shutdown 5
```

Setup при остановленном API идемпотентен, не сбрасывает current State. Users,
config, default run и два approved сценария: demo_main_v1/manual-control-v1.
Ручное исправление таблиц не требуется. API тоже умеет bootstrap main, но полный
setup сразу добавляет manual fixture. Миграции обязательны. Node22.12+/npm — только frontend/JS tests.

## 2. Env / PostgreSQL / restart

| Env | Назначение |
|---|---|
| POSTGRES_USER/POSTGRES_DB | локальные station/digital_station |
| POSTGRES_PASSWORD | ваш DB-пароль, не значение из чужого env |
| POSTGRES_PORT |55432, Compose публикует только loopback |
| DATABASE_URL |postgresql+psycopg://USER:ENCODED_PASSWORD@127.0.0.1:55432/digital_station |
| TEST_DATABASE_URL |то же подключение, отдельная **digital_station_h0_test** |
| DEMO_VIEWER_PASSWORD / DEMO_OPERATOR_PASSWORD / DEMO_DISPATCHER_PASSWORD / DEMO_ADMIN_PASSWORD | четыре локальных пароля |
| COOKIE_SECURE |false для HTTP, true только с HTTPS |
| ALLOWED_ORIGINS |точные browser origins через запятую, включая frontend origin |

Пароли/порт/пользователь URLs совпадают с Compose; URI-символы пароля percent-encode.
Секреты/`.env` не передавать frontend и не коммитить. Один API worker, advisory
lock исключает двух владельцев станции. `docker compose stop/restart` сохраняет
volume, `down -v` не применять. Recovery: тот же run/physical State/history/config,
PAUSED, sim-time не включает downtime. Проверенный startup replan может заменить
active_plan_id, добавить события/revisions. Это не сброс фактических позиций.

## 3. Demo users / RBAC

| Login | Role | Display persona | Права |
|---|---|---|---|
|viewer|viewer|Наблюдатель|все чтения/CSV/render telemetry, без mutations |
|operator|operator|Исполнитель технологической операции|чтение + только own допустимая manual service |
|dispatcher|dispatcher|Дежурный по станции|симуляция/incidents/resolve/replan/apply |
|admin|admin|Администратор системы|все выше + config/new-run, допустимая manual service |

Пароли соответствуют DEMO_*_PASSWORD, реальных паролей в docs нет. API роли не
равны профессиям станции. Осмотрщики/составитель/машинисты — domain resources.
Operator user.id=u-operator, manual inspection T1 назначен ему. Disabled UI
недостаточен: server RBAC/guards всегда проверяются.

## 4. LAN / same-origin Vite

Backend0.0.0.0:8000, DB толькоloopback. На машине друга relative `/api` проксируется
на backend LAN IP; browser общается только с frontend origin, cookie относится
к нему. Добавить точный `http://FRONTEND_LAN_IP:5173` в backend ALLOWED_ORIGINS,
перезапустить API. Это **CSRF Origin allowlist, не CORS**. Не переходить на
absolute cross-origin API/localStorage bearer token.

```javascript
// Фрагмент server существующего vite.config.js
server: { host:'0.0.0.0', proxy: {
  '/api': {target:'http://BACKEND_LAN_IP:8000',changeOrigin:true,timeout:0,proxyTimeout:0},
  '/health': {target:'http://BACKEND_LAN_IP:8000',changeOrigin:true},
  '/tech': {target:'http://BACKEND_LAN_IP:8000',changeOrigin:true}
}}
```

Cookie без Domain, HttpOnly/SameSite=Lax; cookieDomainRewrite не нужен. Открыть
порты8000/5173 в LAN firewall при необходимости, DB наружу не публиковать.
Reference [integration/lan-probe](../integration/lan-probe/vite.config.mjs):
`npm ci`, `BACKEND_URL=http://BACKEND_LAN_IP:8000 npm run start` в его папке.
На одном host BACKEND_URL можно не задавать. Loopback и LAN-IP URLs проверены;
физический ноутбук друга/его firewall и основной frontend пока не проверены.

## 5. Login / minimal curl

Base **/api/v1**. Swagger `/docs`, JSON `/openapi.json`, экспорт
[contracts/openapi.json](../contracts/openapi.json). Health вне base:
GET `/health/live`, `/health/ready`. Readiness включает DB/migrations/actor/worker.

```javascript
await fetch('/api/v1/auth/login',{method:'POST',credentials:'same-origin',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({username,password})});
// 200 {user}; HttpOnly session cookie, НЕ token response
await fetch('/api/v1/auth/me',{credentials:'same-origin'}); //200 {user}
await fetch('/api/v1/auth/logout',{method:'POST',credentials:'same-origin'}); //204
```

Сессия12ч, revoke/expiry в PostgreSQL. Перед logout закрыть SSE и stop/drain
render reports. При401 вернуть login. Env password change отзывает старые сессии.

```sh
curl -fsS http://127.0.0.1:8000/health/ready
# Заменить placeholder только своим локальным паролем; cookie — локальный секрет.
curl -sS -c /tmp/station.cookies -H 'Content-Type: application/json' \
  -d '{"username":"dispatcher","password":"<DEMO_DISPATCHER_PASSWORD>"}' \
  http://127.0.0.1:8000/api/v1/auth/login
curl -fsS -b /tmp/station.cookies http://127.0.0.1:8000/api/v1/snapshot
curl -N -b /tmp/station.cookies http://127.0.0.1:8000/api/v1/stream
```

Cookie/пароли не коммитить. Для реальных команд run_id/revision брать из свежего snapshot.

## 6. Snapshot / types / time

GET `/snapshot` возвращает **State**, не data-wrapper. Topology/layout, fact
tracks/zones/trains/groups/resources/operations, incidents/conflicts/plans,
last_replan/efficiency. [Core TS](../contracts/api-v1.ts) — domain/display,
[HTTP TS](../contracts/http-v1.ts) — полный транспорт из actual OpenAPI и SSE
envelopes. Одноимённые типы импортировать с aliases, не объединять modules.
Pydantic defaults могут делать поле optional в input schema, но output State
сериализует соответствующие значения. Не удалять поля/unknown enum самовольно.

`*_sim_s` — целые секунды относительно scenario_epoch, не Unix-ms;
ISO UTC server_time/created_at — wall-time; `*_ms` — реальные wall-ms.
Length_m — метры, progress/route_progress0..1. Фазы/labels —
[railway.js](../src/digital_station/static/railway.js) и Railway-oriented UI reference
в [FRONTEND_HANDOFF](FRONTEND_HANDOFF.md). В API payload enum остаются внутренними.

## 7. SSE / reconnect

```javascript
const es=new EventSource('/api/v1/stream');
es.addEventListener('state',e=>{const {state,cause}=JSON.parse(e.data); /* replace live */});
es.addEventListener('reset',e=>{const {state,reason}=JSON.parse(e.data); /* clear old run */});
```

ID=run_id:event_seq, full State nominal1.25wall-Hz, immediate important transitions.
PAUSED heartbeat может повторять seq с новым server_time; freshness определяется
приходом кадра. Native reconnect передаёт Last-Event-ID; при новом EventSource
использовать `/stream?after=RUN_ID:SEQ`. Истёкший/чужой cursor →reset.
>3wall-с без кадров — stale, >10с — offline; watchdog закрывает half-open transport,
backoff/auth-check/cursor. Reference `/tech/station` это реализует.
Проверить generation/run_id: поздние ответы replay/logout/старого run игнорировать.

## 8. Mutations / CAS / retries

Общий envelope:

```json
{"request_id":"<UUID>","run_id":"<current run>","expected_input_revision":12}
```

UUID один на действие. Network timeout →повтор того же body/UUID (даже старого
run_id при new-run), durable receipt возвращается идемпотентно.409 →snapshot,
объяснение изменившихся условий, новый UUID только для нового осознанного действия.
Не молча заменять revision/retry старую команду. Clock часто меняет seq/state_version;
mutation CAS использует input_revision, не event_seq.
Receipt `{request_id,run_id,input_revision,state_version,result}`.

| Метод / путь после base | Extra body | Ответ/права |
|---|---|---|
|POST `/simulation/control`|action play/pause/step/set_speed, speed1/5/10 только set_speed|200; dispatcher/admin |
|POST `/incidents`|items1..10 IncidentInput|201,incident_ids/replan_id; dispatcher/admin |
|POST `/incidents/{id}/resolve`|нет|200,incident_id/replan_id; dispatcher/admin |
|POST `/replans`|reason:"manual"|202,replan_id; dispatcher/admin |
|POST `/plans/{id}/apply`|нет|200,plan_id; dispatcher/admin, backend can_apply |
|PATCH `/config`|patch weights?/category_thresholds?/planner?|200,config_version/replan_id; admin |
|POST `/operations/{id}/complete`|нет|200,operation_id/replan_id; own operator/admin |
|POST `/runs`|scenario_id,seed|201,new_run_id + SSE reset; admin |

```sh
curl -sS -b /tmp/station.cookies -H 'Content-Type: application/json' \
  -d '{"request_id":"<UUID>","run_id":"<RUN>","expected_input_revision":12,"action":"play"}' \
  http://127.0.0.1:8000/api/v1/simulation/control
```

Step только PAUSED, +1sim-с. Play/pause/speed управляют server clock. Начатая
работа не отменяется. Input changes делают старый результат stale; no-feasible
не означает математически доказанной невозможности или timeout.

## 9. Incidents / alternatives / actual KPI

4 kind: train_delay, track_closure, resource_loss, destination_block. Duration1..7200
sim-с. Только train_delay требует delay_sim_s1..3600 и поезд ещё на подходе:

```json
{"items":[{"kind":"train_delay","target_id":"P1","duration_sim_s":600,"delay_sim_s":300}]}
```

Добавить envelope. Track/resource targets из State; DEST_W/DEST_E — учебные окна
соседней станции, не её полная модель. Busy target →pending, running prefix
не прерывается. Backend affected entities/conflicts/messages, без local safety logic.

GET `/replans/{job_id}` → `{job,plans:PlanDetail[]}`. Job queued/running/succeeded/
no_feasible_plan/stale/timeout/failed. Plan: operations/validator/changed_operation_ids/
explanation/forecast/J/can_apply. Лучший actual feasible autoactive, второй proposed;
сортировка backend по J, не frontend по индексу. Feasible ≠optimal.
GET `/replans/{job_id}/explanation` — server diff/departures от сохранённых inputs,
unavailable явно, не придуманные old values. Job details только current run,
старый run — через replay State. Apply stale/invalid никогда не обходить клиентом.

Actual KPI — **State.efficiency**, `/efficiency` endpoint отсутствует.
5 raw factors/units/penalties/weights/contributions, score/category/formula_version;
rolling900sim-с. Null — недостаточно данных, не0/100. Forecast каждого plan —
validated7200sim-с horizon. Не сравнивать разные окна actual/forecast как процент
улучшения. J — отдельная минимизируемая цель, не тот же индекс.

## 10. History / replay / CSV

GET `/history?run_id=...&from_seq=0&limit=500&from_wall_time=...&to_wall_time=...`.
from_seq exclusive, limit1..500, window ISO timezone. HistoryPage items/next_from_seq/
has_more/anchor_seq/available_from_wall_time/available_to_wall_time. Для15wall-мин
фиксировать window и пройти pagination, anchor загрузить отдельно.
GET `/history/snapshot?run_id=...&seq=...` — точный durable State через checkpoints/
effects, не запуск engine/planner. Все роли, read-only; server retention24h.
Держать liveState/displayedState отдельно; SSE продолжает приходить при replay,
mutations disabled, Return LIVE — последний live кадр. Новый run короче15wall-мин
показывать честно; paused anchor до окна допустим.

GET `/reports.csv?run_id=...&from_wall_time=...&to_wall_time=...` →attachment UTF-8;
без window — вся доступная история run. Raw actual KPI/incidents/plans/timings,
units/formula_version, details с seq/window/config. Это не rolling900sim-с KPI.

```sh
curl -fsS -b /tmp/station.cookies 'http://127.0.0.1:8000/api/v1/history?run_id=<RUN>&limit=100'
curl -fsS -b /tmp/station.cookies 'http://127.0.0.1:8000/api/v1/reports.csv?run_id=<RUN>' -o station-report.csv
```

## 11. Config / manual / new run

GET `/config` — всем, current run. Admin PATCH полными переданными вложенными
объектами, непереданные сохраняются. Не patch config_version/units.
5 finite неотрицательных весов суммарно1;0<=attention_min<normal_min<=100;
time_limit_ms100..3000,max_rollouts1..12 strict integer. Пустой/null/частичный/
неверный patch422 без write. Новая config_version/input_revision и replan,
включая пороги; не считать варианты готовыми в момент receipt.

GET `/scenarios` — actual seeded catalog. Admin new-run с scenario/seed42.
Main auto; manual-control-v1 — same12/7, inspectionT1/u-operator. Manual исполняет
**guarded smoke prefix**, не optimized полный план. Job no_feasible/
MANUAL_CONFIRMATION_REQUIRED до неизвестного human end честно показан. Play/speed
не ставят повторный control-only search barrier на этот prefix; guards остаются.
Operator onlyassigned + backend can_complete после min duration/predecessors.
Ресурс busy до подтверждения; movement нельзя завершить вручную. Foreign403,
auto/movement/не готова409. Completion →durable event→validated tail replan→DAG.

Seed0..2147483647 строгий integer, влияет на mock noise, не создаёт новых поездов/
путей. Старые runs/history сохраняются; reset заменяет full current State.
SERVICE-P2 fixture отсутствует, Train.type всё ещё FREIGHT/PASSENGER/SERVICE/OTHER.

## 12. Metrics / errors / fixtures

GET `/time` Unix wall-ms probes; POST `/telemetry/ui-render`204 отдельная служебная
schema без envelope, GET `/metrics` actual p50/p95/max/invalid/hidden/missing/
exceedances + planner timings. Все auth roles. Reference
[render-telemetry.js](../src/digital_station/static/render-telemetry.js): probes/double-rAF.
Никаких fake fast samples, hidden tab не foreground. Render metrics bounded15wall-мин/
in-memory, domain/events durable. Нормализация mock progress server-side,
occupancy/incidents не сглаживаются; внешняя интеграция не требуется.

Errors `{error:{code,message,details,request_id}}`:401 auth,403 role/Origin,
404 entity/run/seq,409 CAS/stale/idempotency/history gap/invalid transition,
422 schema/config/window/unknown scenario,429 actor busy Retry-After1,503 DB/readiness.
Network error отдельно от API JSON error,204 body не парсить как JSON.

Independent fixture mode: [static](../fixtures/api/v1/manifest.json) synthetic
samples, **не** solver/SLA proof; [live](../fixtures/api/v1/live/manifest.json)
captured actual auth/state/replay/manual/plans/diff/KPI/config/history/metrics;
[State schema](../fixtures/api/v1/state.schema.json). Auth fixtures без cookie/password.
Offline mode явно подписать, transport mock чтений; mutation mock не выдавать за
backend success. API mode — тот же display logic, настоящий relative API/SSE.

## 13. Frontend НЕ рассчитывает

Физические positions/occupancy/traction/groups/phases; safety/routes/conflicts;
schedule/admissibility/validator/can_apply/can_complete; planner/KPI/J/forecast;
plan diff/railway reasons и право операции — backend-authoritative.
Можно форматировать labels/time, рисовать layout/progress из State, фильтровать
визуальный список, выбирать replay seq, измерять render. Нельзя додвигать поезд
browser timer, локально применять план, сглаживать discrete State/создавать правила.

Friend делает UI, затем smoke login→snapshot→SSE→play→incident→alternatives/
autoapply→KPI→replay→LIVE→CSV→manual/roles/new-run. Backend без новых features:
v1.0 frozen; только критические совместимые исправления с повторной приёмкой.
