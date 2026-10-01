import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from digital_station.api import create_app
from digital_station.auth import COOKIE_NAME, token_hash
from digital_station.contracts import State
from digital_station.db import AuthSession, RunState, database
from digital_station.runtime import MIGRATION


def test_health_and_smoke_assets(client):
    assert client.get("/health/live").json()["status"] == "alive"
    ready = client.get("/health/ready")
    assert ready.status_code == 200 and ready.json()["checks"]["schema"] == "ok"
    assert ready.json()["capabilities"]["simulation"] is True
    assert ready.json()["capabilities"]["sse"] is True
    assert ready.json()["capabilities"]["optimization"] is True
    for path in (
        "/tech/smoke",
        "/tech/smoke.js",
        "/tech/smoke.css",
        "/tech/station",
        "/tech/station.js",
        "/tech/station.css",
        "/tech/railway.js",
        "/docs",
    ):
        assert client.get(path).status_code == 200
    assert client.get("/tech", follow_redirects=False).headers["location"] == "/tech/station"
    assert client.get("/tech/debug", follow_redirects=False).headers["location"] == "/tech/smoke"


@pytest.mark.parametrize(
    "path", ["/api/v1/auth/me", "/api/v1/snapshot", "/api/v1/config", "/api/v1/scenarios", "/api/v1/stream"]
)
def test_authentication_required(client, path):
    response = client.get(path)
    assert response.status_code == 401 and response.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize("username", ["dispatcher", "unknown"])
def test_bad_login(client, username):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": "invalid-password"})
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Неверное имя пользователя или пароль"


@pytest.mark.parametrize("role", ["viewer", "operator", "dispatcher", "admin"])
def test_roles_me_snapshot_and_cookie(client, login, role):
    response = login(role)
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "max-age=43200" in cookie
    assert "password" not in response.text and "token" not in response.text
    assert client.get("/api/v1/auth/me").json()["user"]["role"] == role
    state = State.model_validate(client.get("/api/v1/snapshot").json())
    assert state.run_id.startswith("run-") and len(state.trains) == 7 and len(state.tracks) == 12
    assert state.mode == "paused" and state.sim_time_s == 0


def test_logout_revokes_real_database_session(client, login):
    login()
    stolen = client.cookies.get(COOKIE_NAME)
    cookie = next(c for c in client.cookies.jar if c.name == COOKIE_NAME)
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    client.cookies.set(COOKIE_NAME, stolen, domain=cookie.domain, path=cookie.path)
    assert client.get("/api/v1/snapshot").status_code == 401
    login()
    assert client.get("/api/v1/auth/me").status_code == 200


@pytest.mark.parametrize(
    "role,status", [("viewer", 403), ("operator", 403), ("dispatcher", 200), ("admin", 200)]
)
def test_control_permissions_and_real_execution(client, login, role, status):
    login(role)
    before = client.get("/api/v1/snapshot").json()
    command_id = str(uuid4())
    response = client.post(
        "/api/v1/simulation/control",
        json={
            "request_id": command_id,
            "run_id": before["run_id"],
            "expected_input_revision": before["input_revision"],
            "action": "play",
        },
    )
    assert response.status_code == status
    if status == 403:
        assert response.json()["error"]["request_id"] == command_id
    else:
        assert response.json()["request_id"] == command_id
    after = client.get("/api/v1/snapshot").json()
    if status == 200:
        assert after["mode"] == "running"
        assert after["input_revision"] == before["input_revision"] + 1
        first = next(op for op in after["operations"] if op["id"] == "op-T1-arrival")
        assert first["status"] == "running" and first["actual_start_sim_s"] == 0
        return
    for field in (
        "run_id",
        "event_seq",
        "state_version",
        "input_revision",
        "mode",
        "sim_time_s",
        "operations",
    ):
        assert before[field] == after[field]


def test_error_envelopes_and_origin(client, login):
    response = client.post("/api/v1/auth/login", json={"username": "dispatcher"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["details"]["fields"][0]["path"] == "password"
    assert client.get("/not-found").json()["error"]["code"] == "NOT_FOUND"
    response = client.post("/api/v1/auth/logout", headers={"Origin": "https://untrusted.invalid"})
    assert response.status_code == 403
    login()
    assert client.get("/api/v1/config").json()["planner"]["time_limit_ms"] == 3000
    assert client.get("/api/v1/scenarios").json()["items"][0]["train_count"] == 7


def test_repeated_reads_and_restart_preserve_persisted_state(test_settings, prepared_run):
    with TestClient(create_app(test_settings)) as first_client:
        assert (
            first_client.post(
                "/api/v1/auth/login",
                json={
                    "username": "dispatcher",
                    "password": test_settings.demo_dispatcher_password.get_secret_value(),
                },
            ).status_code
            == 200
        )
        first = first_client.get("/api/v1/snapshot").json()
        token = first_client.cookies.get(COOKIE_NAME)
    with TestClient(create_app(test_settings)) as restarted:
        restarted.cookies.set(COOKIE_NAME, token)
        second = restarted.get("/api/v1/snapshot").json()
    for key in ("event_seq", "state_version"):
        assert second[key] > first[key]  # Recovery plus independently checked init replan.
    assert second["input_revision"] == first["input_revision"] + 1
    for key in ("run_id", "sim_time_s", "mode", "operations", "tracks", "wagon_groups", "resources"):
        assert first[key] == second[key]


def test_session_expiry(client, login, test_settings):
    login()
    token = client.cookies.get(COOKIE_NAME)

    async def expire():
        engine, sessions = database(test_settings.database_url.get_secret_value())
        async with sessions() as session:
            auth = await session.get(AuthSession, token_hash(token))
            auth.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        await engine.dispose()

    asyncio.run(expire())
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401 and response.json()["error"]["code"] == "SESSION_EXPIRED"


def test_migration_jsonb_and_snapshot_uses_database(client, login, test_settings):
    login()
    before = client.get("/api/v1/snapshot").json()

    async def inspect_and_change(restore=False):
        engine, sessions = database(test_settings.database_url.get_secret_value())
        async with sessions() as session:
            assert await session.scalar(text("SELECT version_num FROM alembic_version")) == MIGRATION
            assert (
                await session.scalar(
                    text(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_name='run_state' AND column_name='payload'"
                    )
                )
                == "jsonb"
            )
            row = await session.scalar(select(RunState).where(RunState.run_id == before["run_id"]))
            payload = dict(row.payload)
            payload["station"] = dict(payload["station"])
            payload["station"]["name"] = before["station"]["name"] if restore else "DB-source-proof"
            row.payload = payload
            await session.commit()
        await engine.dispose()

    asyncio.run(inspect_and_change())
    try:
        assert client.get("/api/v1/snapshot").json()["station"]["name"] == "DB-source-proof"
    finally:
        asyncio.run(inspect_and_change(restore=True))


def test_openapi_matches_snapshot_model(client):
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/api/v1/snapshot"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"].endswith("/State")
    enum = schema["components"]["schemas"]["Train"]["properties"]["type"]["enum"]
    assert enum == ["FREIGHT", "PASSENGER", "SERVICE", "OTHER"]


@pytest.mark.parametrize(
    "patch",
    [
        {"action": "set_speed"},
        {"action": "play", "speed": 5},
        {"action": "set_speed", "speed": 2},
        {"request_id": "not-a-uuid"},
    ],
)
def test_control_validation_does_not_change_state(client, login, patch):
    login()
    before = client.get("/api/v1/snapshot").json()
    body = {
        "request_id": str(uuid4()),
        "run_id": before["run_id"],
        "expected_input_revision": before["input_revision"],
        "action": "play",
        **patch,
    }
    response = client.post("/api/v1/simulation/control", json=body)
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"
    after = client.get("/api/v1/snapshot").json()
    for field in ("sim_time_s", "input_revision", "state_version", "event_seq", "mode", "operations"):
        assert before[field] == after[field]
