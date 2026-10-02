"""Additive real-server clean-install proof; never drops/resets existing databases.

prepare: empty DB -> migrations -> idempotent documented setup.
serve: actual uvicorn on isolated DB port18000 (keep running for browser QA).
exercise: network API/SSE/physical state/history/CSV + save committed fingerprint.
recovery: compare after restarting API/PostgreSQL; no state injection/DB repairs.
"""

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from digital_station.contracts import Config, HistoryPage, ReplanDetail, State
from digital_station.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
TARGET = "digital_station_v1_clean_20261002b"
ARTIFACT = ROOT / "artifacts/backend-v1"
BASE = "http://127.0.0.1:18000"


def qa_url():
    return make_url(Settings().database_url.get_secret_value()).set(database=TARGET)


def connect(url, **extra):
    return psycopg.connect(
        host=url.host, port=url.port, user=url.username, password=url.password, dbname=url.database, **extra
    )


def environment():
    env = os.environ.copy()
    env["DATABASE_URL"] = qa_url().render_as_string(hide_password=False)
    env["ALEMBIC_DATABASE_URL"] = env["DATABASE_URL"]
    return env


def save(name, value):
    ARTIFACT.mkdir(parents=True, exist_ok=True)
    (ARTIFACT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def prepare():
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    with connect(url, autocommit=True) as connection:
        if not connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (TARGET,)).fetchone():
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TARGET)))
    with connect(qa_url()) as connection:
        tables = connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
        ).fetchone()[0]
        if tables:
            raise RuntimeError("Clean DB is not empty; preserved, no destructive reset allowed")
    result = {"database": TARGET, "tables_before": tables, "transport": "real uvicorn + HTTP"}
    env = environment()
    for args in (["upgrade", "head"], ["check"]):
        reply = subprocess.run(
            [str(ROOT / ".venv/bin/alembic"), *args], cwd=ROOT, env=env, capture_output=True, text=True
        )
        if reply.returncode:
            raise RuntimeError(f"Alembic {args[0]} failed; SQL/credentials suppressed")
        result[f"alembic_{args[0]}"] = reply.stdout.strip()
    setups = []
    for _ in range(2):
        reply = subprocess.run(
            [str(ROOT / ".venv/bin/python"), "scripts/setup_backend.py"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        if reply.returncode:
            raise RuntimeError("Documented setup failed; credentials suppressed")
        setups.append(json.loads(reply.stdout))
    assert setups[0]["run_id"] == setups[1]["run_id"]
    assert setups[0]["scenarios"] == ["demo_main_v1", "manual-control-v1"]
    result["setup_twice"] = setups
    with connect(qa_url()) as connection:
        result["revision"] = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        result["tables_after"] = connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
        ).fetchone()[0]
    save("clean-start.json", result)
    print(json.dumps(result))


def serve():
    os.environ.update(environment())
    import uvicorn

    uvicorn.run(
        "digital_station.main:app",
        host="0.0.0.0",
        port=18000,
        workers=1,
        timeout_graceful_shutdown=5,
        access_log=False,
    )


def login(client, role="admin"):
    settings = Settings()
    response = client.post(
        "/api/v1/auth/login",
        json={
            "username": role,
            "password": getattr(settings, f"demo_{role}_password").get_secret_value(),
        },
    )
    response.raise_for_status()
    assert response.json()["user"]["role"] == role


def snapshot(client):
    response = client.get("/api/v1/snapshot")
    response.raise_for_status()
    State.model_validate(response.json())
    return response.json()


def command(client, path, extra=None):
    state = snapshot(client)
    body = dict(
        request_id=str(uuid4()),
        run_id=state["run_id"],
        expected_input_revision=state["input_revision"],
        **(extra or {}),
    )
    response = client.post(path, json=body)
    response.raise_for_status()
    return response.json()


def physical(state):
    keys = (
        "run_id",
        "scenario_id",
        "sim_time_s",
        "mode",
        "speed",
        "config_version",
        "tracks",
        "zones",
        "trains",
        "wagon_groups",
        "resources",
        "operations",
        "incidents",
    )
    return {key: state[key] for key in keys}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def collect_sse(client, seconds=5):
    frames, ingresses = [], []
    with client.stream("GET", "/api/v1/stream", timeout=15) as response:
        response.raise_for_status()
        assert "text/event-stream" in response.headers["content-type"]
        assert response.headers["x-accel-buffering"] == "no"
        start = time.monotonic()
        for line in response.iter_lines():
            if line.startswith("data:"):
                payload = json.loads(line[5:])
                State.model_validate(payload["state"])
                frames.append(
                    {"seq": payload["state"]["event_seq"], "sim_time_s": payload["state"]["sim_time_s"]}
                )
                ingresses.append(time.monotonic())
            if time.monotonic() - start >= seconds and len(frames) >= 5:
                break
    assert frames[-1]["sim_time_s"] > frames[0]["sim_time_s"]
    return {
        "frames": frames,
        "elapsed_wall_s": ingresses[-1] - ingresses[0],
        "hz": (len(frames) - 1) / (ingresses[-1] - ingresses[0]),
    }


def exercise():
    result = json.loads((ARTIFACT / "clean-start.json").read_text())
    with httpx.Client(base_url=BASE, timeout=15) as client:
        result["health"] = client.get("/health/ready").json()
        login(client)
        state = snapshot(client)
        assert len(state["tracks"]) == 12 and len(state["trains"]) == 7 and len(state["plans"]) == 2
        result["initial"] = {"run_id": state["run_id"], "tracks": 12, "trains": 7, "plans": 2}
        result["scenarios"] = client.get("/api/v1/scenarios").json()
        command(client, "/api/v1/simulation/control", {"action": "set_speed", "speed": 10})
        command(client, "/api/v1/simulation/control", {"action": "play"})
        result["sse"] = collect_sse(client)
        while snapshot(client)["sim_time_s"] < 150:
            time.sleep(0.4)
        command(client, "/api/v1/simulation/control", {"action": "pause"})
        before = snapshot(client)
        assert any(t["occupied_length_m"] for t in before["tracks"] if t["id"] != "D1")
        result["physical_changes"] = {
            "sim_time_s": before["sim_time_s"],
            "running": [o["id"] for o in before["operations"] if o["status"] == "running"],
            "occupied": [t["id"] for t in before["tracks"] if t["occupied_length_m"] > 0],
        }
        command(
            client,
            "/api/v1/incidents",
            {
                "items": [
                    {"kind": "train_delay", "target_id": "T3", "duration_sim_s": 600, "delay_sim_s": 300}
                ]
            },
        )
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = snapshot(client)
            if state["last_replan"]["status"] not in {"queued", "running"}:
                break
            time.sleep(0.1)
        assert state["last_replan"]["status"] == "succeeded" and state["last_replan"]["applied_plan_id"]
        result["replan"] = state["last_replan"]
        detail = client.get(f"/api/v1/replans/{state['last_replan']['id']}").json()
        ReplanDetail.model_validate(detail)
        result["alternatives"] = len(detail["plans"])
        assert len(detail["plans"]) == 2 and all(p["validator"]["passed"] for p in detail["plans"])
        result["kpi"] = snapshot(client)["efficiency"]
        assert result["kpi"]["mode"] == "actual" and len(result["kpi"]["factors"]) == 5
        config = client.get("/api/v1/config").json()
        Config.model_validate(config)
        # Real persisted admin mutation; keep weights/limits unchanged, change a threshold.
        body = dict(
            request_id=str(uuid4()),
            run_id=state["run_id"],
            expected_input_revision=state["input_revision"],
            patch={
                "category_thresholds": {
                    "normal_min": config["category_thresholds"]["normal_min"] - 1,
                    "attention_min": config["category_thresholds"]["attention_min"],
                }
            },
        )
        response = client.patch("/api/v1/config", json=body)
        response.raise_for_status()
        state = snapshot(client)
        history = client.get("/api/v1/history", params={"run_id": state["run_id"], "limit": 200}).json()
        HistoryPage.model_validate(history)
        assert history["items"]
        seq = history["items"][0]["seq"]
        replay = client.get("/api/v1/history/snapshot", params={"run_id": state["run_id"], "seq": seq})
        replay.raise_for_status()
        State.model_validate(replay.json())
        assert replay.json()["event_seq"] == seq and snapshot(client)["sim_time_s"] == state["sim_time_s"]
        result["history"] = {"count": len(history["items"]), "replayed_seq": seq, "live_unchanged": True}
        csv_reply = client.get("/api/v1/reports.csv", params={"run_id": state["run_id"]})
        csv_reply.raise_for_status()
        assert "text/csv" in csv_reply.headers["content-type"]
        (ARTIFACT / "clean-export.csv").write_text(csv_reply.text)
        result["csv"] = {"bytes": len(csv_reply.content), "rows": len(csv_reply.text.splitlines())}
        state = snapshot(client)
        result["before_restart"] = {
            "physical_sha256": fingerprint(physical(state)),
            "state": state,
            "config": client.get("/api/v1/config").json(),
            "history_seq": seq,
            "replay_sha256": fingerprint(replay.json()),
        }
    save("clean-start.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "before_restart"}))


def recovery():
    result = json.loads((ARTIFACT / "clean-start.json").read_text())
    before = result["before_restart"]
    with httpx.Client(base_url=BASE, timeout=15) as client:
        assert client.get("/health/ready").status_code == 200
        login(client)
        state = snapshot(client)
        # Startup performs an already-approved validated replan: new active plan ID
        # is expected. Physical state/operation assignments and actual KPI persist.
        assert fingerprint(physical(state)) == fingerprint(physical(before["state"]))
        assert state["efficiency"] == before["state"]["efficiency"]
        assert client.get("/api/v1/config").json() == before["config"]
        replay = client.get(
            "/api/v1/history/snapshot", params={"run_id": state["run_id"], "seq": before["history_seq"]}
        )
        replay.raise_for_status()
        assert fingerprint(replay.json()) == before["replay_sha256"]
        result["recovery"] = {
            "physical_match": True,
            "config_match": True,
            "history_match": True,
            "run_id": state["run_id"],
            "sim_time_s": state["sim_time_s"],
            "mode": state["mode"],
            "ready": True,
            "actual_match": True,
            "active_plan_before": before["state"]["active_plan_id"],
            "active_plan_after": state["active_plan_id"],
            "active_plan_change_reason": "approved validated startup replan; physical state unchanged",
        }
    save("clean-start.json", result)
    print(json.dumps(result["recovery"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "serve", "exercise", "recovery"])
    {"prepare": prepare, "serve": serve, "exercise": exercise, "recovery": recovery}[
        parser.parse_args().action
    ]()
