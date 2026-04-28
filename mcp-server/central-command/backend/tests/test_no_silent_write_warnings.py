"""Source-level CI gate: no `logger.warning` on DB write failures.

Session 205 invariant: "DB writes log-and-raise (or
log-with-exc_info via logger.error). logger.warning on a write
failure is BANNED — reads may eat exceptions; writes never may."

Round-table 2026-04-28 angle 4 P0: the rule existed but wasn't
enforced. After auditing for the sigauth_observations site
(commit b62c91d2), three more peers were found:
  - agent_api.py:1109 (incident_remediation_steps INSERT)
  - agent_api.py:1787 (sync event INSERT)
  - fleet_updates.py:1490 (fleet_order_completions INSERT)

Plus signature_auth.py:261 (nonces INSERT) and
device_sync.py:601 (credentials UPDATE) caught by broader sweep.

This test fails CI if any new `logger.warning` lands on a string
that names a DB write verb. Tightens the class so future code
can't quietly re-introduce silent write failures.
"""
from __future__ import annotations

import pathlib
import re
from typing import List


REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
BACKEND_DIR = REPO_ROOT / "mcp-server" / "central-command" / "backend"


# Patterns that indicate a DB write failure was logged at WARNING.
# Each is conservative — phrases that could appear in a write-failure
# log line. Add more as new write-verbs surface.
_WARNING_PATTERNS = [
    re.compile(
        r'logger\.warning\s*\(\s*[fF]?["\'][^"\'\)]*'
        r'(?:[Ff]ailed to record|[Ff]ailed to (?:insert|update|delete|persist|write|store|save))',
        re.MULTILINE,
    ),
    re.compile(
        r'logger\.warning\s*\(\s*[fF]?["\'][^"\'\)]*'
        r'(?:nonce.*record|observation.*insert|audit.*write|record.*failed)',
        re.IGNORECASE | re.MULTILINE,
    ),
]


# Files that are NOT backend Python (skip).
_SKIP_DIRS = ("__pycache__", "tests", "scripts", "venv", "node_modules")


def _walk_python_sources() -> List[pathlib.Path]:
    out: List[pathlib.Path] = []
    for p in BACKEND_DIR.rglob("*.py"):
        if any(skip in p.parts for skip in _SKIP_DIRS):
            continue
        out.append(p)
    return out


def test_no_logger_warning_on_db_writes():
    """Grep backend/*.py for logger.warning on DB-write phrases.
    Failures are upgrades-to-error required."""
    offenders: List[str] = []
    for src in _walk_python_sources():
        try:
            text = src.read_text(encoding="utf-8")
        except OSError:
            continue
        for pat in _WARNING_PATTERNS:
            for match in pat.finditer(text):
                lineno = text[: match.start()].count("\n") + 1
                snippet = match.group(0)[:100]
                offenders.append(
                    f"{src.relative_to(REPO_ROOT)}:{lineno}: {snippet!r}"
                )
    assert not offenders, (
        "logger.warning on DB write failure is BANNED (Session 205, "
        "round-table 2026-04-28 P0). Upgrade to logger.error(..., "
        "exc_info=True). Reads may eat exceptions; writes never may.\n\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )
