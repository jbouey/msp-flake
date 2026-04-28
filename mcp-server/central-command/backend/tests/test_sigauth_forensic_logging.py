"""Forensic-event handler test for sigauth `unknown_pubkey` path.

Defends the Phase-4 evidence path against future refactor (task #168,
Session 211 Phase 4 QA). The Phase 2 instrumentation added a
`logger.error("sigauth_unknown_pubkey", extra={...})` at
`signature_auth.py:323` so the log shipper alerts on the rejection
moment + captures ts_iso/nonce/sig_len/headers_present for time-
correlation against PgBouncer pool stats and daemon checkin logs.

Without this test, a refactor that drops the logger.error or renames
the keys silently destroys the only evidence path for the rare
`unknown_pubkey` jitter we have not yet root-caused. The QA verdict
explicitly required this gate before deferring the fix.

This test is hermetic — uses the same `_fake_conn` shim as
test_signature_auth.py and pytest's `caplog` fixture.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import signature_auth  # type: ignore[import-not-found]


def _ts_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fake_request(method: str, path: str, headers: dict):
    req = MagicMock()
    req.method = method
    req.url = MagicMock()
    req.url.path = path
    lower = {k.lower(): v for k, v in headers.items()}

    class CaseInsensitiveHeaders(dict):
        def get(self, key, default=None):
            return super().get(key.lower(), default)

    req.headers = CaseInsensitiveHeaders(lower)
    return req


def _fake_conn_no_row():
    """Fake conn whose every fetchrow returns None — forces
    `_resolve_pubkey` down the early-return branch that triggers the
    forensic logger.error."""
    conn = AsyncMock()

    async def fetchrow(query, *args):
        return None

    async def execute(query, *args):
        return ""

    conn.fetchrow = fetchrow
    conn.execute = execute
    return conn


@pytest.mark.asyncio
async def test_unknown_pubkey_logs_forensic_error(caplog):
    """When `_resolve_pubkey` returns None (both site_appliances row
    AND v_current_appliance_identity miss), `verify_appliance_signature`
    MUST emit `logger.error("sigauth_unknown_pubkey", extra={...})`
    with `ts_iso`, `nonce_hex`, `sig_len`, and `headers_present` in the
    extra dict. Captured here so a future refactor can't silently drop
    this evidence path. (#168, Session 211 Phase 4 QA)
    """
    conn = _fake_conn_no_row()
    ts_iso = _ts_now()
    nonce = "ab" * 16
    sig_b64 = "Z" * 86  # length-validated only after this branch fires
    req = _fake_request(
        "POST", "/api/appliances/checkin",
        {
            "X-Appliance-Signature": sig_b64,
            "X-Appliance-Timestamp": ts_iso,
            "X-Appliance-Nonce": nonce,
        },
    )

    with caplog.at_level(logging.ERROR, logger="signature_auth"):
        result = await signature_auth.verify_appliance_signature(
            req, conn=conn,
            site_id="north-valley-branch-2",
            mac_address="7C:D3:0A:7C:55:18",
            body_bytes=b"{}",
        )

    # Verify the result first so a refactor that breaks the reason
    # string also fails the test (not just the log).
    assert result.reason == "unknown_pubkey", (
        f"expected unknown_pubkey, got {result.reason!r}"
    )
    assert not result.pubkey_fingerprint, (
        "fingerprint must be empty when row is missing — confirms we "
        "hit the early-return branch (not the late pubkey-decode branch). "
        "Production sigauth_observations table reflects this as NULL."
    )

    forensic = [
        r for r in caplog.records
        if r.name == "signature_auth"
        and r.levelno == logging.ERROR
        and "sigauth_unknown_pubkey" in r.getMessage()
    ]
    assert forensic, (
        "logger.error('sigauth_unknown_pubkey', ...) was NOT emitted. "
        "The forensic evidence path that task #168 depends on has been "
        "broken — restore it at signature_auth.py:323 before merging."
    )
    record = forensic[-1]

    # The four extra keys named in the QA contract. Each must be
    # present and have the right semantic value — a refactor that
    # renames a key (e.g. ts_iso → timestamp) breaks the log-shipper
    # alert + the cross-system join, so we pin them by exact name.
    for required_key in ("site_id", "mac_address", "ts_iso", "nonce_hex", "sig_len", "headers_present"):
        assert hasattr(record, required_key), (
            f"forensic log record missing required extra key {required_key!r}. "
            f"Available extras: {sorted(set(record.__dict__) - {'msg', 'args', 'levelname', 'levelno', 'pathname', 'filename', 'module', 'exc_info', 'exc_text', 'stack_info', 'lineno', 'funcName', 'created', 'msecs', 'relativeCreated', 'thread', 'threadName', 'processName', 'process', 'name', 'message'})}"
        )

    # Semantic checks — values must match what was sent.
    assert record.site_id == "north-valley-branch-2"
    assert record.mac_address == "7C:D3:0A:7C:55:18"
    assert record.ts_iso == ts_iso
    assert record.nonce_hex == nonce
    assert record.sig_len == len(sig_b64)
    assert record.headers_present is True
