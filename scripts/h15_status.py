"""Read-only committed State/history/KPI proof; optional local evidence capture/comparison."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from sqlalchemy import func, select, text

from digital_station.contracts import State
from digital_station.db import ConfigRevision, DomainEvent, RunState, StateSnapshot, database
from digital_station.efficiency import actual
from digital_station.history import load_samples, snapshot
from digital_station.settings import Settings
from digital_station.validator import physical_signature


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def audit(compare=None):
    engine, sessions = database(Settings().database_url.get_secret_value())
    try:
        async with sessions() as session:
            row = await session.scalar(select(RunState).order_by(RunState.updated_at.desc()).limit(1))
            state = State.model_validate(row.payload)
            restored = await snapshot(session, row.run_id, state.event_seq)
            assert restored.model_dump(mode="json") == row.payload
            config = await session.get(ConfigRevision, state.config_version)
            index = actual(state, await load_samples(session, state), config.payload)
            assert index == state.efficiency
            result = dict(
                revision=await session.scalar(text("SELECT version_num FROM alembic_version")),
                run_id=state.run_id,
                scenario_id=state.scenario_id,
                mode=state.mode,
                sim_time_s=state.sim_time_s,
                event_seq=state.event_seq,
                config_version=state.config_version,
                committed_sha256=digest(row.payload),
                physical_sha256=digest(physical_signature(row.payload)),
                replay_equals_committed=True,
                actual_equals_recorded=True,
                efficiency=index.model_dump(mode="json"),
                events=await session.scalar(
                    select(func.count()).select_from(DomainEvent).where(DomainEvent.run_id == state.run_id)
                ),
                checkpoints=await session.scalar(
                    select(func.count())
                    .select_from(StateSnapshot)
                    .where(StateSnapshot.run_id == state.run_id)
                ),
            )
            if compare:
                old = json.loads(Path(compare).read_text())
                previous = await snapshot(session, old["run_id"], old["event_seq"])
                result["restart_checks"] = dict(
                    same_run=old["run_id"] == state.run_id,
                    same_sim_time=old["sim_time_s"] == state.sim_time_s,
                    same_config=old["config_version"] == state.config_version,
                    same_physical_state=old["physical_sha256"] == result["physical_sha256"],
                    historical_hash_unchanged=old["committed_sha256"]
                    == digest(previous.model_dump(mode="json")),
                    actual_unchanged=old["efficiency"] == result["efficiency"],
                )
                assert all(result["restart_checks"].values()), result["restart_checks"]
            return result
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    result = asyncio.run(audit(args.compare))
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(serialized + "\n")
    print(serialized)
