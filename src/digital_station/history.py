"""Durable read-only journal reduction and checkpoint retention. No engine/solver replay."""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from .contracts import State
from .db import DomainEvent, Run, RunState, Scenario, StateSnapshot
from .efficiency import append_sample, metric_sample


class HistoryError(ValueError):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def iso(value):
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def require_run(session, run_id):
    run = await session.get(Run, run_id)
    if run is None:
        raise HistoryError(404, "NOT_FOUND", "Прогон не найден")
    return run


def window(start=None, end=None):
    now = datetime.now(UTC)
    for value in (start, end):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise HistoryError(422, "VALIDATION_ERROR", "Wall-время должно содержать часовой пояс")
    if start is not None and end is not None and start > end:
        raise HistoryError(422, "VALIDATION_ERROR", "Начало окна позже конца")
    return start, min(end, now) if end else now


async def event_bounds(session, run_id):
    await require_run(session, run_id)
    query = select(DomainEvent).where(DomainEvent.run_id == run_id)
    first = await session.scalar(query.order_by(DomainEvent.seq).limit(1))
    last = await session.scalar(query.order_by(DomainEvent.seq.desc()).limit(1))
    if first is None:
        raise HistoryError(404, "NOT_FOUND", "Для прогона нет сохранённой истории")
    return first, last


async def state_sequence(session, run_id, lo, hi):
    """Snapshot <= lo, then replacement effects in seq order through hi."""
    run = await require_run(session, run_id)
    checkpoint = await session.scalar(
        select(StateSnapshot)
        .where(StateSnapshot.run_id == run_id, StateSnapshot.seq <= lo)
        .order_by(StateSnapshot.seq.desc())
        .limit(1)
    )
    target = await session.get(DomainEvent, (run_id, hi))
    if checkpoint is None or target is None:
        raise HistoryError(404, "NOT_FOUND", "Seq вне доступного диапазона истории")
    scenario = await session.get(Scenario, (run.scenario_id, run.scenario_version))
    state = {**checkpoint.payload, "station": scenario.payload["initial_state"]["station"]}
    events = list(
        await session.scalars(
            select(DomainEvent)
            .where(DomainEvent.run_id == run_id, DomainEvent.seq > checkpoint.seq, DomainEvent.seq <= hi)
            .order_by(DomainEvent.seq)
        )
    )
    return await asyncio.to_thread(reduce_states, state, [(e.seq, e.payload) for e in events], lo, hi)


def reduce_states(state, events, lo, hi):
    """Pure journal reduction off the actor loop; no DB, Simulator or planner access."""
    from .runtime import apply_effects

    states = [state] if state["event_seq"] >= lo else []
    expected = state["event_seq"] + 1
    for seq, payload in events:
        if seq != expected or "effects" not in payload:
            raise HistoryError(409, "HISTORY_GAP", "Журнал содержит разрыв; восстановление запрещено")
        state = apply_effects(state, payload["effects"])
        if seq >= lo:
            states.append(state)
        expected += 1
    if state["event_seq"] != hi:
        raise HistoryError(409, "HISTORY_GAP", "Запрошенное состояние не восстановлено")
    return states


async def snapshot(session, run_id, seq):
    return State.model_validate((await state_sequence(session, run_id, seq, seq))[-1])


async def load_samples(session, state):
    start = max(0, state.sim_time_s - 900)
    checkpoint = await session.scalar(
        select(StateSnapshot)
        .where(
            StateSnapshot.run_id == state.run_id,
            StateSnapshot.seq <= state.event_seq,
            StateSnapshot.sim_time_s <= start,
        )
        .order_by(StateSnapshot.seq.desc())
        .limit(1)
    )
    if checkpoint is None:
        raise HistoryError(409, "HISTORY_GAP", "Нет опорного checkpoint для фактического KPI")
    states = await state_sequence(session, state.run_id, checkpoint.seq, state.event_seq)
    return await asyncio.to_thread(samples_from_states, states, start)


def samples_from_states(states, start=None):
    samples: list[dict] = []
    for payload in states:
        samples = append_sample(samples, metric_sample(State.model_validate(payload)), start)
    return samples


def public_event(event):
    transitions = event.payload.get("transitions", [])
    entities = sorted({entity for item in transitions for entity in item.get("entity_ids", [])})
    messages = {
        "run_initialized": "Создан учебный прогон",
        "operation_started": "Начата операция",
        "operation_completed": "Завершена операция",
        "manual_operation_completed": "Исполнитель подтвердил операцию",
        "incident_created": "Зарегистрирован инцидент",
        "incident_resolved": "Снято ограничение",
        "plan_applied": "Применён проверенный план",
        "recovery": "Восстановление после перезапуска",
        "config_changed": "Изменена конфигурация",
        "simulation_control": "Изменён режим симуляции",
        "replan_queued": "Запрошен новый план",
        "replan_started": "Начат расчёт плана",
        "replan_finished": "Завершён расчёт плана",
        "tick": "Ход исполнения",
    }
    return dict(
        run_id=event.run_id,
        seq=event.seq,
        server_time=iso(event.received_at),
        sim_time_s=event.sim_time_s,
        kind=event.kind,
        entity_ids=entities,
        message=messages.get(event.kind, "Изменение станции"),
        actor_user_id=event.actor_id,
    )


async def page(session, run_id, from_seq=0, limit=100, start=None, end=None):
    start, end = window(start, end)
    first, last = await event_bounds(session, run_id)
    anchor = (
        await session.scalar(
            select(DomainEvent)
            .where(DomainEvent.run_id == run_id, DomainEvent.received_at <= (start or first.received_at))
            .order_by(DomainEvent.seq.desc())
            .limit(1)
        )
        or first
    )
    query = select(DomainEvent).where(
        DomainEvent.run_id == run_id, DomainEvent.seq > from_seq, DomainEvent.received_at <= end
    )
    if start is not None:
        query = query.where(DomainEvent.received_at >= start)
    rows = list(await session.scalars(query.order_by(DomainEvent.seq).limit(limit + 1)))
    items = rows[:limit]
    run = await require_run(session, run_id)
    return dict(
        items=[public_event(e) for e in items],
        next_from_seq=items[-1].seq if items else from_seq,
        has_more=len(rows) > limit,
        anchor_seq=anchor.seq,
        available_from_wall_time=iso(first.received_at),
        available_to_wall_time=iso(run.closed_at or datetime.now(UTC)),
    )


async def prune_retention(session, now=None):
    """Keep an anchor + every subsequent effect. Never delete runs/current/config/plans."""
    cutoff = (now or datetime.now(UTC)) - timedelta(hours=24)
    removed = {"events": 0, "snapshots": 0}
    runs = list(await session.scalars(select(Run.id)))
    for run_id in runs:
        anchor = await session.scalar(
            select(StateSnapshot)
            .where(StateSnapshot.run_id == run_id, StateSnapshot.created_at <= cutoff)
            .order_by(StateSnapshot.seq.desc())
            .limit(1)
        )
        if anchor is None:
            continue
        current = await session.get(RunState, run_id)
        run = await session.get(Run, run_id)
        if current is not None and run.closed_at is None:
            metric_anchor = await session.scalar(
                select(StateSnapshot)
                .where(
                    StateSnapshot.run_id == run_id,
                    StateSnapshot.sim_time_s <= max(0, current.sim_time_s - 900),
                )
                .order_by(StateSnapshot.seq.desc())
                .limit(1)
            )
            if metric_anchor is not None and metric_anchor.seq < anchor.seq:
                anchor = metric_anchor
        deleted = await session.execute(
            delete(StateSnapshot).where(StateSnapshot.run_id == run_id, StateSnapshot.seq < anchor.seq)
        )
        removed["snapshots"] += deleted.rowcount
        deleted = await session.execute(
            delete(DomainEvent).where(DomainEvent.run_id == run_id, DomainEvent.seq < anchor.seq)
        )
        removed["events"] += deleted.rowcount
    return removed
