# Railway-oriented UI: фактический результат после H12

Дата: 1 октября 2026 года. Граница задачи: предметная проверка и компактный визуальный reference поверх работающего H12; **не приёмка всего оставшегося ТЗ**.

## Результат и запуск

Работает [станционный экран](http://127.0.0.1:8000/tech/station). `/tech` и `/` ведут сюда; `/tech/debug` ведёт на сохранённый `/tech/smoke`. PostgreSQL и FastAPI оставлены запущенными. Последний проверенный run — `run-513160d5-fb78-4b87-915b-827be2d1f920`, пауза на sim995, после двух учебных инцидентов. Пароли только в существующей локальной `.env`, не в документах/браузерном коде.

Не изменены: 12 путей, 7 поездов, 60 вагонов, 19 ресурсов, 33 операции, профили грузовой работы, архитектура actor/process/SSE, PostgreSQL, API v1.0 и RBAC. FCFS/heuristic не заменены. Не добавлено зависимостей или миграций.

## 1. Предметная достоверность и источники

[RAILWAY_DOMAIN_REVIEW.md](RAILWAY_DOMAIN_REVIEW.md) отделяет нормативную терминологию, конкретное должностное описание КТЖ и наши допущения.

- «Дежурный по станции» выбран для поездного положения/приёма/отправления по [ПТЭ РК](https://old.adilet.zan.kz/rus/docs/V1500011897). Это display persona роли `dispatcher`, не переименование RBAC.
- Разделение маневрового диспетчера, составителя и локомотивного персонала опирается на [ИДП РК](https://old.adilet.zan.kz/rus/docs/V1100007021) и [описание обязанностей КТЖ](https://job.railways.kz/vacancy/55342). Совмещение поездного и маневрового планирования одной persona — учебное упрощение.
- «Подача/уборка вагонов», грузовой фронт и отличие следующей станции от станции назначения груза сверены с [Правилами перевозок грузов РК](https://old.adilet.zan.kz/rus/docs/V1900019188). D1 подписан как стоянка, не депо; CG1 выполняет именно погрузку текущих групп unloaded→loaded.
- I1/I2 показаны как осмотрщики, TC/TCP — локомотивные бригады, TP1 — встроенная тяга. Профессии не стали отдельными кабинетами/ролями.

Шымкент не копировался. Геометрия, длины, длительности, приоритеты, веса, укрупнённые горловины, SH1, осмотр/подготовка, окно соседней станции и600sim-с пути остаются **нашими учебными данными**. При аварии занятый ресурс не исчезает мгновенно: недоступность после завершения текущей работы — ограниченная модель H12, не норматив аварийного реагирования.

## 2. Что изменилось на экране

- Data-driven SVG из существующего State/layout: R1–R4, S1–S4, C1/C2, H, D1, W/E, BW/BE.
- Раздельно показаны фактическая занятость, резервация операции, закрытие/ожидание закрытия, горловинный lock и активный маршрут. Цвет дополнен надписями, символами, формой линии.
- Поезда, отцепленные группы и L1 рисуются по backend location/route_progress. Присоединённые группы и поездная тяга не дублируются вторым объектом. Назначенный путь не становится фактическим положением.
- Поездное положение: ID/номер, русские тип и профиль, направление, подход/путь/движение/отправлен, ETA, срок и план отправления, backend delay с отметкой факт/прогноз, операция, путь, приоритет.
- Семь фаз манёвра переведены на русский; видны L1, SH1, группа и W/H. Цепочки операций показывают реальные статусы, времена и progress. Это **не полноценный Гант**.
- Ресурсы показывают доступность, текущую работу/поезд; ограничения — затронутые объекты и человеческую причину. Закрытый свободный путь не подписывается как физически занятый.
- Две настоящие альтернативы: independent validator, J, прогноз индекса/пять факторов, backend diff и текущая доступность apply. Клиент не сортирует планы заново и не придумывает причину победы.
- Главный экран скрывает seq/revisions/CAS/HTTP. Raw JSON и журнал текущего подключения свёрнуты, старый debug оставлен. Потеря связи/устаревание отключают команды. Logout очищает State и DOM.

Основной frontend может переиспользовать `railway.js`, геометрию и словарь, связанное выделение, панели и REST/SSE interaction flow. Самодостаточное описание добавлено в [FRONTEND_HANDOFF.md](FRONTEND_HANDOFF.md#дополнение-после-h12-railway-oriented-ui-reference). Vanilla-JS reference не навязывает другу framework или транспортную реализацию.

## 3. Необходимые backend-изменения

1. Аддитивный read-only `GET /api/v1/replans/{job_id}/explanation`: diff относительно **сохранённого входа**, задержки departure и deterministic пояснение. Нельзя корректно получить «до» из уже изменённого live State. Endpoint не пишет State/БД, не запускает planner/validator, не вызывает LLM; доступен читающим ролям в текущем run.
2. Исправлена давняя несовместимость с handoff: `actual_arrival_sim_s` — завершение приёма, не начало маршрута. Исправлены executor и независимый validator.
3. Исправлена совместимость `passenger_transit_v1`: R1/R2 вместо произвольного R. Planner, validator и runtime guard проверяют **профиль**, а не `Train.type`. Четыре прикладных типа остаются допустимыми.

Последние два пункта — восстановление согласованного контракта, не новые правила КТЖ. Старые run/факты не переписывались; подготовлен новый прогон реальным executor. `contracts/api-v1.ts` и исходные четыре документа v1.0 сохранены; handoff/plan расширены только за отдельным маркером. OpenAPI дополнен фактически реализованным endpoint, дополнительные типы лежат в `contracts/railway-display.ts`.

## 4. Проверки

| Проверка | Фактический результат |
|---|---|
| Полный pytest | **178 passed**, 0 failed/skipped, 138,587с; [JUnit](../artifacts/railway-ui/pytest.xml). Один внешний Starlette/httpx deprecation warning |
| Дополнительная регрессия API/документов | contracts/fixtures + presentation + API: **53 passed**, 56,97с; после финального display mapping ещё12 contract checks и9 JS tests passed |
| Ruff lint / format | passed; 46 файлов отформатированы |
| mypy | passed, 21 source file по настройкам проекта |
| TypeScript strict | passed: `api-v1.ts` + `railway-display.ts`; это проверка контрактов, не checkJs всей vanilla-JS страницы |
| JavaScript | node syntax check station/smoke; **9/9** tests railway mappings/SVG/geometry/identity/no mutation |
| Истории A/B/C | 4 pytest-проверки и отдельный повторяемый [JSON evidence](../artifacts/railway-ui/stories.json) |
| Chromium / Playwright MCP | Реальный PostgreSQL/FastAPI, вход ДСП, 12 путей/7 поездов, play10×, движение/фазы/занятость, два incidents, alternatives, autoapply, SSE без reload |
| Ошибки nominal браузера | **0 console errors/warnings, 0 page errors, 0 failed requests**; [console](../artifacts/railway-ui/console-final-nominal.log), [network](../artifacts/railway-ui/network-final-nominal.log) |
| Роли | Logout очистил DOM; viewer видит State, кнопки disabled, POST play возвращает403 и не меняет State; повторный dispatcher login успешен. [Результат](../artifacts/railway-ui/role-browser.json) |
| Ширина | 1600px визуально проверено; при1280/390px ширина документа не превышала viewport. Это не полноценная мобильная приёмка |

Состав новых46 Python-тестов: timestamp прибытия1, технологические истории4, presentation/auth/read-only15, совместимость профилей26. Существующие132 также прошли.

### Измерения именно этого прогона

Формула receive Hz: `(N−1)×1000/(last_received_ms−first_received_ms)`, часы браузера monotonic. [Raw frames/State/plans](../artifacts/railway-ui/browser-final.json).

| Окно | Результат |
|---|---|
| PAUSED heartbeat | 8 frames /5,6071с между первым и последним → **1,248 Гц**, max gap802,7мс |
| LIVE от480 до831sim-с | 68 frames → **1,904 Гц**, max gap919,1мс |
| LIVE от831 до995sim-с | 29 frames → **1,725 Гц**, max gap849,5мс |
| Frames со статусом queued/running двух расчётов | 12 frames →3,150Гц на этом коротком окне; max gap802,5мс |
| R2 track_closure | elapsed **1635,9мс**; compute822,3мс; validation316,3мс; 2 feasible, autoapply |
| I1 resource_loss | elapsed **1736,6мс**; compute864,0мс; validation341,3мс; 2 feasible, autoapply |

Elapsed взят из backend ReplanJob, включает ожидание/coalescing до завершения, не только worker compute. Эти sample не являются20повторами,120с/двухклиентной приёмкой или измерением ingress→paint<500мс. Полная численная проверка остаётся MUST. Фактическое увеличение частоты на transitions не заменяет steady heartbeat.

### Пройденный live-сценарий

`PAUSED sim480 → play10× → T2 shunt-out/L1 на маршруте → R2 закрыт → P1 affected/conflicts → queued/running → 2 feasible → autoapply/P1 назначен R1 → I1 loss pending → 2 feasible → осмотр T4 I1→I2 → SSE → pause → play → G2L на C1/cargo running, L1 на D1, I1 unavailable → pause sim995`.

Идентификатор документа браузера до/после live-цикла совпал; не было reload или подмены snapshot. В последних вариантах A: J0,260745, индекс93,9; B: J0,264438, индекс94,4. Рекомендован A по меньшей **общей J**, не по одному индексу. Различия реальные: A меняет1 операцию, B —9 относительно входа второго расчёта.

## 5. Скриншоты

- [Схема и поездное положение](../artifacts/railway-ui/11-station-reference.png).
- [Движение L1 без reload](../artifacts/railway-ui/04-station-live-shunting.png).
- [Инцидент, конфликт и очередь расчёта](../artifacts/railway-ui/05-incident-replan.png) — сделан до заключительной правки подписи «Конфликт использования пути».
- [Группа и локомотив на маршруте, закрытый R2](../artifacts/railway-ui/06-station-applied.png).
- [Грузовая операция и недоступный I1](../artifacts/railway-ui/10-resources-and-restrictions.png).
- [Цепочки приёма, подачи/уборки, погрузки и формирования групп](../artifacts/railway-ui/13-operation-chains.png).
- [Финальные варианты и diff](../artifacts/railway-ui/12-plans-reference.png).

Файлы01/02 и `pilot.json` — промежуточный пилот **до** исправления допустимых путей passenger profile; не использовать их как окончательное доказательство. Заключительная правка только подписей повторно проверена входом/снимком/console в [console-reference.log](../artifacts/railway-ui/console-reference.log).

## 6. Три истории и их честные ограничения

**A — местная работа T2.** Полный цикл от approach до departure воспроизведён offline текущими engine/validator; все60 wagon IDs сохраняются. Live выше прошёл подачу и начало погрузки; полный возврат/отправление этого инцидентного live-run здесь не объявлены показанными. Исходный цикл sim480→departure2700 при10× требует примерно222wall-с, не минуту.

**B — поезд против манёвра.** На одинаковом исходном входе проверены FCFS и разные heuristic rules. Реальные две лучшие альтернативы не обязаны ставить P1 первым: приоритет мягкий, начатый манёвр не прерывается. Отдельная urgency-стратегия пропускает P1 раньше, но проигрывает по общей J. Её не вставляли в UI вместо фактического второго результата.

**C — следующая станция не принимает T6.** Fixture у T6 задаёт E_W/**DEST_W**, не DEST_E. Offline execution доsim3300: старое отправление заканчивается3780, приём после600с —4380. Блок DEST_W[3300,4800) переносит отправление до4440–4560, приём5160. DEST_E проверен как отрицательное прямое ограничение T6. Соседняя станция не моделируется целиком.

Числа и входы сохранены в `stories.json`; это явно **offline event-jump evidence**, не live replay/видеозапись/SLA. Описание и скрипт дают воспроизводимую репетицию, но готового пользовательского переключателя новых run/replay пока нет.

## 7. Оставшиеся приоритеты

Подробные задачи/исполнители/основания — [post-H12 PLAN_24H](PLAN_24H.md#дополнение-после-h12-оставшаяся-работа-и-железнодорожный-ui). Таблица ниже — оценки дальнейшей работы, не уже выполненные часы.

| Класс | Работа | B, часы |
|---|---|---:|
| MUST | Actual пятифакторный KPI + admin config PATCH |1,3 |
| MUST | History/snapshot/replay support + retention24h; CSV |1,4 +0,3 |
| MUST | Clock/render telemetry/metrics; noisy normalization/dedup |0,9 +0,4 |
| MUST | Новый run без удаления истории; минимальное manual confirmation |0,4 +0,8 |
| MUST | Интеграция frontend/Ганта/replay друга |0,7 B; F отдельно |
| MUST | Формальная latency/burst/replan серия и честное baseline сравнение |1,2 |
| MUST | Документы запуска, комплект защиты/репетиция |0,8 B; оформление F параллельно |
| SHOULD после MUST | Derived demo route indicators, не СЦБ |0,5–1 |
| SHOULD после MUST/P0/P1 | Gemini structured explanation, env-only, timeout/fallback |1–1,5 B +0,25–0,5 F |
| ONLY IF TIME | Фильтры, дополнительная запись, необязательная анимация |0,25–0,5 |
| DROP | Новые поезда/станции/topology, полноценная СЦБ, CV/RFID, OR-Tools, Kafka/Redis/микросервисы, production scaling |0 |

Целевой **оставшийся B timebox8,2ч**, практически около9–12ч **до резерва**; frontend параллельно ещё примерно4–6,5ч. Условные2ч резерва существуют только если реальный остаток позволяет — elapsed H12 не означает автоматически ещё12часов. Если времени недостаточно, показываем незакрытые критерии, не переименовываем официальные MUST в optional.

## 8. Что не реализовано / mock

Синтетические fixtures, длительности и календарь приёма остаются учебными; работающий live State, planner, validator и incidents не mock. Новый read-only explanation deterministic, не AI.

Не реализованы: actual live индекс (есть настоящий forecast), config PATCH, history/replay API/UI/retention/CSV, manual confirmation, POST runs, /time + UI-render telemetry + /metrics, полная noisy pipeline. Текущий SSE-log не заменяет replay; chains не заменяют основной Гант; frontend друга/LAN integration и max paint SLA не проверены. Signals/Gemini намеренно не начаты, чтобы не задержать обязательные хвосты. Работа остановлена на разрешённых безопасных UI/семантических изменениях.
