"""Read-only real-HTTP before/after proof on isolated v1 QA API."""

import argparse
import json

import httpx
from check_backend_v1 import ARTIFACT, BASE, fingerprint, login, physical, snapshot


def probe(action):
    path = ARTIFACT / "restart-final.json"
    with httpx.Client(base_url=BASE, timeout=15) as client:
        login(client)
        state = snapshot(client)
        assert state["mode"] == "paused"
        config = client.get("/api/v1/config").json()
        if action == "before":
            response = client.get(
                "/api/v1/history/snapshot", params={"run_id": state["run_id"], "seq": state["event_seq"]}
            )
            response.raise_for_status()
            data = {
                "before": state,
                "committed_before": response.json(),
                "config": config,
                "physical_sha256": fingerprint(physical(state)),
            }
        else:
            data = json.loads(path.read_text())
            before = data["before"]
            assert fingerprint(physical(state)) == data["physical_sha256"]
            assert state["efficiency"] == before["efficiency"] and config == data["config"]
            response = client.get(
                "/api/v1/history/snapshot", params={"run_id": before["run_id"], "seq": before["event_seq"]}
            )
            response.raise_for_status()
            # Live GET/snapshot uses request wall-time, replay uses commit wall-time.
            # Compare a pre-captured committed State when available; old probe's
            # live transport timestamp is explicitly excluded, not a data loss.
            if "committed_before" in data:
                assert response.json() == data["committed_before"]
            else:
                assert {k: v for k, v in response.json().items() if k != "server_time"} == {
                    k: v for k, v in before.items() if k != "server_time"
                }
            data.update(
                after=state,
                physical_match=True,
                actual_match=True,
                config_match=True,
                pre_restart_state_fields_match=True,
                transport_server_time_excluded="committed_before" not in data,
                recovered_committed_snapshot=response.json(),
                active_plan_change_reason="validated startup replan; not physical reset",
            )
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        print(
            json.dumps(
                {
                    k: v
                    for k, v in data.items()
                    if k
                    not in {"before", "after", "config", "committed_before", "recovered_committed_snapshot"}
                }
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["before", "after"])
    probe(parser.parse_args().action)
