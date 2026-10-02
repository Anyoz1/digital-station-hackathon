"""Single-writer station actor, monotonic clock, transactional effects and full-State SSE."""

import asyncio
import copy
import hashlib
import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, text, update
from sqlalchemy.exc import SQLAlchemyError

from .contracts import State
from .db import (
    CommandReceipt,
    ConfigRevision,
    DomainEvent,
    IncidentRecord,
    Run,
    RunState,
    Scenario,
    StateSnapshot,
    TelemetryObservation,
)
from .efficiency import actual, append_sample, metric_sample
from .history import load_samples, prune_retention
from .planning import PlanningCoordinator
from .scenario import utc_now
from .simulator import Simulator
from .smoke_plan import SCHEDULE_ID, load_smoke_plan
from .telemetry import Measurements

MIGRATION = "0006_telemetry_observation"
COLLECTIONS = (
    "tracks",
    "zones",
    "trains",
    "wagon_groups",
    "resources",
    "operations",
    "incidents",
    "conflicts",
    "plans",
)
LOGGER = logging.getLogger(__name__)


class ActorError(Exception):
    def __init__(self, status, code, message, details=None):
        self.status, self.code, self.message, self.details = status, code, message, details or {}
        super().__init__(code)


def timer_ingress(deadline):
    """UTC of the actual monotonic deadline; received_at remains real commit wall-time."""
    due = datetime.now(UTC) - timedelta(seconds=max(0, time.monotonic() - deadline))
    return due.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def effects(before, after):
    """Compact replacement records, no repeated topology; deterministic replay substrate."""
    result: dict[str, Any] = {"scalars": {}, "replacements": {}, "deleted": {}}
    for key, value in after.items():
        if key == "station":
            continue
        if key in COLLECTIONS:
            previous = {item["id"]: item for item in before[key]}
            current = {item["id"]: item for item in value}
            changed = [item for item in value if item != previous.get(item["id"])]
            removed = list(previous.keys() - current.keys())
            if changed:
                result["replacements"][key] = changed
            if removed:
                result["deleted"][key] = removed
        elif before.get(key) != value:
            result["scalars"][key] = value
    return result


def apply_effects(state, effect):
    """For persistence tests/future replay; not a history API or a second simulator."""
    state = json.loads(json.dumps(state))
    state.update(effect["scalars"])
    for key in set(effect["replacements"]) | set(effect["deleted"]):
        replacements = {item["id"]: item for item in effect["replacements"].get(key, [])}
        deleted = set(effect["deleted"].get(key, []))
        state[key] = [replacements.pop(item["id"], item) for item in state[key] if item["id"] not in deleted]
        state[key].extend(replacements.values())
    return state


@dataclass
class Frame:
    seq: int
    emitted: float
    wire: str
    run_id: str = ""
    ingested_ms: float | None = None


def frame(state, kind="heartbeat", entity_ids=None, ingested_at=None, reset=None):
    payload = state.model_dump(mode="json")
    payload["server_time"] = utc_now()
    data = (
        {"state": payload, "reason": reset}
        if reset
        else {
            "state": payload,
            "cause": {"kind": kind, "entity_ids": entity_ids or [], "ingested_at": ingested_at or utc_now()},
        }
    )
    wire = f"id: {state.run_id}:{state.event_seq}\nevent: {'reset' if reset else 'state'}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
    ingress_ms = None
    if not reset and kind != "heartbeat":
        ingress_ms = (
            datetime.fromisoformat(data["cause"]["ingested_at"].replace("Z", "+00:00")).timestamp() * 1000
        )
    return Frame(state.event_seq, time.monotonic(), wire, state.run_id, ingress_ms)


class StationActor:
    PERIOD = 0.8  # 1.25Hz nominal: headroom for >=1Hz; independent of simulation speed.
    RING_SECONDS = 900
    RING_BYTES = 32 * 1024 * 1024

    def __init__(self, engine, sessions, run_id, enable_planner=True):
        self.engine, self.sessions, self.run_id = engine, sessions, run_id
        self.queue: asyncio.Queue[tuple[str, Any, asyncio.Future | None]] = asyncio.Queue(maxsize=128)
        self.subscribers = set()
        self.ring: deque[Frame] = deque()
        self.ring_bytes = 0
        self.ready = False
        self.task = None
        self.lock_connection = None
        self.enable_planner = enable_planner
        self.coordinator = None
        self.metric_samples = []
        self.maintenance_task = None
        self.measurements = Measurements(run_id)

    async def start(self):
        self.lock_connection = await self.engine.connect()
        if not await self.lock_connection.scalar(text("SELECT pg_try_advisory_lock(73100524)")):
            await self.lock_connection.close()
            self.lock_connection = None
            raise RuntimeError("Station already has a writer; use one API worker")
        await self.lock_connection.commit()
        try:
            async with self.sessions() as session:
                row = await session.get(RunState, self.run_id)
                run = await session.get(Run, self.run_id)
                scenario = await session.get(Scenario, (run.scenario_id, run.scenario_version))
                self.state = State.model_validate(row.payload)
                initial = State.model_validate(scenario.payload["initial_state"])
                schedule = scenario.payload["smoke_plan"]
                config = (await session.get(ConfigRevision, self.state.config_version)).payload
                self.config = config
                self.metric_samples = await load_samples(session, self.state)
                initialized = run.execution_schedule_id is not None
                self.seed = run.seed
            self.simulator = Simulator(self.state, initial)
            self.last_checkpoint = time.monotonic()
            if not initialized:
                require_initial = self.state.sim_time_s == 0 and all(
                    o.status == "pending" for o in self.state.operations
                )
                if not require_initial:
                    raise RuntimeError("Untouched initial state required for smoke fixture")
                candidate = load_smoke_plan(self.state, schedule)
                self.simulator = Simulator(candidate, initial)
                candidate = self.simulator.state.model_copy(deep=True)
                await self.persist(
                    candidate, [dict(kind="smoke_schedule_loaded", entity_ids=[])], "tick", checkpoint=True
                )
            else:
                # Preserve running operations/phases/locks; do NOT add downtime to sim clock.
                self.simulator.refresh()
                candidate = self.simulator.state.model_copy(deep=True)
                candidate.mode = "paused"
                candidate.input_revision += 1
                await self.persist(
                    candidate, [dict(kind="recovery", entity_ids=[])], "simulation_control", checkpoint=True
                )
            self.simulator.guard_replanning = self.enable_planner
            if self.enable_planner:
                self.coordinator = PlanningCoordinator(self, initial, config)
                await self.coordinator.start()
                await self.coordinator.initialize()
            self.anchor_sim, self.anchor_wall = float(self.state.sim_time_s), time.monotonic()
            self.next_publish = self.anchor_wall + self.PERIOD
            self.ready = True
            self.task = asyncio.create_task(self.run(), name="station-actor")
            self.maintenance_task = asyncio.create_task(self.maintain_history(), name="history-retention")
        except BaseException:
            if self.coordinator:
                await self.coordinator.stop()
            await self.release_writer()
            raise

    async def release_writer(self):
        if self.lock_connection is not None:
            await self.lock_connection.execute(text("SELECT pg_advisory_unlock(73100524)"))
            await self.lock_connection.commit()
            await self.lock_connection.close()
            self.lock_connection = None

    async def stop(self):
        if self.maintenance_task:
            self.maintenance_task.cancel()
            await asyncio.gather(self.maintenance_task, return_exceptions=True)
        if self.coordinator:
            await self.coordinator.stop()
        if self.task and not self.task.done():
            await self.queue.put(("stop", None, None))
            await self.task
        self.ready = False
        self.close_streams()
        await self.release_writer()

    async def maintain_history(self):
        while self.ready:
            await asyncio.sleep(60)
            try:
                async with self.sessions() as session, session.begin():
                    await prune_retention(session)
                    await session.execute(
                        delete(TelemetryObservation).where(
                            TelemetryObservation.received_at < datetime.now(UTC) - timedelta(hours=24)
                        )
                    )
            except SQLAlchemyError:
                # Retention failure cannot remove partial effects or stop the station actor.
                LOGGER.warning("History retention transaction failed; retry next minute", exc_info=True)

    def exact_time(self, now):
        return self.anchor_sim + (
            (now - self.anchor_wall) * self.state.speed if self.state.mode == "running" else 0
        )

    async def call(self, kind, data):
        if not self.ready:
            raise ActorError(503, "SERVICE_NOT_READY", "Симулятор недоступен")
        future = asyncio.get_running_loop().create_future()
        try:
            self.queue.put_nowait((kind, data, future))
        except asyncio.QueueFull:
            raise ActorError(429, "QUEUE_FULL", "Очередь станции заполнена") from None
        return await asyncio.shield(future)

    async def subscribe(self, cursor):
        return await self.call("subscribe", cursor)

    def unsubscribe(self, queue):
        self.subscribers.discard(queue)

    def abort(self):
        self.ready = False
        self.close_streams()

    def close_streams(self):
        for queue in self.subscribers:
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(None)
        self.subscribers.clear()

    def broadcast(self, item, durable=False):
        if durable:
            self.measurements.published(item)
            self.ring.append(item)
            self.ring_bytes += len(item.wire.encode())
            while self.ring and (
                item.emitted - self.ring[0].emitted > self.RING_SECONDS or self.ring_bytes > self.RING_BYTES
            ):
                self.ring_bytes -= len(self.ring.popleft().wire.encode())
        for queue in list(self.subscribers):
            if queue.full():
                self.unsubscribe(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)
            else:
                queue.put_nowait(item)

    def register(self, cursor):
        while self.ring and time.monotonic() - self.ring[0].emitted > self.RING_SECONDS:
            self.ring_bytes -= len(self.ring.popleft().wire.encode())
        queue: asyncio.Queue[Frame | None] = asyncio.Queue(maxsize=100)
        reason = None
        try:
            run_id, seq = cursor.rsplit(":", 1)
            seq = int(seq)
            if run_id != self.run_id:
                reason = "run_changed"
            elif seq < 0 or seq > self.state.event_seq:
                reason = "invalid_cursor"
            elif seq != self.state.event_seq and (not self.ring or seq < self.ring[0].seq - 1):
                reason = "cursor_expired"
        except (ValueError, AttributeError):
            reason = "invalid_cursor"
        catchup = [] if reason else [item for item in self.ring if item.seq > seq]
        if len(catchup) >= 100:
            reason = "cursor_expired"
        if reason:
            queue.put_nowait(frame(self.state, reset=reason))
        else:
            for item in catchup:
                queue.put_nowait(item)
            if not catchup:
                queue.put_nowait(frame(self.state))
        self.subscribers.add(queue)
        return queue

    async def persist(
        self,
        candidate,
        transitions,
        kind,
        actor_id=None,
        command=None,
        ingested=None,
        checkpoint=False,
        receipt_status=200,
        receipt_result=None,
        config_record=None,
        stream_reset=None,
    ):
        before = self.state.model_dump(mode="json")
        candidate.event_seq = self.state.event_seq + 1
        candidate.state_version = self.state.state_version + 1
        candidate.server_time = utc_now()
        config = config_record.payload if config_record is not None else self.config
        samples = append_sample(
            self.metric_samples, metric_sample(candidate), max(0, candidate.sim_time_s - 900)
        )
        candidate.efficiency = actual(candidate, samples, config)
        if self.coordinator:
            self.coordinator.update_can_apply(candidate)
        payload = State.model_validate(candidate.model_dump()).model_dump(mode="json")
        candidate = State.model_validate(payload)
        now = datetime.now(UTC)
        ingress = ingested or candidate.server_time
        receipt = None
        if command:
            body, digest = command
            receipt = dict(
                request_id=body.request_id,
                run_id=self.run_id,
                input_revision=candidate.input_revision,
                state_version=candidate.state_version,
                result=receipt_result
                if receipt_result is not None
                else {
                    "action": body.action,
                    "mode": candidate.mode,
                    "speed": candidate.speed,
                    "sim_time_s": candidate.sim_time_s,
                },
            )
        async with self.sessions() as session, session.begin():
            if config_record is not None:
                session.add(config_record)
                await session.flush()
            changed = await session.execute(
                update(RunState)
                .where(
                    RunState.run_id == self.run_id,
                    RunState.state_version == self.state.state_version,
                    RunState.input_revision == self.state.input_revision,
                    RunState.config_version == self.state.config_version,
                    RunState.active_plan_id == self.state.active_plan_id,
                )
                .values(
                    state_version=candidate.state_version,
                    input_revision=candidate.input_revision,
                    config_version=candidate.config_version,
                    sim_time_s=candidate.sim_time_s,
                    active_plan_id=candidate.active_plan_id,
                    updated_at=now,
                    payload=payload,
                )
            )
            if changed.rowcount != 1:
                raise RuntimeError("Station CAS failed: concurrent writer or stale state")
            await session.execute(
                update(Run)
                .where(Run.id == self.run_id)
                .values(
                    status=candidate.mode,
                    execution_schedule_id="planner-v1" if candidate.active_plan_id else SCHEDULE_ID,
                )
            )
            session.add(
                DomainEvent(
                    run_id=self.run_id,
                    seq=candidate.event_seq,
                    source_event_id=f"{self.run_id}:{candidate.event_seq}",
                    received_at=now,
                    sim_time_s=candidate.sim_time_s,
                    kind=transitions[0]["kind"] if transitions else "tick",
                    actor_id=actor_id,
                    request_id=body.request_id if command else None,
                    payload={
                        "effects": effects(before, payload),
                        "transitions": transitions,
                        "ingested_at": ingress,
                        "metric_sample": samples[-1],
                    },
                )
            )
            await session.flush()
            if checkpoint or time.monotonic() - self.last_checkpoint >= 30:
                dynamic = {key: value for key, value in payload.items() if key != "station"}
                session.add(
                    StateSnapshot(
                        run_id=self.run_id,
                        seq=candidate.event_seq,
                        created_at=now,
                        sim_time_s=candidate.sim_time_s,
                        schema_version="1.0",
                        payload=dynamic,
                    )
                )
                self.last_checkpoint = time.monotonic()
            if receipt:
                session.add(
                    CommandReceipt(
                        user_id=actor_id,
                        request_id=body.request_id,
                        run_id=self.run_id,
                        body_hash=digest,
                        response_status=receipt_status,
                        created_at=now,
                        payload=receipt,
                    )
                )
            if self.coordinator:
                await self.coordinator.write_records(session, candidate)
            previous_incidents = {i.id: i for i in self.state.incidents}
            for incident in candidate.incidents:
                if previous_incidents.get(incident.id) == incident:
                    continue
                record = await session.get(IncidentRecord, incident.id)
                if record is None:
                    record = IncidentRecord(id=incident.id, run_id=self.run_id)
                    session.add(record)
                record.kind, record.target_id, record.status = (
                    incident.kind,
                    incident.target_id,
                    incident.status,
                )
                record.starts_sim_s, record.ends_sim_s = incident.starts_sim_s, incident.ends_sim_s
                record.created_at = datetime.fromisoformat(incident.created_at.replace("Z", "+00:00"))
                record.payload = incident.model_dump(mode="json")
        # Nothing is visible to SSE subscribers until the transaction has committed.
        self.state = candidate
        self.config, self.metric_samples = config, samples
        if self.coordinator:
            self.coordinator.dirty_jobs.clear()
        for name in (
            "event_seq",
            "state_version",
            "input_revision",
            "config_version",
            "server_time",
            "mode",
            "speed",
            "last_replan",
            "plans",
            "active_plan_id",
            "incidents",
            "conflicts",
            "efficiency",
        ):
            setattr(self.simulator.state, name, copy.deepcopy(getattr(candidate, name)))
        entity_ids = sorted({eid for change in transitions for eid in change["entity_ids"]})
        self.broadcast(frame(candidate, kind, entity_ids, ingress, reset=stream_reset), durable=True)
        return receipt

    async def observe_progress(self):
        from .normalization import normalize

        # Explicit synthetic observer, seeded noise independent from authoritative engine.
        objects: list[Any] = [*self.state.trains, *self.state.wagon_groups, *self.state.resources]
        for obj in objects:
            loc = obj.location
            if loc is None or loc.kind != "route":
                continue
            seq = self.state.event_seq
            noise = ((seq * 17 + self.seed) % 11 - 5) / 100
            raw = dict(
                source_event_id=f"{self.run_id}:mock:{seq}:{obj.id}",
                source_id=f"mock-progress:{obj.id}",
                source_seq=seq,
                operation_id=loc.operation_id,
                route_id=loc.route_id,
                observed_at=utc_now(),
                observed_progress=loc.route_progress + noise,
            )
            await normalize(self.sessions, self.state, raw)

    async def advance(self, target, ingress=None):
        for batch in self.simulator.advance(
            target, allow_starts=not (self.coordinator and self.coordinator.owner)
        ):
            for transition in batch.transitions:
                transition["sim_time_s"] = batch.state.sim_time_s
            if any(t["kind"] in {"incident_resolved", "operation_blocked"} for t in batch.transitions):
                batch.state.input_revision += 1
            kind = (
                "incident_resolved"
                if any(t["kind"] == "incident_resolved" for t in batch.transitions)
                else "operation_completed"
                if any(t["kind"] == "operation_completed" for t in batch.transitions)
                else "operation_started"
                if any(t["kind"] == "operation_started" for t in batch.transitions)
                else "tick"
            )
            await self.persist(batch.state, batch.transitions, kind, ingested=ingress)
            await self.observe_progress()
            if self.coordinator and any(
                t["kind"] in {"incident_resolved", "operation_blocked"} for t in batch.transitions
            ):
                await self.coordinator.request(
                    "incident_resolved"
                    if any(t["kind"] == "incident_resolved" for t in batch.transitions)
                    else "guard_violation",
                    ingress or utc_now(),
                )

    async def flush_clock(self, now, ingress=None):
        if self.state.mode == "running":
            await self.advance(math.floor(self.exact_time(now) + 1e-8), ingress or utc_now())

    async def control(self, user_id, body, ingress):
        digest = hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
        async with self.sessions() as session:
            previous = await session.get(CommandReceipt, (user_id, body.request_id))
            if previous:
                if previous.body_hash != digest:
                    raise ActorError(409, "IDEMPOTENCY_MISMATCH", "request_id уже использован с другим телом")
                return previous.payload
        if body.run_id != self.run_id:
            raise ActorError(409, "RUN_MISMATCH", "Запрошен другой run")
        if body.expected_input_revision != self.state.input_revision:
            raise ActorError(
                409,
                "REVISION_MISMATCH",
                "Условия станции изменились",
                {"current_input_revision": self.state.input_revision},
            )
        if body.action == "step" and self.state.mode != "paused":
            raise ActorError(409, "INVALID_TRANSITION", "Step доступен только на паузе")
        now = time.monotonic()
        exact = self.exact_time(now)
        await self.flush_clock(now)
        if body.action == "step":
            # One atomic command transaction: never persist step effects before its receipt.
            batches = list(self.simulator.advance(self.state.sim_time_s + 1))
            candidate = batches[-1].state
            transitions = [
                dict(t, sim_time_s=batch.state.sim_time_s) for batch in batches for t in batch.transitions
            ]
            exact = float(candidate.sim_time_s)
        else:
            candidate = self.state.model_copy(deep=True)
            transitions = []
        candidate.input_revision += 1
        if body.action in {"play", "pause"}:
            candidate.mode = "running" if body.action == "play" else "paused"
        elif body.action == "set_speed":
            candidate.speed = body.speed
        transitions.append(
            dict(
                kind="simulation_control", entity_ids=[], action=body.action, sim_time_s=candidate.sim_time_s
            )
        )
        receipt = await self.persist(
            candidate,
            transitions,
            "simulation_control",
            actor_id=user_id,
            command=(body, digest),
            ingested=ingress,
        )
        self.anchor_sim, self.anchor_wall = exact, now
        if self.coordinator:
            trigger = next(
                (t["kind"] for t in transitions if t["kind"] in {"operation_blocked", "incident_resolved"}),
                None,
            )
            # Approved manual fixture executes its guarded smoke prefix until the
            # human service completion is known. A redundant control-only search
            # would impose a barrier on start0, return the same manual wait, and
            # leave that unchanged timetable irreversibly START_TIME_MISSED.
            # Incident/guard/config requests still replan; no safety check bypass.
            manual_wait = (
                not self.coordinator.owner
                and self.state.last_replan is not None
                and self.state.last_replan.status == "no_feasible_plan"
                and "MANUAL_CONFIRMATION_REQUIRED" in self.state.last_replan.outcome_reason_codes
            )
            if trigger or self.coordinator.owner or self.state.active_plan_id is None and not manual_wait:
                await self.coordinator.request(
                    "guard_violation" if trigger == "operation_blocked" else trigger or "simulation_control",
                    ingress,
                )
        if body.action == "play":
            await self.advance(self.state.sim_time_s, ingress)  # Handle initial start0 before tick1.
        return receipt

    async def run(self):
        pending_future = None
        try:
            while self.ready:
                now = time.monotonic()
                if self.coordinator and self.coordinator.owner and now >= self.coordinator.deadline:
                    await self.coordinator.timeout_current()
                due = (
                    self.simulator.next_due(allow_starts=not (self.coordinator and self.coordinator.owner))
                    if self.state.mode == "running"
                    else None
                )
                if self.state.mode == "running" and due is not None and due <= self.exact_time(now) + 1e-8:
                    deadline = self.anchor_wall + (due - self.anchor_sim) / self.state.speed
                    await self.flush_clock(now, timer_ingress(deadline))
                if now >= self.next_publish:
                    previous_seq = self.state.event_seq
                    await self.flush_clock(now, timer_ingress(self.next_publish))
                    if previous_seq == self.state.event_seq:
                        self.broadcast(frame(self.state))
                    self.next_publish = now + self.PERIOD
                wake = self.next_publish
                due = (
                    self.simulator.next_due(allow_starts=not (self.coordinator and self.coordinator.owner))
                    if self.state.mode == "running"
                    else None
                )
                if due is not None:
                    wake = min(wake, now + max(0, due - self.exact_time(now)) / self.state.speed)
                if self.coordinator and self.coordinator.owner:
                    wake = min(wake, self.coordinator.deadline)
                try:
                    kind, data, pending_future = await asyncio.wait_for(
                        self.queue.get(), max(0.001, wake - time.monotonic())
                    )
                except TimeoutError:
                    continue
                if kind == "stop":
                    await self.flush_clock(time.monotonic())
                    return
                assert pending_future is not None
                try:
                    if kind in {
                        "replan",
                        "apply",
                        "incidents",
                        "resolve",
                        "planner_start",
                        "planner_result",
                        "config",
                        "complete",
                        "new_run",
                    }:
                        await self.flush_clock(time.monotonic())
                    if kind == "subscribe":
                        result = self.register(data)
                    elif kind == "control":
                        result = await self.control(*data)
                    elif kind == "new_run":
                        from .runs import new_run

                        result = await new_run(self, *data)
                    elif kind == "telemetry":
                        from .normalization import normalize

                        result = await normalize(self.sessions, self.state, data)
                    elif kind == "replan":
                        assert self.coordinator is not None
                        user_id, body, ingress = data
                        result = await self.coordinator.request("manual", ingress, user_id, body)
                    elif kind == "apply":
                        assert self.coordinator is not None
                        result = await self.coordinator.apply(*data)
                    elif kind == "incidents":
                        assert self.coordinator is not None
                        result = await self.coordinator.incidents(*data)
                    elif kind == "resolve":
                        assert self.coordinator is not None
                        result = await self.coordinator.resolve_incident(*data)
                    elif kind in {"config", "complete"}:
                        from .services import complete_operation, patch_config

                        result = await (
                            patch_config(self, *data) if kind == "config" else complete_operation(self, *data)
                        )
                    elif kind == "planner_start":
                        assert self.coordinator is not None
                        result = await self.coordinator.start_job(data)
                    elif kind == "planner_result":
                        assert self.coordinator is not None
                        result = await self.coordinator.result(*data)
                    else:
                        raise ActorError(422, "UNKNOWN_COMMAND", "Неизвестная команда actor")
                except ActorError as exc:
                    pending_future.set_exception(exc)
                else:
                    pending_future.set_result(result)
                pending_future = None
        except Exception as exc:
            # Exception text/SQL parameters can contain secrets: log only a fixed error.
            LOGGER.error(
                "station_actor_failed type=%s; runtime stopped; uncommitted state not published",
                type(exc).__name__,
            )
            if pending_future is not None and not pending_future.done():
                pending_future.set_exception(ActorError(503, "SERVICE_NOT_READY", "Симулятор остановлен"))
        finally:
            self.ready = False
            self.close_streams()
            while not self.queue.empty():
                _, _, future = self.queue.get_nowait()
                if future is not None and not future.done():
                    future.set_exception(ActorError(503, "SERVICE_NOT_READY", "Симулятор остановлен"))
