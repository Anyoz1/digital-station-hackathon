"""Fair fixed-input FCFS/heuristic benchmark on unchanged engine; NOT live SLA.

Each disruption receives the same FCFS-executed checkpoint at sim480, identical
incident batch/config/seed, frozen prefix and 50sim-s start barrier. Metric window
is [0,7200] for both. All durations are measured, not preset improvements.
"""

import argparse
import copy
import csv
import hashlib
import json
import time
from pathlib import Path
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

from digital_station.contracts import IncidentInput, Operation, PlanSummary, State
from digital_station.efficiency import actual, append_sample, metric_sample, supplementary
from digital_station.forecast import forecast, objective
from digital_station.incidents import apply_batch
from digital_station.planner import rollout, search
from digital_station.scenario import DEFAULT_CONFIG, make_initial_state
from digital_station.simulator import Simulator
from digital_station.validator import validate_plan

BURST5 = [
    dict(kind="train_delay", target_id="T3", duration_sim_s=600, delay_sim_s=300),
    dict(kind="track_closure", target_id="S1", duration_sim_s=600),
    dict(kind="resource_loss", target_id="I2", duration_sim_s=600),
    dict(kind="destination_block", target_id="DEST_E", duration_sim_s=600),
    dict(kind="track_closure", target_id="C2", duration_sim_s=600),
]
CASES = {
    "normal": [],
    "train_delay": [BURST5[0]],
    "track_closure": [BURST5[1]],
    "resource_loss": [BURST5[2]],
    "burst5": BURST5,
    "burst10": BURST5
    + [
        dict(kind="train_delay", target_id="P1", duration_sim_s=600, delay_sim_s=300),
        dict(kind="track_closure", target_id="S2", duration_sim_s=600),
        dict(kind="resource_loss", target_id="CG1", duration_sim_s=600),
        dict(kind="destination_block", target_id="DEST_W", duration_sim_s=600),
        dict(kind="train_delay", target_id="T6", duration_sim_s=600, delay_sim_s=300),
    ],
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def solve(data, algorithm):
    started = time.monotonic()
    data = copy.deepcopy(data)
    data["deadline"] = started + 3
    if algorithm == "heuristic":
        result = search(data)
    else:
        base = State.model_validate(data["state"])
        initial = State.model_validate(data["initial"])
        calculated = time.monotonic()
        ops, reason = rollout(base, initial, data["cutover_sim_s"], "fcfs", "ascending", data["deadline"])
        compute_ms = (time.monotonic() - calculated) * 1000
        candidates = []
        validation_ms = 0.0
        if ops is not None:
            report = validate_plan(base, ops, initial, data["cutover_sim_s"], deadline=data["deadline"])
            validation_ms = report.elapsed_ms
            if report.passed:
                J, components, changed, explanation = objective(base, ops, report)
                candidates.append(
                    dict(
                        strategy="fcfs/ascending",
                        operations=[o.model_dump(mode="json") for o in ops],
                        validator=report.public(),
                        objective_value=J,
                        objective_components=components,
                        forecast=forecast(base, report, data["cutover_sim_s"], data["config"]).model_dump(
                            mode="json"
                        ),
                        changed_operation_ids=changed,
                        explanation=explanation,
                    )
                )
        result = dict(
            status="succeeded" if candidates else "no_feasible_plan",
            candidates=candidates,
            compute_ms=compute_ms,
            validation_ms=validation_ms,
            reason=reason,
        )
    result["offline_solve_elapsed_ms"] = (time.monotonic() - started) * 1000
    return result


def install(base, candidate, cutover):
    state = base.model_copy(deep=True)
    frozen = {o.id: o for o in base.operations if o.status in {"running", "completed"}}
    state.operations = [
        frozen[o["id"]].model_copy(deep=True) if o["id"] in frozen else Operation.model_validate(o)
        for o in candidate["operations"]
    ]
    identifier = "benchmark-plan-" + digest(candidate["operations"])[:16]
    state.plans = [
        PlanSummary(
            id=identifier,
            status="active",
            validity="feasible",
            optimization_run_id="offline-benchmark",
            strategy=candidate["strategy"],
            base_input_revision=base.input_revision,
            base_state_version=base.state_version,
            base_active_plan_id=base.active_plan_id,
            config_version=base.config_version,
            created_at=base.server_time,
            cutover_sim_s=cutover,
            forecast=candidate["forecast"],
            objective_value=candidate["objective_value"],
            changed_operation_ids=candidate["changed_operation_ids"],
            explanation=candidate["explanation"],
            can_apply=False,
        )
    ]
    state.active_plan_id = identifier
    state.conflicts = []  # Exactly as validated apply: obsolete old-plan conflicts are resolved.
    for train in state.trains:
        train.planned_track_id = next(
            o.target_track_id for o in state.operations if o.train_id == train.id and o.kind == "arrival"
        )
    return state


def execute(engine, target, samples, allow_starts=True):
    for batch in engine.advance(target, allow_starts=allow_starts):
        samples[:] = append_sample(samples, metric_sample(batch.state))
    engine.check_inventory()


def benchmark():
    initial = make_initial_state("run-benchmark-v1")
    config = copy.deepcopy(DEFAULT_CONFIG)
    initial_data = dict(
        state=initial.model_dump(mode="json"),
        initial=initial.model_dump(mode="json"),
        config=config,
        cutover_sim_s=0,
    )
    prelude = solve(initial_data, "fcfs")
    if prelude["status"] != "succeeded":
        raise RuntimeError("FCFS prelude failed; cannot create fair common checkpoint")
    engine = Simulator(install(initial, prelude["candidates"][0], 0), initial)
    pre_samples = [metric_sample(initial)]
    execute(engine, 480, pre_samples)
    checkpoint = engine.state.model_copy(deep=True)
    rows, evidence = [], []
    for case, batch in CASES.items():
        base = initial.model_copy(deep=True) if case == "normal" else checkpoint.model_copy(deep=True)
        if batch:
            # Stable event IDs/wall labels for paired offline replay only. No rule,
            # priority, duration or physical position is modified for either solver.
            identifiers = [uuid5(NAMESPACE_URL, f"backend-v1:{case}:{n}") for n in range(len(batch))]
            with (
                patch("digital_station.incidents.uuid4", side_effect=identifiers),
                patch("digital_station.incidents.utc_now", return_value=initial.server_time),
            ):
                base, _ = apply_batch(base, [IncidentInput.model_validate(i) for i in batch])
        cutover = base.sim_time_s + (50 if case != "normal" else 0)
        data = dict(
            state=base.model_dump(mode="json"),
            initial=initial.model_dump(mode="json"),
            config=config,
            cutover_sim_s=cutover,
        )
        input_hash = digest(data)
        for algorithm in ("fcfs", "heuristic"):
            result = solve(data, algorithm)
            if result["status"] != "succeeded":
                raise RuntimeError(f"{case}/{algorithm}: {result['status']}; no metrics fabricated")
            candidate = result["candidates"][0]
            operations = [Operation.model_validate(o) for o in candidate["operations"]]
            independent = validate_plan(base, operations, initial, cutover)
            if not independent.passed:
                raise RuntimeError(f"{case}/{algorithm}: independent validation failed: {independent.errors}")
            barrier = Simulator(base, initial)
            samples = [metric_sample(initial)] if case == "normal" else copy.deepcopy(pre_samples)
            samples = append_sample(samples, metric_sample(base))
            execute(barrier, cutover, samples, allow_starts=False)
            applied = install(barrier.state, candidate, cutover)
            samples = append_sample(samples, metric_sample(applied))
            physical = Simulator(applied, initial)
            execute(physical, 7200, samples)
            end = physical.state
            index = actual(end, samples, config, a=0)
            raw = {f.key: f.raw for f in index.factors}
            extra = supplementary(end, samples, a=0)
            row = dict(
                scenario=case,
                algorithm=algorithm,
                seed=42,
                input_sha256=input_hash,
                window_start_sim_s=0,
                window_end_sim_s=7200,
                incident_sim_s=None if case == "normal" else 480,
                barrier_sim_s=0 if case == "normal" else 50,
                throughput_ratio=raw["throughput"],
                departed_trains=sum(t.status == "departed" for t in end.trains),
                trains_per_hour=extra["trains_per_hour"],
                delay_sim_s=raw["delay"],
                freight_wagon_hours=extra["freight_wagon_hours"],
                resource_idle_ratio=raw["resource_idle"],
                occupancy_ratio=raw["occupancy"],
                unresolved_conflicts=raw["conflicts"],
                efficiency_index=index.score,
                formula_version=index.formula_version,
                objective_J=candidate["objective_value"],
                compute_ms=result["compute_ms"],
                validation_ms=result["validation_ms"],
                offline_solve_elapsed_ms=result["offline_solve_elapsed_ms"],
                total_replan_elapsed_ms=None,
                feasible_alternatives=len(result["candidates"]),
                validator_passed=independent.passed,
                strategy=candidate["strategy"],
                timing_mode="offline_solve_not_SLA",
            )
            rows.append(row)
            evidence.append(
                dict(
                    row=row,
                    inputs=data,
                    result=result,
                    actual=index.model_dump(mode="json"),
                    supplementary=extra,
                    samples=samples,
                    final_state=end.model_dump(mode="json"),
                    validator=independent.public(),
                )
            )
    return rows, evidence


def write_results(target):
    target.mkdir(parents=True, exist_ok=True)
    rows, evidence = benchmark()
    with (target / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (target / "raw.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    lines = [
        "# FCFS vs heuristic — backend v1.0",
        "",
        "Same immutable input per pair; seed42/default config; 12 tracks/7 trains/33 operations.",
        "",
        "Normal starts sim0. Disruptions use the same FCFS-executed sim480 checkpoint, identical H18 incident batches, 50sim-s launch barrier, window [0,7200]. Nothing was tuned to prefer heuristic.",
        "",
        "Offline event-jump physical engine, not recorded live history or realtime SLA. All candidates independently validated. Raw/null values preserved.",
        "",
        "| Case | Algorithm | Departed | Mean delay,sim-s | Wagon-hours | Idle ratio | Actual index | J | Offline compute,ms | Feasible |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"|{row['scenario']}|{row['algorithm']}|{row['departed_trains']}|{row['delay_sim_s']:.3f}|{row['freight_wagon_hours']:.3f}|{row['resource_idle_ratio']:.6f}|{row['efficiency_index']}|{row['objective_J']:.6f}|{row['compute_ms']:.3f}|{row['feasible_alternatives']}|"
        )
    lines += [
        "",
        "Heuristic minimizes J, not actual efficiency index alone. Equal or worse individual factors/index are retained, not hidden. Conflicts here are unresolved at window end; both strategies must be safe, 0 is not a fabricated optimization gain.",
        "",
        "Quality/compute_ms use the offline checkpoint above. total_replan_elapsed_ms is populated only by scripts/benchmark_live_timings.py: paired real REST/DB/worker/publication timings at a separate identical paused sim0 checkpoint, recorded in live-timings.json and live_* columns. Before that companion run it is null. No virtual 50sim-s barrier is advertised as wall time.",
        "",
        "Source: scripts/benchmark_backend.py; raw.json contains exact inputs, candidate/validator, engine samples and final State. Run: uv run python scripts/benchmark_backend.py.",
    ]
    (target / "summary.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(rows, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/benchmark"))
    write_results(parser.parse_args().output)
