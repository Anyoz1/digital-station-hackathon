import asyncio
import csv
import hashlib
import io
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from test_planning_api import envelope, wait_result

from digital_station.contracts import ConfigPatchInput
from digital_station.db import ConfigRevision, DomainEvent, OptimizationRun, RunState, database
from digital_station.reporting import safe_cell


def advance(client, seconds):
    client.portal.call(client.app.state.actor.advance, seconds)


def canonical(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize("role", ["viewer", "operator", "dispatcher", "admin"])
def test_new_read_endpoints_all_roles_and_config_manual_rbac(client, login, role):
    login(role)
    state = client.get("/api/v1/snapshot").json()
    run = state["run_id"]
    assert client.get("/api/v1/config").status_code == 200
    assert client.get("/api/v1/history", params={"run_id": run}).status_code == 200
    assert client.get("/api/v1/history/snapshot", params={"run_id": run, "seq": 1}).status_code == 200
    assert client.get("/api/v1/reports.csv", params={"run_id": run}).status_code == 200
    response = client.patch(
        "/api/v1/config",
        json={**envelope(client), "patch": {"category_thresholds": {"normal_min": 80, "attention_min": 50}}},
    )
    assert response.status_code == (200 if role == "admin" else 403)
    response = client.post("/api/v1/operations/op-T1-arrival/complete", json=envelope(client))
    assert response.status_code == (409 if role == "admin" else 403)


@pytest.mark.parametrize(
    "path",
    ["/api/v1/history?run_id=x", "/api/v1/history/snapshot?run_id=x&seq=1", "/api/v1/reports.csv?run_id=x"],
)
def test_new_reads_require_auth(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize(
    "patch",
    [
        {},
        {"weights": None},
        {"weights": {"throughput": 1}},
        {
            "weights": {
                "throughput": 0.3,
                "delay": 0.25,
                "occupancy": 0.15,
                "conflicts": 0.2,
                "resource_idle": 0.2,
            }
        },
        {
            "weights": {
                "throughput": -0.1,
                "delay": 0.5,
                "occupancy": 0.3,
                "conflicts": 0.2,
                "resource_idle": 0.1,
            }
        },
        {"category_thresholds": {"normal_min": 50, "attention_min": 50}},
        {"category_thresholds": {"normal_min": 101, "attention_min": 40}},
        {"planner": {"time_limit_ms": 3001, "max_rollouts": 12}},
        {"planner": {"time_limit_ms": 99, "max_rollouts": 12}},
        {"planner": {"time_limit_ms": 3000, "max_rollouts": 0}},
        {"planner": {"time_limit_ms": True, "max_rollouts": 12}},
        {"config_version": 55},
    ],
)
def test_config_validation_rejects_without_any_state_or_revision_write(patch):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ConfigPatchInput.model_validate(
            {
                "request_id": "bdc183a5-5856-43ad-9d91-9c52d57ef0d3",
                "run_id": "run",
                "expected_input_revision": 1,
                "patch": patch,
            }
        )


def test_config_actual_version_replan_receipt_history_and_no_fake_forecast(client, login, test_settings):
    login("admin")
    advance(client, 900)
    before = client.get("/api/v1/snapshot").json()
    weights = {"throughput": 0.2, "delay": 0.2, "occupancy": 0.1, "conflicts": 0.2, "resource_idle": 0.3}
    body = {
        **envelope(client),
        "patch": {
            "weights": weights,
            "category_thresholds": {"normal_min": 85, "attention_min": 55},
            "planner": {"time_limit_ms": 3000, "max_rollouts": 12},
        },
    }
    response = client.patch("/api/v1/config", json=body)
    assert response.status_code == 200, response.text
    assert client.patch("/api/v1/config", json=body).json() == response.json()
    job_id = response.json()["result"]["replan_id"]
    detail = wait_result(client, job_id)
    assert detail["job"]["status"] == "succeeded"
    state = client.get("/api/v1/snapshot").json()
    config = client.get("/api/v1/config").json()
    assert config["config_version"] == state["config_version"] == response.json()["result"]["config_version"]
    assert state["config_version"] > before["config_version"]
    assert state["input_revision"] == before["input_revision"] + 1
    assert config["weights"] == weights
    assert state["efficiency"]["mode"] == "actual"
    assert all(
        p["forecast"]["mode"] == "forecast" and p["config_version"] == state["config_version"]
        for p in detail["plans"]
    )

    async def persisted():
        engine, sessions = database(test_settings.database_url.get_secret_value())
        async with sessions() as session:
            revision = await session.get(ConfigRevision, state["config_version"])
            assert revision.payload == config and revision.actor_id == "u-admin"
            job = await session.get(OptimizationRun, job_id)
            assert job.payload["inputs"]["config"]["weights"] == weights
        await engine.dispose()

    asyncio.run(persisted())
    assert any(
        e["kind"] == "config_changed"
        for e in client.get("/api/v1/history", params={"run_id": state["run_id"], "limit": 500}).json()[
            "items"
        ]
    )


def test_history_pagination_hash_reduction_readonly_and_paused_empty_window(client, login, test_settings):
    login()
    advance(client, 900)
    state = client.get("/api/v1/snapshot").json()
    run = state["run_id"]
    page = client.get("/api/v1/history", params={"run_id": run, "limit": 2}).json()
    assert len(page["items"]) == 2 and page["has_more"] and page["anchor_seq"] == 1
    next_page = client.get(
        "/api/v1/history", params={"run_id": run, "limit": 2, "from_seq": page["next_from_seq"]}
    ).json()
    assert set(e["seq"] for e in page["items"]).isdisjoint(e["seq"] for e in next_page["items"])
    samples = [
        client.get("/api/v1/history/snapshot", params={"run_id": run, "seq": seq}).json()
        for seq in [1, 2, state["event_seq"]]
    ]
    assert samples[0]["sim_time_s"] == 0 and samples[-1]["sim_time_s"] == 900
    future = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
    empty = client.get("/api/v1/history", params={"run_id": run, "from_wall_time": future}).json()
    assert empty["items"] == [] and empty["anchor_seq"] == state["event_seq"]

    async def durable():
        from digital_station.runtime import apply_effects

        engine, sessions = database(test_settings.database_url.get_secret_value())
        async with sessions() as session:
            events = list(
                await session.scalars(
                    select(DomainEvent).where(DomainEvent.run_id == run).order_by(DomainEvent.seq)
                )
            )
            replay = events[0].payload["state"]
            for event in events[1:]:
                replay = apply_effects(replay, event.payload["effects"])
            row = await session.get(RunState, run)
            assert canonical(samples[-1]) == canonical(replay) == canonical(row.payload)
            assert row.state_version == state["state_version"]
            assert (
                await session.scalar(
                    select(func.count()).select_from(OptimizationRun).where(OptimizationRun.run_id == run)
                )
                == 1
            )
        await engine.dispose()

    asyncio.run(durable())
    assert client.get("/api/v1/snapshot").json()["event_seq"] == state["event_seq"]


def test_csv_units_formula_raw_values_window_and_injection(client, login):
    login()
    advance(client, 900)
    run = client.get("/api/v1/snapshot").json()["run_id"]
    response = client.get("/api/v1/reports.csv", params={"run_id": run})
    assert response.status_code == 200 and "attachment" in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert {r["record_type"] for r in rows} >= {"summary", "factor", "plan", "timing"}
    assert all(r["run_id"] == run and r["formula_version"] == "efficiency-v1" for r in rows)
    factors = {r["metric"]: r for r in rows if r["record_type"] == "factor"}
    assert len(factors) == 5 and factors["delay"]["unit"] == "sim_seconds"
    assert factors["occupancy"]["unit"] == "ratio"
    assert json.loads(factors["occupancy"]["details"])["mode"] == "actual"
    assert float(factors["occupancy"]["value"]) > 0
    paused = datetime.now(UTC).isoformat()
    empty = client.get("/api/v1/reports.csv", params={"run_id": run, "from_wall_time": paused}).text
    assert next(r for r in csv.DictReader(io.StringIO(empty)) if r["metric"] == "score")["value"] == ""
    assert safe_cell("=HYPERLINK(1)").startswith("'")
    assert safe_cell("\t@bad").startswith("'") and safe_cell(-3) == -3


@pytest.mark.parametrize("path", ["/api/v1/history", "/api/v1/history/snapshot", "/api/v1/reports.csv"])
def test_unknown_history_run_404_and_invalid_window(client, login, path):
    login("viewer")
    params = {"run_id": "missing", "seq": 1} if path.endswith("snapshot") else {"run_id": "missing"}
    assert client.get(path, params=params).status_code == 404
    if not path.endswith("snapshot"):
        params = {
            "run_id": client.get("/api/v1/snapshot").json()["run_id"],
            "from_wall_time": "2026-01-02T00:00:00Z",
            "to_wall_time": "2026-01-01T00:00:00Z",
        }
        assert client.get(path, params=params).status_code == 422
