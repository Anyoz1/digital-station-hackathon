"""Generate transport types from actual OpenAPI, never rename the frozen domain TS."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ts(schema):
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1].replace("-", "_")
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(x) for x in schema["enum"])
    for key, join in (("anyOf", " | "), ("oneOf", " | "), ("allOf", " & ")):
        if key in schema:
            return "(" + join.join(ts(x) for x in schema[key]) + ")"
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        required = set(schema.get("required", []))
        parts = [
            f"{json.dumps(k)}{'' if k in required else '?'}: {ts(v)};"
            for k, v in schema.get("properties", {}).items()
        ]
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            parts.append(f"[key: string]: {ts(extra)};")
        elif extra is True:
            parts.append("[key: string]: unknown;")
        return "{ " + " ".join(parts) + " }" if parts else "Record<string, unknown>"
    if kind == "array":
        if "prefixItems" in schema:
            return "[" + ", ".join(ts(x) for x in schema["prefixItems"]) + "]"
        return f"Array<{ts(schema.get('items', {}))}>"
    if kind in {"integer", "number"}:
        return "number"
    if kind in {"string", "boolean", "null"}:
        return kind
    if not schema or set(schema) <= {"title", "description"}:
        return "unknown"
    raise ValueError(f"Unsupported schema: {schema}")


def render():
    document = json.loads((ROOT / "contracts/openapi.json").read_text())
    lines = [
        "// Generated from actual frozen OpenAPI. Do not edit by hand.",
        "// api-v1.ts remains the canonical domain/display contract; this file covers HTTP DTOs.",
    ]
    for name, schema in sorted(document["components"]["schemas"].items()):
        lines.append(f"export type {name.replace('-', '_')} = {ts(schema)};")
    lines += [
        "",
        "export interface StateCause { kind: string; entity_ids: string[]; ingested_at: string; }",
        "export interface StateEvent { state: State; cause: StateCause; }",
        "export interface ResetEvent { state: State; reason: string; }",
        'export type SSEEventName = "state" | "reset";',
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    (ROOT / "contracts/http-v1.ts").write_text(render())
    print("Exported all actual HTTP schemas + SSE payload types")
