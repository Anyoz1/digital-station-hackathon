import asyncio
import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_planner import inputs
from test_planning_api import envelope, wait_result

from digital_station.calendars import refresh_calendars
from digital_station.contracts import IncidentBatchInput, IncidentInput, Operation, SimulationCommand
from digital_station.db import DomainEvent, IncidentRecord, OptimizationRun, database
from digital_station.incidents import apply_batch, resolve
from digital_station.planner import search
from digital_station.runtime import ActorError, StationActor, apply_effects
from digital_station.scenario import make_initial_state
from digital_station.simulator import GuardViolation, Simulator
from digital_station.smoke_plan import load_smoke_plan
from digital_station.validator import validate_plan

BURST = [
    *[
        dict(kind="train_delay", target_id=t, duration_sim_s=900, delay_sim_s=180)
        for t in ("T3", "T4", "T5", "T6")
    ],
    *[dict(kind="track_closure", target_id=t, duration_sim_s=600) for t in ("R2", "S2", "C2")],
    *[dict(kind="resource_loss", target_id=r, duration_sim_s=300) for r in ("I1", "CG1")],
    dict(kind="destination_block", target_id="DEST_E", duration_sim_s=600),
]


def sample(at=480):
    initial = make_initial_state()
    simulator = Simulator(load_smoke_plan(initial), initial)
    for _ in simulator.advance(at):
        pass
    return simulator.state


@pytest.mark.parametrize("count", [1, 5, 10])
def test_burst_uses_same_inputs_two_validated_alternatives(count):
    base = sample()
    state, ids = apply_batch(base, [IncidentInput.model_validate(i) for i in BURST[:count]])
    assert len(ids) == count and state.input_revision == base.input_revision + 1
    assert base.incidents == [] and len(state.incidents) == count
    assert state.conflicts and all(
        i.affected_operation_ids for i in state.incidents if i.kind == "train_delay"
    )
    data = inputs(state)
    result = search(data)
    assert result["status"] == "succeeded", result["attempts"]
    assert len(result["candidates"]) == 2
    assert result["baseline"] and result["baseline"]["validator"]["passed"]
    assert {a["input_digest"] for a in result["attempts"]} == {result["input_digest"]}
    frozen = [o for o in state.operations if o.status in {"running", "completed"}]
    for candidate in result["candidates"]:
        operations = [Operation.model_validate(o) for o in candidate["operations"]]
        assert [o for o in operations if o.status in {"running", "completed"}] == frozen
        assert validate_plan(state, operations, make_initial_state(), state.sim_time_s).passed


def test_busy_loss_and_closure_are_pending_do_not_interrupt_and_runtime_blocks_new_starts():
    base = sample(960)
    running = next(o for o in base.operations if o.kind == "shunt_transfer" and o.status == "running")
    state, _ = apply_batch(
        base,
        [
            IncidentInput(kind="resource_loss", target_id="L1", duration_sim_s=300),
            IncidentInput(kind="track_closure", target_id="C1", duration_sim_s=300),
        ],
    )
    assert all(i.status == "pending" and i.starts_sim_s == running.end_sim_s for i in state.incidents)
    assert next(r for r in state.resources if r.id == "L1").status == "unavailable_pending"
    simulator = Simulator(state, make_initial_state())
    for _ in simulator.advance(running.end_sim_s, allow_starts=False):
        pass
    assert simulator.operations[running.id].status == "completed"
    assert all(i.status == "active" for i in simulator.state.incidents)
    assert (
        simulator.resources["L1"].status == "unavailable" and simulator.tracks["C1"].availability == "closed"
    )
    cargo = simulator.operations["op-T2-cargo"]
    cargo.start_sim_s = simulator.state.sim_time_s
    cargo.end_sim_s = cargo.start_sim_s + cargo.duration_sim_s
    with pytest.raises(GuardViolation):
        simulator.guard(cargo)
    old_ids = [w for g in simulator.state.wagon_groups for w in g.wagon_ids]
    assert len(old_ids) == len(set(old_ids)) == 60


def test_resolve_pending_cancels_outage_and_delay_eta_never_rewinds():
    base = sample(960)
    state, ids = apply_batch(base, [IncidentInput(kind="resource_loss", target_id="L1", duration_sim_s=300)])
    resolved = resolve(state, ids[0])
    assert next(r for r in resolved.resources if r.id == "L1").status == "busy"
    delayed, ids = apply_batch(
        sample(600), [IncidentInput(kind="train_delay", target_id="T4", duration_sim_s=1, delay_sim_s=180)]
    )
    eta = next(t for t in delayed.trains if t.id == "T4").expected_arrival_sim_s
    delayed.sim_time_s += 1
    assert refresh_calendars(delayed)[0]["kind"] == "incident_resolved"
    assert next(t for t in delayed.trains if t.id == "T4").expected_arrival_sim_s == eta
    with pytest.raises(ActorError):
        resolve(delayed, ids[0])


def test_forecast_counts_running_pending_loss_available_until_effective_outage():
    state, _ = apply_batch(
        sample(960), [IncidentInput(kind="resource_loss", target_id="L1", duration_sim_s=300)]
    )
    result = search(inputs(state))
    operations = [Operation.model_validate(o) for o in result["candidates"][0]["operations"]]
    report = validate_plan(state, operations, make_initial_state(), state.sim_time_s)
    assert report.passed
    pools = {sample["sim_time_s"]: sample["pools"]["L1"] for sample in report.samples}
    assert pools[960]["available"] == pools[960]["busy"] == 1
    assert pools[1260]["available"] == pools[1260]["busy"] == 0
    assert pools[1560]["available"] == 1


@pytest.mark.parametrize(
    "kind,target",
    [
        ("train_delay", "T1"),
        ("track_closure", "missing"),
        ("resource_loss", "missing"),
        ("destination_block", "missing"),
    ],
)
def test_invalid_batch_has_no_partial_effect(kind, target):
    base = sample()
    original = base.model_dump(mode="json")
    item = dict(kind=kind, target_id=target, duration_sim_s=30)
    if kind == "train_delay":
        item["delay_sim_s"] = 30
    with pytest.raises(ActorError):
        apply_batch(base, [IncidentInput.model_validate(BURST[0]), IncidentInput.model_validate(item)])
    assert base.model_dump(mode="json") == original


@pytest.mark.parametrize(
    "body",
    [
        dict(kind="track_closure", target_id="R1", duration_sim_s=1, delay_sim_s=1),
        dict(kind="train_delay", target_id="T5", duration_sim_s=1),
        dict(kind="resource_loss", target_id="I1", duration_sim_s=7201),
    ],
)
def test_incident_schema_rejects_invalid_inputs(body):
    with pytest.raises(ValidationError):
        IncidentInput.model_validate(body)


@pytest.mark.parametrize("count", [5, 10])
def test_api_burst_is_atomic_idempotent_autoapplied_and_persisted(client, login, test_settings, count):
    login()
    body = {**envelope(client), "items": BURST[:count]}
    reply = client.post("/api/v1/incidents", json=body)
    assert reply.status_code == 201, reply.text
    assert client.post("/api/v1/incidents", json=body).json() == reply.json()
    result = reply.json()["result"]
    detail = wait_result(client, result["replan_id"])
    assert detail["job"]["status"] == "succeeded" and len(detail["plans"]) == 2
    assert all(p["validator"]["passed"] for p in detail["plans"])
    state = client.get("/api/v1/snapshot").json()
    assert len(state["incidents"]) == count and state["active_plan_id"] == detail["job"]["applied_plan_id"]
    assert detail["plans"][0]["changed_operation_ids"]
    assert not state["conflicts"]

    async def durable():
        engine, sessions = database(test_settings.database_url.get_secret_value())
        async with sessions() as session:
            records = list(
                await session.scalars(select(IncidentRecord).where(IncidentRecord.run_id == state["run_id"]))
            )
            assert {r.id for r in records} == set(result["incident_ids"])
            events = list(
                await session.scalars(
                    select(DomainEvent).where(DomainEvent.run_id == state["run_id"]).order_by(DomainEvent.seq)
                )
            )
            replay = events[0].payload["state"]
            for event in events[1:]:
                replay = apply_effects(replay, event.payload["effects"])
            assert (
                replay["incidents"] == state["incidents"]
                and replay["active_plan_id"] == state["active_plan_id"]
            )
        await engine.dispose()

    asyncio.run(durable())


def test_incident_api_roles_errors_resolve_and_path_idempotency(client, login):
    login("viewer")
    assert client.post("/api/v1/incidents", json={**envelope(client), "items": BURST[:1]}).status_code == 403
    assert client.post("/api/v1/incidents/missing/resolve", json=envelope(client)).status_code == 403
    login()
    for items in (
        [],
        BURST + [BURST[0]],
        [dict(kind="track_closure", target_id="missing", duration_sim_s=1)],
    ):
        assert client.post("/api/v1/incidents", json={**envelope(client), "items": items}).status_code == 422
    receipt = client.post("/api/v1/incidents", json={**envelope(client), "items": BURST[:1]}).json()
    wait_result(client, receipt["result"]["replan_id"])
    body = envelope(client)
    iid = receipt["result"]["incident_ids"][0]
    reply = client.post(f"/api/v1/incidents/{iid}/resolve", json=body)
    assert reply.status_code == 200, reply.text
    assert client.post(f"/api/v1/incidents/{iid}/resolve", json=body).json() == reply.json()
    assert (
        client.post("/api/v1/incidents/missing/resolve", json=body).json()["error"]["code"]
        == "IDEMPOTENCY_MISMATCH"
    )


def test_no_feasible_is_not_timeout_and_invalid_old_plan_is_not_an_alternative(client, login):
    login()
    result = client.post(
        "/api/v1/incidents",
        json={
            **envelope(client),
            "items": [dict(kind="track_closure", target_id=t, duration_sim_s=7200) for t in ("R3", "R4")],
        },
    ).json()["result"]
    detail = wait_result(client, result["replan_id"])
    assert detail["job"]["status"] == "no_feasible_plan" and not detail["plans"]
    state = client.get("/api/v1/snapshot").json()
    assert all(p["validity"] == "invalid" and not p["can_apply"] for p in state["plans"])
    assert state["conflicts"] and detail["job"]["applied_plan_id"] is None


@pytest.mark.parametrize(
    "item,code",
    [
        (dict(kind="train_delay", target_id="T3", duration_sim_s=900, delay_sim_s=180), "ARRIVAL_ETA"),
        (dict(kind="track_closure", target_id="R3", duration_sim_s=7200), "TRACK_CALENDAR"),
        (dict(kind="resource_loss", target_id="CG1", duration_sim_s=7200), "RESOURCE_CALENDAR"),
        (dict(kind="destination_block", target_id="DEST_E", duration_sim_s=7200), "DESTINATION_CALENDAR"),
    ],
)
def test_independent_validator_rejects_old_assignments_that_violate_incident(item, code):
    state, _ = apply_batch(sample(), [IncidentInput.model_validate(item)])
    report = validate_plan(state, state.operations, make_initial_state(), state.sim_time_s)
    assert not report.passed and report.errors[0]["code"] == code, report.errors


@pytest.mark.asyncio
async def test_coalescing_and_running_job_replacement_preserve_barrier_deadline(test_settings, prepared_run):
    engine, sessions = database(test_settings.database_url.get_secret_value())
    actor = StationActor(engine, sessions, prepared_run)
    await actor.start()
    try:

        def command(target):
            return IncidentBatchInput(
                request_id=str(uuid4()),
                run_id=actor.run_id,
                expected_input_revision=actor.state.input_revision,
                items=[
                    IncidentInput(kind="train_delay", target_id=target, duration_sim_s=900, delay_sim_s=30)
                ],
            )

        a = await actor.call("incidents", ("u-dispatcher", command("T5"), datetime.now(UTC).isoformat()))
        first_deadline = actor.coordinator.deadline
        b = await actor.call("incidents", ("u-dispatcher", command("T6"), datetime.now(UTC).isoformat()))
        assert a["result"]["replan_id"] == b["result"]["replan_id"]
        while actor.state.last_replan.status == "queued":
            await asyncio.sleep(0.01)
        old = actor.state.last_replan.id
        c = await actor.call("incidents", ("u-dispatcher", command("T4"), datetime.now(UTC).isoformat()))
        new = c["result"]["replan_id"]
        assert new != old and actor.coordinator.deadline == first_deadline and actor.simulator.launch_barrier
        end = time.monotonic() + 5
        while (
            actor.state.last_replan.status in {"queued", "running"} or actor.coordinator.owner
        ) and time.monotonic() < end:
            await asyncio.sleep(0.02)
        assert actor.state.last_replan.status == "succeeded" and actor.state.last_replan.id == new
        assert len(actor.state.incidents) == 3 and not actor.simulator.launch_barrier
        async with sessions() as session:
            old_record = await session.get(OptimizationRun, old)
            assert old_record.status == "stale"
        assert not await actor.coordinator.result(old, dict(status="succeeded", candidates=[]))
    finally:
        await actor.stop()
        await engine.dispose()


@pytest.mark.parametrize("trigger", ["expiry", "guard"])
@pytest.mark.asyncio
async def test_expiry_and_runtime_guard_automatically_replan_without_losing_facts(
    test_settings, prepared_run, trigger
):
    engine, sessions = database(test_settings.database_url.get_secret_value())
    actor = StationActor(engine, sessions, prepared_run)
    await actor.start()
    try:
        if trigger == "expiry":
            body = IncidentBatchInput(
                request_id=str(uuid4()),
                run_id=actor.run_id,
                expected_input_revision=actor.state.input_revision,
                items=[IncidentInput(kind="train_delay", target_id="T5", duration_sim_s=1, delay_sim_s=180)],
            )
            await actor.call("incidents", ("u-dispatcher", body, datetime.now(UTC).isoformat()))
            while actor.coordinator.owner:
                await asyncio.sleep(0.01)
            eta = next(t for t in actor.state.trains if t.id == "T5").expected_arrival_sim_s
        else:
            # Intentionally damage only an unstarted assignment, never a positive fixture.
            actor.simulator.operations["op-T1-arrival"].resource_ids = ["TL2", "TC1"]
        old = actor.state.last_replan.id
        body = SimulationCommand(
            request_id=str(uuid4()),
            run_id=actor.run_id,
            expected_input_revision=actor.state.input_revision,
            action="step",
        )
        await actor.call("control", ("u-dispatcher", body, datetime.now(UTC).isoformat()))
        assert actor.state.last_replan.id != old and actor.simulator.launch_barrier
        assert actor.state.last_replan.reason == (
            "incident_resolved" if trigger == "expiry" else "guard_violation"
        )
        end = time.monotonic() + 5
        while actor.coordinator.owner and time.monotonic() < end:
            await asyncio.sleep(0.01)
        assert actor.state.last_replan.status == "succeeded" and not actor.simulator.launch_barrier
        if trigger == "expiry":
            assert actor.state.incidents[0].status == "resolved"
            assert next(t for t in actor.state.trains if t.id == "T5").expected_arrival_sim_s == eta
            assert (
                actor.state.operations[0].status == "running"
                and actor.state.operations[0].actual_start_sim_s == 0
            )
        else:
            assert actor.state.operations[0].status == "planned" and actor.state.operations[
                0
            ].resource_ids == ["TL1", "TC1"]
        assert sum(g.wagon_count for g in actor.state.wagon_groups) == 60
    finally:
        await actor.stop()
        await engine.dispose()
