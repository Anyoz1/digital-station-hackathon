import importlib.util
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def stories():
    script = Path(__file__).resolve().parents[1] / "scripts/verify_railway_stories.py"
    spec = importlib.util.spec_from_file_location("railway_stories", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify_stories()


def test_story_evidence_is_offline_and_keeps_scope(stories):
    assert stories["source"] == "offline_engine_planner_independent_validator"
    assert not stories["live_database_used"] and not stories["live_realtime_SLA_verified"]
    assert not stories["recorded_live_history"] and not stories["physical_state_injected"]
    assert stories["unchanged_scope"] == {"tracks": 12, "trains": 7, "operations": 33, "wagon_ids": 60}


def test_local_story_uses_actual_engine_paths_and_preserves_identity(stories):
    local = stories["stories"]["A"]
    assert local["wagon_identity_and_assignment_preserved_at_every_sample"]
    assert local["final_cargo_state"] == "loaded"
    assert local["final_attached_groups"] == local["target_attached_groups"] == ["G2L", "G2K"]
    assert local["all_33_operations_completed"] and local["all_7_trains_departed"]
    assert local["T4_final_order"] == ["G4B", "G4A", "G4K"]
    assert [o["kind"] for o in local["operations"]] == [
        "arrival",
        "inspection",
        "shunt_transfer",
        "cargo",
        "shunt_transfer",
        "departure_prep",
        "departure",
    ]
    routes = {row["shunter_location"]["route_id"] for row in local["execution"]["local_trace"]}
    assert {"move-D1-H", "move-H-D1", "move-H-R3", "move-R3-H"} <= routes
    for operation in local["operations"]:
        if operation["kind"] == "shunt_transfer":
            assert local["actual_shunt_route_sequences"][operation["id"]] == operation["route_ids"]
    assert any(row["local_group_current_train_id"] is None for row in local["execution"]["local_trace"])


def test_priority_story_does_not_claim_urgency_wins(stories):
    priority = stories["stories"]["B"]
    assert len(priority["current_top_alternatives"]) == 2
    assert all(c["validator"]["passed"] for c in priority["current_top_alternatives"])
    urgency = priority["independently_validated_urgency_illustration"]
    assert urgency["validator"]["passed"] and priority["urgency_completed_in_engine"]
    assert urgency["departures_sim_s"]["P1"] == 960
    assert priority["baseline"]["departures_sim_s"]["P1"] > 960
    # An explicit regression guard against turning a losing illustration into a winner claim.
    assert priority["urgency_objective_minus_winner"] > 0
    assert not priority["urgency_is_in_current_top_two"]


def test_destination_story_targets_west_and_keeps_negative_control(stories):
    reception = stories["stories"]["C"]
    assert reception["direction"] == "E_W" and reception["destination_id"] == "DEST_W"
    assert reception["travel_time_sim_s"] == 600
    cases = {case["target_id"]: case for case in reception["cases"]}
    assert cases["DEST_E"]["checks"]["old_T6_interval_allowed"]
    assert not cases["DEST_W"]["checks"]["old_T6_interval_allowed"]
    for case in cases.values():
        assert case["chosen_plan"]["validator"]["passed"]
        assert case["checks"]["frozen_prefix_unchanged"]
        assert case["checks"]["all_operations_completed"]
        assert case["checks"]["engine_departure_matches_plan"]
    assert cases["DEST_W"]["new_projected_reception_sim_s"] >= cases["DEST_W"]["block_end_sim_s"]
