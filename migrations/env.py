import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from digital_station.db import Base
from digital_station.settings import Settings

target_metadata = Base.metadata


def migrate(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def online():
    # Explicit URL override allows isolated migration tests without altering the demo DB.
    url = os.environ.get("ALEMBIC_DATABASE_URL") or Settings().database_url.get_secret_value()
    engine = create_async_engine(url, hide_parameters=True)
    async with engine.connect() as connection:
        await connection.run_sync(migrate)
    await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("H0–H2 migration requires a PostgreSQL connection")
else:
    asyncio.run(online())
