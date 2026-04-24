#!/usr/bin/env python3
"""export_openapi.py — dump the FastAPI OpenAPI schema to a JSON file.

Session 210 (2026-04-24). Part of the 4-layer enterprise API reliability
plan. This is Layer 1 step 1 of 2: produce the deterministic schema
file that Layer 1 step 2 (frontend codegen) consumes.

Why a side script instead of `/openapi.json` exposed on HTTP:
    main.py intentionally sets `openapi_url=None` on the FastAPI app
    — exposing the schema on prod HTTP is an attack surface
    (every endpoint and its Pydantic models leak to unauthenticated
    callers). A local/CI-only export avoids that surface while still
    giving the frontend codegen a deterministic source of truth.

Determinism:
    `json.dumps(..., sort_keys=True, indent=2, separators=...)` — the
    same committed schema across re-runs on identical code. This is
    load-bearing for the CI diff-check gate.

Output:
    `mcp-server/central-command/openapi.json` (sibling to backend/
    and frontend/). Committed. Regenerated on every backend change.
"""
from __future__ import annotations

import json
import pathlib
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "mcp-server"
OUTPUT = REPO_ROOT / "mcp-server" / "central-command" / "openapi.json"


def main() -> int:
    # Import main.py's FastAPI app. Module import has side effects
    # (middleware install, logging setup) but we accept that — we're
    # only here to call app.openapi(), then exit.
    sys.path.insert(0, str(BACKEND_DIR))
    try:
        import main  # type: ignore[import-not-found]
    except Exception as e:
        print(f"ERROR: cannot import main: {e}", file=sys.stderr)
        return 2

    app = getattr(main, "app", None)
    if app is None:
        print("ERROR: main.app not found — FastAPI app variable moved?", file=sys.stderr)
        return 2

    schema = app.openapi()

    # Stable JSON — sort_keys + fixed indent + fixed separators. If anything
    # about the schema shape is non-deterministic (e.g., model ordering
    # depends on import order), sort_keys flattens it.
    rendered = json.dumps(
        schema,
        sort_keys=True,
        indent=2,
        separators=(",", ": "),
        ensure_ascii=False,
    )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    # If the file already exists and matches, don't re-write (keeps mtime
    # stable for reproducible builds + lets `git diff --exit-code` stay
    # quiet on no-op runs).
    if OUTPUT.exists():
        current = OUTPUT.read_text(encoding="utf-8")
        if current == rendered:
            print(f"[export-openapi] {OUTPUT.relative_to(REPO_ROOT)} unchanged "
                  f"({len(schema.get('paths', {}))} paths)", file=sys.stderr)
            return 0

    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"[export-openapi] wrote {OUTPUT.relative_to(REPO_ROOT)} "
          f"({len(schema.get('paths', {}))} paths, {len(rendered)} bytes)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
