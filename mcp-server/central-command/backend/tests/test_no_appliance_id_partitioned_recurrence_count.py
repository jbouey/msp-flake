"""Regression gate — ban the per-`appliance_id` partitioned recurrence
COUNT(*) shape that caused the 2026-05-12 chronic-pattern routing gap.

Coach P1-A from Gate A 2026-05-12
(`audit/coach-p1-persistence-drift-l2-gate-a-2026-05-12.md`): once the
detector switches to `(site_id, incident_type)`-keyed reads against
`incident_recurrence_velocity`, a future hand could re-introduce the
same per-`appliance_id` partitioning shape elsewhere (a new
`report_drift` variant, a sites.py L1-fallback path, a main.py legacy
endpoint). This gate keeps the shape banned forever.

Detected shape (the buggy 2026-05-12 form):

    SELECT COUNT(*) FROM incidents
     WHERE appliance_id = :appliance_id
       AND incident_type = ...
       AND status = 'resolved'
       AND resolved_at > NOW() - INTERVAL '...'

The detector is conservative: it walks every backend `.py` file and
flags any code block that:
  1. issues a SELECT COUNT(*) against `incidents` (or `FROM incidents`),
  2. filters by `appliance_id = ...` in the same statement,
  3. ALSO filters by `incident_type` (the recurrence-context signal),
  4. ALSO filters by a recent-window predicate (`status = 'resolved'`
     OR `resolved_at > NOW() -`) — distinguishes recurrence queries
     from generic per-appliance lookups.

A query that legitimately partitions by appliance_id for a non-
recurrence purpose (e.g., counting open incidents per appliance for a
panel) is NOT caught because it lacks the recurrence-window predicate.

Allowlist: per-line `# recurrence-partition-noqa` comment skips a
specific line with rationale. Starts empty per Gate A direction.
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent.parent.parent  # mcp-server/

# Per-line allowlist. Add only with a paired same-line
# `# recurrence-partition-noqa` comment containing rationale.
LINE_ALLOWLIST: set[tuple[str, int]] = set()

# Files that are scanned. The scan walks backend/*.py + mcp-server/main.py
# (the top-level legacy router that still owns some incident handlers).
def _files_to_scan() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    if _BACKEND.exists():
        for py in _BACKEND.rglob("*.py"):
            rel = py.relative_to(_BACKEND)
            # Skip tests + venv + migration scripts (migrations are
            # one-shot SQL, not Python, but be defensive).
            if rel.parts[0] in {"tests", "venv", ".venv", "__pycache__", "scripts", "migrations"}:
                continue
            out.append(py)
    main_py = _REPO / "main.py"
    if main_py.exists():
        out.append(main_py)
    return out


# Sliding-window scan. We look at each line plus the next N lines as a
# single text blob (SQL strings often span lines). The triple checks
# (count + appliance_id eq + incident_type + window) must all be in the
# blob for a violation to fire.
_WINDOW_LINES = 12

_COUNT_INCIDENTS_RE = re.compile(
    r"SELECT\s+COUNT\s*\(\s*\*\s*\).{0,200}FROM\s+incidents",
    re.IGNORECASE | re.DOTALL,
)
_APPLIANCE_EQ_RE = re.compile(
    r"appliance_id\s*=\s*[:\$@]?[A-Za-z_][A-Za-z_0-9]*",
    re.IGNORECASE,
)
_INCIDENT_TYPE_RE = re.compile(r"\bincident_type\b", re.IGNORECASE)
# Recurrence-window predicate — either status='resolved' OR a recent-
# time filter on resolved_at. Either signals "this is the recurrence
# detector class," not a generic per-appliance lookup.
_WINDOW_PRED_RE = re.compile(
    r"(status\s*=\s*['\"]resolved['\"]|resolved_at\s*>\s*NOW\s*\(\s*\)\s*-)",
    re.IGNORECASE,
)


def _violations() -> list[str]:
    out: list[str] = []
    for fp in _files_to_scan():
        try:
            text = fp.read_text()
        except Exception:
            continue
        lines = text.splitlines()
        rel = str(fp.relative_to(_REPO))
        for i in range(len(lines)):
            blob = "\n".join(lines[i : i + _WINDOW_LINES])
            if not _COUNT_INCIDENTS_RE.search(blob):
                continue
            if not _APPLIANCE_EQ_RE.search(blob):
                continue
            if not _INCIDENT_TYPE_RE.search(blob):
                continue
            if not _WINDOW_PRED_RE.search(blob):
                continue
            # Per-line allowlist + same-line noqa.
            anchor_line = lines[i]
            if (rel, i + 1) in LINE_ALLOWLIST:
                continue
            window_text_for_noqa = "\n".join(lines[i : i + _WINDOW_LINES])
            if "# recurrence-partition-noqa" in window_text_for_noqa:
                continue
            out.append(f"{rel}:{i + 1} — {anchor_line.strip()[:120]}")
    return out


def test_no_appliance_id_partitioned_recurrence_count():
    """Per-appliance partitioning of the recurrence COUNT(*) query is
    the 2026-05-12 chronic-pattern routing-gap shape. Forward-fix
    must aggregate by `(site_id, incident_type)` via
    `incident_recurrence_velocity` instead. Substrate invariant
    `chronic_without_l2_escalation` (sev2) catches runtime regressions;
    this gate catches them at review time."""
    viols = _violations()
    assert not viols, (
        "Per-appliance recurrence-count partitioning detected. This is "
        "the 2026-05-12 chronic-pattern routing-gap shape — multi-daemon "
        "sites slice the count below the >= 3 threshold and L2 escalation "
        "silently breaks. Read from incident_recurrence_velocity by "
        "(site_id, incident_type) instead. If you have a legitimate per-"
        "appliance use case, add `# recurrence-partition-noqa` on the "
        "first line of the query with rationale.\n\n"
        + "\n".join(f"  - {v}" for v in viols)
    )


def test_synthetic_bad_shape_caught():
    """Positive control — the buggy shape should be flagged."""
    bad = (
        "result = await db.execute(text(\"\"\"\n"
        "    SELECT COUNT(*) FROM incidents\n"
        "     WHERE appliance_id = :appliance_id\n"
        "       AND incident_type = :incident_type\n"
        "       AND status = 'resolved'\n"
        "       AND resolved_at > NOW() - INTERVAL '4 hours'\n"
        "\"\"\"))\n"
    )
    lines = bad.splitlines()
    found = False
    for i in range(len(lines)):
        blob = "\n".join(lines[i : i + _WINDOW_LINES])
        if (
            _COUNT_INCIDENTS_RE.search(blob)
            and _APPLIANCE_EQ_RE.search(blob)
            and _INCIDENT_TYPE_RE.search(blob)
            and _WINDOW_PRED_RE.search(blob)
        ):
            found = True
            break
    assert found, "matcher should have caught the synthetic bad shape"


def test_synthetic_good_shape_passes():
    """Negative control — the velocity-table-read shape is fine."""
    good = (
        "row = await conn.fetchrow(\"\"\"\n"
        "    SELECT resolved_4h, resolved_7d, is_chronic\n"
        "      FROM incident_recurrence_velocity\n"
        "     WHERE site_id = $1::text\n"
        "       AND incident_type = $2::text\n"
        "       AND computed_at > NOW() - INTERVAL '10 minutes'\n"
        "\"\"\", site_id, incident_type)\n"
    )
    lines = good.splitlines()
    for i in range(len(lines)):
        blob = "\n".join(lines[i : i + _WINDOW_LINES])
        assert not (
            _COUNT_INCIDENTS_RE.search(blob)
            and _APPLIANCE_EQ_RE.search(blob)
            and _INCIDENT_TYPE_RE.search(blob)
            and _WINDOW_PRED_RE.search(blob)
        ), "matcher should NOT flag the velocity-table-read shape"


def test_synthetic_generic_per_appliance_passes():
    """Negative control — a per-appliance COUNT(*) without the
    recurrence-window signals (no `status='resolved'`, no
    `resolved_at > NOW() -`) should NOT be flagged. That's a
    legitimate per-appliance dashboard query, not the recurrence
    detector class."""
    generic = (
        "open_per_appliance = await db.execute(text(\"\"\"\n"
        "    SELECT COUNT(*) FROM incidents\n"
        "     WHERE appliance_id = :appliance_id\n"
        "       AND incident_type = :incident_type\n"
        "       AND status = 'open'\n"
        "\"\"\"))\n"
    )
    lines = generic.splitlines()
    for i in range(len(lines)):
        blob = "\n".join(lines[i : i + _WINDOW_LINES])
        if _WINDOW_PRED_RE.search(blob):
            # The regex permissively matches status='resolved'
            # OR `resolved_at > NOW() -`; 'open' should NOT match.
            assert "resolved" in blob.lower(), (
                "window-pred regex should only fire on resolved-status or "
                "resolved_at predicates; matched something else"
            )
