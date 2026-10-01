"""efficiency-v1 from recorded physical state transitions; never calls a planner."""

from .contracts import Efficiency, Factor, State

POOLS = {
    "L1": (["L1"], {"shunt_transfer"}),
    "SH1": (["SH1"], {"shunt_transfer"}),
    "inspection": (["I1", "I2"], {"inspection", "departure_prep"}),
    "CG1": (["CG1"], {"cargo"}),
}


def metric_sample(state: State) -> dict:
    """Right-continuous facts for resource/track integrals, compact enough for the journal."""
    resources = {r.id: r for r in state.resources}
    operations = {o.id: o for o in state.operations}
    trains = {t.id: t for t in state.trains}
    pools = {}
    for name, (ids, kinds) in POOLS.items():
        available = sum(resources[r].status != "unavailable" for r in ids)
        busy = sum(resources[r].active_operation_id is not None for r in ids)
        demand = sum(
            op.kind in kinds
            and (
                op.status == "running"
                or (
                    op.status != "completed"
                    and trains[op.train_id].expected_arrival_sim_s <= state.sim_time_s
                    and all(operations[p].status == "completed" for p in op.predecessor_ids)
                )
            )
            for op in state.operations
        )
        pools[name] = dict(available=available, busy=busy, demand=demand)
    return {
        "sim_time_s": state.sim_time_s,
        "occupied_track_ids": [
            t.id
            for t in state.tracks
            if t.kind in {"receiving_departure", "sorting", "cargo"} and t.occupied_length_m > 0
        ],
        "pools": pools,
        "activity": any(t.status != "departed" for t in state.trains) or bool(state.conflicts),
        "resources": {
            r.id: {"available": r.status != "unavailable", "busy": r.active_operation_id is not None}
            for r in state.resources
        },
    }


def append_sample(samples: list[dict], sample: dict, retain_from: int | None = None) -> list[dict]:
    result = samples.copy()
    if result and result[-1]["sim_time_s"] == sample["sim_time_s"]:
        result[-1] = sample
    else:
        result.append(sample)
    if retain_from is not None:
        first = 0
        while first + 1 < len(result) and result[first + 1]["sim_time_s"] <= retain_from:
            first += 1
        result = result[first:]
    return result


def intervals(samples: list[dict], a: int, b: int):
    for i, sample in enumerate(samples):
        following = samples[i + 1]["sim_time_s"] if i + 1 < len(samples) else b
        seconds = max(0, min(b, following) - max(a, sample["sim_time_s"]))
        if seconds:
            yield sample, seconds


def actual(state: State, samples: list[dict], config: dict, a: int | None = None) -> Efficiency:
    b = state.sim_time_s
    a = max(0, b - 900) if a is None else a
    cohort = [
        t
        for t in state.trains
        if (t.actual_departure_sim_s is None or t.actual_departure_sim_s > a) and t.due_departure_sim_s <= b
    ]
    occupied, idle, demand, activity = 0.0, 0.0, 0.0, False
    for sample, seconds in intervals(samples, a, b):
        occupied += seconds * len(sample["occupied_track_ids"])
        activity |= sample["activity"]
        for pool in sample["pools"].values():
            needed = min(pool["available"], pool["demand"])
            idle += seconds * max(0, needed - pool["busy"])
            demand += seconds * needed
    activity |= bool(state.conflicts) or any(t.status != "departed" for t in cohort)
    raw = {
        "throughput": sum(t.actual_departure_sim_s is not None for t in cohort) / len(cohort)
        if cohort
        else None,
        "delay": sum(
            max(
                0,
                (t.actual_departure_sim_s if t.actual_departure_sim_s is not None else b)
                - t.due_departure_sim_s,
            )
            for t in cohort
        )
        / len(cohort)
        if cohort
        else None,
        "occupancy": occupied / (10 * (b - a)) if b > a else None,
        "conflicts": float(len({c.id for c in state.conflicts})),
        "resource_idle": idle / demand if demand else 0.0,
    }
    if b <= a:
        raw = {key: None for key in raw}
    penalties = {
        "throughput": 1 - raw["throughput"] if raw["throughput"] is not None else None,
        "delay": min(raw["delay"] / 900, 1) if raw["delay"] is not None else None,
        "occupancy": max(0, min(1, (raw["occupancy"] - 0.8) / 0.2)) if raw["occupancy"] is not None else None,
        "conflicts": min(raw["conflicts"] / 3, 1) if raw["conflicts"] is not None else None,
        "resource_idle": raw["resource_idle"],
    }
    weights = config["weights"]
    total = sum(weights[k] for k, p in penalties.items() if p is not None)
    affected = ", ".join(t.id for t in cohort) or "нет поездов с наступившим сроком отправления"
    reasons = {
        "throughput": f"Фактические отправления к концу окна; когорта: {affected}",
        "delay": f"Фактическое опоздание/ожидание относительно due; когорта: {affected}",
        "occupancy": "Интеграл физической занятости R1–R4/S1–S4/C1–C2; H/D1 исключены",
        "conflicts": "Нерешённые предупреждения: " + (", ".join(c.id for c in state.conflicts) or "нет"),
        "resource_idle": "Интеграл незанятых доступных L1/SH1/I1/I2/CG1 при готовом спросе",
    }
    factors = [
        Factor(
            key=key,
            raw=value,
            unit="sim_seconds" if key == "delay" else "count" if key == "conflicts" else "ratio",
            norm_penalty=penalties[key],
            weight=weights[key],
            contribution=100 * weights[key] / total * penalties[key]
            if penalties[key] is not None and total
            else None,
            reason=reasons[key] if value is not None else "Недостаточно данных для фактора",
        )
        for key, value in raw.items()
    ]
    score = round(100 - sum(f.contribution or 0 for f in factors), 1) if total and activity else None
    category = (
        None
        if score is None
        else (
            "normal"
            if score >= config["category_thresholds"]["normal_min"]
            else "attention"
            if score >= config["category_thresholds"]["attention_min"]
            else "critical"
        )
    )
    return Efficiency(
        mode="actual",
        formula_version="efficiency-v1",
        window_start_sim_s=a,
        window_end_sim_s=b,
        score=score,
        category=category,
        factors=factors,
    )


def supplementary(state: State, samples: list[dict], a: int) -> dict:
    b = state.sim_time_s
    departures = sum(
        t.actual_departure_sim_s is not None and a < t.actual_departure_sim_s <= b for t in state.trains
    )
    wagon_hours = 0.0
    for train in state.trains:
        if train.consist_kind != "wagon_groups":
            continue
        entry = next(o for o in state.operations if o.train_id == train.id and o.kind == "arrival")
        if entry.actual_start_sim_s is not None:
            seconds = max(0, min(b, train.actual_departure_sim_s or b) - max(a, entry.actual_start_sim_s))
            count = sum(g.wagon_count for g in state.wagon_groups if g.assigned_train_id == train.id)
            wagon_hours += count * seconds / 3600
    resource_usage = {}
    for resource in state.resources:
        available, busy = 0.0, 0.0
        for sample, seconds in intervals(samples, a, b):
            item = sample.get("resources", {}).get(resource.id, {})
            available += seconds * bool(item.get("available"))
            busy += seconds * bool(item.get("busy"))
        resource_usage[resource.id] = 100 * busy / available if available else None
    return {
        "trains_per_hour": departures * 3600 / (b - a) if b > a else None,
        "freight_wagon_hours": wagon_hours if b > a else None,
        "resource_busy_percent": resource_usage,
    }
