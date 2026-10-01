import hashlib
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from digital_station.contracts import APIError, AuthResponse, Config, State, Train
from digital_station.fixtures import fixture_suite
from digital_station.scenario import make_initial_state

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures/api/v1"


def test_initial_scenario_identity_and_budget():
    state = make_initial_state()
    assert len(state.tracks) == 12
    assert len(state.trains) == 7
    assert len(state.operations) == 33
    assert len(state.resources) == 19
    assert sum(g.wagon_count for g in state.wagon_groups) == 60
    assert len({w for g in state.wagon_groups for w in g.wagon_ids}) == 60
    assert sum(t.type == "FREIGHT" for t in state.trains) == 6
    assert state.sim_time_s == 0 and state.mode == "paused"
    assert all(o.status == "pending" and o.actual_start_sim_s is None for o in state.operations)
    passenger = next(t for t in state.trains if t.id == "P1")
    assert passenger.priority == 3 and passenger.group_ids == [] and passenger.total_length_m == 120
    assert [o.kind for o in state.operations if o.train_id == "P1"] == ["arrival", "departure"]
    assert next(t for t in state.tracks if t.id == "D1").occupied_length_m == 20


@pytest.mark.parametrize("category", ["FREIGHT", "PASSENGER", "SERVICE", "OTHER"])
def test_fixed_consist_dto_is_type_independent(category):
    data = next(t for t in make_initial_state().model_dump()["trains"] if t["id"] == "P1")
    data["type"] = category
    train = Train.model_validate(data)
    assert train.group_ids == [] and train.type == category


@pytest.mark.parametrize("field", ["group_ids", "target_group_ids"])
def test_fixed_consist_cannot_gain_fake_groups(field):
    data = next(t for t in make_initial_state().model_dump()["trains"] if t["id"] == "P1")
    data[field] = ["G1"]
    with pytest.raises(ValidationError):
        Train.model_validate(data)


def test_wagon_duplicate_is_rejected():
    data = make_initial_state().model_dump()
    data["wagon_groups"][1]["wagon_ids"][0] = data["wagon_groups"][0]["wagon_ids"][0]
    with pytest.raises(ValidationError, match="multiple groups"):
        State.model_validate(data)


def test_all_checked_in_fixtures_match_generator_and_manifest():
    expected = fixture_suite()
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    assert manifest["source"] == "synthetic_static_fixture"
    assert not manifest["solver_validated"] and not manifest["performance_measured"]
    for name, data in expected.items():
        assert json.loads((FIXTURES / name).read_text()) == data
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == digest
    State.model_validate_json((FIXTURES / "snapshot.initial.json").read_text())
    for name in ("stream.normal.json", "stream.incident.json"):
        frames = json.loads((FIXTURES / name).read_text())
        previous = -1
        for frame in frames:
            state = State.model_validate(frame["data"]["state"])
            assert state.event_seq > previous
            assert frame["id"] == f"{state.run_id}:{state.event_seq}"
            previous = state.event_seq
    for role in ("viewer", "operator", "dispatcher", "admin"):
        assert (
            AuthResponse.model_validate_json((FIXTURES / f"auth.{role}.json").read_text()).user.role == role
        )
    assert Config.model_validate_json((FIXTURES / "config.json").read_text()).config_version == 1
    for item in json.loads((FIXTURES / "errors.json").read_text()):
        APIError.model_validate(item["body"])
    assert json.loads((FIXTURES / "state.schema.json").read_text()) == State.model_json_schema()


def test_generated_types_follow_frozen_handoff():
    handoff = (ROOT / "docs/FRONTEND_HANDOFF.md").read_text()
    blocks = re.findall(r"```ts\n(.*?)\n```", handoff, re.S)
    expected = "\n\n".join(block for block in blocks if re.search(r"^(type|interface) ", block, re.M))
    expected = re.sub(r"^(type|interface) ", r"export \1 ", expected, flags=re.M)
    assert (ROOT / "contracts/api-v1.ts").read_text() == (
        "// Generated from frozen FRONTEND_HANDOFF v1.0.\n" + expected + "\n"
    )


def test_operation_dag_references_and_maneuver_routes():
    state = make_initial_state()
    seen = set()
    for operation in state.operations:
        assert set(operation.predecessor_ids) <= seen
        seen.add(operation.id)
    route_pairs = {(r.from_id, r.to_id) for r in state.station.layout.routes}
    assert ("D1", "H") in route_pairs and ("H", "R3") in route_pairs
    assert ("D1", "R3") not in route_pairs  # Same-side shortcut is absent even in static geometry.


def test_frozen_document_hashes():
    manifest = (ROOT / "docs/V1.0.md").read_text()
    entries = re.findall(r"^([0-9a-f]{64})  (docs/[^\n]+)$", manifest, re.M)
    assert len(entries) == 4
    marker = b"\n<!-- POST_H12_RAILWAY_UI -->\n"
    approved_append = {"docs/FRONTEND_HANDOFF.md", "docs/PLAN_24H.md"}
    for digest, name in entries:
        content = (ROOT / name).read_bytes()
        if name in approved_append:
            assert content.count(marker) == 1, f"Expected exactly one approved append marker: {name}"
            original, appendix = content.split(marker)
            assert appendix.strip(), f"Empty approved appendix: {name}"
            assert b"```ts" not in appendix, "Frozen normative TypeScript stays in the original prefix"
        else:
            assert marker not in content, f"No append approved for {name}"
            original = content
        assert hashlib.sha256(original).hexdigest() == digest
