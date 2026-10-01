"""Create the approved manual-control fixture with API stopped; preserves every old run."""

import argparse
import asyncio

from sqlalchemy import text

from digital_station.bootstrap import bootstrap, create_initial_run, ensure_manual_scenario
from digital_station.db import database
from digital_station.runtime import StationActor
from digital_station.settings import Settings


async def prepare():
    settings = Settings()
    engine, sessions = database(settings.database_url.get_secret_value())
    async with engine.connect() as connection:
        if not await connection.scalar(text("SELECT pg_try_advisory_lock(73100524)")):
            raise RuntimeError("Stop API first: station writer is active")
        await connection.execute(text("SELECT pg_advisory_unlock(73100524)"))
    await bootstrap(sessions, settings)
    async with sessions() as session, session.begin():
        await ensure_manual_scenario(session)
        run_id = await create_initial_run(session, "manual-control-v1")
    actor = StationActor(engine, sessions, run_id)
    await actor.start()
    try:
        await actor.advance(120)
        print(
            f"Prepared {run_id}: manual-control-v1 sim120 paused, T1 inspection manual/u-operator; old runs retained"
        )
    finally:
        await actor.stop()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-api-stopped", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(prepare())
