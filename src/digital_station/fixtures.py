"""Static frontend samples, explicitly separate from the persistent live API."""

from copy import deepcopy

from .contracts import State
from .scenario import DEFAULT_CONFIG, EPOCH, location, make_initial_state


def fixture_suite() -> dict:
    initial = make_initial_state().model_dump(mode="json")
    arrival = deepcopy(initial)
    arrival.update(
        event_seq=2,
        state_version=2,
        input_revision=2,
        mode="running",
        sim_time_s=60,
        server_time="2026-10-01T08:01:00.000Z",
    )
    train = next(t for t in arrival["trains"] if t["id"] == "T1")
    loc = location("route", route_id="arrival-W-R1", operation_id="op-T1-arrival", route_progress=0.5)
    train.update(status="on_station", location=loc, planned_track_id="R1")
    group = next(g for g in arrival["wagon_groups"] if g["id"] == "G1")
    group["location"] = loc
    op = next(o for o in arrival["operations"] if o["id"] == "op-T1-arrival")
    op.update(
        status="running",
        start_sim_s=0,
        end_sim_s=120,
        actual_start_sim_s=0,
        target_track_id="R1",
        route_ids=["arrival-W-R1"],
        resource_ids=["TL1", "TC1"],
        progress=0.5,
    )
    track = next(t for t in arrival["tracks"] if t["id"] == "R1")
    track.update(assigned_train_id="T1", active_operation_ids=[op["id"]])
    arrival["zones"][0]["active_operation_id"] = op["id"]
    for resource in arrival["resources"]:
        if resource["id"] in ("TL1", "TC1"):
            resource.update(status="busy", active_operation_id=op["id"])
            if resource["id"] == "TL1":
                resource["location"] = loc

    inspection = deepcopy(arrival)
    inspection.update(event_seq=3, state_version=3, sim_time_s=120, server_time="2026-10-01T08:02:00.000Z")
    train = next(t for t in inspection["trains"] if t["id"] == "T1")
    loc = location("track", "R1")
    train.update(location=loc, current_track_id="R1", actual_arrival_sim_s=120)
    next(g for g in inspection["wagon_groups"] if g["id"] == "G1")["location"] = loc
    op = next(o for o in inspection["operations"] if o["id"] == "op-T1-arrival")
    op.update(status="completed", actual_end_sim_s=120, progress=1)
    op = next(o for o in inspection["operations"] if o["id"] == "op-T1-inspection")
    op.update(
        status="running",
        start_sim_s=120,
        end_sim_s=360,
        actual_start_sim_s=120,
        source_track_id="R1",
        target_track_id="R1",
        resource_ids=["I1"],
    )
    track = next(t for t in inspection["tracks"] if t["id"] == "R1")
    track.update(
        train_ids=["T1"],
        group_ids=["G1"],
        locomotive_ids=["TL1"],
        occupied_length_m=188,
        active_operation_ids=[op["id"]],
    )
    inspection["zones"][0]["active_operation_id"] = None
    for resource in inspection["resources"]:
        if resource["id"] in ("TL1", "TC1"):
            resource.update(status="available", active_operation_id=None)
            if resource["id"] == "TL1":
                resource["location"] = loc
        if resource["id"] == "I1":
            resource.update(status="busy", active_operation_id=op["id"])

    disrupted = deepcopy(initial)
    disrupted.update(event_seq=2, state_version=2, input_revision=2, server_time="2026-10-01T08:00:01.000Z")
    next(t for t in disrupted["tracks"] if t["id"] == "R2")["availability"] = "closed"
    next(t for t in disrupted["trains"] if t["id"] == "T3")["expected_arrival_sim_s"] += 180
    for identifier, kind, target, delay, affected in (
        ("inc-fixture-1", "track_closure", "R2", None, []),
        ("inc-fixture-2", "train_delay", "T3", 180, ["op-T3-arrival"]),
    ):
        disrupted["incidents"].append(
            dict(
                id=identifier,
                kind=kind,
                target_id=target,
                status="active",
                created_at=EPOCH,
                starts_sim_s=0,
                ends_sim_s=600,
                delay_sim_s=delay,
                affected_operation_ids=affected,
                description="Статический пример для frontend, не результат API",
            )
        )

    def frames(states, kind):
        return [
            dict(
                event="state",
                id=f"{s['run_id']}:{s['event_seq']}",
                data=dict(state=s, cause=dict(kind=kind, entity_ids=[], ingested_at=s["server_time"])),
            )
            for s in states
        ]

    files = {
        "snapshot.initial.json": initial,
        "stream.normal.json": frames([initial, arrival, inspection], "fixture_normal"),
        "stream.incident.json": frames([initial, disrupted], "fixture_incident"),
        "config.json": DEFAULT_CONFIG,
        "errors.json": [
            dict(status=status, body={"error": dict(code=code, message=message, details={}, request_id=None)})
            for status, code, message in (
                (401, "UNAUTHENTICATED", "Требуется вход"),
                (403, "FORBIDDEN", "Недостаточно прав"),
                (409, "REVISION_MISMATCH", "Устаревшая версия"),
                (422, "VALIDATION_ERROR", "Ошибка формата запроса"),
            )
        ],
    }
    for role, name in (
        ("viewer", "Наблюдатель"),
        ("operator", "Исполнитель"),
        ("dispatcher", "Диспетчер"),
        ("admin", "Администратор"),
    ):
        files[f"auth.{role}.json"] = dict(
            user=dict(
                id=f"u-{role}",
                display_name=name,
                role=role,
                resource_ids=["I1"] if role == "operator" else [],
            )
        )
    for state in (initial, arrival, inspection, disrupted):
        State.model_validate(state)
    return files
