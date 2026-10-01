"""Read-only railway explanations from persisted inputs and validated plan details.

No planner, simulator, guard, DB access or state mutation. Presentation is not a
safety certificate: consumers still use the independent validator and can_apply.
"""

from typing import Any

OP_LABELS = {
    "arrival": "Приём",
    "inspection": "Осмотр",
    "shunt_transfer": "Манёвр",
    "cargo": "Погрузка",
    "departure_prep": "Подготовка к отправлению",
    "departure": "Отправление",
}


def explain_plans(job: dict, plans: list[dict]) -> dict[str, Any]:
    base = job.get("inputs", {}).get("state")
    if not base:
        return {"available": False, "reason": "Исходный снимок расчёта не сохранён", "plans": []}
    old = {op["id"]: op for op in base["operations"]}
    trains = {train["id"]: train for train in base["trains"]}
    result = []
    for plan in plans:
        changes, departures = [], []
        for op in plan["operations"]:
            previous = old[op["id"]]
            if op["kind"] == "departure":
                end = op["actual_end_sim_s"] if op["status"] == "completed" else op["end_sim_s"]
                departures.append(
                    {
                        "train_id": op["train_id"],
                        "departure_sim_s": end,
                        "delay_sim_s": max(0, end - trains[op["train_id"]]["due_departure_sim_s"])
                        if end is not None and plan["validity"] == "feasible"
                        else None,
                        "basis": "actual" if op["status"] == "completed" else "forecast",
                    }
                )
            if op["id"] not in plan["changed_operation_ids"]:
                continue
            delta = (
                op["start_sim_s"] - previous["start_sim_s"]
                if op["start_sim_s"] is not None and previous["start_sim_s"] is not None
                else None
            )
            changes.append(
                {
                    "operation_id": op["id"],
                    "train_id": op["train_id"],
                    "label": OP_LABELS.get(op["kind"], op["kind"]),
                    "before_start_sim_s": previous["start_sim_s"],
                    "after_start_sim_s": op["start_sim_s"],
                    "shift_sim_s": delta,
                    "before_track_id": previous["target_track_id"] or previous["source_track_id"],
                    "after_track_id": op["target_track_id"] or op["source_track_id"],
                    "before_resource_ids": previous["resource_ids"],
                    "after_resource_ids": op["resource_ids"],
                }
            )
        result.append(
            {
                "plan_id": plan["id"],
                "changes": changes,
                "departures": departures,
                "reason": "Варианты ранжированы по общей цели: задержки, ожидание, маневровая работа и стабильность. "
                "Высокий приоритет не прерывает начатый манёвр и не гарантирует проход первым. "
                "Допустимость проверена отдельно; глобальная оптимальность не доказана.",
            }
        )
    return {
        "available": True,
        "run_id": base["run_id"],
        "replan_id": job["job"]["id"],
        "base_event_seq": base["event_seq"],
        "base_plan_id": base["active_plan_id"],
        "plans": result,
    }
