"""CI gate: stdlib logger.* calls must not pass arbitrary kwargs.

P0.1 prod bug 2026-05-01: `_resolve_stale_incidents` in
health_monitor.py used `logger.info("msg", incident_id=...,
incident_type=...)` — but `logger = logging.getLogger("health_monitor")`
is stdlib, NOT structlog. stdlib's `Logger._log()` only accepts the
documented kwargs (`extra`, `exc_info`, `stack_info`, `stacklevel`).
Anything else raises TypeError, the outer try/except swallows it,
and the function aborts before completing its UPDATE chain.

The bug was silent for ≥7 days because the kwarg-laden code path
only fires when there's data to log (zombie cleanup row count > 0).

Banned pattern: in any file that uses `logging.getLogger(...)` and
does NOT use structlog, `logger.<level>("msg", <name>=value)` is
forbidden. Use `logger.<level>("msg", extra={"<name>": value})`
instead.

Allowed kwargs (stdlib-documented): extra, exc_info, stack_info,
stacklevel.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
BACKEND_DIR = REPO_ROOT / "mcp-server" / "central-command" / "backend"

ALLOWED_KWARGS = {"extra", "exc_info", "stack_info", "stacklevel"}

_LOGGER_CALL_PATTERN = re.compile(
    r"""logger\.(?:info|warning|error|debug|critical)\(\s*  # call
        (?:f?["'][^"']*["'])                                # message string
        \s*,\s*                                             # comma
        ([a-zA-Z_]\w*)\s*=                                  # FIRST kwarg name
    """,
    re.VERBOSE | re.MULTILINE,
)


def _scan_file(path: pathlib.Path) -> list[str]:
    """Return list of (line_no, line_text) for offending sites."""
    src = path.read_text()
    if "logging.getLogger" not in src:
        return []
    if "structlog" in src:
        # Module uses structlog; kwargs are valid in that context.
        return []
    findings: list[str] = []
    for m in _LOGGER_CALL_PATTERN.finditer(src):
        kwarg = m.group(1)
        if kwarg in ALLOWED_KWARGS:
            continue
        line_no = src.count("\n", 0, m.start()) + 1
        line = src.splitlines()[line_no - 1]
        findings.append(f"{line_no}: {line.strip()[:120]}")
    return findings


def test_no_stdlib_logger_kwargs():
    """Walk all backend Python (excl tests/, fixtures/, test_*.py
    prefix) and assert no stdlib-logger-with-arbitrary-kwargs sites
    exist. Migration path: replace `logger.info("msg", k=v)` with
    `logger.info("msg", extra={"k": v})` — preserves the structured
    payload AND survives both stdlib + structlog adapters."""
    failures: list[str] = []
    for path in BACKEND_DIR.rglob("*.py"):
        if "tests" in path.parts or "fixtures" in path.parts:
            continue
        if path.name.startswith("test_"):
            continue
        offenders = _scan_file(path)
        if offenders:
            rel = path.relative_to(REPO_ROOT)
            for o in offenders:
                failures.append(f"{rel}:{o}")

    assert not failures, (
        f"Forbidden stdlib-logger-with-kwargs pattern found in "
        f"{len(failures)} location(s). stdlib `Logger._log()` raises "
        f"TypeError on arbitrary kwargs — at runtime, behind the "
        f"outer try/except, this becomes a SILENT bug (P0.1 audit "
        f"finding 2026-05-01). Use `logger.<level>(\"msg\", "
        f"extra={{\"key\": value}})` instead.\n\n"
        + "\n".join(failures)
    )
