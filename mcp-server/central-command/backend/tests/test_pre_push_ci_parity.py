"""CI gate: pre-push hook covers the same source-level tests CI runs.

Followup #46 closes the 2026-05-02 cascade where commit 675fa1a6
CI-failed because `test_sql_columns_match_schema` wasn't in the
pre-push allowlist. Pre-push hooks pass; CI fails. Same class as the
2026-04-28 90-min cascade (TS errors only surfaced in CI).

This gate enforces the lockstep:

  - Every source-level test (tests/test_*.py NOT ending in _pg.py)
    that exists in CI's `Run backend tests` step MUST be listed in
    .githooks/pre-push's SOURCE_LEVEL_TESTS array OR explicitly
    exempted via _PRE_PUSH_DEP_HEAVY_EXEMPT below.

A test is considered "DB-gated" / pg-test if its filename ends in
`_pg.py` — those are excluded from pre-push because they require a
live PostgreSQL fixture.

If a test is dep-heavy (imports asyncpg models or pynacl) and would
fail to collect on a dev box without the full backend env, add it
to _PRE_PUSH_DEP_HEAVY_EXEMPT with a one-line justification.

Adding a new source-level test? Append it to BOTH:
  - .githooks/pre-push SOURCE_LEVEL_TESTS array
  - (this test will pass automatically once the array is updated)
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND.parent.parent.parent
_PRE_PUSH = _REPO_ROOT / ".githooks" / "pre-push"
_TESTS_DIR = _BACKEND / "tests"

# Naming-convention prefixes/patterns for TIER-1 source-level
# governance tests (regex/AST against source files; no backend deps
# beyond stdlib + pytest). These MUST be in pre-push.
#
# TIER-2 (FastAPI TestClient, pynacl crypto, SQLAlchemy models) +
# TIER-3 (_pg.py DB integration) are CI-only by design — listing
# them in pre-push would force every dev box to install the full
# backend env or use --no-verify.
#
# The 2026-05-02 cascade was a TIER-1 miss: test_sql_columns_match_schema
# is a static-source check that should have been in pre-push.
_TIER1_PATTERNS = [
    re.compile(r"^test_no_"),                  # ratchet gates
    re.compile(r"^test_iso_"),                 # ISO-config source checks
    re.compile(r"_lockstep(?:_pg)?\.py$"),     # cross-location lockstep gates
    re.compile(r"^test_(check_constraint|sql_columns|sql_filter|sql_on_conflict)_"),
    re.compile(r"^test_(canonical|admin_transaction)_"),
    re.compile(r"^test_(lifespan_imports|test_filename_uniqueness)"),
    re.compile(r"^test_(loop_records_heartbeat|expected_interval_calibration)"),
    re.compile(r"^test_(frontend_mutation_csrf|no_same_origin_credentials|no_stdlib_logger_kwargs)"),
    re.compile(r"^test_(assertion_metadata_complete|substrate_docs_present)"),
    re.compile(r"^test_(pydantic_contract_check|openapi_schema_in_sync|consumer_contract_check)"),
    re.compile(r"^test_(client_telemetry_ingest|ci_prod_version_lockstep)"),
    re.compile(r"^test_per_control_lockstep"),
    re.compile(r"^test_go_agent_terminal_status_lockstep"),
    re.compile(r"^test_pre_push_ci_parity"),  # this very file
    re.compile(r"^test_compliance_status_not_read"),
    re.compile(r"^test_compliance_chain_llm_free"),  # AI-audit dim 8
    re.compile(r"^test_data_completeness_field"),    # D1 followup #47
]

# Tests that match a TIER-1 pattern but are dep-heavy and exempted
# with one-line reason. Empty today.
_PRE_PUSH_DEP_HEAVY_EXEMPT: dict[str, str] = {}


def _is_tier1(test_name: str) -> bool:
    return any(p.search(test_name) for p in _TIER1_PATTERNS)


def _read_pre_push_allowlist() -> set[str]:
    """Parse SOURCE_LEVEL_TESTS=( ... ) array from .githooks/pre-push.

    Manually walks paren depth instead of regex because inline comments
    can contain `(` and `)` chars (e.g. `# (asyncpg, pynacl)`) and the
    naive regex would terminate at the first `)`.
    """
    if not _PRE_PUSH.exists():
        pytest.skip(f"Pre-push hook not present at {_PRE_PUSH}")
    text = _PRE_PUSH.read_text()
    # Find the start of the array
    start_match = re.search(r"SOURCE_LEVEL_TESTS=\(", text)
    if not start_match:
        raise AssertionError(
            f"Could not find SOURCE_LEVEL_TESTS=( in {_PRE_PUSH}. "
            f"Either the array was renamed (update this test) or the "
            f"hook was refactored away."
        )
    # Walk paren depth from after the opening `(`
    pos = start_match.end()
    depth = 1
    in_string = False
    string_char = ""
    while pos < len(text) and depth > 0:
        c = text[pos]
        if in_string:
            if c == string_char and text[pos - 1] != "\\":
                in_string = False
        elif c == "#" and not in_string:
            # Skip to end of line
            nl = text.find("\n", pos)
            pos = nl if nl != -1 else len(text)
            continue
        elif c in ('"', "'"):
            in_string = True
            string_char = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        pos += 1
    body = text[start_match.end() : pos - 1]
    paths = re.findall(r'"([^"]+)"', body)
    return {p.split("/")[-1] for p in paths if p.endswith(".py")}


def _enumerate_tier1_tests() -> list[str]:
    """All TIER-1 source-level tests (matched by naming convention)."""
    out: list[str] = []
    for path in sorted(_TESTS_DIR.glob("test_*.py")):
        if path.name.endswith("_pg.py"):
            continue
        if _is_tier1(path.name):
            out.append(path.name)
    return out


def test_pre_push_allowlist_covers_all_tier1_tests():
    """Every TIER-1 source-level governance test must be in pre-push.

    TIER-1 = static-source check (regex/AST) with no backend deps.
    Adding a TIER-1 test requires:
      1. Naming the file to match a _TIER1_PATTERNS regex above
      2. Listing it in SOURCE_LEVEL_TESTS in .githooks/pre-push

    Both happen automatically if you follow the naming convention
    (test_no_*, *_lockstep.py, etc.) AND add to pre-push. The gate
    here verifies (2) given (1)."""
    allowlist = _read_pre_push_allowlist()
    discovered = set(_enumerate_tier1_tests())
    missing = discovered - allowlist - set(_PRE_PUSH_DEP_HEAVY_EXEMPT)
    assert not missing, (
        f"TIER-1 source-level tests not covered by pre-push ({len(missing)}):\n"
        + "\n".join(f"  - tests/{name}" for name in sorted(missing))
        + "\n\nAdd each to SOURCE_LEVEL_TESTS in .githooks/pre-push, OR\n"
        f"add to _PRE_PUSH_DEP_HEAVY_EXEMPT here with a reason.\n\n"
        f"This gate exists to prevent the 2026-05-02 class where a new\n"
        f"TIER-1 CI gate landed but pre-push didn't run it locally — first\n"
        f"developer to push got a CI failure that should have been caught\n"
        f"at pre-push time."
    )


def test_pre_push_allowlist_only_references_real_files():
    allowlist = _read_pre_push_allowlist()
    all_tests = {p.name for p in _TESTS_DIR.glob("test_*.py")}
    bogus = allowlist - all_tests
    assert not bogus, (
        f"Pre-push allowlist references files that don't exist:\n"
        + "\n".join(f"  - tests/{name}" for name in sorted(bogus))
        + "\n\nLikely a stale entry from a deleted test. Remove from "
        f"SOURCE_LEVEL_TESTS in .githooks/pre-push."
    )


def test_no_pg_tests_in_pre_push_allowlist():
    """DB-gated tests (filename ends in _pg.py) require a live Postgres
    fixture — they'd skip locally anyway, but listing them in pre-push
    is misleading."""
    allowlist = _read_pre_push_allowlist()
    pg_in_allowlist = {name for name in allowlist if name.endswith("_pg.py")}
    assert not pg_in_allowlist, (
        f"DB-gated tests in pre-push allowlist (will skip locally): "
        f"{sorted(pg_in_allowlist)}. Remove from SOURCE_LEVEL_TESTS."
    )
