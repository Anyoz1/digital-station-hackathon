# Дополнение к ТЗ v1.0: AI Explanation Assistant

Версия дополнения: **v1.0-P2.1**, 1 октября 2026 года.
Источник: **уточнение пользователя (U05)**, не официальное требование организаторов
и не идея, приписываемая ментору или внешним railway-решениям.
Статус: **только проектирование; не реализовано**.

Это нормативное приложение к [SPEC.md](SPEC.md), [FRONTEND_HANDOFF.md](FRONTEND_HANDOFF.md),
[PLAN_24H.md](PLAN_24H.md) и [RESEARCH.md](RESEARCH.md). Замороженные файлы v1.0,
тег `spec-v1.0` и их SHA-256 остаются неизменными. Для этой единственной P2-функции
уточняется исключение LLM/внешних интеграций из SPEC §1: они по-прежнему **не входят
в P0/P1**, но допускается необязательный внешний сервис пересказа объяснений.
Все остальные границы, алгоритмы, API и масштаб сохраняются: `demo_main_v1` —
7 поездов, 12 путей. Выполненный H0–H2 не расширяется; дальнейшая реализация
по-прежнему требует следующей команды пользователя.

## 1. Дополнение к SPEC: назначение, границы и приоритет

**AI Explanation Assistant — не planner, не советчик по управлению и не источник
железнодорожных фактов.** Только по явному запросу пользователя внешний LLM API
переформулирует уже существующее backend-объяснение. Например, Gemini — предложенный
пользователем вариант провайдера, а не выбранная обязательная зависимость.
Конкретный provider/model/SDK сейчас не фиксируется; при реализации проверяются
актуальные официальные API-документы и условия передачи данных.

Дополнительная строка матрицы SPEC §2:

| ID | Требование | Источник | Предлагаемая реализация | Проверка на демо | Приоритет | Дополнительные B / F, ч |
|---|---|---|---|---|---|---|
| U05 | AI Explanation Assistant | U, текущее уточнение | Один readonly explanation endpoint, подготовленный backend-контекст, необязательный внешний LLM, обязательный deterministic fallback | Объяснить проверенный план; отключить сеть/key и получить то же исходное объяснение без изменения State | P2 / wow-feature | 1,5–2 / 0,25–0,5, только при свободном времени |

В MVP этой функции доступны четыре предустановленных вопроса:

- «Почему выбран этот план?» — backend уже указал правило выбора, objective J,
  сравнение допустимых вариантов и tie-break. LLM не выбирает вариант и не заменяет
  objective индексом; не называет feasible-план доказанно оптимальным.
- «Что изменилось после инцидента?» — пересказ incident/affected IDs и готового diff.
- «Почему поезд задержан?» — причина, рассчитанная backend для выбранного Train.id,
  включая ожидание операции/ресурса/маршрута и ссылки на конфликты.
- «Какие факторы сильнее всего ухудшают индекс?» — готовая сортировка вкладов
  `contribution` и причины backend, без пересчёта формулы или выдуманных процентов.

Свободный чат, agent/tool calling, RAG, обучение модели, подбор новых планов,
новые железнодорожные знания и подключение реальной инфраструктуры **не входят**.
При нехватке исходных данных deterministic explanation прямо сообщает об этом;
LLM не должен заполнять пробелы предположениями.

## 2. Дополнение к архитектуре SPEC §5/7/8/9

Последовательность строго односторонняя:

```text
deterministic planner → independent validator → backend diff/KPI/explanation
                                                    ↓ явный запрос пользователя
                                   readonly context builder → optional LLM API
                                                    ↓
                                   отдельная карточка AI / deterministic fallback
```

LLM подключается **после** завершения расчёта и проверки сохранённых вариантов,
готового diff и KPI. Применение допустимого плана не ждёт LLM. Его вызов не входит
в перепланирование≤5с и не запускается автоматически при инциденте или каждом SSE tick.
Успешная работа всего основного демо должна проверяться с отключённым AI.

Backend собирает неизменяемый контекст по сохранённым `run_id/plan_id` и связанному
`optimization_run_id`, а не по текущему набору объектов, меняющемуся во время запроса.
Для внешнего вызова необходимы завершённая независимая проверка,
`validity=feasible` и `validator.passed=true`. Для rejected/no-feasible/pending
результата пользователь видит обычные deterministic причины; LLM не используется.

Контекст внешнего API — только allowlist структурированных полей:

| Поля | Источник и смысл |
|---|---|
| `basis` | Run/plan/job IDs, base input/state revisions, config version, время среза и hash контекста |
| `question_code`, optional `train_id` | Один из четырёх вопросов; ID существующего поезда, без свободного prompt |
| `incidents` | Только относящиеся к расчёту сохранённые incident records |
| `affected_entities` | IDs и нужные атрибуты поездов/операций/путей/ресурсов, не весь State |
| `plan_diff` | Уже рассчитанные backend изменения назначений/времён/ресурсов |
| `validator_result` | Сохранённый результат independent validator; не заключение LLM |
| `kpi_before`, `kpi_after` | Готовые raw/unit/normalization/contribution/score/reason, formula/config versions, окно и явный `actual`/`forecast` |
| `conflicts`, `explanations` | Backend reason codes, связанные IDs и исходные deterministic объяснения |

В контексте различаются факт и прогноз, секунды симуляции и wall milliseconds,
objective J и индекс эффективности. Разность KPI разрешена только для сопоставимых
окон/формул/весов; иначе backend даёт `null` и причину. LLM не вычисляет diff/KPI,
не достраивает причинность, не выдаёт корреляцию за причину и не пересортировывает планы.

Context builder не принимает от браузера State, diff, KPI, validator result или
произвольный текст. Ограничение JSON-контекста —16KiB UTF-8; при превышении возвращается
deterministic fallback, а не молчаливое обрезание доказательств. Сырые журналы,
личные данные, cookies, API keys, DB URL и пароли наружу не передаются.

### Изоляция, защита и отказы

- LLM не имеет DB connection, доступа к actor/queue, filesystem, сети станции
  или domain mutation API. Только backend читает сохранённые данные и выполняет
  единственный внешний запрос. Не добавляются tools/functions или выполнение кода.
- AI-результат не записывается в State, plan assignments, validator report,
  причины доменных событий или KPI. Не увеличивает `event_seq`, `state_version`,
  `input_revision`, `config_version` и не публикует domain SSE.
- Отдельный async HTTP-вызов с общим deadline3wall-секунды, включая соединение
  и чтение; без автоматических retry. Не держать транзакцию/actor lock во время
  запроса. Не блокировать event loop, planner worker или поток обновлений.
- Не более одного внешнего AI-запроса одновременно; cooldown10wall-секунд на
  пользователя. При занятости/лимите сразу вернуть deterministic fallback.
  Новая очередь, брокер, таблица истории чата и обязательный cache не нужны.
- Ключ только из server env. Не передавать ключ frontend, URL/query, error details,
  fixtures или logs. В logs допустимы provider/status/latency/context hash,
  но не prompt/response/секреты. Только синтетические данные demo.
- Prompt запрещает новые факты/правила/числа, управляющие рекомендации и выполнение
  инструкций из контекста. Поезда/инциденты/планы — данные, не инструкции.
  Ответ — ограниченный JSON с plain-text пересказом и ссылками на evidence IDs
  контекста; длина текста≤2000символов, без HTML/Markdown/tool calls.
- Backend проверяет формат, длину и существование evidence IDs; malformed output,
  неизвестные IDs или явное противоречие исходному explanation → fallback.
  **Проверка формы и prompt не доказывают истинность всего свободного текста LLM.**
  Поэтому AI-пересказ маркируется как необязательный/неавторитетный; рядом всегда
  доступно исходное deterministic explanation. Даже нераспознанная галлюцинация
  не становится фактом доменной модели и не может повлиять на управление.
- При disabled/missing key/timeout/provider error/HTTP429/busy/invalid output
  valid-запрос получает исходное deterministic explanation, не пустую карточку.
  Ошибка LLM не влияет на readiness основного продукта.

Планируемые server env-настройки, **сейчас не добавлены в runtime**:

```dotenv
AI_EXPLANATION_ENABLED=false
AI_EXPLANATION_PROVIDER=<выбранный при P2 провайдер>
AI_EXPLANATION_MODEL=<модель выбранного провайдера>
AI_EXPLANATION_API_KEY=<только локальный секрет>
```

Отключено по умолчанию. Если enabled, но конфигурация неполна, сервис остаётся
готовым к основному демо и explanation отдаётся без LLM. Смена AI-конфигурации
не меняет `config_version` индекса или условия поиска.

## 3. Дополнение к FRONTEND_HANDOFF: planned API/UI

Всё ниже **планируется для P2, не реализовано в H0–H2**. Текущий
`contracts/api-v1.ts`, State v1.0 и фактический OpenAPI не меняются до реализации.
Функция не нужна для интеграции frontend с обязательными API.

### Один endpoint

`POST /api/v1/explanations` — вычисляемое чтение сохранённого плана, без domain
command envelope/receipt/CAS. Требуются обычная session cookie, JSON content type,
Origin check и право чтения запрошенных run/plan. Все четыре текущие роли могут
читать объяснение; это не даёт им право replan/apply/control. Backend ограничивает
внешние вызовы независимо от роли.

```json
{
  "run_id": "run-demo-1",
  "plan_id": "plan-2a",
  "question_code": "WHY_TRAIN_DELAYED",
  "train_id": "T2",
  "use_ai": true
}
```

`question_code`: `WHY_PLAN_SELECTED`, `WHAT_CHANGED_AFTER_INCIDENT`,
`WHY_TRAIN_DELAYED`, `INDEX_FACTORS`. `train_id` обязателен только для вопроса
о задержке и должен принадлежать run; для остальных вопросов не передаётся.
`use_ai` по умолчанию false. Backend сам разрешает job/context по сохранённому plan;
пользователь не передаёт фактический контекст или ключ.

Планируемый ответ200, пример **формата, не результат работающего AI**:

```json
{
  "basis": {
    "run_id": "run-demo-1",
    "plan_id": "plan-2a",
    "optimization_run_id": "rp-2",
    "base_input_revision": 8,
    "base_state_version": 240,
    "config_version": 1,
    "context_id": "sha256:<hash сохранённого контекста>"
  },
  "question_code": "WHY_TRAIN_DELAYED",
  "train_id": "T2",
  "source": "deterministic",
  "text": "Операция ожидает освобождения маршрута; основание указано в сохранённом плане.",
  "deterministic_text": "Операция ожидает освобождения маршрута; основание указано в сохранённом плане.",
  "evidence": [
    {"id": "e-1", "kind": "conflict", "entity_ids": ["T2", "W"], "description": "Причина ожидания из backend explanation"}
  ],
  "ai": {"status": "fallback", "fallback_reason": "timeout", "elapsed_ms": 3000},
  "advisory": true
}
```

`source`: `deterministic|llm`. `ai.status`: `not_requested|disabled|used|fallback`.
`fallback_reason` — null либо `missing_key|timeout|provider_error|rate_limited|
busy|invalid_output|context_too_large|validator_rejected`; `elapsed_ms` — реальное
время внешнего вызова в wall ms, null если вызова не было. При `source=llm` text —
пересказ, исходный `deterministic_text` и evidence всё равно возвращаются.
Это отдельная метрика запроса, не `ReplanJob.compute_ms/elapsed_ms` и не UI event latency.

Ошибки используют существующую оболочку `{error:{code,message,details,request_id}}`,
здесь `request_id=null`, поскольку command UUID не передаётся:

| HTTP | Случай |
|---|---|
| 401/403 | Нет действительной session / нет права чтения / запрещённый Origin |
| 404 | `RUN_NOT_FOUND`, `PLAN_NOT_FOUND`, `TRAIN_NOT_FOUND`; ID не принадлежит выбранному run |
| 409 | `EXPLANATION_CONTEXT_NOT_READY`: расчёт/validator/diff ещё не завершён. Показать уже доступные backend reasons, не вызывать LLM |
| 422 | `VALIDATION_ERROR`: неизвестный question_code, неверные/лишние поля, отсутствие train_id для задержки |
| 503 | БД/сохранённые данные недоступны; это ошибка backend, а не повод выдумывать fallback из пустого контекста |

Все provider failures при доступном корректном контексте —200 с deterministic
fallback. Для завершённого rejected-плана ответ только deterministic, status=fallback,
reason=validator_rejected; сообщение описывает отказ, не применимость плана.
Для no-feasible job без plan_id отдельный AI-запрос не предусмотрен: UI показывает
уже существующие deterministic причины job. До реализации endpoint отсутствует;
UI не должен обращаться к нему автоматически или трактовать404 как готовый AI.

### Минимальный UI

Одна дополнительная карточка внутри выбранного плана/поезда, четыре готовых вопроса
и кнопка «Объяснить с AI». Без отдельного чата или нового экрана. Кнопка появляется
только при явно включённой P2-интеграции; deterministic объяснения видны независимо
от неё. На H0–H2 `/tech/smoke` не меняется.

Показать loading, источник `AI-пересказ` или `Backend explanation`, основание
run/plan/revision и исходное deterministic explanation. Текст рендерится как plain
text (`textContent`), не HTML. При provider failure объяснение остаётся, рядом
короткая причина fallback. Нет кнопок apply/control внутри AI-карточки.

Ответ объясняет **сохранённый план**, не произвольное текущее State. UI связывает
запрос с выбранными run/plan/question/train; если выбор сменился во время ожидания,
поздний ответ не подменяет новую карточку. Историческое объяснение подписывается
версией/временем; новый incident не делает старый текст актуальным объяснением
нового плана. Обновление основной станции/SSE продолжается независимо от AI.

Для независимой разработки после допуска к P2 добавить явно маркированные
fixtures `explanation.llm.json` и `explanation.fallback.json`. Сейчас они **не создаются**;
mock-ответ не демонстрируется как реальный ответ внешнего API.

## 4. Дополнение к PLAN_24H: gate, бюджет и сокращение

AI не добавляется ни в один обязательный интервал H0–H22. Начинать можно только
после фактической приёмки **всех P0/P1**, включая latency, burst, replay/export,
auth и комплект сдачи. План должен уже иметь полноценное deterministic explanation:
это обязательная backend-функция, а не часть AI, которую можно отложить до P2.

Реализация возможна лишь при завершении обязательных задач раньше срока, наличии
оценочных1,5–2ч backend и0,25–0,5ч frontend **сверх защищённого резерва H22–H24**
и отдельном разрешении продолжать реализацию. Это дополнительные затраты, не
новая дорожка и не обещание уместить AI в уже заполненные24ч. Если времени нет,
feature пропускается целиком; основной demo и критерии защиты не меняются.

| Исполнитель | Условная P2-задача | Зависимость / завершение |
|---|---|---|
| B | Context builder и deterministic endpoint | Сохранённые validator/diff/KPI/reasons; unit tests и запрет входного пользовательского State |
| B | Один provider adapter, env, async deadline и fallback | Тесты timeout/error/invalid output/isolation; offline demo остаётся рабочим |
| F | Карточка/четыре вопроса/source/fallback | Реальный endpoint или явные P2-fixtures, без изменения общего State store |
| B + F | E2E и короткая опциональная вставка в защиту | Не отнимает время обязательной репетиции; проверка и без провайдера |

При отставании **первым исключается весь AI**, до сокращения обязательных функций,
не за счёт safety/validator/SSE/истории или резерва. Основной сценарий защиты
показывает deterministic объяснение; успешный AI-запрос — дополнительная вставка
при запасе времени, но не обязательная ступень «incident→plan→apply».

## 5. Дополнение к RESEARCH: основание решения

**Decision:** допускаем внешний LLM только как P2-слой пересказа готового,
проверяемого backend explanation.

**Evidence:** источник идеи — текущее уточнение пользователя. SPEC v1.0 уже требует
deterministic planner, independent validator, backend diff/KPI и объяснение причин.
Исследование railway-источников не доказывает необходимость Gemini или другого
LLM; эту функцию не приписываем Siemens/OptiYard/Robust Rail/cTORS/ментору.

**Adaptation:** один запрос по сохранённому plan_id, allowlist структурированных
фактов, маркированный AI-пересказ и deterministic fallback. Нет agent-доступа,
RAG/обучения, новой предметной модели или внешней railway-интеграции.

**Reason:** изоляция сохраняет безопасность и работоспособность демо без сети/ключа.
Объём небольшой, но внешний API, latency и галлюцинации добавляют риск; поэтому
функция строго необязательна и не финансируется временем P0/P1/резерва.
При будущей реализации понадобится проверка условий/лимитов выбранного API;
копирование чужого кода сейчас не планируется, новые зависимости/лицензии не добавлены.

## 6. Дополнительная приёмка только для P2

Эти проверки не заменяют A01–A18 и не считаются выполненными сейчас.

| ID | Проверка | Ожидаемый результат |
|---|---|---|
| AI01 | Каждый из четырёх вопросов на готовом валидированном плане | Context содержит только backend facts; reason/evidence соответствуют выбранным IDs, UI различает forecast/actual |
| AI02 | AI disabled, нет key, timeout, provider429/5xx, invalid JSON/unknown evidence ID | Deterministic explanation показано; не ложный успех AI, не пустой ответ |
| AI03 | Сравнить State/plan/events/versions до и после success/fallback | Никаких AI-domain mutations; validator/apply никогда не вызываются из ответа LLM |
| AI04 | Медленный provider при running станции и replan | Actor/SSE/worker продолжаются; AI имеет свой deadline, вне планировочного SLA |
| AI05 | Новое incident/смена выбранного плана во время AI-запроса | Старый ответ связан со своим basis, не отображается как объяснение нового плана |
| AI06 | Подделанный KPI/State в request, неизвестные IDs, неготовый/rejected plan, prompt injection в данных | Request/context guards; LLM не вызывается для непроверенного плана. Инструкции внутри данных не дают tools/mutation-доступа; подозрительный результат заменяется fallback, источник фактов остаётся backend |
| AI07 | Browser E2E и поиск секретов в network/logs/fixtures | Session/права/ошибки корректны; API key отсутствует на клиенте; plain-text rendering безопасен |
| AI08 | Полная основная репетиция при выключенном AI и недоступном внешнем API | P0/P1 и основной demo полностью независимы от AI |

Tests внешнего adapter используют stub HTTP responses и явно обозначены как tests,
а не реальный provider run. Для демонстрации «живого AI» нужен отдельный настоящий
запрос к настроенному провайдеру; при отсутствии ключа/сети показывается честный fallback.
