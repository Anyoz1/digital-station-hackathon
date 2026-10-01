import ast
import time
from pathlib import Path

import pytest

from digital_station.contracts import RouteGeometry
from digital_station.scenario import make_initial_state
from digital_station.simulator import Simulator
from digital_station.smoke_plan import load_smoke_plan
from digital_station.validator import expected_prefix, physical_signature, validate_plan


def setup():
    initial = make_initial_state()
    state = load_smoke_plan(initial)
    return initial, state


def test_smoke_is_independently_validated_and_reaches_identical_facts():
    initial, state = setup()
    report = validate_plan(state, state.operations, initial, 0)
    assert report.passed, report.errors
    engine = Simulator(state, initial)
    list(engine.advance(7200))
    assert physical_signature(report.end_state) == physical_signature(engine.state.model_dump(mode="json"))
    assert len(report.samples) > 80


def test_validator_does_not_call_simulator_guard(monkeypatch):
    initial, state = setup()
    monkeypatch.setattr(
        Simulator, "guard", lambda *args: (_ for _ in ()).throw(AssertionError("shared guard"))
    )
    assert validate_plan(state, state.operations, initial, 0).passed
    module = ast.parse((Path(__file__).parents[1] / "src/digital_station/validator.py").read_text())
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module in {"simulator", "planner"}
        for node in ast.walk(module)
    )


@pytest.mark.parametrize(
    "case,code",
    [
        ("resource", "RESOURCE_OVERLAP"),
        ("track_lock", "TRACK_OVERLAP"),
        ("zone", "ZONE_OVERLAP"),
        ("resident", "TRACK_RESIDENT"),
        ("length", "CAPACITY"),
        ("route", "SHUNT_ROUTE_TEMPLATE"),
        ("topology", "TOPOLOGY_TRACK_EDGE"),
        ("precedence", "PRECEDENCE"),
        ("cycle", "DAG_CYCLE"),
        ("middle", "GROUP_ORDER"),
        ("wagon", "WAGON_IDENTITY"),
        ("traction", "TRACTION_LOCATION"),
        ("crew", "TRACTION_ASSIGNMENT"),
        ("missing_operation", "OPERATION_IDENTITY"),
        ("fake_actual", "FUTURE_OPERATION_STATE"),
        ("occupancy", "FACTUAL_OCCUPANCY"),
    ],
)
def test_damaged_plan_or_snapshot_is_rejected(case, code):
    initial, state = setup()
    candidate = [op.model_copy(deep=True) for op in state.operations]
    ops = {op.id: op for op in candidate}
    if case == "resource":
        ops["op-T2-inspection"].resource_ids = ["I1"]
    elif case == "track_lock":
        ops["op-T2-inspection"].source_track_id = ops["op-T2-inspection"].target_track_id = "R1"
    elif case == "zone":
        ops["op-T3-departure"].start_sim_s, ops["op-T3-departure"].end_sim_s = 960, 1080
    elif case == "resident":
        ops["op-T4-arrival"].target_track_id = "R3"
        ops["op-T4-arrival"].route_ids = ["arrival-W-R3"]
    elif case == "length":
        next(t for t in state.tracks if t.id == "H").length_m = 50
    elif case == "route":
        state.station.layout.routes.append(
            RouteGeometry(
                id="forbidden-direct",
                from_id="D1",
                to_id="R3",
                track_ids=["D1", "R3"],
                zone_ids=["W"],
                points=[(350, 820), (230, 300), (350, 280)],
            )
        )
        ops["op-T2-shunt-out"].route_ids[0] = "forbidden-direct"
    elif case == "topology":
        state.station.layout.edges = [e for e in state.station.layout.edges if e.track_id != "C2"]
    elif case == "precedence":
        ops["op-T2-inspection"].start_sim_s, ops["op-T2-inspection"].end_sim_s = 200, 440
    elif case == "cycle":
        ops["op-T1-arrival"].predecessor_ids = ["op-T1-departure"]
    elif case == "middle":
        next(t for t in state.trains if t.id == "T4").group_ids = ["G4B", "G4A", "G4K"]
    elif case == "wagon":
        next(g for g in state.wagon_groups if g.id == "G2L").wagon_ids[0] = "lost-wagon"
    elif case == "traction":
        next(r for r in state.resources if r.id == "TL2").location.track_id = "D1"
    elif case == "crew":
        ops["op-T2-arrival"].resource_ids = ["TL2", "TC1"]
    elif case == "missing_operation":
        candidate.pop()
    elif case == "fake_actual":
        ops["op-T2-arrival"].actual_start_sim_s = 180
    else:
        state.tracks[0].occupied_length_m = 12
    result = validate_plan(state, candidate, initial, 0)
    assert not result.passed and result.errors[0]["code"] == code, result.errors


@pytest.mark.parametrize("operation_id", ["op-T2-shunt-out", "op-T1-inspection"])
def test_running_and_completed_prefix_cannot_be_rescheduled(operation_id):
    initial, state = setup()
    engine = Simulator(state, initial)
    list(engine.advance(1080))
    state = engine.state
    candidate = [op.model_copy(deep=True) for op in state.operations]
    op = next(o for o in candidate if o.id == operation_id)
    op.start_sim_s += 60
    op.end_sim_s += 60
    report = validate_plan(state, candidate, initial, 1080)
    assert not report.passed and report.errors[0]["code"] == "FROZEN_PREFIX"


def test_prefix_certificate_matches_uninterrupted_running_operation():
    initial, state = setup()
    engine = Simulator(state, initial)
    list(engine.advance(1080))
    base = engine.state.model_copy(deep=True)
    list(engine.advance(1110))
    assert expected_prefix(base, initial, 1110) == physical_signature(engine.state.model_dump(mode="json"))


def test_validation_deadline_does_not_produce_a_success_certificate():
    initial, state = setup()
    with pytest.raises(TimeoutError):
        validate_plan(state, state.operations, initial, 0, deadline=time.monotonic() - 1)
