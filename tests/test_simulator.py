"""Runtime executor tests, NOT the independent plan-validator stage H5–H7."""

import json
from pathlib import Path

import pytest

from digital_station.contracts import State
from digital_station.scenario import make_initial_state
from digital_station.simulator import GuardViolation, Simulator, at, check_topology
from digital_station.smoke_plan import fixture, load_smoke_plan


def simulator():
    initial = make_initial_state()
    return Simulator(load_smoke_plan(initial), initial)


def advance(engine, target):
    frames = list(engine.advance(target))
    for batch in frames:
        State.model_validate(batch.state.model_dump())
    return frames


def test_topology_and_saved_fixture():
    state = make_initial_state()
    check_topology(state)
    assert len(state.tracks) == 12 and len(state.trains) == 7
    path = Path(__file__).resolve().parents[1] / "fixtures/scenarios/demo_main_v1.smoke_plan.json"
    assert json.loads(path.read_text()) == fixture(state)
    assert not fixture(state)["optimizer_implemented"] and not fixture(state)["independent_validator_passed"]
    broken = state.model_copy(deep=True)
    broken.station.layout.edges = [edge for edge in broken.station.layout.edges if edge.track_id != "C2"]
    with pytest.raises(GuardViolation, match="DISCONNECTED_TOPOLOGY"):
        check_topology(broken)


def test_complete_smoke_scenario_wagon_conservation_and_dags():
    engine = simulator()
    expected_wagons = {w for g in engine.state.wagon_groups for w in g.wagon_ids}
    frames = advance(engine, 5940)
    for batch in frames:
        assert {w for g in batch.state.wagon_groups for w in g.wagon_ids} == expected_wagons
        assert sum(g.wagon_count for g in batch.state.wagon_groups) == 60
        groups = {g.id: g for g in batch.state.wagon_groups}
        for train in batch.state.trains:
            if train.consist_kind == "wagon_groups":
                assert train.body_length_m == sum(groups[g].length_m for g in train.group_ids)
                assert train.total_length_m == train.body_length_m + 20
        ops = {o.id: o for o in batch.state.operations}
        for op in ops.values():
            if op.actual_start_sim_s is not None:
                assert all(ops[p].actual_end_sim_s <= op.actual_start_sim_s for p in op.predecessor_ids)
    assert all(t.status == "departed" for t in engine.state.trains)
    assert all(o.status == "completed" and o.progress == 1 for o in engine.state.operations)
    assert [t.actual_departure_sim_s for t in engine.state.trains] == [480, 2760, 1500, 4380, 5940, 3240, 960]
    assert engine.trains["T4"].group_ids == ["G4B", "G4A", "G4K"]
    assert engine.wagon_groups["G2L"].cargo_state == engine.wagon_groups["G5L"].cargo_state == "loaded"
    assert engine.resources["L1"].location == at("track", "D1")
    assert engine.tracks["D1"].occupied_length_m == 20
    assert not engine.tracks["R3"].assigned_train_id


def test_same_fixture_is_reproducible_across_tick_granularity():
    direct, stepped = simulator(), simulator()
    jumped_frames = advance(direct, 5940)
    stepped_frames = []
    for target in range(60, 5941, 60):
        stepped_frames.extend(advance(stepped, target))
    assert direct.state == stepped.state
    assert [(batch.state.sim_time_s, batch.transitions) for batch in jumped_frames if batch.transitions] == [
        (batch.state.sim_time_s, batch.transitions) for batch in stepped_frames if batch.transitions
    ]


def test_actual_arrival_is_route_completion_not_entry_start():
    engine = simulator()
    advance(engine, 60)
    assert engine.trains["T1"].status == "on_station"
    assert engine.trains["T1"].location.kind == "route"
    assert engine.operations["op-T1-arrival"].actual_start_sim_s == 0
    assert engine.trains["T1"].actual_arrival_sim_s is None
    advance(engine, 120)
    assert engine.trains["T1"].location.kind == "track"
    assert engine.trains["T1"].actual_arrival_sim_s == 120


@pytest.mark.parametrize(
    "offset,phase,location_kind,identifier,attached",
    [
        (0, "empty_to_source", "route", "move-D1-H", True),
        (25, "empty_to_source", "track", "H", True),
        (35, "empty_to_source", "route", "move-H-R3", True),
        (60, "couple", "track", "R3", True),
        (120, "pull_to_lead", "route", "move-R3-H", False),
        (210, "reverse", "track", "H", False),
        (240, "push_to_target", "route", "move-H-C1", False),
        (330, "uncouple", "track", "C1", False),
        (360, "return_to_depot", "route", "move-C1-H", False),
        (385, "return_to_depot", "track", "H", False),
        (395, "return_to_depot", "route", "move-H-D1", False),
    ],
)
def test_every_shunt_phase_and_leg(offset, phase, location_kind, identifier, attached):
    engine = simulator()
    advance(engine, 840 + offset)
    op, loco, group = engine.operations["op-T2-shunt-out"], engine.resources["L1"], engine.wagon_groups["G2L"]
    assert op.phase == phase and op.status == "running"
    assert loco.location.kind == location_kind
    assert (loco.location.track_id or loco.location.route_id) == identifier
    assert (group.current_train_id == "T2") is attached
    assert engine.trains["T2"].body_length_m == (112 if attached else 56)
    assert engine.trains["T2"].total_length_m == (132 if attached else 76)
    assert set(engine.locks(op)[0]) == {"R3", "C1", "H", "D1"}
    if offset == 210:
        assert engine.tracks["H"].occupied_length_m == 76
        assert engine.tracks["R3"].occupied_length_m == 76
    if 120 <= offset < 360:
        assert group.location == loco.location


def test_residual_occupancy_and_complete_return():
    engine = simulator()
    advance(engine, 1260)
    assert engine.tracks["R3"].group_ids == ["G2K"]
    assert engine.tracks["R3"].occupied_length_m == 76 and engine.tracks["R3"].assigned_train_id == "T2"
    assert engine.tracks["C1"].occupied_length_m == 56
    assert engine.wagon_groups["G2L"].current_train_id is None
    assert engine.operations["op-T2-cargo"].status == "running"
    assert engine.resources["L1"].location == at("track", "D1")
    advance(engine, 2400)
    assert engine.trains["T2"].group_ids == ["G2L", "G2K"]
    assert engine.trains["T2"].body_length_m == 112
    assert engine.trains["T2"].total_length_m == 132


def test_current_train_length_is_recomputed_on_recovery_without_changing_roster():
    engine = simulator()
    advance(engine, 1260)
    stale = engine.state.model_copy(deep=True)
    train = next(t for t in stale.trains if t.id == "T2")
    train.body_length_m, train.total_length_m = 112, 132
    recovered = Simulator(stale, make_initial_state())
    assert recovered.trains["T2"].body_length_m == 56
    assert recovered.trains["T2"].total_length_m == 76
    assert recovered.trains["T2"].target_group_ids == ["G2L", "G2K"]
    assert recovered.state.wagon_groups == engine.state.wagon_groups
    assert recovered.state.resources == engine.state.resources
    assert recovered.state.operations == engine.state.operations


def test_type_independence_and_fixed_traction_not_double_counted():
    engine = simulator()
    for train in engine.state.trains:
        train.type = "SERVICE" if train.id == "P1" else "OTHER"
    # Postpone only this synthetic unit-test departure to observe a stationary fixed120m.
    engine.operations["op-P1-departure"].start_sim_s = 1000
    engine.operations["op-P1-departure"].end_sim_s = 1120
    advance(engine, 900)
    assert engine.tracks["R1"].occupied_length_m == 120
    assert engine.trains["P1"].group_ids == []
    assert {o.kind for o in engine.operations.values() if o.train_id == "P1"} == {"arrival", "departure"}
    advance(engine, 5940)
    assert all(t.status == "departed" for t in engine.state.trains)


@pytest.mark.parametrize(
    "failure,expected",
    [
        ("resource", "RESOURCE_UNAVAILABLE"),
        ("closed", "TRACK_CLOSED"),
        ("middle", "MIDDLE_GROUP_EXTRACTION"),
        ("capacity", "CAPACITY"),
        ("route", "DISCONNECTED_ROUTE"),
        ("precedence", "PREDECESSOR_NOT_COMPLETED"),
    ],
)
def test_runtime_guard_blocks_without_losing_groups(failure, expected):
    engine = simulator()
    advance(engine, 839)
    op = engine.operations["op-T2-shunt-out"]
    if failure == "resource":
        engine.resources["SH1"].status = "unavailable"
    elif failure == "closed":
        engine.tracks["C1"].availability = "closed"
    elif failure == "middle":
        engine.trains["T2"].group_ids.reverse()
    elif failure == "capacity":
        engine.tracks["H"].length_m = 50
    elif failure == "route":
        op.route_ids[1] = "move-H-S4"
    else:
        engine.operations["op-T2-inspection"].status = "blocked"
    advance(engine, 840)
    assert op.status == "blocked" and op.blocked_reason_codes == [expected]
    assert engine.resources["L1"].location == at("track", "D1")
    assert engine.wagon_groups["G2L"].current_train_id == "T2"
    assert sum(g.wagon_count for g in engine.state.wagon_groups) == 60


def test_location_progress_moves_and_half_open_resource_handover():
    engine = simulator()
    advance(engine, 60)
    assert engine.trains["T1"].location.route_progress == 0.5
    assert engine.operations["op-T1-arrival"].progress == 0.5
    advance(engine, 120)
    assert engine.tracks["R1"].occupied_length_m == 188
    assert (
        engine.operations["op-T1-arrival"].actual_end_sim_s
        == engine.operations["op-T1-inspection"].actual_start_sim_s
    )
    advance(engine, 4080)
    assert engine.operations["op-T4-shunt-B-back"].status == "completed"
    assert engine.operations["op-T5-shunt-out"].status == "running"
    assert engine.resources["L1"].active_operation_id == "op-T5-shunt-out"
