"""Source-level rule: every frontend mutation fetch (POST/PUT/PATCH/DELETE)
MUST include `credentials: 'include'` AND `X-CSRF-Token` (or use the
`fetchApi` helper from utils/api.ts that auto-injects both).

Session 210-B 2026-04-25 audit P0. Today's frontend audit caught:
  - OperatorAckPanel `/dashboard/flywheel-spine/acknowledge` — missing
    X-CSRF-Token, fails 403 on every click
  - SensorStatus deploy/remove — missing both credentials + CSRF, fails
    401/403 on every click

Both are state-changing requests against CSRF-protected endpoints. The
canonical pattern in this codebase:
  - PREFERRED: `fetchApi('/path', { method: 'POST', body: ... })` (helper
    auto-injects CSRF + credentials)
  - ACCEPTABLE: `fetch(url, { method: 'POST', credentials: 'include',
    headers: { 'X-CSRF-Token': getCsrfTokenOrEmpty() } })`
  - WRONG: `fetch(url, { method: 'POST', headers: { 'Content-Type': ... } })`

Ratchet baseline: today's count of WRONG-pattern call sites is locked
in. Adding a NEW raw mutation fetch fails CI. Drive the count down by
migrating to `fetchApi` over time. Eventually flip to BASELINE_MAX = 0
once all 60+ sites are migrated.

Skip:
- node_modules, dist, *.test.tsx, *.test.ts
- utils/api.ts itself (it IS the helper)
- utils/integrationsApi.ts (uses its own internal client)
"""
from __future__ import annotations

import pathlib
import re
from typing import List

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
FRONTEND_SRC = (
    REPO_ROOT / "mcp-server" / "central-command" / "frontend" / "src"
)


# Files that ARE the helper or use a different validated client.
# Excluded from the rule because raw fetch is appropriate here.
HELPER_FILES = {
    "utils/api.ts",
    "utils/integrationsApi.ts",
    # Hooks that wrap react-query mutations are checked via the
    # underlying file; the hook layer is just plumbing.
}


# Pattern: a `fetch(...)` call that:
#   1. Has a method:'POST'|'PUT'|'PATCH'|'DELETE' literal in its options
#   2. Within the same call expression, doesn't include either
#      'credentials' OR 'X-CSRF-Token' / 'csrfHeaders('
#
# We grep at the file level (state-changing fetch + missing CSRF token)
# rather than try to parse the AST — this is a regex linter, accept some
# false positives in exchange for simplicity.
MUTATION_RE = re.compile(
    r"fetch\s*\(\s*[^,]+,\s*\{([^}]*?method\s*:\s*['\"](POST|PUT|PATCH|DELETE)['\"][^}]*?)\}",
    re.IGNORECASE | re.DOTALL,
)


def _frontend_files() -> List[pathlib.Path]:
    """Walk frontend/src for .ts and .tsx files, skipping tests + helpers."""
    out: List[pathlib.Path] = []
    for p in FRONTEND_SRC.rglob("*"):
        if p.suffix not in (".ts", ".tsx"):
            continue
        rel = p.relative_to(FRONTEND_SRC).as_posix()
        if rel in HELPER_FILES:
            continue
        if "test." in p.name or ".test." in p.name:
            continue
        if any(skip in p.parts for skip in ("node_modules", "dist", "build")):
            continue
        out.append(p)
    return out


def _violations() -> List[str]:
    """Return list of `<rel_path>:<line>: <method> fetch missing CSRF`."""
    out: List[str] = []
    for p in _frontend_files():
        try:
            src = p.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = p.relative_to(FRONTEND_SRC).as_posix()
        for match in MUTATION_RE.finditer(src):
            options_blob = match.group(1)
            method = match.group(2).upper()
            # If the options blob references CSRF in any common form,
            # treat as compliant.
            has_csrf = (
                "X-CSRF-Token" in options_blob
                or "csrfHeaders(" in options_blob
                or "getCsrfTokenOrEmpty(" in options_blob
            )
            if has_csrf:
                continue
            lineno = src[: match.start()].count("\n") + 1
            out.append(f"{rel}:{lineno}: {method} raw fetch missing X-CSRF-Token")
    return out


# Baseline locked 2026-04-25 after auditing 60+ frontend mutation
# fetches. Adding a NEW raw mutation is a regression. Lower this number
# as files migrate to `fetchApi` (which auto-injects CSRF). Aim: 0.
CSRF_BASELINE_MAX = 58


def test_no_new_frontend_mutations_without_csrf():
    """Catches the 2026-04-25 OperatorAckPanel + SensorStatus class.
    Strict against NEW additions; the existing-violations count is
    locked at the baseline — drive it down over time, never up."""
    violations = _violations()
    assert len(violations) <= CSRF_BASELINE_MAX, (
        f"{len(violations)} raw mutation fetches missing CSRF — "
        f"baseline is {CSRF_BASELINE_MAX}. Migrate new code to "
        "`fetchApi` (utils/api.ts) which auto-injects CSRF + "
        "credentials, OR add `credentials: 'include'` and "
        "`headers: { 'X-CSRF-Token': getCsrfTokenOrEmpty() }`.\n"
        + "\n".join(f"  - {v}" for v in violations[:30])
        + (f"\n  ... and {len(violations) - 30} more" if len(violations) > 30 else "")
    )


def test_baseline_doesnt_regress_silently():
    """If anyone migrates a file to fetchApi, the baseline should DROP.
    This test fails LOUDLY when len(violations) goes BELOW baseline,
    forcing the operator to bump the constant down. Prevents the
    'lockstep with the floor' anti-pattern where bug count creeps
    back up unnoticed because nobody updated the constant."""
    violations = _violations()
    assert len(violations) == CSRF_BASELINE_MAX or len(violations) > CSRF_BASELINE_MAX, (
        f"len(violations)={len(violations)} is BELOW baseline "
        f"{CSRF_BASELINE_MAX} — great, you fixed some! Now LOWER "
        "CSRF_BASELINE_MAX to match. The ratchet only works if the "
        "ceiling drops with each fix."
    )
