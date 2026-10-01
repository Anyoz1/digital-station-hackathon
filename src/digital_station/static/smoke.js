"use strict";
const byId = (id) => document.getElementById(id);
let user = null, state = null, stream = null, retryTimer = null;
let lastReceived = 0, retries = 0, connection = "OFFLINE", commandPending = false;
const log = [];
window.smokeMetrics = { frames: [] }; // Only observations of real backend frames.
function showError(error) { const box = byId("api-error"); box.hidden = false; box.textContent = `${error.message}\n${JSON.stringify(error.body ?? {}, null, 2)}`; }
function clearError() { byId("api-error").hidden = true; byId("api-error").textContent = ""; }
async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  const body = response.status === 204 ? null : await response.json();
  if (!response.ok) { const error = new Error(`HTTP ${response.status}: ${body?.error?.code ?? response.statusText} — ${body?.error?.message ?? "Ошибка API"}`); error.body = body; error.status = response.status; throw error; }
  return body;
}
function controls() {
  const writable = user && ["dispatcher", "admin"].includes(user.role) && state && connection === "LIVE" && lastReceived && performance.now() - lastReceived <= 3000 && !commandPending;
  byId("play-button").disabled = !writable || state?.mode === "running";
  byId("pause-button").disabled = !writable || state?.mode === "paused";
  byId("speed-button").disabled = !writable;
  byId("replan-button").disabled = !writable;
  byId("incident-button").disabled = !writable;
  byId("replan-detail-button").disabled = !user || !state?.last_replan;
  document.querySelectorAll('#plan-actions button').forEach((button)=>{button.disabled=!writable || button.dataset.canApply!=="true";});
  document.querySelectorAll('#incident-actions button').forEach((button)=>{button.disabled=!writable;});
}
function authenticated(value) {
  user = value; byId("user-status").textContent = user ? `${user.display_name} · роль: ${user.role} · ${user.id}` : "Вход не выполнен";
  ["me-button", "logout-button", "snapshot-button"].forEach((id) => { byId(id).disabled = !user; }); controls();
}
function table(target, headers, rows) {
  const element = document.createElement("table"), head = document.createElement("thead"), heading = document.createElement("tr");
  headers.forEach((text) => { const cell = document.createElement("th"); cell.textContent = text; heading.append(cell); }); head.append(heading); element.append(head);
  const body = document.createElement("tbody");
  rows.forEach((row) => { const tr = document.createElement("tr"); tr.dataset.entityId = String(row[0]); row.forEach((text) => { const cell = document.createElement("td"); cell.textContent = String(text ?? "—"); tr.append(cell); }); body.append(tr); });
  element.append(body); byId(target).replaceChildren(element);
}
function place(loc) {
  if (!loc) return "—";
  return `${loc.kind}: ${loc.track_id ?? loc.boundary_id ?? loc.route_id ?? "—"}${loc.route_progress == null ? "" : ` (${(100 * loc.route_progress).toFixed(1)}%)`}`;
}
function render(value, reset = false) {
  if (!reset && state?.run_id === value.run_id && value.event_seq < state.event_seq) return;
  state = value; window.smokeState = state;
  byId("snapshot-status").textContent = `Backend: ${state.tracks.length} путей, ${state.trains.length} поездов, ${state.resources.length} ресурсов · ${state.server_time}`;
  byId("simulation-status").textContent = `${state.mode === "running" ? "LIVE" : "PAUSED"} · sim ${state.sim_time_s} s · speed ${state.speed}× · seq ${state.event_seq}`;
  byId("summary").replaceChildren();
  Object.entries({ run_id: state.run_id, scenario_id: state.scenario_id, schema_version: state.schema_version, event_seq: state.event_seq, state_version: state.state_version, input_revision: state.input_revision, config_version: state.config_version, sim_time_s: state.sim_time_s, mode: state.mode, speed: state.speed }).forEach(([key, value]) => {
    const term = document.createElement("dt"); term.textContent = key; const description = document.createElement("dd"); description.textContent = String(value); byId("summary").append(term, description);
  });
  table("tracks-table", ["ID", "Длина, м", "Факт, м", "Поезда / группы / тяга", "Lock", "Home", "Доступность"], state.tracks.map((t) => [t.id, t.length_m, t.occupied_length_m, [...t.train_ids, ...t.group_ids, ...t.locomotive_ids].join(", "), t.active_operation_ids.join(", "), t.assigned_train_id, t.availability]));
  table("trains-table", ["ID", "Тип / профиль", "Приоритет", "Статус", "Location", "ETA, с"], state.trains.map((t) => [t.id, `${t.type} / ${t.service_profile_id}`, t.priority, t.status, place(t.location), t.expected_arrival_sim_s]));
  table("resources-table", ["ID", "Вид", "Статус", "Location", "Операция"], state.resources.map((r) => [r.id, r.kind, r.status, place(r.location), r.active_operation_id]));
  table("operations-table", ["ID", "Вид", "Статус", "Progress", "Phase", "Источник → цель", "Start / end, sim s", "Причины запрета"], state.operations.map((o) => [o.id, o.kind, o.status, `${(100 * o.progress).toFixed(1)}%`, o.phase, `${o.source_track_id ?? "boundary"} → ${o.target_track_id ?? "boundary"}`, `${o.start_sim_s} / ${o.end_sim_s}`, o.blocked_reason_codes.join(", ")]));
  table("conflicts-table", ["ID", "Вид", "Причина", "Операции", "Сообщение", "Рекомендация"], state.conflicts.map((c) => [c.id, c.kind, c.reason_code, c.operation_ids.join(", "), c.message, c.recommendation]));
  table("incidents-table", ["ID", "Вид / цель", "Статус", "Effective / end, sim s", "Affected operations", "Описание"], state.incidents.map(i=>[i.id,`${i.kind} / ${i.target_id}`,i.status,`${i.starts_sim_s} / ${i.ends_sim_s}`,i.affected_operation_ids.join(", "),i.description]));
  byId("incident-actions").replaceChildren();state.incidents.filter(i=>i.status!=="resolved").forEach(i=>{
    const button=document.createElement('button');button.type='button';button.textContent=`Resolve ${i.id}`;button.addEventListener('click',()=>domainCommand(`/api/v1/incidents/${encodeURIComponent(i.id)}/resolve`,{}));byId("incident-actions").append(button);
  });
  const job=state.last_replan;
  byId("replan-status").textContent=job ? `${job.id} · ${job.status} · elapsed ${job.elapsed_ms.toFixed(1)} ms · compute ${job.compute_ms?.toFixed(1) ?? "—"} ms · applied ${job.applied_plan_id ?? "—"} · ${(job.outcome_reason_codes ?? []).join(", ")}` : "Расчёта пока нет";
  table("plans-table",["ID","Статус","Strategy","Validity","J ↓","Forecast index","Changed operations"],state.plans.map(p=>[p.id,p.status,p.strategy,p.validity,p.objective_value?.toFixed(6),p.forecast.score,p.changed_operation_ids.join(", ")]));
  byId("plan-actions").replaceChildren();state.plans.filter(p=>p.status==="proposed").forEach(p=>{
    const button=document.createElement('button');button.type='button';button.textContent=`Применить ${p.id}`;button.dataset.canApply=String(p.can_apply);button.addEventListener('click',()=>domainCommand(`/api/v1/plans/${encodeURIComponent(p.id)}/apply`,{}));byId("plan-actions").append(button);
  });
  byId("snapshot-json").textContent = JSON.stringify(state, null, 2); controls();
}
function stopStream() { if (stream) stream.close(); stream = null; clearTimeout(retryTimer); retryTimer = null; }
function connectionStatus() {
  const age = lastReceived ? performance.now() - lastReceived : Infinity;
  const label = !user ? "OFFLINE" : age > 10000 && lastReceived ? "OFFLINE" : age > 3000 && lastReceived ? "STALE" : connection;
  byId("connection-status").textContent = `${label} · SSE${Number.isFinite(age) ? ` · последний кадр ${(age / 1000).toFixed(1)} s назад` : ""}`; controls();
}
function received(event, data) {
  lastReceived = performance.now(); connection = "LIVE"; retries = 0;
  const value = data.state, frames = window.smokeMetrics.frames;
  frames.push({ received_ms: lastReceived, event: event.type, kind: data.cause?.kind ?? data.reason, seq: value.event_seq, sim_time_s: value.sim_time_s, mode: value.mode, speed: value.speed,
    running: value.operations.filter((o) => o.status === "running").map((o) => ({ id: o.id, progress: o.progress, phase: o.phase })),
    occupied: value.tracks.filter((t) => t.occupied_length_m).map((t) => ({ id: t.id, m: t.occupied_length_m })), locomotive: value.resources.find((r) => r.id === "L1")?.location,
    incidents: value.incidents.map(i=>({id:i.id,status:i.status})), conflicts:value.conflicts.map(c=>({id:c.id,operation_ids:c.operation_ids})), job:value.last_replan ? {id:value.last_replan.id,status:value.last_replan.status,applied_plan_id:value.last_replan.applied_plan_id}:null });
  if (frames.length > 1500) frames.shift();
  log.unshift(`${value.server_time} · ${event.type}/${data.cause?.kind ?? data.reason} · seq ${value.event_seq} · sim ${value.sim_time_s} · ${(data.cause?.entity_ids ?? []).join(", ")}`); if (log.length > 100) log.pop(); byId("event-log").textContent = log.join("\n");
  if (event.type === "reset" || !state || value.run_id !== state.run_id || value.event_seq > state.event_seq) render(value, event.type === "reset");
  connectionStatus();
}
function connectStream() {
  stopStream(); if (!user || !state) return; connection = "CONNECTING";
  stream = new EventSource(`/api/v1/stream?after=${encodeURIComponent(`${state.run_id}:${state.event_seq}`)}`);
  ["state", "reset"].forEach((kind) => stream.addEventListener(kind, (event) => { try { received(event, JSON.parse(event.data)); } catch (error) { showError(error); } }));
  stream.onerror = () => {
    stopStream(); connection = "RECONNECTING"; connectionStatus(); if (!user) return;
    const delay = Math.min(10000, 1000 * (2 ** retries++)) * (0.8 + Math.random() * 0.4);
    retryTimer = setTimeout(async () => { try { authenticated((await api("/api/v1/auth/me")).user); connectStream(); } catch (error) { showError(error); if (error.status === 401) { authenticated(null); connection = "OFFLINE"; } else { connection = "RECONNECTING"; connectStream(); } } }, delay);
  };
}
async function loadSnapshot() { render(await api("/api/v1/snapshot")); connectStream(); }
function commandUUID() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  // LAN HTTP is not a secure context: randomUUID may be absent; CSPRNG remains available.
  const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}
async function command(action) {
  clearError(); commandPending = true; controls();
  try {
    const body = { request_id: commandUUID(), run_id: state.run_id, expected_input_revision: state.input_revision, action }; if (action === "set_speed") body.speed = Number(byId("speed-select").value);
    await api("/api/v1/simulation/control", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch (error) { showError(error); if (error.status === 409) { try { await loadSnapshot(); } catch (refreshError) { showError(refreshError); } } } finally { commandPending = false; controls(); }
}
async function domainCommand(path,fields) {
  clearError();commandPending=true;controls();
  try {return await api(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({request_id:commandUUID(),run_id:state.run_id,expected_input_revision:state.input_revision,...fields})});}
  catch(error){showError(error);if(error.status===409)await loadSnapshot();}
  finally{commandPending=false;controls();}
}
byId("replan-button").addEventListener("click",()=>domainCommand("/api/v1/replans",{reason:"manual"}));
byId("incident-button").addEventListener("click",async()=>{try{const items=JSON.parse(byId("incident-items").value);const result=await domainCommand("/api/v1/incidents",{items});if(result)byId("incident-receipt").textContent=JSON.stringify(result,null,2);}catch(error){showError(error);}});
byId("replan-detail-button").addEventListener("click",async()=>{clearError();try{const detail=await api(`/api/v1/replans/${encodeURIComponent(state.last_replan.id)}`);byId("replan-json").textContent=JSON.stringify(detail,null,2);window.smokeReplanDetail=detail;}catch(error){showError(error);}});
async function health() {
  try { const live = await api("/health/live"), ready = await api("/health/ready"); byId("health-status").textContent = `live: ${live.status} · ready: ${ready.status} · ${ready.stage}`; byId("health-json").textContent = JSON.stringify({ live, ready }, null, 2); } catch (error) { byId("health-status").textContent = "Backend не готов"; showError(error); }
}
byId("health-button").addEventListener("click", () => { clearError(); health(); });
byId("login-form").addEventListener("submit", async (event) => {
  event.preventDefault(); clearError(); const input = byId("password");
  try { await api("/api/v1/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: byId("username").value, password: input.value }) }); authenticated((await api("/api/v1/auth/me")).user); await loadSnapshot(); } catch (error) { showError(error); } finally { input.value = ""; }
});
byId("me-button").addEventListener("click", async () => { clearError(); try { authenticated((await api("/api/v1/auth/me")).user); } catch (error) { stopStream(); authenticated(null); showError(error); } });
byId("snapshot-button").addEventListener("click", async () => { clearError(); try { await loadSnapshot(); } catch (error) { showError(error); } });
byId("logout-button").addEventListener("click", async () => {
  clearError(); try { await api("/api/v1/auth/logout", { method: "POST" }); stopStream(); authenticated(null); state = null; window.smokeState = null; window.smokeReplanDetail=null; lastReceived = 0; connection = "OFFLINE"; byId("summary").replaceChildren(); ["tracks-table", "trains-table", "resources-table", "operations-table", "snapshot-json","conflicts-table","plans-table","plan-actions","replan-json","incidents-table","incident-actions","incident-receipt"].forEach((id) => byId(id).replaceChildren()); byId("snapshot-status").textContent = "Snapshot ещё не загружен"; byId("simulation-status").textContent = "—"; } catch (error) { showError(error); }
});
byId("play-button").addEventListener("click", () => command("play"));
byId("pause-button").addEventListener("click", () => command("pause"));
byId("speed-button").addEventListener("click", () => command("set_speed"));
setInterval(connectionStatus, 250); health();
