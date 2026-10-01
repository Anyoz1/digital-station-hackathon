# H15–H18: интеграционная приёмка

Дата: 2026-10-02 Asia/Almaty; основной browser-прогон18:36:59–18:47:27UTC
1октября. Выполнен только последний разрешённый backlog H15–H18.
**H18–H20 автоматически не начат.** Planner/independent validator, архитектура,
12путей/7поездов, State/контракт v1.0 не перепроектированы. SPEC/RESEARCH и
нормативные prefixes handoff/plan сохранены; актуальные статусы — в дополнениях.

## 1. Что закрыто

| Scope H18 | Фактическая реализация и проверка |
|---|---|
| LAN/frontend support | API0.0.0.0:8000/один worker, PostgreSQL127.0.0.1:55432. Настоящий Vite proxy5173, relative API/cookie/native SSE; login/snapshot/incidents/replans/alternatives/autoapply/KPI/history/replay/CSV/config/manual/new-run. Loopback proxy и LAN-IP URL проверены Chromium; позднее отдельно LAN-IP Vite-origin тоже проверен |
| Noisy normalization | Separate `telemetry_observation` JSONB/0006; schema/freshness/current operation+route, durable source ID dedup и sequence high-water, median3/clamp0..1 только observed_progress. Повтор exact body возвращает тот же результат; changed duplicate/out-of-order/invalid/stale/future quarantine. Physical State/occupancy/incidents не сглаживаются. Source — честно synthetic mock, не внешняя железнодорожная телеметрия |
| New-run | Admin POST/runs, scenario+strict seed, durable idempotency, старый run/history сохраняется. Новый paused run/config inheritance, stale старого worker/cancel, SSE reset, init planner в отдельном процессе. Main scope неизменен. Viewer/operator/dispatcher403; bad/stale/duplicate mismatch422/409 |
| Measurements | Auth GET/time, POST/telemetry/ui-render204, GET/metrics. Five probes/min-RTT/refresh30с, double-rAF после настоящего DOM update. UI p50/p95/max/invalid/hidden/missing/exceedances; durable compute/elapsed/outcomes planner отдельно. Metrics не меняют State/план/безопасность |
| Backup reference | `/tech/station` сохранён; добавлены простой data-driven timeline33 Operation, folded metrics/admin new-run. Русские labels/12tracks/7trains/resources/incidents/actual/replay/manual прежние; нового продуктового frontend нет |
| Integration regression |401/403/409/422, stale/tampered result, timeout vs no-feasible, invalid candidates, duplicate/noisy input, receipt retry/reset/recovery, batch5/10, replay/CSV/config/manual. Реальные API/PG restart и browser reconnect с нетривиальным State |

Основные добавления: [normalization.py](../src/digital_station/normalization.py),
[runs.py](../src/digital_station/runs.py), [telemetry.py](../src/digital_station/telemetry.py),
[render-telemetry.js](../src/digital_station/static/render-telemetry.js),
[migration0006](../migrations/versions/0006_telemetry_observation.py).
Kafka/Redis/Kubernetes/CV/RFID/OR-Tools/Gemini/signals не добавлены.

### Контракт и инфраструктура

Все новые endpoints уже предусмотрены v1.0. Единственное additive query —
optional UUID `client_id` у `/stream`, чтобы missing samples считались от
**доставленных** событий, а не только успешных report. Старые клиенты работают
без параметра. Никаких обязательных новых полей State или rename enum.
Текущая OpenAPI экспортирована; frozen `api-v1.ts` не изменён.

`ALLOWED_ORIGINS` — exact CSRF Origin allowlist, не CORS. Same-origin Vite proxy
с `changeOrigin:true`/SSE timeout0 сохраняет cookie; absolute backend URLs для
fetch/EventSource в frontend не нужны. Добавлен локальный проверенный
`http://10.63.52.9:5173` в ignored `.env`; неизвестный origin машины друга нужно
добавить отдельно и перезапустить API. Direct same-origin backend LAN `/tech`
тоже работает. БД в LAN не открыта. Auth session переживает restart.

Проверочный [Vite probe](../integration/lan-probe/vite.config.mjs) — QA/fallback,
не React-продукт. Два браузера находятся на **одном физическом host**. Основной
frontend друга и firewall/сеть второго ноутбука не проверены: его файлов/URL
в текущей работе нет. Это открытая совместная интеграционная приёмка, не скрытый pass.

## 2. Foreground SLA, без подставных цифр

System Chromium151, Playwright SDK + MCP, Linux/Hyprland/Xwayland; AMD Ryzen7 5700U,
16logicalCPU/14GiBRAM. Две независимые headed instances с видимыми окнами на
workspace9: A — настоящий Vite127.0.0.1:5173, B — LAN-interface URL10.63.52.9:8000.
Оба клиента авторизованы (admin/dispatcher), backend и PostgreSQL на том же host.
Это не WAN или две физические машины. Workspaces проверялись каждые15с, оба
120wall-с прогона имеют `foreground_verified=true`.

Время measured event: server ingress→commit/SSE→DOM update→double-rAF, поправка
часов + uncertainty. Initial/reset/catch-up/heartbeat не входят в paint samples;
heartbeats входят в receive-frequency. Missing≥2с/invalid/hidden/exceedances
учитываются отдельно. Double-rAF — практический proxy paint, не измеритель пикселя.
Nearest-rank percentiles; таблица **по клиентам**, не усреднённые percentile.

| Скорость/клиент | wall-с | SSE Hz | Render samples | p50 upper,мс | p95 upper,мс | max upper,мс |
|---|---:|---:|---:|---:|---:|---:|
|1× A|120,208|1,27822|128|149,020|231,020|361,613|
|1× B|120,208|1,27824|128|146,714|228,527|367,511|
|10× A|120,178|1,44866|177|84,942|166,613|289,613|
|10× B|120,178|1,44865|177|87,473|166,511|268,511|

Во всех четырёх строках **invalid=hidden=missing=exceedances=0**. Всего610valid
render samples. Max receive frame gap961,3ms/929,6ms соответственно;
snapshot UTF-8 на конце53549/54376bytes (не средний wire payload).
Sim-time действительно достиг120/1201; raw сохраняет фазы, прогресс, фактическую
занятость/местоположение, ресурсы и job transitions, не только анимацию.

Batch5: по20valid render samples/клиент, max upper195,595ms; batch10: по20,
max146,942ms. Missing/invalid/exceedances0. Оба дали2distinct feasible alternatives,
independent validator passed, automatic apply и новый State. Conflicts реально
наблюдались в SSE до применения; результаты не нарисованы клиентом.

Raw: [1×](../artifacts/h18-live-1x-raw.json),
[10×](../artifacts/h18-live-10x-raw.json),
[batch5](../artifacts/h18-batch5-raw.json),
[batch10](../artifacts/h18-batch10-raw.json),
[сводка](../artifacts/h18-acceptance.json).

### Неуспешные проверки и условия применимости

Сначала браузеры мешали пользователю на его рабочем столе. Узкое runtime-rule
для `alt-h18-sla-[01]` помещает **только наши QA-окна** на стол9; личный config
не изменён и стол пользователя автоматически не переключался. По согласованию
пользователь оставил стол9 активным на время замера, затем вернулся к учёбе.

Предыдущий **background** прогон на неактивном desktop честно не прошёл:
max≈2088ms, rAF throttling. `document.visibilityState=visible` там не доказывает
foreground. Его [1×raw](../artifacts/h18-background-1x-raw.json),
[10×raw](../artifacts/h18-background-10x-raw.json) и
[partial](../artifacts/h18-background-partial.json) сохранены, не удалены ради
красивых чисел. Foreground цифры не обещают SLA скрытой вкладки/другого desktop.
Там также QA-bot использовал отставшую SSE revision и получил409; helper исправлен
на получение настоящего snapshot перед командой, без обхода CAS.

## 3. End-to-end planner/replan timings

`elapsed_ms` — первый incident ingress→validated committed applied plan→SSE
publication; включает coalescing/queue/input/worker/validator/commit/publish.
`compute_ms` — отдельно CPU worker search, не заменяет end-to-end.
По20повторов delay/closure/resource_loss/burst10: каждый начинается новым run
одинакового demo_main_v1/seed42/config1, speed10, без заранее известных будущих
инцидентов, с одним и тем же стартовым контрольным состоянием. Контрольные burst
содержат разные target/kind; pause после job; старые runs сохраняются.

| Case | n | p50 elapsed,мс | p95,мс | max,мс |
|---|---:|---:|---:|---:|
|train_delay|20|1248,354|1297,609|1313,096|
|track_closure|20|1215,729|1265,844|1306,777|
|resource_loss|20|1231,948|1284,772|1288,020|
|burst10|20|1308,881|1366,932|1369,717|
|Всего|80|1248,354|1336,437|1369,717|

**80succeeded,0timeouts/no-feasible в этих допустимых контрольных случаях,
0deadline exceedances.** Compute p50/p95/max571,301/610,885/631,321ms.
Отдельные демонстрационные batch5/10:1306,855/1356,102ms elapsed,
587,448/584,970ms compute. [80raw jobs](../artifacts/h18-replan-timings-raw.json).

Отрицательные planner regression проверяют принудительный timeout/replacement,
no_feasible vs timeout, stale input, tampered certificate/invalid candidate и
continuing SSE. Отсутствие feasible в ограниченном поиске не называется
доказанной математической несовместимостью. **Эти80 timing repeats не являются
paired сравнением качества FCFS vs heuristic.** Оба planner получают одинаковые
inputs в regression; итоговый six-scenario quality report ещё предстоит.

## 4. Browser/API/replay/CSV/manual evidence

Основной headed Playwright flow: login→live→play→фактическое движение→incident
→conflicts→2validated alternatives→autoapply/SSE→actual KPI→history snapshot
→ReturnLIVE→настоящий CSV download→roles→config→new-run→manual confirmation.
MCP дополнительно подключался к headed CDP и затем проверил post-fix reference
в системном Chromium без открытия мешающих GUI-окон.

Actual sample10×: window `[301,1201]`, score96,7/normal, efficiency-v1.
Raw throughput1ratio, delay120sim_seconds, occupancy0,183333ratio, conflicts0count,
resource_idle0ratio. Только delay даёт3,333333пункта потери при weight0,25;
это реальный due-cohort T1/P1, **не** обещанный процент улучшения. В начале run
throughput/delay null по отсутствию когорты — не подставленные0/100.

Replay: run `run-1298df06-930a-4907-a661-71aaf257f1da`, live seq25, selectedseq1;
live не изменён просмотром; ReturnLIVE показал текущий кадр. Новые QA-runs короче
15wall-мин и UI честно это показывает. Предыдущая полноценная15wall-мин проверка
с651позициями/pagination и реальными timestamps сохранена в H15-report; ускорение
clock не выдаётся за накопление15wall-мин истории.

CSV: настоящий browser download58246bytes/52split-lines (включая trailing newline),
raw actual/incidents/plans/timings, formula_version efficiency-v1 и units.
[Файл](../artifacts/h18-browser-report.csv). Read-only export не меняет State.

Config admin действительно создалversion1→3 (2уже существовала в другом run),
thresholds сохранены без изменения weights/timetable, replan успешен; новый run
унаследовалconfig3. Viewer new-run/config/control403; operator чужая inspection403.
Manual fixture main-compatible12/7: T1 inspection actual_start120, min_end360,
can_complete=true/resourceI1 остаётсяbusy; operator подтвердилactual_end361,
durable completion/replan, затем engine действительно начал dependent departure
в361 (движение не подтверждалось вручную). Старый run history200 после new-run.

В длинном прогоне обнаружены два крайних случая, исправлены и повторно проверены:

1. In-flight render report после logout получал401. Теперь stop/drain telemetry
   и stop SSE до revoke; stale auth-generation больше не отправляет reports.
   [Post-fix проверка](../artifacts/h18-postfix-browser.json):4logout/login,
   viewer/admin/dispatcher/admin, настоящий LAN-Vite/play/occupancy/actual,
   **0console/page errors,0HTTP>=400**, telemetry_errors0. ЧетыреERR_ABORTED
   соответствуют явному `EventSource.close()` при logout и имеютHTTP200 — это
   ожидаемая отмена транспорта, не ошибка номинального API.
2. Vite мог оставить half-open SSE после остановки upstream без onerror.
   Reference теперь reconnect при отсутствии heartbeat>3wall-с с прежним auth,
   backoff/cursor/reset. Не изменяет domain и не продвигает часы браузером.
   Проверено реальным PG/API restart без reload; proof ниже.

Первый полный raw acceptance сохраняет401до исправления и намеренный
ERR_INTERNET_DISCONNECTED; не выдаётся за zero-errors final. Chromium SDK также
фиксировал многоERR_ABORTED на204diagnostic requests, тогда как actual fetch
reports accepted и сервер missing0; raw не очищен. Номинальные final проверки
и deliberate denial/restart fault перечислены раздельно. Старый `setOffline`
не всегда разрывал уже открытый SSE (UI оставался connected), поэтому **не**
считается hard-disconnect proof; его заменяет настоящий restart-тест.
SLA-таблица измерена до малых logout/watchdog fixes; эти ветки не работают в
steady live, повторная functional проверка проведена, повторный foreground
120с после них не требовал пользователя оставаться на тестовом desktop.

## 5. Restart, clean migrations, normalization

Реально остановлен FastAPI, `docker compose restart postgres` без удаления
volume, API поднят снова. Финальная нетривиальная проверка: T1 наR1,
occupied188m/G1/TL1, op-T1-inspection running/I1busy, pausedsim137/config3.
Доrestartseq33/6checkpoints, после38/8: recovery/init planning добавили события,
но тот же run/sim/config/physical hash/actual. Историческийseq33 восстановлен
с исходным committed hash. Все6restart checks true; actual повторно рассчитан
из настоящей истории и равен записанному.

[До](../artifacts/h18-active-before-restart.json),
[после](../artifacts/h18-active-after-restart.json),
[browser](../artifacts/h18-restart-browser.json):offline/stale→connected без reload,
same run/sim137/config3, seq33→38. Наshutdown с открытым SSE после5с допустимого
graceful ожидания Uvicorn отменял stream task/логировал CancelledError; это
намеренный restart fault, не nominal runtime error или потеря committed data.

Чистая **дополнительная** БД `digital_station_h18_clean_final`:0tables→14,
Alembic upgrade/check passed, revision0006_telemetry_observation;
ready/login200,12tracks/7trains/2plans, initial replan833,345ms.
[Clean proof](../artifacts/h18-clean-migration.json). Helpers отказываются
очищать непустую БД; существующие test/demo DB/volumes не удалены.

[Normalization audit](../artifacts/h18-normalization-proof.json) показывает
8546accepted durable mock observations на момент аудита; raw/filtered progress
и source IDs реально сохранены. Негативные/repeated/out-of-order случаи —
автотесты на отдельной БД; не выдаются за реальные датчики или production faults.
Freshness5wall-с/future tolerance1с — учебное допущение. Render metrics bounded
15wall-мин/3000events×16clients, in-memory reset приrestart/new-run; State/events/
config/receipts/plans/timings/observations durable в PostgreSQL.

## 6. Проверки и screenshots

Финальный полный pytest:237passed/195,94с;11JS tests (9railway +2render lifecycle) passed.
Ruff check/format65files, mypy28sourcefiles, TypeScript strict v1.0/railway display,
station/render/QA JS syntax, Alembic upgrade/check и frozen docs/contracts прошли.
Один известный Starlette TestClient deprecation warning, не failed/skipped.
Конкретные команды/финальный результат — [checks](../artifacts/h18-checks.json).

Screenshots сохранены:

- [LIVE1×](../artifacts/h18-live-1x.png), [LIVE10×](../artifacts/h18-live-10x.png)
- [Batch5conflicts](../artifacts/h18-batch5-conflicts.png), [applied](../artifacts/h18-batch5-applied.png)
- [Batch10conflicts](../artifacts/h18-batch10-conflicts.png), [applied](../artifacts/h18-batch10-applied.png)
- [Replay](../artifacts/h18-replay.png), [operator/manual](../artifacts/h18-manual-operator.png)
- [12tracks SVG](../artifacts/h18-station-svg.png), [33Operation timeline](../artifacts/h18-operation-timeline.png)
- [LAN-Vite post-fix](../artifacts/h18-postfix-proxy-lan.png)
- [Restart offline](../artifacts/h18-restart-offline.png), [reconnected](../artifacts/h18-restart-reconnected.png)

Backend оставлен0.0.0.0:8000, PostgreSQL healthy, Vite5173; current main pausedsim137,
config3,12/7. Admin может начать fresh main из UI, старую историю не теряя.
Секретов в артефактах/Git/frontend нет.

## 7. Что ещё реально открыто

Ограниченный H18 backend/reference scope закрыт. Полная защита **не объявлена
готовой**: A16/основной frontend и два физических компьютера ещё требуют smoke;
R23/10–12слайдов/резервное видео/полная репетиция отсутствуют; A18/paired quality
comparison по шести одинаковым сценариям ещё не выполнен. Текущие live timings
не доказывают экономию/превосходство heuristic. Полноценная industrial safety,
СЦБ, реальные нормы/станция/датчики/соседняя станция вне учебного scope.

Остаток: **3–5ч** при уже готовом frontend и параллельной работе друга;
**5–8ч**, если основной UI ещё отсутствует. Это оценка задач, не обещание по
неизвестному времени защиты. Подробности/приоритеты в H18-дополнении PLAN_24H.
После этого отчёта **STOP H18**, новые функции/этапы не запускаются.
