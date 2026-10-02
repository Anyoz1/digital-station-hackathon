"""Consolidate REAL accepted measurements; never substitute headless for foreground."""

import json
import os
import platform
from pathlib import Path

from digital_station.telemetry import stats

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts/backend-v1"


def main():
    accepted = json.loads((ROOT / "artifacts/h18-acceptance.json").read_text())
    timings = json.loads((ROOT / "artifacts/h18-replan-timings-raw.json").read_text())
    elapsed = stats([row["job"]["elapsed_ms"] for row in timings])
    assert elapsed == accepted["replan"]["elapsed"]
    rows = []
    for measurement in accepted["normal"]:
        assert measurement["foreground_verified"] and measurement["wall_ms"] >= 120000
        for client in measurement["clients"]:
            row = dict(
                speed=measurement["speed"],
                wall_ms=measurement["wall_ms"],
                sse_hz=client["sse_hz"],
                **client["measurement"],
            )
            assert row["latency_upper"]["max_ms"] < 500 and row["sse_hz"] >= 1
            assert row["invalid_samples"] == row["hidden_samples"] == row["unreported_events"] == 0
            rows.append(row)
    batches = {}
    for count in (5, 10):
        raw = json.loads((ROOT / f"artifacts/h18-batch{count}-raw.json").read_text())
        assert len(raw["detail"]["plans"]) == 2 and all(
            p["validator"]["passed"] for p in raw["detail"]["plans"]
        )
        batches[str(count)] = {
            "job": raw["detail"]["job"],
            "render": raw["metrics"]["ui_render"],
            "source": f"artifacts/h18-batch{count}-raw.json",
        }
    browser = json.loads((ARTIFACT / "browser-proof.json").read_text())
    clean = json.loads((ARTIFACT / "clean-start.json").read_text())
    result = dict(
        source_foreground="artifacts/h18-acceptance.json + h18-live-{1,10}x-raw.json",
        measured_foreground_at=accepted["finished_at"],
        qualification="Accepted H18 foreground120s each/2 headed clients/workspace9. Reused explicitly: final functional E2E is headless, NOT a new foreground SLA run. Main State/SSE/planner/validator/render steady path unchanged; fresh-manual runtime-control branch fixed and separately tested.",
        environment=dict(
            python=platform.python_version(),
            platform=platform.platform(),
            logical_cpus=os.cpu_count(),
            cpu="AMD Ryzen7 5700U",
            memory="14GiB",
            chromium="151.0.7922.137",
            postgres="16-alpine",
            network="backend/PG + two browser clients on ONE physical host; Vite loopback and LAN IP, not two laptops",
        ),
        foreground=rows,
        valid_render_samples=sum(r["latency_upper"]["sample_count"] for r in rows),
        replan_80=dict(
            elapsed=elapsed,
            compute=stats([r["job"]["compute_ms"] for r in timings]),
            cases=accepted["replan"]["cases"],
            deadline_exceedances=0,
            source="artifacts/h18-replan-timings-raw.json",
        ),
        batches=batches,
        fresh_clean_sse=clean["sse"],
        fresh_functional_main_replan_ms=next(
            s["snapshot"]["last_replan"]["elapsed_ms"]
            for s in browser["steps"]
            if s["step"] == "incident_conflicts_alternatives_autoapply"
        ),
        browser=dict(
            steps=len(browser["steps"]),
            console_page_errors=len(browser["pageErrors"]),
            nominal_console_errors=sum(e["phase"] == "nominal" for e in browser["consoleErrors"]),
            nominal_http_errors=sum(e["phase"] == "nominal" for e in browser["httpErrors"]),
            source="artifacts/backend-v1/browser-proof.json",
        ),
        paired_live_timing_source="artifacts/benchmark/live-timings.json",
        negative_background_evidence=[
            "artifacts/h18-background-1x-raw.json",
            "artifacts/h18-background-10x-raw.json",
        ],
    )
    (ARTIFACT / "final-sla.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            dict(
                valid_render_samples=result["valid_render_samples"], replan=elapsed, browser=result["browser"]
            )
        )
    )


if __name__ == "__main__":
    main()
