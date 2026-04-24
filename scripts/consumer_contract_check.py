#!/usr/bin/env python3
"""consumer_contract_check.py — consumer-driven contract tests.

Session 210 (2026-04-24) Layer 2 of enterprise API reliability. The
frontend declares in `contracts/consumer.json` exactly which fields it
reads from each backend endpoint. This script cross-references those
declarations against the committed `openapi.json` and fails if any
declared field is missing, renamed, or (in the case of required fields)
marked optional-removed.

Why separate from Layer 1 (OpenAPI codegen):
    Codegen produces TYPES from the schema. A backend change that drops
    a field still produces valid TS (the field just becomes `undefined
    | T`). The frontend keeps compiling. This script catches the
    SEMANTIC break: "frontend says it needs X, backend no longer
    provides X."

Contrast with Layer 3 (runtime telemetry):
    Layer 3 catches drift AFTER it reaches prod. Layer 2 catches it
    before merge. Both are needed — Layer 2 for deterministic CI,
    Layer 3 for the residual class Layer 2 can't predict (JSONB
    sub-fields, enum enumeration in fields the frontend wasn't
    explicitly declaring).

Exit codes:
    0 — all declared consumer contracts are satisfied by the backend
    1 — one or more consumer contracts violated
    2 — script-level error (missing file, invalid JSON, etc.)
"""
from __future__ import annotations

import json
import pathlib
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Set


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CONSUMER_JSON = REPO_ROOT / "mcp-server" / "central-command" / "frontend" / "contracts" / "consumer.json"
OPENAPI_JSON = REPO_ROOT / "mcp-server" / "central-command" / "openapi.json"


@dataclass
class ContractViolation:
    endpoint: str
    method: str
    field: str
    reason: str


def _load(path: pathlib.Path, label: str) -> dict:
    if not path.exists():
        print(f"ERROR: {label} not found at {path}", file=sys.stderr)
        sys.exit(2)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: {label} is not valid JSON: {e}", file=sys.stderr)
        sys.exit(2)


def _resolve_ref(schema: dict, ref: str) -> Optional[dict]:
    """Resolve `#/components/schemas/X` → the dict. OpenAPI 3 canonical."""
    if not ref.startswith("#/"):
        return None
    parts = ref[2:].split("/")
    cursor: dict = schema
    for p in parts:
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(p, {})
    return cursor if isinstance(cursor, dict) else None


def _collect_200_schema(schema: dict, path: str, method: str) -> Optional[dict]:
    """Return the JSON Schema object for the 200 response body of the
    given endpoint, resolving $ref. Returns None if not present."""
    methods = schema.get("paths", {}).get(path)
    if not methods:
        return None
    op = methods.get(method.lower())
    if not op:
        return None
    # 204 endpoints (telemetry) legitimately have no response body →
    # we don't validate them through this script.
    responses = op.get("responses", {})
    for status in ("200", "201"):
        resp = responses.get(status)
        if not resp:
            continue
        content = resp.get("content", {})
        app_json = content.get("application/json", {})
        body_schema = app_json.get("schema")
        if not body_schema:
            continue
        if "$ref" in body_schema:
            resolved = _resolve_ref(schema, body_schema["$ref"])
            if resolved is not None:
                return resolved
        return body_schema
    return None


def _available_fields(schema: dict, body_schema: dict) -> Set[str]:
    """Walk a JSON Schema object and return the field names it declares."""
    fields: Set[str] = set()
    visited: Set[int] = set()

    def walk(obj: dict) -> None:
        if id(obj) in visited:
            return
        visited.add(id(obj))
        if "$ref" in obj:
            resolved = _resolve_ref(schema, obj["$ref"])
            if resolved is not None:
                walk(resolved)
            return
        # Typical FastAPI case: type=object with properties.
        props = obj.get("properties")
        if isinstance(props, dict):
            fields.update(props.keys())
        # Composition (allOf/oneOf/anyOf) — walk each branch and merge.
        for key in ("allOf", "oneOf", "anyOf"):
            for sub in obj.get(key, []):
                if isinstance(sub, dict):
                    walk(sub)

    walk(body_schema)
    return fields


def main() -> int:
    consumer = _load(CONSUMER_JSON, "consumer.json")
    openapi = _load(OPENAPI_JSON, "openapi.json")

    contracts: List[dict] = consumer.get("contracts", [])
    if not contracts:
        print("WARNING: consumer.json has no contracts declared", file=sys.stderr)
        return 0

    violations: List[ContractViolation] = []
    for c in contracts:
        endpoint = c.get("endpoint")
        method = c.get("method", "get")
        required = c.get("required_fields", [])
        if not endpoint or not required:
            continue
        body_schema = _collect_200_schema(openapi, endpoint, method)
        if body_schema is None:
            # Endpoint entirely missing from the schema — treat as violation.
            violations.append(ContractViolation(
                endpoint=endpoint, method=method, field="<all>",
                reason="endpoint not found in openapi.json",
            ))
            continue
        available = _available_fields(openapi, body_schema)
        for field in required:
            if field not in available:
                violations.append(ContractViolation(
                    endpoint=endpoint, method=method, field=field,
                    reason=f"field not in response schema (available: {sorted(available)})",
                ))

    if not violations:
        print(f"[consumer-contract-check] ✓ {len(contracts)} contracts satisfied", file=sys.stderr)
        return 0

    print(f"[consumer-contract-check] ❌ {len(violations)} contract violations:",
          file=sys.stderr)
    for v in violations:
        print(f"  {v.method.upper()} {v.endpoint} expects '{v.field}' → {v.reason}",
              file=sys.stderr)
    print("", file=sys.stderr)
    print("To fix:", file=sys.stderr)
    print("  (a) If the backend intentionally removed the field: update the "
          "frontend component that reads it, then REMOVE the field from "
          "contracts/consumer.json in the same commit. "
          "Regenerate openapi.json + api-generated.ts.", file=sys.stderr)
    print("  (b) If the backend accidentally dropped the field: restore it on "
          "the backend Pydantic response model. Regenerate openapi.json.",
          file=sys.stderr)
    print("  (c) If the endpoint shape changed legitimately: update the "
          "contract declaration in consumer.json to match the new shape.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
