# Backend v1.0 — завершение и freeze

Дата: 2026-10-02. **Backend готов для разработки основного UI; API/domain v1.0 FROZEN.**
Это приёмка учебного backend, не сертификация железнодорожного управления и не
объявление всей защиты готовой. Основной frontend и ноутбук друга пока недоступны.

## 1. Что реально завершено

Неизменный demo_main_v1: 12 путей, 7 поездов (6 грузовых/1 пассажирский),
60 вагонов, 19 ресурсов, 33 операции. Симулятор действительно исполняет три
грузовых DAG и passenger arrival→departure; factual location/occupancy/groups/
traction/resources меняются только на backend. Runtime guards, independent
validator, FCFS и bounded deterministic heuristic, отдельный planner process,
4 incidents/batch/coalescing/barrier/stale/CAS/autoapply/alternatives работают.

Закрыты auth/RBAC, session cookie, full-State SSE/reconnect/reset, actual KPI и
forecast, admin config, PostgreSQL history/checkpoints/read-only replay/CSV,
own manual service confirmation, noisy progress validation/dedup/out-of-order,
render telemetry/metrics, new-run/idempotency, recovery, health/ready/OpenAPI.
Все **25 HTTP endpoints** соответствуют таблице FRONTEND_HANDOFF и реальному
OpenAPI; SSE/CSV имеют правильные content types. KPI находится в State.efficiency,
отдельного `/efficiency` endpoint нет. [Полный endpoint/RBAC handoff](BACKEND_HANDOFF.md).

В этом финальном этапе добавлены только setup/проверки/документы и совместимые
уточнения: GET scenarios читает реально seeded каталог; уже существующие health/
metrics/explanation responses описаны DTO в OpenAPI; полный HTTP TS сгенерирован
из actual schema. Core api-v1.ts/domain/planner/validator/topology не переписаны.

Найден и исправлен критический fresh-manual edge case: control-only повторный
search без active plan ставил barrier до initial arrival и мешал исполнению
manual smoke prefix. Теперь redundant control-only search пропускается **только**
при MANUAL_CONFIRMATION_REQUIRED/no_feasible_plan без текущего worker; incidents/
config/реальные input changes по-прежнему перепланируются, guards не отключены.
Добавлены API regression и настоящий свежий browser flow. До подтверждения этот
fixture честно остаётся guarded smoke prefix, **не** validated полный план.

## 2. Clean start и durable recovery

[Clean-start proof](../artifacts/backend-v1/clean-start.json): дополнительная
пустая PostgreSQL digital_station_v1_clean_20261002b, **0→14 таблиц**, Alembic
upgrade/check, head0006_telemetry_observation. Setup дважды сохраняет тот же
run_id/reset=false; оба approved сценария готовы без ручной правки БД.
Далее реальный uvicorn/HTTP, не только TestClient: login/ready, snapshot12/7,
2 validated plans, play10×, изменившиеся physical operations/occupancy,
SSE, incident→replan→autoapply, actual KPI/config, 57 history events, replayseq1
без изменения live, настоящий CSV88081bytes. Smoke SSE8frames/4,418wall-с,
7межкадровых интервалов →1,5845Hz; это короткая проверка, не SLA120с.

Реально перезапущены API и PostgreSQL без удаления volume. Проверены main и
последний нетривиальный manual run: sim387, config4, T1 departure/T2 inspection
running, manual end361. [Финальный restart proof](../artifacts/backend-v1/restart-final.json)
содержит physical/actual/config и pre-restart history state equality. Live State
сохраняется PAUSED, downtime не увеличивает sim-time; startup validated replan
может заменить active_plan_id и добавить seq/revisions. Это не потеря физического
состояния. GET snapshot.server_time — transport wall-time; replay.server_time —
исходный committed wall-time. При сравнении REST снимка с commit исключён только
этот явно различающийся transport timestamp; сохранённый checkpoint не изменён.

Helpers отказываются очищать непустую БД. Существующие demo/test volumes не
сбрасывались. Ранние ошибки QA helper (неверное имя manual сценария, догадка о
несуществующем KPI endpoint, сравнение startup plan ID/transport timestamp)
исправлены в проверках, не ручными SQL updates. Неуспешная первая QA-БД сохранена;
успешный полный clean-start проведён в отдельной новой пустой БД.

## 3. Честный FCFS vs heuristic

[CSV](../artifacts/benchmark/results.csv), [summary](../artifacts/benchmark/summary.md),
[точные inputs/validators/engine history](../artifacts/benchmark/raw.json).
6 случаев ×2 algorithms, seed42/default config, одинаковые полные inputs/hash
в каждой паре. Normal sim0; disturbances — общий **FCFS-executed sim480**
checkpoint, те же H18 incident batches, frozen prefix и50sim-с barrier. Окно
качества обоих `[0,7200]`, event-jump physical execution, не browser clock.
Не менялись durations/priorities/incident targets, чтобы предпочесть heuristic.

| Сценарий | Actual index FCFS / heuristic | Delay mean,sim-с FCFS / heuristic | Wagon-hours FCFS / heuristic |
|---|---:|---:|---:|
|normal|95,2 /95,2|171,429 /171,429|25,133 /25,067|
|train_delay|96,4 /95,5|128,571 /154,286|24,467 /23,000|
|track_closure|95,2 /95,2|171,429 /171,429|25,133 /25,133|
|resource_loss|95,2 /95,2|171,429 /171,429|25,133 /25,133|
|burst5|96,4 /95,5|128,571 /154,286|24,467 /23,000|
|burst10|96,2 /96,0|137,143 /137,143|23,300 /27,100|

Везде7departed, throughput3,5trains/hour, конечные unresolved conflicts0,
independent validator passed, ledger60 сохранён. FCFS1feasible, heuristic2.
Resource idle ratio FCFS0; heuristic0,023121 в train_delay/burst5/burst10,
остальные0. J ниже в4 случаях, равен в2, но **actual index равен в3 и хуже в3**.
Heuristic оптимизирует J, не каждый фактор/actual index. Даже wagon-hours в
burst10 хуже — не скрыто. Track/resource windows в этом fixture часто заканчиваются
до основной будущей работы, поэтому равенство здесь не доказывает отсутствие
эффекта ограничений вообще. No universal improvement percentage claimed.

Отдельный [paired live timing control](../artifacts/benchmark/live-timings.json):
реальные REST/PG/actor/worker/validator/commit/SSE, общий validated FCFS plan,
**paused sim0 для обоих**, не sim480 trajectory качества. Только QA worker
выбирает существующий FCFS rollout; production planner не изменён. CSV явно
разделяет offline compute и live total elapsed. FCFS316–505ms,
heuristic1037–1152ms; все12succeeded/validated. Не смешивать эти checkpoints
или считать simulated barrier реальными wall-ms. Read-only CSV audit проверил
единицы/нулевые значения/null/hash пары/score contributions и сохранил worse cases.

## 4. Final SLA: реальные значения и границы доказательства

[Сводный машинный отчёт](../artifacts/backend-v1/final-sla.json).
Формальный foreground120wall-с на1× и10×, по2 headed Chromium clients,
workspace9, **из уже принятого H18 прогона 2026-10-01 18:47UTC**. Здесь эти raw
измерения явно переиспользованы, не выданы за новый foreground benchmark.
Финальный MCP E2E headless, чтобы окно не мешало пользователю; он подтверждает
функции, но не заменяет foreground SLA. Main steady State/SSE/planner/validator/
render path неизменен; fresh-manual control branch проверен отдельно.

UI ingress→render upper включает server/client clock calibration uncertainty;
double-rAF после DOM — proxy видимого paint, не датчик физических пикселей.
Initial/replay/reset/catch-up/heartbeat исключены из render samples;
heartbeat учитывается в SSE frequency. Скрытые вкладки не считаются foreground.

| Режим/клиент | SSE Hz | Render samples | p50 upper,ms | p95 upper,ms | Max upper,ms |
|---|---:|---:|---:|---:|---:|
|1× A|1,278222|128|149,020|231,020|361,613|
|1× B|1,278238|128|146,714|228,527|367,511|
|10× A|1,448660|177|84,942|166,613|289,613|
|10× B|1,448649|177|87,473|166,511|268,511|

Всего610 valid render samples; invalid/hidden/missing/>500ms=0.
Background negative run с throttling/~2088ms сохранён в H18 evidence, не спрятан
и не относится к foreground SLA.80 real replans: p50/p95/max total elapsed
**1248,354 /1336,437 /1369,717ms**, compute571,301 /610,885 /631,321ms,
deadline exceedances0. Batch5 **1306,855ms**, batch10 **1356,102ms**:
2feasible/validator/autoapply/SSE каждый; rendermax195,595 /146,942ms.
Новый функциональный browser incident replan **966,267ms**, clean-start1060,999ms;
это отдельные single samples, не дополнительные80 observations.

Environment: Linux7.1.8-arch1-3/glibc2.44, Python3.12.13, PostgreSQL16-alpine,
Chromium151.0.7922.137, AMD Ryzen7 5700U/16logical CPUs/~14GiBRAM.
Backend/PG/оба browser clients на **одном физическом host**. SLA результата
по реальной двухноутбучной Wi-Fi сети пока нет; не обещаем его по этим цифрам.

## 5. Playwright, actual KPI, manual и LAN

[Полный18-step proof](../artifacts/backend-v1/browser-proof.json):
login→new main run→play→T1arrival/inspection→T2realshuntingL1/G2L→P1ждётW
→incident/real conflicts→2validated alternatives→autoapply/diff→actual KPI
→history/replay→ReturnLIVE→CSV→manual alternative apply→admin config replan
→fresh manual run→operator own completion→dependent DAG→viewer denials
→PG/API restart→SSE connected без reload.

Passenger contention — безопасное ожидание занятой горловины, не незаконное
одновременное движение. В sim560 L1 на маршруте D1→H/progress0,8, G2L ещё R3:
SVG не телепортирует группу по плановому назначению. Own manual inspection
начался120, min end360, can_complete только после360; оператор завершил361,
durable событие, dependent departure реально стартовал361. Чужая операция403;
viewer control/config/new-run403. Роль не задаёт физический результат в browser.

Номинально **0 console/page errors,0HTTP>=400,0unexpected network failures**.
Четыре intentional403, restart ERR_CONNECTION_REFUSED и явные SSE close/
logout ERR_ABORTED записаны отдельно, не удалены из raw evidence.
Browser CSV [48695bytes/41data rows](../artifacts/backend-v1/browser-report.csv):
raw factors/incidents/plans/timings, formula_version/units. Replayseq1/sim0
против live736 не изменил live. Новая QA-сессия короче15wall-мин; настоящие
15wall-мин/pagination651items проверены в принятом H15, не имитируются10×clock.

Actual sample sim736/window[0,736]: score100/normal, throughput1ratio,
delay0sim_seconds, occupancy0,09402ratio, conflicts0count, idle0ratio —
все из engine/history; это маленькая due-cohort T1, не preset improvement.
Другой принятый реальный window[301,1201]: score96,7, delay120sim_seconds,
occupancy0,183333, loss3,333333points по delay. Empty cohort raw=null с reason,
не подставленный0; early score нельзя выдавать за полноту оценки пяти факторов.

[LAN proxy proof](../artifacts/backend-v1/lan-proxy-proof.json): настоящие
Chromium login/snapshot/nativeSSE/logout/cookie HttpOnly+Lax через127.0.0.1:5173
и10.63.52.9:5173, errors0. Same-origin/Vite proxy/CSRF allowlist сохранены,
backend0.0.0.0:8000, PostgreSQL loopback55432. Оба origins всё ещё одинhost.

Screenshots: [станция](../artifacts/backend-v1/station-viewport.png),
[манёвр](../artifacts/backend-v1/freight-shunting.png),
[пассажирский конфликт](../artifacts/backend-v1/passenger-contention.png),
[applied](../artifacts/backend-v1/incident-applied.png),
[replay](../artifacts/backend-v1/replay.png),
[operator](../artifacts/backend-v1/manual-operator.png),
[DAG](../artifacts/backend-v1/manual-dag.png),
[restart reconnect](../artifacts/backend-v1/restart-reconnected.png).

## 6. Checks и замороженный контракт

Финальный набор: **244 pytest**, **13 JS tests** (9railway/2render/2contract),
Ruff check/format69files, mypy29sourcefiles, TypeScript strict core/HTTP/display,
Alembic clean upgrade/check и current/check, stationJSsyntax, contractaudit,
git diff --check. Фактические exit codes/output: [checks](../artifacts/backend-v1/checks.json).
Один Starlette TestClient deprecation warning; failed/skipped нет.

[contracts/v1.0-freeze.json](../contracts/v1.0-freeze.json) фиксирует SHA256
OpenAPI/core+HTTP+display TS/State schema/all Python domain+API sources/migrations/
fixture manifests. Это файловый manifest, **не** выдуманный Git tag/commit.
`verify_backend_contract.py` проверяет actual routes/DTO/State schema/docs/fixtures/
hashes; AST JS checks —20domain interfaces и24enum sets.22captured real API
fixtures валидны; static fixtures явно synthetic/not solver/performance proof.
Оригинальные SPEC/RESEARCH и prefix PLAN/FRONTEND сохраняют утверждённые hashes
docs/V1.0.md. Исторические «not implemented» stages не переписаны задним числом;
актуальный backend status добавлен только в разрешённые appendices/handoff/report.

## 7. Ограничения, передача и STOP

Нет обязательных незавершённых **backend-функций в утверждённом v1.0 scope**.
Есть учебные ограничения SPEC: aggregate W/E, fixed train traction/roster,
bounded search/feasible≠optimal, синтетические duration/noisy observations,
никакой реальной СЦБ/датчиков/КТЖнормы/соседней станции. History durable24h,
replay15wall-мин; render metrics in-memory bounded15wall-мин и reset после restart.
Manual unknown end требует confirmation до полноценного tail plan.

Для **всей защиты** ещё обязательны основной UI/integration на ноутбуке друга
(A16),10–12slides/резервное видео/репетиция(R23). Paired comparison/A18 теперь
закрыт этим benchmark. При отсутствии основного UI reference/резервное demo
`/tech/station` работает; это не утверждение о готовности чужого продукта.

Другу передать репозиторий без `.env`, README, BACKEND_HANDOFF, FRONTEND_HANDOFF,
contracts/api-v1.ts +http-v1.ts +railway-display.ts +openapi.json, live/static
fixtures, .env.example/compose/uv.lock. Его работа — UI, relative API/SSE и
совместный smoke; не дописывать planner/state/KPI/guards в browser.
Секреты вводятся локально, demo accounts/roles указаны без настоящих паролей.

Остаток до полной защиты: **3–5ч**, если основной UI уже готов и друг работает
параллельно; **5–8ч**, если UI ещё отсутствует. В резервном demo-пути около
2–3ч на материалы/видео/репетицию, но без утверждения выполненного основного UI.
Это оценка задач, не известный deadline и не обязательство уложиться в него.

Backend/domain/API **FROZEN**: никаких новых features; только критические
совместимые fixes с явной review/повторной приёмкой и обновлением hashes.
**STOP. Gemini/virtual signals/новые этапы не начаты.**
