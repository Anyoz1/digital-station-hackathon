import copy
import time

import pytest

from digital_station import planner
from digital_station.contracts import Operation, State
from digital_station.forecast import objective
from digital_station.scenario import DEFAULT_CONFIG, make_initial_state
from digital_station.simulator import Simulator
from digital_station.smoke_plan import load_smoke_plan
from digital_station.validator import ValidationResult, validate_plan


def inputs(state=None, rollouts=12):
    initial = make_initial_state()
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["planner"]["max_rollouts"] = rollouts
    return dict(
        state=(state or initial).model_dump(mode="json"),
        initial=initial.model_dump(mode="json"),
        config=config,
        cutover_sim_s=(state or initial).sim_time_s,
        deadline=time.monotonic() + 3,
    )


def test_fcfs_and_heuristics_same_input_two_distinct_validated_candidates():
    data = inputs()
    result = planner.search(data)
    assert result["status"] == "succeeded" and len(result["candidates"]) == 2
    assert result["baseline"] and result["baseline"]["validator"]["passed"]
    assert {a["input_digest"] for a in result["attempts"]} == {result["input_digest"]}
    signatures = []
    for candidate in result["candidates"]:
        ops = [Operation.model_validate(o) for o in candidate["operations"]]
        report = validate_plan(
            State.model_validate(data["state"]), ops, State.model_validate(data["initial"]), 0
        )
        assert report.passed, report.errors
        signatures.append(
            [(o.id, o.start_sim_s, o.source_track_id, o.target_track_id, o.resource_ids) for o in ops]
        )
        assert candidate["forecast"]["mode"] == "forecast"
        assert candidate["forecast"]["window_end_sim_s"] == 7200
        assert all(f["raw"] is not None for f in candidate["forecast"]["factors"])
    assert signatures[0] != signatures[1]
    assert result["candidates"][0]["objective_value"] <= result["baseline"]["objective_value"]
    assert len(result["attempts"]) <= 12


def test_least_scarce_counts_other_unfinished_operations_not_trains():
    initial = make_initial_state()
    engine = Simulator(initial, initial)
    counts = planner.scarcity_counts(engine, engine.operations["op-T1-arrival"], ["R1", "R3"])
    # The two passenger-profile operations can use R1/R2, not R3/R4.
    assert counts == {"R1": 10, "R3": 28}
    assert planner.ranked(counts, "least_scarce", counts) == ["R1", "R3"]


def test_priority_wait_is_generic_and_fcfs_never_waits_on_purpose():
    initial = make_initial_state()
    initial.trains[-1].type = "OTHER"
    fcfs, _ = planner.rollout(initial, initial, 0, "fcfs", "ascending", time.monotonic() + 3)
    urgency, _ = planner.rollout(initial, initial, 0, "urgency", "ascending", time.monotonic() + 3)
    assert next(o for o in fcfs if o.id == "op-T2-shunt-out").start_sim_s == 540
    assert next(o for o in urgency if o.id == "op-P1-arrival").start_sim_s == 720
    assert next(o for o in urgency if o.id == "op-T2-shunt-out").start_sim_s >= 840


def test_planner_preserves_running_prefix_and_resident_home():
    initial = make_initial_state()
    engine = Simulator(load_smoke_plan(initial), initial)
    list(engine.advance(1080))
    data = inputs(engine.state)
    data["cutover_sim_s"] = 1130
    result = planner.search(data)
    assert result["status"] == "succeeded", result["attempts"]
    for candidate in result["candidates"]:
        by_id = {o["id"]: o for o in candidate["operations"]}
        for op in engine.state.operations:
            if op.status in {"running", "completed"}:
                assert by_id[op.id] == op.model_dump(mode="json")
        assert by_id["op-T2-departure"]["source_track_id"] == "R3"


def test_no_feasible_is_not_timeout():
    state = make_initial_state()
    for track in state.tracks:
        if track.id.startswith("R"):
            track.availability = "closed"
    no_plan = planner.search(inputs(state))
    assert no_plan["status"] == "no_feasible_plan" and no_plan["candidates"] == []
    expired = inputs()
    expired["deadline"] = time.monotonic() - 1
    timeout = planner.search(expired)
    assert timeout["status"] == "timeout" and timeout["candidates"] == []


def test_invalid_candidates_are_never_returned_as_feasible(monkeypatch):
    monkeypatch.setattr(
        planner,
        "validate_plan",
        lambda *args, **kwargs: ValidationResult(
            False, [dict(code="DELIBERATELY_INVALID", entity_ids=[], message="test negative")]
        ),
    )
    result = planner.search(inputs())
    assert result["candidates"] == [] and result["baseline"] is None
    assert all(a["outcome"] == "invalid" for a in result["attempts"])


def test_deterministic_results_and_type_independence():
    data = inputs(rollouts=3)
    first = planner.search(data)
    for t in data["state"]["trains"]:
        t["type"] = "SERVICE"
    second = planner.search(data)
    assert [(c["operations"], c["objective_value"]) for c in first["candidates"]] == [
        (c["operations"], c["objective_value"]) for c in second["candidates"]
    ]


def test_objective_and_forecast_formula_are_from_actual_validated_facts():
    initial = make_initial_state()
    state = load_smoke_plan(initial)
    report = validate_plan(state, state.operations, initial, 0)
    J, components, _, _ = objective(state, state.operations, report)
    assert J == pytest.approx(
        0.60 * components["L"] + 0.25 * components["V"] + 0.10 * components["M"] + 0.05 * components["C"]
    )
    assert components["M"] == pytest.approx(8 * 420 / 7200)
    assert components["C"] == 0
