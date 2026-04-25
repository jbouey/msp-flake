"""Appliance signature auth — Week 1 soak-mode implementation.

Verifies Ed25519 signatures on appliance HTTP requests. During the
30-day soak the verifier is OBSERVE-ONLY: it inspects any signature
headers that are present, reports cryptographic validity, and writes
to an adoption metric. It does not reject requests. Bearer api_key
auth (shared.require_appliance_bearer) remains the enforcement path
until Week 5 flips the switch.

The canonical signing input (frozen for all future weeks) is:

    METHOD
    PATH
    SHA256_HEX_LOWER(body)
    RFC3339_UTC_SECONDS_Z
    NONCE_HEX32

joined with exactly \\n (0x0A), no trailing newline.

  * METHOD     uppercase HTTP verb
  * PATH       absolute URI path, no querystring, leading '/'
  * body hash  lowercase hex sha256 of raw body bytes the client sent.
               Empty body → sha256("") = e3b0c442…7852b855
  * timestamp  rfc3339 with Z suffix, second precision, UTC
  * nonce      32 lowercase hex chars (128 bits)

Signature is base64url-encoded (no padding) in `X-Appliance-Signature`.
Timestamp is `X-Appliance-Timestamp`; nonce is `X-Appliance-Nonce`.

Server verification order:
  1. All three headers present?  absent → return PRESENT=False, VALID=False.
  2. Timestamp within ±60s of server UTC.
  3. Nonce has not been seen in the last 2 hours for this appliance.
  4. Resolve expected pubkey via v_current_appliance_identity for
     (site_id, mac_address). The legacy site_appliances.agent_public_key
     fallback was removed (Session 211, #179) — that column held the
     EVIDENCE-bundle key, not the IDENTITY key sigauth needs. A MAC
     without a `provisioning_claim_events` row now returns
     `unknown_pubkey` (the honest reason).
  5. Reconstruct canonical_input; verify ed25519 signature.

The function returns a SignatureVerifyResult describing what happened.
Callers in soak mode log the result and continue; callers in strict
mode (Week 5+) raise HTTPException(401) on anything but VALID=True.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from fastapi import Request

logger = logging.getLogger("signature_auth")

# The canonical signing input separator. Frozen at 0x0A (LF).
CANONICAL_SEP = b"\n"

# sha256 of empty string, lowercase hex. Spelled out so it doesn't
# look like a magic number in tests.
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

# Acceptable clock skew between daemon and server. 60s window.
MAX_CLOCK_SKEW = timedelta(seconds=60)

# Replay window. 2 hours matches the existing nonce TTL used by the
# daemon's order processor (processor.go).
NONCE_TTL = timedelta(hours=2)

# Header names. Case-insensitive per HTTP spec, but we normalize.
HDR_SIG = "x-appliance-signature"
HDR_TS = "x-appliance-timestamp"
HDR_NONCE = "x-appliance-nonce"

# Regex for the 32-hex-char nonce.
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")

# Regex for RFC3339 UTC second precision with Z suffix. Example:
# 2026-04-15T03:45:23Z
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@dataclass(frozen=True)
class SignatureVerifyResult:
    """Outcome of an appliance signature verification attempt.

    present
        All three sig headers were supplied. False if any are missing.
        A missing signature is not a verification failure during soak.
    valid
        Signature verified cryptographically against the expected pubkey.
        Implies present=True AND the timestamp/nonce/crypto checks all
        passed.
    reason
        When valid=False, short machine-parsable code explaining why.
        One of: no_headers, bad_timestamp, clock_skew, bad_nonce,
        nonce_replay, unknown_pubkey, bad_signature_format, bad_body_hash,
        invalid_signature, exception.
    pubkey_fingerprint
        Fingerprint of the pubkey used for verification, when we got
        far enough to look one up. "" otherwise.
    detail
        Human-readable context for logs / metrics. Never shown to the
        caller.
    """
    present: bool
    valid: bool
    reason: str = ""
    pubkey_fingerprint: str = ""
    detail: str = ""


def _canonical_input(
    method: str, path: str, body_sha256_hex: str, ts_iso: str, nonce_hex: str
) -> bytes:
    """Build the canonical signing input exactly once per request.

    Pure function. No global state. Same inputs → same bytes.
    """
    parts = [
        method.upper().encode("ascii"),
        path.encode("ascii"),
        body_sha256_hex.lower().encode("ascii"),
        ts_iso.encode("ascii"),
        nonce_hex.lower().encode("ascii"),
    ]
    return CANONICAL_SEP.join(parts)


def _fingerprint(pubkey_hex: str) -> str:
    """First 16 hex chars of sha256(pubkey_raw_bytes). Stable across
    daemon restarts; matches the value the daemon writes to
    /etc/osiriscare-identity.json. Ed25519 pubkeys are 32 bytes → 64
    hex chars; anything else is garbage."""
    if not pubkey_hex or len(pubkey_hex) != 64:
        return ""
    try:
        raw = bytes.fromhex(pubkey_hex)
    except ValueError:
        return ""
    return hashlib.sha256(raw).hexdigest()[:16]


async def _resolve_pubkey(
    conn, site_id: str, mac_address: str
) -> Optional[tuple[str, str]]:
    """Return (pubkey_hex, fingerprint) for (site_id, mac_address).

    Resolution order (#179 Commit C, 2026-04-25):
      1. `site_appliances.agent_identity_public_key` — populated at
         checkin time from the daemon's IDENTITY pubkey
         (phonehome.go::AgentIdentityPublicKey, daemon v0.4.13+).
         This is the authoritative source for verifying sigauth
         request signatures on actively-checking-in appliances.
      2. `v_current_appliance_identity` — historic provisioning-claim
         identity. Pre-#179 this was the only source. Kept as a
         fallback for sites that have completed a claim event but
         haven't yet checked in with v0.4.13 (or have no daemon
         actively running).
      3. Return None — caller treats as `unknown_pubkey`. Honest
         signal: we don't know how to verify this MAC's signatures
         yet. Substrate signature_verification_failures fires; an
         operator either deploys v0.4.13 or runs the recovery script.

    *Legacy fallback to `site_appliances.agent_public_key` removed
    in Session 211 / #179.* That column holds the EVIDENCE-bundle
    signing key (Session 196 — `evidence_chain.py` reads it). Sigauth
    needs the IDENTITY signing key. They're different keys by design
    (key separation). The new
    `site_appliances.agent_identity_public_key` column added by
    migration 251 is the correct sigauth-side source.

    Operator recovery for `unknown_pubkey`:
      - Best path: roll the daemon to v0.4.13+ — its next checkin
        populates agent_identity_public_key automatically.
      - Standard rekey: `fleet_cli orders rekey --site <id> --mac <mac>`.
      - Manual recovery: `scripts/recover_legacy_appliance.sh
        <site_id> <mac> <ip>` mints a fresh API key + claim event.
      - Admin restore: `POST /api/provision/admin/restore` for orphan-row.
    See substrate runbook signature_verification_failures.md.
    """
    # 1. Hot path: identity pubkey persisted by Commit A's STEP 3.6c.
    #    Compute fingerprint locally — the column stores raw hex.
    row = await conn.fetchrow(
        """
        SELECT agent_identity_public_key
          FROM site_appliances
         WHERE site_id = $1 AND mac_address = $2
           AND deleted_at IS NULL
           AND agent_identity_public_key IS NOT NULL
        """,
        site_id,
        mac_address,
    )
    if row and row["agent_identity_public_key"]:
        pk = row["agent_identity_public_key"]
        return pk, _fingerprint(pk)

    # 2. Fallback: provisioning-claim identity view.
    row = await conn.fetchrow(
        """
        SELECT agent_pubkey_hex, agent_pubkey_fingerprint
          FROM v_current_appliance_identity
         WHERE site_id = $1 AND mac_address = $2
        """,
        site_id,
        mac_address,
    )
    if row:
        return row["agent_pubkey_hex"], row["agent_pubkey_fingerprint"]

    return None


async def _nonce_seen(conn, fingerprint: str, nonce_hex: str) -> bool:
    """Has this nonce been observed recently for this appliance?

    We scope nonces by fingerprint (not site_id) so a rotated-key
    replay window doesn't let a stolen nonce survive a rotation. The
    `nonces` table is the existing order-replay tracker; we reuse it
    with a distinct key prefix so the two populations don't mingle.
    """
    key = f"sigauth:{fingerprint}:{nonce_hex}"
    # Bind asyncpg's Python timedelta directly (NOT an "<n> seconds"
    # string — that raised 'str' object has no attribute 'days' inside
    # asyncpg.pgproto.pgproto.interval_encode). Keep the explicit
    # `$2::interval` cast so Postgres can resolve `NOW() - $2` at
    # PREPARE time before parameter binding; without the cast the
    # planner can't infer `$2`'s type and fails with
    # "operator does not exist: timestamp with time zone > interval".
    row = await conn.fetchrow(
        "SELECT 1 FROM nonces WHERE nonce = $1 AND created_at > NOW() - $2::interval",
        key,
        NONCE_TTL,
    )
    return row is not None


async def _record_nonce(conn, fingerprint: str, nonce_hex: str) -> None:
    """Persist the nonce so a later replay is detected. Best-effort; a
    failure to record doesn't reject the current request — that's on
    the nonce replay check next time around."""
    key = f"sigauth:{fingerprint}:{nonce_hex}"
    try:
        await conn.execute(
            "INSERT INTO nonces (nonce, created_at) VALUES ($1, NOW()) "
            "ON CONFLICT (nonce) DO NOTHING",
            key,
        )
    except Exception:
        # Reads may eat exceptions; this write-failure is observable
        # via the metric but shouldn't flip verification to invalid.
        logger.warning("sigauth nonce record failed", exc_info=True)


async def verify_appliance_signature(
    request: Request,
    conn,
    site_id: str,
    mac_address: str,
    body_bytes: bytes,
    *,
    strict: bool = False,
) -> SignatureVerifyResult:
    """Verify the appliance signature on an incoming request.

    Week 1 soak-mode: callers use strict=False. If the headers are
    absent, returns PRESENT=False immediately and callers carry on
    with bearer auth. If headers are present but invalid, returns
    VALID=False and callers log + proceed.

    Strict-mode (Week 5+) callers pass strict=True; caller is
    responsible for translating anything other than VALID=True to an
    HTTP 401.
    """
    sig_b64 = request.headers.get(HDR_SIG)
    ts_iso = request.headers.get(HDR_TS)
    nonce_hex = request.headers.get(HDR_NONCE)

    if not (sig_b64 and ts_iso and nonce_hex):
        return SignatureVerifyResult(
            present=False, valid=False, reason="no_headers",
            detail="one or more signature headers missing",
        )

    # 2. Timestamp shape + skew.
    if not TS_RE.match(ts_iso):
        return SignatureVerifyResult(
            present=True, valid=False, reason="bad_timestamp",
            detail=f"timestamp doesn't match RFC3339-second-Z: {ts_iso!r}",
        )
    try:
        ts = datetime.strptime(ts_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return SignatureVerifyResult(
            present=True, valid=False, reason="bad_timestamp",
            detail=f"timestamp unparsable: {ts_iso!r}",
        )
    now = datetime.now(timezone.utc)
    if abs(now - ts) > MAX_CLOCK_SKEW:
        return SignatureVerifyResult(
            present=True, valid=False, reason="clock_skew",
            detail=f"skew {(now - ts).total_seconds():.1f}s exceeds {MAX_CLOCK_SKEW.total_seconds():.0f}s",
        )

    # 3. Nonce shape.
    if not NONCE_RE.match(nonce_hex):
        return SignatureVerifyResult(
            present=True, valid=False, reason="bad_nonce",
            detail=f"nonce not 32 lower-hex: {nonce_hex!r}",
        )

    # 4. Resolve expected pubkey.
    resolved = await _resolve_pubkey(conn, site_id, mac_address)
    if resolved is None:
        return SignatureVerifyResult(
            present=True, valid=False, reason="unknown_pubkey",
            detail=f"no identity row for site_id={site_id} mac={mac_address}",
        )
    pubkey_hex, fingerprint = resolved

    # 5. Decode signature.
    try:
        # base64url without padding. Python's urlsafe_b64decode requires
        # padding, so we add back any missing '=' before decoding.
        pad = "=" * ((4 - len(sig_b64) % 4) % 4)
        sig_raw = base64.urlsafe_b64decode(sig_b64 + pad)
    except (binascii.Error, ValueError) as e:
        return SignatureVerifyResult(
            present=True, valid=False, reason="bad_signature_format",
            pubkey_fingerprint=fingerprint,
            detail=f"base64url decode failed: {e}",
        )
    if len(sig_raw) != 64:
        return SignatureVerifyResult(
            present=True, valid=False, reason="bad_signature_format",
            pubkey_fingerprint=fingerprint,
            detail=f"ed25519 signatures are 64 bytes, got {len(sig_raw)}",
        )

    # 6. Nonce replay check — AFTER signature-format validation so we
    #    don't poison the nonce cache with garbage. The check is scoped
    #    to the fingerprint, tolerating future key rotations cleanly.
    if await _nonce_seen(conn, fingerprint, nonce_hex):
        return SignatureVerifyResult(
            present=True, valid=False, reason="nonce_replay",
            pubkey_fingerprint=fingerprint,
            detail="nonce already observed within TTL",
        )

    # 7. Build canonical input + verify.
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    canonical = _canonical_input(
        request.method,
        request.url.path,
        body_hash,
        ts_iso,
        nonce_hex,
    )
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex))
    except (ValueError, TypeError) as e:
        return SignatureVerifyResult(
            present=True, valid=False, reason="unknown_pubkey",
            pubkey_fingerprint=fingerprint,
            detail=f"pubkey decode failed: {e}",
        )
    try:
        pub.verify(sig_raw, canonical)
    except InvalidSignature:
        return SignatureVerifyResult(
            present=True, valid=False, reason="invalid_signature",
            pubkey_fingerprint=fingerprint,
            detail="ed25519 verify failed",
        )
    except Exception as e:
        # Defensive: never let a cryptography lib quirk surface as a
        # 500. Report as invalid and log.
        logger.error("sigauth verify raised unexpectedly", exc_info=True)
        return SignatureVerifyResult(
            present=True, valid=False, reason="exception",
            pubkey_fingerprint=fingerprint,
            detail=f"{type(e).__name__}: {e}",
        )

    # 8. Valid. Record the nonce so the next replay gets caught.
    await _record_nonce(conn, fingerprint, nonce_hex)

    return SignatureVerifyResult(
        present=True, valid=True, reason="",
        pubkey_fingerprint=fingerprint,
        detail="",
    )
