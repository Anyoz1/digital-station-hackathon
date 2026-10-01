"""Forecast efficiency-v1 and J: validated rollout facts only, not fabricated gains."""

from typing import Any

from .contracts import Efficiency, Factor


def forecast(base, report, cutover, config):
    end = cutover + 7200
    trains = report.end_state["trains"]
    cohort = [
        t
        for t in trains
        if (t["actual_departure_sim_s"] is None or t["actual_departure_sim_s"] > cutover)
        and t["due_departure_sim_s"] <= end
    ]
    samples = report.samples
    occupied_seconds, idle, demand = 0.0, 0.0, 0.0
    activity = False
    for sample, following in zip(samples, samples[1:]):
        seconds = max(0, min(end, following["sim_time_s"]) - max(cutover, sample["sim_time_s"]))
        if not seconds:
            continue
        occupied_seconds += seconds * sum(t.startswith(("R", "S", "C")) for t in sample["occupied_track_ids"])
        activity |= sample["activity"]
        for pool in sample["pools"].values():
            ready_demand = min(pool["available"], pool["demand"])
            idle += seconds * max(0, ready_demand - pool["busy"])
            demand += seconds * ready_demand
    raw: dict[str, Any] = {
        "throughput": sum(
            t["actual_departure_sim_s"] is not None and t["actual_departure_sim_s"] <= end for t in cohort
        )
        / len(cohort)
        if cohort
        else None,
        "delay": sum(
            max(0, min(t["actual_departure_sim_s"] or end, end) - t["due_departure_sim_s"]) for t in cohort
        )
        / len(cohort)
        if cohort
        else None,
        "occupancy": occupied_seconds / (10 * 7200),
        "conflicts": 0.0,
        "resource_idle": idle / demand if demand else 0.0,
    }
    penalties = {
        "throughput": 1 - raw["throughput"] if cohort else None,
        "delay": min(raw["delay"] / 900, 1) if cohort else None,
        "occupancy": max(0, min(1, (raw["occupancy"] - 0.8) / 0.2)),
        "conflicts": 0.0,
        "resource_idle": raw["resource_idle"],
    }
    weights = config["weights"]
    available_weight = sum(weights[k] for k, v in penalties.items() if v is not None)
    factors = [
        Factor(
            key=k,
            raw=value,
            unit="sim_seconds" if k == "delay" else "count" if k == "conflicts" else "ratio",
            norm_penalty=penalties[k],
            weight=weights[k],
            contribution=100 * weights[k] / available_weight * penalties[k]
            if penalties[k] is not None and available_weight
            else None,
            reason="Прогноз по независимо проверенным transitions"
            if value is not None
            else "Нет поездов в когорте",
        )
        for k, value in raw.items()
    ]
    score = (
        round(100 - sum(f.contribution or 0 for f in factors), 1) if available_weight and activity else None
    )
    category = (
        None
        if score is None
        else "normal"
        if score >= config["category_thresholds"]["normal_min"]
        else ("attention" if score >= config["category_thresholds"]["attention_min"] else "critical")
    )
    return Efficiency(
        mode="forecast",
        formula_version="efficiency-v1",
        window_start_sim_s=cutover,
        window_end_sim_s=end,
        score=score,
        category=category,
        factors=factors,
    )


def changes(base, operations):
    previous = {o.id: o for o in base.operations}
    changed, significant, explanations = [], [], []
    for op in operations:
        old = previous[op.id]
        if old.status in {"running", "completed"}:
            continue
        assignment = (op.source_track_id, op.target_track_id, op.route_ids, sorted(op.resource_ids)) != (
            old.source_track_id,
            old.target_track_id,
            old.route_ids,
            sorted(old.resource_ids),
        )
        time_changed = op.start_sim_s != old.start_sim_s
        if assignment or time_changed:
            changed.append(op.id)
            explanations.append(
                f"{op.id}: {old.start_sim_s}→{op.start_sim_s} sim s; путь {old.target_track_id}→{op.target_track_id}; ресурсы {','.join(op.resource_ids)}"
            )
        if assignment or old.start_sim_s is None or abs(op.start_sim_s - old.start_sim_s) > 60:
            significant.append(op.id)
    return changed, significant, explanations


def objective(base, operations, report):
    trains = report.end_state["trains"]
    lateness = sum(
        t["priority"] * max(0, t["actual_departure_sim_s"] - t["due_departure_sim_s"]) for t in trains
    )
    L = lateness / (900 * sum(t["priority"] for t in trains))
    V = sum(t["actual_departure_sim_s"] - t["expected_arrival_sim_s"] for t in trains) / (7200 * len(trains))
    M = sum(o.duration_sim_s for o in operations if o.kind == "shunt_transfer") / 7200
    changed, significant, explanations = changes(base, operations)
    C = (
        len(significant) / max(1, sum(o.status not in {"running", "completed"} for o in base.operations))
        if base.active_plan_id
        else 0
    )
    return 0.60 * L + 0.25 * V + 0.10 * M + 0.05 * C, dict(L=L, V=V, M=M, C=C), changed, explanations
