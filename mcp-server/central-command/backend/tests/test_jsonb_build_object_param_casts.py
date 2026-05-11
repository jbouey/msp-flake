"""Pin gate — every `jsonb_build_object($N, ...)` param MUST be cast.

Session 219 (2026-05-11) caught a silent prod failure where
`journal_api.py:178` was emitting `JOURNAL_UPLOAD_UNSCRUBBED` audit
rows that ALL silently failed to write — asyncpg's prepare phase
can't infer the JSONB-component type for unannotated `$N` params,
and PgBouncer statement-caching makes the inference even worse.

Symptom in prod: `IndeterminateDatatypeError: could not determine
data type of parameter $N`, firing 1×/3-5min for ~months. The
exception was caught in a broad try/except and logged at WARNING,
silently failing every audit row.

Same shape as the `auth.py + execute_with_retry` ::text rule
(Session 199 PgBouncer DuplicatePreparedStatementError class).

What this gate pins (regex over source):
  Every `jsonb_build_object(...)` call that references `$N` (asyncpg
  positional param) MUST have either an explicit `::text` / `::int`
  / `::uuid` / `::bigint` / `::timestamptz` / `::jsonb` / `::float`
  / `::bool` cast on each occurrence, OR be a static literal call
  with no `$N` at all.

Sibling pattern:
  - `test_minio_worm_bucket_validation_pinned.py` (operator-discipline)
  - `test_email_opacity_harmonized.py` (banned-shape)
  - `test_l2_resolution_requires_decision_record.py` (audit-chain)
"""
from __future__ import annotations

import pathlib
import re

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent.parent.parent
_BACKEND = _REPO / "mcp-server" / "central-command" / "backend"

# Recognized cast types — extend if new ones become legitimate.
_ALLOWED_CASTS = {
    "text", "int", "bigint", "uuid", "timestamptz", "timestamp",
    "jsonb", "json", "float", "float8", "real", "bool", "boolean",
    "numeric", "smallint", "interval", "date", "bytea",
}

# Match jsonb_build_object(...) calls spanning multi-line; capture body
# between balanced parens. Use a simple non-greedy approach with a
# safety bound — production SQL strings are bounded.
_JSONB_BUILD = re.compile(r"jsonb_build_object\s*\(", re.MULTILINE)


def _extract_body(src: str, open_idx: int) -> str:
    """Return text inside the matched jsonb_build_object(...) call
    starting at the opening paren index. Balanced paren walk."""
    depth = 0
    i = open_idx
    while i < len(src):
        ch = src[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return src[open_idx + 1:i]
        i += 1
    return src[open_idx + 1:]  # unclosed — pathological, fall through


def _scan_file(path: pathlib.Path) -> list[tuple[str, int, str]]:
    """Return list of (relpath, lineno, snippet) for every offending
    callsite — `$N` reference inside jsonb_build_object body lacking
    `$N::<allowed_cast>` immediately after it."""
    src = path.read_text()
    issues: list[tuple[str, int, str]] = []
    for m in _JSONB_BUILD.finditer(src):
        body_start = m.end() - 1  # position of '('
        body = _extract_body(src, body_start)
        # Find every $N reference in the body
        for pm in re.finditer(r"\$(\d+)", body):
            after = body[pm.end():pm.end() + 30]
            # Cast must be `::<word>` immediately after
            cast_m = re.match(r"::(\w+)", after)
            if not cast_m or cast_m.group(1).lower() not in _ALLOWED_CASTS:
                # Compute line number of the offense
                offense_offset = body_start + 1 + pm.start()
                lineno = src.count("\n", 0, offense_offset) + 1
                snippet = body[max(0, pm.start() - 20):pm.end() + 30].replace("\n", " ")
                issues.append((str(path.name), lineno, snippet))
    return issues


def test_every_jsonb_build_object_param_is_cast():
    """Every `jsonb_build_object($N, ...)` must cast `$N` to an
    explicit type. asyncpg's prepare phase + PgBouncer statement
    caching together cannot infer JSONB-component types reliably;
    Session 219 caught a silent audit-row leak (~1×/3-5min for
    months) caused by exactly this shape.

    Allowed casts: text, int, bigint, uuid, timestamptz, jsonb,
    float, bool, numeric, smallint, interval, date, bytea.

    If you genuinely need a new cast type, add it to _ALLOWED_CASTS
    in this test file with a comment citing the prod path.
    """
    all_issues: list[tuple[str, int, str]] = []
    for py_file in _BACKEND.rglob("*.py"):
        # Skip tests + migrations dir (migrations have their own gates).
        if "/tests/" in str(py_file) or py_file.name.startswith("test_"):
            continue
        if "/migrations/" in str(py_file):
            continue
        all_issues.extend(_scan_file(py_file))

    if all_issues:
        lines = ["jsonb_build_object($N, ...) callsites lacking explicit casts:"]
        for fname, lineno, snippet in all_issues:
            lines.append(f"  {fname}:{lineno}  ...{snippet}...")
        lines.append("")
        lines.append(
            "Each $N MUST have `::text` / `::int` / `::uuid` etc. "
            "immediately after it. Session 219 (commit fbf… in 2026-05-11) "
            "shows the prod symptom: IndeterminateDatatypeError, silent "
            "audit-row leak. Fix: write `$1::text` not `$1`."
        )
        raise AssertionError("\n".join(lines))


def test_synthetic_violation_is_caught():
    """Positive control: synthesize a violation, scan it, confirm
    the gate catches it. Prevents the gate from silently rotting
    (e.g. if someone breaks the regex)."""
    tmp = pathlib.Path("/tmp/test_jsonb_synthetic_bad.py")
    tmp.write_text(
        '''
async def bad():
    await conn.execute(
        """
        UPDATE foo SET meta = jsonb_build_object('site_id', $1)
         WHERE id = $2
        """,
        site_id, foo_id,
    )
'''
    )
    issues = _scan_file(tmp)
    tmp.unlink()
    assert issues, "synthetic violation MUST be caught — gate regex is broken"


def test_synthetic_safe_is_not_flagged():
    """Negative control: a properly-cast callsite is NOT flagged."""
    tmp = pathlib.Path("/tmp/test_jsonb_synthetic_good.py")
    tmp.write_text(
        '''
async def good():
    await conn.execute(
        """
        UPDATE foo SET meta = jsonb_build_object('site_id', $1::text)
         WHERE id = $2::uuid
        """,
        site_id, foo_id,
    )
'''
    )
    issues = _scan_file(tmp)
    tmp.unlink()
    assert not issues, f"properly-cast callsite was FLAGGED: {issues!r}"


def test_no_static_only_jsonb_build_object_is_flagged():
    """Edge case: `jsonb_build_object('key', 'literal_value')` has
    no `$N` at all — must NOT be flagged. (See
    appliance_relocation_api.py:201 — `event_type` literal.)"""
    tmp = pathlib.Path("/tmp/test_jsonb_synthetic_literal.py")
    tmp.write_text(
        '''
async def static_only():
    await conn.execute(
        """
        SELECT * FROM bundles
         WHERE meta @> jsonb_build_object('event_type', 'finalized')
        """,
    )
'''
    )
    issues = _scan_file(tmp)
    tmp.unlink()
    assert not issues, f"static-literal callsite was FLAGGED: {issues!r}"
