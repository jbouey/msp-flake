"""CI gate: every SMTP send goes through email_alerts._send_smtp_with_retry.

Task #12 SMTP consolidation 2026-05-05. Pre-fix: 6 raw smtplib send
sites bypassed the central retry+branding+DLQ helper:
  - email_service.py × 3 (invite, generic, password reset)
  - portal.py × 2 (magic link helper + inline fallback)
  - escalation_engine.py × 1

Consequences of bypass: no retry on transient failures, no DLQ row on
final failure (mig 272 invisible to these paths), no partner branding,
no email_dlq_growing substrate invariant coverage.

This gate ratchets `smtplib.SMTP(` callsites to BASELINE_MAX = 0
outside the canonical helper in email_alerts.py. New regressions fail
CI.
"""
from __future__ import annotations

import pathlib
import re

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND.parent.parent.parent

# Files allowed to import smtplib + call SMTP(. The helper itself
# is the ONLY canonical send. Test files are exempted (they may mock).
_EXEMPT_PATHS = {
    "email_alerts.py",
}

# Ratchet baseline. 0 = no raw smtplib calls outside the helper after
# task #12 consolidation. Increment ONLY if a new SMTP transport is
# legitimately added; decrement only via further consolidation.
BASELINE_MAX = 0

# Pattern: smtplib.SMTP( with optional whitespace.
_SMTP_CALL_PATTERN = re.compile(r"\bsmtplib\s*\.\s*SMTP\s*\(")


def _scan_backend() -> list[tuple[pathlib.Path, int]]:
    """Return list of (file, line_number) for every smtplib.SMTP( call
    in backend/ outside _EXEMPT_PATHS. Skips test/, fixtures/, comments."""
    hits = []
    for path in sorted(_BACKEND.rglob("*.py")):
        rel = path.relative_to(_BACKEND)
        parts = rel.parts
        if parts[0] in ("tests", "__pycache__", "fixtures", "scripts"):
            continue
        if path.name in _EXEMPT_PATHS:
            continue
        try:
            content = path.read_text()
        except (UnicodeDecodeError, IsADirectoryError):
            continue
        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if _SMTP_CALL_PATTERN.search(line):
                hits.append((path, i))
    return hits


def test_no_raw_smtplib_calls_outside_helper():
    """Ratchet: no `smtplib.SMTP(` calls outside email_alerts.py.
    Pre-task-#12 baseline was 6 sites; post-fix is 0."""
    hits = _scan_backend()
    if len(hits) > BASELINE_MAX:
        formatted = "\n".join(
            f"  - {p.relative_to(_REPO_ROOT)}:{n}" for p, n in hits
        )
        pytest.fail(
            f"Found {len(hits)} `smtplib.SMTP(` callsite(s) outside "
            f"email_alerts.py (baseline {BASELINE_MAX}). The central "
            f"helper `email_alerts._send_smtp_with_retry()` MUST be "
            f"the only path — it provides retry + DLQ + branding + "
            f"invariant coverage. A new raw SMTP path bypasses all "
            f"four and reopens the gap task #12 just closed.\n\n"
            f"Found at:\n{formatted}\n\n"
            f"Fix: replace `with smtplib.SMTP(...) as server: ...` "
            f"with `_send_smtp_with_retry(msg, recipients, label=..., "
            f"from_address=...)`. See "
            f".agent/plans/12-smtp-consolidation-roundtable-2026-05-05.md"
        )


def test_email_alerts_remains_canonical_send_path():
    """Pin email_alerts._send_smtp_with_retry as the helper that
    actually contains the smtplib.SMTP() call. If a refactor moves
    the canonical send elsewhere, this test catches it."""
    src = (_BACKEND / "email_alerts.py").read_text()
    assert _SMTP_CALL_PATTERN.search(src), (
        "email_alerts.py no longer contains smtplib.SMTP() — the "
        "canonical send path moved. Update _EXEMPT_PATHS in this "
        "test to reflect the new home, OR move the canonical back."
    )
    helper_start = src.find("def _send_smtp_with_retry(")
    assert helper_start >= 0
    helper_body = src[helper_start:helper_start + 5000]
    assert _SMTP_CALL_PATTERN.search(helper_body), (
        "smtplib.SMTP() call moved out of _send_smtp_with_retry."
    )


def test_consolidated_callers_use_helper():
    """Pin the 3 files that were consolidated in task #12 — they MUST
    still import + call _send_smtp_with_retry. Catches an accidental
    revert that re-introduces raw smtplib at a known site."""
    expected_callers = {
        "email_service.py",
        "portal.py",
        "escalation_engine.py",
    }
    for fname in expected_callers:
        src = (_BACKEND / fname).read_text()
        assert "_send_smtp_with_retry" in src, (
            f"{fname} no longer references _send_smtp_with_retry — "
            f"task #12 consolidation regressed for this file."
        )


def test_from_address_parameter_present():
    """The from_address parameter added in task #12 is what makes the
    consolidation possible without forcing all email through alerts@.
    Pin the parameter so it can't get refactored away."""
    src = (_BACKEND / "email_alerts.py").read_text()
    assert "from_address: Optional[str] = None" in src, (
        "from_address parameter missing from _send_smtp_with_retry "
        "signature. Without it, transactional client/admin email "
        "(noreply@) can't be distinguished from operator-class email "
        "(alerts@) — DKIM-correct envelope sender + display From "
        "split would be lost."
    )
