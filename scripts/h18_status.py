"""Read-only H18 persistence/normalization evidence; no mutation or secrets."""

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import func, select

from digital_station.db import Run, TelemetryObservation, database
from digital_station.settings import Settings


async def audit():
    engine, sessions = database(Settings().database_url.get_secret_value())
    try:
        async with sessions() as session:
            runs = list(await session.scalars(select(Run).order_by(Run.started_at.desc()).limit(5)))
            counts = (
                await session.execute(
                    select(TelemetryObservation.status, func.count())
                    .group_by(TelemetryObservation.status)
                    .order_by(TelemetryObservation.status)
                )
            ).all()
            observations = list(
                await session.scalars(
                    select(TelemetryObservation).order_by(TelemetryObservation.received_at.desc()).limit(5)
                )
            )
            return {
                "runs": [
                    {
                        "id": r.id,
                        "scenario_id": r.scenario_id,
                        "seed": r.seed,
                        "closed_at": r.closed_at.isoformat() if r.closed_at else None,
                    }
                    for r in runs
                ],
                "normalization_counts": dict(counts),
                "recent_observations": [
                    {
                        "run_id": o.run_id,
                        "source_event_id": o.source_event_id,
                        "received_at": o.received_at.isoformat(),
                        "payload": o.payload,
                    }
                    for o in observations
                ],
                "source": "mock-observed-progress, separate from physical State",
            }
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    serialized = json.dumps(asyncio.run(audit()), ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(serialized + "\n")
    print(serialized)
