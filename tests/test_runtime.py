import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from digital_station.contracts import SimulationCommand, State
from digital_station.db import DomainEvent, Run, RunState, StateSnapshot, database
from digital_station.runtime import ActorError, StationActor, apply_effects, frame
from digital_station.scenario import make_initial_state
from digital_station.simulator import Simulator
from digital_station.smoke_plan import SCHEDULE_ID, load_smoke_plan


@pytest.fixture
async def actor(test_settings, prepared_run):
    engine, sessions = database(test_settings.database_url.get_secret_value())
    actor = StationActor(engine, sessions, prepared_run, enable_planner=False)
    await actor.start()
    try:
        yield actor
    finally:
        await actor.stop()
        await engine.dispose()


async def control(actor, action, speed=None, body=None):
    body = body or SimulationCommand(
        request_id=str(uuid4()),
        run_id=actor.run_id,
        expected_input_revision=actor.state.input_revision,
        action=action,
        speed=speed,
    )
    return await actor.call("control", ("u-dispatcher", body, datetime.now(UTC).isoformat()))


async def receive(queue):
    item = await asyncio.wait_for(queue.get(), 2)
    assert item is not None
    payload = json.loads(item.wire.split("\ndata: ", 1)[1])
    State.model_validate(payload["state"])
    return payload


@pytest.mark.asyncio
async def test_paused_heartbeats_without_durable_writes(actor):
    queue = await actor.subscribe(f"{actor.run_id}:{actor.state.event_seq}")
    snapshots = [await receive(queue) for _ in range(3)]
    assert len({p["state"]["event_seq"] for p in snapshots}) == 1
    assert len({p["state"]["server_time"] for p in snapshots}) == 3
    assert all(p["state"]["sim_time_s"] == 0 and p["cause"]["kind"] == "heartbeat" for p in snapshots)
    async with actor.sessions() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(DomainEvent).where(DomainEvent.run_id == actor.run_id)
            )
            == 2
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(StateSnapshot).where(StateSnapshot.run_id == actor.run_id)
            )
            == 2
        )
    actor.unsubscribe(queue)


@pytest.mark.asyncio
async def test_clock_start0_speed_pause_step_and_event_replay(actor):
    queue = await actor.subscribe(f"{actor.run_id}:{actor.state.event_seq}")
    await receive(queue)
    await control(actor, "set_speed", speed=5)
    revision = actor.state.input_revision
    await control(actor, "play")
    assert actor.state.operations[0].actual_start_sim_s == 0
    while actor.state.sim_time_s < 4:
        await receive(queue)
    assert actor.state.input_revision == revision + 1  # ticks don't change planner inputs
    assert actor.state.operations[0].progress > 0
    await control(actor, "pause")
    paused = actor.state.model_dump(mode="json")
    await asyncio.sleep(0.9)
    assert actor.state.model_dump(mode="json") == paused
    await control(actor, "step")
    assert actor.state.sim_time_s == paused["sim_time_s"] + 1
    async with actor.sessions() as session:
        events = list(
            await session.scalars(
                select(DomainEvent).where(DomainEvent.run_id == actor.run_id).order_by(DomainEvent.seq)
            )
        )
        replay = events[0].payload["state"]
        for event in events[1:]:
            assert "station" not in event.payload["effects"]["scalars"]
            replay = apply_effects(replay, event.payload["effects"])
        row = await session.get(RunState, actor.run_id)
        assert replay == row.payload == actor.state.model_dump(mode="json")
        assert [e.seq for e in events] == list(range(1, actor.state.event_seq + 1))
    actor.unsubscribe(queue)


@pytest.mark.asyncio
async def test_receipts_versions_and_invalid_step(actor):
    body = SimulationCommand(
        request_id=str(uuid4()),
        run_id=actor.run_id,
        expected_input_revision=actor.state.input_revision,
        action="play",
    )
    first = await control(actor, "play", body=body)
    revision = actor.state.input_revision
    assert await control(actor, "play", body=body) == first
    assert actor.state.input_revision == revision
    with pytest.raises(ActorError, match="IDEMPOTENCY_MISMATCH"):
        await control(actor, "pause", body=body.model_copy(update={"action": "pause"}))
    with pytest.raises(ActorError, match="REVISION_MISMATCH"):
        await control(actor, "pause", body=body.model_copy(update={"request_id": str(uuid4())}))
    with pytest.raises(ActorError, match="INVALID_TRANSITION"):
        await control(actor, "step")


@pytest.mark.asyncio
async def test_atomic_subscribe_catchup_reset_and_slow_client(actor):
    old_seq = actor.state.event_seq
    await control(actor, "step")
    queue = await actor.subscribe(f"{actor.run_id}:{old_seq}")
    frames = [await receive(queue) for _ in range(actor.state.event_seq - old_seq)]
    assert [p["state"]["event_seq"] for p in frames] == list(range(old_seq + 1, actor.state.event_seq + 1))
    actor.unsubscribe(queue)
    for cursor, reason in [
        ("another-run:1", "run_changed"),
        ("bad", "invalid_cursor"),
        (f"{actor.run_id}:0", "cursor_expired"),
    ]:
        queue = await actor.subscribe(cursor)
        assert (await receive(queue))["reason"] == reason
        actor.unsubscribe(queue)
    slow = await actor.subscribe(f"{actor.run_id}:{actor.state.event_seq}")
    fast = await actor.subscribe(f"{actor.run_id}:{actor.state.event_seq}")
    await receive(slow)
    await receive(fast)
    for _ in range(101):
        actor.broadcast(frame(actor.state))
        await receive(fast)
    assert slow not in actor.subscribers and await slow.get() is None
    assert fast in actor.subscribers and actor.ready
    actor.unsubscribe(fast)


@pytest.mark.asyncio
async def test_single_writer_lock(actor):
    second = StationActor(actor.engine, actor.sessions, actor.run_id, enable_planner=False)
    with pytest.raises(RuntimeError, match="already has a writer"):
        await second.start()
    assert actor.ready


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["play", "step"])
async def test_failed_commit_rolls_back_and_never_publishes(actor, action):
    previous = actor.state.model_dump(mode="json")
    queue = await actor.subscribe(f"{actor.run_id}:{actor.state.event_seq}")
    await receive(queue)
    # Deliberate corruption ONLY in isolated test DB: force a unique-key failure after CAS UPDATE.
    async with actor.sessions() as session, session.begin():
        session.add(
            DomainEvent(
                run_id=actor.run_id,
                seq=actor.state.event_seq + 1,
                source_event_id=f"test-collision-{uuid4()}",
                received_at=datetime.now(UTC),
                sim_time_s=0,
                kind="test_collision",
                actor_id=None,
                request_id=None,
                payload={},
            )
        )
    with pytest.raises(ActorError, match="SERVICE_NOT_READY"):
        await control(actor, action)
    assert not actor.ready and await queue.get() is None
    async with actor.sessions() as session:
        row = await session.get(RunState, actor.run_id)
        assert row.payload == previous and row.state_version == previous["state_version"]


@pytest.mark.asyncio
async def test_recovery_preserves_running_phase_groups_locks(test_settings, prepared_run):
    engine, sessions = database(test_settings.database_url.get_secret_value())
    initial = make_initial_state(run_id=prepared_run)
    simulator = Simulator(load_smoke_plan(initial), initial)
    list(simulator.advance(1080))
    state = simulator.state
    state.mode = "running"
    # Explicit synthetic restart fixture, not a live demo recording.
    async with sessions() as session, session.begin():
        await session.execute(
            update(RunState)
            .where(RunState.run_id == prepared_run)
            .values(sim_time_s=1080, payload=state.model_dump(mode="json"))
        )
        await session.execute(
            update(Run)
            .where(Run.id == prepared_run)
            .values(status="running", execution_schedule_id=SCHEDULE_ID)
        )
    actor = StationActor(engine, sessions, prepared_run, enable_planner=False)
    await actor.start()
    try:
        assert actor.state.mode == "paused" and actor.state.sim_time_s == 1080
        assert actor.state.input_revision == state.input_revision + 1
        for key in ("operations", "wagon_groups", "resources", "tracks"):
            assert actor.state.model_dump()[key] == state.model_dump()[key]
        await control(actor, "play")
        op = next(o for o in actor.state.operations if o.id == "op-T2-shunt-out")
        assert op.status == "running" and op.phase == "push_to_target"
        assert op.actual_start_sim_s == 840 and op.progress == pytest.approx(240 / 420)
        async with sessions() as session:
            checkpoint = await session.scalar(
                select(StateSnapshot)
                .where(StateSnapshot.run_id == prepared_run)
                .order_by(StateSnapshot.seq.desc())
            )
            assert "station" not in checkpoint.payload
    finally:
        await actor.stop()
        await engine.dispose()
