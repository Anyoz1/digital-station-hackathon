"""Read-only H12 audit: PostgreSQL journal reconstruction, validator and measured runs."""

import asyncio
import json

from sqlalchemy import func, select, text

from digital_station.contracts import State
from digital_station.db import (
    DomainEvent,
    IncidentRecord,
    OptimizationRun,
    PlanRecord,
    RunState,
    Scenario,
    database,
)
from digital_station.runtime import apply_effects
from digital_station.settings import Settings
from digital_station.validator import validate_plan


async def main():
    engine, sessions = database(Settings().database_url.get_secret_value())
    async with sessions() as session:
        row = await session.scalar(select(RunState).order_by(RunState.updated_at.desc()).limit(1))
        state = State.model_validate(row.payload)
        scenario = await session.get(Scenario, (state.scenario_id, 1))
        events = list(
            await session.scalars(
                select(DomainEvent).where(DomainEvent.run_id == state.run_id).order_by(DomainEvent.seq)
            )
        )
        restored = events[0].payload["state"]
        for event in events[1:]:
            restored = apply_effects(restored, event.payload["effects"])
        assert restored == row.payload
        validation = validate_plan(
            state, state.operations, State.model_validate(scenario.payload["initial_state"]), state.sim_time_s
        )
        assert validation.passed, validation.errors
        histories = []
        for run in await session.scalars(select(RunState).order_by(RunState.updated_at)):
            histories.append(
                dict(
                    run_id=run.run_id,
                    sim_time_s=run.sim_time_s,
                    event_seq=run.payload["event_seq"],
                    event_count=await session.scalar(
                        select(func.count()).select_from(DomainEvent).where(DomainEvent.run_id == run.run_id)
                    ),
                    incidents=await session.scalar(
                        select(func.count())
                        .select_from(IncidentRecord)
                        .where(IncidentRecord.run_id == run.run_id)
                    ),
                    plans=await session.scalar(
                        select(func.count()).select_from(PlanRecord).where(PlanRecord.run_id == run.run_id)
                    ),
                )
            )
        runs = []
        for record in await session.scalars(select(OptimizationRun).order_by(OptimizationRun.requested_at)):
            job = record.payload["job"]
            inputs = record.payload.get("inputs", {})
            runs.append(
                dict(
                    id=record.id,
                    run_id=record.run_id,
                    status=record.status,
                    reason=job["reason"],
                    elapsed_ms=job["elapsed_ms"],
                    compute_ms=job["compute_ms"],
                    validation_ms=job["validation_ms"],
                    apply_check_ms=record.payload.get("apply_check_ms"),
                    candidate_count=len(job["candidate_plan_ids"]),
                    captured_inputs=bool(inputs),
                )
            )
        result = dict(
            revision=await session.scalar(text("SELECT version_num FROM alembic_version")),
            run_id=state.run_id,
            sim_time_s=state.sim_time_s,
            mode=state.mode,
            event_seq=state.event_seq,
            tracks=len(state.tracks),
            trains=len(state.trains),
            resources=len(state.resources),
            operations=len(state.operations),
            wagon_ids=sum(g.wagon_count for g in state.wagon_groups),
            journal_reconstruction="passed",
            independent_validator=validation.public(),
            histories=histories,
            optimization_runs=runs,
        )
    await engine.dispose()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
