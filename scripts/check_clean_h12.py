"""Additive clean-PostgreSQL migration/bootstrap smoke; never drops or truncates a DB."""

import json
import os
import subprocess
from pathlib import Path

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy.engine import make_url

from digital_station.api import create_app
from digital_station.settings import Settings

TARGET = "digital_station_h12_clean_test"
ROOT = Path(__file__).resolve().parents[1]


def connect(url, **extra):
    return psycopg.connect(
        host=url.host, port=url.port, user=url.username, password=url.password, dbname=url.database, **extra
    )


def main():
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    with connect(url, autocommit=True) as connection:
        if not connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (TARGET,)).fetchone():
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TARGET)))
    clean = url.set(database=TARGET)
    with connect(clean) as connection:
        tables = connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
        ).fetchone()[0]
        if tables:
            raise RuntimeError("Clean check requires an untouched DB; existing data preserved")
    env = os.environ.copy()
    env["ALEMBIC_DATABASE_URL"] = clean.render_as_string(hide_password=False)
    results = {"database": TARGET, "tables_before": tables}
    for command in ("upgrade", "check"):
        args = [str(ROOT / ".venv/bin/alembic"), command] + (["head"] if command == "upgrade" else [])
        reply = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True)
        if reply.returncode:
            raise RuntimeError(f"Alembic {command} failed; credentials/SQL output suppressed")
        results[f"alembic_{command}"] = "passed"
    with connect(clean) as connection:
        results["revision"] = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        results["tables_after"] = connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
        ).fetchone()[0]
        assert connection.execute("SELECT count(*) FROM run").fetchone()[0] == 0
    clean_settings = Settings(database_url=clean.render_as_string(hide_password=False))
    with TestClient(create_app(clean_settings)) as client:
        results["ready_status"] = client.get("/health/ready").status_code
        results["login_status"] = client.post(
            "/api/v1/auth/login",
            json={
                "username": "dispatcher",
                "password": clean_settings.demo_dispatcher_password.get_secret_value(),
            },
        ).status_code
        state = client.get("/api/v1/snapshot").json()
        results.update(
            snapshot_tracks=len(state["tracks"]),
            snapshot_trains=len(state["trains"]),
            plans=len(state["plans"]),
            initial_replan_ms=state["last_replan"]["elapsed_ms"],
        )
        assert results["ready_status"] == results["login_status"] == 200
        assert len(state["tracks"]) == 12 and len(state["trains"]) == 7 and len(state["plans"]) == 2
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
