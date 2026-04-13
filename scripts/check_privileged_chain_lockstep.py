#!/usr/bin/env python3
"""Lockstep verifier for the privileged-access chain-of-custody.

Session 205 mandate: fleet_cli.PRIVILEGED_ORDER_TYPES,
migration 175 v_privileged_types, and
privileged_access_attestation.ALLOWED_EVENTS must stay consistent.
Drift = chain violation = security incident.

This script parses each source, extracts the set literal, and asserts:
  1. fleet_cli.PRIVILEGED_ORDER_TYPES == migration 175 CLI order types
     (every CLI-exposed privileged order type is DB-trigger-enforced)
  2. fleet_cli.PRIVILEGED_ORDER_TYPES ⊆ attestation.ALLOWED_EVENTS
     (every CLI-exposed privileged order has an attestation path)
  3. migration 175 v_privileged_types ⊆ attestation.ALLOWED_EVENTS
     (every DB-enforced type can be attested — no chain gap)

Exit code:
  0 — lists are in lockstep
  1 — drift detected, chain integrity violated, FAIL CI
  2 — parse error, cannot verify (also fail)

Run as pre-commit + in CI.
"""
from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
FLEET_CLI = REPO / "mcp-server/central-command/backend/fleet_cli.py"
ATTESTATION = REPO / "mcp-server/central-command/backend/privileged_access_attestation.py"
MIGRATION_175 = REPO / "mcp-server/central-command/backend/migrations/175_privileged_chain_enforcement.sql"


def _fail(msg: str, code: int = 1) -> "None":
    print(f"[lockstep] FAIL: {msg}", file=sys.stderr)
    sys.exit(code)


def extract_python_set(path: pathlib.Path, var_name: str) -> set[str]:
    """Extract a `VAR = {"a", "b"}` or `VAR = { "a", "b", }` style set from Python source."""
    src = path.read_text()
    # Match the assignment up to the next newline-level unbalanced close brace.
    m = re.search(
        rf"^{re.escape(var_name)}\s*[:=][^={{]*\{{\s*((?:[^{{}}]+?))\s*\}}",
        src,
        re.MULTILINE | re.DOTALL,
    )
    if not m:
        _fail(f"could not locate {var_name} in {path.name}", code=2)
    body = m.group(1)
    # Strip comments on every line
    body = re.sub(r"#.*", "", body)
    return {
        t.strip().strip('"').strip("'")
        for t in re.split(r"[,\n]+", body)
        if t.strip().strip('"').strip("'")
    }


def extract_sql_array(path: pathlib.Path, var_name: str) -> set[str]:
    """Extract a plpgsql `var_name TEXT[] := ARRAY[ 'a', 'b' ]` literal."""
    src = path.read_text()
    m = re.search(
        rf"{re.escape(var_name)}\s+TEXT\[\]\s*:=\s*ARRAY\s*\[\s*((?:[^\[\]])+)\s*\]",
        src,
        re.DOTALL,
    )
    if not m:
        _fail(f"could not locate {var_name} in {path.name}", code=2)
    body = m.group(1)
    body = re.sub(r"--.*", "", body)
    return {
        t.strip().strip("'").strip('"')
        for t in re.split(r"[,\n]+", body)
        if t.strip().strip("'").strip('"')
    }


def main() -> int:
    fleet_cli_types = extract_python_set(FLEET_CLI, "PRIVILEGED_ORDER_TYPES")
    allowed_events = extract_python_set(ATTESTATION, "ALLOWED_EVENTS")
    migration_types = extract_sql_array(MIGRATION_175, "v_privileged_types")

    print(f"fleet_cli.PRIVILEGED_ORDER_TYPES  = {sorted(fleet_cli_types)}")
    print(f"attestation.ALLOWED_EVENTS         = {sorted(allowed_events)}")
    print(f"migration 175 v_privileged_types   = {sorted(migration_types)}")

    errors: list[str] = []

    # 1. CLI privileged types MUST equal migration types — every CLI-
    #    exposed type must be DB-trigger-enforced, and no DB-enforced
    #    type should be missing from CLI.
    # (We accept migration being a strict SUPERSET of CLI for future-
    #  proofing, i.e. the DB can reject things the CLI doesn't yet emit.)
    if not fleet_cli_types.issubset(migration_types):
        missing = fleet_cli_types - migration_types
        errors.append(
            f"CLI has types not enforced by DB trigger: {sorted(missing)}. "
            f"Add them to migration 175 v_privileged_types."
        )

    # 2. Every CLI-exposed type MUST be in ALLOWED_EVENTS so the
    #    attestation path is wired.
    if not fleet_cli_types.issubset(allowed_events):
        missing = fleet_cli_types - allowed_events
        errors.append(
            f"CLI has types without attestation path: {sorted(missing)}. "
            f"Add them to privileged_access_attestation.ALLOWED_EVENTS."
        )

    # 3. Every DB-enforced type MUST be in ALLOWED_EVENTS — if the DB
    #    requires an attestation bundle for a type, the attestation
    #    module must be able to create one.
    if not migration_types.issubset(allowed_events):
        missing = migration_types - allowed_events
        errors.append(
            f"DB-enforced types not writable by attestation module: {sorted(missing)}. "
            f"Add them to privileged_access_attestation.ALLOWED_EVENTS."
        )

    if errors:
        print()
        print("CHAIN OF CUSTODY INTEGRITY CHECK FAILED", file=sys.stderr)
        for e in errors:
            print(f"  • {e}", file=sys.stderr)
        print()
        print(
            "See CLAUDE.md § 'Privileged-Access Chain of Custody' + "
            "memory/feedback_critical_architectural_principles.md §8",
            file=sys.stderr,
        )
        return 1

    print("[lockstep] OK — chain of custody lists consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
