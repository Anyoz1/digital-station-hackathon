from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_planning_api import envelope, wait_result
from test_runtime import actor as actor
from test_runtime import control, receive

from digital_station.api import create_app
from digital_station.contracts import RenderTelemetry
from digital_station.db import Run, RunState, TelemetryObservation
from digital_station.normalization import normalize
from digital_station.runtime import frame
from digital_station.telemetry import Measurements, now_ms, stats


def test_percentiles_are_nearest_rank_and_empty_not_zero():
    assert stats([]) == dict(sample_count=0, p50_ms=None, p95_ms=None, max_ms=None)
    assert stats(range(1, 21)) == dict(sample_count=20, p50_ms=10, p95_ms=19, max_ms=20)


def test_measurements_count_missing_invalid_hidden_duplicate_catchup_and_exceedance():
    from digital_station.scenario import make_initial_state

    state = make_initial_state()
    metrics = Measurements(state.run_id)
    client_id = str(uuid4())
    now = now_ms()
    ingress = datetime.fromtimestamp((now - 600) / 1000, UTC).isoformat()
    for seq in range(1, 7):
        state.event_seq = seq
        item = frame(state, "tick", ingested_at=ingress)
        metrics.published(item)
        metrics.delivered(client_id, item, item.emitted - 1)

    def report(seq, **extra):
        body = RenderTelemetry(
            run_id=state.run_id,
            event_seq=seq,
            client_id=client_id,
            received_client_ms=now - 20,
            rendered_client_ms=now,
            offset_ms=0,
            uncertainty_ms=5,
            visible=True,
        )
        return body.model_copy(update=extra)

    body = report(1)
    metrics.report(body)
    metrics.report(body)
    metrics.report(report(2, uncertainty_ms=51))
    metrics.report(report(3, rendered_client_ms=now - 700))
    metrics.report(report(4, visible=False))
    metrics.clients[client_id]["events"][5]["delivered_ms"] -= 2100
    # Missing seq5 is counted independently of successful reports.
    result = metrics.ui_stats()[0]
    assert result["latency_upper"]["sample_count"] == 1
    assert result["latency_exceedances"] == 1
    assert result["invalid_samples"] == 2 and result["hidden_samples"] == 1
    assert result["unreported_events"] == 1
    other = str(uuid4())
    metrics.delivered(other, item, item.emitted + 1)  # catch-up must not create denominator
    assert other not in metrics.clients
    heartbeat = frame(state)
    metrics.delivered(other, heartbeat, 0)
    assert other not in metrics.clients
    metrics.report(report(999))
    assert metrics.ui_stats()[0]["invalid_samples"] == 3
    metrics.reset("another-run")
    assert metrics.ui_stats() == [] and metrics.published_events == 0


@pytest.mark.parametrize("role", ["viewer", "operator", "dispatcher", "admin"])
def test_metrics_clock_read_auth_and_new_run_permissions(client, login, role):
    login(role)
    before = client.get("/api/v1/snapshot").json()
    clock = client.get("/api/v1/time").json()
    assert clock["server_sent_ms"] >= clock["server_received_ms"]
    metrics = client.get("/api/v1/metrics").json()
    assert metrics["run_id"] == before["run_id"] and metrics["ui_render"] == []
    assert metrics["replans"]["succeeded"] == 1
    reply = client.post("/api/v1/runs", json={**envelope(client), "scenario_id": "demo_main_v1", "seed": 43})
    assert reply.status_code == (201 if role == "admin" else 403)


@pytest.mark.parametrize(
    "path", ["/api/v1/time", "/api/v1/metrics", "/api/v1/runs", "/api/v1/telemetry/ui-render"]
)
def test_h18_endpoints_unauthenticated(client, path):
    reply = client.post(path, json={}) if path.endswith(("runs", "ui-render")) else client.get(path)
    assert reply.status_code == 401


def test_telemetry_never_changes_domain_and_schema_errors_are_explicit(client, login):
    login("viewer")
    before = client.app.state.actor.state.model_dump(mode="json")
    client_id = str(uuid4())
    item = frame(client.app.state.actor.state, "tick")
    metrics = client.app.state.actor.measurements
    metrics.published(item)
    metrics.delivered(client_id, item, item.emitted - 1)
    body = dict(
        run_id=before["run_id"],
        event_seq=before["event_seq"],
        client_id=client_id,
        received_client_ms=now_ms(),
        rendered_client_ms=now_ms() + 20,
        offset_ms=0,
        uncertainty_ms=2,
        visible=True,
    )
    assert client.post("/api/v1/telemetry/ui-render", json=body).status_code == 204
    assert client.post("/api/v1/telemetry/ui-render", json=body).status_code == 204
    assert metrics.ui_stats()[0]["latency_upper"]["sample_count"] == 1
    assert client.post("/api/v1/telemetry/ui-render", json={**body, "visible": "yes"}).status_code == 422
    assert metrics.ui_stats()[0]["invalid_samples"] == 1
    assert client.app.state.actor.state.model_dump(mode="json") == before


def test_new_run_receipt_reset_history_config_seed_and_stale_certificate(client, login):
    login("admin")
    old = client.get("/api/v1/snapshot").json()
    old_saved = client.get(
        "/api/v1/history/snapshot", params={"run_id": old["run_id"], "seq": old["event_seq"]}
    ).json()
    queue = client.portal.call(client.app.state.actor.subscribe, f"{old['run_id']}:{old['event_seq']}")
    client.portal.call(receive, queue)
    body = {**envelope(client), "scenario_id": "demo_main_v1", "seed": 123}
    reply = client.post("/api/v1/runs", json=body)
    assert reply.status_code == 201, reply.text
    receipt = reply.json()
    new = client.get("/api/v1/snapshot").json()
    assert new["run_id"] == receipt["result"]["new_run_id"] != old["run_id"]
    assert new["config_version"] == old["config_version"]
    assert new["sim_time_s"] == 0 and new["mode"] == "paused"
    assert len(new["tracks"]) == 12 and len(new["trains"]) == 7
    seen_reset = False
    for _ in range(8):
        value = client.portal.call(receive, queue)
        if value.get("reason") == "run_changed":
            assert value["state"]["run_id"] == new["run_id"]
            seen_reset = True
            break
    assert seen_reset
    client.app.state.actor.unsubscribe(queue)
    assert client.post("/api/v1/runs", json=body).json() == receipt
    assert client.post("/api/v1/runs", json={**body, "seed": 124}).status_code == 409
    assert (
        client.post("/api/v1/runs", json={**body, "request_id": str(uuid4())}).json()["error"]["code"]
        == "RUN_MISMATCH"
    )
    detail = wait_result(client, new["last_replan"]["id"])
    assert detail["job"]["status"] == "succeeded" and len(detail["plans"]) == 2
    assert (
        client.get(
            "/api/v1/history/snapshot", params={"run_id": old["run_id"], "seq": old["event_seq"]}
        ).json()
        == old_saved
    )
    assert client.get("/api/v1/reports.csv", params={"run_id": old["run_id"]}).status_code == 200
    assert (
        client.post(f"/api/v1/plans/{old['active_plan_id']}/apply", json=envelope(client)).status_code == 409
    )

    async def stored():
        async with client.app.state.actor.sessions() as session:
            assert (await session.get(Run, old["run_id"])).status == "closed"
            assert (await session.get(Run, new["run_id"])).seed == 123

    client.portal.call(stored)


@pytest.mark.parametrize("seed", [-1, 2147483648, True, 1.5, "42"])
def test_new_run_seed_validation(client, login, seed):
    login("admin")
    before = client.get("/api/v1/snapshot").json()
    reply = client.post(
        "/api/v1/runs", json={**envelope(client), "scenario_id": "demo_main_v1", "seed": seed}
    )
    assert reply.status_code == 422
    assert client.get("/api/v1/snapshot").json()["run_id"] == before["run_id"]


def test_unknown_scenario_revision_and_origin_fail_without_reset(client, login):
    login("admin")
    before = client.get("/api/v1/snapshot").json()
    body = {**envelope(client), "scenario_id": "unknown", "seed": 42}
    assert client.post("/api/v1/runs", json=body).status_code == 422
    assert client.post("/api/v1/runs", json={**body, "expected_input_revision": 0}).status_code == 409
    assert (
        client.post("/api/v1/runs", json=body, headers={"Origin": "http://evil.invalid"}).status_code == 403
    )
    assert client.get("/api/v1/snapshot").json()["run_id"] == before["run_id"]
    assert (
        "access-control-allow-origin"
        not in client.get("/api/v1/snapshot", headers={"Origin": "http://evil.invalid"}).headers
    )


@pytest.mark.asyncio
async def test_noisy_normalization_durable_dedup_order_quarantine_and_no_physical_effect(actor):
    await control(actor, "play")
    await control(actor, "pause")
    before = actor.state.model_dump(mode="json")
    op = next(o for o in actor.state.operations if o.id == "op-T1-arrival")
    route = actor.state.trains[0].location.route_id

    def body(seq, value, **patch):
        return dict(
            source_event_id=f"noisy-{uuid4()}",
            source_id="noisy-fixture",
            source_seq=seq,
            operation_id=op.id,
            route_id=route,
            observed_at=datetime.now(UTC).isoformat(),
            observed_progress=value,
            **patch,
        )

    first = body(1, 0.2)
    assert (await normalize(actor.sessions, actor.state, first))["filtered_progress"] == 0.2
    assert await normalize(actor.sessions, actor.state, first) == await normalize(
        actor.sessions, actor.state, first
    )
    assert (await normalize(actor.sessions, actor.state, {**first, "observed_progress": 0.9}))[
        "reason"
    ] == "DUPLICATE_MISMATCH"
    assert (await normalize(actor.sessions, actor.state, body(2, 0.3)))["filtered_progress"] == 0.25
    assert (await normalize(actor.sessions, actor.state, body(3, 0.95)))["filtered_progress"] == 0.3
    assert (await normalize(actor.sessions, actor.state, body(2, 0.1)))["reason"] == "OUT_OF_ORDER"
    stale = body(4, 0.4)
    stale["observed_at"] = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    assert (await normalize(actor.sessions, actor.state, stale))["reason"] == "STALE_SAMPLE"
    future = body(4, 0.4)
    future["observed_at"] = (datetime.now(UTC) + timedelta(seconds=10)).isoformat()
    assert (await normalize(actor.sessions, actor.state, future))["reason"] == "FUTURE_SAMPLE"
    wrong = body(4, 0.4)
    wrong["operation_id"] = "op-no-such"
    assert (await normalize(actor.sessions, actor.state, wrong))["reason"] == "NOT_CURRENT_ROUTE"
    assert (await normalize(actor.sessions, actor.state, {"occupancy": False}))["reason"] == "INVALID_SCHEMA"
    invalid = body(4, float("nan"))
    assert (await normalize(actor.sessions, actor.state, invalid))["reason"] == "INVALID_SCHEMA"
    # Source A cannot overwrite factual engine progress, incident/occupancy or seq.
    assert actor.state.model_dump(mode="json") == before
    async with actor.sessions() as session:
        rows = list(
            await session.scalars(
                select(TelemetryObservation).where(
                    TelemetryObservation.run_id == actor.run_id,
                    TelemetryObservation.source_id == "noisy-fixture",
                )
            )
        )
        assert sum(r.status == "accepted" for r in rows) == 3
        assert any(r.payload["result"]["reason"] == "DUPLICATE_MISMATCH" for r in rows)
        assert (await session.get(RunState, actor.run_id)).payload == before


def test_new_run_restart_preserves_current_old_history_config_and_receipt(test_settings, prepared_run):
    with TestClient(create_app(test_settings)) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": test_settings.demo_admin_password.get_secret_value()},
        )
        assert login.status_code == 200
        old = client.get("/api/v1/snapshot").json()
        body = {**envelope(client), "scenario_id": "demo_main_v1", "seed": 99}
        receipt = client.post("/api/v1/runs", json=body).json()
        new = client.get("/api/v1/snapshot").json()
        wait_result(client, new["last_replan"]["id"])
        cookies = client.cookies
    with TestClient(create_app(test_settings)) as client:
        client.cookies = cookies
        current = client.get("/api/v1/snapshot").json()
        assert current["run_id"] == new["run_id"]
        assert current["config_version"] == old["config_version"]
        assert client.post("/api/v1/runs", json=body).json() == receipt
        assert (
            client.get(
                "/api/v1/history/snapshot", params={"run_id": old["run_id"], "seq": old["event_seq"]}
            ).status_code
            == 200
        )
