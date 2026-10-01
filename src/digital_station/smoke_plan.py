"""Hand-authored timetable from SPEC 4.1. NOT FCFS/optimizer/validated PlanSummary."""

from .contracts import State

SCHEDULE_ID = "smoke-plan-demo-main-v1"
TIMETABLE = {
    "T1": ("R1", [0, 120, 360]),
    "T2": ("R3", [180, 300, 840, 1260, 2040, 2460, 2640]),
    "T3": ("R2", [600, 720, 1380]),
    "T4": ("R4", [1260, 1380, 1620, 2460, 3240, 3660, 4080, 4260]),
    "T5": ("R3", [2880, 3000, 4080, 4500, 5220, 5640, 5820]),
    "T6": ("R2", [2760, 2880, 3120]),
    "P1": ("R1", [720, 840]),
}


def assignments(state: State) -> list[dict]:
    """Data preparation only: fixed tracks/resources/start times, no scheduling search."""
    result = []
    for train in state.trains:
        home, starts = TIMETABLE[train.id]
        ops = [op for op in state.operations if op.train_id == train.id]
        if len(starts) != len(ops):
            raise ValueError("Smoke timetable/profile mismatch")
        for op, start in zip(ops, starts, strict=True):
            source: str | None = home
            target: str | None = home
            routes: list[str] = []
            crew = "TCP1" if train.id == "P1" else f"TC{train.id[1:]}"
            resources = [train.traction_resource_id, crew]
            if op.kind == "arrival":
                source = None
                routes = [f"arrival-{'W' if train.direction == 'W_E' else 'E'}-{home}"]
            elif op.kind == "departure":
                target = None
                routes = [f"departure-{'E' if train.direction == 'W_E' else 'W'}-{home}"]
            elif op.kind in {"inspection", "departure_prep"}:
                resources = ["I2" if op.kind == "inspection" and train.id in {"T2", "T5"} else "I1"]
                if op.kind == "departure_prep":
                    resources += [train.traction_resource_id, crew]
            elif op.kind == "cargo":
                source = target = "C1"
                resources = ["CG1"]
            elif op.kind == "shunt_transfer":
                side = "C1" if train.processing_kind == "local" else "S1" if "-A-" in op.id else "S2"
                source, target = (home, side) if op.id.endswith("out") else (side, home)
                routes = [
                    "move-D1-H",
                    f"move-H-{source}",
                    f"move-{source}-H",
                    f"move-H-{target}",
                    f"move-{target}-H",
                    "move-H-D1",
                ]
                resources = ["L1", "SH1"]
            result.append(
                dict(
                    operation_id=op.id,
                    start_sim_s=start,
                    end_sim_s=start + op.duration_sim_s,
                    source_track_id=source,
                    target_track_id=target,
                    route_ids=routes,
                    resource_ids=resources,
                )
            )
    return result


def load_smoke_plan(state: State, schedule: dict | None = None) -> State:
    state = state.model_copy(deep=True)
    ops = {op.id: op for op in state.operations}
    items = (schedule or fixture(state))["assignments"]
    if len(items) != len(ops) or {item["operation_id"] for item in items} != set(ops):
        raise ValueError("Smoke fixture must cover all operations exactly once")
    for item in items:
        op = ops[item["operation_id"]]
        if op.status != "pending":
            raise ValueError("Smoke fixture can only initialize untouched operations")
        for key, value in item.items():
            if key != "operation_id":
                setattr(op, key, value)
        op.status = "planned"
    for train in state.trains:
        train.planned_track_id = next(
            op.target_track_id for op in state.operations if op.train_id == train.id and op.kind == "arrival"
        )
    return State.model_validate(state.model_dump())


def fixture(state: State) -> dict:
    return dict(
        id=SCHEDULE_ID,
        scenario_id=state.scenario_id,
        source="hand_authored_SPEC_4.1",
        optimizer_implemented=False,
        independent_validator_passed=False,
        description="Fixed executable smoke timetable, not an optimization result or certified plan.",
        assignments=assignments(state),
    )
