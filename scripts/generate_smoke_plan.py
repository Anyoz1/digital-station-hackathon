"""Generate explicitly hand-authored smoke timetable artifact, not solver output."""

import json
from pathlib import Path

from digital_station.scenario import make_initial_state
from digital_station.smoke_plan import fixture

target = Path(__file__).resolve().parents[1] / "fixtures/scenarios/demo_main_v1.smoke_plan.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(fixture(make_initial_state()), ensure_ascii=False, indent=2) + "\n")
print(target.relative_to(target.parents[2]))
