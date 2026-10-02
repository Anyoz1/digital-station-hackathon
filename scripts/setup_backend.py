"""Idempotent v1.0 seed: users/config/main + approved manual scenario, no run reset."""

import asyncio
import json

from sqlalchemy import select, text

from digital_station.bootstrap import bootstrap, ensure_manual_scenario
from digital_station.db import Scenario, database
from digital_station.runtime import MIGRATION
from digital_station.settings import Settings


async def setup():
    settings = Settings()
    engine, sessions = database(settings.database_url.get_secret_value())
    try:
        async with engine.connect() as connection:
            if await connection.scalar(text("SELECT version_num FROM alembic_version")) != MIGRATION:
                raise RuntimeError("Run alembic upgrade head first")
            if not await connection.scalar(text("SELECT pg_try_advisory_lock(73100524)")):
                raise RuntimeError("Stop API before setup; existing data preserved")
            try:
                run_id = await bootstrap(sessions, settings)
                async with sessions() as session, session.begin():
                    await ensure_manual_scenario(session)
                    scenarios = list(await session.scalars(select(Scenario.id).order_by(Scenario.id)))
                return {"run_id": run_id, "scenarios": scenarios, "reset": False, "status": "seeded"}
            finally:
                await connection.execute(text("SELECT pg_advisory_unlock(73100524)"))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(setup()), ensure_ascii=False))
