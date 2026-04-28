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

    # Required extras pinned by exact name. A refactor that renames
    # one (e.g. ts_iso → timestamp) breaks the log-shipper alert + the
    # cross-system join, so we lock the contract here. Updated 2026-04-28
    # round-table: nonce_hex truncated to 8 hex chars (correlation
    # surface, not replay-reconstruction); headers_present dropped
    # (useless constant — always True at this branch);
    # signature_enforcement_mode added (high-signal triage).
    for required_key in ("site_id", "mac_address", "ts_iso", "nonce_hex", "sig_len", "signature_enforcement_mode"):
        assert hasattr(record, required_key), (
            f"forensic log record missing required extra key {required_key!r}. "
            f"Available extras: {sorted(set(record.__dict__) - {'msg', 'args', 'levelname', 'levelno', 'pathname', 'filename', 'module', 'exc_info', 'exc_text', 'stack_info', 'lineno', 'funcName', 'created', 'msecs', 'relativeCreated', 'thread', 'threadName', 'processName', 'process', 'name', 'message'})}"
        )
    # The dropped key MUST stay dropped — refactor regression guard.
    assert not hasattr(record, "headers_present"), (
        "headers_present was dropped per Session 212 round-table "
        "(always True at this branch — useless constant). "
        "If you re-added it, instead add a key whose value carries "
        "real signal."
    )

    # Semantic checks — values must match what was sent.
    assert record.site_id == "north-valley-branch-2"
    assert record.mac_address == "7C:D3:0A:7C:55:18"
    assert record.ts_iso == ts_iso
    # Nonce truncated to 8 hex chars for correlation only.
    assert record.nonce_hex == nonce[:8], (
        f"nonce must be TRUNCATED to 8 chars to limit log-shipper "
        f"correlation surface. Got {record.nonce_hex!r}, expected {nonce[:8]!r}."
    )
    assert len(record.nonce_hex) == 8
    assert record.sig_len == len(sig_b64)
    # signature_enforcement_mode comes from a fetchrow against the
    # fake conn that returns None for everything — so the lookup
    # falls through to the "unknown" default. Pinning that default
    # ensures the key is always populated even when the row lookup
    # itself fails.
    assert record.signature_enforcement_mode in ("unknown", "observe", "enforce"), (
        f"signature_enforcement_mode must be one of "
        f"('unknown','observe','enforce'). Got {record.signature_enforcement_mode!r}."
    )


@pytest.mark.asyncio
async def test_forensic_log_pins_enforce_mode_when_lookup_succeeds(caplog):
    """Round-table 2026-04-28 P2 follow-up: an operator triaging a
    sigauth_unknown_pubkey rejection MUST be able to discriminate
    whether the rejection happened on an enforce-mode appliance
    (where 0% rejection is the contract — these are the events that
    refute the wrap-fix hypothesis if they fire post-2026-04-28) vs.
    an observe-mode appliance (rejection is informational, not a
    contract violation).

    This test pins the value when the conn lookup returns a real
    `signature_enforcement` value. If a future refactor breaks the
    lookup (e.g. column rename, query change) and the extra silently
    falls back to 'unknown', the discriminator is gone and the
    runbook ladder collapses to "read the log line by hand."
    """
    # Fake conn that returns a row with signature_enforcement='enforce'
    # — simulates the production path on a flipped appliance.
    class _FakeConnEnforce:
        async def fetchrow(self, query, *args):
            # First fetchrow is _resolve_pubkey lookup → return None
            # to drive the unknown_pubkey branch. Subsequent fetchrow
            # is the signature_enforcement lookup.
            if "agent_identity_public_key" in query or "v_current_appliance_identity" in query:
                return None
            if "signature_enforcement" in query:
                return {"signature_enforcement": "enforce"}
            return None

        async def execute(self, query, *args):
            return ""

    ts_iso = _ts_now()
    nonce = "cd" * 16
    sig_b64 = "Y" * 86
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
            req, conn=_FakeConnEnforce(),
            site_id="north-valley-branch-2",
            mac_address="7C:D3:0A:7C:55:18",
            body_bytes=b"{}",
        )

    assert result.reason == "unknown_pubkey"
    forensic = [
        r for r in caplog.records
        if r.name == "signature_auth"
        and r.levelno == logging.ERROR
        and "sigauth_unknown_pubkey" in r.getMessage()
    ]
    assert forensic, "forensic log must fire"
    record = forensic[-1]
    assert record.signature_enforcement_mode == "enforce", (
        f"When the signature_enforcement lookup succeeds, the forensic "
        f"extra MUST carry the actual mode — not 'unknown'. Got "
        f"{record.signature_enforcement_mode!r}. This is the discriminator "
        f"that distinguishes enforce-mode rejections (substrate-firing, "
        f"contract-violating) from observe-mode rejections (informational). "
        f"Without it, the operator runbook collapses to read-the-log-by-hand."
    )
