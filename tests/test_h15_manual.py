import asyncio

import pytest
from fastapi.testclient import TestClient
from test_h15_api import advance
from test_planning_api import envelope, wait_result

from digital_station.api import create_app
from digital_station.bootstrap import bootstrap, create_initial_run, ensure_manual_scenario
from digital_station.db import database


@pytest.fixture
def manual_client(test_settings):
    async def prepare():
        engine, sessions = database(test_settings.database_url.get_secret_value())
        await bootstrap(sessions, test_settings)
        async with sessions() as session, session.begin():
            await ensure_manual_scenario(session)
            await create_initial_run(session, "manual-control-v1")
        await engine.dispose()

    asyncio.run(prepare())
    with TestClient(create_app(test_settings)) as client:
        yield client


def authenticate(client, settings, role):
    reply = client.post(
        "/api/v1/auth/login",
        json={"username": role, "password": getattr(settings, f"demo_{role}_password").get_secret_value()},
    )
    assert reply.status_code == 200


def operation(client):
    state = client.get("/api/v1/snapshot").json()
    return state, next(o for o in state["operations"] if o["id"] == "op-T1-inspection")


def test_manual_minimum_assignment_locks_confirmation_history_receipt_and_dag(manual_client, test_settings):
    client = manual_client
    authenticate(client, test_settings, "operator")
    state, op = operation(client)
    assert state["scenario_id"] == "manual-control-v1"
    assert state["last_replan"]["status"] == "no_feasible_plan"
    assert "MANUAL_CONFIRMATION_REQUIRED" in state["last_replan"]["outcome_reason_codes"]
    assert client.post(f"/api/v1/operations/{op['id']}/complete", json=envelope(client)).status_code == 409
    advance(client, 359)
    state, op = operation(client)
    assert op["status"] == "running" and not op["can_complete"]
    assert client.post(f"/api/v1/operations/{op['id']}/complete", json=envelope(client)).status_code == 409
    # A human may finish AFTER min duration. The engine must keep the resource reserved.
    advance(client, 390)
    state, op = operation(client)
    assert op["can_complete"] and op["status"] == "running" and op["progress"] == 1
    assert any(r["active_operation_id"] == op["id"] and r["status"] == "busy" for r in state["resources"])
    body = envelope(client)
    response = client.post(f"/api/v1/operations/{op['id']}/complete", json=body)
    assert response.status_code == 200, response.text
    assert client.post(f"/api/v1/operations/{op['id']}/complete", json=body).json() == response.json()
    confirmed, _ = operation(client)
    assert next(t for t in confirmed["trains"] if t["id"] == "T1")["status"] == "ready_departure"
    # Regression: blocked departure must not make future physical prefix differ from validator.
    from digital_station.contracts import State
    from digital_station.simulator import Simulator
    from digital_station.validator import expected_prefix, physical_signature

    initial = client.app.state.actor.coordinator.initial
    engine = Simulator(State.model_validate(confirmed), initial)
    engine.launch_barrier = True
    list(engine.advance(391, allow_starts=False))
    assert physical_signature(engine.state.model_dump(mode="json")) == expected_prefix(
        State.model_validate(confirmed), initial, 391
    )
    result = wait_result(client, response.json()["result"]["replan_id"])
    assert result["job"]["status"] == "succeeded"
    assert all(p["validator"]["passed"] for p in result["plans"])
    state, completed = operation(client)
    assert completed["actual_end_sim_s"] == 390 and completed["status"] == "completed"
    assert not completed["can_complete"]
    events = client.get("/api/v1/history", params={"run_id": state["run_id"], "limit": 500}).json()["items"]
    confirmations = [e for e in events if e["actor_user_id"] == "u-operator" and op["id"] in e["entity_ids"]]
    assert confirmations
    replay = client.get(
        "/api/v1/history/snapshot", params={"run_id": state["run_id"], "seq": confirmations[-1]["seq"]}
    ).json()
    assert next(o for o in replay["operations"] if o["id"] == op["id"])["status"] == "completed"
    advance(client, 900)
    state, _ = operation(client)
    assert next(t for t in state["trains"] if t["id"] == "T1")["status"] == "departed"


@pytest.mark.parametrize("role", ["viewer", "dispatcher", "operator", "admin"])
def test_manual_permission_matrix_and_movements_cannot_be_confirmed(manual_client, test_settings, role):
    client = manual_client
    authenticate(client, test_settings, role)
    advance(client, 360)
    # Unassigned auto inspection is foreign to operator. Administrator still cannot complete auto.
    response = client.post("/api/v1/operations/op-T2-inspection/complete", json=envelope(client))
    assert response.status_code == (409 if role == "admin" else 403)
    movement = client.post("/api/v1/operations/op-T1-departure/complete", json=envelope(client))
    assert movement.status_code == (409 if role == "admin" else 403)
    own = client.post("/api/v1/operations/op-T1-inspection/complete", json=envelope(client))
    assert own.status_code == (200 if role in {"operator", "admin"} else 403)
