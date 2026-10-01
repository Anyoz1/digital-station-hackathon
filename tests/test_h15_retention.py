import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from digital_station.db import DomainEvent, Run, StateSnapshot, database
from digital_station.history import HistoryError, load_samples, prune_retention, snapshot
from digital_station.runtime import StationActor


@pytest.mark.asyncio
@pytest.mark.parametrize("closed", [False, True])
async def test_retention_keeps_wall_anchor_effect_chain_and_open_run_kpi_anchor(
    test_settings, prepared_run, closed
):
    engine, sessions = database(test_settings.database_url.get_secret_value())
    actor = StationActor(engine, sessions, prepared_run, enable_planner=False)
    await actor.start()
    try:
        for t in [300, 600, 900]:
            actor.last_checkpoint = time.monotonic() - 31
            await actor.advance(t)
            await actor.persist(actor.state.model_copy(deep=True), [], "tick", checkpoint=True)
        final = actor.state.model_copy(deep=True)
        async with sessions() as session, session.begin():
            checkpoints = list(
                await session.scalars(
                    select(StateSnapshot)
                    .where(StateSnapshot.run_id == prepared_run)
                    .order_by(StateSnapshot.seq)
                )
            )
            anchor = next(s for s in checkpoints if s.sim_time_s == 600)
            metric_anchor = max((s for s in checkpoints if s.sim_time_s == 0), key=lambda s: s.seq)
            # Synthetic aging ONLY in isolated test DB, not proof of a 15-minute live demo.
            old, recent = datetime.now(UTC) - timedelta(hours=25), datetime.now(UTC) - timedelta(hours=23)
            await session.execute(
                update(StateSnapshot).where(StateSnapshot.run_id == prepared_run).values(created_at=recent)
            )
            await session.execute(
                update(StateSnapshot)
                .where(StateSnapshot.run_id == prepared_run, StateSnapshot.seq <= anchor.seq)
                .values(created_at=old)
            )
            await session.execute(
                update(DomainEvent).where(DomainEvent.run_id == prepared_run).values(received_at=recent)
            )
            await session.execute(
                update(DomainEvent)
                .where(DomainEvent.run_id == prepared_run, DomainEvent.seq <= anchor.seq)
                .values(received_at=old)
            )
            if closed:
                run = await session.get(Run, prepared_run)
                run.closed_at, run.status = datetime.now(UTC), "closed"
            await session.flush()
            await prune_retention(session)
            events = list(
                await session.scalars(
                    select(DomainEvent).where(DomainEvent.run_id == prepared_run).order_by(DomainEvent.seq)
                )
            )
            assert events[0].seq == (anchor.seq if closed else metric_anchor.seq)
            assert [e.seq for e in events] == list(range(events[0].seq, final.event_seq + 1))
            assert await snapshot(session, prepared_run, final.event_seq) == final
            if not closed:
                assert await load_samples(session, final)
            else:
                with pytest.raises(HistoryError):
                    await snapshot(session, prepared_run, 1)
    finally:
        await actor.stop()
        await engine.dispose()


@pytest.mark.asyncio
async def test_history_rejects_broken_effects_without_invoking_engine(test_settings, prepared_run):
    engine, sessions = database(test_settings.database_url.get_secret_value())
    actor = StationActor(engine, sessions, prepared_run, enable_planner=False)
    await actor.start()
    try:
        await actor.advance(120)
        seq = actor.state.event_seq
        async with sessions() as session, session.begin():
            event = await session.get(DomainEvent, (prepared_run, seq))
            event.payload = {"corrupted_test_only": True}
            await session.flush()
            with pytest.raises(HistoryError, match="разрыв") as failure:
                await snapshot(session, prepared_run, seq)
            assert failure.value.code == "HISTORY_GAP"
    finally:
        await actor.stop()
        await engine.dispose()
