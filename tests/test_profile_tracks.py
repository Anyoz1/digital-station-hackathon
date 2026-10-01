"""Contract specialization is profile-based and enforced at all three boundaries."""

import time

import pytest

from digital_station.planner import compatible_homes, options, search
from digital_station.scenario import DEFAULT_CONFIG, make_initial_state
from digital_station.simulator import GuardViolation, Simulator
from digital_station.smoke_plan import load_smoke_plan
from digital_station.validator import validate_plan

TRAIN_TYPES = ["FREIGHT", "PASSENGER", "SERVICE", "OTHER"]


@pytest.mark.parametrize("train_type", TRAIN_TYPES)
def test_planner_domains_follow_service_profile_not_train_type(train_type):
    initial = make_initial_state()
    engine = Simulator(initial, initial)
    passenger = engine.trains["P1"]
    passenger.type = train_type
    assert compatible_homes(passenger) == ["R1", "R2"]
    assert {
        target for _, target, _, _ in options(engine, engine.operations["op-P1-arrival"], "ascending")
    } == {
        "R1",
        "R2",
    }
    # Conversely, a transit train does not acquire this restriction from its display category.
    freight_transit = engine.trains["T1"]
    freight_transit.type = train_type
    assert compatible_homes(freight_transit) == ["R1", "R2", "R3", "R4"]
    assert compatible_homes(engine.trains["T2"]) == ["R3", "R4"]


@pytest.mark.parametrize("train_type", TRAIN_TYPES)
@pytest.mark.parametrize("track_id", ["R3", "R4"])
def test_runtime_guard_rejects_incompatible_passenger_profile_track(train_type, track_id):
    initial = make_initial_state()
    engine = Simulator(load_smoke_plan(initial), initial)
    engine.trains["P1"].type = train_type
    # Isolate the start guard with all physical objects still at their initial boundaries.
    engine.state.sim_time_s = 720
    arrival = engine.operations["op-P1-arrival"]
    arrival.target_track_id = track_id
    arrival.route_ids = [f"arrival-W-{track_id}"]
    before = engine.state.model_dump(mode="json")
    with pytest.raises(GuardViolation, match="TRACK_INCOMPATIBLE"):
        engine.guard(arrival)
    assert engine.state.model_dump(mode="json") == before
    assert engine.trains["P1"].location.kind == "boundary"


@pytest.mark.parametrize("train_type", TRAIN_TYPES)
@pytest.mark.parametrize("track_id", ["R1", "R2"])
def test_runtime_guard_accepts_both_specialized_tracks_independent_of_type(train_type, track_id):
    initial = make_initial_state()
    engine = Simulator(load_smoke_plan(initial), initial)
    engine.trains["P1"].type = train_type
    engine.state.sim_time_s = 720
    arrival = engine.operations["op-P1-arrival"]
    arrival.target_track_id = track_id
    arrival.route_ids = [f"arrival-W-{track_id}"]
    engine.guard(arrival)


@pytest.mark.parametrize("train_type", TRAIN_TYPES)
def test_independent_validator_rejects_P1_R4_even_with_matching_physical_routes(train_type):
    initial = make_initial_state()
    next(train for train in initial.trains if train.id == "P1").type = train_type
    state = load_smoke_plan(initial)
    candidate = [op.model_copy(deep=True) for op in state.operations]
    arrival = next(op for op in candidate if op.id == "op-P1-arrival")
    arrival.target_track_id = "R4"
    arrival.route_ids = ["arrival-W-R4"]
    departure = next(op for op in candidate if op.id == "op-P1-departure")
    departure.source_track_id = "R4"
    departure.route_ids = ["departure-E-R4"]
    report = validate_plan(state, candidate, initial, 0)
    assert not report.passed
    assert report.errors[0]["code"] == "TRACK_COMPATIBILITY", report.errors
    assert "op-P1-arrival" in report.errors[0]["entity_ids"]


def test_closed_passenger_tracks_do_not_allow_fallback_to_cargo_homes():
    initial = make_initial_state()
    state = initial.model_copy(deep=True)
    for track in state.tracks:
        if track.id in {"R1", "R2"}:
            track.availability = "closed"
    config = {**DEFAULT_CONFIG, "planner": {"time_limit_ms": 3000, "max_rollouts": 2}}
    result = search(
        dict(
            state=state.model_dump(mode="json"),
            initial=initial.model_dump(mode="json"),
            config=config,
            cutover_sim_s=0,
            deadline=time.monotonic() + 3,
        )
    )
    assert result["status"] == "no_feasible_plan", result["attempts"]
    assert result["candidates"] == []


def test_runtime_execution_blocks_P1_R4_without_relocating_fixed_train():
    initial = make_initial_state()
    engine = Simulator(load_smoke_plan(initial), initial)
    arrival = engine.operations["op-P1-arrival"]
    arrival.target_track_id = "R4"
    arrival.route_ids = ["arrival-W-R4"]
    list(engine.advance(720))
    assert arrival.status == "blocked"
    assert arrival.blocked_reason_codes == ["TRACK_INCOMPATIBLE"]
    assert engine.trains["P1"].location.kind == "boundary"
    assert engine.tracks["R4"].occupied_length_m == 0
    assert engine.resources["TP1"].active_operation_id is None
