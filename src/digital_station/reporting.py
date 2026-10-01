"""UTF-8 reports from durable events, checkpoints and actual efficiency-v1 integrals."""

import asyncio
import csv
import io
import json

from sqlalchemy import select

from .contracts import State
from .db import ConfigRevision, DomainEvent
from .efficiency import actual, supplementary
from .history import HistoryError, event_bounds, iso, samples_from_states, state_sequence, window

COLUMNS = (
    "record_type",
    "run_id",
    "wall_time",
    "sim_time_s",
    "entity_id",
    "metric",
    "value",
    "unit",
    "plan_id",
    "formula_version",
    "details",
)


def safe_cell(value):
    if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


async def export_csv(session, run_id, start=None, end=None):
    start, end = window(start, end)
    first, _ = await event_bounds(session, run_id)
    start = max(start or first.received_at, first.received_at)
    if end < start:
        raise HistoryError(422, "VALIDATION_ERROR", "Окно не пересекается с доступной историей")
    query = select(DomainEvent).where(DomainEvent.run_id == run_id)
    anchor = (
        await session.scalar(
            query.where(DomainEvent.received_at <= start).order_by(DomainEvent.seq.desc()).limit(1)
        )
        or first
    )
    last = await session.scalar(
        query.where(DomainEvent.received_at <= end).order_by(DomainEvent.seq.desc()).limit(1)
    )
    if last is None:
        raise HistoryError(404, "NOT_FOUND", "Нет сохранённых состояний в выбранном окне")
    states = await state_sequence(session, run_id, anchor.seq, last.seq)
    configuration = await session.get(ConfigRevision, states[-1]["config_version"])
    events = list(
        await session.scalars(
            query.where(DomainEvent.received_at >= start, DomainEvent.received_at <= end).order_by(
                DomainEvent.seq
            )
        )
    )
    return await asyncio.to_thread(
        render_csv, run_id, start, end, anchor, last, states, configuration.payload, events
    )


def render_csv(run_id, start, end, anchor, last, states, config, events):
    """Pure formatting/integrals in a background thread; no writes or solver execution."""
    state = State.model_validate(states[-1])
    samples = samples_from_states(states)
    index = actual(state, samples, config, anchor.sim_time_s)
    extra = supplementary(state, samples, anchor.sim_time_s)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    context = {
        "from_wall_time": iso(start),
        "to_wall_time": iso(end),
        "anchor_seq": anchor.seq,
        "end_seq": last.seq,
        "window_start_sim_s": anchor.sim_time_s,
        "window_end_sim_s": state.sim_time_s,
        "config_version": state.config_version,
        "mode": "actual",
    }

    def row(kind, entity, metric, value, unit, details, event=None, plan_id=""):
        cells = (
            kind,
            run_id,
            iso(event.received_at if event else last.received_at),
            event.sim_time_s if event else state.sim_time_s,
            entity,
            metric,
            "" if value is None else value,
            unit,
            plan_id,
            "efficiency-v1",
            json.dumps(details, ensure_ascii=False, separators=(",", ":")),
        )
        writer.writerow([safe_cell(v) for v in cells])

    row("summary", run_id, "score", index.score, "points", {**context, "category": index.category})
    for factor in index.factors:
        row(
            "factor",
            run_id,
            factor.key,
            factor.raw,
            factor.unit,
            {**context, **factor.model_dump(mode="json")},
        )
    row("summary", run_id, "trains_per_hour", extra["trains_per_hour"], "trains/hour", context)
    row("summary", run_id, "freight_wagon_hours", extra["freight_wagon_hours"], "wagon-hours", context)
    for rid, percent in extra["resource_busy_percent"].items():
        row("summary", rid, "resource_busy", percent, "percent", context)
    timings = {}
    for event in events:
        effect = event.payload.get("effects", {})
        for incident in effect.get("replacements", {}).get("incidents", []):
            row("incident", incident["id"], "status", incident["status"], "status", incident, event)
        for plan in effect.get("replacements", {}).get("plans", []):
            row("plan", plan["id"], "objective_J", plan["objective_value"], "ratio", plan, event, plan["id"])
        job = effect.get("scalars", {}).get("last_replan")
        if job and job["status"] not in {"queued", "running"}:
            timings[job["id"]] = (job, event)
    for job, event in timings.values():
        for metric in ("elapsed_ms", "compute_ms", "validation_ms"):
            row(
                "timing",
                job["id"],
                metric,
                job.get(metric),
                "wall_ms",
                job,
                event,
                job["applied_plan_id"] or "",
            )
    return output.getvalue()
