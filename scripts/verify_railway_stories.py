"""Offline evidence for three railway stories; no database, server or wall-time replay.

Run: .venv/bin/python scripts/verify_railway_stories.py --pretty
The JSON is measured pure-engine/planner/validator evidence, NOT realtime SLA
evidence or a recorded live run. Initial data, priorities and objective are unchanged.
"""

import argparse
import hashlib
import json
import time

from digital_station.calendars import interval_available
from digital_station.contracts import IncidentInput, Operation, PlanSummary
from digital_station.forecast import forecast, objective
from digital_station.incidents import apply_batch
from digital_station.planner import rollout, search
from digital_station.scenario import DEFAULT_CONFIG, make_initial_state
from digital_station.simulator import Simulator
from digital_station.validator import validate_plan


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def ledger(state):
    return {
        group.id: (group.origin_train_id, group.assigned_train_id, tuple(group.wagon_ids))
        for group in state.wagon_groups
    }


def operation_rows(operations):
    return [
        {
            key: getattr(op, key)
            for key in (
                "id",
                "train_id",
                "kind",
                "group_ids",
                "predecessor_ids",
                "start_sim_s",
                "end_sim_s",
                "actual_start_sim_s",
                "actual_end_sim_s",
                "source_track_id",
                "target_track_id",
                "route_ids",
                "resource_ids",
                "status",
            )
        }
        for op in operations
    ]


def run_search(base, initial):
    result = search(
        dict(
            state=base.model_dump(mode="json"),
            initial=initial.model_dump(mode="json"),
            config=DEFAULT_CONFIG,
            cutover_sim_s=base.sim_time_s,
            deadline=time.monotonic() + 5,
        )
    )
    if result["status"] != "succeeded" or not result["candidates"]:
        raise RuntimeError(f"No successful offline search: {result['status']} / {result['attempts']}")
    return result


def planned_state(base, candidate):
    """Apply only validated planned assignments, never edit physical positions."""
    state = base.model_copy(deep=True)
    state.operations = [Operation.model_validate(op) for op in candidate["operations"]]
    # Include the active-plan metadata so subsequent searches retain the existing
    # stability term C. A missing active_plan_id would silently disable that term.
    identifier = f"offline-plan-{digest(candidate['operations'])[:16]}"
    state.plans = [
        PlanSummary(
            id=identifier,
            status="active",
            validity="feasible",
            optimization_run_id=f"offline-job-{identifier}",
            strategy=candidate["strategy"],
            base_input_revision=base.input_revision,
            base_state_version=base.state_version,
            base_active_plan_id=base.active_plan_id,
            config_version=base.config_version,
            created_at=base.server_time,
            cutover_sim_s=base.sim_time_s,
            forecast=candidate["forecast"],
            objective_value=candidate["objective_value"],
            changed_operation_ids=candidate.get("changed_operation_ids", []),
            explanation=candidate.get("explanation", []),
            can_apply=False,
        )
    ]
    state.active_plan_id = identifier
    for train in state.trains:
        train.planned_track_id = next(
            op.target_track_id for op in state.operations if op.train_id == train.id and op.kind == "arrival"
        )
    return state


def describe_candidate(base, initial, candidate):
    operations = [Operation.model_validate(op) for op in candidate["operations"]]
    certificate = validate_plan(base, operations, initial, base.sim_time_s)
    if not certificate.passed:
        raise RuntimeError(f"Independent validator rejected evidence: {certificate.errors}")
    return {
        "strategy": candidate["strategy"],
        "objective_value": candidate["objective_value"],
        "objective_components": candidate["objective_components"],
        "forecast": candidate["forecast"],
        "validator": certificate.public(),
        "departures_sim_s": candidate["departures"],
        "operations": operation_rows(operations),
    }


def execute(base, initial, candidate, target=None, trace_local=False):
    state = planned_state(base, candidate)
    end = target if target is not None else max(op.end_sim_s for op in state.operations)
    engine = Simulator(state, initial)
    original_ledger = ledger(initial)
    initial_fixed = next(t for t in initial.trains if t.id == "P1")
    trace, transitions, sampled = [], 0, 0
    for batch in engine.advance(end):
        sampled += 1
        transitions += len(batch.transitions)
        current = batch.state
        if ledger(current) != original_ledger:
            raise RuntimeError("Wagon identity/ownership changed during physical execution")
        fixed = next(t for t in current.trains if t.id == "P1")
        if fixed.group_ids or fixed.total_length_m != initial_fixed.total_length_m:
            raise RuntimeError("Fixed passenger acquired groups or changed its length")
        if trace_local:
            local = next(t for t in current.trains if t.id == "T2")
            group = next(g for g in current.wagon_groups if g.id == "G2L")
            loco = next(r for r in current.resources if r.id == "L1")
            relevant = [o for o in current.operations if o.train_id == "T2" and o.status == "running"]
            if relevant or any("op-T2-" in entity for ev in batch.transitions for entity in ev["entity_ids"]):
                trace.append(
                    dict(
                        sim_time_s=current.sim_time_s,
                        operations=[dict(id=o.id, phase=o.phase, progress=o.progress) for o in relevant],
                        train_location=local.location.model_dump(mode="json"),
                        attached_group_ids=local.group_ids,
                        train_total_length_m=local.total_length_m,
                        local_group_location=group.location.model_dump(mode="json"),
                        local_group_current_train_id=group.current_train_id,
                        local_group_cargo_state=group.cargo_state,
                        shunter_active_operation_id=loco.active_operation_id,
                        shunter_location=loco.location.model_dump(mode="json"),
                        west_zone_owner=next(z.active_operation_id for z in current.zones if z.id == "W"),
                    )
                )
    return engine.state, dict(sampled_states=sampled, domain_transitions=transitions, local_trace=trace)


def departure(operations, train_id):
    return next(op for op in operations if op.train_id == train_id and op.kind == "departure")


def verify_stories():
    started = time.monotonic()
    initial = make_initial_state()
    main = run_search(initial, initial)
    winner = main["candidates"][0]
    top = [describe_candidate(initial, initial, candidate) for candidate in main["candidates"]]
    baseline = describe_candidate(initial, initial, main["baseline"])
    terminal, execution = execute(initial, initial, winner, trace_local=True)
    if not all(op.status == "completed" for op in terminal.operations):
        raise RuntimeError("Main physical execution did not complete all operations")
    local_final = next(t for t in terminal.trains if t.id == "T2")
    local_group = next(g for g in terminal.wagon_groups if g.id == "G2L")
    actual_shunt_routes = {}
    for op in terminal.operations:
        if op.train_id != "T2" or op.kind != "shunt_transfer":
            continue
        observed: list[str] = []
        for row in execution["local_trace"]:
            route = row["shunter_location"]["route_id"]
            if (
                row["shunter_active_operation_id"] == op.id
                and route
                and (not observed or route != observed[-1])
            ):
                observed.append(route)
        if observed != op.route_ids:
            raise RuntimeError(f"Actual local route sequence differs from validated assignment: {op.id}")
        actual_shunt_routes[op.id] = observed
    story_a = {
        "title": "T2 local work: detach, deliver, load, return, reattach and depart",
        "source_plan_strategy": winner["strategy"],
        "operations": operation_rows([o for o in terminal.operations if o.train_id == "T2"]),
        "execution": execution,
        "actual_shunt_route_sequences": actual_shunt_routes,
        "wagon_identity_and_assignment_preserved_at_every_sample": True,
        "wagon_count": sum(g.wagon_count for g in terminal.wagon_groups),
        "final_attached_groups": local_final.group_ids,
        "target_attached_groups": local_final.target_group_ids,
        "final_cargo_state": local_group.cargo_state,
        "all_33_operations_completed": len(terminal.operations) == 33,
        "all_7_trains_departed": all(t.status == "departed" for t in terminal.trains),
        "T4_final_order": next(t.group_ids for t in terminal.trains if t.id == "T4"),
    }

    urgency_ops, reason = rollout(initial, initial, 0, "urgency", "ascending", time.monotonic() + 3)
    if urgency_ops is None:
        raise RuntimeError(f"Urgency illustration unavailable: {reason}")
    urgency_validation = validate_plan(initial, urgency_ops, initial, 0)
    if not urgency_validation.passed:
        raise RuntimeError(f"Urgency illustration invalid: {urgency_validation.errors}")
    value, components, _, _ = objective(initial, urgency_ops, urgency_validation)
    urgency = dict(
        strategy="urgency/ascending",
        objective_value=value,
        objective_components=components,
        forecast=forecast(initial, urgency_validation, 0, DEFAULT_CONFIG).model_dump(mode="json"),
        operations=[o.model_dump(mode="json") for o in urgency_ops],
        departures={t["id"]: t["actual_departure_sim_s"] for t in urgency_validation.end_state["trains"]},
    )
    urgency_terminal, _ = execute(initial, initial, urgency)
    story_b = {
        "title": "P1 and not-yet-started T2 shunt compete for W; priority is soft",
        "same_initial_input_and_weights": True,
        "main_search_input_digest": main["input_digest"],
        "main_search_attempts": main["attempts"],
        "baseline": baseline,
        "current_top_alternatives": top,
        "independently_validated_urgency_illustration": describe_candidate(initial, initial, urgency),
        "urgency_completed_in_engine": all(o.status == "completed" for o in urgency_terminal.operations),
        "urgency_is_in_current_top_two": any(c["strategy"] == "urgency/ascending" for c in top),
        "urgency_objective_minus_winner": value - winner["objective_value"],
        "claim": "WAIT can protect P1, but is not promised to win the station-wide objective or appear in API top two.",
    }

    checkpoint, _ = execute(initial, initial, winner, target=3300)
    t6 = next(t for t in checkpoint.trains if t.id == "T6")
    old_departure = departure(checkpoint.operations, "T6")
    if t6.status != "ready_departure" or old_departure.status in {"running", "completed"}:
        raise RuntimeError(
            "Story C checkpoint no longer places T6 before departure; revise the evidence recipe"
        )
    travel = 600
    block_end = old_departure.end_sim_s + travel + 420
    cases = []
    for target in ("DEST_E", "DEST_W"):
        request = IncidentInput(
            kind="destination_block", target_id=target, duration_sim_s=block_end - checkpoint.sim_time_s
        )
        changed, _ = apply_batch(checkpoint, [request])
        allowed = interval_available(
            changed,
            old_departure,
            old_departure.start_sim_s,
            old_departure.end_sim_s,
            ({old_departure.source_track_id}, set()),
        )
        result = run_search(changed, initial)
        candidate = result["candidates"][0]
        new_departure = departure([Operation.model_validate(o) for o in candidate["operations"]], "T6")
        finished, _ = execute(changed, initial, candidate)
        projected = new_departure.end_sim_s + travel
        actual_end = departure(finished.operations, "T6").actual_end_sim_s
        direct_positive = target == t6.destination_id
        checks = {
            "old_T6_interval_allowed": allowed,
            "direct_reception_constraint_matches_destination": direct_positive,
            "all_operations_completed": all(o.status == "completed" for o in finished.operations),
            "projected_reception_outside_own_block": not direct_positive
            or not checkpoint.sim_time_s <= projected < block_end,
            "engine_departure_matches_plan": actual_end == new_departure.end_sim_s,
            "frozen_prefix_unchanged": all(
                next(o for o in candidate["operations"] if o["id"] == op.id) == op.model_dump(mode="json")
                for op in checkpoint.operations
                if op.status in {"running", "completed"}
            ),
        }
        if allowed == direct_positive or not all(
            checks[key]
            for key in (
                "all_operations_completed",
                "projected_reception_outside_own_block",
                "engine_departure_matches_plan",
                "frozen_prefix_unchanged",
            )
        ):
            raise RuntimeError(f"Reception story checks failed: {target} {checks}")
        cases.append(
            dict(
                target_id=target,
                role="positive" if direct_positive else "negative_direct_constraint_control",
                incident_input=request.model_dump(mode="json"),
                block_start_sim_s=checkpoint.sim_time_s,
                block_end_sim_s=block_end,
                old_plan_conflicts=[c.model_dump(mode="json") for c in changed.conflicts],
                old_departure_end_sim_s=old_departure.end_sim_s,
                old_projected_reception_sim_s=old_departure.end_sim_s + travel,
                new_departure_start_sim_s=new_departure.start_sim_s,
                new_departure_end_sim_s=new_departure.end_sim_s,
                new_projected_reception_sim_s=projected,
                checks=checks,
                chosen_plan=describe_candidate(changed, initial, candidate),
                note="An unrelated destination can still change T6 timing indirectly through other trains; unchanged timing is not asserted.",
            )
        )
    story_c = {
        "title": "T6 is E_W: check reception at DEST_W, not DEST_E",
        "checkpoint_source": "execute selected validated main plan from sim0 to sim3300, no physical state injection",
        "checkpoint_sim_s": checkpoint.sim_time_s,
        "train_id": t6.id,
        "direction": t6.direction,
        "destination_id": t6.destination_id,
        "travel_time_sim_s": travel,
        "acceptance_rule": "block_start <= departure_end + 600 < block_end is forbidden for matching destination",
        "cases": cases,
    }
    return {
        "schema_version": "railway-stories-evidence-v1",
        "source": "offline_engine_planner_independent_validator",
        "live_database_used": False,
        "live_realtime_SLA_verified": False,
        "recorded_live_history": False,
        "physical_state_injected": False,
        "scenario_id": initial.scenario_id,
        "initial_state_sha256": digest(initial.model_dump(mode="json")),
        "config_sha256": digest(DEFAULT_CONFIG),
        "config": DEFAULT_CONFIG,
        "unchanged_scope": {
            "tracks": len(initial.tracks),
            "trains": len(initial.trains),
            "operations": len(initial.operations),
            "wagon_ids": sum(group.wagon_count for group in initial.wagon_groups),
        },
        "profiles": [
            {
                key: getattr(t, key)
                for key in ("id", "type", "service_profile_id", "direction", "destination_id", "priority")
            }
            for t in initial.trains
        ],
        "timing_note": "Offline computation duration only. No REST/SSE/browser/actor barrier timing is measured.",
        "offline_compute_elapsed_ms": (time.monotonic() - started) * 1000,
        "stories": {"A": story_a, "B": story_b, "C": story_c},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pretty", action="store_true", help="Indent machine-readable JSON output")
    args = parser.parse_args()
    print(json.dumps(verify_stories(), ensure_ascii=False, indent=2 if args.pretty else None))
