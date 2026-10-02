"""Read-only OpenAPI/types/fixtures/docs parity and frozen SHA256 verification."""

import hashlib
import json
import re
from pathlib import Path

from export_http_types import render

from digital_station.api import create_app
from digital_station.contracts import State

ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / "contracts/v1.0-freeze.json"


def canonical(path):
    return re.sub(r"\{[^}]+\}", "{id}", path.split("?", 1)[0])


def audit(check_hashes=True):
    document = create_app().openapi()
    assert json.loads((ROOT / "contracts/openapi.json").read_text()) == document, "Stale OpenAPI export"
    assert (ROOT / "contracts/http-v1.ts").read_text() == render(), "Stale generated HTTP types"
    assert json.loads((ROOT / "fixtures/api/v1/state.schema.json").read_text()) == State.model_json_schema()
    actual = {
        (method.upper(), canonical(path))
        for path, item in document["paths"].items()
        for method in item
        if method in {"get", "post", "patch", "delete", "put"}
    }
    handoff = (ROOT / "docs/FRONTEND_HANDOFF.md").read_text()
    table = handoff.split("| Метод и путь после `/api/v1`", 1)[1].split("Вне префикса:", 1)[0]
    expected = {
        (method, canonical("/api/v1" + path))
        for method, path in re.findall(r"`(GET|POST|PATCH) (/[^` ]+)`", table)
    }
    assert expected <= actual, f"Documented endpoints absent: {expected - actual}"
    expected |= {
        ("GET", "/health/live"),
        ("GET", "/health/ready"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/logout"),
        ("GET", "/api/v1/auth/me"),
        ("GET", "/api/v1/replans/{id}/explanation"),
    }
    assert actual == expected, f"Undocumented or missing endpoint: {actual ^ expected}"
    for method, path in actual:
        original = next(p for p in document["paths"] if canonical(p) == path)
        response = document["paths"][original][method.lower()]["responses"]
        ok = response[next(code for code in response if code.startswith("2"))]
        if path.endswith("/stream"):
            assert "text/event-stream" in ok["content"]
        elif path.endswith("/reports.csv"):
            assert "text/csv" in ok["content"]
        elif method == "GET" or path.endswith("/login"):
            assert ok["content"]["application/json"]["schema"], f"Untyped response {path}"
    captured = ROOT / "fixtures/api/v1/live"
    checked = []
    if captured.exists():
        manifest = json.loads((captured / "manifest.json").read_text())
        for name, digest in manifest["files"].items():
            assert hashlib.sha256((captured / name).read_bytes()).hexdigest() == digest
            checked.append(name)
        for name in manifest["state_files"]:
            State.model_validate_json((captured / name).read_text())
    if check_hashes:
        manifest = json.loads(FREEZE.read_text())
        assert manifest["status"] == "FROZEN" and manifest["version"] == "1.0"
        for name, digest in manifest["files"].items():
            assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, f"Frozen drift: {name}"
    return dict(
        status="passed",
        endpoint_count=len(actual),
        typed_http_responses=True,
        documented_paths_match=True,
        generated_http_types_match=True,
        state_schema_matches=True,
        captured_fixtures_checked=checked,
        frozen_hashes_checked=check_hashes,
    )


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, indent=2))
