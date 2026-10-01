# H12–H15: фактическая приёмка

Дата: 2026-10-01. Выполнен **последний ограниченный backlog пользователя**: actual KPI, config, history/replay, CSV, manual service confirmation, RBAC и их проверки. H15–H18 не начат. Исходные SPEC/RESEARCH и нормативные части handoff/plan v1.0 сохранены; TypeScript v1.0 не менялся. Топология12 путей/7поездов, core planner и independent validator не переписаны. БД/FastAPI оставлены запущенными.

## Что действительно работает

| Критерий последнего задания | Реализация / проверка |
|---|---|
| Actual5факторов | `efficiency.py`: throughput/delay по due-cohort, физическая track-occupation integral R/S/C, unresolved unique conflicts, demand-conditioned pool idle. Серверный actual score/category/contribution, `efficiency-v1`, rolling900sim-с; null не заменён0/100. Forecast вариантов остаётся отдельно. Unit-tests считают известные engine integrals, проверяют sampling independence, нулевое окно, пустую когорту, веса и отсутствие activity |
| Config | GET current run config; admin PATCH полных nested objects. Bounds/sum/finite/ordered thresholds/strict integer limits, неизвестные поля и null422. ConfigRevision/current State/event/receipt/request атомарны. Version и input_revision растут; config_changed replan использует новые параметры и валидирует варианты |
| History/replay | PostgreSQL events и snapshots, indexes0005; GET history с exclusive pagination/window/anchor и GET exact seq snapshot. Replacement-effect reducer, без simulator/planner. Repeatable-read readonly transaction. Retention24h с anchor+последующими effects; paused current KPI дополнительно защищён sim-anchor. Никаких DB-записей на heartbeat |
| Reference UI | `/tech/station`: actual table, «История», event list/slider, сохранённый State, Return LIVE, CSV, folded config JSON, назначенное manual action. Replay readonly; SSE live идёт в фоне и не заменяет выбранный кадр. Исходная railway схема не переделана |
| CSV | UTF-8 attachment по run/wall-window. Summary/factor/incident/plan/timing из durable facts; raw KPI, normalization/weights/contribution/config/window/formula_version и явные units. Null пустой, текст защищён от spreadsheet injection |
| Manual/RBAC | Только manual inspection/cargo/departure_prep, operator assigned user или admin. Backend can_complete после predecessors/min-duration/locks. До подтверждения running+busy сохраняются; движение/auto409, чужая операция403, viewer/dispatcher403. Durable human event + ready DAG + automatic replan; реальное зависимое departure прошло |

Новые основные файлы: [efficiency.py](../src/digital_station/efficiency.py), [history.py](../src/digital_station/history.py), [reporting.py](../src/digital_station/reporting.py), [services.py](../src/digital_station/services.py), [0005_history_indexes.py](../migrations/versions/0005_history_indexes.py). Actor остаётся единственным writer. Pure reduction/integrals/CSV вынесены в thread **только как read-only вычисления**, без новой инфраструктуры, DB mutation или второго planner.

Manual сохраняет неизвестность человеческого ответа: до подтверждения полный поиск возвращает `no_feasible_plan/MANUAL_CONFIRMATION_REQUIRED`, не прогнозирует завершение человеком. Отдельный разрешённый `manual-control-v1` имеет те же12путей/7поездов, только inspection T1 manual/u-operator; контрольное начальное расписание явно smoke fixture. После подтверждения оставшийся известный auto-DAG планируется обычным planner+validator. Demo_main остаётся auto и не менялся.

## Реальный actual sample, не обещанный процент улучшения

Run `run-513160d5-fb78-4b87-915b-827be2d1f920`, seq1031, paused sim3512; actual window `[2612,3512]`, config2, formula `efficiency-v1`.

| Фактор | Raw / unit | Нормированный штраф | Вес | Потеря пунктов |
|---|---:|---:|---:|---:|
| throughput |1 ratio|0|0,30|0|
| delay |0 sim_seconds|0|0,25|0|
| occupancy |0,3771111111 ratio|0|0,15|0|
| conflicts |0 count|0|0,20|0|
| resource_idle |0,1052631579 ratio|0,1052631579|0,10|1,0526315789|

Score **98,9 / normal**, вычислен как round(100−1,0526315789,1), не задан в fixture. Когорта этого окна — T2. Это результат одного настоящего окна, **не доказательство экономии/превосходства алгоритма**. [Committed audit](../artifacts/h15-before-restart.json) независимо восстанавливает State и повторно вычисляет actual из истории. Финальный отдельный manual run имеет иной actual/window и не сравнивается с этим score.

## History/replay и restart proof

Chromium запрашивал точный15wall-мин window `16:23:46.776Z–16:38:46.776Z` 1октября.651позиция с anchor, pagination>500; первый anchor seq225/sim1737 предшествует window, поскольку станция долго была paused. Это правильно восстанавливает состояние на начале окна. Никаких production timestamps не backdated. Replay seq225/sim1737 оставался неизменным, когда backend live дошёл доseq878/sim3358; Return LIVE восстановил текущий кадр. Предыдущая ручная проверка также выбирала промежуточный seq317/sim2275 из event list. Slider использует тот же snapshot endpoint.

Run имел1031event/41checkpoint перед restart. Реально остановлен FastAPI, выполнен `docker compose restart postgres` **без down-v/удаления volume**, затем запущен FastAPI. После PG restart committed hash остался `74ca9db5c0e9d224cf845e92ed21ea65450d54f9367744f427c29d493f236927`. После API startup — тот же run/sim3512/config2, неизменные physical signature и actual; сохранённый seq1031 даёт прежний hash. Current сталseq1036/43checkpoint вследствие ожидаемых recovery+init replan commits, paused; downtime не прибавлен к sim-time.

- [До restart](../artifacts/h15-before-restart.json)
- [После PostgreSQL restart](../artifacts/h15-after-postgres-restart.json)
- [После API recovery](../artifacts/h15-after-api-restart.json): все6restart_checks=true
- [Final manual State audit](../artifacts/h15-final-state.json): replay_equals_committed/actual_equals_recorded=true

Retained checkpoints/effect chain проверены также synthetic aged fixtures **только в isolated test DB**: closed run удаляет старые записи, не разрушая доступный snapshot; открытый paused run сохраняет KPI-anchor. Разрыв effect chain даёт409 HISTORY_GAP. Это тест24h retention, не fake live history.

## CSV proof

[Файл, скачанный реальным Chromium](../artifacts/h15-report-main-window.csv):50строк —22summary,5factor,9plan,2incident,12timing; все `formula_version=efficiency-v1`. Units: ratio/sim_seconds/count/points/percent/trains/hour/wagon-hours/wall_ms/status. Window `16:23:49.764Z–16:38:49.764Z`, anchor225→end878, sim1737→3358, config2. Факторы пересчитываются из этого selected durable window, **не копируются из rolling live window**. Plan/incident rows — исторические изменения сущностей, не mutable сегодняшние records. В pytest проверены raw value, window context, paused zero-duration null и formula injection text escaping.

## Manual proof

Номинальный повтор после исправления readiness: run `run-2c555a95-8bb8-40c6-bb40-f1bdd2d140b4`, inspection T1 started120, minimum360. Operator видел disabled приsim121; приsim360 can_complete=true и I1 всё ещёbusy/active_operation_id=op-T1-inspection. Кнопка вызвала настоящий POST; actual completion365, durable seq61, actor=u-operator, T1 ready_departure.

Automatic manual_confirmation replan `rp-503a2bdf-ca78-4e96-a9aa-e98a20839f0c`: **1077,22ms elapsed**,532,55ms compute,193,15ms validation; **2feasible variants**, оба validator passed, лучший autoapplied. Дальше настоящий engine выполнил departure415→535, T1 location=departed. Это не ручной fake transition. Browser отрицательные случаи: до min409, чужая inspection403, admin shunt/auto completion409. API tests проверили дополнительно viewer/dispatcher403, administrator допустимую manual, receipt retry и удержание busy после min.

До финального повтора Chromium нашёл две проблемы, исправленные и повторно проверенные: большой read-only export задерживал loop; manual confirmation не маркировал readiness поезда при blocked departure и давал stale prefix. Первый устранён thread offload, второй — фактическим backend update readiness при manual completion, без изменения solver/validator. Regression проверяет равенство engine/independent prefix и продолжение DAG. Неуспешный первый browser wait не выдан за успешный E2E.

Текущий API оставлен **paused sim890** на этом manual run; основные live результаты/demo_main/history/config2 сохранены в прежнем run. Для следующего manual повторения существует helper; он отказывает при активном writer и не удаляет историю. Отдельные fixture runs стартуют с baseline config1; main config2 не потеряна.

## Проверки и Chromium artifacts

Финальный полный `uv run pytest -q`: **217passed**,184,21s, один известный Starlette TestClient deprecation warning (не failed/skipped). В том числе178прежних regression +39новых H15. Дополнительно9JavaScript display/SVG tests passed. Ruff check/format, mypy25sourcefiles, TypeScript strict `api-v1.ts`/`railway-display.ts`, station.js syntax и Alembic check passed.

Clean migration: **новая** `digital_station_h15_clean_test`,0tables before→13after, upgrade/check passed, head0005_history_indexes; ready/login200, snapshot12tracks/7trains/2plans. Существующие БД не очищались. Одноразовый helper повторно откажет на непустой БД — не применяйте drop ради повтора.

[Санитизированные результаты Playwright](../artifacts/h15-browser-results.json): login/LIVE/actual, history/event selection/read-only freeze/ReturnLIVE, реальный CSV download, viewer403/admin config+invalid422, incident→replan, operator min-duration/confirmation/validator/autoapply/departure. Номинальные проверки — без console/page/network errors. Намеренные403/409/422 и отменённые соединения при logout/restart ожидаемы, отдельно указаны, не скрыты как0errors.

[Финальный slider-check](../artifacts/h15-slider-final.json) выбрал manual confirmation seq61/sim365 при live seq146/sim890. Play/config disabled, backend seq/sim не изменились; ReturnLIVE снова показал146/890. Chromium проверил12SVG `.railway-track`,7trains и19resources. [Checks summary](../artifacts/h15-checks.json). Повторная проверка H15+frozen contracts после документации:51passed/47,58s.

SSE после исправления: во время полного durable CSV export (HTTP200,3731,9ms,111158символов) Chromium получил21кадр; observed15,4816s, **1,291856Hz**, maxgap950,4ms. Это короткая paused receive-проверка с нагруженным read endpoint, **не финальная ingress→paint<500ms/120s/2clients приёмка**. Live/changes дополнительно наблюдались в manual и main replay tests без reload. На API shutdown с открытым SSE был timeout graceful shutdown5s/cancel; committed-state proof прошёл, crash/DB-failure acceptance этим не подменена.

Screenshots:

- [LIVE + actual KPI](../artifacts/h15-live.png)
- [Replay](../artifacts/h15-replay.png)
- [Config/admin](../artifacts/h15-config-admin.png)
- [Incident/KPI](../artifacts/h15-incident-kpi.png)
- [Manual до min](../artifacts/h15-manual-before.png), [manual ready/busy](../artifacts/h15-manual-ready.png), [завершение + DAG](../artifacts/h15-manual-completed.png)
- [Manual replay slider](../artifacts/h15-manual-replay-slider.png)
- [Финальный station viewport](../artifacts/h15-final-station-viewport.png)

## Что остаётся обязательным / оценка до защиты

Не выполненные mandatory хвосты проекта: основной frontend/Гант/LAN integration; noisy input normalization/dedup/out-of-order; `/time`, `/telemetry/ui-render`, `/metrics` и настоящая paint instrumentation; admin POST `/runs`; полная численная нагрузочная приёмка120s/2clients/20replans + честный paired FCFS benchmark; финальный README/manual/10–12слайдов/демо/резервная запись. Некоторые из них были в широкой исходной строке H12–H15, но последняя команда ограничила эту итерацию шестью приоритетами — они **не объявлены необязательными или выполненными**.

По актуальному [PLAN_24H](PLAN_24H.md#контрольная-точка-h15--по-последнему-ограниченному-разрешению): ориентир **6–8ч** с параллельным frontend и резервом; если frontend ещё не построен или SLA выявит дефект — больше. Оценка условная, не фактически доступный остаток24часов. Scope H15 закрыт; автоматического начала H15–H18 нет.

Mock/учебные остаются: synthetic station, durations/priorities/ETA/destination calendar/travel600sim-с и старые offline API fixtures. Manual smoke bootstrap — явно заданное контрольное расписание до человеческого подтверждения; actual KPI/history/CSV/config/RBAC/manual event/continued departure **не mock**. Gemini, virtual signals, новые поезда/topology/СЦБ/CV/RFID/OR-Tools/scaling не делались.
