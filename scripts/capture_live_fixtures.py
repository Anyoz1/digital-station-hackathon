"""Read-only real API responses, never passwords/session tokens or synthetic state."""

import argparse
import hashlib
import json
from pathlib import Path

import httpx

from digital_station.contracts import State
from digital_station.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


def capture(base):
    target = ROOT / "fixtures/api/v1/live"
    target.mkdir(parents=True, exist_ok=True)
    values = {}
    settings = Settings()
    with httpx.Client(base_url=base, timeout=15) as client:
        for role in ("viewer", "operator", "dispatcher", "admin"):
            response = client.post(
                "/api/v1/auth/login",
                json={
                    "username": role,
                    "password": getattr(settings, f"demo_{role}_password").get_secret_value(),
                },
            )
            response.raise_for_status()
            values[f"auth.{role}.json"] = response.json()
        response = client.get("/api/v1/snapshot")
        response.raise_for_status()
        state = response.json()
        State.model_validate(state)
        values["snapshot.live.json"] = state
        run = state["run_id"]
        paths = {
            "health.live.json": "/health/live",
            "health.ready.json": "/health/ready",
            "config.json": "/api/v1/config",
            "scenarios.json": "/api/v1/scenarios",
            "metrics.json": "/api/v1/metrics",
            "clock.json": "/api/v1/time",
            "history.json": f"/api/v1/history?run_id={run}&limit=500",
            "snapshot.replay.json": f"/api/v1/history/snapshot?run_id={run}&seq=1",
            "replan.json": f"/api/v1/replans/{state['last_replan']['id']}",
            "explanation.json": f"/api/v1/replans/{state['last_replan']['id']}/explanation",
        }
        for name, path in paths.items():
            response = client.get(path)
            response.raise_for_status()
            values[name] = response.json()
        with client.stream("GET", "/api/v1/stream", timeout=10) as response:
            response.raise_for_status()
            event = {}
            for line in response.iter_lines():
                if line.startswith("id:"):
                    event["id"] = line[3:].strip()
                if line.startswith("event:"):
                    event["event"] = line[6:].strip()
                if line.startswith("data:"):
                    event["data"] = json.loads(line[5:])
                    break
            values["stream.json"] = [event]
    proof_file = ROOT / "artifacts/backend-v1/browser-proof.json"
    state_files = ["snapshot.live.json", "snapshot.replay.json"]
    if proof_file.exists():
        proof = json.loads(proof_file.read_text())
        for step in proof["steps"]:
            if step["step"] == "incident_conflicts_alternatives_autoapply":
                values["snapshot.main.incident.json"] = step["snapshot"]
                values["replan.main.incident.json"] = step["detail"]
                values["explanation.main.incident.json"] = step["explanation"]
                state_files.append("snapshot.main.incident.json")
            if step["step"] == "freight_shunting":
                values["snapshot.main.shunting.json"] = step["snapshot"]
                state_files.append("snapshot.main.shunting.json")
            if step["step"] == "manual_confirmation":
                for phase in ("before", "after"):
                    name = f"snapshot.manual.{phase}.json"
                    values[name] = step[phase]
                    state_files.append(name)
    for name, value in values.items():
        (target / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    manifest = {
        "schema_version": "1.0",
        "status": "FROZEN",
        "source": "captured_real_API",
        "captured_at": values["clock.json"]["server_sent_ms"],
        "base_url": base,
        "run_id": run,
        "description": "Read-only response samples, not a client simulator or independent SLA measurement",
        "state_files": state_files,
        "files": {name: hashlib.sha256((target / name).read_bytes()).hexdigest() for name in values},
    }
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"captured": list(values), "run_id": run, "state_files": state_files}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    capture(parser.parse_args().base_url)
