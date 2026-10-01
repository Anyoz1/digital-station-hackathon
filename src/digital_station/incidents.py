"""Pure incident effects. No worker, DB, railway commands or interruption of running work."""

from uuid import uuid4

from .calendars import interval_available, refresh_calendars
from .contracts import Conflict, Incident
from .scenario import utc_now


def operation_tracks(state, op):
    tracks = {t for t in (op.source_track_id, op.target_track_id) if t}
    routes = {r.id: r for r in state.station.layout.routes}
    for rid in op.route_ids:
        tracks.update(routes[rid].track_ids)
    return tracks


def affected(state, kind, target):
    direct = set()
    for op in state.operations:
        if op.status == "completed":
            continue
        train = next(t for t in state.trains if t.id == op.train_id)
        if (
            (kind == "train_delay" and op.train_id == target)
            or (kind == "track_closure" and target in operation_tracks(state, op))
            or (kind == "resource_loss" and target in op.resource_ids)
            or (kind == "destination_block" and op.kind == "departure" and train.destination_id == target)
        ):
            direct.add(op.id)
    while True:
        expanded = direct | {op.id for op in state.operations if set(op.predecessor_ids) & direct}
        if expanded == direct:
            return sorted(direct)
        direct = expanded


def old_plan_conflicts(state):
    """Old future assignments only: incidents do not make a running prefix disappear."""
    conflicts = [c for c in state.conflicts if c.id.startswith("guard-")]
    for incident in state.incidents:
        if incident.status == "resolved":
            continue
        violations = []
        for op in state.operations:
            if op.status in {"running", "completed"} or op.start_sim_s is None:
                continue
            train = next(t for t in state.trains if t.id == op.train_id)
            if incident.kind == "train_delay":
                bad = (
                    op.train_id == incident.target_id
                    and op.kind == "arrival"
                    and op.start_sim_s < train.expected_arrival_sim_s
                )
            else:
                one = state.model_copy(deep=False)
                one.incidents = [incident]
                bad = not interval_available(
                    one, op, op.start_sim_s, op.end_sim_s, (operation_tracks(state, op), set())
                )
            if bad:
                violations.append(op.id)
        if not violations:
            continue
        code, kind = {
            "train_delay": ("ETA_CHANGED", "precedence"),
            "track_closure": ("TRACK_CALENDAR", "track_occupied"),
            "resource_loss": ("RESOURCE_CALENDAR", "resource_unavailable"),
            "destination_block": ("DESTINATION_CALENDAR", "destination_closed"),
        }[incident.kind]
        conflicts.append(
            Conflict(
                id=f"conflict-{incident.id}",
                kind=kind,
                severity="critical",
                reason_code=code,
                entity_ids=[incident.id, incident.target_id],
                operation_ids=violations,
                message=f"{incident.kind} {incident.target_id}: старые назначения больше не допустимы.",
                recommendation="Новые старты остановлены до проверенного пересчёта; running prefix продолжается.",
                detected_at=utc_now(),
            )
        )
    state.conflicts = conflicts
    if conflicts:
        for plan in state.plans:
            plan.validity = "invalid"
            plan.can_apply = False
            plan.forecast.score = None
            plan.forecast.category = None


def apply_batch(state, items):
    from .runtime import ActorError

    trains, tracks, resources = (
        {obj.id: obj for obj in values} for values in (state.trains, state.tracks, state.resources)
    )
    destinations = {t.destination_id for t in state.trains}
    # Validate the whole batch before changing even the cloned domain State.
    for item in items:
        known = {
            "train_delay": trains,
            "track_closure": tracks,
            "resource_loss": resources,
            "destination_block": destinations,
        }[item.kind]
        if item.target_id not in known:
            raise ActorError(
                422, "VALIDATION_ERROR", "Неизвестная цель инцидента", {"target_id": item.target_id}
            )
        if item.kind == "train_delay":
            train = trains[item.target_id]
            arrival = next(o for o in state.operations if o.train_id == train.id and o.kind == "arrival")
            if train.status not in {"expected", "waiting_entry"} or arrival.status in {
                "running",
                "completed",
            }:
                raise ActorError(
                    409,
                    "PRECONDITION_FAILED",
                    "Delay разрешён только до начала arrival",
                    {"train_id": train.id},
                )
    result = state.model_copy(deep=True)
    result_trains = {t.id: t for t in result.trains}
    ids = []
    for item in items:
        effective = result.sim_time_s
        if item.kind in {"track_closure", "resource_loss"}:
            effective = max(
                [effective]
                + [
                    op.actual_start_sim_s + op.duration_sim_s
                    for op in result.operations
                    if op.status == "running"
                    and (
                        item.target_id in operation_tracks(result, op)
                        if item.kind == "track_closure"
                        else item.target_id in op.resource_ids
                    )
                ]
            )
        incident = Incident(
            id=f"inc-{uuid4()}",
            kind=item.kind,
            target_id=item.target_id,
            status="pending" if effective > result.sim_time_s else "active",
            created_at=utc_now(),
            starts_sim_s=effective,
            ends_sim_s=effective + item.duration_sim_s,
            delay_sim_s=item.delay_sim_s,
            affected_operation_ids=affected(result, item.kind, item.target_id),
            description=f"Учебный {item.kind}: {item.target_id}; effective sim {effective}, duration {item.duration_sim_s}s.",
        )
        result.incidents.append(incident)
        ids.append(incident.id)
        if item.kind == "train_delay":
            result_trains[item.target_id].expected_arrival_sim_s += item.delay_sim_s
    result.input_revision += 1
    refresh_calendars(result)
    old_plan_conflicts(result)
    return result, ids


def resolve(state, incident_id):
    from .runtime import ActorError

    result = state.model_copy(deep=True)
    incident = next((i for i in result.incidents if i.id == incident_id), None)
    if incident is None:
        raise ActorError(404, "NOT_FOUND", "Инцидент не найден")
    if incident.status == "resolved":
        raise ActorError(409, "PRECONDITION_FAILED", "Инцидент уже resolved")
    incident.status = "resolved"
    result.input_revision += 1
    refresh_calendars(result)
    old_plan_conflicts(result)
    return result
