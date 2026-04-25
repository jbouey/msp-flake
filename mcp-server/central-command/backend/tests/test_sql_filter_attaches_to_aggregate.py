"""Static check: every `FILTER (WHERE ...)` clause in backend Python
SQL strings attaches directly to an aggregate function call.

`EXTRACT(EPOCH FROM (NOW() - MIN(x))) FILTER (WHERE ...)` is a
PostgreSQL syntax error — FILTER is only valid on aggregate function
calls, not on arbitrary scalar expressions like EXTRACT. The runtime
error is `PostgresSyntaxError: syntax error at or near "FILTER"`.

This test caught the regression on 2026-04-25 in
`prometheus_metrics.py::prometheus_metrics()` — the OTS-proofs query
wrote `EXTRACT(EPOCH FROM (NOW() - MIN(submitted_at))) FILTER (...)`
and was firing every minute in prod. Fix: move FILTER inside, attached
to MIN: `EXTRACT(EPOCH FROM (NOW() - MIN(submitted_at) FILTER (...)))`.

The check is regex-based: find every `) FILTER (`, then walk back to
the matching open paren, then verify the token immediately before that
open paren is one of the canonical Postgres aggregates.
"""
from __future__ import annotations

import pathlib
import re
from typing import List

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
BACKEND_DIR = REPO_ROOT / "mcp-server" / "central-command" / "backend"


# Whitelist of aggregate-like functions that legitimately accept FILTER
AGGREGATES = {
    "count", "sum", "min", "max", "avg", "array_agg", "string_agg",
    "json_agg", "jsonb_agg", "json_object_agg", "jsonb_object_agg",
    "bit_and", "bit_or", "bool_and", "bool_or", "every",
    "stddev", "variance", "stddev_pop", "stddev_samp",
    "var_pop", "var_samp", "percentile_cont", "percentile_disc",
    "mode", "rank", "dense_rank", "row_number",
}

FILTER_RE = re.compile(r"\)\s*FILTER\s*\(", re.IGNORECASE)
TOKEN_BEFORE_PAREN_RE = re.compile(r"([a-zA-Z_][\w]*)\s*$")
# Strip Python `#` line comments BEFORE scanning, so a docstring or
# comment that names the antipattern (for the maintainer's benefit)
# doesn't fail this test.
PY_COMMENT_RE = re.compile(r"(?m)^([^'\"#\n]*?)#[^\n]*")


def _strip_python_line_comments(src: str) -> str:
    """Replace each `# ...` tail with spaces, preserving line offsets so
    reported line numbers still match the original file."""
    out = []
    for line in src.split("\n"):
        # Don't touch lines inside triple-quoted SQL strings — but we
        # can't track that without parsing; the simple heuristic of
        # "if the # is preceded by an unmatched quote on this line,
        # leave it" handles the common case.
        in_str = False
        quote = None
        for i, ch in enumerate(line):
            if not in_str and ch in ("'", '"'):
                in_str = True
                quote = ch
            elif in_str and ch == quote and (i == 0 or line[i-1] != "\\"):
                in_str = False
                quote = None
            elif not in_str and ch == "#":
                line = line[:i] + " " * (len(line) - i)
                break
        out.append(line)
    return "\n".join(out)


def _backend_py_files() -> List[pathlib.Path]:
    out: List[pathlib.Path] = []
    for p in BACKEND_DIR.rglob("*.py"):
        if any(skip in p.parts for skip in (
            "tests", "archived", "venv", "__pycache__", "node_modules",
        )):
            continue
        out.append(p)
    return out


def _matching_open_paren(text: str, close_idx: int) -> int:
    """Walk back from `close_idx` (a `)` position) to find the matching `(`.
    Returns -1 if unbalanced (indicates we're scanning across statement
    boundaries — skip the check for that match)."""
    depth = 1
    i = close_idx - 1
    while i >= 0:
        ch = text[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            depth -= 1
            if depth == 0:
                return i
        i -= 1
    return -1


def test_every_filter_clause_attaches_to_aggregate():
    """The headline check. Every `) FILTER (` in backend Python must be
    preceded by a canonical Postgres aggregate function name.

    Regression target: prometheus_metrics.py 2026-04-25 OTS-proofs
    query — `EXTRACT(...) FILTER (...)` 500'd every minute.
    """
    failures: List[str] = []
    for py_path in _backend_py_files():
        try:
            raw = py_path.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = py_path.relative_to(REPO_ROOT)
        src = _strip_python_line_comments(raw)
        for match in FILTER_RE.finditer(src):
            close_idx = match.start()  # position of `)`
            open_idx = _matching_open_paren(src, close_idx)
            if open_idx < 0:
                # Unbalanced parens within the scan window (likely a
                # multi-statement string boundary). Skip — too many
                # false positives if we try to be clever.
                continue
            tok_match = TOKEN_BEFORE_PAREN_RE.search(src[:open_idx])
            if not tok_match:
                continue
            token = tok_match.group(1).lower()
            if token in AGGREGATES:
                continue
            lineno = src[:close_idx].count("\n") + 1
            failures.append(
                f"{rel}:{lineno}: FILTER clause attached to '{token}(...)' — "
                f"only aggregate functions accept FILTER. Move FILTER "
                f"inside, attached to a COUNT/MIN/MAX/SUM/etc."
            )
    assert not failures, (
        f"{len(failures)} `FILTER (WHERE ...)` clauses on non-aggregate "
        "expressions. Each one will raise PostgresSyntaxError at runtime.\n"
        + "\n".join(f"  - {f}" for f in failures)
    )
