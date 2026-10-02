import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from digital_station.contracts import State
from digital_station.db import RunState, Scenario, database
from digital_station.http_contracts import ExplanationAvailable, HealthLive, HealthReady, MetricsResponse

ROOT = Path(__file__).resolve().parents[1]


def test_frozen_contract_matches_actual_backend_docs_and_captured_fixtures(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    result = script("verify_backend_contract").audit()
    assert result["status"] == "passed" and result["endpoint_count"] == 25
    assert result["frozen_hashes_checked"] and result["typed_http_responses"]
    assert len(result["captured_fixtures_checked"]) == 22


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_documented_setup_is_idempotent_and_never_resets(test_settings, prepared_run, monkeypatch):
    module = script("setup_backend")
    monkeypatch.setattr(module, "Settings", lambda: test_settings)

    async def verify():
        engine, sessions = database(test_settings.database_url.get_secret_value())
        async with sessions() as session:
            before = (await session.get(RunState, prepared_run)).payload
        first = await module.setup()
        second = await module.setup()
        assert first == second and first["run_id"] == prepared_run and not first["reset"]
        assert first["scenarios"] == ["demo_main_v1", "manual-control-v1"]
        async with sessions() as session:
            assert (await session.get(RunState, prepared_run)).payload == before
            manual = await session.get(Scenario, ("manual-control-v1", 1))
            state = State.model_validate(manual.payload["initial_state"])
            assert len(state.trains) == 7 and len(state.tracks) == 12
            assert (
                next(o for o in state.operations if o.id == "op-T1-inspection").assigned_user_id
                == "u-operator"
            )
        await engine.dispose()

    asyncio.run(verify())


def test_setup_refuses_live_writer_without_changing_data(client, test_settings, monkeypatch):
    module = script("setup_backend")
    monkeypatch.setattr(module, "Settings", lambda: test_settings)
    before = client.app.state.actor.state.model_dump()
    with pytest.raises(RuntimeError, match="Stop API before setup"):
        asyncio.run(module.setup())
    assert client.app.state.actor.state.model_dump() == before


def test_actual_catalog_and_transport_schemas(client, login):
    login("admin")
    HealthLive.model_validate(client.get("/health/live").json())
    HealthReady.model_validate(client.get("/health/ready").json())
    MetricsResponse.model_validate(client.get("/api/v1/metrics").json())
    state = client.get("/api/v1/snapshot").json()
    ExplanationAvailable.model_validate(
        client.get(f"/api/v1/replans/{state['last_replan']['id']}/explanation").json()
    )
    catalog = client.get("/api/v1/scenarios").json()["items"]
    assert [row["id"] for row in catalog] == ["demo_main_v1", "manual-control-v1"]
    assert all(row["train_count"] == 7 and row["horizon_sim_s"] == 7200 for row in catalog)
    assert len(client.get("/api/v1/snapshot").json()["trains"]) == 7


def test_benchmark_is_paired_and_honest():
    raw = json.loads((ROOT / "artifacts/benchmark/raw.json").read_text())
    assert len(raw) == 12
    for case in {item["row"]["scenario"] for item in raw}:
        pair = [item for item in raw if item["row"]["scenario"] == case]
        assert pair[0]["inputs"] == pair[1]["inputs"]
        assert pair[0]["row"]["input_sha256"] == pair[1]["row"]["input_sha256"]
        for item in pair:
            assert item["validator"]["passed"] and item["row"]["unresolved_conflicts"] == 0
            assert item["row"]["window_start_sim_s"] == 0 and item["row"]["window_end_sim_s"] == 7200
            assert item["row"]["departed_trains"] == 7
            assert item["row"]["efficiency_index"] == item["actual"]["score"]
            state = State.model_validate(item["final_state"])
            assert len({wid for g in state.wagon_groups for wid in g.wagon_ids}) == 60
            base = State.model_validate(item["inputs"]["state"])
            candidate = {o["id"]: o for o in item["result"]["candidates"][0]["operations"]}
            for op in base.operations:
                if op.status in {"running", "completed"}:
                    assert candidate[op.id] == op.model_dump(mode="json")
    audit = json.loads((ROOT / "artifacts/benchmark/csv-audit.json").read_text())
    assert audit["heuristic_lower_actual_index"] == ["train_delay", "burst5", "burst10"]
    assert audit["independent_validation_passed"] == 12


def test_live_timing_pairs_use_same_physical_input():
    evidence = json.loads((ROOT / "artifacts/benchmark/live-timings.json").read_text())
    rows = evidence
    assert len(rows) == 12
    for case in {r["scenario"] for r in rows}:
        pair = [r for r in rows if r["scenario"] == case]
        assert pair[0]["physical_input_sha256"] == pair[1]["physical_input_sha256"]
        assert all(r["detail"]["job"]["status"] == "succeeded" for r in pair)
        assert all(all(p["validator"]["passed"] for p in r["detail"]["plans"]) for r in pair)


def test_fresh_manual_run_plays_initial_arrival_without_control_search_barrier(client, login):
    from test_planning_api import envelope, wait_result

    login("admin")
    reply = client.post(
        "/api/v1/runs", json={**envelope(client), "scenario_id": "manual-control-v1", "seed": 42}
    )
    assert reply.status_code == 201
    # Wait for the actual asynchronous init result, not just the REST receipt.
    import time

    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        state = client.get("/api/v1/snapshot").json()
        if state["last_replan"] and state["last_replan"]["status"] == "no_feasible_plan":
            break
        time.sleep(0.02)
    assert "MANUAL_CONFIRMATION_REQUIRED" in state["last_replan"]["outcome_reason_codes"]
    job_id = state["last_replan"]["id"]
    for extra in ({"action": "set_speed", "speed": 10}, {"action": "play"}):
        assert (
            client.post("/api/v1/simulation/control", json={**envelope(client), **extra}).status_code == 200
        )
    state = client.get("/api/v1/snapshot").json()
    arrival = next(o for o in state["operations"] if o["id"] == "op-T1-arrival")
    assert arrival["status"] == "running" and arrival["actual_start_sim_s"] == 0
    assert state["last_replan"]["id"] == job_id and not state["conflicts"]
    assert (
        client.post("/api/v1/simulation/control", json={**envelope(client), "action": "pause"}).status_code
        == 200
    )
    client.portal.call(client.app.state.actor.advance, 360)
    state = client.get("/api/v1/snapshot").json()
    manual = next(o for o in state["operations"] if o["id"] == "op-T1-inspection")
    assert manual["status"] == "running" and manual["can_complete"]
    assert manual["assigned_user_id"] == "u-operator"
    reply = client.post(f"/api/v1/operations/{manual['id']}/complete", json=envelope(client))
    assert reply.status_code == 200
    detail = wait_result(client, reply.json()["result"]["replan_id"])
    assert detail["job"]["status"] == "succeeded" and all(p["validator"]["passed"] for p in detail["plans"])
