"""Actual PostgreSQL/actor/worker/SSE publish timings, separate from offline quality.

Baseline selects ONLY the existing FCFS strategy in an isolated QA worker. No
production files/selector/API are changed. Both sides start same FCFS plan at
paused sim0; transport IDs/timestamps differ, physical inputs/config do not.
"""

import asyncio
import csv
import hashlib
import json
import multiprocessing as mp
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from benchmark_backend import CASES
from check_clean_h12 import connect
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy.engine import make_url

from digital_station.api import create_app
from digital_station.bootstrap import bootstrap, create_initial_run
from digital_station.db import database
from digital_station.settings import Settings
from digital_station.validator import physical_signature
from digital_station.worker import PlannerWorker, worker_loop

ROOT = Path(__file__).resolve().parents[1]
TARGET = "digital_station_v1_benchmark"


def baseline_loop(connection):
    from digital_station import planner

    planner.RULES, planner.TIES = ("fcfs",), ("ascending",)
    worker_loop(connection)


class BaselineWorker(PlannerWorker):
    async def start(self):
        self.context = mp.get_context("spawn")
        parent, child = self.context.Pipe()
        self.process = self.context.Process(target=baseline_loop, args=(child,), daemon=True)
        self.process.start()
        child.close()
        self.connection = parent
        hello = await asyncio.wait_for(asyncio.to_thread(parent.recv), 3)
        self.pid, self.ready = hello["pid"], hello["ready"]


async def fresh(settings):
    engine, sessions = database(settings.database_url.get_secret_value())
    try:
        await bootstrap(sessions, settings)
        async with sessions() as session, session.begin():
            await create_initial_run(session)
    finally:
        await engine.dispose()


async def use_heuristic(actor):
    await actor.coordinator.worker.stop()
    actor.coordinator.worker = PlannerWorker()
    await actor.coordinator.worker.start()


def measured_cases():
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    with connect(url, autocommit=True) as db:
        if not db.execute("SELECT 1 FROM pg_database WHERE datname=%s", (TARGET,)).fetchone():
            db.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TARGET)))
    url = url.set(database=TARGET)
    env = {**os.environ, "ALEMBIC_DATABASE_URL": url.render_as_string(hide_password=False)}
    subprocess.run(
        [str(ROOT / ".venv/bin/alembic"), "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    isolated = Settings(database_url=url.render_as_string(hide_password=False))
    evidence = []
    for case, items in CASES.items():
        for algorithm in ("fcfs", "heuristic"):
            asyncio.run(fresh(isolated))
            # A common validated FCFS starting plan for BOTH algorithms.
            with patch("digital_station.planning.PlannerWorker", BaselineWorker):
                with TestClient(create_app(isolated)) as client:
                    assert (
                        client.post(
                            "/api/v1/auth/login",
                            json={
                                "username": "admin",
                                "password": isolated.demo_admin_password.get_secret_value(),
                            },
                        ).status_code
                        == 200
                    )
                    state = client.get("/api/v1/snapshot").json()
                    common = physical_signature(state)
                    if algorithm == "heuristic":
                        client.portal.call(use_heuristic, client.app.state.actor)
                    body = dict(
                        request_id=str(uuid4()),
                        run_id=state["run_id"],
                        expected_input_revision=state["input_revision"],
                    )
                    started = time.monotonic()
                    reply = client.post(
                        "/api/v1/incidents" if items else "/api/v1/replans",
                        json={**body, **({"items": items} if items else {"reason": "manual"})},
                    )
                    assert reply.status_code == (201 if items else 202)
                    job_id = reply.json()["result"]["replan_id"]
                    while time.monotonic() - started < 7:
                        detail = client.get(f"/api/v1/replans/{job_id}").json()
                        if (
                            detail["job"]["status"] not in {"queued", "running"}
                            and client.app.state.actor.coordinator.owner is None
                        ):
                            break
                        time.sleep(0.01)
                    assert detail["job"]["status"] == "succeeded", detail["job"]
                    assert detail["plans"] and all(p["validator"]["passed"] for p in detail["plans"])
                    assert client.app.state.actor.ring  # State emitted only after DB commit.
                    evidence.append(
                        dict(
                            scenario=case,
                            algorithm=algorithm,
                            database=TARGET,
                            physical_input_sha256=hashlib.sha256(
                                json.dumps(common, sort_keys=True).encode()
                            ).hexdigest(),
                            actor_mode="paused",
                            timing_checkpoint_sim_s=0,
                            seed=42,
                            full_wall_scope="incident/manual ingress -> queue/coalesce/worker/validator/DB commit/SSE publish",
                            endpoint_elapsed_ms=(time.monotonic() - started) * 1000,
                            state_before=state,
                            incident_items=items,
                            detail=detail,
                        )
                    )
    return evidence


if __name__ == "__main__":
    target = ROOT / "artifacts/benchmark"
    evidence = measured_cases()
    for case in CASES:
        pair = [e for e in evidence if e["scenario"] == case]
        assert len({e["physical_input_sha256"] for e in pair}) == 1
    (target / "live-timings.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    with (target / "results.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        record = next(
            e for e in evidence if e["scenario"] == row["scenario"] and e["algorithm"] == row["algorithm"]
        )
        job = record["detail"]["job"]
        row.update(
            total_replan_elapsed_ms=job["elapsed_ms"],
            live_compute_ms=job["compute_ms"],
            live_validation_ms=job["validation_ms"],
            live_feasible_alternatives=len(record["detail"]["plans"]),
            live_validator_passed=all(p["validator"]["passed"] for p in record["detail"]["plans"]),
            live_timing_checkpoint_sim_s=0,
            live_timing_actor_mode="paused",
            live_physical_input_sha256=record["physical_input_sha256"],
            timing_mode="offline_quality_plus_separate_live_timing_sim0",
        )
    with (target / "results.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (target / "summary.md").open("a") as handle:
        handle.write(
            "\n## Actual paired live replan timings\n\nSeparate timing control: paused sim0, common validated FCFS starting plan for both. Not the sim480/530 offline quality trajectory. PostgreSQL/actor/process/validator/commit/SSE publish are real; no virtual barrier in these wall-ms. Production core files unchanged.\n\n| Case | Algorithm | Compute,wall-ms | End-to-end,wall-ms | Valid alternatives |\n|---|---|---:|---:|---:|\n"
        )
        for e in evidence:
            j = e["detail"]["job"]
            handle.write(
                f"|{e['scenario']}|{e['algorithm']}|{j['compute_ms']:.3f}|{j['elapsed_ms']:.3f}|{len(e['detail']['plans'])}|\n"
            )
    print(
        json.dumps(
            [dict(scenario=e["scenario"], algorithm=e["algorithm"], job=e["detail"]["job"]) for e in evidence]
        )
    )
