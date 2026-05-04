"""Tests for the deferred batch shipped 2026-05-04:
- Email DLQ (mig 272 + _record_email_dlq_failure hook in
  _send_smtp_with_retry).
- notify_users wiring (org_management deprovision now actually fires
  emails when the flag is True).

Each test is structural / source-level — no SMTP or DB live calls.
The contracts pinned here are what regressed silently before this
batch and what the substrate's own observability could not catch.
"""
from __future__ import annotations

import pathlib

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND.parent.parent.parent


def _read(p: pathlib.Path) -> str:
    return p.read_text()


# ─── Email DLQ ────────────────────────────────────────────────────


def test_dlq_migration_present():
    """Migration 272 creates email_send_failures + indexes."""
    mig_dir = _BACKEND / "migrations"
    mig = mig_dir / "272_email_send_failures_dlq.sql"
    assert mig.exists(), (
        "migrations/272_email_send_failures_dlq.sql missing — "
        "_send_smtp_with_retry's DLQ hook will fail every final-failure "
        "write because the table doesn't exist."
    )
    src = mig.read_text()
    for needle in [
        "CREATE TABLE IF NOT EXISTS email_send_failures",
        "label",
        "recipient_count",
        "error_class",
        "retry_count",
        "failed_at",
        "resolved_at",
        "idx_email_send_failures_unresolved",
    ]:
        assert needle in src, (
            f"Migration 272 missing `{needle}` — schema drift between "
            f"the table definition and what _record_email_dlq_failure "
            f"expects to INSERT."
        )


def test_dlq_hook_in_send_smtp_with_retry():
    """Final-failure path of _send_smtp_with_retry MUST call the DLQ
    helper. Substring search is acceptable here because the helper
    name is unique."""
    src = _read(_BACKEND / "email_alerts.py")
    assert "_record_email_dlq_failure(" in src, (
        "_send_smtp_with_retry no longer calls _record_email_dlq_failure. "
        "Final-failure email sends will be invisible to operators again."
    )


def test_dlq_helper_does_not_store_recipient_addresses():
    """Privacy / minimization: the DLQ schema records recipient_count
    only (NOT addresses). If a future refactor accidentally adds
    recipient address persistence, customer/admin emails would land
    in an operational table that's NOT audit-class — easy to leak."""
    src = _read(_BACKEND / "email_alerts.py")
    helper_start = src.find("def _record_email_dlq_failure(")
    assert helper_start >= 0
    helper_body = src[helper_start:helper_start + 3000]
    # The INSERT statement should reference recipient_count, not
    # individual addresses or the recipients list.
    assert "recipient_count" in helper_body, (
        "DLQ helper missing recipient_count column — schema drift."
    )
    # Sanity: helper should NOT serialize the recipients list into
    # error_message or any other column.
    assert "json.dumps(recipients" not in helper_body, (
        "DLQ helper appears to serialize recipient addresses into a "
        "DB column — privacy / minimization violation. Use "
        "recipient_count only."
    )


def test_dlq_helper_swallows_all_exceptions():
    """Best-effort contract: DLQ write failure must NOT amplify the
    original send failure. The whole helper body sits inside try/except."""
    src = _read(_BACKEND / "email_alerts.py")
    helper_start = src.find("def _record_email_dlq_failure(")
    assert helper_start >= 0
    helper_body = src[helper_start:helper_start + 3000]
    assert "except Exception" in helper_body, (
        "DLQ helper missing the outer except — could now raise from "
        "inside _send_smtp_with_retry's final-failure path."
    )


# ─── notify_users wiring ──────────────────────────────────────────


def test_deprovision_collects_notify_recipients():
    """Deprovision endpoint must SELECT active client_user emails when
    notify_users=True. Pre-fix the flag was logged but never fired."""
    src = _read(_BACKEND / "org_management.py")
    # Look for the gate + the SELECT
    assert "if req.notify_users:" in src, (
        "deprovision endpoint missing the notify_users gate — flag "
        "would still be inert."
    )
    assert "FROM client_users" in src, (
        "deprovision endpoint not selecting client_users at all."
    )
    assert "is_active = true" in src, (
        "deprovision endpoint not filtering on is_active — would "
        "email already-deactivated users."
    )


def test_deprovision_send_helper_present():
    """The actual SMTP fan-out helper must exist + be invoked."""
    src = _read(_BACKEND / "org_management.py")
    assert "async def _send_deprovision_notices(" in src, (
        "_send_deprovision_notices helper missing — emails would not "
        "actually be sent even after the SELECT lands."
    )
    assert "_send_deprovision_notices(" in src, (
        "deprovision endpoint not calling the send helper."
    )


def test_deprovision_send_helper_uses_per_recipient_try_except():
    """One bad address must not block the rest. Per-recipient try/except
    + logger.error per-failure."""
    src = _read(_BACKEND / "org_management.py")
    helper_start = src.find("async def _send_deprovision_notices(")
    assert helper_start >= 0
    helper_body = src[helper_start:helper_start + 3000]
    assert "for rec in recipients:" in helper_body, (
        "_send_deprovision_notices missing per-recipient loop."
    )
    assert "logger.error" in helper_body, (
        "_send_deprovision_notices missing logger.error on failure — "
        "per CLAUDE.md no-silent-write rule, send failures must alert."
    )


def test_deprovision_send_happens_after_txn_commit():
    """SMTP I/O must NOT happen inside `async with conn.transaction():`
    — would hold a DB transaction open across the network call. The
    notify_recipients list is collected inside the txn; the actual
    send fires AFTER the txn block.

    Heuristic: locate the txn-block opener line, find its closing
    line by indentation, then assert the helper call's line number
    is GREATER than the txn-block closer.
    """
    full_src = _read(_BACKEND / "org_management.py")
    lines = full_src.splitlines()

    # Find the deprovision function definition line
    fn_line_idx = next(
        (i for i, ln in enumerate(lines)
         if ln.startswith("async def deprovision_org(")),
        None,
    )
    assert fn_line_idx is not None, "deprovision_org function not found"

    # Within the function, find `async with conn.transaction():` opener
    txn_open_idx = None
    txn_open_indent = None
    for i in range(fn_line_idx, min(fn_line_idx + 200, len(lines))):
        stripped = lines[i].lstrip()
        if stripped.startswith("async with conn.transaction():"):
            txn_open_idx = i
            txn_open_indent = len(lines[i]) - len(stripped)
            break
    assert txn_open_idx is not None, (
        "async with conn.transaction(): not found in deprovision_org"
    )

    # Find the line where the txn block closes — first line after
    # the opener whose indent is <= txn_open_indent (and non-blank).
    txn_close_idx = None
    for i in range(txn_open_idx + 1, len(lines)):
        ln = lines[i]
        if not ln.strip():
            continue
        indent = len(ln) - len(ln.lstrip())
        if indent <= txn_open_indent:
            txn_close_idx = i
            break
    assert txn_close_idx is not None, (
        "could not find txn block close — file truncated?"
    )

    # Find the helper call line inside the function body
    helper_call_idx = next(
        (i for i in range(fn_line_idx, len(lines))
         if "await _send_deprovision_notices(" in lines[i]),
        None,
    )
    assert helper_call_idx is not None, (
        "await _send_deprovision_notices(...) not found in function"
    )

    # The call MUST appear strictly after the txn block closes.
    assert helper_call_idx >= txn_close_idx, (
        f"_send_deprovision_notices call at line {helper_call_idx + 1} "
        f"is BEFORE the txn block closes at line {txn_close_idx + 1}. "
        f"This would hold a DB transaction open across SMTP I/O."
    )


def test_deprovision_notice_uses_neutral_legal_language():
    """Per CLAUDE.md Session 199: the body must NOT use ensures /
    prevents / protects / guarantees / 100% / audit-ready. The
    framing must be operator-neutral — Osiris is the substrate,
    the MSP / admin is the actor."""
    src = _read(_BACKEND / "org_management.py")
    helper_start = src.find("async def _send_deprovision_notices(")
    assert helper_start >= 0
    helper_body = src[helper_start:helper_start + 3000]
    banned = ["ensures", "prevents", "protects", "guarantees",
              "audit-ready", "PHI never leaves", "100%"]
    for word in banned:
        assert word not in helper_body, (
            f"deprovision notice body contains banned word `{word}` "
            f"per CLAUDE.md Session 199 legal-language rules."
        )
