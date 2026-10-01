"""Add an H12 control run; use only with API stopped. Never deletes/reset existing data."""

import argparse
import asyncio

from sqlalchemy import text

from digital_station.bootstrap import bootstrap, create_initial_run
from digital_station.db import database
from digital_station.runtime import StationActor
from digital_station.settings import Settings


async def prepare():
    settings = Settings()
    engine, sessions = database(settings.database_url.get_secret_value())
    async with engine.connect() as connection:
        if not await connection.scalar(text("SELECT pg_try_advisory_lock(73100524)")):
            raise RuntimeError("Stop the API first: station writer is active")
        await connection.execute(text("SELECT pg_advisory_unlock(73100524)"))
    await bootstrap(sessions, settings)
    async with sessions() as session, session.begin():
        run_id = await create_initial_run(session)
    actor = StationActor(engine, sessions, run_id)
    await actor.start()
    try:
        # Offline event-jump fixture preparation using the actual executor/transactions.
        # This is not a new product simulation mode or a proof of live timing SLA.
        await actor.advance(480)
        print(
            f"Prepared {run_id}: demo_main_v1, sim480, paused, 12 tracks, 7 trains; previous history retained"
        )
    finally:
        await actor.stop()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-api-stopped", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(prepare())
