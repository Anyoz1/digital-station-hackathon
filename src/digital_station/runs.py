"""Serialized new-run command; old history is immutable and never deleted."""

import time
from datetime import UTC, datetime

from sqlalchemy import update

from .bootstrap import create_initial_run
from .contracts import State
from .db import CommandReceipt, Run, RunState, Scenario
from .planning import PlanningCoordinator
from .runtime import ActorError
from .scenario import utc_now
from .simulator import Simulator
from .smoke_plan import load_smoke_plan


async def new_run(actor, user_id, body, ingress):
    previous, digest = await actor.coordinator.command_check(user_id, body, "runs")
    if previous:
        return previous
    async with actor.sessions() as session:
        scenario = await session.get(Scenario, (body.scenario_id, 1))
        if scenario is None:
            raise ActorError(
                422,
                "VALIDATION_ERROR",
                "Неизвестный учебный сценарий",
                {"fields": [{"path": "scenario_id", "message": "not found"}]},
            )
        initial = State.model_validate(scenario.payload["initial_state"])
        if len(initial.tracks) != 12 or len(initial.trains) != 7:
            raise ActorError(422, "SCENARIO_LIMIT_EXCEEDED", "MVP ограничен текущими 12 путями и 7 поездами")
        schedule = scenario.payload["smoke_plan"]
    # Finish the current clock transaction before closing. In-flight workers are cancelled,
    # and their run/revision certificate can never apply to the new run.
    candidate = actor.state.model_copy(deep=True)
    candidate.mode = "paused"
    candidate.input_revision += 1
    if candidate.last_replan and candidate.last_replan.status in {"queued", "running"}:
        candidate.last_replan.status = "stale"
        candidate.last_replan.finished_at = utc_now()
        candidate.last_replan.outcome_reason_codes = ["RUN_CHANGED"]
    await actor.persist(
        candidate,
        [dict(kind="run_closed", entity_ids=[])],
        "simulation_control",
        ingested=ingress,
        checkpoint=True,
    )
    await actor.coordinator.stop()
    now = datetime.now(UTC)
    old_id = actor.run_id
    async with actor.sessions() as session, session.begin():
        await session.execute(update(Run).where(Run.id == old_id).values(status="closed", closed_at=now))
        run_id = await create_initial_run(session, body.scenario_id, body.seed, actor.state.config_version)
        receipt = dict(
            request_id=body.request_id,
            run_id=old_id,
            input_revision=actor.state.input_revision,
            state_version=actor.state.state_version,
            result={"new_run_id": run_id},
        )
        session.add(
            CommandReceipt(
                user_id=user_id,
                request_id=body.request_id,
                run_id=old_id,
                body_hash=digest,
                response_status=201,
                created_at=now,
                payload=receipt,
            )
        )
        await session.flush()
        row = await session.get(RunState, run_id)
        new_state = State.model_validate(row.payload)
    actor.coordinator = None
    actor.run_id, actor.state, actor.seed = run_id, new_state, body.seed
    actor.metric_samples = []
    actor.ring.clear()
    actor.ring_bytes = 0
    actor.measurements.reset(run_id)
    actor.simulator = Simulator(load_smoke_plan(new_state, schedule), initial)
    actor.last_checkpoint = time.monotonic()
    # Commit actual schedule initialization, then reset every existing SSE subscription.
    await actor.persist(
        actor.simulator.state.model_copy(deep=True),
        [dict(kind="smoke_schedule_loaded", entity_ids=[])],
        "run_reset",
        ingested=ingress,
        checkpoint=True,
        stream_reset="run_changed",
    )
    actor.simulator.guard_replanning = actor.enable_planner
    actor.anchor_sim, actor.anchor_wall = 0.0, time.monotonic()
    actor.next_publish = actor.anchor_wall + actor.PERIOD
    actor.coordinator = PlanningCoordinator(actor, initial, actor.config)
    await actor.coordinator.start()
    # Never solve on the actor: the existing isolated worker and launch barrier handle init.
    await actor.coordinator.request("init", utc_now())
    return receipt
