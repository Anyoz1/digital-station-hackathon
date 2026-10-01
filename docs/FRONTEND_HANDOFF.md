# Frontend цифровой станции

Статус на 1 октября 2026 года: **предлагаемое техническое задание и контракт v1.0, ещё не утверждённые пользователем**. Документ переработан после сравнительной матрицы [RESEARCH.md](RESEARCH.md): выбрана учебная станция среднего масштаба, ограничен активный горизонт планирования. Backend, frontend, `/tech`, endpoint-ы и fixture-файлы пока не реализованы. Этот документ позволяет параллельно подготовить интерфейс после утверждения ТЗ; перечисление endpoint-а не означает его готовность.

Каноническая предметная модель, ограничения и формулы находятся в [SPEC.md](SPEC.md). Здесь закреплены представление данных и сетевой контракт. Изменения названий полей и enum после контрольной точки H2 допускаются только согласованно в обоих документах; добавлять необязательные поля можно без ломания клиента. План работ — [PLAN_24H.md](PLAN_24H.md).

## Что показываем

Учебная станция среднего масштаба: 12 путей — приёмо-отправочные R1–R4, сортировочные S1–S4, грузовые C1/C2, вытяжной H и стояночный D1; западная и восточная границы BW/BE, конфликтные зоны W/E. D1 служит местом стоянки маневрового локомотива и имеет календарь доступности; полный процесс локомотивного депо не моделируем. Схема показывает парки, связность, назначения и фактическую занятость.

Основной сценарий `demo_main_v1` содержит **семь поездов**: шесть грузовых (три транзитных, два с местной работой, один с переработкой фиксированных групп) и высокоприоритетный пассажирский транзит P1. Пассажирский поезд входит в обязательное основное демо: фиксированный самоходный состав длиной 120м, направление W_E, R1/R2, arrival→departure без грузовых операций и осмотра. Планировщик получает не больше 8 известных поездов, одновременно на станции не больше 6 поездов любого типа, не больше 80 макроопераций в горизонте 7200 sim-секунд. Основной fixture содержит 33 макрооперации. Это ограничение расчётной нагрузки, а не причина скрывать остальные пути.

`FREIGHT`, `PASSENGER`, `SERVICE`, `OTHER` — наши прикладные категории, а не классификатор КТЖ и не железнодорожный норматив. Алгоритм работает с профилем обслуживания, длиной, маршрутом, тяговым ресурсом и бригадой; тип используется для подписей и фильтров. SERVICE/OTHER известны схеме, но не получают технологию автоматически. Дополнительный сервисный транзитный fixture — P2. Пассажирских посадки/высадки, оборота бригад и полного passenger workflow нет. P0 — основной работающий цикл, P1 — остальные обязательные требования, также включённые в 24 часа; P2 — необязательные расширения. ID пассажирского поезда `P1` не является обозначением приоритета задачи P1.

Грузовой транзит проходит R1–R4 в направлениях `W_E` и `E_W`; пассажирский профиль допускает R1/R2. Местная работа и переработка используют R3/R4 и направление `W_E`; их манёвры идут через западную горловину. Положение отдельного поездного локомотива определяется направлением: со стороны будущего отправления. Самоходный состав не получает дополнительный нарисованный локомотив. 3D, карта реальной сети, свободный редактор инфраструктуры и drag-and-drop расписания не входят в P0.

Главный сценарий защиты: станция выполняет операции → диспетчер создаёт инцидент → UI немедленно показывает затронутые пути/ресурсы/операции → сервер автоматически рассчитывает варианты → новый допустимый план применяется → UI объясняет, какие назначения и времена изменились, показывает измеренное время и прогноз индекса. Дополнительно показываем 10 одновременных инцидентов, восстановление соединения, историю и CSV.

Числа эффективности и улучшения приходят от backend. Никаких заранее заданных процентов улучшения. `feasible` означает допустимый по учебной модели план; это не доказанная глобальная оптимальность и не допуск к управлению реальной инфраструктурой.

## Экраны и приоритеты

Один основной экран с вкладкой истории и небольшими диалогами достаточен.

| Область | Что показать | Действия |
|---|---|---|
| Шапка | Название станции, live/replay, wall-время последнего события, sim-время, скорость, роль, связь, признак «данные устарели» | Вход/выход, play/pause/step, скорость 1×/5×/10× |
| Схема SVG | Пути, их длина/занятость, состав или группа, стрелочные зоны, закрытие/ожидание закрытия, движение по маршруту | Выбор пути/поезда; никаких непосредственных команд стрелкам |
| Гант | Строки путей/ресурсов, операции с `start_sim_s`/`end_sim_s`, выполняемые/завершённые/будущие различаются | Выбор операции и её зависимостей |
| Состояние и индекс | Score, category, пять raw-факторов, нормализованный штраф/вклад, окно измерения | Раскрыть причины; настройки только admin |
| Инциденты и конфликты | Причины, affected IDs, время, статус, рекомендации | Добавить один/пакет инцидентов; завершить инцидент |
| Перепланирование | queued/running/outcome, дедлайн, elapsed, список вариантов, отличие от прошлого плана, валидность | Ручной повтор расчёта; применить ещё актуальную альтернативу |
| Карточка операции | Вид, статус, поезд/группы, путь/маршрут, ресурсы, predecessors, planned/actual times | Operator подтверждает только свою manual service operation |
| История | Список событий и slider по последним 15 wall-минутам, отдельно sim-время выбранного состояния | Просмотреть snapshot, play/pause replay, вернуться live, CSV |
| Admin-диалог | Перечень scenario, seed и текущая конфигурация | Новый run через `POST /runs`; сохранить настройки через `PATCH /config` |

На узком экране можно складывать панели вертикально. Цвет не должен быть единственным сигналом: у закрытого пути и ошибки есть подпись/иконка. Гант можно построить обычными DOM-элементами; дополнительная диаграммная библиотека не обязательна. Движение рисуется по route geometry и server progress. Интерполяция между полученными отсчётами допустима только как оформление и не меняет состояние, статусы или занятость.

Технический `/tech` делает backend-исполнитель независимо за ≤0,75ч: одна HTML/JS-страница, login, сырые таблицы State, версии/связь, stream/event log, play/pause/step/speed/reset, raw JSON-форма incident-команды, replan/result/apply. Это реальный клиент тех же API/SSE, без диаграмм, второго дизайна и бизнес-логики. Manual complete и history проверяются общей формой endpoint+JSON или Swagger, CSV — ссылкой на export; отдельного replay-slider в `/tech` нет. Полноценную схему/Гант/replay из таблицы выше реализует основной frontend, их отсутствие в `/tech` не означает исключение из продукта.

## Подключение и авторизация

Все бизнес-endpoint-ы имеют префикс `/api/v1`. Клиент использует относительные URL. Предпочтительный frontend — React + TypeScript + Vite, SVG схема, простая временная диаграмма. Backend — FastAPI; хранение — PostgreSQL, SQLAlchemy 2 async с Psycopg 3, миграции Alembic. Frontend не подключается к БД напрямую.

В разработке браузер обращается только к Vite на машине друга. Vite проксирует `/api` и SSE на backend пользователя, например `http://192.168.1.10:8000`. Это обеспечивает один origin для cookie и EventSource. `localhost` на машине друга указывает на его машину, а не на backend. На backend слушаем `0.0.0.0:8000`, firewall разрешает доступ из локальной сети. Через proxy отключить buffering/compression для `text/event-stream`, не задавать короткий timeout. На защите предпочтительно собрать frontend и раздавать его тем же origin, что API.

```ts
// Планируемая конфигурация vite.config.ts, адрес берётся из локального env.
server: {
  host: "0.0.0.0",
  proxy: {
    "/api": {
      target: process.env.BACKEND_URL ?? "http://127.0.0.1:8000",
      changeOrigin: true
    }
  }
}
```

`POST /auth/login` устанавливает server-side session cookie `station_session`, `HttpOnly`, `SameSite=Lax`, срок 12 часов. `Secure=true` при HTTPS; на локальном HTTP демо false. Учётные записи/пароли задаются env, в Git только `.env.example` с заглушками. Пароли и session tokens не передаются в URL или SSE-параметрах. Сервер проверяет Origin mutating-запросов; JSON content type обязателен. Production CORS/SSO/управление пользователями не входят.

| Роль | Чтение live/history/reports | Симулятор, инциденты, replan/apply | Ручное завершение | Настройки и новый run |
|---|---|---|---|---|
| `viewer` | Да | Нет | Нет | Нет |
| `operator` | Да | Нет | Только назначенная ему manual service operation | Нет |
| `dispatcher` | Да | Да | Нет | Нет |
| `admin` | Да | Да | Любая разрешённая manual service operation | Да |

Это роли приложения. Вагонник, машинист, составитель и бригада являются предметными исполнителями/ресурсами, а не отдельными обязательными кабинетами. Сервер отвечает `403` независимо от того, скрыта ли кнопка в UI.

`POST /api/v1/auth/login`

```json
{"username":"dispatcher","password":"<из локального env>"}
```

Ответ `200`: `{"user":{"id":"u-dispatcher","display_name":"Диспетчер","role":"dispatcher","resource_ids":[]}}`. `GET /auth/me` возвращает тот же объект; `POST /auth/logout` очищает cookie и возвращает `204`. Login/logout не требуют command envelope. Неавторизованные запросы возвращают `401`.

## Идентификаторы, версии и время

- Все ID — непрозрачные строки; UI не извлекает из них тип или номер. `run_id` меняется при создании нового запуска. IDs вагонов/поездов стабильны внутри run.
- `schema_version = "1.0"`; неизвестное дополнительное поле игнорируем, неизвестный major version показываем как несовместимость.
- `event_seq` — строго возрастающий номер сохранённого события/пакета изменений внутри run, включая running ticks. SSE ID = `run_id:event_seq`. Heartbeat на паузе не является новым доменным событием и повторяет текущий seq.
- `state_version` возрастает при изменении авторитетного состояния, в том числе sim-time/progress; в P0 может совпадать с event_seq. Paused heartbeat сохраняет обе версии и меняет только транспортный `server_time`.
- `input_revision` возрастает при изменении условий планирования: инцидент, ручное подтверждение, конфигурация, play/pause/step/speed, принятие новой ручной команды, меняющей условия. Предсказуемый tick и выполнение уже учтённого плана не увеличивают её. Это позволяет планировщику завершаться при потоке 1 Гц.
- `config_version` обозначает точную версию весов/порогов/лимитов, использованную при расчёте.
- Все `*_sim_s` — целые секунды от `scenario_epoch = "2026-10-01T08:00:00Z"`; длительности тоже секунды. Симулятор хранит дробный остаток wall-time внутри, наружу выдаёт целые секунды.
- Все `*_at`, `server_time` — UTC ISO 8601 с миллисекундами. Реальная latency и replan duration измеряются в **wall ms**, не sim-time. `speed` только 1, 5 или 10.
- `null` означает отсутствующее/ещё не наступившее значение. Нулевое время и нулевая метрика не заменяют `null`.
- `priority`: 1 — низкий, 2 — обычный, 3 — высокий. `processing_kind`, `service_profile_id` и `type` независимы от приоритета. Тип не выбирает цепочку работ автоматически.

Сервер — единственный источник статусов и occupancy. UI не ставит `completed`, не освобождает пути, не вычитает вагоны и не применяет план самостоятельно.

## Типы

Ниже нормативные enum v1.0. Не показывать внутренние значения пользователю без русских подписей.

```ts
type Role = "viewer" | "operator" | "dispatcher" | "admin";
type Mode = "paused" | "running";
type TrainType = "FREIGHT" | "PASSENGER" | "SERVICE" | "OTHER";
type Direction = "W_E" | "E_W";
type ServiceProfileId = "freight_transit_v1" | "freight_local_v1"
  | "freight_reclassify_v1" | "passenger_transit_v1" | "service_transit_v1";
type ConsistKind = "wagon_groups" | "fixed";
type TractionKind = "locomotive" | "self_propelled";
type ProcessingKind = "transit" | "local" | "reclassify";
type TrainStatus = "expected" | "waiting_entry" | "on_station"
  | "ready_departure" | "departed";
type OperationKind = "arrival" | "inspection" | "shunt_transfer"
  | "cargo" | "departure_prep" | "departure";
type OperationStatus = "pending" | "planned" | "running" | "completed" | "blocked";
type ExecutionMode = "auto" | "manual";
type ShuntPhase = "empty_to_source" | "couple" | "pull_to_lead"
  | "reverse" | "push_to_target" | "uncouple" | "return_to_depot";
type TrackAvailability = "open" | "closed" | "closure_pending";
type ResourceKind = "shunting_locomotive" | "train_locomotive" | "self_propelled_unit" | "shunting_crew"
  | "inspection_crew" | "cargo_crew" | "train_crew";
type ResourceStatus = "available" | "busy" | "unavailable" | "unavailable_pending";
type PlanStatus = "proposed" | "active" | "superseded" | "stale" | "rejected";
type PlanValidity = "feasible" | "invalid";
type ReplanStatus = "queued" | "running" | "succeeded" | "no_feasible_plan"
  | "stale" | "timeout" | "failed";
type IncidentKind = "train_delay" | "track_closure" | "resource_loss" | "destination_block";
type IncidentStatus = "active" | "pending" | "resolved";
type ConflictKind = "route_overlap" | "track_occupied" | "resource_unavailable"
  | "precedence" | "capacity" | "destination_closed";
type Severity = "warning" | "critical";
type EfficiencyCategory = "normal" | "attention" | "critical";
```

### State

`GET /snapshot` и каждое SSE-событие `state` содержат один и тот же **полный** `State`. JSONPatch, частичных entity-updates и второго локального хранилища бизнес-состояния нет. При получении более нового seq клиент атомарно заменяет state; transient UI-состояние выбранной вкладки/объекта хранит отдельно. Heartbeat с прежним seq обновляет время связи, не заменяя доменные сущности повторно.

```ts
interface State {
  schema_version: "1.0";
  run_id: string; scenario_id: string; scenario_epoch: string;
  event_seq: number; state_version: number; input_revision: number; config_version: number;
  server_time: string; sim_time_s: number; speed: 1 | 5 | 10; mode: Mode;
  station: Station; active_plan_id: string | null;
  tracks: Track[]; zones: Zone[]; trains: Train[]; wagon_groups: WagonGroup[];
  resources: Resource[]; operations: Operation[];
  incidents: Incident[]; conflicts: Conflict[]; plans: PlanSummary[];
  last_replan: ReplanJob | null; efficiency: Efficiency;
}
interface Station {
  id: string; name: string;
  parks: {id: string; name: string; kind: "receiving_departure" | "sorting" | "cargo" | "service"}[];
  layout: {
    view_box: [number, number, number, number];
    nodes: {id: string; kind: "boundary" | "switch_zone" | "track_end";
      x: number; y: number; switch_ids: string[]}[];
    edges: {id: string; from_node_id: string; to_node_id: string;
      track_id: string | null; zone_id: string | null; bidirectional: boolean}[];
    tracks: {track_id: string; points: [number, number][]}[];
    routes: {id: string; from_id: string; to_id: string;
      track_ids: string[]; zone_ids: string[]; points: [number, number][]}[];
  };
}
interface Track {
  id: string; name: string; park_id: string;
  kind: "receiving_departure" | "lead" | "sorting" | "cargo" | "parking";
  length_m: number; availability: TrackAvailability;
  occupied_length_m: number; train_ids: string[]; group_ids: string[]; locomotive_ids: string[];
  assigned_train_id: string | null; active_operation_ids: string[];
}
interface Zone {
  id: "W" | "E"; active_operation_id: string | null;
  availability: "open" | "closed";
}
interface Train {
  id: string; number: string; type: TrainType; service_profile_id: ServiceProfileId;
  processing_kind: ProcessingKind; direction: Direction;
  consist_kind: ConsistKind; body_length_m: number; total_length_m: number;
  location: PhysicalLocation;
  priority: 1 | 2 | 3; status: TrainStatus; destination_id: string;
  scheduled_arrival_sim_s: number; expected_arrival_sim_s: number;
  due_departure_sim_s: number; actual_arrival_sim_s: number | null;
  actual_departure_sim_s: number | null; group_ids: string[];
  target_group_ids?: string[];
  traction_resource_id: string; traction_kind: TractionKind;
  planned_track_id: string | null; current_track_id: string | null;
}
interface PhysicalLocation {
  kind: "boundary" | "track" | "route" | "departed";
  boundary_id: string | null; track_id: string | null; route_id: string | null;
  operation_id: string | null; route_progress: number | null;
}
interface WagonGroup {
  id: string; origin_train_id: string; assigned_train_id: string;
  current_train_id: string | null; wagon_ids: string[];
  wagon_count: number; length_m: number; destination_id: string;
  cargo_state: "unloaded" | "loaded" | "not_applicable";
  location: PhysicalLocation;
}
interface Resource {
  id: string; name: string; kind: ResourceKind; status: ResourceStatus;
  active_operation_id: string | null;
  location: PhysicalLocation | null;
  available_after_sim_s: number | null; assigned_user_id: string | null;
}
interface Operation {
  id: string; train_id: string; group_ids: string[]; kind: OperationKind;
  status: OperationStatus; execution_mode: ExecutionMode;
  predecessor_ids: string[]; source_track_id: string | null; target_track_id: string | null;
  route_ids: string[]; resource_ids: string[];
  duration_sim_s: number; start_sim_s: number | null; end_sim_s: number | null;
  actual_start_sim_s: number | null; actual_end_sim_s: number | null;
  progress: number; phase: ShuntPhase | null; blocked_reason_codes: string[];
  assigned_user_id: string | null; can_complete: boolean;
}
```

`progress` — серверное число 0..1 для отображения; `location.route_progress` отдельно показывает долю именно текущего маршрута. Каждый поезд имеет `Train.location`, `total_length_m` и `traction_resource_id`. Для `consist_kind=fixed` массив `group_ids=[]`, объекты WagonGroup не нужны. У P1 `body_length_m=total_length_m=120`, а ресурс TP1 вида `self_propelled_unit` является встроенной тягой; его длина второй раз не добавляется. Прибытие и отправление работают с Train, а не требуют грузовых групп.

Для `consist_kind=wagon_groups` текущие `group_ids` идут по физическому порядку запад→восток; `target_group_ids` фиксирует ожидаемый состав для отправления. `body_length_m` равна длине сейчас присоединённых групп, `total_length_m` включает отдельный локомотив 20м. Отцепленные группы имеют `current_train_id=null` и собственное местоположение; Train.location остаётся у присоединённой части. `origin_train_id` и `assigned_train_id` групп в демо неизменны. `Operation.group_ids=[]` допустим для целого фиксированного состава. Входы cargo/shunt требуют соответствующих возможностей service profile и наличия перемещаемых групп, а не проверки `type == FREIGHT`.

В демо группа однородна по грузовому состоянию. `WagonGroup.cargo_state` меняется на backend после cargo: местные G2L/G5L начинают `unloaded` и становятся `loaded`, остальные грузовые группы начинают `loaded`; `not_applicable` предусмотрен для группы без грузового процесса. Это учебная погрузка, не справочник реальных грузов. Fixed P1 не имеет такого поля или фиктивной группы. UI показывает состояние, но не меняет его отдельной командой.

`Track.train_ids` отражает присутствующие составы, включая fixed с пустым group_ids. `occupied_length_m` приходит готовой от backend: frontend не суммирует независимо train/group/resource длины, иначе посчитает вагоны или встроенную тягу дважды. `locomotive_ids` обозначает отдельные локомотивы; TP1 отображается внутри P1 через traction_resource_id. Во время движения `location.kind=route`; поезд/группа/локомотив не рисуются одновременно стоящими на двух путях. `Resource.location=null` допустимо для персонала, которому не задаётся координата; у отдельного локомотива и самоходной тяговой единицы location обязательна. Для kind=boundary задан boundary_id (BW/BE), kind=track — track_id, kind=route — route_id/operation_id/route_progress; неприменимые поля равны null.

Семь фаз манёвра: `empty_to_source` — D1→W→source без вагонов (60с), `couple` (60с), `pull_to_lead` — source→W→H (90с), `reverse` (30с), `push_to_target` — H→W→target (90с), `uncouple` (30с), `return_to_depot` — target→W→D1 (60с). Итого 420 sim-секунд. Для всей макрооперации резервируются D1, W, H, source, target, локомотив и составительская бригада. UI рисует текущую фазу по server location и не выводит занятость ресурсов из одной общей полосы progress. Reserved/assigned track не равен фактической occupancy. Блокировки и допустимость назначений определяет backend.

Неизвестный либо отключённый `service_profile_id` в scenario/input возвращает `422 UNKNOWN_SERVICE_PROFILE`. Сервер проверяет требования профиля к составу, ресурсам и маршрутам; несоответствие — `422 PROFILE_REQUIREMENT_MISMATCH`. Ядро не разрешает/запрещает операции по четырём значениям Train.type. `passenger_transit_v1` включён в `demo_main_v1`; `service_transit_v1` предусмотрен как выключенное по умолчанию P2-расширение. Приоритет P1 явно задан числом 3 в сценарии; тип сам по себе его не повышает.

Узлы ZW/ZE агрегируют внутреннюю работу стрелок в две взаимно исключающие горловины; `switch_ids` хранит подписи моделируемых стрелок. Фактический владелец блокировки зоны приходит в `State.zones.active_operation_id`: frontend не рассчитывает его по пересечению route_ids. В P0 нет команды закрытия всей зоны, поэтому её availability обычно open; инцидент закрывает выбранный путь. Полной модели электрической централизации нет.

```ts
interface Incident {
  id: string; kind: IncidentKind; target_id: string; status: IncidentStatus;
  created_at: string; starts_sim_s: number; ends_sim_s: number;
  delay_sim_s: number | null; affected_operation_ids: string[]; description: string;
}
interface Conflict {
  id: string; kind: ConflictKind; severity: Severity; reason_code: string;
  entity_ids: string[]; operation_ids: string[]; message: string;
  recommendation: string; detected_at: string;
}
interface Efficiency {
  mode: "actual" | "forecast"; formula_version: string;
  window_start_sim_s: number; window_end_sim_s: number;
  score: number | null; category: EfficiencyCategory | null;
  factors: {
    key: "throughput" | "delay" | "occupancy" | "conflicts" | "resource_idle";
    raw: number | null; unit: string; norm_penalty: number | null;
    weight: number; contribution: number | null; reason: string;
  }[];
}
interface PlanSummary {
  id: string; status: PlanStatus; validity: PlanValidity;
  strategy: string; base_input_revision: number; base_state_version: number;
  base_active_plan_id: string | null;
  config_version: number; created_at: string; cutover_sim_s: number;
  forecast: Efficiency; objective_value: number | null;
  changed_operation_ids: string[]; explanation: string[];
  can_apply: boolean;
}
interface PlanDetail extends PlanSummary {
  operations: Operation[];
  validator: {passed: boolean; errors: {code: string; entity_ids: string[]; message: string}[]};
}
interface ReplanJob {
  id: string; status: ReplanStatus; reason: string;
  created_at: string; started_at: string | null; finished_at: string | null;
  base_input_revision: number; candidate_plan_ids: string[]; applied_plan_id: string | null;
  elapsed_ms: number; compute_ms: number | null;
  validation_ms: number | null; deadline_ms: 5000;
  outcome_reason_codes: string[];
}
```

`contribution` — потеря пунктов индекса, а не значение raw-показателя; нормативную формулу `efficiency-v1` задаёт SPEC. `throughput.raw` — доля обслуженной когорты (`unit=ratio`), `delay.raw` — sim-секунды, `occupancy`/`resource_idle` — доли, `conflicts` — количество. Дополнительные trains/hour не подменяют throughput.raw. При ещё пустом окне `null` отображается «недостаточно данных». Forecast варианта сравнивается с forecast другого варианта на **том же горизонте**; рядом с actual всегда явная подпись «факт»/«прогноз». Backend сортирует причины по влиянию. Score из примера — fixture, не обещанное улучшение. `objective_value` — отдельная минимизируемая цель J планировщика; меньший J не обязан давать больший индекс, поэтому frontend не пересортировывает кандидатов самостоятельно.

`GET /replans/{id}` возвращает `{job: ReplanJob, plans: PlanDetail[]}`. State.plans содержит active plan и кандидатов последнего расчёта, а не всю историю планов. Вариант отображается безопасным только при `validity=feasible` и `validator.passed=true`. `can_apply` — серверная подсказка, но apply всё равно выполняет повторную проверку.

## Endpoint-ы и команды

Все mutating domain-запросы имеют `request_id` UUID, `run_id`, `expected_input_revision`. Новый запрос — новый UUID; сетевой retry **того же** запроса сохраняет UUID и тело. Сервер возвращает прежний результат без повторного эффекта. Один UUID с другим телом — `409 IDEMPOTENCY_MISMATCH`. На `409 REVISION_MISMATCH` клиент обновляет snapshot и предлагает повторить действие; скрыто повторять потенциально устаревшую команду с новой revision нельзя.

```ts
interface CommandEnvelope {
  request_id: string;
  run_id: string;
  expected_input_revision: number;
}
interface CommandReceipt {
  request_id: string; run_id: string;
  input_revision: number; state_version: number;
  result: Record<string, unknown>;
}
```

| Метод и путь после `/api/v1` | Назначение, тело/параметры | Ответ |
|---|---|---|
| `GET /snapshot` | Текущее состояние | `200 State` |
| `GET /stream?after=<run_id:seq>` | SSE с cookie и курсором | `state`, `reset`; ошибка auth до открытия потока |
| `GET /scenarios` | Доступные учебные сценарии | `{items:[{id,name,description,train_count,horizon_sim_s,enabled_service_profiles}]}` |
| `POST /runs` | Admin: envelope + `{scenario_id,seed}`. `run_id`/revision относятся к текущему run; при первом запуске сервер сам создаёт default paused run | `201 receipt`, `result={new_run_id}`; stream `reset` |
| `POST /simulation/control` | Envelope + `{action:"play"|"pause"|"step"|"set_speed",speed?:1|5|10}` | `200 receipt` |
| `POST /incidents` | Envelope + `{items:[IncidentInput]}`, от 1 до 10 | `201 receipt`, `result={incident_ids,replan_id}` |
| `POST /incidents/{id}/resolve` | Envelope | `200 receipt`, `result={incident_id,replan_id}` |
| `POST /replans` | Envelope + `{reason:"manual"}` | `202 receipt`, `result={replan_id}` |
| `GET /replans/{id}` | Job detail | `200 {job,plans}` |
| `POST /plans/{id}/apply` | Envelope; применить предложенную актуальную альтернативу | `200 receipt`, `result={plan_id}` |
| `POST /operations/{id}/complete` | Envelope; manual service operation при выполненных предусловиях | `200 receipt`, `result={operation_id,replan_id}` |
| `GET /history` | `run_id`, `from_seq` exclusive (default 0), `limit` 1..500 (default 100), опционально `from_wall_time`,`to_wall_time` UTC ISO | `{items:HistoryEvent[],next_from_seq:number,has_more:boolean}` |
| `GET /history/snapshot` | `run_id`, `seq` | `200 State` на запрошенном seq; не изменяет live |
| `GET /reports.csv` | `run_id`; опционально `from_wall_time`,`to_wall_time` | UTF-8 CSV attachment с реальными KPI/планами/инцидентами |
| `GET /config` | Все authenticated users | `{config_version,weights,category_thresholds,planner,units}` |
| `PATCH /config` | Admin: envelope + `{patch:{weights?,category_thresholds?,planner?}}` | `200 receipt`, `result={config_version,replan_id}` |
| `GET /time` | Clock-offset probe, authenticated | `{server_received_ms,server_sent_ms}` Unix wall-ms |
| `POST /telemetry/ui-render` | Измерение показа; отдельная служебная схема ниже, без command envelope, не меняет domain | `204` |
| `GET /metrics` | Readonly authenticated JSON counters/latencies | `200 metrics` |

Вне префикса: `GET /health/live`, `GET /health/ready`; Swagger `/docs`, схема `/openapi.json`; технический интерфейс `/tech`. Health не раскрывает секреты. `/ready` проверяет БД, запуск actor/simulator и доступность planner worker; отсутствие текущего допустимого плана отображается отдельным состоянием станции, не ложным падением процесса.

`step` допустим только на паузе, продвигает ровно одну sim-секунду и применяет все due events в её пределах. `set_speed` сохраняет play/pause; новая скорость начинает действовать с момента команды, прошлое время не пересчитывается. Play/pause/step/speed инвалидируют незавершённый расчёт и не позволяют применить результат с прежним временным допущением. Reset создаёт новый run, не стирает старую историю. UI перед reset показывает обычный диалог о начале нового прогона, но API не требует второго специального подтверждения.

Config редактирует только веса/пороги и ограниченные параметры planner. Топология, длительности операций, длины составов и вместимость путей задаются scenario JSON до создания нового run; редактора этих сущностей в UI нет. Веса неотрицательны и суммируются к 1. Planner time cap не выше 3000ms, общий replan deadline неизменяем и равен 5000ms.

```ts
interface Config {
  config_version: number;
  weights: {throughput: number; delay: number; occupancy: number; conflicts: number; resource_idle: number};
  category_thresholds: {normal_min: number; attention_min: number};
  planner: {time_limit_ms: number; max_rollouts: number};
  units: {simulation_time: "seconds"; real_time: "milliseconds"; length: "meters"};
}
```

`GET /config` возвращает Config. Исходные веса `.30/.25/.15/.20/.10`, пороги `normal_min=80, attention_min=50`, `time_limit_ms=3000, max_rollouts=12`. Допустимы `0≤attention_min<normal_min≤100`, целые `time_limit_ms` 100..3000 и `max_rollouts` 1..12. В PATCH каждый переданный вложенный объект заменяется целиком и проходит серверную валидацию; `units`/`config_version` только для чтения. Ограничение числа rollout-ов не отменяет независимую проверку и не обещает наличие двух альтернатив.

`PATCH /api/v1/config`

```json
{
  "request_id":"bf4643e4-4661-42f1-bc37-7a833a918b9a",
  "run_id":"run-demo-1","expected_input_revision":8,
  "patch":{"category_thresholds":{"normal_min":80,"attention_min":50}}
}
```

```ts
interface IncidentInput {
  kind: IncidentKind;
  target_id: string;
  duration_sim_s: number; // integer, 1..7200
  delay_sim_s?: number; // integer, 1..3600, только train_delay
}
```

Для delay target — поезд, для closure — путь, resource_loss — ресурс, destination_block — абстрактное направление назначения из `Train.destination_id`. Инцидент применяется с текущего sim-времени. Delay разрешён только при status expected/waiting_entry и ещё не начатой arrival; после старта движения — `409 PRECONDITION_FAILED`. Повторные delay суммируют сдвиги ETA и не меняют исходный график. Истечение/resolve закрывает инцидент, но уже зафиксированное опоздание ETA не «отматывает» поезд. Для closure/resource loss сервер может вернуть `pending`: начатая операция завершается, новые старты на ресурсе запрещены, затем ограничение становится active. Это учебная политика «недоступен после освобождения», а не аварийная остановка движущегося состава.

## Примеры JSON

Полные входы ниже можно сразу использовать для mock transport. UUID в реальном клиенте генерирует `crypto.randomUUID()`.

`POST /api/v1/incidents`

```json
{
  "request_id":"0df9e150-9ee7-45cb-b5e7-0c922741c2ec",
  "run_id":"run-demo-1",
  "expected_input_revision":7,
  "items":[
    {"kind":"train_delay","target_id":"T2","duration_sim_s":900,"delay_sim_s":300},
    {"kind":"track_closure","target_id":"R2","duration_sim_s":600},
    {"kind":"resource_loss","target_id":"I1","duration_sim_s":900}
  ]
}
```

```json
{
  "request_id":"0df9e150-9ee7-45cb-b5e7-0c922741c2ec",
  "run_id":"run-demo-1",
  "input_revision":8,
  "state_version":121,
  "result":{"incident_ids":["inc-1","inc-2","inc-3"],"replan_id":"rp-2"}
}
```

`POST /api/v1/simulation/control`

```json
{
  "request_id":"88a0038e-aa98-4a9b-a313-176a31817d8d",
  "run_id":"run-demo-1",
  "expected_input_revision":8,
  "action":"set_speed",
  "speed":5
}
```

Пример одного объекта Operation внутри полного State при `sim_time_s=240` (другие объекты здесь намеренно не показаны, это **не** partial-update payload):

```json
{
  "id":"op-T1-inspection","train_id":"T1","group_ids":["G1"],
  "kind":"inspection","status":"running","execution_mode":"auto",
  "predecessor_ids":["op-T1-arrival"],"source_track_id":"R1","target_track_id":"R1",
  "route_ids":[],"resource_ids":["I2"],"duration_sim_s":240,
  "start_sim_s":120,"end_sim_s":360,
  "actual_start_sim_s":120,"actual_end_sim_s":null,
  "progress":0.5,"phase":null,"blocked_reason_codes":[],
  "assigned_user_id":null,"can_complete":false
}
```

Пример завершённого job с двумя вариантами; `plans` из detail-запроса содержит их полные данные:

```json
{
  "id":"rp-2","status":"succeeded","reason":"incident_batch",
  "created_at":"2026-10-01T08:00:20.000Z",
  "started_at":"2026-10-01T08:00:20.050Z",
  "finished_at":"2026-10-01T08:00:21.250Z",
  "base_input_revision":8,"candidate_plan_ids":["plan-3","plan-4"],
  "applied_plan_id":"plan-4","elapsed_ms":1250,"compute_ms":1050,
  "validation_ms":40,"deadline_ms":5000,"outcome_reason_codes":[]
}
```

Значения времени в примерах иллюстративные; измеренные результаты появятся только после реализации.

## Realtime и восстановление

Используем SSE: все изменения идут сервер→браузер, команды выполняются REST. Двусторонний сокет не нужен. Native `EventSource` передаёт same-origin cookie и не требует хранения bearer token в JavaScript.

1. `GET /auth/me`, затем `GET /snapshot`. Сохранить State и `lastApplied={run_id,event_seq}`.
2. Открыть `EventSource('/api/v1/stream?after=' + encodeURIComponent(run_id+':'+seq))`.
3. Сервер атомарно подписывает клиент и отдаёт все события после cursor, затем live. Изменения между snapshot и подпиской не теряются.
4. Любой корректный кадр обновляет `lastReceivedAt` и возраст связи **до дедупликации**. Доменные данные `event: state` заменяются только если run совпадает и seq больше уже применённого. Полный snapshot заменяет предыдущий одним commit. Прежний seq на heartbeat — нормальная работа паузы.
5. `event: reset` несёт `{reason,state}` с полным актуальным State. Причины: `cursor_expired`, `run_changed`, `invalid_cursor`. Очистить выбор сущностей прошлого run и принять новый cursor. История старого run остаётся доступна.

Формат кадра:

```text
id: run-demo-1:122
event: state
data: {"state":<полный State>,"cause":{"kind":"incident_batch","entity_ids":["inc-1","inc-2","inc-3"],"ingested_at":"2026-10-01T08:00:20.000Z"}}

```

`<полный State>` — обозначение в описании формата, не literal JSON и не fixture. В wire payload находится валидный объект State. Для reset: `event: reset`, `id` нового State и `data: {"reason":"run_changed","state":...}`. Других обязательных типов кадров v1.0 нет. `cause.kind`: `tick`, `heartbeat`, `incident_batch`, `incident_resolved`, `operation_started`, `operation_completed`, `plan_applied`, `replan_updated`, `config_changed`, `simulation_control`, `run_created`. Несколько доменных переходов одного sim-tick могут публиковаться одним snapshot, но все исходные события сохраняются в history. Для объединённого кадра `cause.ingested_at` — самый ранний ingress покрываемых причин, `entity_ids` — их объединение; batch из 10 инцидентов не превращается в единственную последнюю причину.

На running поток передаёт полный State **минимум один раз в wall-секунду**. Операции и инциденты публикуются сразу после commit, не ждут следующего tick. На paused — heartbeat `state` 1 Гц с обновлённым `server_time`, прежними `event_seq`, `state_version` и доменными данными. Heartbeat не сохраняется в PostgreSQL и не добавляется в replay ring; именно время последнего принятого кадра, а не прирост seq, определяет свежесть связи.

На `EventSource.onerror` клиент вызывает `close()` и сам управляет reconnect: 1, 2, 4, 8, максимум 10с, jitter ±20%. Перед новой попыткой можно проверить `/auth/me`; `401` завершает retry и открывает login. Это **собственный** retry loop, а не обещание управлять скрытым алгоритмом native EventSource. После первого успешного кадра backoff сбрасывается. Одновременно держим только один EventSource.

Статусы связи по wall-clock `performance.now()`: `connecting` до первого кадра, `live` при возрасте последнего кадра ≤3с, `stale` >3с, `offline` >10с или явном завершении retry по auth. На разрыве сразу показываем `reconnecting`; возраст продолжает расти. Кнопки мутаций блокируются в stale/offline, последняя схема остаётся видна с затемнением и возрастом. `mode=paused` при свежем heartbeat — нормальная связь.

Хранилище PostgreSQL сохраняет доменную историю 24 wall-часа. Memory ring SSE — последние 900 wall-секунд, ограничен дополнительно размером; слишком медленный клиент либо cursor с разрывом получает `reset` snapshot. Дедупликация доменных данных использует `(run_id,event_seq)`; одинаковые seq/state_version на heartbeat всё равно обновляют индикатор связи. WebSocket, polling каждую секунду и отдельная симуляция в браузере не нужны.

## Что UI должен знать о перепланировании

Инциденты автоматически запускают один coalesced расчёт. Рядом с Гантом появляется «Перепланирование» и elapsed wall time; поток state продолжает идти 1 Гц. На время расчёта новые операции могут быть задержаны launch barrier максимум на 5 wall-секунд, уже начатые продолжаются. Это правило симулятора явно показывается при необходимости как «ожидание нового плана», а не потеря связи.

Результат применяется только после проверки `run_id`, `input_revision`, config, `base_active_plan_id`, допустимости относительно фактически завершённого/начатого prefix и временной границы. Прогресс и ожидаемое завершение уже начатых операций сами по себе не делают результат устаревшим. Новый инцидент/manual action/play-pause-step-speed делает прежние входы устаревшими, worker возвращает `stale` и сервер пересчитывает актуальные условия. Клиент не переиспользует старый результат.

`no_feasible_plan`, `timeout` и `failed` — разные исходы. При отсутствии допустимого плана UI показывает причины и сохраняет выполняемые операции; невозможные новые старты блокируются. «Не найден за лимит» не означает «невозможность доказана». Не окрашивать такой результат зелёным и не считать его выполнением требования успешного перепланирования за 5 секунд.

Исторический план не применяется из replay. Для актуальной альтернативы кнопка доступна только при `can_apply=true`; `POST /plans/{id}/apply` может вернуть `409 PLAN_STALE`, если условия успели измениться. Старое расписание и новые forecast планы не меняют фактические положения поездов.

## История и replay

Replay по умолчанию охватывает последние **15 wall-минут**. Данные событий сохраняют и `server_time`, и `sim_time_s`; в ускоренном режиме вторичная шкала показывает учебное время. Для нового запуска история короче 15 минут — показываем фактический доступный диапазон. Для демонстрации slider сразу можно использовать заранее выполненный и сохранённый fixture-run с корректными timestamps, явно обозначенный как записанный сценарий.

`HistoryEvent`:

```ts
interface HistoryEvent {
  run_id: string; seq: number; server_time: string; sim_time_s: number;
  kind: string; entity_ids: string[]; message: string;
  actor_user_id: string | null;
}
```

Slider получает события за `[now−15min, now]` через `/history?run_id=...&from_wall_time=...&to_wall_time=...`, переходит по seq через `/history/snapshot?run_id=...&seq=...`. При `has_more=true` следующая страница использует `from_seq=next_from_seq` с теми же временными границами. Клиент может кэшировать прочитанные snapshots и проигрывать их с регулируемой скоростью UI; это replay сохранённого состояния, а не повторный расчёт симуляции. Действия управления станцией в replay скрыты/disabled. Live SSE остаётся подключён, складывает только последний live State. Возврат live мгновенно показывает его; позиции в history не меняют live cursor.

## Ошибки

Все HTTP-ошибки, включая schema validation, имеют одну оболочку:

```json
{
  "error":{
    "code":"REVISION_MISMATCH",
    "message":"Условия станции изменились. Обновите состояние и повторите действие.",
    "details":{"expected_input_revision":7,"current_input_revision":8},
    "request_id":"0df9e150-9ee7-45cb-b5e7-0c922741c2ec"
  }
}
```

`error.request_id` равен command UUID, если он был передан, иначе null; это поле не заменяет серверный trace ID. `details` — объект с конкретными полями причины; для 422 содержит `fields:[{path,message}]`, где path — путь JSON-поля. SSE-ошибка авторизации обрабатывается до открытия потока; после обрыва клиент проверяет `/auth/me`, а не ожидает JSON внутри EventSource.

| HTTP | Коды | Поведение клиента |
|---|---|---|
| 401 | `UNAUTHENTICATED`, `SESSION_EXPIRED` | Остановить stream, показать login |
| 403 | `FORBIDDEN` | Сообщить о роли; не retry |
| 404 | `NOT_FOUND`, `RUN_NOT_FOUND` | Обновить перечень/состояние |
| 409 | `REVISION_MISMATCH`, `RUN_MISMATCH`, `PLAN_STALE`, `INVALID_TRANSITION`, `PRECONDITION_FAILED`, `IDEMPOTENCY_MISMATCH` | Показать причину, обновить snapshot, не применять локальный эффект |
| 410 | `HISTORY_EXPIRED` | Предложить доступный диапазон, не подставлять live как историю |
| 422 | `VALIDATION_ERROR`, `UNKNOWN_SERVICE_PROFILE`, `PROFILE_REQUIREMENT_MISMATCH`, `SCENARIO_LIMIT_EXCEEDED` | Показать path поля и ограничение из details |
| 429 | `QUEUE_FULL` | Сообщить перегрузку, использовать `Retry-After`; запрос повторять с тем же request_id |
| 503 | `PLANNER_UNAVAILABLE`, `SERVICE_NOT_READY` | Показать отказ, оставить последнее состояние |

`no_feasible_plan` — успешный HTTP `200` чтения job с соответствующим статусом, не `500`. Network timeout команды не доказывает, что команда не выполнилась: повторяем с прежним request_id.

## Измерение отображения

Проверяем задержку от server `cause.ingested_at` до **видимого применения соответствующего состояния**, а не только до получения SSE. Не измеряем доменные события локальными animation timers.

Перед измерением выполнить 5 `/time` probes; для каждого сохранить `t0`/`t3` клиента и серверные `t1`/`t2`. Offset server−client = `((t1−t0)+(t2−t3))/2`, network RTT = `(t3−t0)−(t2−t1)`. Использовать sample с минимальным RTT, неопределённость = RTT/2; повторять каждые 30с. Использовать `Date.now()` для сопоставления UTC, локальные интервалы считать `performance.now()`; clock jump аннулирует калибровку. Отрицательная latency либо uncertainty>50ms — невалидный замер с повторной калибровкой, не успешное прохождение.

После state commit для данного seq измерить двойной `requestAnimationFrame` при `document.visibilityState==='visible'`, затем отправить:

`POST /api/v1/telemetry/ui-render`

```json
{
  "run_id":"run-demo-1","event_seq":122,
  "client_id":"7ccff519-5016-4ae7-b850-32dd8e025f15",
  "received_client_ms":1790841620120,"rendered_client_ms":1790841620137,
  "offset_ms":4,"uncertainty_ms":5,"visible":true
}
```

Это служебная телеметрия, не команда станции: envelope/revision не нужны, mutation state не происходит; auth нужна. `client_id` — UUID вкладки в sessionStorage; это не auth token. Один report на `(client_id,run_id,event_seq)`, отправка не ждёт ответа для рендера. Heartbeat с прежним seq не создаёт повторный report. Сервер связывает seq с ingress time и считает `rendered_client_ms + offset_ms − ingested_ms`; для проверки использует верхнюю оценку `+ uncertainty_ms`. Дополнительно сохраняются receive→render и количества пропущенных/невидимых samples. Двойной rAF — практический proxy paint, не аппаратное измерение пикселя; это ограничение указываем в отчёте.

React может объединить несколько входных state в один render. До paint хранить очередь pending seq, их received time и cause IDs; после paint отправить отдельный report для каждого реально покрытого seq с общим rendered time. Подтверждать более ранний seq можно только если его эффект/причина действительно видимы в итоговом состоянии или отрисованном журнале событий. Нельзя подтверждать потерянный инцидент только потому, что пришёл более новый seq. Промежуточные tick-progress разрешено визуально объединять, но факт такого объединения отмечается в протоколе измерения; приёмка инцидентов требует показа всех причин. Очередь pending ограничена, при переполнении пропуск учитывается, а не выдаётся за быстрый render.

Приёмка: минимум 120 wall-секунд foreground live, затем burst 5 и 10. Указать машину/браузер/сеть, число событий, p50/p95/max и неопределённость. Требование `<500ms` проверяется по max верхней оценки для всех измеряемых доменных обновлений, p95 приводится дополнительно. Если max не проходит, требование не закрыто. Не учитываем hidden tab как успешный sample; отдельно сообщаем её исключение. Replan `elapsed_ms` измеряет сервер от приёма инцидента до валидации/применения/публикации результата; `compute_ms` показывается отдельно.

`GET /metrics` возвращает текущий run и wall-интервал накопления. Значения UI latency разделяются по client_id; две машины не склеиваются в один якобы успешный sample. Рекомендуемый экран берёт таблицу готовых значений, не вычисляет перцентили сам:

```ts
interface DurationStats {
  sample_count: number;
  p50_ms: number | null; p95_ms: number | null; max_ms: number | null;
}
interface Metrics {
  run_id: string; window_started_at: string; measured_at: string;
  published_events: number; connected_clients: number;
  actor_queue_depth: number; planner_running_jobs: number; planner_pending_jobs: number;
  ui_render: {client_id: string; latency_upper: DurationStats; receive_to_render: DurationStats;
    invalid_samples: number; hidden_samples: number; unreported_events: number}[];
  replans: {elapsed: DurationStats; compute: DurationStats; succeeded: number;
    no_feasible_plan: number; stale: number; timeout: number; failed: number};
}
```

Нулевой sample_count означает null во всех перцентилях, а не нулевую задержку. `unreported_events` учитывает только новые domain seq, доставленные данной активной подписке и не подтверждённые telemetry в течение 2 wall-секунд; heartbeat и catch-up replay исключаются из SLA-потока. Неотображённые, объединённые React batching или потерянные события нельзя молча исключать из проверки: их количество публикуется, а ручная приёмка проверяет соответствие причин/состояния. Telemetry сама не порождает новое domain event.

## Fixtures для независимой разработки

На момент написания fixture-файлов ещё нет. Первый совместный результат этапа H0–H2 — TypeScript/OpenAPI contract и валидные JSON ниже. Друг может немедленно строить layout и типы из этого документа; до backend подключить один mock transport, выдающий **ту же** State-модель. Бизнес-правила в mock frontend не реализуются.

Планируемое расположение: `fixtures/api/v1/`. Backend владеет содержимым и проверкой ссылок/enum, frontend читает их без преобразования схемы. Все snapshots одного файла полные, все ID ссылаются на присутствующие entities. Fixture timestamp/seed явно подписаны как пример.

| Fixture | Содержание и проверка UI |
|---|---|
| `snapshot.initial.json` | `demo_main_v1`: 12 путей R1–R4/S1–S4/C1/C2/H/D1, парки reception/sorting/cargo/service, 6 грузовых поездов + fixed пассажирский P1, 60 грузовых вагонов в группах, ресурсы TL1–TL6/TP1/L1, маневровый локомотив в D1, paused sim=0, пустые incidents/conflicts, index с корректными raw/null |
| `stream.normal.json` | Кадры initial→freight arrival→inspection и passenger arrival→departure; у P1 нет inspection/cargo/groups. Seq растёт; motion только на связанном маршруте |
| `stream.incident.json` | Snapshot перед burst → batch из closure/delay/loss → replan running → два валидных кандидата → applied, одинаковая схема |
| `replan.succeeded.json` | Полный `{job,plans}` со списком изменённых операций, validator=true, разными forecast, без утверждения optimal |
| `replan.no-feasible.json` | Все допустимые маршруты/ресурсы недоступны в горизонте; причины, выполняемый prefix сохранён |
| `stream.reconnect.json` | Дубликат seq → правильный новый seq → `reset` после expired cursor → reset с новым run_id |
| `history.page.json`, `history.snapshot.json` | 15 минут записанного wall-интервала, связанный seq/sim-time, read-only state |
| `errors.json` | Примеры 401/403/409/422 в единой оболочке |
| `config.json`, `auth.*.json` | Конфиг и четыре роли; frontend проверяет скрытие действий |
| `snapshot.manual-service.json` | Inspection с execution_mode=manual и assigned_user_id: сначала can_complete=false, после минимального времени true; operator завершает своим endpoint, движение завершить нельзя |
| `metrics.empty.json`, `metrics.measured.json` | Нулевой sample_count/null и отдельные измерения двух client_id; никаких встроенных «хороших» чисел |
| `snapshot.passenger-running.json` | Обязательный P1 из main scenario на R1/R2: group_ids пуст, train_ids содержит P1, occupied_length 120м, встроенная тяга не прибавляется второй раз |
| `snapshot.service-p2.json` | Необязательное расширение SERVICE с включённым `service_transit_v1`; ядро не требует грузовой группы или cargo |

Исходные параметры основного fixture (времена — sim-секунды от scenario_epoch):

| Поезд | Профиль | Направление | Вагоны по группам | Прибытие | Плановый срок отправления |
|---|---|---|---|---:|---:|
| T1 | `freight_transit_v1` | W_E | G1:12 | 0 | 600 |
| T2 | `freight_local_v1` | W_E | G2L:4 + G2K:4 | 180 | 2700 |
| T3 | `freight_transit_v1` | E_W | G3:10 | 600 | 1500 |
| T4 | `freight_reclassify_v1` | W_E | G4A:3 + G4B:3 + G4K:4 | 840 | 3900 |
| T5 | `freight_local_v1` | W_E | G5L:4 + G5K:4 | 1920 | 5100 |
| T6 | `freight_transit_v1` | E_W | G6:12 | 2700 | 4200 |
| P1 | `passenger_transit_v1` | W_E | Фиксированный самоходный состав 120м, без WagonGroup | 720 | 960 |

Всего 60 грузовых вагонов, каждый имеет свой ID, плюс один неделимый пассажирский состав P1. Группы сохраняют принадлежность и количество. Вагон длиной 14м, поездной локомотив 20м — явно учебные исходные данные. Грузовые профили дают три цепочки: freight_transit — arrival/inspection/departure; freight_local — inspection, два shunt_transfer, cargo и departure_prep между прибытием и отправлением; freight_reclassify — inspection, четыре shunt_transfer и departure_prep между прибытием и отправлением. Пассажирский профиль — arrival120с→departure120с, без inspection/cargo/departure_prep. Основной fixture содержит 31 грузовую макрооперацию и 2 пассажирские. Генератор операций находится на backend, frontend получает готовые predecessor_ids.

Ниже полные, совместимые с типами примеры отдельных entities для fixture-файла. Это не команды и не сетевые partial updates. Числа задают исходный сценарий и не изображают измеренный эффект оптимизации.

```json
{
  "id":"T2","number":"2002","type":"FREIGHT",
  "service_profile_id":"freight_local_v1","processing_kind":"local","direction":"W_E",
  "consist_kind":"wagon_groups","body_length_m":112,"total_length_m":132,
  "location":{"kind":"boundary","boundary_id":"BW","track_id":null,
    "route_id":null,"operation_id":null,"route_progress":null},
  "priority":2,"status":"expected","destination_id":"DEST_E",
  "scheduled_arrival_sim_s":180,"expected_arrival_sim_s":180,
  "due_departure_sim_s":2700,"actual_arrival_sim_s":null,"actual_departure_sim_s":null,
  "group_ids":["G2L","G2K"],"target_group_ids":["G2L","G2K"],
  "traction_resource_id":"TL2","traction_kind":"locomotive",
  "planned_track_id":null,"current_track_id":null
}
```

```json
{
  "id":"P1","number":"P001","type":"PASSENGER",
  "service_profile_id":"passenger_transit_v1","processing_kind":"transit","direction":"W_E",
  "consist_kind":"fixed","body_length_m":120,"total_length_m":120,
  "location":{"kind":"boundary","boundary_id":"BW","track_id":null,
    "route_id":null,"operation_id":null,"route_progress":null},
  "priority":3,"status":"expected","destination_id":"DEST_E",
  "scheduled_arrival_sim_s":720,"expected_arrival_sim_s":720,
  "due_departure_sim_s":960,"actual_arrival_sim_s":null,"actual_departure_sim_s":null,
  "group_ids":[],"target_group_ids":[],
  "traction_resource_id":"TP1","traction_kind":"self_propelled",
  "planned_track_id":null,"current_track_id":null
}
```

```json
{
  "id":"G2L","origin_train_id":"T2","assigned_train_id":"T2","current_train_id":"T2",
  "wagon_ids":["G2L-W01","G2L-W02","G2L-W03","G2L-W04"],
  "wagon_count":4,"length_m":56,"destination_id":"DEST_E","cargo_state":"unloaded",
  "location":{"kind":"boundary","boundary_id":"BW","track_id":null,
    "route_id":null,"operation_id":null,"route_progress":null}
}
```

```json
{
  "id":"L1","name":"Маневровый локомотив","kind":"shunting_locomotive",
  "status":"available","active_operation_id":null,
  "location":{"kind":"track","boundary_id":null,"track_id":"D1",
    "route_id":null,"operation_id":null,"route_progress":null},
  "available_after_sim_s":null,"assigned_user_id":null
}
```

```json
{
  "id":"R3","name":"Приёмо-отправочный 3","park_id":"reception",
  "kind":"receiving_departure","length_m":350,"availability":"open",
  "occupied_length_m":0,"train_ids":[],"group_ids":[],"locomotive_ids":[],
  "assigned_train_id":null,"active_operation_ids":[]
}
```

```json
{
  "mode":"actual","formula_version":"efficiency-v1",
  "window_start_sim_s":0,"window_end_sim_s":0,"score":null,"category":null,
  "factors":[
    {"key":"throughput","raw":null,"unit":"ratio","norm_penalty":null,"weight":0.3,"contribution":null,"reason":"Окно наблюдения ещё пусто"},
    {"key":"delay","raw":null,"unit":"sim_seconds","norm_penalty":null,"weight":0.25,"contribution":null,"reason":"Окно наблюдения ещё пусто"},
    {"key":"occupancy","raw":null,"unit":"ratio","norm_penalty":null,"weight":0.15,"contribution":null,"reason":"Окно наблюдения ещё пусто"},
    {"key":"conflicts","raw":null,"unit":"count","norm_penalty":null,"weight":0.2,"contribution":null,"reason":"Окно наблюдения ещё пусто"},
    {"key":"resource_idle","raw":null,"unit":"ratio","norm_penalty":null,"weight":0.1,"contribution":null,"reason":"Окно наблюдения ещё пусто"}
  ]
}
```

Mock transport предоставляет `getSnapshot`, `subscribe`, `sendCommand`, `getHistory`, `getReplan`; переключатель `VITE_DATA_SOURCE=fixtures|api`. В fixtures режиме кнопки могут выбирать готовую последовательность снимков; они не рассчитывают расписание и не объявляют его результатом backend. На защите режим строго `api`, надпись fixture не скрывается при автономной разработке.

## Проверка готовности frontend

- Все данные экрана принадлежат одному run/seq, таблицы и схема не рассинхронизируются при замене State.
- Train type, service_profile_id, processing_kind, direction и priority показываются раздельно; факт и forecast раздельны. P1 присутствует в основном демо и не получает Cargo/WagonGroup/inspection автоматически.
- Fixed состав занимает путь и виден на схеме при пустом group_ids; его integrated traction не удваивает длину. Cargo/shunt зависят от profile capabilities, а не от сравнения Train.type.
- Каждая кнопка из таблицы экранов имеет endpoint; у недоступной по статусу/роли команды есть понятная причина.
- Занятый/закрываемый путь, unavailable_pending ресурс, running shunt, blocked operation различимы.
- SSE продолжает обновляться при replan, неизвестном результате и 10 инцидентах.
- Потеря сети/401/stale/reset корректно отражаются; reconnect не дублирует команды.
- Replay не запускает live-команды, возвращение live восстанавливает последний кадр.
- CSV открывается; config server validation отображается; 403 виден и не обходится UI.
- Latency instrumentation привязана к показанному seq; live demo использует backend, не fixtures.
