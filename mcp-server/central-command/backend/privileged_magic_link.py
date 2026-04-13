"""Magic-link tokens for privileged-access approval (Phase 14 T2.1).

HMAC-signed, single-use, 30-minute-TTL tokens embedded in the notifier
email's Approve / Reject URLs. Consumption requires a valid token AND
an authenticated client session matching the token's target_user_email —
so the cryptographic chain of custody is preserved (token is a deep-
link convenience; session-auth is still the attested actor).

Design:
  token format:    <token_id>.<hmac_hex>
    token_id        16 random bytes, hex
    hmac_hex        HMAC-SHA256 over canonical payload
  canonical payload:
    token_id ":" request_id ":" action ":" target_user_email ":" exp_unix

  exp default 30 minutes from mint.
  Single-use: privileged_access_magic_links.consumed_at tracked.
  Key: derived from server signing key so no new secret to manage.

Security invariants:
  - Token alone is NOT sufficient to approve — caller must ALSO be
    logged in as target_user_email at the point of consumption.
  - Expired tokens are rejected even if present in DB.
  - Consumed tokens reject on second use.
  - Action mismatch (token says 'approve', caller POSTs 'reject') → reject.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import pathlib
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)


SIGNING_KEY_PATH = os.getenv("SIGNING_KEY_FILE", "/app/secrets/signing.key")
# Phase 15 A-spec hygiene — defense in depth: prefer a dedicated magic-
# link HMAC secret when the operator provisions one, so a leak of the
# Ed25519 signing key (which signs fleet orders + attestation bundles)
# does not also enable forgery of magic-link approval tokens. Falls
# back to deriving from signing.key when unset, preserving the
# Session 205 single-secret default for existing deploys.
MAGIC_LINK_HMAC_KEY_FILE = os.getenv("MAGIC_LINK_HMAC_KEY_FILE", "").strip()
DEFAULT_TTL_SECONDS = 30 * 60  # 30 minutes
ALLOWED_ACTIONS = frozenset({"approve", "reject"})


class MagicLinkError(Exception):
    """Token-specific failure. String form is safe to log."""


@dataclass(frozen=True)
class VerifiedToken:
    token_id: str
    request_id: str
    action: str
    target_user_email: str


def _hmac_key() -> bytes:
    """Derive the HMAC key for magic-link tokens.

    Preference order:
      1. MAGIC_LINK_HMAC_KEY_FILE (separate secret, defense in depth) —
         when the file exists and is non-empty, use its raw bytes
         hashed with the magic-link domain tag.
      2. SIGNING_KEY_FILE (default) — derive from the Ed25519 signing
         key. Stable across restarts without storing a second secret.

    Why the optional separate file: a leak of signing.key would
    otherwise allow forgery of BOTH fleet orders AND magic-link
    approval tokens. With a dedicated magic-link key, a signing.key
    rotation does not invalidate outstanding links and a leak of one
    secret does not compromise the other.

    Domain separation: even when derived from signing.key, we hash
    `b"magic-link-v1|" + key_bytes` so the HMAC key is never the
    signing key itself.
    """
    if MAGIC_LINK_HMAC_KEY_FILE:
        try:
            mlk = pathlib.Path(MAGIC_LINK_HMAC_KEY_FILE).read_bytes().strip()
        except Exception as e:
            raise MagicLinkError(f"magic-link HMAC key unreadable: {e}")
        if not mlk:
            raise MagicLinkError("magic-link HMAC key file is empty")
        return hashlib.sha256(b"magic-link-v1|" + mlk).digest()

    try:
        raw = pathlib.Path(SIGNING_KEY_PATH).read_bytes().strip()
    except Exception as e:
        raise MagicLinkError(f"signing key unreadable: {e}")
    # hash the raw bytes (not the hex-encoded form) so we never include
    # the actual signing key material in the derived key
    return hashlib.sha256(b"magic-link-v1|" + raw).digest()


def _canonical(
    token_id: str,
    request_id: str,
    action: str,
    target_user_email: str,
    exp_unix: int,
) -> bytes:
    return f"{token_id}:{request_id}:{action}:{target_user_email}:{exp_unix}".encode("utf-8")


async def mint_token(
    conn: asyncpg.Connection,
    request_id: str,
    action: str,
    target_user_email: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    """Mint a single-use magic-link token. Writes the tracking row so
    consumption can enforce single-use semantics. Returns the opaque
    token string to embed in the URL."""
    if action not in ALLOWED_ACTIONS:
        raise MagicLinkError(f"action must be in {sorted(ALLOWED_ACTIONS)}")
    if not target_user_email or "@" not in target_user_email:
        raise MagicLinkError("target_user_email must be a valid email")
    if ttl_seconds < 60 or ttl_seconds > 3600:
        raise MagicLinkError("ttl_seconds out of range (60..3600)")

    token_id = secrets.token_hex(16)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    exp_unix = int(expires_at.timestamp())

    mac = hmac.new(
        _hmac_key(),
        _canonical(token_id, request_id, action, target_user_email, exp_unix),
        hashlib.sha256,
    ).hexdigest()

    await conn.execute(
        """
        INSERT INTO privileged_access_magic_links (
            token_id, request_id, action, target_user_email,
            expires_at
        ) VALUES ($1, $2::uuid, $3, $4, $5)
        """,
        token_id, request_id, action, target_user_email, expires_at,
    )

    # Token encodes enough to re-derive canonical at verify time.
    # token_id is the primary key; HMAC is the tamper-proof seal.
    return f"{token_id}.{mac}.{exp_unix}"


async def verify_and_consume(
    conn: asyncpg.Connection,
    token: str,
    expected_action: str,
    session_user_email: str,
    client_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> VerifiedToken:
    """Validate token + session binding, mark it consumed.

    Raises MagicLinkError on any of: malformed, expired, consumed,
    tampered HMAC, session/user mismatch, action mismatch.

    Atomic UPDATE ... WHERE consumed_at IS NULL RETURNING semantics
    ensure single-use even under races.
    """
    if not token:
        raise MagicLinkError("missing token")
    parts = token.split(".")
    if len(parts) != 3:
        raise MagicLinkError("malformed token")
    token_id, mac_hex, exp_str = parts
    try:
        exp_unix = int(exp_str)
    except ValueError:
        raise MagicLinkError("malformed token exp")
    if expected_action not in ALLOWED_ACTIONS:
        raise MagicLinkError("unknown expected_action")
    if exp_unix < int(time.time()):
        raise MagicLinkError("token expired")

    row = await conn.fetchrow(
        "SELECT token_id, request_id::text AS request_id, action, "
        "target_user_email, expires_at, consumed_at "
        "FROM privileged_access_magic_links WHERE token_id = $1",
        token_id,
    )
    if not row:
        raise MagicLinkError("token not found")
    if row["consumed_at"] is not None:
        raise MagicLinkError("token already consumed")
    if row["expires_at"] < datetime.now(timezone.utc):
        raise MagicLinkError("token expired")
    if row["action"] != expected_action:
        raise MagicLinkError(
            f"action mismatch: token={row['action']} expected={expected_action}"
        )
    if row["target_user_email"] != session_user_email:
        raise MagicLinkError(
            "session user does not match target_user_email; "
            "log in as the intended approver"
        )

    # Recompute HMAC — tamper detection
    expected_mac = hmac.new(
        _hmac_key(),
        _canonical(
            token_id,
            row["request_id"],
            row["action"],
            row["target_user_email"],
            exp_unix,
        ),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_mac, mac_hex):
        raise MagicLinkError("HMAC mismatch (tampered token)")

    # Atomic single-use consume. If the UPDATE returns 0 rows (another
    # worker consumed it between our SELECT and UPDATE), reject.
    consumed = await conn.fetchrow(
        """
        UPDATE privileged_access_magic_links
        SET consumed_at = NOW(),
            consumed_by_ip = $2,
            consumed_by_ua = $3
        WHERE token_id = $1
          AND consumed_at IS NULL
        RETURNING token_id
        """,
        token_id, client_ip, (user_agent or "")[:512],
    )
    if consumed is None:
        raise MagicLinkError("token already consumed (race)")

    return VerifiedToken(
        token_id=row["token_id"],
        request_id=row["request_id"],
        action=row["action"],
        target_user_email=row["target_user_email"],
    )
