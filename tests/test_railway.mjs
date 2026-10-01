import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { LABELS, DISPATCHER_NOTE, label, operationCaption, displayTime, locationLabel, pointOnPolyline, affectedIds, stationEntities, renderStation } from "../src/digital_station/static/railway.js";

const initial = JSON.parse(await readFile(new URL("../fixtures/api/v1/snapshot.initial.json", import.meta.url), "utf8"));
const copy = () => structuredClone(initial);
const at = (kind, fields = {}) => ({ kind, boundary_id: null, track_id: null, route_id: null, operation_id: null, route_progress: null, ...fields });

test("Russian railway labels do not turn the parking track into a depot or a role into railway authority", () => {
  assert.equal(label("resourceKind", "shunting_crew"), "Составительская бригада");
  assert.equal(label("resourceKind", "inspection_crew"), "Осмотрщики вагонов");
  assert.equal(label("conflictKind", "track_occupied"), "Конфликт использования пути");
  assert.equal(label("resourceKind", "train_crew"), "Локомотивная бригада");
  assert.equal(label("role", "dispatcher"), "Дежурный по станции");
  assert.match(DISPATCHER_NOTE, /учебное/);
  assert.match(label("phase", "return_to_depot"), /стоянку D1/);
  assert.equal(label("phase", null), "—");
  assert.match(label("trainType", "UNKNOWN"), /Неизвестно/);
  assert.ok(Object.isFrozen(LABELS.phase));
  const unplanned = initial.operations.find(o => o.id === "op-T2-shunt-out");
  assert.equal(operationCaption(unplanned, initial), "Маневровая перестановка");
  for (const [source, target, expected] of [["R3", "C1", "Подача на грузовой фронт"], ["C1", "R3", "Уборка с грузового фронта"], ["R4", "S1", "Подача на сортировочный путь"], ["S1", "R4", "Возврат группы в состав"]]) {
    assert.equal(operationCaption({...unplanned, source_track_id:source, target_track_id:target}, initial), expected);
  }
});

test("time uses scenario epoch plus sim seconds in Almaty, independent of wall time and speed", () => {
  const state = copy();
  assert.equal(displayTime(state, 0), "13:00:00 · 01.10.2026 (учебная дата, Asia/Almaty)");
  state.sim_time_s = 125; state.speed = 10; state.server_time = "2099-01-01T00:00:00Z";
  assert.match(displayTime(state), /^13:02:05 · 01.10.2026/);
  assert.match(displayTime(state, 86400), /02.10.2026/);
  assert.equal(displayTime(state, null), "—");
  assert.equal(displayTime({ scenario_epoch: "bad", sim_time_s: 1 }), "—");
});

test("route points follow segment length and backend progress, not straight-line interpolation", () => {
  assert.deepEqual(pointOnPolyline([[0, 0], [100, 0], [100, 300]], .5), { x: 100, y: 100, angle: 90 });
  assert.deepEqual(pointOnPolyline([[3, 4], [3, 4]], .5), { x: 3, y: 4, angle: 0 });
  assert.deepEqual(pointOnPolyline([[0, 0], [10, 0]], 2), { x: 10, y: 0, angle: 0 });
  assert.equal(pointOnPolyline([], .5), null);
  assert.equal(pointOnPolyline([[0, 0], [10, 0]], null), null);
});

test("fixed trains, attached groups and train traction are not duplicated; detached groups are separate", () => {
  const state = copy();
  assert.equal(stationEntities(state).length, 8); // seven trains plus L1
  assert.equal(stationEntities(state).filter((item) => item.id === "P1").length, 1);
  assert.ok(!stationEntities(state).some((item) => ["TP1", "TL1", "G1"].includes(item.id)));
  state.wagon_groups.find((item) => item.id === "G2L").current_train_id = null;
  assert.ok(stationEntities(state).some((item) => item.id === "G2L"));
  state.trains[0].location = at("departed");
  assert.ok(!stationEntities(state).some((item) => item.id === state.trains[0].id));
});

test("locations and affected IDs display only backend facts and their explicit relationships", () => {
  const state = copy(), op = state.operations[0];
  op.resource_ids = ["I1"]; op.target_track_id = "R1";
  state.incidents = [
    { target_id: "R2", status: "resolved", affected_operation_ids: [] },
    { target_id: "I1", status: "pending", affected_operation_ids: [op.id] },
  ];
  state.conflicts = [{ entity_ids: ["S1"], operation_ids: [] }];
  assert.deepEqual(new Set(affectedIds(state)), new Set(["I1", "S1", op.id, op.train_id, "R1", ...op.group_ids]));
  assert.equal(locationLabel(at("track", { track_id: "D1" })), "Стоянка D1");
  const route = state.station.layout.routes[0];
  assert.match(locationLabel(at("route", { route_id: route.id, operation_id: op.id, route_progress: 0 }), state), /0%$/);
});

// Tiny DOM double: tests actual SVG construction without adding a browser or runtime dependency.
class Element {
  constructor(tag, ownerDocument) { this.tagName = tag; this.ownerDocument = ownerDocument; this.attributes = {}; this.children = []; this.style = {}; this.handlers = {}; this.textContent = ""; }
  setAttribute(key, value) { this.attributes[key] = value; }
  getAttribute(key) { return this.attributes[key] ?? null; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(kind, handler) { this.handlers[kind] = handler; }
  contains(element) { return this === element || this.children.some((child) => child.contains(element)); }
  focus() { this.ownerDocument.activeElement = this; }
}
const makeSVG = () => { const document = { activeElement: null, createElementNS: (_ns, tag) => new Element(tag, document) }; return new Element("svg", document); };
const flatten = (element) => [element, ...element.children.flatMap(flatten)];

test("SVG renders 12 real tracks, preserves selection, and never mutates State", () => {
  const state = copy(), before = structuredClone(state), svg = makeSVG(), selected = [];
  const result = renderStation(svg, state, (id) => selected.push(id), "D1");
  assert.deepEqual(state, before);
  assert.equal(svg.getAttribute("viewBox"), "0 0 1200 900");
  assert.equal(flatten(svg).filter((item) => item.getAttribute("class") === "railway-track").length, 12);
  assert.deepEqual(result.activeRouteIds, []);
  assert.deepEqual(new Set(result.renderedEntityIds), new Set(["T1", "T2", "T3", "T4", "T5", "T6", "P1", "L1"]));
  const parking = flatten(svg).find((item) => item.getAttribute("data-entity-id") === "D1");
  assert.equal(parking.getAttribute("aria-pressed"), "true");
  parking.handlers.click(); parking.focus();
  renderStation(svg, state, (id) => selected.push(id), "D1");
  assert.equal(svg.ownerDocument.activeElement.getAttribute("data-entity-id"), "D1");
  assert.deepEqual(selected, ["D1"]);
});

test("only current locations highlight routes; zone locks come only from server zone state", () => {
  const state = copy(), svg = makeSVG(), route = state.station.layout.routes[0];
  const train = state.trains[0], op = state.operations[0];
  op.status = "running"; op.route_ids = [route.id, state.station.layout.routes[1].id];
  train.location = at("route", { route_id: route.id, operation_id: op.id, route_progress: .25 });
  state.zones.forEach((zone) => { zone.active_operation_id = null; });
  const result = renderStation(svg, state);
  assert.deepEqual(result.activeRouteIds, [route.id]);
  const marker = flatten(svg).find((item) => item.getAttribute("data-entity-id") === train.id);
  const expected = pointOnPolyline(route.points, .25);
  assert.equal(Number(marker.getAttribute("data-anchor-x")), expected.x);
  assert.equal(Number(marker.getAttribute("data-anchor-y")), expected.y);
  assert.equal(marker.getAttribute("data-route-progress"), "0.25");
  const west = flatten(svg).find((item) => item.getAttribute("data-entity-id") === "W");
  assert.match(west.getAttribute("aria-label"), /свободна/);
  state.zones[0].active_operation_id = op.id;
  renderStation(svg, state);
  assert.match(flatten(svg).find((item) => item.getAttribute("data-entity-id") === "W").getAttribute("aria-label"), /занята операцией/);
});

test("closures, pending loss and untrusted names use safe text nodes, with no fake locations", () => {
  const state = copy(), svg = makeSVG();
  state.tracks[0].availability = "closure_pending";
  state.resources.find((resource) => resource.id === "L1").status = "unavailable_pending";
  state.station.name = "<script>not executable</script>";
  state.trains[0].location = at("route", { route_id: "missing", operation_id: "missing", route_progress: .5 });
  const result = renderStation(svg, state);
  const texts = flatten(svg).map((item) => item.textContent);
  assert.ok(texts.includes(state.station.name));
  assert.ok(texts.includes("ЗАКРЫТИЕ ОЖИДАЕТ"));
  assert.ok(texts.includes("ОТКЛЮЧЕНИЕ ОЖИДАЕТ"));
  assert.ok(!result.renderedEntityIds.includes(state.trains[0].id));
  assert.ok(!flatten(svg).some((item) => item.tagName === "script"));
});

test("assigned and reserved tracks never relocate trains or become actual occupied tracks", () => {
  const state = copy(), svg = makeSVG(), track = state.tracks.find((item) => item.id === "R2"), op = state.operations[0];
  track.assigned_train_id = "T1"; track.active_operation_ids = [op.id]; track.occupied_length_m = 0;
  state.trains[0].planned_track_id = "R2"; op.status = "running";
  renderStation(svg, state);
  const trackNode = flatten(svg).find((item) => item.getAttribute("data-entity-id") === "R2");
  assert.ok(flatten(trackNode).some((item) => item.getAttribute("class") === "railway-reservation"));
  assert.ok(!flatten(trackNode).some((item) => item.getAttribute("class") === "railway-occupied"));
  const trainNode = flatten(svg).find((item) => item.getAttribute("data-entity-id") === "T1");
  assert.equal(trainNode.getAttribute("data-location-kind"), "boundary");
  op.status = "planned"; renderStation(svg, state);
  assert.ok(!flatten(svg).find((item) => item.getAttribute("data-entity-id") === "R2").children.some((item) => item.getAttribute("class") === "railway-reservation"));
});
