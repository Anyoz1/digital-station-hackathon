"""Generate deterministic data artifacts and the frozen TypeScript contract."""

import hashlib
import json
import re
from pathlib import Path

from digital_station.contracts import State
from digital_station.fixtures import fixture_suite

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = ROOT / "fixtures/api/v1"
    target.mkdir(parents=True, exist_ok=True)
    files = fixture_suite()
    files["state.schema.json"] = State.model_json_schema()
    for name, data in files.items():
        (target / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    manifest = {
        "schema_version": "1.0",
        "spec_tag": "spec-v1.0",
        "stage": "backend-v1-compatible-static",
        "seed": 42,
        "source": "synthetic_static_fixture",
        "runtime_transitions_implemented": True,
        "solver_validated": False,
        "performance_measured": False,
        "description": "Статические автономные образцы, не SLA/solver evidence. Backend v1.0 полностью реализован; live API читает PostgreSQL. Реальные captured responses — в live/.",
        "files": {name: hashlib.sha256((target / name).read_bytes()).hexdigest() for name in files},
        "deferred": ["SERVICE-P2"],
    }
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    handoff = (ROOT / "docs/FRONTEND_HANDOFF.md").read_text()
    blocks = re.findall(r"```ts\n(.*?)\n```", handoff, re.S)
    ts = "\n\n".join(block for block in blocks if re.search(r"^(type|interface) ", block, re.M))
    ts = re.sub(r"^(type|interface) ", r"export \1 ", ts, flags=re.M)
    contract = ROOT / "contracts"
    contract.mkdir(exist_ok=True)
    (contract / "api-v1.ts").write_text("// Generated from frozen FRONTEND_HANDOFF v1.0.\n" + ts + "\n")
    print(f"Generated {len(files)} JSON artifacts + manifest + TypeScript contract")


if __name__ == "__main__":
    main()
