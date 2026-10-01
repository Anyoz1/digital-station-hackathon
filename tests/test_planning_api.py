import asyncio
import json
import os
import signal
import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_planner import inputs

from digital_station.contracts import ReplanDetail, ReplanInput, SimulationCommand
from digital_station.db import OptimizationRun, PlanRecord, database
from digital_station.runtime import StationActor
from digital_station.worker import PlannerWorker


def envelope(client):
    state = client.get("/api/v1/snapshot").json()
    return dict(
        request_id=str(uuid4()), run_id=state["run_id"], expected_input_revision=state["input_revision"]
    )


def wait_result(client, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        reply = client.get(f"/api/v1/replans/{job_id}")
        assert reply.status_code == 200, reply.text
        detail = reply.json()
        if detail["job"]["status"] not in {"queued", "running"}:
            ReplanDetail.model_validate(detail)
            return detail
        time.sleep(0.025)
    pytest.fail("Planner did not terminate by deadline")


def test_real_replan_two_candidates_durable_detail_and_idempotent_receipt(client, login, test_settings):
    login()
    body = {**envelope(client), "reason": "manual"}
    reply = client.post("/api/v1/replans", json=body)
    assert reply.status_code == 202
    assert client.post("/api/v1/replans", json=body).json() == reply.json()
    job_id = reply.json()["result"]["replan_id"]
    detail = wait_result(client, job_id)
    assert detail["job"]["status"] == "succeeded" and len(detail["plans"]) == 2
    assert all(p["validator"]["passed"] and p["validity"] == "feasible" for p in detail["plans"])
    assert detail["job"]["elapsed_ms"] < 5000 and detail["job"]["compute_ms"] < 3000
    assert detail["plans"][0]["operations"] != detail["plans"][1]["operations"]
    state = client.get("/api/v1/snapshot").json()
    assert state["active_plan_id"] == detail["job"]["applied_plan_id"]

    async def database_assertions():
        engine, sessions = database(test_settings.database_url.get_secret_value())
        async with sessions() as session:
            record = await session.get(OptimizationRun, job_id)
            assert record.status == "succeeded" and record.payload["result"]["worker_pid"] != os.getpid()
            result = record.payload["result"]
            assert {a["input_digest"] for a in result["attempts"]} == {result["input_digest"]}
            plans = list(
                await session.scalars(select(PlanRecord).where(PlanRecord.optimization_run_id == job_id))
            )
            assert len(plans) == 2
        await engine.dispose()

    asyncio.run(database_assertions())


def test_paused_sibling_apply_preserves_facts_and_path_is_in_idempotency_hash(client, login):
    login()
    reply = client.post("/api/v1/replans", json={**envelope(client), "reason": "manual"})
    detail = wait_result(client, reply.json()["result"]["replan_id"])
    state = client.get("/api/v1/snapshot").json()
    sibling = next(p for p in detail["plans"] if p["id"] != state["active_plan_id"])
    body = envelope(client)
    response = client.post(f"/api/v1/plans/{sibling['id']}/apply", json=body)
    assert response.status_code == 200, response.text
    assert client.post(f"/api/v1/plans/{sibling['id']}/apply", json=body).json() == response.json()
    changed_path = client.post(f"/api/v1/plans/{state['active_plan_id']}/apply", json=body)
    assert changed_path.status_code == 409 and changed_path.json()["error"]["code"] == "IDEMPOTENCY_MISMATCH"
    after = client.get("/api/v1/snapshot").json()
    assert after["active_plan_id"] == sibling["id"] and after["input_revision"] == state["input_revision"] + 1
    assert after["wagon_groups"] == state["wagon_groups"] and after["resources"] == state["resources"]
    assert [t["location"] for t in after["trains"]] == [t["location"] for t in state["trains"]]
    assert not any(p["can_apply"] for p in after["plans"])
    stale = client.post(f"/api/v1/plans/{state['active_plan_id']}/apply", json=envelope(client))
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "PLAN_STALE"


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_planner_write_roles_enforced_on_server(client, login, role):
    login(role)
    assert client.post("/api/v1/replans", json={**envelope(client), "reason": "manual"}).status_code == 403
    assert client.post("/api/v1/plans/nonexistent/apply", json=envelope(client)).status_code == 403


@pytest.mark.asyncio
async def test_real_worker_does_not_block_actor_sse_or_started_prefix(test_settings, prepared_run):
    engine, sessions = database(test_settings.database_url.get_secret_value())
    actor = StationActor(engine, sessions, prepared_run)
    await actor.start()
    try:
        queue = await actor.subscribe(f"{actor.run_id}:{actor.state.event_seq}")
        await asyncio.wait_for(queue.get(), 5)
        command = SimulationCommand(
            request_id=str(uuid4()),
            run_id=actor.run_id,
            expected_input_revision=actor.state.input_revision,
            action="play",
        )
        await actor.call("control", ("u-dispatcher", command, datetime.now(UTC).isoformat()))
        assert actor.state.mode == "running"
        started = time.monotonic()
        while actor.state.sim_time_s < 1 and time.monotonic() - started < 2:
            await asyncio.wait_for(queue.get(), 5)
        assert actor.state.sim_time_s >= 1, actor.state.mode
        before = actor.state.operations[0].model_copy(deep=True)
        body = ReplanInput(
            request_id=str(uuid4()),
            run_id=actor.run_id,
            expected_input_revision=actor.state.input_revision,
            reason="manual",
        )
        receipt = await actor.call("replan", ("u-dispatcher", body, datetime.now(UTC).isoformat()))
        job_id = receipt["result"]["replan_id"]
        statuses = []
        received = []
        while True:
            frame = await asyncio.wait_for(queue.get(), 5)
            payload = json.loads(frame.wire.split("\ndata: ")[1])
            state = payload["state"]
            received.append(time.monotonic())
            if state["last_replan"]["id"] == job_id:
                statuses.append(state["last_replan"]["status"])
                if state["last_replan"]["status"] == "running":
                    tick = time.monotonic()
                    peer = await actor.subscribe(f"{actor.run_id}:{actor.state.event_seq}")
                    assert time.monotonic() - tick < 0.2
                    actor.unsubscribe(peer)
                if state["last_replan"]["status"] == "succeeded":
                    break
        assert "queued" in statuses and "running" in statuses
        assert actor.state.operations[0].actual_start_sim_s == before.actual_start_sim_s
        assert actor.state.operations[0].progress >= before.progress
        assert max((b - a for a, b in zip(received, received[1:])), default=0) < 1
        assert actor.coordinator.worker.pid != os.getpid()
        actor.unsubscribe(queue)
    finally:
        await actor.stop()
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_watchdog_terminates_hung_process_and_can_restart():
    worker = PlannerWorker()
    await worker.start()
    old_pid = worker.pid
    try:
        os.kill(old_pid, signal.SIGSTOP)
        data = inputs()
        data["deadline"] = time.monotonic() + 0.15
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            await worker.solve(data)
        assert time.monotonic() - started < 1
        assert not worker.ready and worker.process is None
        result = await worker.solve(inputs())
        assert result["status"] == "succeeded" and worker.pid != old_pid
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_stale_input_and_tampered_certificate_are_never_applied(test_settings, prepared_run):
    engine, sessions = database(test_settings.database_url.get_secret_value())
    actor = StationActor(engine, sessions, prepared_run)
    await actor.start()
    try:
        coordinator = actor.coordinator
        await coordinator.request("manual", datetime.now(UTC).isoformat(), spawn=False)
        job_id = coordinator.owner
        data = await coordinator.start_job(job_id)
        result = await coordinator.worker.solve(data)
        assert coordinator.valid_result(job_id, result)
        for field, replacement in (
            ("input_revision", actor.state.input_revision + 1),
            ("config_version", actor.state.config_version + 1),
            ("active_plan_id", "another-plan"),
        ):
            original = getattr(actor.state, field)
            setattr(actor.state, field, replacement)
            assert not coordinator.valid_result(job_id, result)
            setattr(actor.state, field, original)
        before = actor.state.active_plan_id
        for candidate in result["candidates"]:
            candidate["operations"][0]["duration_sim_s"] += 1
        await coordinator.result(job_id, result)
        assert actor.state.last_replan.status == "failed"
        assert actor.state.last_replan.outcome_reason_codes == ["INVALID_CERTIFICATE"]
        assert actor.state.active_plan_id == before
    finally:
        await actor.stop()
        await engine.dispose()


def test_real_api_worker_timeout_keeps_sse_alive_and_replacement_recovers(client, login):
    login()
    actor = client.app.state.actor
    queue = client.portal.call(actor.subscribe, f"{actor.run_id}:{actor.state.event_seq}")
    os.kill(actor.coordinator.worker.pid, signal.SIGSTOP)
    reply = client.post("/api/v1/replans", json={**envelope(client), "reason": "manual"})
    detail = wait_result(client, reply.json()["result"]["replan_id"])
    assert detail["job"]["status"] == "timeout" and detail["plans"] == []
    assert detail["job"]["elapsed_ms"] < 5000 and actor.ready
    assert not actor.coordinator.worker.ready

    async def observed_stream():
        frames = []
        while not queue.empty():
            frames.append(queue.get_nowait())
        actor.unsubscribe(queue)
        return frames

    frames = client.portal.call(observed_stream)
    assert len(frames) >= 5 and sum('"kind":"heartbeat"' in f.wire for f in frames) >= 3
    assert max((b.emitted - a.emitted for a, b in zip(frames, frames[1:])), default=0) < 1
    recovered = client.post("/api/v1/replans", json={**envelope(client), "reason": "manual"}).json()
    assert wait_result(client, recovered["result"]["replan_id"])["job"]["status"] == "succeeded"
    assert client.get("/health/ready").status_code == 200
