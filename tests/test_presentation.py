"""Read-only explanations: persisted-before differences, no fabricated delay, auth.

API cases use conftest's isolated digital_station_h0_test database only.
"""

import asyncio
import copy
import time
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from digital_station.db import DomainEvent, OptimizationRun, RunState, database
from digital_station.presentation import explain_plans
from digital_station.scenario import make_initial_state
from digital_station.smoke_plan import load_smoke_plan


def sample_explanation_input():
    base = load_smoke_plan(make_initial_state()).model_dump(mode="json")
    base["active_plan_id"] = "plan-before"
    base["event_seq"] = 31
    operations = copy.deepcopy(base["operations"])
    changed = next(op for op in operations if op["id"] == "op-T1-arrival")
    changed["start_sim_s"] += 180
    changed["end_sim_s"] += 180
    changed["target_track_id"] = "R2"
    changed["resource_ids"] = ["TL1", "TC1"]
    job = {"job": {"id": "rp-example"}, "inputs": {"state": base}}
    plan = {
        "id": "plan-after",
        "validity": "feasible",
        "operations": operations,
        "changed_operation_ids": [changed["id"]],
    }
    return job, plan


def test_explanation_uses_saved_before_input_and_does_not_mutate_it():
    job, plan = sample_explanation_input()
    before_job, before_plan = copy.deepcopy(job), copy.deepcopy(plan)
    result = explain_plans(job, [plan])
    assert result["available"] is True
    assert result["replan_id"] == "rp-example"
    assert result["base_event_seq"] == 31
    assert result["base_plan_id"] == "plan-before"
    changes = result["plans"][0]["changes"]
    assert len(changes) == 1
    assert changes[0]["operation_id"] == "op-T1-arrival"
    assert changes[0]["before_start_sim_s"] == 0
    assert changes[0]["after_start_sim_s"] == 180
    assert changes[0]["shift_sim_s"] == 180
    assert changes[0]["before_track_id"] == "R1"
    assert changes[0]["after_track_id"] == "R2"
    assert changes[0]["before_resource_ids"] == ["TL1", "TC1"]
    assert job == before_job and plan == before_plan
    assert "inputs" not in result and "state" not in result
    assert "глобальная оптимальность не доказана" in result["plans"][0]["reason"]


def test_missing_saved_input_returns_explicit_unavailable_not_current_or_empty_fake_diff():
    result = explain_plans({"job": {"id": "legacy-job"}}, [])
    assert result == {
        "available": False,
        "reason": "Исходный снимок расчёта не сохранён",
        "plans": [],
    }


@pytest.mark.parametrize(
    "before,after,expected", [(None, 0, None), (0, None, None), (0, 0, 0), (300, 0, -300)]
)
def test_shift_distinguishes_missing_time_zero_and_earlier_start(before, after, expected):
    job, plan = sample_explanation_input()
    next(op for op in job["inputs"]["state"]["operations"] if op["id"] == "op-T1-arrival")["start_sim_s"] = (
        before
    )
    next(op for op in plan["operations"] if op["id"] == "op-T1-arrival")["start_sim_s"] = after
    assert explain_plans(job, [plan])["plans"][0]["changes"][0]["shift_sim_s"] == expected


@pytest.mark.parametrize(
    "validity,status,planned_end,actual_end,expected_time,expected_delay,basis",
    [
        ("feasible", "planned", 780, None, 780, 180, "forecast"),
        ("feasible", "completed", 780, 660, 660, 60, "actual"),
        ("feasible", "completed", 780, 480, 480, 0, "actual"),
        ("feasible", "planned", None, None, None, None, "forecast"),
        ("feasible", "completed", 780, None, None, None, "actual"),
        ("invalid", "planned", 780, None, 780, None, "forecast"),
        ("invalid", "completed", 780, 660, 660, None, "actual"),
    ],
)
def test_departure_delay_is_backend_time_with_actual_forecast_basis_or_null(
    validity, status, planned_end, actual_end, expected_time, expected_delay, basis
):
    job, plan = sample_explanation_input()
    plan["validity"] = validity
    departure = next(op for op in plan["operations"] if op["id"] == "op-T1-departure")
    departure.update(status=status, end_sim_s=planned_end, actual_end_sim_s=actual_end)
    result = explain_plans(job, [plan])
    actual = next(item for item in result["plans"][0]["departures"] if item["train_id"] == "T1")
    assert actual == {
        "train_id": "T1",
        "departure_sim_s": expected_time,
        "delay_sim_s": expected_delay,
        "basis": basis,
    }


def wait_for_job(client, job_id):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        reply = client.get(f"/api/v1/replans/{job_id}")
        assert reply.status_code == 200, reply.text
        detail = reply.json()
        # Completion status is first published with the plan, then the measured
        # post-publication timing is committed. Await both before asserting that
        # subsequent read-only requests cannot modify a paused State.
        if (
            detail["job"]["status"] not in {"queued", "running"}
            and client.app.state.actor.coordinator.owner is None
        ):
            return detail
        time.sleep(0.025)
    pytest.fail("Explanation integration fixture: planner did not finish")


def persisted_domain(test_settings, run_id, job_id):
    async def read():
        engine, sessions = database(test_settings.database_url.get_secret_value())
        try:
            async with sessions() as session:
                state = await session.get(RunState, run_id)
                job = await session.get(OptimizationRun, job_id)
                event_count = await session.scalar(
                    select(func.count()).select_from(DomainEvent).where(DomainEvent.run_id == run_id)
                )
                return copy.deepcopy({"state": state.payload, "job": job.payload, "events": event_count})
        finally:
            await engine.dispose()

    return asyncio.run(read())


def test_explanation_api_requires_auth_before_disclosing_job_existence(client, login):
    job_id = client.app.state.actor.state.last_replan.id
    for identifier in (job_id, "nonexistent-job"):
        response = client.get(f"/api/v1/replans/{identifier}/explanation")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"
        assert response.headers["cache-control"] == "no-store"
    login("viewer")
    assert client.get(f"/api/v1/replans/{job_id}/explanation").status_code == 200
    assert client.get("/api/v1/replans/nonexistent-job/explanation").status_code == 404
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get(f"/api/v1/replans/{job_id}/explanation").status_code == 401


def test_explanation_api_preserves_versions_and_compares_persisted_before_autoapply(
    client, login, test_settings
):
    login()
    before = client.get("/api/v1/snapshot").json()
    assert before["mode"] == "paused"
    reply = client.post(
        "/api/v1/incidents",
        json={
            "request_id": str(uuid4()),
            "run_id": before["run_id"],
            "expected_input_revision": before["input_revision"],
            "items": [{"kind": "train_delay", "target_id": "T1", "duration_sim_s": 900, "delay_sim_s": 300}],
        },
    )
    assert reply.status_code == 201, reply.text
    job_id = reply.json()["result"]["replan_id"]
    detail = wait_for_job(client, job_id)
    assert detail["job"]["status"] == "succeeded", detail
    now = client.get("/api/v1/snapshot").json()
    assert now["active_plan_id"] == detail["job"]["applied_plan_id"]
    assert now["active_plan_id"] != before["active_plan_id"]
    login("viewer")  # The new endpoint is authenticated read access, not a dispatcher command.
    durable_before = persisted_domain(test_settings, now["run_id"], job_id)
    response = client.get(f"/api/v1/replans/{job_id}/explanation")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    explanation = response.json()
    assert client.get(f"/api/v1/replans/{job_id}/explanation").json() == explanation
    durable_after = persisted_domain(test_settings, now["run_id"], job_id)
    assert durable_before == durable_after
    after = client.get("/api/v1/snapshot").json()
    assert {key: value for key, value in after.items() if key != "server_time"} == {
        key: value for key, value in now.items() if key != "server_time"
    }

    saved = durable_before["job"]["inputs"]["state"]
    assert explanation["base_event_seq"] == saved["event_seq"] < now["event_seq"]
    assert explanation["base_plan_id"] == saved["active_plan_id"] == before["active_plan_id"]
    base_ops = {op["id"]: op for op in saved["operations"]}
    current_ops = {op["id"]: op for op in now["operations"]}
    plan_ops = {plan["id"]: {op["id"]: op for op in plan["operations"]} for plan in detail["plans"]}
    for plan in explanation["plans"]:
        for change in plan["changes"]:
            source, target = (
                base_ops[change["operation_id"]],
                plan_ops[plan["plan_id"]][change["operation_id"]],
            )
            assert change["before_start_sim_s"] == source["start_sim_s"]
            assert change["after_start_sim_s"] == target["start_sim_s"]
            assert change["before_resource_ids"] == source["resource_ids"]
            assert change["after_resource_ids"] == target["resource_ids"]
    applied = next(plan for plan in explanation["plans"] if plan["plan_id"] == now["active_plan_id"])
    arrival = next(change for change in applied["changes"] if change["operation_id"] == "op-T1-arrival")
    assert arrival["before_start_sim_s"] != current_ops["op-T1-arrival"]["start_sim_s"]
    assert arrival["after_start_sim_s"] == current_ops["op-T1-arrival"]["start_sim_s"]
    assert arrival["shift_sim_s"] >= 300
    assert "inputs" not in explanation and "state" not in explanation
