"""Deterministic initial data. No simulator or scheduler is implemented here."""

from datetime import UTC, datetime
from typing import Any

from .contracts import State

EPOCH = "2026-10-01T08:00:00.000Z"
PROFILE_IDS = ["freight_transit_v1", "freight_local_v1", "freight_reclassify_v1", "passenger_transit_v1"]
DEFAULT_CONFIG: dict[str, Any] = {
    "config_version": 1,
    "weights": {
        "throughput": 0.30,
        "delay": 0.25,
        "occupancy": 0.15,
        "conflicts": 0.20,
        "resource_idle": 0.10,
    },
    "category_thresholds": {"normal_min": 80, "attention_min": 50},
    "planner": {"time_limit_ms": 3000, "max_rollouts": 12},
    "units": {"simulation_time": "seconds", "real_time": "milliseconds", "length": "meters"},
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def location(kind: str, identifier: str | None = None, **kwargs) -> dict:
    result = dict(
        kind=kind, boundary_id=None, track_id=None, route_id=None, operation_id=None, route_progress=None
    )
    if kind in ("boundary", "track"):
        result[f"{kind}_id"] = identifier
    result.update(kwargs)
    return result


def infrastructure() -> tuple[dict, list[dict]]:
    tracks, nodes, edges, geometries, routes = [], [], [], [], []
    centers: dict[str, tuple[float, float]] = {}
    west_ends: dict[str, tuple[float, float]] = {}
    east_ends: dict[str, tuple[float, float]] = {}
    coords = {"BW": (30, 300), "ZW": (230, 300), "ZE": (970, 300), "BE": (1170, 300)}
    for identifier, (x, y) in coords.items():
        nodes.append(
            dict(
                id=identifier,
                kind="boundary" if identifier in ("BW", "BE") else "switch_zone",
                x=x,
                y=y,
                switch_ids=[] if identifier in ("BW", "BE") else [f"SW-{identifier}"],
            )
        )

    def edge(a, b, track=None, zone=None):
        edges.append(
            dict(
                id=f"edge-{a}-{b}",
                from_node_id=a,
                to_node_id=b,
                track_id=track,
                zone_id=zone,
                bidirectional=True,
            )
        )

    edge("BW", "ZW", zone="W")
    edge("ZE", "BE", zone="E")
    definitions = (
        [(f"R{i}", "reception", "receiving_departure", 350, 100 + i * 60) for i in range(1, 5)]
        + [(f"S{i}", "sorting", "sorting", 160, 420 + i * 40) for i in range(1, 5)]
        + [
            ("C1", "cargo", "cargo", 160, 660),
            ("C2", "cargo", "cargo", 160, 720),
            ("H", "service", "lead", 200, 400),
            ("D1", "service", "parking", 160, 820),
        ]
    )
    for identifier, park, kind, length, y in definitions:
        tracks.append(
            dict(
                id=identifier,
                name=identifier,
                park_id=park,
                kind=kind,
                length_m=length,
                availability="open",
                occupied_length_m=20 if identifier == "D1" else 0,
                train_ids=[],
                group_ids=[],
                locomotive_ids=["L1"] if identifier == "D1" else [],
                assigned_train_id=None,
                active_operation_ids=[],
            )
        )
        if identifier == "H":
            a, b = (30, y), coords["ZW"]
            nodes.append(dict(id="H-end", kind="track_end", x=a[0], y=a[1], switch_ids=[]))
            edge("H-end", "ZW", track="H", zone="W")
            centers[identifier] = a
            west_ends[identifier] = a
        else:
            a, b = (350, y), (900 if identifier.startswith("R") else 670, y)
            for suffix, point in (("W", a), ("E", b)):
                nodes.append(
                    dict(id=f"{identifier}-{suffix}", kind="track_end", x=point[0], y=point[1], switch_ids=[])
                )
            edge("ZW", f"{identifier}-W", zone="W")
            edge(f"{identifier}-W", f"{identifier}-E", track=identifier)
            if identifier.startswith("R"):
                edge(f"{identifier}-E", "ZE", zone="E")
            centers[identifier] = ((a[0] + b[0]) / 2, y)
            west_ends[identifier], east_ends[identifier] = a, b
        geometries.append(dict(track_id=identifier, points=[a, b]))

    def route(identifier, source, target, track_ids, zone, points):
        routes.append(
            dict(
                id=identifier,
                from_id=source,
                to_id=target,
                track_ids=track_ids,
                zone_ids=[zone],
                points=points,
            )
        )

    for i in range(1, 5):
        r = f"R{i}"
        for side, boundary, zone, throat, end in (
            ("W", "BW", "W", "ZW", west_ends[r]),
            ("E", "BE", "E", "ZE", east_ends[r]),
        ):
            points = [coords[boundary], coords[throat], end, centers[r]]
            route(f"arrival-{side}-{r}", boundary, r, [r], zone, points)
            route(f"departure-{side}-{r}", r, boundary, [r], zone, list(reversed(points)))
    for identifier in centers:
        if identifier == "H":
            continue
        points = [centers[identifier], west_ends[identifier], coords["ZW"], centers["H"]]
        route(f"move-{identifier}-H", identifier, "H", [identifier, "H"], "W", points)
        route(f"move-H-{identifier}", "H", identifier, ["H", identifier], "W", list(reversed(points)))
    station = dict(
        id="station-demo-v1",
        name="Учебная цифровая станция",
        parks=[
            dict(id=i, name=n, kind=k)
            for i, n, k in (
                ("reception", "Приёмо-отправочный", "receiving_departure"),
                ("sorting", "Сортировочный", "sorting"),
                ("cargo", "Грузовой", "cargo"),
                ("service", "Служебный", "service"),
            )
        ],
        layout=dict(view_box=[0, 0, 1200, 900], nodes=nodes, edges=edges, tracks=geometries, routes=routes),
    )
    return station, tracks


def make_initial_state(run_id: str = "run-demo-1", server_time: str = EPOCH) -> State:
    station, tracks = infrastructure()
    trains, groups, resources, operations = [], [], [], []

    def resource(identifier, kind, loc=None):
        resources.append(
            dict(
                id=identifier,
                name=identifier,
                kind=kind,
                status="available",
                active_operation_id=None,
                location=loc,
                available_after_sim_s=None,
                assigned_user_id=None,
            )
        )

    resource("L1", "shunting_locomotive", location("track", "D1"))
    for identifier, kind in (
        ("SH1", "shunting_crew"),
        ("I1", "inspection_crew"),
        ("I2", "inspection_crew"),
        ("CG1", "cargo_crew"),
    ):
        resource(identifier, kind)
    definitions = [
        ("T1", "transit", "W_E", 0, 600, 1, [("G1", 12)]),
        ("T2", "local", "W_E", 180, 2700, 2, [("G2L", 4), ("G2K", 4)]),
        ("T3", "transit", "E_W", 600, 1500, 3, [("G3", 10)]),
        ("T4", "reclassify", "W_E", 840, 3900, 1, [("G4A", 3), ("G4B", 3), ("G4K", 4)]),
        ("T5", "local", "W_E", 1920, 5100, 2, [("G5L", 4), ("G5K", 4)]),
        ("T6", "transit", "E_W", 2700, 4200, 1, [("G6", 12)]),
        ("P1", "transit", "W_E", 720, 960, 3, []),
    ]
    for identifier, processing, direction, eta, due, priority, group_defs in definitions:
        fixed = not group_defs
        profile = (
            "passenger_transit_v1"
            if fixed
            else f"freight_{processing if processing != 'reclassify' else 'reclassify'}_v1"
        )
        loc = location("boundary", "BW" if direction == "W_E" else "BE")
        traction = "TP1" if fixed else f"TL{identifier[1:]}"
        crew = "TCP1" if fixed else f"TC{identifier[1:]}"
        resource(traction, "self_propelled_unit" if fixed else "train_locomotive", loc)
        resource(crew, "train_crew")
        group_ids = [gid for gid, _ in group_defs]
        target_ids = ["G4B", "G4A", "G4K"] if processing == "reclassify" else group_ids.copy()
        body = 120 if fixed else sum(count for _, count in group_defs) * 14
        trains.append(
            dict(
                id=identifier,
                number="P001" if fixed else f"200{identifier[1:]}",
                type="PASSENGER" if fixed else "FREIGHT",
                service_profile_id=profile,
                processing_kind=processing,
                direction=direction,
                consist_kind="fixed" if fixed else "wagon_groups",
                body_length_m=body,
                total_length_m=body if fixed else body + 20,
                location=loc,
                priority=priority,
                status="expected",
                destination_id="DEST_E" if direction == "W_E" else "DEST_W",
                scheduled_arrival_sim_s=eta,
                expected_arrival_sim_s=eta,
                due_departure_sim_s=due,
                actual_arrival_sim_s=None,
                actual_departure_sim_s=None,
                group_ids=group_ids,
                target_group_ids=target_ids,
                traction_resource_id=traction,
                traction_kind="self_propelled" if fixed else "locomotive",
                planned_track_id=None,
                current_track_id=None,
            )
        )
        for gid, count in group_defs:
            groups.append(
                dict(
                    id=gid,
                    origin_train_id=identifier,
                    assigned_train_id=identifier,
                    current_train_id=identifier,
                    wagon_ids=[f"{gid}-W{i:02}" for i in range(1, count + 1)],
                    wagon_count=count,
                    length_m=count * 14,
                    destination_id="DEST_E" if direction == "W_E" else "DEST_W",
                    cargo_state="unloaded" if gid in ("G2L", "G5L") else "loaded",
                    location=loc,
                )
            )
        chain = [("arrival", "arrival", 120, group_ids)]
        if not fixed:
            chain.append(("inspection", "inspection", 240, group_ids))
        if processing == "local":
            local = [group_ids[0]]
            chain += [
                ("shunt-out", "shunt_transfer", 420, local),
                ("cargo", "cargo", 720, local),
                ("shunt-back", "shunt_transfer", 420, local),
                ("prep", "departure_prep", 180, group_ids),
            ]
        elif processing == "reclassify":
            chain += [
                (f"shunt-{label}", "shunt_transfer", 420, [gid])
                for label, gid in (("A-out", "G4A"), ("B-out", "G4B"), ("A-back", "G4A"), ("B-back", "G4B"))
            ]
            chain.append(("prep", "departure_prep", 180, group_ids))
        chain.append(("departure", "departure", 120, target_ids))
        predecessor = None
        for suffix, kind, duration, subject_groups in chain:
            op_id = f"op-{identifier}-{suffix}"
            operations.append(
                dict(
                    id=op_id,
                    train_id=identifier,
                    group_ids=subject_groups,
                    kind=kind,
                    status="pending",
                    execution_mode="auto",
                    predecessor_ids=[predecessor] if predecessor else [],
                    source_track_id=None,
                    target_track_id=None,
                    route_ids=[],
                    resource_ids=[],
                    duration_sim_s=duration,
                    start_sim_s=None,
                    end_sim_s=None,
                    actual_start_sim_s=None,
                    actual_end_sim_s=None,
                    progress=0,
                    phase=None,
                    blocked_reason_codes=[],
                    assigned_user_id=None,
                    can_complete=False,
                )
            )
            predecessor = op_id
    factors = [
        dict(
            key=key,
            raw=None,
            unit="sim_seconds" if key == "delay" else "count" if key == "conflicts" else "ratio",
            norm_penalty=None,
            weight=weight,
            contribution=None,
            reason="Окно наблюдения ещё пусто",
        )
        for key, weight in DEFAULT_CONFIG["weights"].items()
    ]
    return State.model_validate(
        dict(
            schema_version="1.0",
            run_id=run_id,
            scenario_id="demo_main_v1",
            scenario_epoch=EPOCH,
            event_seq=1,
            state_version=1,
            input_revision=1,
            config_version=1,
            server_time=server_time,
            sim_time_s=0,
            speed=1,
            mode="paused",
            station=station,
            active_plan_id=None,
            tracks=tracks,
            zones=[dict(id=i, active_operation_id=None, availability="open") for i in ("W", "E")],
            trains=trains,
            wagon_groups=groups,
            resources=resources,
            operations=operations,
            incidents=[],
            conflicts=[],
            plans=[],
            last_replan=None,
            efficiency=dict(
                mode="actual",
                formula_version="efficiency-v1",
                window_start_sim_s=0,
                window_end_sim_s=0,
                score=None,
                category=None,
                factors=factors,
            ),
        )
    )
