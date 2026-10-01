"""Noisy mock observations: durable dedup/order/quarantine, never physical occupancy."""

import hashlib
import json
import math
from datetime import UTC, datetime
from statistics import median
from uuid import uuid4

from pydantic import AwareDatetime, Field, ValidationError
from sqlalchemy import select

from .contracts import DTO
from .db import TelemetryObservation


class ProgressObservation(DTO):
    source_event_id: str = Field(min_length=1, max_length=200)
    source_id: str = Field(min_length=1, max_length=100)
    source_seq: int = Field(ge=1, le=9223372036854775807, strict=True)
    operation_id: str = Field(min_length=1, max_length=100)
    route_id: str = Field(min_length=1, max_length=100)
    observed_at: AwareDatetime
    observed_progress: float = Field(ge=-10, le=10, allow_inf_nan=False, strict=True)


def observed_routes(state):
    return {
        (x.location.operation_id, x.location.route_id)
        for x in [*state.trains, *state.wagon_groups, *state.resources]
        if x.location is not None and x.location.kind == "route"
    }


async def normalize(sessions, state, raw, received_at=None):
    """Called only by the single-writer actor; serial source order survives restart.

    Five wall-seconds freshness and one-second future tolerance are demo input
    assumptions, not railway rules. Median is reset per operation/route, not a delay.
    """
    received_at = received_at or datetime.now(UTC)
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()
    reason, body = None, None
    try:
        body = ProgressObservation.model_validate(raw)
    except ValidationError:
        reason = "INVALID_SCHEMA"
    event_id = raw.get("source_event_id") if isinstance(raw, dict) else None
    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 200:
        event_id = None
    async with sessions() as session, session.begin():
        previous = (
            await session.scalar(
                select(TelemetryObservation).where(
                    TelemetryObservation.run_id == state.run_id,
                    TelemetryObservation.source_event_id == event_id,
                )
            )
            if event_id
            else None
        )
        if previous:
            if previous.payload["body_hash"] == digest:
                return previous.payload["result"]
            reason, event_id = "DUPLICATE_MISMATCH", None
        recent = []
        if body:
            recent = list(
                await session.scalars(
                    select(TelemetryObservation)
                    .where(
                        TelemetryObservation.run_id == state.run_id,
                        TelemetryObservation.source_id == body.source_id,
                        TelemetryObservation.operation_id == body.operation_id,
                        TelemetryObservation.status == "accepted",
                    )
                    .order_by(TelemetryObservation.source_seq.desc())
                    .limit(3)
                )
            )
            running = {o.id for o in state.operations if o.status == "running"}
            age = (received_at - body.observed_at).total_seconds()
            if reason:
                pass
            elif body.operation_id not in running or (
                body.operation_id,
                body.route_id,
            ) not in observed_routes(state):
                reason = "NOT_CURRENT_ROUTE"
            elif recent and body.source_seq <= recent[0].source_seq:
                reason = "OUT_OF_ORDER"
            elif age > 5 or age < -1:
                reason = "STALE_SAMPLE" if age > 5 else "FUTURE_SAMPLE"
        values = (
            [
                r.payload["raw"]["observed_progress"]
                for r in recent[:2]
                if r.payload["raw"]["route_id"] == body.route_id
            ]
            if body
            else []
        )
        filtered = min(1, max(0, median([body.observed_progress, *values]))) if body and not reason else None
        result = {
            "status": "rejected" if reason else "accepted",
            "reason": reason,
            "filtered_progress": filtered,
        }

        # JSON-safe quarantine including non-finite input, without letting it into State.
        def safe(value):
            if isinstance(value, float) and not math.isfinite(value):
                return str(value)
            if isinstance(value, dict):
                return {str(k): safe(v) for k, v in value.items()}
            if isinstance(value, list):
                return [safe(v) for v in value]
            if value is None or isinstance(value, (str, int, float, bool)):
                return value
            return str(value)

        safe_raw = safe(raw)
        session.add(
            TelemetryObservation(
                id=str(uuid4()),
                run_id=state.run_id,
                source_event_id=event_id,
                source_id=body.source_id if body else None,
                operation_id=body.operation_id if body else None,
                source_seq=body.source_seq if body else None,
                received_at=received_at,
                status=result["status"],
                payload={"raw": safe_raw, "body_hash": digest, "result": result},
            )
        )
        return result
