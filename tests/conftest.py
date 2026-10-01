import os
import subprocess
from pathlib import Path

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from digital_station.api import create_app
from digital_station.bootstrap import bootstrap, create_initial_run
from digital_station.db import database
from digital_station.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def test_settings():
    values = dotenv_values(ROOT / ".env")
    url = os.environ.get("TEST_DATABASE_URL") or values.get("TEST_DATABASE_URL")
    if not url or make_url(url).database != "digital_station_h0_test":
        pytest.fail("TEST_DATABASE_URL must name the isolated digital_station_h0_test database")
    settings = Settings(database_url=url)
    env = os.environ.copy()
    env["ALEMBIC_DATABASE_URL"] = url
    subprocess.run(
        [str(ROOT / ".venv/bin/alembic"), "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    return settings


@pytest.fixture
def prepared_run(test_settings):
    import asyncio

    async def prepare():
        engine, sessions = database(test_settings.database_url.get_secret_value())
        await bootstrap(sessions, test_settings)
        async with sessions() as session, session.begin():
            run_id = await create_initial_run(session)
        await engine.dispose()
        return run_id

    return asyncio.run(prepare())


@pytest.fixture
def client(test_settings, prepared_run):
    with TestClient(create_app(test_settings)) as client:
        yield client


@pytest.fixture
def login(client, test_settings):
    def authenticate(role="dispatcher"):
        response = client.post(
            "/api/v1/auth/login",
            json={
                "username": role,
                "password": getattr(test_settings, f"demo_{role}_password").get_secret_value(),
            },
        )
        assert response.status_code == 200, response.text
        return response

    return authenticate
