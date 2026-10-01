from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from .auth import seed_users
from .contracts import State
from .db import ConfigRevision, DomainEvent, Run, RunState, Scenario, StateSnapshot
from .scenario import DEFAULT_CONFIG, PROFILE_IDS, make_initial_state, utc_now
from .smoke_plan import fixture


async def bootstrap(sessions, settings) -> str:
    async with sessions() as session, session.begin():
        await seed_users(session, settings)
        await session.flush()
        now = datetime.now(UTC)
        if await session.get(ConfigRevision, 1) is None:
            session.add(ConfigRevision(version=1, actor_id=None, created_at=now, payload=DEFAULT_CONFIG))
        if await session.get(Scenario, ("demo_main_v1", 1)) is None:
            template = make_initial_state().model_dump(mode="json")
            session.add(
                Scenario(
                    id="demo_main_v1",
                    version=1,
                    created_at=now,
                    payload={
                        "initial_state": template,
                        "seed": 42,
                        "horizon_sim_s": 7200,
                        "enabled_service_profiles": PROFILE_IDS,
                        "limits": {"known_trains": 8, "active_trains": 6, "macro_operations": 80},
                        "wagons": [
                            {"id": wid, "length_m": 14, "origin_train_id": group["origin_train_id"]}
                            for group in template["wagon_groups"]
                            for wid in group["wagon_ids"]
                        ],
                    },
                )
            )
        await session.flush()
        scenario = await session.get(Scenario, ("demo_main_v1", 1))
        if "smoke_plan" not in scenario.payload:
            scenario.payload = {
                **scenario.payload,
                "smoke_plan": fixture(State.model_validate(scenario.payload["initial_state"])),
            }
        await session.flush()
        current = await session.scalar(select(RunState).order_by(RunState.updated_at.desc()).limit(1))
        if current is not None:
            return current.run_id
        return await create_initial_run(session)


async def create_initial_run(session, scenario_id="demo_main_v1", seed=42, config_version=None):
    """Create a new initial run without deleting existing runs (also used by isolated tests)."""
    now = datetime.now(UTC)
    run_id = f"run-{uuid4()}"
    scenario = await session.get(Scenario, (scenario_id, 1))
    template = State.model_validate(scenario.payload["initial_state"])
    template.run_id, template.server_time = run_id, utc_now()
    if config_version is not None:
        template.config_version = config_version
    state = template.model_dump(mode="json")
    session.add(
        Run(
            id=run_id,
            scenario_id=scenario_id,
            scenario_version=1,
            seed=seed,
            status="paused",
            started_at=now,
            closed_at=None,
        )
    )
    await session.flush()
    session.add(
        RunState(
            run_id=run_id,
            state_version=1,
            input_revision=1,
            config_version=template.config_version,
            sim_time_s=0,
            active_plan_id=None,
            updated_at=now,
            payload=state,
        )
    )
    session.add(
        DomainEvent(
            run_id=run_id,
            seq=1,
            source_event_id=f"{run_id}:initialized",
            received_at=now,
            sim_time_s=0,
            kind="run_initialized",
            actor_id=None,
            request_id=None,
            payload={"state": state},
        )
    )
    await session.flush()
    session.add(
        StateSnapshot(run_id=run_id, seq=1, created_at=now, sim_time_s=0, schema_version="1.0", payload=state)
    )
    return run_id


async def ensure_manual_scenario(session):
    """The approved separate fixture: same topology/trains, one assigned manual inspection."""
    if await session.get(Scenario, ("manual-control-v1", 1)) is not None:
        return
    main = await session.get(Scenario, ("demo_main_v1", 1))
    initial = State.model_validate(main.payload["initial_state"])
    initial.scenario_id = "manual-control-v1"
    op = next(o for o in initial.operations if o.id == "op-T1-inspection")
    op.execution_mode, op.assigned_user_id = "manual", "u-operator"
    session.add(
        Scenario(
            id="manual-control-v1",
            version=1,
            created_at=datetime.now(UTC),
            payload={
                **main.payload,
                "initial_state": initial.model_dump(mode="json"),
                "smoke_plan": fixture(initial),
            },
        )
    )
    await session.flush()
