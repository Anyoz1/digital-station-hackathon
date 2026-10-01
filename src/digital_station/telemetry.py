"""Bounded, non-domain render measurements. Never a safety/planning input."""

import hashlib
import json
import math
import time
from collections import OrderedDict
from datetime import UTC, datetime


def now_ms():
    return time.time() * 1000


def stats(values):
    values = sorted(values)
    return {
        "sample_count": len(values),
        "p50_ms": values[max(0, math.ceil(len(values) * 0.5) - 1)] if values else None,
        "p95_ms": values[max(0, math.ceil(len(values) * 0.95) - 1)] if values else None,
        "max_ms": values[-1] if values else None,
    }


class Measurements:
    """15 wall-minute/3000 sample window per client; nearest-rank percentiles.

    Delivery is registered on the live SSE generator, not inferred from successful acks.
    Initial frames, heartbeats and catch-up are excluded from the foreground SLA.
    Reset/process restart explicitly starts a new window, not persisted history.
    """

    MAX_CLIENTS = 16
    MAX_SAMPLES = 3000
    WINDOW_MS = 900000

    def __init__(self, run_id):
        self.reset(run_id)

    def reset(self, run_id):
        self.run_id = run_id
        self.started_at = datetime.now(UTC).isoformat()
        self.published_events = 0
        self.clients: OrderedDict = OrderedDict()
        self.ingress: OrderedDict = OrderedDict()

    def client(self, client_id):
        client_id = str(client_id)
        if client_id not in self.clients:
            if len(self.clients) >= self.MAX_CLIENTS:
                return None  # Do not evict missing events on an active client.
            self.clients[client_id] = {"events": OrderedDict(), "schema_invalid": 0}
        return self.clients[client_id]

    def published(self, item):
        self.published_events += 1
        if item.ingested_ms is not None:
            self.ingress[item.seq] = (item.ingested_ms, now_ms())
            while len(self.ingress) > self.MAX_SAMPLES:
                self.ingress.popitem(last=False)

    def delivered(self, client_id, item, subscribed_at):
        if client_id is None or item.run_id != self.run_id:
            return
        if item.ingested_ms is None or item.emitted < subscribed_at:
            return
        client = self.client(client_id)
        if client is None:
            return
        rows = client["events"]
        rows.setdefault(item.seq, {"delivered_ms": now_ms(), "ingested_ms": item.ingested_ms})
        self.prune(rows)

    def prune(self, rows):
        cutoff = now_ms() - self.WINDOW_MS
        while rows and (len(rows) > self.MAX_SAMPLES or next(iter(rows.values()))["delivered_ms"] < cutoff):
            rows.popitem(last=False)

    def schema_invalid(self, client_id):
        client = self.client(client_id)
        if client is not None:
            client["schema_invalid"] += 1

    def report(self, body):
        if body.run_id != self.run_id:
            return  # An ack in flight at reset belongs to the closed measurement window.
        client = self.client(body.client_id)
        if client is None:
            return
        rows = client["events"]
        digest = hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
        row = rows.get(body.event_seq) if body.run_id == self.run_id else None
        if row is None:
            if body.run_id == self.run_id and body.event_seq in self.ingress:
                return  # Catch-up from an older subscription is not live SLA.
            client["schema_invalid"] += 1  # Unknown/not-delivered seq isn't a fast sample.
            return
        if row.get("digest"):
            if row["digest"] != digest:
                client["schema_invalid"] += 1
            return
        row["digest"] = digest
        row["visible"] = body.visible
        latency = body.rendered_client_ms + body.offset_ms - row["ingested_ms"]
        receive_to_render = body.rendered_client_ms - body.received_client_ms
        calibrated_render = body.rendered_client_ms + body.offset_ms
        row["invalid"] = (
            latency < 0
            or receive_to_render < 0
            or body.uncertainty_ms > 50
            or abs(calibrated_render - now_ms()) > 10000
        )
        if body.visible and not row["invalid"]:
            row["upper"] = latency + body.uncertainty_ms
            row["receive_to_render"] = receive_to_render

    def ui_stats(self):
        result = []
        for client_id, client in self.clients.items():
            self.prune(client["events"])
            rows = list(client["events"].values())
            upper = [r["upper"] for r in rows if "upper" in r]
            result.append(
                dict(
                    client_id=client_id,
                    latency_upper=stats(upper),
                    receive_to_render=stats([r["receive_to_render"] for r in rows if "upper" in r]),
                    invalid_samples=client["schema_invalid"] + sum(bool(r.get("invalid")) for r in rows),
                    hidden_samples=sum(r.get("visible") is False for r in rows),
                    unreported_events=sum(
                        not r.get("digest") and now_ms() - r["delivered_ms"] >= 2000 for r in rows
                    ),
                    latency_exceedances=sum(v >= 500 for v in upper),
                )
            )
        return result

    async def metrics(self, actor):
        from sqlalchemy import select

        from .db import OptimizationRun

        async with actor.sessions() as session:
            jobs = list(
                await session.scalars(select(OptimizationRun).where(OptimizationRun.run_id == self.run_id))
            )
        completed = [j for j in jobs if j.status not in {"queued", "running"}]
        return dict(
            run_id=self.run_id,
            window_started_at=self.started_at,
            measured_at=datetime.now(UTC).isoformat(),
            published_events=self.published_events,
            connected_clients=len(actor.subscribers),
            actor_queue_depth=actor.queue.qsize(),
            planner_running_jobs=sum(j.status == "running" for j in jobs),
            planner_pending_jobs=sum(j.status == "queued" for j in jobs),
            ui_render=self.ui_stats(),
            replans={
                "elapsed": stats([j.elapsed_ms for j in completed]),
                "compute": stats(
                    [
                        j.payload["job"]["compute_ms"]
                        for j in completed
                        if j.payload["job"]["compute_ms"] is not None
                    ]
                ),
                **{
                    status: sum(j.status == status for j in completed)
                    for status in ("succeeded", "no_feasible_plan", "stale", "timeout", "failed")
                },
                "deadline_exceedances": sum(j.elapsed_ms > 5000 for j in completed),
            },
        )
