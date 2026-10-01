/** Presentation only: no transport, timers, commands, scheduling or KPI calculations. */
export const LABELS = Object.freeze(Object.fromEntries(Object.entries({
  trainType: { FREIGHT: "Грузовой", PASSENGER: "Пассажирский", SERVICE: "Служебный", OTHER: "Прочий" },
  profile: { freight_transit_v1: "Грузовой транзит", freight_local_v1: "Местная грузовая работа", freight_reclassify_v1: "Переработка групп", passenger_transit_v1: "Пассажирский транзит", service_transit_v1: "Служебный транзит" },
  processingKind: { transit: "Транзит", local: "Местная работа", reclassify: "Переработка" },
  trainStatus: { expected: "Ожидается", waiting_entry: "Ожидает приёма", on_station: "На станции", ready_departure: "Готов к отправлению", departed: "Отправлен" },
  operationKind: { arrival: "Приём на путь", inspection: "Осмотр состава", shunt_transfer: "Маневровая перестановка", cargo: "Погрузка", departure_prep: "Подготовка к отправлению", departure: "Отправление со станции" },
  operationStatus: { pending: "Ожидает планирования", planned: "Запланирована", running: "Выполняется", completed: "Завершена", blocked: "Заблокирована" },
  phase: { empty_to_source: "Подача локомотива к группе", couple: "Сцепка и подготовка", pull_to_lead: "Вытягивание на H", reverse: "Смена направления на H", push_to_target: "Осаживание на целевой путь", uncouple: "Отцепка и подготовка", return_to_depot: "Возврат на стоянку D1" },
  resourceKind: { shunting_locomotive: "Маневровый локомотив", train_locomotive: "Поездной локомотив", self_propelled_unit: "Встроенная тяга состава", shunting_crew: "Составительская бригада", inspection_crew: "Осмотрщики вагонов", cargo_crew: "Грузовая бригада", train_crew: "Локомотивная бригада" },
  resourceStatus: { available: "Доступен", busy: "Занят", unavailable: "Недоступен", unavailable_pending: "Отключение после освобождения" },
  role: { viewer: "Наблюдатель", operator: "Исполнитель технологической операции", dispatcher: "Дежурный по станции", admin: "Администратор системы" },
  trackKind: { receiving_departure: "Приёмо-отправочный путь", sorting: "Сортировочный путь", cargo: "Грузовой путь", lead: "Вытяжной путь", parking: "Стоянка локомотива" },
  availability: { open: "Открыт", closed: "Закрыт", closure_pending: "Закроется после освобождения" },
  direction: { W_E: "Запад → Восток", E_W: "Восток → Запад" },
  priority: { 1: "Низкий", 2: "Обычный", 3: "Высокий" },
  incidentKind: { train_delay: "Опоздание поезда", track_closure: "Закрытие пути", resource_loss: "Потеря ресурса", destination_block: "Ограничение приёма назначения" },
  incidentStatus: { active: "Действует", pending: "Ожидает освобождения", resolved: "Завершён" },
  conflictKind: { route_overlap: "Конфликт маршрутов", track_occupied: "Конфликт использования пути", resource_unavailable: "Ресурс недоступен", precedence: "Нарушена последовательность", capacity: "Недостаточная вместимость", destination_closed: "Следующая станция не принимает" },
  severity: { warning: "Предупреждение", critical: "Критично" },
  planStatus: { proposed: "Предложен", active: "Применён", superseded: "Заменён", stale: "Устарел", rejected: "Отклонён" },
  planValidity: { feasible: "Допустим", invalid: "Недопустим" },
  replanStatus: { queued: "В очереди", running: "Идёт расчёт", succeeded: "Расчёт завершён", no_feasible_plan: "Допустимый план не найден", stale: "Результат устарел", timeout: "Лимит времени", failed: "Ошибка расчёта" },
  mode: { paused: "Пауза", running: "Симуляция работает" },
  cargoState: { unloaded: "Не погружена", loaded: "Погружена", not_applicable: "Без грузовой операции" },
  efficiencyCategory: { normal: "Норма", attention: "Внимание", critical: "Критично" },
  consistKind: { wagon_groups: "Группы вагонов", fixed: "Неделимый состав" },
  executionMode: { auto: "Автоматически", manual: "Подтверждение исполнителя" },
}).map(([kind, values]) => [kind, Object.freeze(values)])));

// A screen persona, not a claim about the authority or duties of a real railway employee.
export const DISPATCHER_NOTE = "Дежурный по станции — учебное рабочее место планирования. Совмещение поездных и маневровых задач здесь — упрощение; права задаёт роль приложения dispatcher.";
export function label(kind, value) {
  if (value == null) return "—";
  return Object.hasOwn(LABELS[kind] ?? {}, value) ? LABELS[kind][value] : `Неизвестно (${value})`;
}
export const trainTypeLabel = (value) => label("trainType", value);
export const profileLabel = (value) => label("profile", value);
export const operationLabel = (value) => label("operationKind", value);
/** A contextual caption, not an operation/workflow decision. The routes stay server-authored. */
export function operationCaption(operation, state) {
  if (operation.kind !== "shunt_transfer") return operationLabel(operation.kind);
  const source = state.tracks.find(track => track.id === operation.source_track_id);
  const target = state.tracks.find(track => track.id === operation.target_track_id);
  if (target?.kind === "cargo") return "Подача на грузовой фронт";
  if (source?.kind === "cargo") return "Уборка с грузового фронта";
  if (target?.kind === "sorting") return "Подача на сортировочный путь";
  if (source?.kind === "sorting") return "Возврат группы в состав";
  return operationLabel(operation.kind);
}
export const phaseLabel = (value) => label("phase", value);
export const resourceLabel = (value) => label("resourceKind", value);
export const roleLabel = (value) => label("role", value);

/** Always scenario_epoch + supplied sim seconds; never browser wall time or speed. */
export function displayTime(state, simSeconds = state?.sim_time_s) {
  if (simSeconds == null || !Number.isFinite(simSeconds)) return "—";
  const instant = new Date(Date.parse(state?.scenario_epoch) + simSeconds * 1000);
  if (!Number.isFinite(instant.getTime())) return "—";
  const parts = Object.fromEntries(new Intl.DateTimeFormat("ru-RU", {
    timeZone: "Asia/Almaty", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).formatToParts(instant).map(({ type, value }) => [type, value]));
  return `${parts.hour}:${parts.minute}:${parts.second} · ${parts.day}.${parts.month}.${parts.year} (учебная дата, Asia/Almaty)`;
}

const boundaryLabel = (id) => ({ BW: "BW · западная граница", BE: "BE · восточная граница" })[id] ?? `Граница ${id}`;
const endpointLabel = (id) => id === "BW" || id === "BE" ? boundaryLabel(id) : `путь ${id}`;
export function locationLabel(location, state = null) {
  if (!location) return "Местоположение не задаётся";
  if (location.kind === "departed") return "За пределами станции: отправлен";
  if (location.kind === "boundary") return boundaryLabel(location.boundary_id);
  if (location.kind === "track") return location.track_id === "D1" ? "Стоянка D1" : `Путь ${location.track_id}`;
  if (location.kind === "route") {
    const route = state?.station?.layout?.routes?.find((item) => item.id === location.route_id);
    const name = route ? `${endpointLabel(route.from_id)} → ${endpointLabel(route.to_id)}` : location.route_id;
    const progress = Number.isFinite(location.route_progress) ? ` · ${Math.round(location.route_progress * 100)}%` : "";
    return `Движение: ${name}${progress}`;
  }
  return "Местоположение неизвестно";
}

/** Arc-length positioning on the backend polyline, including unequal-length segments. */
export function pointOnPolyline(points, progress) {
  if (!Array.isArray(points) || !points.length || !Number.isFinite(progress)) return null;
  if (!points.every((point) => Array.isArray(point) && point.length === 2 && point.every(Number.isFinite))) return null;
  const lengths = points.slice(1).map((point, i) => Math.hypot(point[0] - points[i][0], point[1] - points[i][1]));
  let remaining = lengths.reduce((a, b) => a + b, 0) * Math.max(0, Math.min(1, progress));
  for (let i = 0; i < lengths.length; i += 1) {
    if (lengths[i] > 0 && (remaining <= lengths[i] || i === lengths.length - 1)) {
      const t = remaining / lengths[i], [x, y] = points[i], [nx, ny] = points[i + 1];
      return { x: x + (nx - x) * t, y: y + (ny - y) * t, angle: Math.atan2(ny - y, nx - x) * 180 / Math.PI };
    }
    remaining -= lengths[i];
  }
  const [x, y] = points.at(-1);
  return { x, y, angle: 0 };
}

/** Display relationships only: highlights are supplied incidents/conflicts, not new detection. */
export function affectedIds(state) {
  const ids = new Set(), operationIds = new Set();
  for (const incident of state.incidents ?? []) {
    if (incident.status === "resolved") continue;
    ids.add(incident.target_id);
    for (const id of incident.affected_operation_ids ?? []) operationIds.add(id);
  }
  for (const conflict of state.conflicts ?? []) {
    for (const id of conflict.entity_ids ?? []) ids.add(id);
    for (const id of conflict.operation_ids ?? []) operationIds.add(id);
  }
  for (const op of state.operations ?? []) {
    if (!operationIds.has(op.id) && !ids.has(op.id)) continue;
    for (const id of [op.id, op.train_id, op.source_track_id, op.target_track_id, ...(op.group_ids ?? []), ...(op.resource_ids ?? []), ...(op.route_ids ?? [])]) {
      if (id != null) ids.add(id);
    }
  }
  return ids;
}

/** A train glyph includes attached groups and its train traction; only detached groups are separate. */
export function stationEntities(state) {
  const trains = (state.trains ?? []).map((train) => ({
    id: train.id, kind: "train", entity: train, location: train.location,
    text: `${train.number} · ${train.id}`, detail: `${trainTypeLabel(train.type)} · ${label("trainStatus", train.status)}`,
    relatedIds: [train.id, train.traction_resource_id, ...(train.group_ids ?? [])],
  }));
  const groups = (state.wagon_groups ?? []).filter((group) => group.current_train_id === null).map((group) => ({
    id: group.id, kind: "group", entity: group, location: group.location,
    text: `${group.id} · ${group.wagon_count} ваг.`, detail: `Отцепленная группа · ${label("cargoState", group.cargo_state)}`,
    relatedIds: [group.id],
  }));
  const locomotives = (state.resources ?? []).filter((resource) => resource.kind === "shunting_locomotive").map((resource) => ({
    id: resource.id, kind: "locomotive", entity: resource, location: resource.location,
    text: resource.id, detail: `${resourceLabel(resource.kind)} · ${label("resourceStatus", resource.status)}`,
    relatedIds: [resource.id],
  }));
  return [...trains, ...groups, ...locomotives].filter((item) => item.location && item.location.kind !== "departed");
}

const NS = "http://www.w3.org/2000/svg";
const COLOR = Object.freeze({
  text: "var(--rail-text, #dce6f3)", muted: "var(--rail-muted, #9aabc0)",
  track: "var(--rail-track, #52647b)", occupied: "var(--rail-occupied, #5ab8de)",
  reserved: "var(--rail-reserved, #d7ac54)", route: "var(--rail-route, #68e0bd)",
  warning: "var(--rail-warning, #ffac66)", closed: "var(--rail-closed, #fb7185)",
  selected: "var(--rail-selected, #ffffff)", card: "var(--rail-card, #152337)",
});

/**
 * Replace only this SVG's contents. onSelect(id) is a display callback, never a command.
 * Returns {renderedEntityIds, activeRouteIds, affectedIds} for presentation diagnostics.
 */
export function renderStation(svg, state, onSelect = () => {}, selectedId = null) {
  const doc = svg.ownerDocument, layout = state.station.layout;
  const [vx, vy, vw, vh] = layout.view_box;
  const previousFocus = svg.contains?.(doc.activeElement) ? doc.activeElement?.getAttribute("data-entity-id") : null;
  const focusTargets = new Map();
  const node = (tag, attrs = {}, text = null) => {
    const element = doc.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attrs)) if (value != null) element.setAttribute(key, String(value));
    if (text != null) element.textContent = String(text);
    return element;
  };
  const root = node("g", { class: "railway-scene", "font-family": "system-ui, sans-serif", "font-size": 13, fill: COLOR.text });
  const text = (parent, x, y, content, attrs = {}) => parent.append(node("text", { x, y, ...attrs }, content));
  const title = (parent, content) => parent.append(node("title", {}, content));
  const line = (parent, points, attrs = {}) => parent.append(node("polyline", { points: points.map((point) => point.join(",")).join(" "), fill: "none", "stroke-linecap": "round", "stroke-linejoin": "round", ...attrs }));
  const selectable = (element, id, description) => {
    element.setAttribute("data-entity-id", id); element.setAttribute("role", "button");
    element.setAttribute("tabindex", "0"); element.setAttribute("aria-label", description);
    element.setAttribute("aria-pressed", String(id === selectedId)); element.style.cursor = "pointer";
    element.addEventListener("click", () => onSelect(id));
    element.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(id); }
    });
    focusTargets.set(id, element);
  };
  svg.setAttribute("viewBox", layout.view_box.join(" "));
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.setAttribute("role", "group");
  svg.setAttribute("aria-label", "Учебная схема станции: фактические положения, занятость и блокировки от backend");
  title(root, `${state.station.name}. ${displayTime(state)}. Положение внутри пути условное; маршруты и прогресс заданы сервером.`);
  text(root, vx + 24, vy + 28, state.station.name, { "font-size": 19, "font-weight": 700 });
  text(root, vx + 24, vy + 49, "Учебная схема · без масштаба · координаты внутри пути условные", { fill: COLOR.muted, "font-size": 12 });

  const nodes = new Map(layout.nodes.map((item) => [item.id, item]));
  const routes = new Map(layout.routes.map((item) => [item.id, item]));
  const geometries = new Map(layout.tracks.map((item) => [item.track_id, item.points]));
  const affected = affectedIds(state), entities = stationEntities(state);
  const runningIds = new Set(state.operations.filter((op) => op.status === "running").map((op) => op.id));

  // Park bands are layout labels, not additional physical resources.
  for (const park of state.station.parks.filter((item) => item.kind !== "service")) {
    const points = state.tracks.filter((track) => track.park_id === park.id).flatMap((track) => geometries.get(track.id) ?? []);
    if (!points.length) continue;
    const minX = Math.min(...points.map((p) => p[0])), maxX = Math.max(...points.map((p) => p[0]));
    const minY = Math.min(...points.map((p) => p[1])), maxY = Math.max(...points.map((p) => p[1]));
    root.append(node("rect", { x: minX - 20, y: minY - 34, width: maxX - minX + 40, height: maxY - minY + 65, rx: 12, fill: "var(--rail-park, #142135)", opacity: .75 }));
    const parkTitle = { receiving_departure: "Приёмо-отправочный парк", sorting: "Сортировочный парк", cargo: "Грузовой район" }[park.kind] ?? park.name;
    text(root, minX, minY - 45, parkTitle, { fill: COLOR.muted, "font-size": 13 });
  }
  for (const edge of layout.edges) {
    const a = nodes.get(edge.from_node_id), b = nodes.get(edge.to_node_id);
    if (a && b) line(root, [[a.x, a.y], [b.x, b.y]], { stroke: COLOR.track, "stroke-width": 2, opacity: .65, class: "railway-edge" });
  }

  for (const track of state.tracks) {
    const points = geometries.get(track.id); if (!points) continue;
    const group = node("g", { class: "railway-track", "data-availability": track.availability, "data-occupied-m": track.occupied_length_m });
    const reserved = track.active_operation_ids.filter((id) => runningIds.has(id));
    const description = `${track.id}: ${label("trackKind", track.kind)}; ${label("availability", track.availability)}; факт ${track.occupied_length_m} из ${track.length_m} м${reserved.length ? `; резерв выполняемых операций: ${reserved.join(", ")}` : ""}${track.assigned_train_id ? `; закреплён за ${track.assigned_train_id}` : ""}`;
    selectable(group, track.id, description); title(group, description);
    if (selectedId === track.id) line(group, points, { stroke: COLOR.selected, "stroke-width": 15, opacity: .25 });
    if (affected.has(track.id)) line(group, points, { stroke: COLOR.warning, "stroke-width": 12, opacity: .3 });
    if (reserved.length) line(group, points, { stroke: COLOR.reserved, "stroke-width": 10, "stroke-dasharray": "8 7", opacity: .6, class: "railway-reservation" });
    line(group, points, { stroke: COLOR.track, "stroke-width": 5 });
    // Presence highlight only; occupied_length_m is the backend scalar, not an inferred position.
    if (track.occupied_length_m > 0) line(group, points, { stroke: COLOR.occupied, "stroke-width": 5, class: "railway-occupied" });
    if (track.availability !== "open") line(group, points, { stroke: track.availability === "closed" ? COLOR.closed : COLOR.warning, "stroke-width": 5, "stroke-dasharray": "4 7" });
    const [startX, startY] = points[0], [endX, endY] = points.at(-1);
    text(group, startX, startY - 15, `${track.id}${track.kind === "lead" ? " · вытяжной" : track.kind === "parking" ? " · стоянка" : ""}`, { "font-weight": 700 });
    if (Math.abs(endY - startY) < 10) text(group, endX, endY - 15, `${track.length_m} м · факт ${track.occupied_length_m} м`, { "text-anchor": "end", fill: COLOR.muted, "font-size": 11 });
    else text(group, startX, startY + 23, `${track.length_m} м · факт ${track.occupied_length_m} м`, { fill: COLOR.muted, "font-size": 11 });
    if (track.availability !== "open") text(group, endX + 10, endY + 5, track.availability === "closed" ? "ЗАКРЫТ" : "ЗАКРЫТИЕ ОЖИДАЕТ", { fill: track.availability === "closed" ? COLOR.closed : COLOR.warning, "font-size": 10 });
    line(group, points, { stroke: "transparent", "stroke-width": 20 });
    root.append(group);
  }

  const activeRouteIds = new Set(entities.filter((item) => item.location.kind === "route").map((item) => item.location.route_id));
  for (const id of activeRouteIds) {
    const route = routes.get(id); if (!route) continue;
    const group = node("g", { class: "railway-active-route", "data-route-id": id });
    title(group, `Текущее движение: ${endpointLabel(route.from_id)} → ${endpointLabel(route.to_id)}`);
    line(group, route.points, { stroke: COLOR.route, "stroke-width": 3.5, opacity: .95 });
    root.append(group);
  }

  for (const zone of state.zones) {
    const point = nodes.get(`Z${zone.id}`); if (!point) continue;
    const group = node("g", { class: "railway-zone", "data-operation-id": zone.active_operation_id ?? "" });
    const busy = zone.active_operation_id !== null; // Authoritative lock; never inferred from routes.
    const description = `Горловина ${zone.id}: ${zone.availability === "closed" ? "закрыта" : busy ? `занята операцией ${zone.active_operation_id}` : "свободна"}`;
    selectable(group, zone.id, description); title(group, description);
    group.append(node("circle", { cx: point.x, cy: point.y, r: 11, fill: COLOR.card, stroke: zone.availability === "closed" ? COLOR.closed : busy ? COLOR.reserved : COLOR.track, "stroke-width": 4 }));
    text(group, point.x, point.y - 20, `${zone.id} · ${zone.availability === "closed" ? "закрыта" : busy ? "занята" : "свободна"}`, { "text-anchor": "middle", fill: busy ? COLOR.reserved : COLOR.muted, "font-size": 11 });
    if (selectedId === zone.id || affected.has(zone.id)) group.append(node("circle", { cx: point.x, cy: point.y, r: 17, fill: "none", stroke: selectedId === zone.id ? COLOR.selected : COLOR.warning, "stroke-width": 2 }));
    root.append(group);
  }
  for (const boundary of layout.nodes.filter((item) => item.kind === "boundary")) {
    root.append(node("circle", { cx: boundary.x, cy: boundary.y, r: 5, fill: COLOR.muted }));
    const right = boundary.x > vx + vw / 2;
    text(root, boundary.x, boundary.y + 27, boundary.id, { "text-anchor": right ? "end" : "start", "font-weight": 700 });
    text(root, right ? vx + vw - 24 : vx + 24, vy + 80, `${boundaryLabel(boundary.id)} · вне станции`, { "text-anchor": right ? "end" : "start", fill: COLOR.muted, "font-size": 11 });
  }

  const clusterCounts = new Map(), boundaryCounts = new Map(), renderedEntityIds = [];
  for (const item of entities) {
    const loc = item.location;
    let point = null;
    if (loc.kind === "route") point = pointOnPolyline(routes.get(loc.route_id)?.points, loc.route_progress);
    if (loc.kind === "track") {
      const endpoint = layout.routes.find((route) => route.to_id === loc.track_id)?.points.at(-1);
      point = endpoint ? { x: endpoint[0], y: endpoint[1], angle: 0 } : pointOnPolyline(geometries.get(loc.track_id), .5);
    }
    if (loc.kind === "boundary") point = nodes.get(loc.boundary_id) ?? null;
    if (!point) continue; // No invented location when geometry/reference is missing.
    const width = item.kind === "locomotive" ? 52 : 126;
    let x = Math.max(vx + width / 2 + 8, Math.min(vx + vw - width / 2 - 8, point.x)), y = point.y;
    if (loc.kind === "boundary") {
      const rank = boundaryCounts.get(loc.boundary_id) ?? 0; boundaryCounts.set(loc.boundary_id, rank + 1);
      const right = point.x > vx + vw / 2;
      x = right ? vx + vw - width / 2 - 24 : vx + width / 2 + 24;
      y = vy + 105 + rank * 30;
    } else {
      // Labels sharing a physical anchor fan out; the dot/leader keeps that anchor explicit.
      const key = `${point.x.toFixed(1)}:${point.y.toFixed(1)}`;
      const rank = clusterCounts.get(key) ?? 0; clusterCounts.set(key, rank + 1);
      y += rank * 26 + (loc.kind === "track" && loc.track_id === "H" ? 52 : 0);
    }
    const group = node("g", { class: `railway-entity railway-${item.kind}`, "data-location-kind": loc.kind, "data-route-progress": loc.route_progress, "data-anchor-x": point.x, "data-anchor-y": point.y });
    const description = `${item.text}: ${item.detail}; ${locationLabel(loc, state)}`;
    selectable(group, item.id, description); title(group, description);
    const selected = item.relatedIds.includes(selectedId), warned = item.relatedIds.some((id) => affected.has(id));
    const color = item.kind === "locomotive" ? "var(--rail-locomotive, #5bc6d6)" : item.kind === "group" ? "var(--rail-wagon, #edb966)" : item.entity.type === "PASSENGER" ? "var(--rail-passenger, #b5a1ee)" : "var(--rail-freight, #89bfe3)";
    if (loc.kind !== "boundary" && (x !== point.x || y !== point.y)) {
      line(group, [[point.x, point.y], [x, y]], { stroke: color, "stroke-width": 1.5 });
      group.append(node("circle", { cx: point.x, cy: point.y, r: 3, fill: color }));
    }
    group.append(node("rect", { x: x - width / 2, y: y - 11, width, height: 22, rx: item.kind === "group" ? 3 : 7, fill: COLOR.card, stroke: selected ? COLOR.selected : warned ? COLOR.warning : color, "stroke-width": selected || warned ? 3 : 2 }));
    text(group, x, y + 4, item.text, { "text-anchor": "middle", fill: color, "font-size": 11, "font-weight": 700 });
    if (warned) text(group, x + width / 2 + 5, y + 4, "!", { fill: COLOR.warning, "font-weight": 800 });
    if (item.kind === "locomotive" && ["unavailable", "unavailable_pending"].includes(item.entity.status)) text(group, x, y + 26, item.entity.status === "unavailable" ? "НЕДОСТУПЕН" : "ОТКЛЮЧЕНИЕ ОЖИДАЕТ", { "text-anchor": "middle", fill: COLOR.warning, "font-size": 10 });
    root.append(group); renderedEntityIds.push(item.id);
  }
  text(root, vx + 24, vy + vh - 18, "Сплошная линия — путь · пунктир — резерв операции · движущийся маркер — положение от сервера · ! — затронут инцидентом", { fill: COLOR.muted, "font-size": 11 });
  svg.replaceChildren(root);
  if (previousFocus && focusTargets.has(previousFocus)) focusTargets.get(previousFocus).focus?.({ preventScroll: true });
  return { renderedEntityIds, activeRouteIds: [...activeRouteIds], affectedIds: [...affected] };
}
