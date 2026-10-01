# Приёмка H5–H12 · 1 октября 2026

**H12 завершён. H12–H15 не начинался.** PostgreSQL и FastAPI оставлены запущенными,
симуляция на паузе. `/tech`: http://127.0.0.1:8000/tech/smoke.

Объём не увеличен: `demo_main_v1`,12 путей,6 FREIGHT +1 PASSENGER,60 wagon IDs,
19 ресурсов,33 операции. SERVICE/OTHER поддержаны типами и типонезависимым ядром,
но новые поезда/профили в основное демо не добавлены. API v1.0 не переименован:
реализованы ранее предусмотренные endpoints. Четыре утверждённых документа и
`contracts/api-v1.ts` неизменны; checksum/генерация контракта входят в тесты.

## Контрольные точки

| Checkpoint | Фактический результат |
|---|---|
| H5 gate | 70 pytest passed; Ruff/mypy/TS strict passed. Chromium: sim1290→1430 при10× без reload, изменились progress/occupancy/resources; SSE1,42Hz. [Результат](../artifacts/h5-gate/results.json) |
| H7 | 92 pytest passed; lint/types passed. Независимый validator и отрицательные планы; `/tech` показывает conflicts/operations/progress. Chromium: sim1430→1510 без reload. [Результат](../artifacts/h7/results.json) |
| H10 | 106 pytest passed; lint/types passed. Настоящий отдельный planner process; два feasible варианта, paused sibling apply и LIVE replan. Chromium: sim1510→1542, replan778,9ms, SSE3,43Hz. [Результат](../artifacts/h10/results.json) |
| H12, финальная версия | **132 passed**,0 failures/errors/skips;107,02s. Ruff passed,41 files formatted; mypy20 source files passed; TypeScript strict и JS syntax passed. [JUnit](../artifacts/h12/pytest.xml), [сводка проверок](../artifacts/h12/checks.json) |

Предыдущие checkpoint-артефакты — настоящие записи соответствующего прогона,
а не заново сгенерированная «история» последней версии. После финального audit
проверены least-scarce по числу операций и availability занятого pending-loss
ресурса до effective outage; добавлены отдельные regression tests.

## Что реализовано

**Validator** не импортирует simulator/planner и не вызывает runtime guards.
Имеет собственный physical replay и start/phase/end timeline. Проверяет connected
topology/catalog/allowed route legs, длину/capacity, двойное использование ресурсов,
track/zone locks и resident home, predecessor DAG, frozen running/completed DTO,
wagon/group identity/ownership/order, тягу/crew/location и outage/destination calendars.
Намеренно испорченные планы отвергаются; ошибка/deadline не дают safety certificate.

**Planner**: безопасный FCFS baseline плюс до12 deterministic rollout попыток:
FCFS/earliest due/urgency/release-R × ascending/descending/least-scarce. Urgency
может WAIT перед известным более приоритетным конфликтующим прибытием; это не
special-case PASSENGER. Перескакивает к событиям, не симулирует каждую секунду.
Baseline и эвристики получают одинаковый serialized input/digest/ограничения.
Каждый candidate проходит независимый validator; J, пять forecast факторов и diff
считаются только после проверки. Два различных feasible кандидата сохраняются,
если найдены; оптимальность не заявляется. Дополнительные перестановки и CP-SAT
не добавлены.

**Actor/coordinator**: bounded queue, отдельный заранее запущенный CPU worker без
live DB session,150ms coalescing, общий deadline5wall-s от первого ingress,
owner launch barrier/cutover. Running prefix продолжает фазы и завершения.
Повторный trigger наследует barrier deadline; старый result не снимает чужую
блокировку. Run/input/config/base active plan/digest/actual prefix проверяются,
затем выполняется PostgreSQL CAS. Future assignments заменяются, факты сохраняются.
Manual apply допускает только свежий paused sibling; повтор command receipt
не создаёт второй эффект, path resolve/apply входит в hash.

**Incidents**: train_delay,track_closure,resource_loss,destination_block; атомарный
batch1..10, affected_operation_ids и conflicts старых назначений, automatic replan
и apply лучшего актуального feasible плана, второй вариант для сравнения.
Занятые пути/ресурсы получают pending; outage начинается после неперерываемой
работы. Resolve pending отменяет ограничение, но не отматывает ETA после delay.
Expiry и runtime guard failure также автоматически запускают пересчёт.

**Persistence**: State/events/checkpoints/changed incidents/plans/jobs/receipts
сохраняются транзакционно; SSE после commit. Exact input записывается в job JSONB
до CPU launch. Есть отдельные incident/plan/optimization_run таблицы. Старые run
и результаты сохранены. Это substrate history, не готовый history/replay API.

**Tech client**: настоящий snapshot/SSE, роли, LIVE/PAUSED/time/speed/seq,
occupancy/location/resources/operations/progress, conflicts, event log,
incident JSON batch/resolve, replan status/detail/validator/forecast/explanation,
paused alternative apply. Нет своей физики/планирования/KPI расчёта. При потере
соединения команды сразу запрещены, затем отображаются STALE/OFFLINE; reconnect
восстанавливает backend State.

## Измеренные Chromium прогоны

Системный Chromium **151.0.7922.137**, Playwright MCP. Номинальные прогоны:
0 page errors,0 failed requests,0 неожиданных HTTP errors; console/network logs
сохранены рядом со screenshots. Измерения ниже — отдельные реальные samples,
не нагрузочная статистика p95 и не обещание одинакового времени на другой машине.

| Прогон | End-to-end elapsed | CPU search / validator | SSE receive Hz | Максимальный gap |
|---|---:|---:|---:|---:|
| Single I1 loss, LIVE | 902,3ms | 370,0 /166,7ms | 1,91 | 808,5ms |
| Busy L1 loss, running shunt | 873,5ms | 369,1 /182,9ms | 3,87 | 804,8ms |
| Batch-5, sim480→521 | 896,7ms | 391,3 /170,7ms | 2,94 | 807,1ms |
| Batch-10, sim480→601 | 919,9ms | 394,9 /177,4ms | 2,16 | 841,8ms |
| Последняя версия, manual LIVE replan, sim601→688 | 959,6ms | 471,5 /169,8ms | 2,27 | 840,7ms |
| Steady paused heartbeat,6,5wall-s observation | — | — | **1,248** | 803,3ms |

`elapsed_ms` измеряется backend monotonic от первого ingress до первого durable
plan publication после commit, включая debounce/queue/validation/CAS/DB/SSE.
Финальный actor prefix check занял **5,05ms**; данные сохранены в job metadata.
SSE frequency=`(frames−1)/(last_received−first_received)`, измерена Chromium
performance clock. Для single-run отдельно считались непрерывные LIVE отрезки:
пауза между ними не превращена в ложный gap. Heartbeat не увеличивает durable seq.

Артефакты: [single/pending](../artifacts/h12/single-live/results.json),
[batch-5](../artifacts/h12/batch-5/results.json),
[batch-10](../artifacts/h12/batch-10/results.json),
[финальный прогон](../artifacts/h12/final-verification/results.json).
Снимки: [batch-10](../artifacts/h12/batch-10/tech-chromium.png),
[финальное состояние](../artifacts/h12/final-verification/tech-chromium.png).

В конечном browser-прогоне действительно изменились backend occupancy и L1 location,
progress/phase, clock и seq; вагонные IDs остались прежними. В pending-loss прогоне
running `op-T2-shunt-out` сохранил actual start540 и продолжал движение после
automatic apply. В batch-5/10 кадры содержат queued→running→succeeded, все incident
IDs, conflicts до apply и plan_applied с новым active_plan_id; два validator reports
passed. Демонстрационный цикл выполнен, не собран из fake UI transitions.

Logout→viewer login, disabled buttons и прямой POST incidents→403 проверены,
затем повторный dispatcher login. [Role/steady результат](../artifacts/h12/auth-reconnect/results.json).
`context.setOffline` не разорвал уже открытый SSE socket — этот опыт не засчитан
как disconnect. Затем проверен **настоящий stop/start API**: OFFLINE, disabled
commands, recovery без page reload, сохранены тот же run/sim601/10 incidents.
Дополнительно network-abort SSE connection дал RECONNECTING→STALE→LIVE без reload
и fake State. [Restart](../artifacts/h12/server-restart/results.json),
[transport reconnect](../artifacts/h12/transport-reconnect/results.json).
403/connection refused/aborted requests в этих отрицательных проверках ожидаемы;
не смешаны с номинальными error counts. При server shutdown открытого SSE uvicorn
достиг graceful timeout5s и отменил stream; recovery проверен после этого.

## Автоматические проверки и чистая БД

| Модуль | Pytest cases |
|---|---:|
| API/auth/roles/recovery | 26 |
| Frozen contracts/fixtures | 12 |
| Simulator/physics/guards | 24 |
| Actor/clock/SSE/CAS/rollback/replay substrate | 8 |
| Independent validator | 22 |
| Planner/FCFS/objective/determinism/least-scarce | 8 |
| Real process/planning API/stale/certificate/watchdog | 8 |
| Incidents/calendars/atomic batch/coalescing/expiry/guard replan | 24 |
| **Всего** | **132** |

В watchdog тестах настоящий worker остановлен SIGSTOP: timeout отличается от
no_feasible_plan, SSE heartbeat продолжает идти, процесс убит, следующий job
создаёт новый worker и succeeds. На новом input старый job становится stale;
его поздний result не применяется. Повреждённый operations certificate не применяется.
Для no_feasible fixture R3/R4 закрыты на7200s: нет новых alternatives/applied plan,
старые недопустимые планы не объявляются feasible. `no_feasible_plan` здесь —
результат ограниченного поиска, не доказанная математическая infeasibility.

Одна warning — Starlette TestClient/httpx deprecation; failures нет. Python mypy
проверен с check_untyped_defs/Pydantic plugin, не в strict/no-Any режиме.
TypeScript strict проверяет общий `contracts/api-v1.ts`, не отсутствующую сборку
frontend друга; технический клиент — обычный HTML/JS, проверен node и браузером.

Новая `digital_station_h12_clean_test`:0→13 public tables, migrations0001..0004,
Alembic check passed, ready200/login200, snapshot12 tracks/7 trains/2 native plans.
Никакая существующая БД не очищалась. [Результат](../artifacts/h12/clean-database.json).
Основная БД: head0004, `No new upgrade operations detected`. Из реальных events
восстановлен точный текущий State; независимая проверка остаточного плана passed.
[Итоговый DB audit](../artifacts/h12/database-audit-final.json).

## Что ещё mock / не реализовано

Учебные/synthetic: topology/scenario/durations/ETA, next-station reception calendar
и travel600s, fixed smoke timetable и static `fixtures/api/v1`. Это не данные и
команды реальной железной дороги. Smoke остаётся историческим fixture/fallback,
не выдаётся за optimizer certificate; активные runtime plans настоящие.

**Не реализовано**, а не скрытый mock: live actual KPI/index и дополнительные
агрегаты, config PATCH, history/replay endpoints/UI, CSV/retention24h, manual
service confirmation, полноценная telemetry/dedup/smoothing pipeline, UI paint
latency telemetry, внешний network integration и AI/LLM. Main React/SVG/Gantt
frontend здесь не строился. Полный passenger workflow и SERVICE P2 не добавлены.

Watchdog replacement lazy: новый process запускается следующим расчётом; до этого
worker readiness503 при живых actor/SSE. Индекс actual остаётся null. Forecast
не объявляется фактическим улучшением. Финальные R16/R17 load trials120s/20 repeats,
ingress→paint<500ms, два видимых клиента под нагрузкой и весь проект P0 ещё не
сертифицированы — это следующие разрешённые этапы, не результат этих samples.

## Критерии H12 и фактическое состояние

Все критерии **данного H12 этапа** из последней команды закрыты: четыре kinds,
batch1..10/5/10 без зависания, coalescing, barrier, frozen prefix, stale protections,
CAS, automatic best feasible apply, second alternative, explanations/diff,
independent validation, живой SSE и browser end-to-end. Непрошедших обязательных
H12 критериев не обнаружено. Это не объявление всего hackathon проекта завершённым.

Текущий run `run-97235c10-7b29-4bcb-a50a-8c47438e587c`: **paused sim688,seq81**,
10 incidents,2 текущих feasible plans; история предыдущих трёх run сохранена.
Новый run sim480 готовится только отдельным additive utility при остановленном
API; публичный reset/new run API не переносился из следующего этапа.

Основные добавленные файлы:

```text
src/digital_station/
  validator.py       independent timeline/inventory/calendar checks
  planner.py         FCFS + bounded event-jump multi-strategy search
  forecast.py        forecast efficiency-v1 / J / changes / explanations
  worker.py          isolated process / hard watchdog
  planning.py        actor-owned lifecycle / barrier / stale checks / apply
  incidents.py       atomic domain effects / affected IDs / conflicts
  calendars.py       simulator outage calendars
  runtime.py         actor / clock / persistence / runtime guards / SSE
  api.py            v1.0 endpoints / auth / /tech
  static/smoke.*     real minimal API/SSE client
migrations/versions/0003_planner.py,0004_incidents.py
tests/test_validator.py,test_planner.py,test_planning_api.py,test_incidents.py
scripts/prepare_h12_demo.py,check_clean_h12.py,h12_status.py
contracts/openapi.json
artifacts/h5-gate/,h7/,h10/,h12/
```

**Остановлено после H12. Продолжение — только по следующей команде.**
