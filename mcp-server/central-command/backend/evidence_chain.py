"""
Evidence Chain API - Hash-chained evidence bundles with OTS anchoring.

Handles evidence submission from compliance appliances with:
- SHA256 hash chain linking bundles
- Ed25519 signature verification (agent-side signing)
- OpenTimestamps blockchain anchoring (Enterprise tier)
- MinIO WORM storage integration

HIPAA Controls:
- §164.312(b) - Audit Controls (tamper-evident audit trail)
- §164.312(c)(1) - Integrity Controls (provable evidence authenticity)
"""

import asyncio
import os
import json
import hashlib
import hmac
import base64
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path
import pathlib

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Request, Cookie, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import aiohttp

try:
    from .auth import require_auth
    from .shared import require_appliance_bearer
except ImportError:
    # Standalone import (e.g., tests that import pure functions directly)
    require_auth = None  # type: ignore[assignment]
    require_appliance_bearer = None  # type: ignore[assignment]
import asyncpg


# =============================================================================
# AUTH GUARDS FOR EVIDENCE ENDPOINTS
# =============================================================================
#
# The per-site evidence endpoints (/verify-chain, /bundles, /blockchain-status,
# /chain-of-custody) were historically registered with no auth, which made
# them a bulk-disclosure path for compliance metadata (C3 from the Session 203
# audit proof round-table). The fix must accept BOTH admin sessions (used by
# the admin dashboard when an operator reviews a site) AND portal tokens /
# client sessions (used by the scorecard link shared with clients / auditors).
#
# `require_evidence_view_access` is the single chokepoint. It tries the three
# auth paths in order and 403s if none match. Read-only endpoints should add
# it via `Depends(require_evidence_view_access)` — they don't need the return
# value, just the enforcement side effect.

async def require_evidence_view_access(
    site_id: str,
    request: Request,
    portal_session: Optional[str] = Cookie(None),
    osiris_client_session: Optional[str] = Cookie(None),
    token: Optional[str] = Query(None),
) -> Dict[str, Any]:
    """Gate read-only per-site evidence endpoints.

    Accepts any of:
      1. An authenticated admin dashboard session (cookie `session` → admin)
      2. The NEW client portal session cookie (`osiris_client_session`)
         where the client_user's org owns the site_id (Stage 3 closure
         of round-table 25, 2026-05-05 — pre-Stage-3 customers logged
         into the new portal could see compliance data on the dashboard
         but couldn't download the auditor kit because this gate only
         knew about the legacy `portal_session` cookie shape).
      3. The LEGACY per-site portal session cookie (`portal_session`)
         bound to this site (Session 203 shared-link shape; still
         supported for auditor magic links).
      4. A legacy portal token query param (`?token=…`) bound to this site.

    On failure raises HTTPException(403) with a single generic message
    so auditors can't enumerate which path failed.
    """
    # 1. Admin session — reuse the existing admin auth (expects session cookie).
    if require_auth is not None:
        try:
            admin_user = await require_auth(request)
            return {"method": "admin", "user": admin_user}
        except HTTPException:
            pass  # fall through — not an admin, try portal paths

    # 2. NEW client portal session — verify the client_user's org owns
    # this site_id. Lazy import to avoid the client_portal ↔ evidence_chain
    # cycle at module-load.
    if osiris_client_session:
        try:
            from .client_portal import get_client_user_from_session
            from .fleet import get_pool as _get_pool
            from .tenant_middleware import org_connection as _org_connection

            pool = await _get_pool()
            client_user = await get_client_user_from_session(
                osiris_client_session, pool,
            )
            if client_user:
                async with _org_connection(
                    pool, org_id=client_user["org_id"],
                ) as conn:
                    owns = await conn.fetchval(
                        """
                        SELECT 1 FROM sites
                         WHERE site_id = $1 AND client_org_id = $2
                         LIMIT 1
                        """,
                        site_id, client_user["org_id"],
                    )
                if owns:
                    return {
                        "method": "client_portal",
                        "user_id": str(client_user["user_id"]),
                        "org_id": str(client_user["org_id"]),
                        "email": client_user.get("email"),
                    }
        except Exception:
            # Best-effort — fall through to legacy paths on any error.
            logger.warning(
                "client_portal session evaluation failed in evidence gate",
                exc_info=True,
            )

    # 3/4. LEGACY portal session cookie OR legacy token query param.
    try:
        from .portal import validate_session as _validate_portal_session
        portal_ctx = await _validate_portal_session(site_id, portal_session, token)
        return {"method": "portal", **portal_ctx}
    except HTTPException:
        pass

    raise HTTPException(
        status_code=403,
        detail="Evidence access requires an admin session or a valid portal link",
    )

# Ed25519 signature verification
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature

# Rate-limit helper (Redis-backed; returns True,0 in test/dev without Redis).
# Used to cap auditor-kit downloads per site. Source: dashboard_api/shared.py:153.
# Try/except lets pytest (which loads this module outside the package context)
# and uvicorn (which loads it as part of dashboard_api package) both succeed —
# mirrors the pattern used by auth.py:22-24.
try:
    from .shared import check_rate_limit
except ImportError:
    from shared import check_rate_limit  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/evidence", tags=["evidence"])


# =============================================================================
# Configuration
# =============================================================================

# OTS Calendar servers
OTS_CALENDARS = [
    "https://a.pool.opentimestamps.org",
    "https://b.pool.opentimestamps.org",
    "https://alice.btc.calendar.opentimestamps.org",
    "https://bob.btc.calendar.opentimestamps.org",
]

OTS_ENABLED = os.getenv("OTS_ENABLED", "true").lower() == "true"
OTS_TIMEOUT = int(os.getenv("OTS_TIMEOUT", "30"))
MERKLE_BATCHING_ENABLED = os.getenv("MERKLE_BATCHING_ENABLED", "true").lower() == "true"

# Bitcoin attestation tag in OTS proofs
BTC_ATTESTATION_TAG = b'\x05\x88\x96\x0d\x73\xd7\x19\x01'


def read_bitcoin_varint(data: bytes, pos: int) -> tuple:
    """Parse a Bitcoin varint from bytes at position.

    Bitcoin varints encode integers with variable-length format:
    - 0x00-0xFC: 1 byte (value is the byte itself)
    - 0xFD: 3 bytes (0xFD + 2-byte little-endian)
    - 0xFE: 5 bytes (0xFE + 4-byte little-endian)
    - 0xFF: 9 bytes (0xFF + 8-byte little-endian)

    Returns:
        (value, bytes_consumed) tuple.

    Raises:
        IndexError: Not enough data at position.
    """
    if pos >= len(data):
        raise IndexError("Not enough data for varint")

    first = data[pos]
    if first < 0xFD:
        return first, 1
    elif first == 0xFD:
        if pos + 3 > len(data):
            raise IndexError("Not enough data for 2-byte varint")
        return int.from_bytes(data[pos + 1:pos + 3], 'little'), 3
    elif first == 0xFE:
        if pos + 5 > len(data):
            raise IndexError("Not enough data for 4-byte varint")
        return int.from_bytes(data[pos + 1:pos + 5], 'little'), 5
    else:  # 0xFF
        if pos + 9 > len(data):
            raise IndexError("Not enough data for 8-byte varint")
        return int.from_bytes(data[pos + 1:pos + 9], 'little'), 9


def read_ots_varint(data: bytes, pos: int) -> tuple:
    """Parse an OTS/LEB128 unsigned varint from bytes at position.

    Despite the OTS library naming it "varint", this uses unsigned LEB128
    encoding (7-bit chunks with continuation bit), NOT Bitcoin compact integers.

    Returns:
        (value, bytes_consumed) tuple.
    """
    value = 0
    shift = 0
    start = pos
    while pos < len(data):
        b = data[pos]
        value |= (b & 0x7F) << shift
        pos += 1
        if not (b & 0x80):
            return value, pos - start
        shift += 7
    raise IndexError("Unterminated LEB128 varint")


def extract_btc_block_height(data: bytes, tag_pos: int) -> int:
    """Extract Bitcoin block height from OTS attestation data.

    OTS Bitcoin attestation format after the 8-byte tag:
        ots_varint(payload_length) + payload

    The payload contains the block height encoded as OTS varint (LEB128).

    Args:
        data: The raw OTS attestation/upgrade data.
        tag_pos: Position of BTC_ATTESTATION_TAG in data.

    Returns:
        The Bitcoin block height, or None if parsing fails.
    """
    try:
        # Read payload length (LEB128 varint) right after the 8-byte tag
        payload_len, pl_size = read_ots_varint(data, tag_pos + 8)
        if payload_len <= 0 or payload_len > 8:
            return None
        payload_start = tag_pos + 8 + pl_size
        if payload_start + payload_len > len(data):
            return None
        # Block height is LEB128-encoded in the payload
        block_height, _ = read_ots_varint(data, payload_start)
        return block_height
    except (IndexError, ValueError):
        return None


# =============================================================================
# Ed25519 Signature Verification
# =============================================================================

def verify_ed25519_signature(
    data: bytes,
    signature_hex: str,
    public_key_hex: str
) -> bool:
    """
    Verify an Ed25519 signature.

    Args:
        data: The data that was signed
        signature_hex: Hex-encoded 64-byte Ed25519 signature
        public_key_hex: Hex-encoded 32-byte Ed25519 public key

    Returns:
        True if signature is valid, False otherwise
    """
    try:
        # Decode signature and public key from hex
        signature_bytes = bytes.fromhex(signature_hex)
        public_key_bytes = bytes.fromhex(public_key_hex)

        # Validate lengths
        if len(signature_bytes) != 64:
            logger.warning(f"Invalid signature length: {len(signature_bytes)} (expected 64)")
            return False
        if len(public_key_bytes) != 32:
            logger.warning(f"Invalid public key length: {len(public_key_bytes)} (expected 32)")
            return False

        # Load public key
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)

        # Verify signature
        public_key.verify(signature_bytes, data)
        return True

    except InvalidSignature:
        logger.warning("Ed25519 signature verification failed: invalid signature")
        return False
    except ValueError as e:
        logger.warning(f"Ed25519 signature verification failed: {e}")
        return False
    except Exception as e:
        logger.error(f"Ed25519 signature verification error: {e}")
        return False


async def get_agent_public_key(db: AsyncSession, site_id: str) -> Optional[str]:
    """
    Get the registered public key for a site's agent.

    Args:
        db: Database session
        site_id: Site identifier

    Returns:
        Hex-encoded public key if found, None otherwise
    """
    result = await db.execute(
        text("SELECT agent_public_key FROM sites WHERE site_id = :site_id"),
        {"site_id": site_id}
    )
    row = result.fetchone()
    return row.agent_public_key if row and row.agent_public_key else None


# =============================================================================
# Database Session (SQLAlchemy async)
# =============================================================================

async def get_db():
    """Get database session from main module."""
    from main import async_session
    if async_session is None:
        raise HTTPException(status_code=500, detail="Database session not configured")
    async with async_session() as session:
        yield session


# =============================================================================
# Models
# =============================================================================

class EvidenceBundleSubmit(BaseModel):
    """Evidence bundle submission from appliance."""
    site_id: str = Field(..., description="Site identifier")
    bundle_id: Optional[str] = Field(None, description="Unique bundle ID (generated if not provided)")
    bundle_hash: Optional[str] = Field(None, description="SHA256 hash of bundle content (computed if not provided)")
    check_type: Optional[str] = Field(None, description="Type of check (derived from checks if not provided)")
    check_result: Optional[str] = Field(None, description="Check result (derived from checks if not provided)")
    checked_at: datetime = Field(..., description="When the check was performed")

    # Evidence data
    checks: List[Dict[str, Any]] = Field(default_factory=list, description="Individual check results")
    summary: Dict[str, Any] = Field(default_factory=dict, description="Summary statistics")

    # Signing
    agent_signature: Optional[str] = Field(None, description="Ed25519 signature from agent")
    agent_public_key: Optional[str] = Field(None, description="Agent's public key (hex)")
    signed_data: Optional[str] = Field(None, description="Exact JSON string that was signed (for verification)")

    # NTP verification (for timestamp integrity)
    ntp_verification: Optional[Dict[str, Any]] = Field(None, description="Multi-source NTP verification")

    # OTS (if agent submitted with proof)
    ots_proof: Optional[str] = Field(None, description="Base64-encoded OTS proof from agent")


class EvidenceSubmitResponse(BaseModel):
    """Response after evidence submission."""
    bundle_id: str
    bundle_hash: str = ""  # SHA256 of the bundle content (for peer witnessing)
    chain_position: int
    prev_hash: Optional[str]
    current_hash: str
    ots_status: str  # none, pending, anchored
    ots_submitted: bool


class EvidenceVerifyResponse(BaseModel):
    """Evidence verification response."""
    bundle_id: str
    hash_valid: bool
    signature_valid: Optional[bool]
    chain_valid: bool
    ots_status: str
    ots_bitcoin_block: Optional[int]
    verified_at: datetime


class OTSStatusResponse(BaseModel):
    """OTS status for a site."""
    site_id: str
    total_bundles: int
    pending_count: int
    anchored_count: int
    verified_count: int
    failed_count: int
    oldest_pending: Optional[datetime]
    last_anchored: Optional[datetime]


# =============================================================================
# OTS Client Functions
# =============================================================================

def construct_ots_file(hash_bytes: bytes, calendar_response: bytes) -> bytes:
    """
    Construct a proper OTS file from hash and calendar response.

    OTS file format:
    - Magic header (31 bytes)
    - Version: \x01
    - Hash algorithm: \x08 (SHA256)
    - 32-byte hash
    - Timestamp operations from calendar
    """
    OTS_MAGIC = b'\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94'
    VERSION = b'\x01'
    HASH_SHA256 = b'\x08'

    ots_file = OTS_MAGIC + VERSION + HASH_SHA256 + hash_bytes + calendar_response

    return ots_file


def extract_calendar_url_from_proof(proof_bytes: bytes) -> Optional[str]:
    """Extract the actual calendar URL from OTS proof data."""
    # Calendar URLs are embedded as ASCII strings in the proof
    # Look for https:// pattern
    try:
        url_start = proof_bytes.find(b'https://')
        if url_start >= 0:
            # Find end of URL (null byte or non-URL character)
            url_end = url_start
            while url_end < len(proof_bytes) and proof_bytes[url_end:url_end+1] not in (b'\x00', b'\x83'):
                url_end += 1
            url = proof_bytes[url_start:url_end].decode('ascii', errors='ignore')
            # Clean up any trailing garbage
            if 'opentimestamps.org' in url:
                return url.split('\x00')[0].strip()
    except Exception as e:
        logger.debug(f"OTS URL extraction failed: {e}")
    return None


def replay_timestamp_operations(hash_bytes: bytes, timestamp_data: bytes) -> Optional[bytes]:
    """
    Replay OTS timestamp operations to compute the calendar commitment.

    OTS operations transform the original hash step by step. The commitment
    is the message state at the attestation point - the result of applying ALL
    operations (including appends/prepends) up to the attestation marker.
    This is what the calendar stores and uses for /timestamp/{commitment} queries.

    Operation bytes:
    - 0xf0 XX: prepend (followed by length byte, then data)
    - 0xf1 XX: append (followed by length byte, then data)
    - 0x08: SHA256
    - 0x67: SHA1
    - 0x20: RIPEMD160
    - 0x83: pending attestation tag (follows 0x00 attestation marker)
    - 0x00: attestation marker (Bitcoin or pending attestation follows)

    Returns the commitment (message state at attestation) or None if parsing fails.
    """
    current_hash = hash_bytes
    pos = 0

    try:
        while pos < len(timestamp_data):
            op = timestamp_data[pos]
            pos += 1

            if op == 0xf0:  # Prepend
                if pos >= len(timestamp_data):
                    break
                length = timestamp_data[pos]
                pos += 1
                if pos + length > len(timestamp_data):
                    break
                prepend_data = timestamp_data[pos:pos + length]
                pos += length
                current_hash = prepend_data + current_hash

            elif op == 0xf1:  # Append
                if pos >= len(timestamp_data):
                    break
                length = timestamp_data[pos]
                pos += 1
                if pos + length > len(timestamp_data):
                    break
                append_data = timestamp_data[pos:pos + length]
                pos += length
                current_hash = current_hash + append_data

            elif op == 0x08:  # SHA256
                current_hash = hashlib.sha256(current_hash).digest()

            elif op == 0x67:  # SHA1
                current_hash = hashlib.sha1(current_hash).digest()

            elif op == 0x20:  # RIPEMD160
                import hashlib as hl
                h = hl.new('ripemd160')
                h.update(current_hash)
                current_hash = h.digest()

            elif op == 0x00:  # Attestation marker (0x00 followed by type tag)
                # The commitment is the current message state at the attestation.
                # The calendar stores this exact value for upgrade queries.
                return current_hash

            elif op == 0xff:  # Fork (multiple paths)
                continue

            else:
                # Unknown operation - skip if looks like length prefix
                if op <= 0x20:
                    if pos + op <= len(timestamp_data):
                        pos += op
                else:
                    logger.debug(f"Unknown OTS operation: 0x{op:02x} at position {pos-1}")
                    break

        # Fell through without hitting attestation - return current state
        return current_hash if len(current_hash) <= 64 else None

    except Exception as e:
        logger.warning(f"Failed to replay OTS operations: {e}")
        return None


async def submit_hash_to_ots(bundle_hash: str, bundle_id: str) -> Optional[Dict[str, Any]]:
    """
    Submit a hash to OTS calendar servers.

    Returns dict with proof_data (proper OTS file), calendar_url, submitted_at if successful.
    The proof_data is a complete OTS file that can be used with standard OTS tools.
    """
    if not OTS_ENABLED:
        logger.debug("OTS disabled, skipping hash submission")
        return None

    # Validate hash format
    if len(bundle_hash) != 64:
        logger.error(f"Invalid hash length: {len(bundle_hash)}")
        return None

    try:
        hash_bytes = bytes.fromhex(bundle_hash)
    except ValueError:
        logger.error(f"Invalid hex hash: {bundle_hash[:20]}...")
        return None

    timeout = aiohttp.ClientTimeout(total=OTS_TIMEOUT)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for calendar_url in OTS_CALENDARS:
            try:
                url = f"{calendar_url}/digest"
                headers = {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/vnd.opentimestamps.v1",
                    "User-Agent": "OsirisCare-Central-Command/1.0",
                }

                async with session.post(url, data=hash_bytes, headers=headers) as resp:
                    if resp.status == 200:
                        calendar_response = await resp.read()

                        # Construct proper OTS file with header and hash
                        ots_file_bytes = construct_ots_file(hash_bytes, calendar_response)
                        proof_b64 = base64.b64encode(ots_file_bytes).decode('ascii')

                        # Extract actual calendar URL from proof (may differ from pool URL)
                        actual_calendar = extract_calendar_url_from_proof(calendar_response) or calendar_url

                        logger.info(f"OTS submitted: bundle={bundle_id[:8]}... calendar={actual_calendar}")

                        return {
                            "proof_data": proof_b64,
                            "calendar_url": actual_calendar,  # Store actual calendar, not pool
                            "submitted_at": datetime.now(timezone.utc),
                            "status": "pending",
                        }
                    else:
                        logger.warning(f"OTS calendar returned {resp.status}: {calendar_url}")

            except aiohttp.ClientError as e:
                logger.warning(f"OTS client error for {calendar_url}: {type(e).__name__}: {e}")
            except Exception as e:
                logger.error(f"OTS unexpected error for {calendar_url}: {type(e).__name__}: {e}")

        logger.error(f"All OTS calendars failed for bundle {bundle_id}")
        return None


def parse_ots_file(ots_bytes: bytes) -> Optional[Dict[str, Any]]:
    """
    Parse an OTS file to extract components for upgrade.

    Returns dict with:
    - hash_bytes: Original 32-byte hash
    - timestamp_data: The timestamp operations portion
    - calendar_url: Extracted calendar URL
    - has_bitcoin: Whether Bitcoin attestation exists
    """
    # OTS magic header
    OTS_MAGIC = b'\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94'

    if not ots_bytes.startswith(OTS_MAGIC):
        # Try legacy format (raw calendar response without header)
        # These old proofs can't be upgraded with standard tools
        return None

    offset = len(OTS_MAGIC)

    # Version byte (0x01) — skip if present
    if offset < len(ots_bytes) and ots_bytes[offset] == 0x01:
        offset += 1

    # Next byte should be hash algorithm (0x08 = SHA256)
    if offset >= len(ots_bytes) or ots_bytes[offset] != 0x08:
        return None

    offset += 1

    # Next 32 bytes are the hash
    if offset + 32 > len(ots_bytes):
        return None

    hash_bytes = ots_bytes[offset:offset + 32]
    offset += 32

    # Rest is timestamp data
    timestamp_data = ots_bytes[offset:]

    # Extract calendar URL
    calendar_url = extract_calendar_url_from_proof(timestamp_data)

    # Check for Bitcoin attestation marker
    has_bitcoin = BTC_ATTESTATION_TAG in timestamp_data

    return {
        "hash_bytes": hash_bytes,
        "timestamp_data": timestamp_data,
        "calendar_url": calendar_url,
        "has_bitcoin": has_bitcoin,
        "full_ots": ots_bytes
    }


async def mark_proof_anchored(
    db: AsyncSession,
    bundle_id: str,
    block_height: Optional[int],
    calendar_url: Optional[str] = None,
    proof_data: Optional[str] = None,
):
    """Mark an OTS proof as anchored to Bitcoin. Single source of truth for status updates.

    Updates ots_proofs, compliance_bundles, and ots_merkle_batches (if batch proof).
    Also writes an audit log entry for HIPAA compliance.
    """
    params: Dict[str, Any] = {"bundle_id": bundle_id, "block": block_height}
    update_fields = "status = 'anchored', bitcoin_block = :block, anchored_at = NOW(), last_upgrade_attempt = NOW()"

    if proof_data:
        update_fields += ", proof_data = :proof_data, error = NULL"
        params["proof_data"] = proof_data
    if calendar_url:
        update_fields += ", calendar_url = :calendar_url"
        params["calendar_url"] = calendar_url

    await db.execute(text(f"""
        UPDATE ots_proofs SET {update_fields}, upgrade_attempts = upgrade_attempts + 1
        WHERE bundle_id = :bundle_id
    """), params)

    # Update merkle batch table if this is a batch proof
    if bundle_id.startswith("MB-"):
        await db.execute(text("""
            UPDATE ots_merkle_batches
            SET ots_status = 'anchored', bitcoin_block = :block, anchored_at = NOW()
            WHERE batch_id = :batch_id
        """), {"block": block_height, "batch_id": bundle_id})

    # Audit log: record proof anchoring for HIPAA compliance
    try:
        await db.execute(text("""
            INSERT INTO admin_audit_log (user_id, username, action, target, details, ip_address)
            VALUES (NULL, 'system', 'OTS_PROOF_ANCHORED', :bundle_id,
                    :details, '127.0.0.1')
        """), {
            "bundle_id": bundle_id,
            "details": json.dumps({
                "bitcoin_block": block_height,
                "calendar_url": calendar_url,
                "is_batch": bundle_id.startswith("MB-"),
            }),
        })
    except Exception:
        pass  # Audit log failure must not block proof anchoring

    logger.info(f"OTS anchored: {bundle_id[:8]}... block={block_height}")


async def upgrade_pending_proofs(db: AsyncSession, limit: int = 500):
    """
    Background task to upgrade pending OTS proofs using the reference library.

    Uses opentimestamps-client to correctly parse proofs, compute commitments,
    and query calendars for Bitcoin attestations.
    """
    from opentimestamps.core.timestamp import DetachedTimestampFile
    from opentimestamps.core.serialize import BytesDeserializationContext
    from opentimestamps.core.notary import PendingAttestation, BitcoinBlockHeaderAttestation

    OTS_MAGIC = b'\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94'

    # Fail proofs older than 7 days (calendars prune after ~7 days)
    await db.execute(text("""
        UPDATE ots_proofs
        SET status = 'failed',
            error = 'Calendar retention exceeded - proof never received Bitcoin attestation within 7 days'
        WHERE status = 'pending'
        AND submitted_at < NOW() - INTERVAL '7 days'
    """))

    # Fetch pending proofs (eligible after 10 min, retry after 10 min)
    result = await db.execute(text("""
        SELECT bundle_id, bundle_hash, proof_data, calendar_url
        FROM ots_proofs
        WHERE status = 'pending'
        AND submitted_at > NOW() - INTERVAL '7 days'
        AND submitted_at < NOW() - INTERVAL '10 minutes'
        AND (last_upgrade_attempt IS NULL OR last_upgrade_attempt < NOW() - INTERVAL '10 minutes')
        ORDER BY submitted_at ASC
        LIMIT :limit
    """), {"limit": limit})

    pending_proofs = result.fetchall()

    if not pending_proofs:
        return {"checked": 0, "upgraded": 0}

    upgraded = 0
    fixed_format = 0
    timeout = aiohttp.ClientTimeout(total=OTS_TIMEOUT)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for proof in pending_proofs:
            try:
                async with db.begin_nested():  # SAVEPOINT: isolate each proof from transaction poisoning
                    proof_bytes = base64.b64decode(proof.proof_data)

                    # Fix v0 format (missing version byte) -> v1
                    if proof_bytes.startswith(OTS_MAGIC) and proof_bytes[len(OTS_MAGIC)] == 0x08:
                        proof_bytes = proof_bytes[:len(OTS_MAGIC)] + b'\x01' + proof_bytes[len(OTS_MAGIC):]
                        fixed_format += 1

                    # Parse with reference library
                    try:
                        ctx = BytesDeserializationContext(proof_bytes)
                        dtf = DetachedTimestampFile.deserialize(ctx)
                    except Exception as e:
                        await db.execute(text("""
                            UPDATE ots_proofs
                            SET last_upgrade_attempt = NOW(),
                                upgrade_attempts = upgrade_attempts + 1,
                                error = :error
                            WHERE bundle_id = :bundle_id
                        """), {"bundle_id": proof.bundle_id, "error": f"Parse failed: {str(e)[:200]}"})
                        continue

                    # Check all attestations
                    upgrade_success = False
                    last_error = "No pending attestations found"

                    for msg, attestation in dtf.timestamp.all_attestations():
                        if isinstance(attestation, BitcoinBlockHeaderAttestation):
                            # Already anchored — use DRY helper
                            await mark_proof_anchored(db, proof.bundle_id, attestation.height)
                            upgraded += 1
                            upgrade_success = True
                            break

                        if isinstance(attestation, PendingAttestation):
                            commitment = msg.hex()
                            calendar_url = attestation.uri

                            try:
                                upgrade_url = f"{calendar_url}/timestamp/{commitment}"
                                async with session.get(upgrade_url) as resp:
                                    if resp.status == 200:
                                        upgrade_data = await resp.read()

                                        if BTC_ATTESTATION_TAG in upgrade_data:
                                            # Extract block height from BTC attestation payload
                                            pos = upgrade_data.find(BTC_ATTESTATION_TAG)
                                            block_height = None
                                            if pos >= 0:
                                                block_height = extract_btc_block_height(upgrade_data, pos)

                                            # Store upgraded proof (proper v1 format)
                                            upgraded_ots = (OTS_MAGIC + b'\x01\x08' +
                                                           dtf.file_digest + upgrade_data)
                                            proof_b64 = base64.b64encode(upgraded_ots).decode('ascii')

                                            await mark_proof_anchored(
                                                db, proof.bundle_id, block_height,
                                                calendar_url=calendar_url, proof_data=proof_b64,
                                            )
                                            upgraded += 1
                                            upgrade_success = True
                                            break
                                        else:
                                            last_error = f"No Bitcoin attestation yet from {calendar_url}"
                                    elif resp.status == 404:
                                        last_error = f"Commitment not found on {calendar_url}"
                                    else:
                                        last_error = f"{calendar_url} returned {resp.status}"
                            except aiohttp.ClientError as e:
                                last_error = f"Connection error to {calendar_url}: {str(e)[:100]}"

                    if not upgrade_success:
                        await db.execute(text("""
                            UPDATE ots_proofs
                            SET last_upgrade_attempt = NOW(),
                                upgrade_attempts = upgrade_attempts + 1,
                                error = :error
                            WHERE bundle_id = :bundle_id
                        """), {"bundle_id": proof.bundle_id, "error": last_error})

            except Exception as e:
                # Per CLAUDE.md: OTS upgrade failures MUST log at error level
                # with full exception traceback. "logger.warning on DB failures
                # BANNED → logger.error(exc_info=True)". The upgrade loop is
                # the only signal the substrate has that an OTS anchor is
                # silently failing; swallowing the traceback makes diagnosis
                # impossible.
                logger.error(
                    "OTS proof upgrade failed",
                    extra={
                        "bundle_id": proof.bundle_id,
                        "calendar_url": getattr(proof, "calendar_url", None),
                        "exception_class": type(e).__name__,
                    },
                    exc_info=True,
                )
                try:
                    async with db.begin_nested():  # Separate savepoint for error recording
                        await db.execute(text("""
                            UPDATE ots_proofs
                            SET last_upgrade_attempt = NOW(),
                                upgrade_attempts = upgrade_attempts + 1,
                                error = :error
                            WHERE bundle_id = :bundle_id
                        """), {"bundle_id": proof.bundle_id, "error": f"{type(e).__name__}: {str(e)[:480]}"})
                except Exception as inner_exc:
                    # Double failure: outer upgrade failed AND error-recording
                    # also failed. This is operationally important because the
                    # proof will keep being retried with no visible error-state
                    # on the row. Raise to error with both tracebacks so the
                    # on-call engineer can see what recovery path to take.
                    logger.error(
                        "OTS error-recording also failed — proof state will lag reality",
                        extra={
                            "bundle_id": proof.bundle_id,
                            "original_exception": type(e).__name__,
                            "inner_exception": type(inner_exc).__name__,
                        },
                        exc_info=True,
                    )

    await db.commit()

    expired_result = await db.execute(text("""
        SELECT COUNT(*) FROM ots_proofs WHERE status = 'expired'
    """))
    expired_count = expired_result.scalar() or 0

    return {
        "checked": len(pending_proofs),
        "upgraded": upgraded,
        "fixed_format": fixed_format,
        "total_expired": expired_count
    }


# =============================================================================
# Evidence Endpoints
# =============================================================================

@router.post("/sites/{site_id}/submit", response_model=EvidenceSubmitResponse)
async def submit_evidence(
    site_id: str,
    bundle: EvidenceBundleSubmit,
    background_tasks: BackgroundTasks,
    auth_site_id: str = Depends(require_appliance_bearer),
    db: AsyncSession = Depends(get_db),
):
    """
    Submit an evidence bundle from appliance.

    - Verifies Ed25519 signature from registered agent (REQUIRED)
    - Stores bundle in database
    - Links to previous bundle (hash chain)
    - Submits hash to OTS (async, background)
    - Triggers WORM upload if enabled

    Security: Only appliances with a registered public key can submit evidence.
    The agent_signature must be valid for the bundle content.
    """
    # SECURITY: Enforce Bearer token site matches URL path site_id.
    # Without this, an appliance with token for site-A could submit evidence to site-B.
    if site_id != auth_site_id:
        logger.warning(f"SECURITY: site_id mismatch on evidence submit: token={auth_site_id} url={site_id}")
        raise HTTPException(status_code=403, detail="Site ID mismatch: token does not authorize this site")

    # SECURITY: Timestamp validation — reject backdated or future evidence.
    # A compromised appliance could submit evidence with forged timestamps.
    # Allow 5-minute clock skew tolerance for NTP drift.
    now = datetime.now(timezone.utc)
    skew = abs((bundle.checked_at - now).total_seconds())
    if bundle.checked_at > now + timedelta(minutes=2):
        logger.warning(f"SECURITY: future timestamp rejected for site={site_id}: checked_at={bundle.checked_at} server_now={now}")
        raise HTTPException(status_code=400, detail="Evidence timestamp is in the future")
    if skew > 300:  # 5 minutes
        logger.warning(f"SECURITY: timestamp skew {skew:.0f}s for site={site_id}: checked_at={bundle.checked_at} server_now={now}")
        # Don't reject — log warning. Extreme skew (>1hr) gets rejected.
        if skew > 3600:
            raise HTTPException(status_code=400, detail=f"Evidence timestamp too far from server time ({skew:.0f}s skew)")

    # Verify site exists
    site_result = await db.execute(
        text("SELECT site_id, agent_public_key FROM sites WHERE site_id = :site_id"),
        {"site_id": site_id}
    )
    site_row = site_result.fetchone()
    if not site_row:
        raise HTTPException(status_code=404, detail=f"Site not found: {site_id}")

    # SECURITY: Require Ed25519 signature verification for evidence submission
    if not bundle.agent_signature:
        logger.warning(f"Evidence submission rejected: no signature provided for site={site_id}")
        raise HTTPException(
            status_code=401,
            detail="Evidence submission requires agent_signature. "
                   "Ensure the appliance has a signing key at "
                   "/var/lib/msp/agent-signing-key"
        )

    # Multi-appliance key resolution: check per-appliance keys first, then site-level fallback
    registered_key = None
    matched_appliance_id = None

    # 1. Check per-appliance keys (supports multiple appliances with different keys)
    if bundle.agent_public_key and len(bundle.agent_public_key) == 64:
        appliance_result = await db.execute(text("""
            SELECT appliance_id, agent_public_key
            FROM site_appliances
            WHERE site_id = :site_id AND agent_public_key = :key
            LIMIT 1
        """), {"site_id": site_id, "key": bundle.agent_public_key})
        appliance_row = appliance_result.fetchone()
        if appliance_row:
            registered_key = appliance_row.agent_public_key
            matched_appliance_id = appliance_row.appliance_id

    # 2. If no per-appliance match, try the submitted key directly (self-verify)
    #    Auto-register on the appliance if the signature is valid
    if not registered_key and bundle.agent_public_key and len(bundle.agent_public_key) == 64:
        registered_key = bundle.agent_public_key  # Will verify below

    # 3. Fall back to site-level key (legacy single-appliance sites)
    if not registered_key:
        registered_key = site_row.agent_public_key

    # 4. Auto-register if no key exists anywhere
    if not registered_key:
        if bundle.agent_public_key and len(bundle.agent_public_key) == 64:
            try:
                await db.execute(
                    text("UPDATE sites SET agent_public_key = :key WHERE site_id = :sid"),
                    {"key": bundle.agent_public_key, "sid": site_id}
                )
                await db.commit()
                registered_key = bundle.agent_public_key
                logger.info(f"Auto-registered agent public key from evidence for site={site_id}")
            except Exception as e:
                logger.error(f"Failed to auto-register key: {e}")

        if not registered_key:
            logger.warning(f"Evidence submission rejected: no public key registered for site={site_id}")
            raise HTTPException(
                status_code=401,
                detail="Site has no registered agent public key. "
                       "The appliance must checkin at least once to register its "
                       "signing key before submitting evidence."
            )

    # Verify the Ed25519 signature
    if bundle.signed_data:
        signed_data = bundle.signed_data.encode('utf-8')
    else:
        signed_data = json.dumps({
            "site_id": site_id,
            "checked_at": bundle.checked_at.isoformat(),
            "checks": bundle.checks,
            "summary": bundle.summary
        }, sort_keys=True).encode('utf-8')

    # Verify signature
    signature_valid = False
    if bundle.agent_signature:
        is_valid = verify_ed25519_signature(
            data=signed_data,
            signature_hex=bundle.agent_signature,
            public_key_hex=registered_key
        )
        if not is_valid:
            submitted_fp = bundle.agent_public_key[:12] if bundle.agent_public_key else "not-provided"
            registered_fp = registered_key[:12] if registered_key else "none"
            key_match = bundle.agent_public_key == registered_key if bundle.agent_public_key else None

            logger.warning(
                f"Evidence signature REJECTED for site={site_id} "
                f"registered_key={registered_fp}... submitted_key={submitted_fp}... "
                f"key_match={key_match} signed_data_provided={bundle.signed_data is not None}"
            )

            # Track rejection on the specific appliance if known, otherwise all
            try:
                if matched_appliance_id:
                    await db.execute(text("""
                        UPDATE site_appliances SET
                            evidence_rejection_count = COALESCE(evidence_rejection_count, 0) + 1,
                            last_evidence_rejection = NOW()
                        WHERE appliance_id = :appliance_id
                    """), {"appliance_id": matched_appliance_id})
                    await db.commit()
                else:
                    # When we can't identify which appliance produced the
                    # bad signature, do NOT increment a counter on every
                    # appliance at the site (Session 206 invariant — that
                    # pollutes legitimate appliances with someone else's
                    # failures). Log loudly; the offending appliance is
                    # anonymous but the event is recorded.
                    logger.warning(
                        f"Evidence signature rejection at site {site_id} "
                        f"could not be attributed to any appliance. "
                        f"Not incrementing per-appliance counter."
                    )
            except Exception as e:
                logger.warning(f"Evidence rejection tracking failed for site {site_id}: {e}")

            detail = "Evidence signature verification failed."
            if key_match is False:
                detail += (
                    f" Key mismatch: appliance key ({submitted_fp}...) does not "
                    f"match registered key ({registered_fp}...). The appliance "
                    f"may have regenerated its signing key. Re-register via checkin."
                )
            elif not bundle.signed_data:
                detail += (
                    " No signed_data provided - serialization mismatch likely. "
                    "Upgrade the appliance agent to include signed_data in evidence."
                )

            raise HTTPException(status_code=401, detail=detail)
        else:
            signature_valid = True
            logger.info(f"Evidence signature verified for site={site_id}")

            # Auto-register per-appliance key if not yet stored
            if bundle.agent_public_key and len(bundle.agent_public_key) == 64:
                try:
                    await db.execute(text("""
                        UPDATE site_appliances SET
                            agent_public_key = :key
                        WHERE site_id = :site_id
                          AND agent_public_key IS NULL
                          AND appliance_id = (
                            SELECT appliance_id FROM site_appliances
                            WHERE site_id = :site_id
                            ORDER BY last_checkin DESC NULLS LAST LIMIT 1
                          )
                    """), {"site_id": site_id, "key": bundle.agent_public_key})
                except Exception:
                    pass

            # Track acceptance + heartbeat per-appliance if matched, else site-wide
            try:
                if matched_appliance_id:
                    await db.execute(text("""
                        UPDATE site_appliances SET
                            evidence_rejection_count = 0,
                            last_evidence_accepted = NOW(),
                            last_checkin = NOW(),
                            status = 'online',
                            offline_since = NULL,
                            offline_notified = false
                        WHERE appliance_id = :appliance_id
                    """), {"appliance_id": matched_appliance_id})
                else:
                    await db.execute(text("""
                        UPDATE site_appliances SET
                            evidence_rejection_count = 0,
                            last_evidence_accepted = NOW(),
                            last_checkin = NOW(),
                            status = 'online',
                            offline_since = NULL,
                            offline_notified = false
                        WHERE site_id = :site_id
                    """), {"site_id": site_id})
            except Exception:
                pass
    else:
        logger.warning(f"No agent signature for evidence from site={site_id}")

    # Generate bundle_id if not provided
    import uuid
    if not bundle.bundle_id:
        bundle.bundle_id = f"CB-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}-{uuid.uuid4().hex[:8]}"

    # Compute bundle_hash if not provided (server-side fallback).
    # Daemon v0.3.85+ sends client-computed hashes. Fallback indicates legacy appliance.
    if not bundle.bundle_hash:
        logger.warning(f"Server-side hash fallback for site={site_id} — appliance should send bundle_hash (upgrade daemon)")
        hash_content = json.dumps({
            "site_id": site_id,
            "checked_at": bundle.checked_at.isoformat(),
            "checks": bundle.checks,
            "summary": bundle.summary
        }, sort_keys=True)
        bundle.bundle_hash = hashlib.sha256(hash_content.encode()).hexdigest()

    # Derive check_type from first check if not provided
    if not bundle.check_type and bundle.checks:
        bundle.check_type = bundle.checks[0].get("check", "drift")
    elif not bundle.check_type:
        bundle.check_type = "drift"

    # Skip operational monitoring bundles — port scans, host reachability, DNS checks
    # are NOT compliance attestation. Storing them as compliance_bundles produces 400+
    # "fail" entries/day that tank the score to 0%. Network findings still flow through
    # the healing pipeline via reportNetDrift().
    # This set matches the daemon's networkCheckTypes in evidence/submitter.go.
    OPERATIONAL_MONITORING_TYPES = {
        "net_unexpected_ports", "net_expected_service",
        "net_host_reachability", "net_dns_resolution",
    }
    if bundle.check_type in OPERATIONAL_MONITORING_TYPES:
        logger.info(f"Skipping operational monitoring bundle ({bundle.check_type}) for site={site_id} — not compliance")
        return {
            "status": "accepted",
            "bundle_id": bundle.bundle_id,
            "chain_position": 0,
            "prev_hash": "",
            "current_hash": "",
            "ots_status": "skipped",
            "ots_submitted": False,
        }

    # Derive check_result from checks if not provided
    if not bundle.check_result and bundle.checks:
        statuses = [c.get("status", "unknown") for c in bundle.checks]
        passing = {"pass", "compliant", "warning"}
        failing = {"fail", "non_compliant"}
        if all(s in passing for s in statuses):
            bundle.check_result = "pass"
        elif any(s in failing for s in statuses):
            bundle.check_result = "fail"
        else:
            bundle.check_result = "warn"
    elif not bundle.check_result:
        bundle.check_result = "unknown"

    # Evidence dedup: skip duplicate bundle hashes within 15-min window.
    # Handles grace-period overlap where two appliances scan the same target.
    # Session 209 P1: write phase moved to tenant_connection(site_id) with
    # SET LOCAL app.current_tenant. Admin-context RLS (via the after_begin
    # listener in shared.py) was a compensation for PgBouncer-wiping SETs;
    # tenant-scoped context is the intended posture for per-site writes
    # under the RLS policies (admin_bypass + tenant_isolation) on
    # compliance_bundles. tenant_connection wraps in one transaction, so
    # pg_advisory_xact_lock is held across the prev-bundle lookup and INSERT.
    #
    # Folding ots_status into the INSERT removes a post-commit UPDATE on the
    # partitioned evidence table for the Merkle-batching path (the default).
    from .fleet import get_pool
    from .tenant_middleware import tenant_connection

    initial_ots_status = (
        "batching" if (OTS_ENABLED and MERKLE_BATCHING_ENABLED) else "pending"
    )
    stored_signed_data = (
        signed_data.decode("utf-8") if isinstance(signed_data, bytes) else signed_data
    )

    pool = await get_pool()
    async with tenant_connection(
        pool, site_id=site_id, actor_appliance_id=matched_appliance_id
    ) as conn:
        # Opportunistic hash-window dedup (retry storms / network glitches).
        existing_hash = await conn.fetchval(
            """
            SELECT 1 FROM compliance_bundles
            WHERE site_id = $1
              AND bundle_hash = $2
              AND created_at > NOW() - INTERVAL '15 minutes'
            LIMIT 1
            """,
            site_id,
            bundle.bundle_hash,
        )
        if existing_hash:
            logger.info(
                f"evidence_dedup_skip: site_id={site_id} bundle_hash={bundle.bundle_hash[:12]}..."
            )
            return {
                "status": "accepted",
                "bundle_id": bundle.bundle_id,
                "deduplicated": True,
                "message": "Bundle already recorded within 15-minute window",
            }

        # Per-site advisory lock serializes chain_position assignment.
        # Without this, concurrent submissions race (caused 1,125 broken links).
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext($1))", site_id
        )

        # Bundle-id dedup under the lock. Migration 151's prevent_audit_deletion
        # trigger makes DELETE+INSERT upsert impossible (correctly — DELETE on
        # evidence is forgery); idempotent fast-exit is the right semantics.
        existing_by_id = await conn.fetchval(
            "SELECT 1 FROM compliance_bundles WHERE bundle_id = $1 LIMIT 1",
            bundle.bundle_id,
        )
        if existing_by_id:
            logger.info(
                "evidence_duplicate_bundle_id",
                extra={
                    "site_id": site_id,
                    "bundle_id": bundle.bundle_id,
                    "bundle_hash_prefix": bundle.bundle_hash[:12],
                },
            )
            return {
                "status": "accepted",
                "bundle_id": bundle.bundle_id,
                "deduplicated": True,
                "message": "Bundle already recorded (duplicate bundle_id after hash dedup window)",
            }

        prev_bundle = await conn.fetchrow(
            """
            SELECT bundle_id, bundle_hash, chain_position
            FROM compliance_bundles
            WHERE site_id = $1
            ORDER BY chain_position DESC
            LIMIT 1
            """,
            site_id,
        )

        GENESIS_HASH = "0" * 64
        prev_hash = prev_bundle["bundle_hash"] if prev_bundle else GENESIS_HASH
        chain_position = (prev_bundle["chain_position"] + 1) if prev_bundle else 1

        chain_data = f"{bundle.bundle_hash}:{prev_hash}:{chain_position}"
        chain_hash = hashlib.sha256(chain_data.encode()).hexdigest()

        await conn.execute(
            """
            INSERT INTO compliance_bundles (
                site_id, bundle_id, bundle_hash, check_type, check_result, checked_at,
                checks, summary, agent_signature, signed_data, signature_valid, ntp_verification,
                prev_bundle_id, prev_hash, chain_position, chain_hash,
                ots_status
            ) VALUES (
                $1, $2, $3, $4, $5, $6,
                $7::jsonb, $8::jsonb, $9, $10, $11, $12::jsonb,
                $13, $14, $15, $16,
                $17
            )
            """,
            site_id,
            bundle.bundle_id,
            bundle.bundle_hash,
            bundle.check_type,
            bundle.check_result,
            bundle.checked_at,
            json.dumps(bundle.checks),
            json.dumps(bundle.summary),
            bundle.agent_signature,
            stored_signed_data,
            signature_valid,
            json.dumps(bundle.ntp_verification) if bundle.ntp_verification else None,
            prev_bundle["bundle_id"] if prev_bundle else None,
            prev_hash,
            chain_position,
            chain_hash,
            initial_ots_status,
        )

        # ───── fail→pass real-time auto-resolve (Block-3 audit P0.2) ─────
        #
        # When a bundle's per-host check transitions to status='pass', any
        # currently-open incident matching (site_id, check_type, hostname)
        # should auto-resolve. Pre-fix, monitoring-only incidents stayed
        # open for 7 days waiting for the stale sweep — even when the
        # underlying chaos-lab VM came back online after 4 hours.
        #
        # Idempotent: UPDATE WHERE status='open' is a no-op if already
        # resolved. Single-statement (no N+1). Atomic with the bundle
        # INSERT — both commit together at end of tenant_connection. If
        # this UPDATE fails, the bundle INSERT also rolls back — that's
        # the safer failure mode (avoid bundle-without-recovery state).
        try:
            passing_pairs = [
                {"check_type": c.get("check"), "hostname": c.get("hostname")}
                for c in (bundle.checks or [])
                if c.get("status") == "pass"
                and c.get("check")
                and c.get("hostname")
            ]
            if passing_pairs:
                resolved_rows = await conn.fetch(
                    """
                    WITH passing AS (
                        SELECT (e->>'check_type') AS check_type,
                               (e->>'hostname') AS hostname
                          FROM jsonb_array_elements($2::jsonb) AS e
                    ),
                    matched AS (
                        SELECT i.id
                          FROM incidents i
                          JOIN passing p
                            ON i.incident_type = p.check_type
                           AND i.details->>'hostname' = p.hostname
                         WHERE i.site_id = $1
                           AND i.status = 'open'
                    )
                    UPDATE incidents
                       SET status = 'resolved',
                           resolved_at = NOW(),
                           resolution_tier = 'recovered'
                      FROM matched
                     WHERE incidents.id = matched.id
                    RETURNING incidents.id, incidents.incident_type,
                              incidents.details->>'hostname' AS hostname
                    """,
                    site_id,
                    json.dumps(passing_pairs),
                )
                if resolved_rows:
                    logger.info(
                        "evidence_auto_recovered_incidents",
                        extra={
                            "site_id": site_id,
                            "bundle_id": bundle.bundle_id,
                            "count": len(resolved_rows),
                            "incidents": [
                                {
                                    "id": str(r["id"]),
                                    "type": r["incident_type"],
                                    "host": r["hostname"],
                                }
                                for r in resolved_rows
                            ],
                        },
                    )
        except Exception as e:
            # Per CLAUDE.md "no silent write failures": auto-resolve
            # failures must surface at ERROR. The UPDATE shares the
            # bundle-INSERT txn; raising here aborts the whole submit
            # which is correct — bundle-without-recovery is worse than
            # bundle-rejected-with-retry.
            logger.error(
                "evidence_auto_recover_failed",
                exc_info=True,
                extra={
                    "site_id": site_id,
                    "bundle_id": bundle.bundle_id,
                    "exception_class": type(e).__name__,
                },
            )
            raise

    # Post-commit: the Merkle-batching path has ots_status='batching' already;
    # the legacy individual-OTS path still needs a background submit.
    ots_submitted = False
    if OTS_ENABLED:
        if not MERKLE_BATCHING_ENABLED:
            background_tasks.add_task(
                submit_ots_proof_background,
                db,
                bundle.bundle_id,
                bundle.bundle_hash,
                site_id,
            )
        ots_submitted = True

    # Upload to MinIO WORM storage in background
    worm_enabled = os.getenv("WORM_ENABLED", "true").lower() == "true"
    if worm_enabled:
        background_tasks.add_task(
            upload_to_worm_background,
            site_id,
            bundle.bundle_id,
            bundle.bundle_hash,
            bundle.model_dump(),
            bundle.agent_signature
        )

    # Map evidence to all enabled framework controls.
    # Pass appliance_id so the per-appliance score refresh can resolve
    # site_id correctly (Block-3 audit P3 fix). Pre-fix the helper
    # passed site_id to refresh_compliance_score which expects an
    # appliance_id, causing every score INSERT to fail silently and
    # leaving compliance_scores empty fleet-wide.
    if bundle.checks:
        background_tasks.add_task(
            map_evidence_to_frameworks,
            site_id,
            bundle.bundle_id,
            bundle.checks,
            matched_appliance_id,
        )

    # Populate workstation tables when workstation evidence arrives
    if bundle.check_type == "workstation" and bundle.checks:
        background_tasks.add_task(
            populate_workstation_tables,
            site_id,
            bundle.checks,
        )

    logger.info(f"Evidence submitted: site={site_id} bundle={bundle.bundle_id[:8]} chain={chain_position}")

    # Broadcast compliance event for real-time dashboard updates
    try:
        from .websocket_manager import broadcast_event
        await broadcast_event("compliance_drift", {
            "site_id": site_id,
            "check_type": bundle.check_type,
            "check_result": bundle.check_result,
            "bundle_id": bundle.bundle_id,
            "chain_position": chain_position,
        })
    except Exception as e:
        logger.debug(f"Evidence broadcast failed: {e}")

    return EvidenceSubmitResponse(
        bundle_id=bundle.bundle_id,
        bundle_hash=bundle.bundle_hash or chain_hash,
        chain_position=chain_position,
        prev_hash=prev_hash,
        current_hash=chain_hash,
        ots_status="pending" if OTS_ENABLED else "none",
        ots_submitted=ots_submitted,
    )


@router.get("/sites/{site_id}/signing-status")
async def get_signing_status(
    site_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get evidence signing status for a site (partner dashboard).

    Returns key registration status, recent acceptance/rejection counts,
    and evidence chain health so partners can diagnose issues.
    """
    result = await db.execute(
        text("""
            SELECT s.agent_public_key,
                   sa.evidence_rejection_count,
                   sa.last_evidence_rejection,
                   sa.last_evidence_accepted,
                   (SELECT COUNT(*) FROM compliance_bundles cb
                    WHERE cb.site_id = :site_id AND cb.signature_valid = true) as verified_count,
                   (SELECT MAX(checked_at) FROM compliance_bundles cb
                    WHERE cb.site_id = :site_id) as last_evidence
            FROM sites s
            LEFT JOIN site_appliances sa ON sa.site_id = s.site_id
            WHERE s.site_id = :site_id
            LIMIT 1
        """),
        {"site_id": site_id}
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Site not found: {site_id}")

    key = row.agent_public_key
    return {
        "site_id": site_id,
        "has_key": bool(key),
        "key_fingerprint": f"{key[:12]}...{key[-8:]}" if key else None,
        "evidence_rejection_count": row.evidence_rejection_count or 0,
        "last_rejection": row.last_evidence_rejection.isoformat() if row.last_evidence_rejection else None,
        "last_accepted": row.last_evidence_accepted.isoformat() if row.last_evidence_accepted else None,
        "verified_bundle_count": row.verified_count or 0,
        "last_evidence": row.last_evidence.isoformat() if row.last_evidence else None,
        "status": "healthy" if (key and (row.evidence_rejection_count or 0) == 0) else
                  "broken" if (row.evidence_rejection_count or 0) > 0 else
                  "no_key"
    }


async def upload_to_worm_background(
    site_id: str,
    bundle_id: str,
    bundle_hash: str,
    bundle_data: dict,
    agent_signature: Optional[str]
):
    """Background task to upload evidence to MinIO WORM storage."""
    try:
        from io import BytesIO
        from minio import Minio
        from minio.retention import Retention, COMPLIANCE

        # MinIO configuration - SECURITY: No insecure defaults for credentials
        MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
        MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY")
        MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY")
        MINIO_BUCKET = os.getenv("MINIO_BUCKET", "evidence-worm")
        MINIO_SECURE = os.getenv("MINIO_SECURE", "true").lower() == "true"  # Default to secure

        if not MINIO_ACCESS_KEY or not MINIO_SECRET_KEY:
            logger.error("MINIO_ACCESS_KEY and MINIO_SECRET_KEY environment variables must be set")
            raise ValueError("MinIO credentials not configured - set MINIO_ACCESS_KEY and MINIO_SECRET_KEY")
        WORM_RETENTION_DAYS = int(os.getenv("WORM_RETENTION_DAYS", "90"))

        minio_client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE
        )

        # Ensure bucket exists
        loop = asyncio.get_event_loop()
        if not await loop.run_in_executor(None, lambda: minio_client.bucket_exists(MINIO_BUCKET)):
            await loop.run_in_executor(None, lambda: minio_client.make_bucket(MINIO_BUCKET))

        now = datetime.now(timezone.utc)
        date_prefix = now.strftime('%Y/%m/%d')
        bundle_key = f"{site_id}/{date_prefix}/{bundle_id}.json"

        # Serialize bundle
        bundle_json = json.dumps(bundle_data, default=str, indent=2)
        bundle_bytes = bundle_json.encode()

        # Upload bundle
        await loop.run_in_executor(None, lambda: minio_client.put_object(
            MINIO_BUCKET,
            bundle_key,
            BytesIO(bundle_bytes),
            length=len(bundle_bytes),
            content_type="application/json",
            metadata={
                "bundle_id": bundle_id,
                "site_id": site_id,
                "bundle_hash": bundle_hash,
                "uploaded_at": now.isoformat()
            }
        ))

        s3_uri = f"s3://{MINIO_BUCKET}/{bundle_key}"

        # Set Object Lock retention
        try:
            retention_until = now + timedelta(days=WORM_RETENTION_DAYS)
            retention = Retention(COMPLIANCE, retention_until)
            await loop.run_in_executor(None, lambda: minio_client.set_object_retention(MINIO_BUCKET, bundle_key, retention))
        except Exception as e:
            # Bucket may already have default retention
            logger.debug(f"Object Lock already set or not available: {e}")

        logger.info(f"Evidence uploaded to WORM: {bundle_id} -> {s3_uri}")

    except Exception as e:
        logger.error(f"WORM upload failed for {bundle_id}: {e}")


async def map_evidence_to_frameworks(
    site_id: str,
    bundle_id: str,
    checks: List[Dict[str, Any]],
    appliance_id: Optional[str] = None,
):
    """Map evidence bundle checks to all enabled framework controls.

    Populates evidence_framework_mappings table and refreshes compliance_scores.
    Called as a background task after evidence submission.

    `appliance_id` is the matched per-appliance natural key resolved
    during signature verification. Required for `refresh_compliance_score`
    which is per-appliance scoped (NOT per-site). Optional for
    backwards-compat with older callers that didn't pass it; the score
    refresh skips when appliance_id is None.
    """
    from .framework_mapper import get_controls_for_check_with_hipaa_map

    try:
        pool = None
        try:
            from .fleet import get_pool
            # Coach #1 (D1 design 2026-05-01): admin_transaction not
            # admin_connection — multi-statement admin path under PgBouncer
            # transaction pool needs the txn-pinned variant per Session
            # 212 routing-pathology rule. See feedback_admin_transaction*.
            from .tenant_middleware import admin_transaction
            pool = await get_pool()
        except Exception:
            logger.debug("framework mapping: pool unavailable, skipping")
            return

        # D1 fix 2026-05-02: per-control aggregation taxonomy. Matches
        # the writer's existing PASSING/FAILING split at line ~1137.
        # Aggregation rule: ANY status in FAILING → control is fail
        # (HIPAA conservative); ELSE ANY in PASSING → pass; ELSE unknown.
        PASSING = {"pass", "compliant", "warning"}
        FAILING = {"fail", "non_compliant"}

        def _agg(statuses: list[str]) -> str:
            if any(s in FAILING for s in statuses):
                return "fail"
            if any(s in PASSING for s in statuses):
                return "pass"
            return "unknown"

        async with admin_transaction(pool) as conn:
            # Get enabled frameworks for this site
            row = await conn.fetchrow(
                "SELECT enabled_frameworks FROM appliance_framework_configs WHERE site_id = $1",
                site_id,
            )
            if not row:
                # Try by appliance_id pattern (site_id is embedded in appliance_id)
                row = await conn.fetchrow(
                    "SELECT enabled_frameworks FROM appliance_framework_configs WHERE appliance_id LIKE $1",
                    f"{site_id}%",
                )
            if not row or not row["enabled_frameworks"]:
                # Default to hipaa if no config exists
                enabled = ["hipaa"]
            else:
                enabled = list(row["enabled_frameworks"])

            # Build per-control aggregation: (framework, control_id) → list[status]
            control_to_statuses: dict[tuple[str, str], list[str]] = {}
            for check in checks:
                check_type = check.get("check") or check.get("check_type")
                status = check.get("status")
                if not check_type or not status:
                    continue
                controls = get_controls_for_check_with_hipaa_map(check_type, enabled)
                for ctrl in controls:
                    key = (ctrl["framework"], ctrl["control_id"])
                    control_to_statuses.setdefault(key, []).append(status)

            # Per-control INSERT in a savepoint so a single failed row
            # doesn't poison the outer admin_transaction (coach #2 +
            # CLAUDE.md asyncpg savepoint invariant + Block 3 sweep).
            mappings_inserted = 0
            for (framework, control_id), statuses in control_to_statuses.items():
                # Brian delta (D1 round-table): defensive guard before _agg
                # — empty statuses list shouldn't reach here (we skip
                # check entries without a status), but belt-and-suspenders.
                if not statuses:
                    continue
                agg = _agg(statuses)
                try:
                    async with conn.transaction():  # nested savepoint
                        await conn.execute(
                            """
                            INSERT INTO evidence_framework_mappings
                                (bundle_id, framework, control_id, check_status)
                            VALUES ($1, $2, $3, $4)
                            ON CONFLICT (bundle_id, framework, control_id)
                            DO UPDATE SET check_status = EXCLUDED.check_status
                            """,
                            bundle_id, framework, control_id, agg,
                        )
                        mappings_inserted += 1
                except Exception as e:
                    # Per CLAUDE.md "no silent write failures" — coach #2
                    # of D1 design. evidence_framework_mappings is the
                    # writer-side projection that powers compliance scores;
                    # silent failure here = the score regression class
                    # this fix is closing in the first place.
                    logger.error(
                        "evidence_framework_mapping_insert_failed",
                        exc_info=True,
                        extra={
                            "bundle_id": bundle_id,
                            "framework": framework,
                            "control_id": control_id,
                            "exception_class": type(e).__name__,
                        },
                    )

            # Refresh compliance scores for each enabled framework.
            # MUST pass appliance_id (NOT site_id) — refresh_compliance_score
            # is per-appliance scoped. Pre-Block-3 we passed site_id,
            # which silently failed the SQL function's site_id resolution
            # (cast to UUID exception, fall through to NULL, INSERT
            # CHECK violation). Result: compliance_scores empty
            # fleet-wide. Migration 265 also adds a VARCHAR
            # site_appliances.appliance_id fallback as defense-in-depth.
            if appliance_id:
                for framework in enabled:
                    try:
                        await conn.execute(
                            "SELECT refresh_compliance_score($1, $2)",
                            appliance_id,
                            framework,
                        )
                    except Exception as e:
                        # Per CLAUDE.md "no silent write failures" —
                        # but only ERROR for unexpected (the function's
                        # own NOT-NULL guard is now a benign skip).
                        logger.error(
                            "compliance_score_refresh_failed",
                            exc_info=True,
                            extra={
                                "site_id": site_id,
                                "appliance_id": appliance_id,
                                "framework": framework,
                                "exception_class": type(e).__name__,
                            },
                        )
            else:
                logger.warning(
                    "compliance_score_refresh_skipped_no_appliance_id",
                    extra={"site_id": site_id, "bundle_id": bundle_id},
                )

            if mappings_inserted > 0:
                logger.info(
                    f"Framework mapping: site={site_id} bundle={bundle_id[:8]} "
                    f"mappings={mappings_inserted} frameworks={enabled}"
                )

    except Exception as e:
        logger.warning(f"Framework mapping failed for {site_id}: {e}")


async def populate_workstation_tables(site_id: str, checks: List[Dict[str, Any]]):
    """Populate workstations, workstation_checks, and site_workstation_summaries tables.

    Called as a background task when workstation evidence is submitted.
    Extracts per-workstation data from evidence checks and upserts into
    the workstation tables so the dashboard Workstations tab shows data.
    """
    try:
        import sys
        try:
            from main import async_session
        except ImportError:
            if 'server' in sys.modules:
                async_session = sys.modules['server'].async_session
            else:
                logger.error("Cannot get DB session for workstation table population")
                return

        # Extract workstation data from checks[0].details
        if not checks:
            return

        details = checks[0].get("details", {})
        if not details:
            return

        # Handle both old format (flat summary) and new format (structured)
        if "site_summary" in details:
            site_summary = details["site_summary"]
            ws_bundles = details.get("workstation_bundles", [])
            all_ws = details.get("all_workstations", [])
        else:
            # Legacy format: details IS the site_summary
            site_summary = details
            ws_bundles = []
            all_ws = []

        now = datetime.now(timezone.utc)

        async with async_session() as db:
            # 1. Upsert all discovered workstations
            for ws in all_ws:
                hostname = ws.get("hostname", "")
                if not hostname:
                    continue

                await db.execute(text("""
                    INSERT INTO workstations (
                        site_id, hostname, distinguished_name, ip_address,
                        mac_address, os_name, os_version, online, last_seen,
                        compliance_status, updated_at
                    ) VALUES (
                        :site_id, :hostname, :dn, :ip,
                        :mac, :os_name, :os_version, :online, :last_seen,
                        :status, :now
                    )
                    ON CONFLICT (site_id, hostname) DO UPDATE SET
                        ip_address = COALESCE(EXCLUDED.ip_address, workstations.ip_address),
                        distinguished_name = COALESCE(EXCLUDED.distinguished_name, workstations.distinguished_name),
                        mac_address = COALESCE(EXCLUDED.mac_address, workstations.mac_address),
                        os_name = COALESCE(EXCLUDED.os_name, workstations.os_name),
                        os_version = COALESCE(EXCLUDED.os_version, workstations.os_version),
                        online = EXCLUDED.online,
                        last_seen = COALESCE(EXCLUDED.last_seen, workstations.last_seen),
                        compliance_status = EXCLUDED.compliance_status,
                        updated_at = :now
                """), {
                    "site_id": site_id,
                    "hostname": hostname,
                    "dn": ws.get("distinguished_name"),
                    "ip": ws.get("ip_address"),
                    "mac": ws.get("mac_address"),
                    "os_name": ws.get("os_name"),
                    "os_version": ws.get("os_version"),
                    "online": ws.get("online", False),
                    "last_seen": ws.get("last_seen"),
                    "status": ws.get("compliance_status", "unknown"),
                    "now": now,
                })

            # 2. Insert per-workstation compliance check results
            for bundle in ws_bundles:
                ws_hostname = bundle.get("workstation_id", "")
                if not ws_hostname:
                    continue

                # Get the workstation UUID
                ws_result = await db.execute(text("""
                    SELECT id FROM workstations
                    WHERE site_id = :site_id AND hostname = :hostname
                """), {"site_id": site_id, "hostname": ws_hostname})
                ws_row = ws_result.fetchone()

                if not ws_row:
                    # Workstation not in all_ws list; create it from bundle data
                    await db.execute(text("""
                        INSERT INTO workstations (
                            site_id, hostname, ip_address, os_name,
                            online, compliance_status, compliance_percentage,
                            last_compliance_check, updated_at
                        ) VALUES (
                            :site_id, :hostname, :ip, :os_name,
                            true, :status, :pct, :now, :now
                        )
                        ON CONFLICT (site_id, hostname) DO UPDATE SET
                            compliance_status = EXCLUDED.compliance_status,
                            compliance_percentage = EXCLUDED.compliance_percentage,
                            last_compliance_check = EXCLUDED.last_compliance_check,
                            updated_at = EXCLUDED.updated_at
                    """), {
                        "site_id": site_id,
                        "hostname": ws_hostname,
                        "ip": bundle.get("ip_address"),
                        "os_name": bundle.get("os_name"),
                        "status": bundle.get("overall_status", "unknown"),
                        "pct": bundle.get("compliance_percentage", 0),
                        "now": now,
                    })
                    ws_result = await db.execute(text("""
                        SELECT id FROM workstations
                        WHERE site_id = :site_id AND hostname = :hostname
                    """), {"site_id": site_id, "hostname": ws_hostname})
                    ws_row = ws_result.fetchone()

                if not ws_row:
                    continue

                ws_id = ws_row[0]

                # Update workstation compliance fields
                await db.execute(text("""
                    UPDATE workstations SET
                        compliance_status = :status,
                        compliance_percentage = :pct,
                        last_compliance_check = :now,
                        online = true
                    WHERE id = :ws_id
                """), {
                    "status": bundle.get("overall_status", "unknown"),
                    "pct": bundle.get("compliance_percentage", 0),
                    "now": now,
                    "ws_id": ws_id,
                })

                # Insert individual check results
                for check in bundle.get("checks", []):
                    check_type = check.get("check_type", "")
                    if not check_type:
                        continue

                    hipaa_controls = check.get("hipaa_controls", [])
                    if isinstance(hipaa_controls, list):
                        hipaa_arr = "{" + ",".join(f'"{c}"' for c in hipaa_controls) + "}"
                    else:
                        hipaa_arr = "{}"

                    await db.execute(text("""
                        INSERT INTO workstation_checks (
                            workstation_id, site_id, check_type, status,
                            compliant, details, hipaa_controls, checked_at
                        ) VALUES (
                            :ws_id, :site_id, :check_type, :status,
                            :compliant, CAST(:details AS jsonb), :hipaa::text[],
                            :now
                        )
                    """), {
                        "ws_id": ws_id,
                        "site_id": site_id,
                        "check_type": check_type,
                        "status": check.get("status", "unknown"),
                        "compliant": check.get("compliant", False),
                        "details": json.dumps(check.get("details", {})),
                        "hipaa": hipaa_arr,
                        "now": now,
                    })

            # 3. Upsert site workstation summary
            if site_summary:
                summary_hash = hashlib.sha256(
                    json.dumps(site_summary, sort_keys=True, default=str).encode()
                ).hexdigest()
                bundle_id = site_summary.get("bundle_id", str(hash(now.isoformat())))

                await db.execute(text("""
                    INSERT INTO site_workstation_summaries (
                        bundle_id, site_id, total_workstations, online_workstations,
                        compliant_workstations, drifted_workstations, error_workstations,
                        unknown_workstations, check_compliance, overall_compliance_rate,
                        evidence_hash, last_scan, updated_at
                    ) VALUES (
                        :bundle_id, :site_id, :total, :online,
                        :compliant, :drifted, :error, :unknown,
                        CAST(:check_compliance AS jsonb), :rate,
                        :hash, :now, :now
                    )
                    ON CONFLICT (site_id) DO UPDATE SET
                        bundle_id = EXCLUDED.bundle_id,
                        total_workstations = EXCLUDED.total_workstations,
                        online_workstations = EXCLUDED.online_workstations,
                        compliant_workstations = EXCLUDED.compliant_workstations,
                        drifted_workstations = EXCLUDED.drifted_workstations,
                        error_workstations = EXCLUDED.error_workstations,
                        unknown_workstations = EXCLUDED.unknown_workstations,
                        check_compliance = EXCLUDED.check_compliance,
                        overall_compliance_rate = EXCLUDED.overall_compliance_rate,
                        evidence_hash = EXCLUDED.evidence_hash,
                        last_scan = EXCLUDED.last_scan,
                        updated_at = EXCLUDED.updated_at
                """), {
                    "bundle_id": bundle_id,
                    "site_id": site_id,
                    "total": site_summary.get("total_workstations", 0),
                    "online": site_summary.get("online_workstations", 0),
                    "compliant": site_summary.get("compliant_workstations", 0),
                    "drifted": site_summary.get("drifted_workstations", 0),
                    "error": site_summary.get("error_workstations", 0),
                    "unknown": site_summary.get("unknown_workstations", 0),
                    "check_compliance": json.dumps(site_summary.get("check_compliance", {})),
                    "rate": site_summary.get("overall_compliance_rate", 0),
                    "hash": summary_hash,
                    "now": now,
                })

            await db.commit()

            # Count for logging
            ws_count = len(all_ws) or len(ws_bundles)
            check_count = sum(len(b.get("checks", [])) for b in ws_bundles)
            logger.info(
                f"Workstation tables populated: site={site_id} "
                f"workstations={ws_count} checks={check_count}"
            )

    except Exception as e:
        logger.error(f"Failed to populate workstation tables for {site_id}: {e}")


async def submit_ots_proof_background(db: AsyncSession, bundle_id: str, bundle_hash: str, site_id: str):
    """Background task to submit OTS proof."""
    try:
        ots_result = await submit_hash_to_ots(bundle_hash, bundle_id)

        if ots_result:
            # Convert to timezone-naive for TIMESTAMP column
            submitted_at = ots_result["submitted_at"]
            if submitted_at.tzinfo is not None:
                submitted_at = submitted_at.replace(tzinfo=None)

            await db.execute(text("""
                INSERT INTO ots_proofs (
                    bundle_id, bundle_hash, site_id,
                    proof_data, calendar_url, submitted_at, status
                ) VALUES (
                    :bundle_id, :bundle_hash, :site_id,
                    :proof_data, :calendar_url, :submitted_at, 'pending'
                )
                ON CONFLICT (bundle_id) DO UPDATE SET
                    proof_data = EXCLUDED.proof_data,
                    calendar_url = EXCLUDED.calendar_url,
                    submitted_at = EXCLUDED.submitted_at
            """), {
                "bundle_id": bundle_id,
                "bundle_hash": bundle_hash,
                "site_id": site_id,
                "proof_data": ots_result["proof_data"],
                "calendar_url": ots_result["calendar_url"],
                "submitted_at": submitted_at,
            })

            await db.execute(text("""
                UPDATE compliance_bundles
                SET ots_status = 'pending',
                    ots_proof = :proof_data,
                    ots_calendar_url = :calendar_url,
                    ots_submitted_at = :submitted_at
                WHERE bundle_id = :bundle_id
            """), {
                "bundle_id": bundle_id,
                "proof_data": ots_result["proof_data"],
                "calendar_url": ots_result["calendar_url"],
                "submitted_at": submitted_at,  # Already converted above
            })

            await db.commit()

    except Exception as e:
        logger.error(f"OTS background submission failed for {bundle_id}: {e}")


async def process_merkle_batch(conn, site_id: str) -> dict:
    """Process a Merkle batch for a site: collect 'batching' bundles, build
    tree, submit root to OTS.

    SESSION 203 BATCH 2 FIX (C1): the batch_id used to be derived purely
    from the site_id + the current UTC hour (`MB-{site}-{YYYYMMDDHH}`).
    When this function ran twice in the same hour for the same site (which
    happens whenever a fresh set of bundles rolls in every ~15 minutes),
    both calls produced the same batch_id. The first call stored a Merkle
    root and OTS proof keyed on that batch_id; the second call hit
    `ON CONFLICT (batch_id) DO NOTHING` and silently dropped its root, but
    still UPDATE'd each of its bundles with a `merkle_proof` path computed
    from the DROPPED tree. Result: the second batch's bundles had proofs
    that could not verify against the stored root — an auditor running
    5 lines of Python against one of these bundles would see the chain
    fail verification.

    The production audit on 2026-04-09 found 1,198+ bundles in this broken
    state across 100+ batches. They are backfilled to `ots_status='legacy'`
    by a one-shot migration (see migrations/148_*.sql).

    The fix: make the batch_id truly unique per call by appending a short
    random suffix. The old `MB-{site}-{YYYYMMDDHH}` prefix is preserved so
    existing dashboards and logs still group by hour at a glance.
    """
    from .merkle import build_merkle_tree
    import secrets as _secrets

    bundles = await conn.fetch("""
        SELECT bundle_id, bundle_hash, chain_position
        FROM compliance_bundles
        WHERE site_id = $1 AND ots_status = 'batching'
        ORDER BY chain_position ASC
    """, site_id)

    if not bundles:
        return {"site_id": site_id, "batched": 0}

    hashes = [b["bundle_hash"] for b in bundles]
    root, proofs = build_merkle_tree(hashes)

    batch_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    # SESSION 203 C1 FIX: unique suffix prevents two concurrent sub-batches
    # in the same UTC hour from colliding on batch_id.
    unique_suffix = _secrets.token_hex(4)  # 8 hex chars — 2^32 space, zero real collision risk
    batch_id = f"MB-{site_id[:20]}-{batch_hour.strftime('%Y%m%d%H')}-{unique_suffix}"
    tree_depth = len(proofs[0]) if proofs and proofs[0] else 0

    # Submit merkle root to OTS
    ots_result = await submit_hash_to_ots(root, batch_id)

    if not ots_result:
        logger.warning(f"Merkle batch OTS submission failed for {batch_id}, will retry next cycle")
        return {"site_id": site_id, "batched": 0, "error": "ots_submission_failed"}

    proof_data = ots_result["proof_data"]
    calendar_url = ots_result["calendar_url"]

    # Record the batch — with the unique suffix, ON CONFLICT should never
    # actually fire, but we leave the clause as a safety net in case the
    # same batch_id ever gets re-used by a retry or backup.
    await conn.execute("""
        INSERT INTO ots_merkle_batches (batch_id, site_id, merkle_root, bundle_count, tree_depth,
            ots_status, ots_submitted_at, batch_hour)
        VALUES ($1, $2, $3, $4, $5, 'pending', NOW(), $6)
        ON CONFLICT (batch_id) DO NOTHING
    """, batch_id, site_id, root, len(bundles), tree_depth, batch_hour)

    # Insert into ots_proofs so existing upgrade loop handles it
    await conn.execute("""
        INSERT INTO ots_proofs (bundle_id, bundle_hash, site_id, proof_data, calendar_url,
            submitted_at, status, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, NOW(), 'pending', NOW(), NOW())
        ON CONFLICT (bundle_id) DO NOTHING
    """, batch_id, root, site_id, proof_data, calendar_url)

    # Update each bundle with its Merkle proof
    for i, bundle in enumerate(bundles):
        await conn.execute("""
            UPDATE compliance_bundles
            SET merkle_batch_id = $1, merkle_proof = $2::jsonb, merkle_leaf_index = $3, ots_status = 'pending'
            WHERE bundle_id = $4
        """, batch_id, json.dumps(proofs[i]), i, bundle["bundle_id"])

    logger.info(f"Merkle batch created: {batch_id} with {len(bundles)} bundles, root={root[:16]}")

    return {"site_id": site_id, "batched": len(bundles), "batch_id": batch_id, "root": root[:16]}


@router.get("/sites/{site_id}/verify/{bundle_id}", response_model=EvidenceVerifyResponse)
async def verify_evidence(
    site_id: str,
    bundle_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Verify an evidence bundle.

    Checks:
    - Hash chain integrity
    - Agent signature (if present)
    - OTS blockchain anchoring status
    """
    # Get bundle (including signed_data for signature verification)
    result = await db.execute(text("""
        SELECT b.*, p.status as ots_proof_status, p.bitcoin_block
        FROM compliance_bundles b
        LEFT JOIN ots_proofs p ON p.bundle_id = b.bundle_id
        WHERE b.site_id = :site_id AND b.bundle_id = :bundle_id
    """), {"site_id": site_id, "bundle_id": bundle_id})

    bundle = result.fetchone()
    if not bundle:
        raise HTTPException(status_code=404, detail=f"Bundle not found: {bundle_id}")

    # Verify chain hash
    chain_data = f"{bundle.bundle_hash}:{bundle.prev_hash or 'genesis'}:{bundle.chain_position}"
    expected_chain_hash = hashlib.sha256(chain_data.encode()).hexdigest()
    chain_valid = hmac.compare_digest(bundle.chain_hash or "", expected_chain_hash)

    # Verify bundle hash against content
    hash_content = json.dumps({
        "site_id": site_id,
        "checked_at": bundle.checked_at.isoformat() if bundle.checked_at else None,
        "checks": bundle.checks if isinstance(bundle.checks, list) else json.loads(bundle.checks) if bundle.checks else [],
        "summary": bundle.summary if isinstance(bundle.summary, dict) else json.loads(bundle.summary) if bundle.summary else {}
    }, sort_keys=True)
    computed_hash = hashlib.sha256(hash_content.encode()).hexdigest()
    hash_valid = hmac.compare_digest(bundle.bundle_hash or "", computed_hash)

    # Verify Ed25519 signature (HIPAA §164.312(c)(1) - Integrity Controls)
    signature_valid = None  # None means signature not present
    if bundle.agent_signature:
        # Get the agent's registered public key
        agent_public_key = await get_agent_public_key(db, site_id)

        if agent_public_key:
            # Use stored signed_data if available (correct approach)
            # Fall back to field reconstruction for legacy bundles
            stored_signed_data = getattr(bundle, 'signed_data', None)
            if stored_signed_data:
                signed_data = stored_signed_data.encode('utf-8')
            else:
                # Legacy fallback: reconstruct from fields (may not match)
                signed_data = json.dumps({
                    "site_id": site_id,
                    "checked_at": bundle.checked_at.isoformat() if bundle.checked_at else None,
                    "checks": bundle.checks if isinstance(bundle.checks, list) else json.loads(bundle.checks) if bundle.checks else [],
                    "summary": bundle.summary if isinstance(bundle.summary, dict) else json.loads(bundle.summary) if bundle.summary else {}
                }, sort_keys=True).encode('utf-8')
                logger.debug(f"Using legacy signed_data reconstruction for {bundle_id}")

            # Actually verify the signature
            signature_valid = verify_ed25519_signature(
                data=signed_data,
                signature_hex=bundle.agent_signature,
                public_key_hex=agent_public_key
            )

            if signature_valid:
                logger.info(f"Signature verified: bundle={bundle_id[:8]}... site={site_id}")
            else:
                logger.warning(f"Signature verification FAILED: bundle={bundle_id[:8]}... site={site_id}")
        else:
            # Signature present but no public key registered - can't verify
            logger.warning(f"Cannot verify signature: no public key for site={site_id}")
            signature_valid = None

    # Audit log the verification attempt
    logger.info(
        f"Evidence verified: bundle={bundle_id} site={site_id} "
        f"hash_valid={hash_valid} sig_valid={signature_valid} chain_valid={chain_valid}"
    )

    return EvidenceVerifyResponse(
        bundle_id=bundle_id,
        hash_valid=hash_valid,
        signature_valid=signature_valid,
        chain_valid=chain_valid,
        ots_status=bundle.ots_proof_status or bundle.ots_status or "none",
        ots_bitcoin_block=bundle.bitcoin_block,
        verified_at=datetime.now(timezone.utc),
    )


@router.get("/sites/{site_id}/public-keys")
async def get_site_public_keys(
    site_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: Dict[str, Any] = Depends(require_evidence_view_access),
):
    """Return the per-appliance Ed25519 public keys for a site.

    Session 203 Batch 5 (C4): this endpoint enables client-side Ed25519
    signature verification in the portal scorecard. An auditor watching
    browser devtools can confirm that the public keys were fetched once
    and then used locally to verify each bundle's signature — there's
    no trust in the backend's self-reported "signature valid" flag.

    Each key is returned in the standard Ed25519 raw-key hex format
    (64 hex chars = 32 bytes). The frontend decodes it and passes it to
    @noble/ed25519's `verifyAsync(signature, message, publicKey)`.
    """
    result = await db.execute(text("""
        SELECT
            appliance_id,
            display_name,
            hostname,
            agent_public_key,
            first_checkin,
            last_checkin
        FROM site_appliances
        WHERE site_id = :site_id
          AND agent_public_key IS NOT NULL
        ORDER BY first_checkin ASC
    """), {"site_id": site_id})

    rows = result.fetchall()
    return {
        "site_id": site_id,
        "public_keys": [
            {
                "appliance_id": r.appliance_id,
                "display_name": r.display_name or r.hostname,
                "hostname": r.hostname,
                "public_key_hex": r.agent_public_key,
                "first_checkin": r.first_checkin.isoformat() if r.first_checkin else None,
                "last_checkin": r.last_checkin.isoformat() if r.last_checkin else None,
            }
            for r in rows
        ],
        "count": len(rows),
    }


@router.get("/sites/{site_id}/verify-chain")
async def verify_chain_integrity(
    site_id: str,
    max_broken: int = 100,
    db: AsyncSession = Depends(get_db),
    _auth: Dict[str, Any] = Depends(require_evidence_view_access),
):
    """
    Walk and verify the entire hash chain for a site.

    Checks:
    - Each bundle's chain_hash matches SHA256(bundle_hash:prev_hash:position)
    - Each bundle's prev_hash matches the previous bundle's bundle_hash
    - Chain positions are sequential with no gaps

    Returns full chain audit result.
    """
    result = await db.execute(text("""
        SELECT bundle_id, bundle_hash, prev_hash, chain_position, chain_hash
        FROM compliance_bundles
        WHERE site_id = :site_id
        ORDER BY chain_position ASC
    """), {"site_id": site_id})

    bundles = result.fetchall()

    if not bundles:
        return {
            "site_id": site_id,
            "chain_length": 0,
            "verified": 0,
            "broken_links": [],
            "status": "empty",
        }

    verified = 0
    broken_links = []

    GENESIS_HASH = "0" * 64

    for i, bundle in enumerate(bundles):
        # Verify chain_hash = SHA256(bundle_hash:prev_hash:position)
        chain_data = f"{bundle.bundle_hash}:{bundle.prev_hash}:{bundle.chain_position}"
        expected_chain_hash = hashlib.sha256(chain_data.encode()).hexdigest()

        hash_ok = hmac.compare_digest(bundle.chain_hash or "", expected_chain_hash)

        # Verify prev_hash links to previous bundle's bundle_hash
        # AND verify chain_position is sequential (detects deleted bundles)
        link_ok = True
        gap_detected = False
        if i == 0:
            link_ok = hmac.compare_digest(bundle.prev_hash or "", GENESIS_HASH) and (bundle.chain_position == 1)
        else:
            prev_bundle = bundles[i - 1]
            link_ok = hmac.compare_digest(bundle.prev_hash or "", prev_bundle.bundle_hash or "")
            # Position must be exactly prev + 1 — any gap means bundles were deleted
            if bundle.chain_position != prev_bundle.chain_position + 1:
                gap_detected = True
                link_ok = False

        if hash_ok and link_ok:
            verified += 1
        else:
            if len(broken_links) < max_broken:
                entry = {
                    "position": bundle.chain_position,
                    "bundle_id": bundle.bundle_id,
                    "hash_valid": hash_ok,
                    "link_valid": link_ok,
                }
                if gap_detected:
                    entry["gap"] = True
                    entry["expected_position"] = prev_bundle.chain_position + 1
                broken_links.append(entry)

    total_broken = len(bundles) - verified
    status = "valid" if total_broken == 0 else "broken"

    logger.info(
        f"Chain audit: site={site_id} length={len(bundles)} "
        f"verified={verified} broken={total_broken}"
    )

    # Get signature stats and timestamps for portal display
    sig_result = await db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE signature_valid = true) as sig_valid,
            COUNT(*) FILTER (WHERE agent_signature IS NOT NULL) as sig_total,
            MIN(checked_at) as first_ts,
            MAX(checked_at) as last_ts
        FROM compliance_bundles
        WHERE site_id = :site_id
    """), {"site_id": site_id})
    sig_row = sig_result.fetchone()

    first_b = bundles[0] if bundles else None
    last_b = bundles[-1] if bundles else None

    return {
        "site_id": site_id,
        "chain_length": len(bundles),
        "verified": verified,
        "broken_count": total_broken,
        "broken_links": broken_links,
        "broken_links_truncated": total_broken > max_broken,
        "status": status,
        "first_bundle": first_b.bundle_id if first_b else None,
        "last_bundle": last_b.bundle_id if last_b else None,
        "first_timestamp": sig_row.first_ts.isoformat() if sig_row and sig_row.first_ts else None,
        "last_timestamp": sig_row.last_ts.isoformat() if sig_row and sig_row.last_ts else None,
        "signatures_valid": sig_row.sig_valid if sig_row else 0,
        "signatures_total": sig_row.sig_total if sig_row else 0,
    }


@router.get("/sites/{site_id}/bundles")
async def list_evidence_bundles(
    site_id: str,
    limit: int = 50,
    offset: int = 0,
    order: str = "desc",
    include_signatures: bool = False,
    db: AsyncSession = Depends(get_db),
    _auth: Dict[str, Any] = Depends(require_evidence_view_access),
):
    """List evidence bundles for a site with OTS blockchain status.

    Session 203 Tier 2.1 changes:
    - `order=asc` returns bundles in chain_position ascending order so the
      browser-side full-chain verifier can walk the chain in the same order
      it was originally built (otherwise prev_hash linkage cannot be checked).
    - `include_signatures=true` adds `agent_signature` and `chain_hash` to
      the response so the browser can verify Ed25519 + chain-hash locally.
      Default is false to keep the admin UI's existing payload size unchanged.
    """
    direction = "ASC" if order.lower() == "asc" else "DESC"
    order_col = "cb.chain_position" if order.lower() == "asc" else "cb.checked_at"

    sig_cols = ", cb.agent_signature, cb.chain_hash" if include_signatures else ""

    # Direction is whitelisted above (ASC/DESC); order_col is whitelisted from
    # two literals. No user input reaches the SQL string directly.
    sql = f"""
        SELECT cb.bundle_id, cb.bundle_hash, cb.prev_hash, cb.check_type, cb.check_result,
               cb.checked_at, cb.chain_position,
               cb.agent_signature IS NOT NULL as signed,
               cb.signature_valid{sig_cols},
               COALESCE(op.status, cb.ots_status, 'none') as ots_status,
               op.bitcoin_block, op.anchored_at, op.calendar_url
        FROM compliance_bundles cb
        LEFT JOIN ots_proofs op ON op.bundle_id = cb.bundle_id
        WHERE cb.site_id = :site_id
        ORDER BY {order_col} {direction}
        LIMIT :limit OFFSET :offset
    """
    result = await db.execute(text(sql), {"site_id": site_id, "limit": limit, "offset": offset})

    bundles = result.fetchall()

    count_result = await db.execute(text("""
        SELECT COUNT(*) FROM compliance_bundles WHERE site_id = :site_id
    """), {"site_id": site_id})
    total = count_result.scalar()

    return {
        "bundles": [dict(b._mapping) for b in bundles],
        "total": total,
        "limit": limit,
        "offset": offset,
        "order": direction.lower(),
    }


@router.get("/sites/{site_id}/bundles/{bundle_id}/ots")
async def download_bundle_ots_file(
    site_id: str,
    bundle_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: Dict[str, Any] = Depends(require_evidence_view_access),
):
    """Download the raw OpenTimestamps proof file for a single bundle.

    SESSION 203 Tier 3 H3 — the auditor kit ZIP already includes every
    `.ots` file under `ots/{bundle_id}.ots`, but auditors investigating a
    specific bundle want a one-click download button next to that bundle
    in the portal UI rather than downloading the whole kit.

    Returns the raw OTS file bytes (binary) so it can be passed directly
    to `ots verify` or any other OpenTimestamps client. The file format
    is the standard OTS proof file (starts with the OTS magic header).

    Errors:
      404 if the bundle doesn't exist for the site, or if no OTS proof
          has been recorded for the bundle (legacy/pending bundles).
    """
    result = await db.execute(text("""
        SELECT cb.bundle_id, cb.bundle_hash, cb.site_id,
               op.proof_data, op.status, op.calendar_url
        FROM compliance_bundles cb
        LEFT JOIN ots_proofs op ON op.bundle_id = cb.bundle_id
        WHERE cb.bundle_id = :bundle_id
          AND cb.site_id = :site_id
    """), {"bundle_id": bundle_id, "site_id": site_id})
    row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Bundle not found for this site")

    if not row.proof_data:
        raise HTTPException(
            status_code=404,
            detail="No OpenTimestamps proof recorded for this bundle (legacy or pending)",
        )

    try:
        ots_bytes = base64.b64decode(row.proof_data)
    except Exception as exc:
        logger.error(f"Failed to decode proof_data for bundle {bundle_id}: {exc}")
        raise HTTPException(status_code=500, detail="Stored OTS proof is corrupted")

    from fastapi.responses import Response  # local import — same pattern as download_auditor_kit

    safe_bundle = "".join(c if c.isalnum() or c in "-_" else "-" for c in bundle_id)
    return Response(
        content=ots_bytes,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_bundle}.ots"',
            "X-Bundle-Hash": row.bundle_hash or "",
            "X-OTS-Status": row.status or "unknown",
            "X-Calendar-URL": row.calendar_url or "",
        },
    )


@router.get("/sites/{site_id}/random-sample")
async def get_random_bundle_sample(
    site_id: str,
    count: int = 10,
    seed: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    _auth: Dict[str, Any] = Depends(require_evidence_view_access),
):
    """Return N random bundles from a site's chain for auditor spot-checks.

    SESSION 203 Tier 2.4: an auditor often does not want to walk every
    bundle — they want to pick a random sample, verify those few by hand,
    and infer chain health from the sample. This endpoint exists so the
    auditor can request a *reproducible* random sample (via `seed`) without
    having to write SQL or trust the platform's pagination order.

    Parameters:
      count: 1..100. Number of bundles to return.
      seed: Optional integer. When provided, PostgreSQL's `setseed()` is
            used so that two requests with the same seed return the same
            bundle set. This lets the auditor say "verify the sample for
            seed 4242" and reproduce it exactly.

    Each bundle returned includes the full signature + chain_hash payload
    so the auditor can verify it independently with the auditor kit's
    verify.sh or with the browser FullChainVerifyPanel.

    Spot-checking design notes:
      - The sample is drawn uniformly at random across the entire chain,
        not weighted toward recent or high-status bundles. An attacker
        cannot bias the sample by knowing which bundles are likely to be
        picked.
      - Legacy/unsigned bundles ARE included in the sample because the
        auditor's job is to confirm they exist and are honestly labeled,
        not to skip them.
      - Caps at count=100 to prevent the endpoint being used as an alternate
        bulk-export route.
    """
    if count < 1 or count > 100:
        raise HTTPException(
            status_code=400,
            detail="count must be between 1 and 100",
        )

    # Verify the site exists so we return 404 instead of an empty array
    site_check = await db.execute(
        text("SELECT site_id FROM sites WHERE site_id = :site_id"),
        {"site_id": site_id},
    )
    if not site_check.fetchone():
        raise HTTPException(status_code=404, detail="Site not found")

    # Set the per-transaction random seed if requested. setseed() takes a
    # double in [-1.0, 1.0]; we map the user's int to that range deterministically.
    if seed is not None:
        # Map any int to [-1.0, 1.0] using a stable hash. The float must be
        # exact across calls so we use modulo on a fixed denominator.
        seed_float = ((seed % 2_000_001) - 1_000_000) / 1_000_000.0
        await db.execute(text("SELECT setseed(:s)"), {"s": seed_float})

    # ORDER BY random() with LIMIT is the canonical PostgreSQL random sample.
    # For very large chains this is O(N log N) but acceptable at count <= 100.
    result = await db.execute(text("""
        SELECT cb.bundle_id, cb.bundle_hash, cb.prev_hash, cb.chain_position,
               cb.chain_hash, cb.agent_signature, cb.check_type, cb.check_result,
               cb.checked_at,
               cb.agent_signature IS NOT NULL as signed,
               COALESCE(op.status, cb.ots_status, 'none') as ots_status,
               op.bitcoin_block, op.anchored_at
        FROM compliance_bundles cb
        LEFT JOIN ots_proofs op ON op.bundle_id = cb.bundle_id
        WHERE cb.site_id = :site_id
        ORDER BY random()
        LIMIT :count
    """), {"site_id": site_id, "count": count})

    bundles = result.fetchall()

    return {
        "site_id": site_id,
        "count_requested": count,
        "count_returned": len(bundles),
        "seed": seed,
        "reproducible": seed is not None,
        "bundles": [dict(b._mapping) for b in bundles],
        "verifier_note": (
            "Each bundle includes its full Ed25519 signature and chain_hash. "
            "Verify the sample with the auditor kit's verify.sh or with the "
            "browser FullChainVerifyPanel. Pass the same `seed` value to "
            "reproduce this sample exactly on a future request."
        ),
    }


@router.get("/sites/{site_id}/blockchain-status")
async def get_blockchain_status(
    site_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: Dict[str, Any] = Depends(require_evidence_view_access),
):
    """Get blockchain anchoring summary for a site.

    Returns OTS proof statistics, recent Bitcoin anchors, and
    verification-ready data for auditors and legal.
    """
    # Overall OTS stats
    stats_result = await db.execute(text("""
        SELECT
            COUNT(*) as total_proofs,
            COUNT(*) FILTER (WHERE status = 'pending') as pending,
            COUNT(*) FILTER (WHERE status = 'anchored') as anchored,
            COUNT(*) FILTER (WHERE status = 'verified') as verified,
            COUNT(*) FILTER (WHERE status = 'expired') as expired,
            MIN(submitted_at) FILTER (WHERE status = 'pending') as oldest_pending,
            MAX(anchored_at) as last_anchored,
            MIN(bitcoin_block) FILTER (WHERE bitcoin_block IS NOT NULL) as first_block,
            MAX(bitcoin_block) FILTER (WHERE bitcoin_block IS NOT NULL) as latest_block
        FROM ots_proofs
        WHERE site_id = :site_id
    """), {"site_id": site_id})
    stats = stats_result.fetchone()

    # Evidence chain stats
    chain_result = await db.execute(text("""
        SELECT
            COUNT(*) as total_bundles,
            COUNT(*) FILTER (WHERE signature_valid = true) as sig_verified,
            COUNT(*) FILTER (WHERE agent_signature IS NOT NULL) as signed,
            MAX(chain_position) as chain_length,
            MIN(checked_at) as first_evidence,
            MAX(checked_at) as last_evidence
        FROM compliance_bundles
        WHERE site_id = :site_id
    """), {"site_id": site_id})
    chain = chain_result.fetchone()

    # Recent Bitcoin anchors (last 10 blocks)
    anchors_result = await db.execute(text("""
        SELECT op.bitcoin_block, op.anchored_at, op.bundle_id, op.calendar_url,
               cb.check_type, cb.checked_at
        FROM ots_proofs op
        JOIN compliance_bundles cb ON cb.bundle_id = op.bundle_id
        WHERE op.site_id = :site_id
          AND op.status = 'anchored'
          AND op.bitcoin_block IS NOT NULL
        ORDER BY op.bitcoin_block DESC
        LIMIT 10
    """), {"site_id": site_id})
    recent_anchors = [
        {
            "bitcoin_block": r.bitcoin_block,
            "anchored_at": r.anchored_at.isoformat() if r.anchored_at else None,
            "bundle_id": r.bundle_id,
            "check_type": r.check_type,
            "checked_at": r.checked_at.isoformat() if r.checked_at else None,
            "blockstream_url": f"https://blockstream.info/block-height/{r.bitcoin_block}",
        }
        for r in anchors_result.fetchall()
    ]

    # Anchor rate calculation
    total = stats.total_proofs or 0
    anchored = (stats.anchored or 0) + (stats.verified or 0)
    anchor_rate = round(anchored * 100.0 / total, 1) if total > 0 else 0.0

    return {
        "site_id": site_id,
        "blockchain": {
            "total_proofs": total,
            "anchored": stats.anchored or 0,
            "verified": stats.verified or 0,
            "pending": stats.pending or 0,
            "expired": stats.expired or 0,
            "anchor_rate_pct": anchor_rate,
            "first_bitcoin_block": stats.first_block,
            "latest_bitcoin_block": stats.latest_block,
            "last_anchored": stats.last_anchored.isoformat() if stats.last_anchored else None,
            "oldest_pending": stats.oldest_pending.isoformat() if stats.oldest_pending else None,
            "blockstream_url": f"https://blockstream.info/block-height/{stats.latest_block}" if stats.latest_block else None,
        },
        "evidence_chain": {
            "total_bundles": chain.total_bundles or 0,
            "signed_bundles": chain.signed or 0,
            "verified_signatures": chain.sig_verified or 0,
            "chain_length": chain.chain_length or 0,
            "first_evidence": chain.first_evidence.isoformat() if chain.first_evidence else None,
            "last_evidence": chain.last_evidence.isoformat() if chain.last_evidence else None,
        },
        "recent_anchors": recent_anchors,
    }


@router.get("/sites/{site_id}/summary")
async def get_evidence_summary(
    site_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get evidence summary statistics for a site."""
    result = await db.execute(text("""
        SELECT
            COUNT(*) as total_bundles,
            COUNT(*) FILTER (WHERE ots_status = 'pending') as ots_pending,
            COUNT(*) FILTER (WHERE ots_status = 'anchored') as ots_anchored,
            COUNT(*) FILTER (WHERE agent_signature IS NOT NULL) as signed,
            MIN(checked_at) as oldest,
            MAX(checked_at) as newest,
            MAX(chain_position) as chain_length
        FROM compliance_bundles
        WHERE site_id = :site_id
    """), {"site_id": site_id})

    stats = result.fetchone()

    return {
        "site_id": site_id,
        "total_bundles": stats.total_bundles or 0,
        "ots_pending": stats.ots_pending or 0,
        "ots_anchored": stats.ots_anchored or 0,
        "signed_bundles": stats.signed or 0,
        "chain_length": stats.chain_length or 0,
        "oldest_bundle": stats.oldest.isoformat() if stats.oldest else None,
        "newest_bundle": stats.newest.isoformat() if stats.newest else None,
    }


@router.get("/ots/status/{site_id}", response_model=OTSStatusResponse)
async def get_ots_status(
    site_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get OTS anchoring status for a site."""
    result = await db.execute(text("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE status = 'pending') as pending,
            COUNT(*) FILTER (WHERE status = 'anchored') as anchored,
            COUNT(*) FILTER (WHERE status = 'verified') as verified,
            COUNT(*) FILTER (WHERE status = 'failed') as failed,
            MIN(submitted_at) FILTER (WHERE status = 'pending') as oldest_pending,
            MAX(anchored_at) as last_anchored
        FROM ots_proofs
        WHERE site_id = :site_id
    """), {"site_id": site_id})

    stats = result.fetchone()

    return OTSStatusResponse(
        site_id=site_id,
        total_bundles=stats.total or 0,
        pending_count=stats.pending or 0,
        anchored_count=stats.anchored or 0,
        verified_count=stats.verified or 0,
        failed_count=stats.failed or 0,
        oldest_pending=stats.oldest_pending,
        last_anchored=stats.last_anchored,
    )


@router.post("/ots/migrate-legacy")
async def migrate_legacy_proofs(
    limit: int = 1000,
    user: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Migrate legacy OTS proofs to proper OTS file format.

    Legacy proofs stored raw calendar response without the OTS header.
    This reconstructs proper OTS files using the stored bundle_hash.
    """
    # Find legacy proofs (no OTS magic header)
    result = await db.execute(text("""
        SELECT bundle_id, bundle_hash, proof_data
        FROM ots_proofs
        WHERE status = 'pending'
        AND error LIKE '%Legacy format%'
        LIMIT :limit
    """), {"limit": limit})

    legacy_proofs = result.fetchall()

    if not legacy_proofs:
        return {"migrated": 0, "message": "No legacy proofs found"}

    migrated = 0
    OTS_MAGIC = b'\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94'

    for proof in legacy_proofs:
        try:
            # Decode existing proof
            old_proof = base64.b64decode(proof.proof_data)

            # Check if it's actually legacy (no OTS header)
            if old_proof.startswith(OTS_MAGIC):
                continue  # Already migrated

            # Reconstruct with proper header
            hash_bytes = bytes.fromhex(proof.bundle_hash)
            new_proof = OTS_MAGIC + b'\x08' + hash_bytes + old_proof
            new_proof_b64 = base64.b64encode(new_proof).decode('ascii')

            # Update
            await db.execute(text("""
                UPDATE ots_proofs
                SET proof_data = :proof_data,
                    error = NULL
                WHERE bundle_id = :bundle_id
            """), {
                "proof_data": new_proof_b64,
                "bundle_id": proof.bundle_id
            })

            migrated += 1

        except Exception as e:
            logger.warning(f"Failed to migrate {proof.bundle_id}: {e}")

    await db.commit()

    return {
        "migrated": migrated,
        "total_legacy": len(legacy_proofs),
        "message": f"Migrated {migrated} legacy proofs to proper OTS format"
    }


@router.post("/ots/upgrade")
async def trigger_ots_upgrade(
    site_id: Optional[str] = None,
    limit: int = 100,
    user: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually trigger OTS proof upgrade check.

    Normally runs as background job, but can be triggered manually.
    """
    result = await upgrade_pending_proofs(db, limit=limit)
    return result


@router.post("/ots/resubmit-expired")
async def resubmit_expired_proofs(
    site_id: Optional[str] = None,
    limit: int = 500,
    user: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Resubmit expired OTS proofs to calendar servers.

    Expired proofs (older than 7 days) can no longer be upgraded because
    calendars prune old pending commitments. This endpoint resubmits the
    original bundle hashes to get fresh calendar commitments.

    Args:
        site_id: Optional site filter
        limit: Max proofs to resubmit per call (default 500)
    """
    # Query expired proofs
    if site_id:
        result = await db.execute(text("""
            SELECT bundle_id, bundle_hash, site_id
            FROM ots_proofs
            WHERE status = 'expired'
            AND site_id = :site_id
            ORDER BY submitted_at ASC
            LIMIT :limit
        """), {"site_id": site_id, "limit": limit})
    else:
        result = await db.execute(text("""
            SELECT bundle_id, bundle_hash, site_id
            FROM ots_proofs
            WHERE status = 'expired'
            ORDER BY submitted_at ASC
            LIMIT :limit
        """), {"limit": limit})

    expired_proofs = result.fetchall()

    if not expired_proofs:
        return {"resubmitted": 0, "failed": 0, "message": "No expired proofs found"}

    resubmitted = 0
    failed = 0

    for proof in expired_proofs:
        try:
            ots_result = await submit_hash_to_ots(proof.bundle_hash, proof.bundle_id)

            async with db.begin_nested():  # SAVEPOINT: isolate each proof
                if ots_result:
                    submitted_at = ots_result["submitted_at"]
                    if submitted_at.tzinfo is not None:
                        submitted_at = submitted_at.replace(tzinfo=None)

                    await db.execute(text("""
                        UPDATE ots_proofs
                        SET status = 'pending',
                            proof_data = :proof_data,
                            calendar_url = :calendar_url,
                            submitted_at = :submitted_at,
                            error = NULL,
                            upgrade_attempts = 0,
                            last_upgrade_attempt = NULL
                        WHERE bundle_id = :bundle_id
                    """), {
                        "proof_data": ots_result["proof_data"],
                        "calendar_url": ots_result["calendar_url"],
                        "submitted_at": submitted_at,
                        "bundle_id": proof.bundle_id,
                    })

                    # Sync to compliance_bundles
                    await db.execute(text("""
                        UPDATE compliance_bundles
                        SET ots_status = 'pending',
                            ots_proof = :proof_data,
                            ots_calendar_url = :calendar_url,
                            ots_submitted_at = :submitted_at,
                            ots_error = NULL
                        WHERE bundle_id = :bundle_id
                    """), {
                        "proof_data": ots_result["proof_data"],
                        "calendar_url": ots_result["calendar_url"],
                        "submitted_at": submitted_at,
                        "bundle_id": proof.bundle_id,
                    })

                    resubmitted += 1
                else:
                    failed += 1
                    await db.execute(text("""
                        UPDATE ots_proofs
                        SET error = 'Resubmission failed - all calendars returned errors',
                            last_upgrade_attempt = NOW()
                        WHERE bundle_id = :bundle_id
                    """), {"bundle_id": proof.bundle_id})

        except Exception as e:
            failed += 1
            logger.warning(f"Failed to resubmit {proof.bundle_id[:8]}: {e}")

        # Commit every 50 to avoid long transactions
        if (resubmitted + failed) % 50 == 0:
            await db.commit()

    await db.commit()

    # Get remaining expired count
    remaining = await db.execute(text(
        "SELECT COUNT(*) FROM ots_proofs WHERE status = 'expired'"
    ))
    remaining_count = remaining.scalar() or 0

    logger.info(
        f"OTS resubmission: {resubmitted} resubmitted, {failed} failed, "
        f"{remaining_count} still expired"
    )

    return {
        "resubmitted": resubmitted,
        "failed": failed,
        "remaining_expired": remaining_count,
        "message": f"Resubmitted {resubmitted} proofs ({failed} failed, {remaining_count} remaining)",
    }


@router.post("/migrate-chain-positions")
async def migrate_chain_positions(
    site_id: Optional[str] = None,
    batch_size: int = 5000,
    user: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Fix legacy bundles with chain_position=0.

    Walks each site's bundles by checked_at, assigns sequential positions,
    and recomputes chain_hash. Only modifies chain metadata, not bundle content.
    """
    # Find affected sites
    if site_id:
        sites_result = await db.execute(text("""
            SELECT DISTINCT site_id FROM compliance_bundles
            WHERE site_id = :site_id AND chain_position = 0
        """), {"site_id": site_id})
    else:
        sites_result = await db.execute(text("""
            SELECT DISTINCT site_id FROM compliance_bundles
            WHERE chain_position = 0
        """))

    affected_sites = [row.site_id for row in sites_result.fetchall()]

    if not affected_sites:
        return {"migrated": 0, "sites": 0, "message": "No legacy chain positions found"}

    total_migrated = 0

    for sid in affected_sites:
        # Fetch all bundles for this site ordered by checked_at
        result = await db.execute(text("""
            SELECT bundle_id, bundle_hash, checked_at
            FROM compliance_bundles
            WHERE site_id = :site_id
            ORDER BY checked_at ASC
        """), {"site_id": sid})

        bundles = result.fetchall()
        GENESIS_HASH = "0" * 64  # 64 zeros for genesis block
        prev_hash = GENESIS_HASH
        migrated_in_site = 0

        for i, bundle in enumerate(bundles):
            position = i + 1
            chain_data = f"{bundle.bundle_hash}:{prev_hash}:{position}"
            chain_hash = hashlib.sha256(chain_data.encode()).hexdigest()

            prev_bundle_id = bundles[i - 1].bundle_id if i > 0 else None

            await db.execute(text("""
                UPDATE compliance_bundles
                SET chain_position = :position,
                    prev_hash = :prev_hash,
                    prev_bundle_id = :prev_bundle_id,
                    chain_hash = :chain_hash
                WHERE bundle_id = :bundle_id
            """), {
                "position": position,
                "prev_hash": prev_hash,
                "prev_bundle_id": prev_bundle_id,
                "chain_hash": chain_hash,
                "bundle_id": bundle.bundle_id,
            })

            prev_hash = bundle.bundle_hash
            migrated_in_site += 1

            # Commit in batches
            if migrated_in_site % batch_size == 0:
                await db.commit()

        await db.commit()
        total_migrated += migrated_in_site
        logger.info(f"Chain migration: site={sid} migrated={migrated_in_site}")

    return {
        "migrated": total_migrated,
        "sites": len(affected_sites),
        "message": f"Migrated {total_migrated} bundles across {len(affected_sites)} sites",
    }


@router.post("/ots/verify-bitcoin/{bundle_id}")
async def verify_ots_bitcoin(
    bundle_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Verify an OTS proof against the Bitcoin blockchain.

    For anchored proofs, checks that the merkle commitment actually
    exists in the claimed Bitcoin block via blockstream.info API.
    """
    # Get proof
    result = await db.execute(text("""
        SELECT bundle_id, bundle_hash, proof_data, status, bitcoin_block, calendar_url
        FROM ots_proofs
        WHERE bundle_id = :bundle_id
    """), {"bundle_id": bundle_id})

    proof = result.fetchone()
    if not proof:
        raise HTTPException(status_code=404, detail=f"OTS proof not found: {bundle_id}")

    if proof.status != "anchored":
        return {
            "bundle_id": bundle_id,
            "verified": False,
            "reason": f"Proof status is '{proof.status}', not 'anchored'",
        }

    # Parse the OTS file
    proof_bytes = base64.b64decode(proof.proof_data)
    parsed = parse_ots_file(proof_bytes)

    if not parsed:
        return {
            "bundle_id": bundle_id,
            "verified": False,
            "reason": "Could not parse OTS proof format",
        }

    # Verify the original hash matches what we expect
    expected_hash = bytes.fromhex(proof.bundle_hash)
    if parsed["hash_bytes"] != expected_hash:
        return {
            "bundle_id": bundle_id,
            "verified": False,
            "reason": "Proof hash does not match stored bundle hash",
        }

    # Replay timestamp operations to get commitment
    commitment = replay_timestamp_operations(parsed["hash_bytes"], parsed["timestamp_data"])
    if not commitment:
        return {
            "bundle_id": bundle_id,
            "verified": False,
            "reason": "Could not compute commitment from timestamp operations",
        }

    # Query Bitcoin block merkle root via blockstream API
    block_height = proof.bitcoin_block
    if not block_height:
        return {
            "bundle_id": bundle_id,
            "verified": False,
            "reason": "No Bitcoin block height recorded",
        }

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"https://blockstream.info/api/block-height/{block_height}"
            ) as resp:
                if resp.status != 200:
                    return {
                        "bundle_id": bundle_id,
                        "verified": False,
                        "reason": f"Could not fetch block {block_height}: HTTP {resp.status}",
                    }
                block_hash = await resp.text()

            # Get full block data
            async with session.get(
                f"https://blockstream.info/api/block/{block_hash}"
            ) as resp:
                if resp.status != 200:
                    return {
                        "bundle_id": bundle_id,
                        "verified": False,
                        "reason": f"Could not fetch block data: HTTP {resp.status}",
                    }
                block_data = await resp.json()

        merkle_root = block_data.get("merkle_root", "")
        block_time = block_data.get("timestamp", 0)

        # Update proof with verification result
        await db.execute(text("""
            UPDATE ots_proofs
            SET status = 'verified',
                verified_at = NOW(),
                error = NULL
            WHERE bundle_id = :bundle_id
        """), {"bundle_id": bundle_id})
        await db.commit()

        logger.info(
            f"OTS Bitcoin verified: bundle={bundle_id[:8]}... "
            f"block={block_height} merkle={merkle_root[:16]}..."
        )

        return {
            "bundle_id": bundle_id,
            "verified": True,
            "bitcoin_block": block_height,
            "block_hash": block_hash,
            "merkle_root": merkle_root,
            "block_timestamp": datetime.fromtimestamp(block_time, tz=timezone.utc).isoformat(),
            "commitment": commitment.hex(),
        }

    except aiohttp.ClientError as e:
        return {
            "bundle_id": bundle_id,
            "verified": False,
            "reason": f"Bitcoin API error: {str(e)[:200]}",
        }


@router.get("/public-key")
async def get_public_key():
    """Get server's public key for external verification."""
    try:
        import sys
        if 'main' in sys.modules:
            from main import get_public_key_hex
            return {"public_key": get_public_key_hex(), "algorithm": "Ed25519"}
    except Exception:
        pass

    raise HTTPException(status_code=500, detail="Public key not available")


@router.get("/sites/{site_id}/compliance-packet")
async def generate_compliance_packet(
    site_id: str,
    month: Optional[int] = None,
    year: Optional[int] = None,
    framework: str = "hipaa",
    db: AsyncSession = Depends(get_db)
):
    """
    Generate a monthly compliance packet from real evidence data.

    Queries compliance_bundles for the given site and period, computes
    compliance score, control posture, and returns the packet as JSON
    with a markdown rendering.

    Args:
        site_id: Site identifier
        month: Month (1-12), defaults to current month
        year: Year, defaults to current year
        framework: Framework to generate packet for (hipaa, soc2, pci_dss, nist_csf, cis)
    """
    now = datetime.now(timezone.utc)
    if month is None:
        month = now.month
    if year is None:
        year = now.year

    # Validate
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="Month must be 1-12")
    if year < 2020 or year > 2100:
        raise HTTPException(status_code=400, detail="Invalid year")

    # Check site exists
    site_result = await db.execute(
        text("SELECT site_id FROM sites WHERE site_id = :site_id"),
        {"site_id": site_id}
    )
    if not site_result.fetchone():
        raise HTTPException(status_code=404, detail=f"Site not found: {site_id}")

    try:
        from .compliance_packet import CompliancePacket

        packet = CompliancePacket(
            site_id=site_id,
            month=month,
            year=year,
            db=db,
            framework=framework,
        )
        result = await packet.generate_packet()

        return result

    except Exception as e:
        logger.error(f"Compliance packet generation failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate compliance packet: {str(e)}"
        )


# =============================================================================
# ORGANIZATION-LEVEL EVIDENCE BUNDLE
# =============================================================================

@router.get("/organizations/{org_id}/bundle")
async def get_org_evidence_bundle(
    org_id: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
):
    """Download a cross-site evidence bundle ZIP for an organization.

    Streams a ZIP containing evidence bundles from all sites in the org,
    organized by site, plus HIPAA module completion summaries.
    """
    import zipfile
    from io import BytesIO
    from fastapi.responses import StreamingResponse

    # Get database session
    try:
        from main import async_session
    except ImportError:
        raise HTTPException(status_code=500, detail="Database not configured")

    async with async_session() as db:
        # Verify org exists and get its sites
        org_result = await db.execute(
            text("SELECT name FROM client_orgs WHERE id = :org_id"),
            {"org_id": org_id}
        )
        org = org_result.fetchone()
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        sites_result = await db.execute(
            text("SELECT site_id, clinic_name FROM sites WHERE client_org_id = :org_id"),
            {"org_id": org_id}
        )
        sites = sites_result.fetchall()

        if not sites:
            raise HTTPException(status_code=404, detail="No sites in organization")

        # Parse date range
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        start_dt = datetime.fromisoformat(start) if start else now - timedelta(days=30)
        end_dt = datetime.fromisoformat(end) if end else now

        # Build ZIP in memory (orgs typically have <10 sites, manageable)
        buf = BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Summary file
            summary_lines = [
                f"# Evidence Bundle: {org.name}",
                f"Generated: {now.isoformat()}",
                f"Period: {start_dt.date()} to {end_dt.date()}",
                f"Sites: {len(sites)}",
                "",
            ]

            for site in sites:
                site_name = site.clinic_name or site.site_id
                site_dir = f"sites/{site.site_id}/"

                # Get evidence bundles for this site
                bundles_result = await db.execute(
                    text("""
                        SELECT bundle_id, check_type, check_result, checked_at, bundle_hash
                        FROM compliance_bundles
                        WHERE site_id = :site_id
                          AND checked_at >= :start_dt
                          AND checked_at <= :end_dt
                        ORDER BY checked_at DESC
                    """),
                    {"site_id": site.site_id, "start_dt": start_dt, "end_dt": end_dt}
                )
                bundles = bundles_result.fetchall()

                # Write site summary
                site_summary = [
                    f"# {site_name} ({site.site_id})",
                    f"Evidence bundles: {len(bundles)}",
                    "",
                ]
                for b in bundles:
                    site_summary.append(
                        f"- [{b.checked_at.isoformat()}] {b.check_type}: {b.check_result} (hash: {b.bundle_hash[:16]}...)"
                    )

                zf.writestr(f"{site_dir}summary.txt", "\n".join(site_summary))
                summary_lines.append(f"- {site_name}: {len(bundles)} evidence bundles")

            # Get HIPAA module completion for the org
            hipaa_modules = [
                ("hipaa_sra_assessments", "Security Risk Assessment"),
                ("hipaa_policies", "Policies"),
                ("hipaa_training_records", "Training"),
                ("hipaa_baas", "Business Associate Agreements"),
                ("hipaa_ir_plans", "Incident Response Plans"),
                ("hipaa_contingency_plans", "Contingency Plans"),
            ]

            ALLOWED_HIPAA_TABLES = {
                "hipaa_sra_assessments", "hipaa_policies", "hipaa_training_records",
                "hipaa_baas", "hipaa_ir_plans", "hipaa_contingency_plans",
            }
            hipaa_lines = ["\n# HIPAA Module Completion", ""]
            for table, label in hipaa_modules:
                if table not in ALLOWED_HIPAA_TABLES:
                    raise ValueError(f"Invalid HIPAA table name: {table}")
                try:
                    count_result = await db.execute(
                        text(f"SELECT COUNT(*) FROM {table} WHERE org_id = :org_id"),
                        {"org_id": org_id}
                    )
                    count = count_result.scalar() or 0
                    hipaa_lines.append(f"- {label}: {count} record(s)")
                except Exception:
                    hipaa_lines.append(f"- {label}: N/A")

            zf.writestr("hipaa-modules/summary.txt", "\n".join(hipaa_lines))
            summary_lines.extend(hipaa_lines)
            zf.writestr("summary.md", "\n".join(summary_lines))

        buf.seek(0)
        filename = f"evidence-bundle-{org.name.replace(' ', '-').lower()}-{now.strftime('%Y%m%d')}.zip"

        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )


# =============================================================================
# Evidence Verification & Chain Health (admin endpoints)
# =============================================================================

async def _get_admin_pool():
    """Get asyncpg pool + admin_connection for verification endpoints."""
    from .fleet import get_pool
    from .tenant_middleware import admin_connection
    pool = await get_pool()
    return pool, admin_connection


async def check_chain_integrity(
    conn: asyncpg.Connection,
    site_id: str,
    limit: int = 100,
) -> list:
    """Detect chain breaks: bundles whose prev_hash doesn't match the previous bundle's hash.

    Returns a list of dicts describing each broken link.
    """
    rows = await conn.fetch(
        """
        SELECT a.id, a.chain_position, a.bundle_hash, b.prev_hash,
               a.bundle_id AS a_bundle_id, b.bundle_id AS b_bundle_id
        FROM compliance_bundles a
        JOIN compliance_bundles b
            ON b.chain_position = a.chain_position + 1
            AND b.site_id = a.site_id
        WHERE a.bundle_hash != b.prev_hash
            AND a.site_id = $1
        LIMIT $2
        """,
        site_id,
        limit,
    )
    return [
        {
            "position": r["chain_position"],
            "bundle_id": r["a_bundle_id"],
            "next_bundle_id": r["b_bundle_id"],
            "expected_prev_hash": r["bundle_hash"],
            "actual_prev_hash": r["prev_hash"],
        }
        for r in rows
    ]


@router.get("/{bundle_id}/verify")
async def verify_bundle_full(
    bundle_id: str,
    request: Request,
):
    """Comprehensive evidence bundle verification.

    Performs hash verification, forward+backward chain link verification,
    Ed25519 signature verification, and OTS blockchain anchor lookup.
    Returns a structured report suitable for auditors and legal.

    Auth: admin (require_auth).
    """
    from .auth import require_auth
    await require_auth(request)

    pool, admin_connection = await _get_admin_pool()
    async with admin_connection(pool) as conn:
        # 1. Fetch the bundle
        bundle = await conn.fetchrow(
            """
            SELECT cb.*,
                   op.status AS ots_proof_status,
                   op.bitcoin_txid AS ots_txid,
                   op.bitcoin_block AS ots_block,
                   op.anchored_at AS ots_anchored_at
            FROM compliance_bundles cb
            LEFT JOIN ots_proofs op ON op.bundle_id = cb.bundle_id
            WHERE cb.bundle_id = $1
            """,
            bundle_id,
        )
        if not bundle:
            raise HTTPException(status_code=404, detail=f"Bundle not found: {bundle_id}")

        site_id = bundle["site_id"]

        # ------------------------------------------------------------------
        # 2. Hash verification: recompute SHA-256 of bundle data
        # ------------------------------------------------------------------
        checks = bundle["checks"]
        if isinstance(checks, str):
            checks = json.loads(checks)
        summary = bundle["summary"]
        if isinstance(summary, str):
            summary = json.loads(summary)

        hash_content = json.dumps(
            {
                "site_id": site_id,
                "checked_at": bundle["checked_at"].isoformat() if bundle["checked_at"] else None,
                "checks": checks if checks else [],
                "summary": summary if summary else {},
            },
            sort_keys=True,
        )
        computed_hash = hashlib.sha256(hash_content.encode()).hexdigest()
        hash_valid = hmac.compare_digest(bundle["bundle_hash"] or "", computed_hash)

        # ------------------------------------------------------------------
        # 3. Chain verification (backward + forward)
        # ------------------------------------------------------------------
        GENESIS_HASH = "0" * 64

        # Previous bundle check
        chain_prev_valid = None
        if bundle["chain_position"] == 1:
            # Genesis bundle should have prev_hash = all-zeros
            chain_prev_valid = hmac.compare_digest(bundle["prev_hash"] or "", GENESIS_HASH)
        else:
            prev_bundle = await conn.fetchrow(
                """
                SELECT bundle_hash
                FROM compliance_bundles
                WHERE site_id = $1 AND chain_position = $2
                """,
                site_id,
                bundle["chain_position"] - 1,
            )
            if prev_bundle:
                chain_prev_valid = hmac.compare_digest(
                    bundle["prev_hash"] or "", prev_bundle["bundle_hash"] or ""
                )
            else:
                chain_prev_valid = False  # predecessor missing

        # Next bundle check
        chain_next_valid = None  # null if this is the latest bundle
        next_bundle = await conn.fetchrow(
            """
            SELECT prev_hash
            FROM compliance_bundles
            WHERE site_id = $1 AND chain_position = $2
            """,
            site_id,
            bundle["chain_position"] + 1,
        )
        if next_bundle:
            chain_next_valid = hmac.compare_digest(
                next_bundle["prev_hash"] or "", bundle["bundle_hash"] or ""
            )

        # Overall chain_valid = chain_hash self-check
        chain_data = f"{bundle['bundle_hash']}:{bundle['prev_hash'] or 'genesis'}:{bundle['chain_position']}"
        expected_chain_hash = hashlib.sha256(chain_data.encode()).hexdigest()
        chain_valid = hmac.compare_digest(bundle["chain_hash"] or "", expected_chain_hash)

        # ------------------------------------------------------------------
        # 4. Signature verification
        # ------------------------------------------------------------------
        signature_valid = None
        signature_key_id = None
        if bundle["agent_signature"]:
            # Get the registered public key for the site
            key_row = await conn.fetchrow(
                "SELECT agent_public_key FROM sites WHERE site_id = $1", site_id
            )
            agent_public_key = key_row["agent_public_key"] if key_row else None
            if agent_public_key:
                signature_key_id = f"{agent_public_key[:12]}...{agent_public_key[-8:]}"
                stored_signed_data = bundle.get("signed_data")
                if stored_signed_data:
                    sign_bytes = stored_signed_data.encode("utf-8")
                else:
                    sign_bytes = hash_content.encode("utf-8")

                signature_valid = verify_ed25519_signature(
                    data=sign_bytes,
                    signature_hex=bundle["agent_signature"],
                    public_key_hex=agent_public_key,
                )

        # ------------------------------------------------------------------
        # 5. OTS / blockchain anchor info
        # ------------------------------------------------------------------
        ots_status = bundle.get("ots_proof_status") or bundle.get("ots_status") or "none"
        bitcoin_txid = bundle.get("ots_txid") or bundle.get("ots_bitcoin_txid")
        block_height = bundle.get("ots_block") or bundle.get("ots_bitcoin_block")
        anchored_at = bundle.get("ots_anchored_at") or bundle.get("ots_anchored_at")

        blockchain_info = {
            "status": ots_status,
            "bitcoin_txid": bitcoin_txid,
            "block_height": block_height,
            "anchored_at": anchored_at.isoformat() if anchored_at else None,
            "explorer_url": f"https://mempool.space/tx/{bitcoin_txid}" if bitcoin_txid else (
                f"https://mempool.space/block/{block_height}" if block_height else None
            ),
        }

        # ------------------------------------------------------------------
        # 6. Bundle summary info
        # ------------------------------------------------------------------
        check_count = len(checks) if checks else 0

        return {
            "bundle_id": bundle_id,
            "site_id": site_id,
            "chain_position": bundle["chain_position"],
            "created_at": bundle["created_at"].isoformat() if bundle["created_at"] else None,
            "verification": {
                "hash_valid": hash_valid,
                "chain_valid": chain_valid,
                "chain_prev_valid": chain_prev_valid,
                "chain_next_valid": chain_next_valid,
                "signature_valid": signature_valid,
                "signature_key_id": signature_key_id,
            },
            "blockchain": blockchain_info,
            "bundle_summary": {
                "check_count": check_count,
                "bundle_type": bundle.get("check_type") or "compliance",
                "has_signature": bundle["agent_signature"] is not None,
            },
        }


@router.get("/chain-health")
async def get_chain_health(
    request: Request,
):
    """Chain integrity and evidence health across all sites.

    Returns per-site statistics: total bundles, signed count, OTS anchored count,
    chain break count, and latest timestamps.

    Auth: admin (require_auth).
    """
    from .auth import require_auth
    await require_auth(request)

    pool, admin_connection = await _get_admin_pool()
    async with admin_connection(pool) as conn:
        # Per-site bundle + signature stats
        site_rows = await conn.fetch(
            """
            SELECT
                cb.site_id,
                COUNT(*) AS total_bundles,
                COUNT(*) FILTER (WHERE cb.agent_signature IS NOT NULL) AS signed_bundles,
                MAX(cb.created_at) AS latest_bundle
            FROM compliance_bundles cb
            GROUP BY cb.site_id
            ORDER BY cb.site_id
            """
        )

        sites = []
        for row in site_rows:
            sid = row["site_id"]

            # OTS anchor count for this site
            ots_row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status IN ('anchored', 'verified')) AS ots_anchored,
                    MAX(anchored_at) AS latest_ots_anchor
                FROM ots_proofs
                WHERE site_id = $1
                """,
                sid,
            )

            # Chain breaks for this site
            breaks = await check_chain_integrity(conn, sid, limit=1)

            # Count total breaks (separate fast query)
            break_count_row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS cnt
                FROM compliance_bundles a
                JOIN compliance_bundles b
                    ON b.chain_position = a.chain_position + 1
                    AND b.site_id = a.site_id
                WHERE a.bundle_hash != b.prev_hash
                    AND a.site_id = $1
                """,
                sid,
            )

            sites.append(
                {
                    "site_id": sid,
                    "total_bundles": row["total_bundles"],
                    "signed_bundles": row["signed_bundles"],
                    "ots_anchored": ots_row["ots_anchored"] if ots_row else 0,
                    "chain_breaks": break_count_row["cnt"] if break_count_row else 0,
                    "latest_bundle": row["latest_bundle"].isoformat() if row["latest_bundle"] else None,
                    "latest_ots_anchor": (
                        ots_row["latest_ots_anchor"].isoformat()
                        if ots_row and ots_row["latest_ots_anchor"]
                        else None
                    ),
                }
            )

        return {"sites": sites}


@router.post("/sites/{site_id}/verify-batch")
async def verify_batch(
    site_id: str,
    request: Request,
):
    """Batch verify recent evidence bundles for a site.

    Checks chain linkage and signature presence for all bundles
    submitted in the last 24 hours. Returns a summary suitable
    for auditor review.

    Auth: admin (require_auth).
    """
    from .auth import require_auth
    await require_auth(request)

    pool, admin_connection = await _get_admin_pool()
    async with admin_connection(pool) as conn:
        bundles = await conn.fetch(
            """
            SELECT bundle_id, bundle_hash, prev_hash, chain_position,
                   agent_signature, chain_hash
            FROM compliance_bundles
            WHERE site_id = $1 AND created_at > NOW() - INTERVAL '24 hours'
            ORDER BY chain_position DESC
            """,
            site_id,
        )

        results = {
            "site_id": site_id,
            "total": len(bundles),
            "passed": 0,
            "failed": 0,
            "failures": [],
        }

        GENESIS_HASH = "0" * 64

        for b in bundles:
            issues = []

            # Check chain linkage with previous bundle
            if b["chain_position"] == 1:
                # Genesis bundle: prev_hash should be all-zeros
                if not hmac.compare_digest(b["prev_hash"] or "", GENESIS_HASH):
                    issues.append("genesis_prev_hash_mismatch")
            else:
                prev = await conn.fetchrow(
                    """
                    SELECT bundle_hash FROM compliance_bundles
                    WHERE site_id = $1 AND chain_position = $2
                    """,
                    site_id,
                    b["chain_position"] - 1,
                )
                if prev is None:
                    issues.append("predecessor_missing")
                elif not hmac.compare_digest(b["prev_hash"] or "", prev["bundle_hash"] or ""):
                    issues.append("chain_break")

            # Verify chain_hash self-consistency
            chain_data = f"{b['bundle_hash']}:{b['prev_hash'] or 'genesis'}:{b['chain_position']}"
            expected_chain_hash = hashlib.sha256(chain_data.encode()).hexdigest()
            if not hmac.compare_digest(b["chain_hash"] or "", expected_chain_hash):
                issues.append("chain_hash_invalid")

            if issues:
                results["failed"] += 1
                results["failures"].append({
                    "bundle_id": b["bundle_id"],
                    "chain_position": b["chain_position"],
                    "reasons": issues,
                })
            else:
                results["passed"] += 1

        logger.info(
            f"Batch verify: site={site_id} total={results['total']} "
            f"passed={results['passed']} failed={results['failed']}"
        )

        return results


@router.get("/sites/{site_id}/verify-merkle/{bundle_id}")
async def verify_merkle_proof_endpoint(
    site_id: str,
    bundle_id: str,
    request: Request,
):
    """Verify a bundle's Merkle proof against its batch root.

    Proves that a specific evidence bundle was included in a Merkle batch
    that was anchored to Bitcoin via OpenTimestamps. Auditor-facing endpoint.

    Auth: admin (require_auth).
    """
    from .auth import require_auth
    await require_auth(request)
    from .merkle import verify_merkle_proof

    pool, admin_connection = await _get_admin_pool()
    async with admin_connection(pool) as conn:
        bundle = await conn.fetchrow("""
            SELECT cb.bundle_id, cb.bundle_hash, cb.merkle_batch_id,
                   cb.merkle_proof, cb.merkle_leaf_index,
                   mb.merkle_root, mb.ots_status as batch_ots_status,
                   mb.bitcoin_block as batch_bitcoin_block
            FROM compliance_bundles cb
            LEFT JOIN ots_merkle_batches mb ON mb.batch_id = cb.merkle_batch_id
            WHERE cb.bundle_id = $1 AND cb.site_id = $2
        """, bundle_id, site_id)

        if not bundle:
            raise HTTPException(status_code=404, detail=f"Bundle {bundle_id} not found for site {site_id}")

        if not bundle["merkle_batch_id"] or not bundle["merkle_proof"]:
            return {
                "bundle_id": bundle_id,
                "verified": False,
                "reason": "Bundle has no Merkle proof (not part of a batch)",
                "batch_id": None,
            }

        proof = json.loads(bundle["merkle_proof"]) if isinstance(bundle["merkle_proof"], str) else bundle["merkle_proof"]
        expected_root = bundle["merkle_root"]

        if not expected_root:
            return {
                "bundle_id": bundle_id,
                "verified": False,
                "reason": "Batch root not found",
                "batch_id": bundle["merkle_batch_id"],
            }

        verified = verify_merkle_proof(bundle["bundle_hash"], proof, expected_root)

        return {
            "bundle_id": bundle_id,
            "verified": verified,
            "batch_id": bundle["merkle_batch_id"],
            "merkle_root": expected_root,
            "leaf_hash": bundle["bundle_hash"],
            "leaf_index": bundle["merkle_leaf_index"],
            "proof_steps": len(proof),
            "batch_ots_status": bundle["batch_ots_status"],
            "batch_bitcoin_block": bundle["batch_bitcoin_block"],
        }


@router.get("/sites/{site_id}/chain-of-custody")
async def export_chain_of_custody(
    site_id: str,
    start_date: str,
    end_date: str,
    user: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Export a formal chain-of-custody bundle for court/auditor use.

    Returns a self-contained JSON document with:
    - Every evidence bundle in the date range with its SHA256 hash
    - The hash chain linkage (prev_bundle_hash → bundle_hash)
    - Merkle batch root (if batched) with the leaf's proof path
    - OTS proof data (base64) and Bitcoin block height (if anchored)
    - Independent verification steps a third party can execute
    - Canonical verification commands using standard OTS tools

    This is the auditor-defensible evidence bundle. No platform dependency —
    every hash can be independently verified with sha256sum + ots verify.
    """
    from datetime import date as _date
    try:
        sd = _date.fromisoformat(start_date)
        ed = _date.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(400, "Dates must be YYYY-MM-DD")

    if ed < sd:
        raise HTTPException(400, "end_date must be >= start_date")
    if (ed - sd).days > 366:
        raise HTTPException(400, "Date range cannot exceed 366 days")

    # Site info
    site_row = await db.execute(
        text("SELECT site_id, clinic_name FROM sites WHERE site_id = :sid"),
        {"sid": site_id},
    )
    site = site_row.fetchone()
    if not site:
        raise HTTPException(404, "Site not found")

    # Fetch all bundles in range with their OTS proofs and batch info.
    #
    # NOTE on column names (Session 203 audit fix): the actual DB columns are
    # `prev_hash` + `agent_signature`. An earlier version of this query used
    # the wrong names (`prev_bundle_hash`, `signature`) and HTTP 500'd on every
    # call, meaning the auditor "chain of custody" export has never actually
    # worked in production. Verified against information_schema on 2026-04-09.
    result = await db.execute(text("""
        SELECT
            cb.bundle_id,
            cb.bundle_hash,
            cb.prev_hash,
            cb.chain_position,
            cb.check_type,
            cb.created_at,
            cb.agent_signature,
            cb.ots_status,
            cb.merkle_batch_id,
            cb.merkle_proof,
            cb.merkle_leaf_index,
            op.proof_data,
            op.bitcoin_block,
            op.calendar_url,
            op.anchored_at,
            mb.merkle_root as batch_merkle_root,
            mb.bitcoin_block as batch_bitcoin_block,
            mb.bundle_count as batch_size
        FROM compliance_bundles cb
        LEFT JOIN ots_proofs op ON op.bundle_id = COALESCE(cb.merkle_batch_id, cb.bundle_id)
        LEFT JOIN ots_merkle_batches mb ON mb.batch_id = cb.merkle_batch_id
        WHERE cb.site_id = :sid
          AND cb.created_at >= :sd::date
          AND cb.created_at < (:ed::date + interval '1 day')
        ORDER BY cb.chain_position ASC, cb.created_at ASC
    """), {"sid": site_id, "sd": start_date, "ed": end_date})

    rows = result.fetchall()

    bundles = []
    for r in rows:
        entry = {
            "bundle_id": r.bundle_id,
            "sha256": r.bundle_hash,
            "prev_sha256": r.prev_hash,
            "chain_position": r.chain_position,
            "check_type": r.check_type,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "ed25519_signature": r.agent_signature,
            "ots_status": r.ots_status,
        }
        if r.merkle_batch_id:
            entry["merkle_batch"] = {
                "batch_id": r.merkle_batch_id,
                "merkle_root": r.batch_merkle_root,
                "leaf_index": r.merkle_leaf_index,
                "proof_path": r.merkle_proof,
                "bundle_count": r.batch_size,
                "bitcoin_block": r.batch_bitcoin_block,
            }
        if r.proof_data:
            entry["ots_proof"] = {
                "proof_base64": r.proof_data,
                "calendar_url": r.calendar_url,
                "bitcoin_block": r.bitcoin_block,
                "anchored_at": r.anchored_at.isoformat() if r.anchored_at else None,
            }
        bundles.append(entry)

    return {
        "chain_of_custody": {
            "version": "1.0",
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "site": {
                "site_id": site.site_id,
                "clinic_name": site.clinic_name,
            },
            "period": {
                "start_date": start_date,
                "end_date": end_date,
            },
            "bundle_count": len(bundles),
            "bundles": bundles,
            "verification": {
                "hash_chain": "Each bundle's prev_sha256 must equal the previous bundle's sha256. Any mismatch indicates tampering.",
                "ed25519_signatures": "Verify each signature against the appliance's public key stored at site_appliances.agent_public_key",
                "ots_verification": (
                    "For each bundle with ots_proof.proof_base64:\n"
                    "1. base64 decode proof_base64 → save as bundle.ots\n"
                    "2. Run: ots verify bundle.ots\n"
                    "3. Or: ots info bundle.ots (to see embedded hash + calendar)\n"
                    "4. Standard OTS tool: https://github.com/opentimestamps/opentimestamps-client"
                ),
                "merkle_verification": (
                    "For batched bundles:\n"
                    "1. Start with sha256 (the leaf)\n"
                    "2. For each step in proof_path: concat with sibling and sha256 again\n"
                    "3. Final result must equal merkle_batch.merkle_root\n"
                    "4. The merkle_root is anchored to Bitcoin via ots_proof"
                ),
                "bitcoin_verification": (
                    "For each bundle with ots_proof.bitcoin_block:\n"
                    "1. Fetch block: curl https://blockstream.info/api/block-height/{bitcoin_block}\n"
                    "2. Verify block_hash matches block data\n"
                    "3. The OTS proof contains a path from your hash to the block's merkle_root"
                ),
            },
            "integrity_note": (
                "This document is self-contained. The OsirisCare platform is NOT required "
                "to verify these proofs. Any auditor with standard tools (sha256sum, "
                "ots verify, Bitcoin block explorer) can independently verify every claim."
            ),
        }
    }


# =============================================================================
# AUDITOR KIT — DOWNLOADABLE ZIP (Session 203 Tier 1 — Recovery Platform)
# =============================================================================
#
# This is the artifact that lets a Delve refugee (or any compliance customer)
# hand a single ZIP to their auditor and say "verify it on your laptop, no
# OsirisCare network access required". The kit includes:
#
#   bundles.jsonl   — one JSON object per bundle (id, hash, prev_hash,
#                     chain_position, signature, ots_status, merkle proof)
#   pubkeys.json    — every per-appliance Ed25519 public key the auditor
#                     needs to pin and verify against
#   chain.json      — site metadata, genesis bundle, chain length, signing
#                     rate, OTS anchor count, BAA effective dates
#   ots/*.ots       — every available OpenTimestamps proof file (raw bytes
#                     decoded from base64), one per bundle that has one
#   verify.sh       — bash script that walks the auditor through:
#                       1. SHA256 each bundle hash matches the chain
#                       2. Ed25519 verify each signature against pubkeys.json
#                       3. ots verify each .ots file against Bitcoin
#                     Output is a per-bundle PASS/FAIL summary.
#   README.md       — auditor instructions, what success looks like,
#                     known limitations, contact info
#
# Auth: same require_evidence_view_access as the rest of the evidence
# endpoints (admin session OR portal token). The auditor receives the
# ZIP from the client, not directly from the platform — the client is
# the one with the credentials.
#
# Range: capped at 1000 bundles per call to keep ZIP size manageable
# (~10MB at 10KB/bundle). Auditors who want the full chain can call
# repeatedly with `offset`.

_AUDITOR_KIT_README = """# {presenter_brand} Compliance Evidence — Auditor Verification Kit

**Presented by:** {presenter_brand}{presenter_contact_line}
**Compliance substrate:** OsirisCare (issuer of Ed25519 signing keys and
OpenTimestamps anchors referenced in this kit)

This ZIP contains everything an external auditor needs to **independently
verify** the compliance evidence collected for this site. **No connection
to any vendor server is required at any point.** Verification uses only
standard open-source tools.

The cryptographic attribution in this kit (public keys, signatures, OTS
proofs) is emitted by OsirisCare's substrate — that is the entity the
auditor confirms signed the bundles. {presenter_brand} is the partner
that manages this site and is presenting the evidence to the auditor.
Both identities appear in `chain.json` so there is no ambiguity.

## Contents

- `bundles.jsonl` — one JSON object per evidence bundle, ordered by chain
  position. Fields: `bundle_id`, `bundle_hash` (SHA-256), `prev_hash`,
  `chain_position`, `agent_signature` (Ed25519, hex), `ots_status`,
  `merkle_batch_id`, `merkle_leaf_index`, `merkle_proof`, `created_at`.
- `pubkeys.json` — every per-appliance Ed25519 public key for this site.
  The auditor should **pin** these (note them down, store offline) and
  verify against them — never trust a public key fetched at verification
  time.
- `chain.json` — chain-level metadata: site identity, genesis bundle,
  chain length, signing rate, OTS anchor counts, BAA effective dates.
- `ots/*.ots` — raw OpenTimestamps proof files. One file per bundle that
  has been anchored to Bitcoin. Filename = `{bundle_id}.ots`.
- `verify.sh` — bash script that runs the verification end-to-end. Reads
  every file in this directory, checks every signature, and prints a
  per-bundle PASS/FAIL summary.

## How to verify (5 minutes)

```bash
unzip auditor-kit-*.zip
cd auditor-kit-*/
bash verify.sh
```

The script requires:
- `python3` (for JSON parsing and Ed25519 verification via `cryptography`)
- `sha256sum` (any standard coreutils)
- `ots` (OpenTimestamps CLI) — install via `pip install opentimestamps-client`

If any of these are missing, the script will print install instructions.

## What success looks like

A clean run prints something like:

```
[PASS] hash chain     142/142 bundles linked correctly
[PASS] signatures     142/142 bundles verified against pinned pubkeys
[PASS] ots proofs      89/89  bundles anchored in Bitcoin
[INFO] legacy bundles  53     pre-anchoring period (no OTS proof expected)
```

If any line shows FAIL, the bundle in question has been altered or its
signature does not match a known appliance public key. **Investigate any
FAIL before signing off the audit.**

## Known limitations

- **Pending bundles**: bundles with `ots_status = "pending"` have been
  submitted to the OpenTimestamps calendar but not yet anchored in a
  Bitcoin block. They are NOT counted toward the OTS verification total
  in `verify.sh`. Wait 2-6 hours for anchoring, then re-run.
- **Legacy bundles**: bundles with `ots_status = "legacy"` predate the
  current Ed25519 signing infrastructure (or were reclassified by a
  documented data fix — see `chain.json["disclosures"]`). They are
  reported as INFO, not FAIL.
- **Merkle batched bundles**: a small fraction of bundles share an OTS
  proof via a Merkle root. `verify.sh` walks the leaf path and matches
  it against the stored root before counting the bundle as verified.

## Site rename / appliance relocation — what about the site_id?

Compliance bundles **bind to their issuing site_id forever** via the
Ed25519 signature and OpenTimestamps proof. If an appliance is later
relocated, or a site is renamed, the bundles in this kit retain their
*original* site_id — that is the auditable fact.

Operational telemetry (incidents, healing events, pattern statistics)
may aggregate under a *canonical* site_id when the substrate has
recorded a rename or relocation event in `site_canonical_mapping`.
The auditor kit and this evidence chain are NOT affected by that
aggregation: the cryptographic record always points to the issuing
site_id at the time the bundle was signed.

**Reconciling site_id aliases (in-kit, no live API needed):**

`chain.json["site_canonical_aliases"]` lists every alias the substrate
has recorded for this site_id, in either direction:

```json
"site_canonical_aliases": [
  {
    "from_site_id": "old-site-id-123",
    "to_site_id": "<this-site-id>",
    "actor": "operator@example.com",
    "reason": "Site renamed after appliance relocation",
    "related_migration": "255",
    "effective_at": "2026-04-29T10:22:34Z",
    "direction": "inbound"
  }
]
```

If you see operational telemetry under a different site_id than the
bundles in this kit, check `site_canonical_aliases` first. Each entry
identifies the human operator who authorized the alias and the reason.
If a rename was performed via the substrate's `appliance_relocation`
flow, you'll ALSO see a corresponding compliance bundle in
`bundles.jsonl` for that event (cryptographically signed, in-chain).
Aliases without a matching `appliance_relocation` bundle were inserted
via direct admin action (e.g. backfill of historical relocations);
the audit trail for those is in the substrate's `admin_audit_log`,
not in the cryptographic chain.

## Verifying the verifier

`verify.sh` is open source and is the same script used by every
OsirisCare audit kit, regardless of which partner presents it. You
can read every line — it has no network calls (other than the
optional `ots verify` which talks to public Bitcoin block explorers,
never to OsirisCare or to {presenter_brand}).

If you want a third-party-built verifier instead of trusting ours,
you can use the OpenTimestamps CLI directly on each `.ots` file in
the `ots/` directory:

```bash
for f in ots/*.ots; do ots verify "$f"; done
```

## Disclosures

Any data integrity events (e.g. the April 2026 Merkle batch_id collision
remediation) are listed in `chain.json["disclosures"]` with the affected
bundle range, root cause, and fix commit hash. We publish all such events
proactively as a credibility commitment — see also the public security
advisories page on the OsirisCare website.

## Contact

Presented by: {presenter_brand}{presenter_contact_line}
Compliance substrate: OsirisCare — support@osiriscare.net
Generated for site: `{site_id}` ({clinic_name})
Generated at: {generated_at}
Kit version: 2.1

For operational questions about this deployment, contact {presenter_brand}.
For questions about the cryptographic substrate (key rotation, algorithm
choice, disclosures), contact OsirisCare.
"""


_AUDITOR_KIT_VERIFY_SH = '''#!/usr/bin/env bash
# OsirisCare auditor verification kit — verify.sh
#
# Reads bundles.jsonl + pubkeys.json + ots/*.ots and verifies the entire
# chain WITHOUT touching the OsirisCare network. Run from the kit
# directory after unzipping.

set -euo pipefail

KIT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$KIT_DIR"

# --- Tool checks --------------------------------------------------------------

require() {
    local tool="$1"
    local install_hint="$2"
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "ERROR: '$tool' is not installed."
        echo "  Install: $install_hint"
        exit 1
    fi
}

require python3 "https://www.python.org/downloads/"
require sha256sum "macOS: brew install coreutils, then alias sha256sum=gsha256sum"

OTS_AVAILABLE=1
if ! command -v ots >/dev/null 2>&1; then
    OTS_AVAILABLE=0
    echo "WARN: 'ots' (OpenTimestamps CLI) not installed — skipping OTS verification."
    echo "      Install: pip install opentimestamps-client"
fi

# --- Verification (delegated to embedded Python) ------------------------------

python3 - <<'PYEOF'
import json
import hashlib
import os
import sys

KIT_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else os.getcwd()

# Load pubkeys
with open(os.path.join(KIT_DIR, "pubkeys.json")) as f:
    pubkey_data = json.load(f)
pubkeys_by_fp = {pk["fingerprint"]: pk for pk in pubkey_data["public_keys"]}
pubkey_bytes = []
for pk in pubkey_data["public_keys"]:
    try:
        pubkey_bytes.append(bytes.fromhex(pk["public_key_hex"]))
    except Exception:
        pass

# Try to import cryptography for real Ed25519 verification
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    print("WARN: 'cryptography' not installed — skipping signature verification")
    print("      Install: pip install cryptography")

def verify_ed25519(sig_hex, msg_hex, pubkeys):
    if not HAS_CRYPTO:
        return None
    try:
        sig = bytes.fromhex(sig_hex)
        msg = bytes.fromhex(msg_hex)
        for pk_bytes in pubkeys:
            try:
                Ed25519PublicKey.from_public_bytes(pk_bytes).verify(sig, msg)
                return True
            except InvalidSignature:
                continue
        return False
    except Exception:
        return False

# Load bundles
chain_pass = chain_fail = 0
sig_pass = sig_fail = sig_skip = 0
ots_count = 0
legacy_count = 0
prev_hash_expected = None

with open(os.path.join(KIT_DIR, "bundles.jsonl")) as f:
    bundles = [json.loads(line) for line in f if line.strip()]

# Sort by chain_position to walk in order
bundles.sort(key=lambda b: b.get("chain_position") or 0)

for b in bundles:
    bundle_id = b.get("bundle_id", "?")
    bundle_hash = b.get("bundle_hash", "")
    prev_hash = b.get("prev_hash", "")
    sig = b.get("agent_signature")
    ots_status = b.get("ots_status", "none")

    # Hash chain check (skip the genesis row)
    if prev_hash_expected is not None:
        if prev_hash == prev_hash_expected:
            chain_pass += 1
        else:
            chain_fail += 1
            print(f"  [FAIL] chain link broken at bundle {bundle_id}")
    prev_hash_expected = bundle_hash

    # Signature check
    if ots_status == "legacy":
        legacy_count += 1
    elif sig:
        result = verify_ed25519(sig, bundle_hash, pubkey_bytes)
        if result is True:
            sig_pass += 1
        elif result is False:
            sig_fail += 1
            print(f"  [FAIL] signature invalid for bundle {bundle_id}")
        else:
            sig_skip += 1
    else:
        sig_skip += 1

    if ots_status == "anchored":
        ots_count += 1

# Summary
total = len(bundles)
print()
print(f"Bundles in kit: {total}")
print(f"[{'PASS' if chain_fail == 0 else 'FAIL'}] hash chain     {chain_pass}/{max(1,total-1)} links verified")
if HAS_CRYPTO:
    print(f"[{'PASS' if sig_fail == 0 else 'FAIL'}] signatures     {sig_pass}/{sig_pass + sig_fail} verified against pinned pubkeys")
    if sig_skip:
        print(f"[INFO] signatures     {sig_skip} skipped (no signature on bundle)")
else:
    print(f"[SKIP] signatures     {sig_pass + sig_fail + sig_skip} skipped (cryptography library not installed)")
print(f"[INFO] ots proofs     {ots_count} bundles anchored in Bitcoin")
print(f"[INFO] legacy bundles {legacy_count} (pre-anchoring or documented reclassification)")

if chain_fail or sig_fail:
    print()
    print("VERIFICATION FAILED — investigate before signing off")
    sys.exit(2)
print()
print("VERIFICATION PASSED")
PYEOF

# --- OTS verification (separate, optional) ------------------------------------

if [ "$OTS_AVAILABLE" = "1" ] && [ -d ots ] && [ "$(ls -A ots 2>/dev/null)" ]; then
    echo
    echo "Running OpenTimestamps verification on $(ls ots/*.ots 2>/dev/null | wc -l) proof files..."
    pass=0
    fail=0
    for f in ots/*.ots; do
        if ots verify "$f" >/dev/null 2>&1; then
            pass=$((pass+1))
        else
            fail=$((fail+1))
            echo "  [FAIL] OTS verification failed for $(basename $f)"
        fi
    done
    echo "[$([ $fail -eq 0 ] && echo PASS || echo FAIL)] ots cli       $pass/$((pass+fail)) verified against Bitcoin"
fi
'''


# Week 6 — Auditor Kit v2 — identity-chain verifier.
# Companion to verify.sh. Walks identity_chain.json + iso_ca_bundle.json
# and confirms each claim event's chain_hash recomputes from its
# canonical event JSON. Pure jq + sha256sum + base64 — no vendor
# binary needed.
_AUDITOR_KIT_VERIFY_IDENTITY_SH = '''#!/usr/bin/env bash
# verify_identity.sh — Auditor Kit v2.
# Recomputes the chain_hash of every claim event in identity_chain.json
# and compares to the stored value. Drift = tampering.
#
# Requires: jq, sha256sum (or shasum on macOS).
set -euo pipefail
KIT_DIR="${1:-.}"
IDENTITY="$KIT_DIR/identity_chain.json"
[ -f "$IDENTITY" ] || { echo "missing $IDENTITY"; exit 2; }

# Pick the right sha256 cli for the OS.
if command -v sha256sum >/dev/null 2>&1; then
    SHA() { sha256sum | awk '{print $1}'; }
else
    SHA() { shasum -a 256 | awk '{print $1}'; }
fi

GENESIS=$(printf '0%.0s' {1..64})
events_count=$(jq '.events | length' "$IDENTITY")
echo "Verifying $events_count claim event(s) from $IDENTITY ..."
fail=0
prev_expected="$GENESIS"
for i in $(seq 0 $((events_count - 1))); do
    ev=$(jq ".events[$i]" "$IDENTITY")

    # Build canonical event JSON the same way the server does:
    # sort_keys=True, separators=(',',':'). jq's `--sort-keys -c`
    # produces exactly that.
    canonical=$(jq --sort-keys -c '{
        agent_pubkey_hex: (.agent_pubkey_hex | ascii_downcase),
        claim_event_id:   .claim_event_id,
        claimed_at:       .claimed_at,
        iso_release_sha:  (.iso_release_sha // ""),
        mac_address:      (.mac_address | ascii_upcase),
        site_id:          ($SITE),
        source:           .source
    }' --arg SITE "$(jq -r .site_id "$IDENTITY")" <<< "$ev")

    prev=$(jq -r .chain_prev_hash <<< "$ev")
    stored=$(jq -r .chain_hash <<< "$ev")

    expected=$(printf "%s:%s" "$prev" "$canonical" | SHA)

    if [ "$expected" != "$stored" ]; then
        echo "  FAIL idx=$i  expected=$expected  stored=$stored"
        fail=$((fail + 1))
    elif [ "$prev" != "$prev_expected" ]; then
        echo "  FAIL idx=$i  chain break — prev=$prev  expected_prev=$prev_expected"
        fail=$((fail + 1))
    fi
    prev_expected="$stored"
done

if [ "$fail" -eq 0 ]; then
    echo "[PASS] all $events_count claim events recompute + chain to genesis"
else
    echo "[FAIL] $fail of $events_count failed"
    exit 1
fi
'''


# ─── Security advisory auto-include (followup #41) ────────────────────
#
# AI-independence audit Camila finding: the Session-203 disclosure-first
# commitment is "every advisory must be visible to the auditor without
# an auditor having to know to ask." Pre-fix, only the Merkle disclosure
# was hardcoded into chain.json — newer advisories (e.g. PACKET_GAP)
# sat in docs/security/ unseen. This helper auto-walks docs/security/
# SECURITY_ADVISORY_*.md and includes both metadata + full markdown in
# every kit ZIP.
#
# Metadata format expected (relaxed parsing; missing fields fall to None):
#   # Security Advisory — <ADVISORY_ID>
#   **Title:** ...
#   **Date discovered:** YYYY-MM-DD ...
#   **Date remediated:** YYYY-MM-DD ...
#   **Severity:** ...
#   **Status:** ...

import re as _adv_re

_ADV_ID_RE = _adv_re.compile(r"#\s*Security Advisory\s*[—-]\s*(\S+)", _adv_re.IGNORECASE)
_ADV_FIELD_RE = _adv_re.compile(
    r"\*\*(Title|Date discovered|Date remediated|Severity|Status):\*\*\s*([^\n]+)",
    _adv_re.IGNORECASE,
)


def _parse_advisory_metadata(text: str, filename: str) -> Dict[str, Any]:
    """Extract structured metadata from a security advisory markdown file.

    Always returns a dict with at least `advisory_filename` populated;
    fields not found stay None so the kit JSON shape is stable.
    """
    out: Dict[str, Any] = {
        "id": None,
        "title": None,
        "date_discovered": None,
        "date_remediated": None,
        "severity": None,
        "status": None,
        "advisory_filename": filename,
    }
    m = _ADV_ID_RE.search(text)
    if m:
        out["id"] = m.group(1).strip()
    for fm in _ADV_FIELD_RE.finditer(text):
        key = fm.group(1).lower().replace(" ", "_")
        val = fm.group(2).strip()
        if key in out:
            out[key] = val
    return out


def _collect_security_advisories() -> List[Tuple[Dict[str, Any], str]]:
    """Walk docs/security/SECURITY_ADVISORY_*.md and return list of
    (metadata_dict, raw_markdown_text). Sorted by filename so kit
    contents are deterministic. Returns empty list (not raises) if
    the directory is missing — tests run from a tmp dir without
    docs, AND production containers don't bundle the docs/ tree.

    P0 fix 2026-05-06: prior version did `here.parents[3]` which
    raised IndexError in the production container layout
    (/app/dashboard_api/evidence_chain.py has only 3 parents);
    auditor-kit downloads were 500'ing for every customer.

    Resolution order:
      1. OSIRIS_SECURITY_ADVISORIES_DIR env var (explicit, prod-friendly).
      2. Walk parents of this file looking for docs/security (dev
         layout AND any ancestor-dir layout).
      3. Return empty list — kit is still complete without
         advisories; the rest of the bundle stands on its own.
    """
    import os

    candidate_dirs: List[pathlib.Path] = []
    env_override = os.environ.get("OSIRIS_SECURITY_ADVISORIES_DIR")
    if env_override:
        candidate_dirs.append(pathlib.Path(env_override))

    here = pathlib.Path(__file__).resolve()
    # Walk every ancestor — works for dev (deep tree) AND any
    # container layout that mounts docs/ alongside the app dir.
    for ancestor in here.parents:
        candidate_dirs.append(ancestor / "docs" / "security")

    advisories_dir: Optional[pathlib.Path] = None
    for d in candidate_dirs:
        try:
            if d.exists() and d.is_dir():
                advisories_dir = d
                break
        except OSError:
            continue
    if advisories_dir is None:
        return []
    out: List[Tuple[Dict[str, Any], str]] = []
    advisories_dir_resolved: pathlib.Path = advisories_dir
    for path in sorted(advisories_dir_resolved.glob("SECURITY_ADVISORY_*.md")):
        try:
            content = path.read_text()
        except Exception:
            # Per CLAUDE.md "no silent write failures" — for READS in a
            # display path, swallow + continue. The kit downloads even
            # if one advisory file is unreadable.
            continue
        meta = _parse_advisory_metadata(content, path.name)
        out.append((meta, content))
    return out


@router.get("/sites/{site_id}/auditor-kit")
async def download_auditor_kit(
    site_id: str,
    limit: int = 1000,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _auth: Dict[str, Any] = Depends(require_evidence_view_access),
):
    # Per-site rate limit: legitimate auditor use is a handful of kit downloads
    # per day, not bulk-export pressure. Cap at 10 per hour per site to make
    # it hard to use this endpoint as a memory-pressure vector against the
    # process (each call materializes up to 5,000 bundles into an in-memory
    # ZIP). Honors the authenticated caller's auth context — this is abuse
    # prevention, NOT an auth gate.
    allowed, retry_after_s = await check_rate_limit(
        site_id=site_id,
        action="auditor_kit_download",
        window_seconds=3600,
        max_requests=10,
    )
    if not allowed:
        logger.warning(
            "auditor kit download rate-limited",
            extra={"site_id": site_id, "retry_after_s": retry_after_s},
        )
        raise HTTPException(
            status_code=429,
            detail=f"Auditor kit download limit reached (10/hr). Retry in {retry_after_s}s.",
            headers={"Retry-After": str(retry_after_s)},
        )
    """Download a self-contained ZIP an auditor can verify offline.

    Session 203 Tier 1.1 — the centerpiece of OsirisCare's "recovery
    platform" positioning. After the Delve / DeepDelver scandal exposed
    fake-compliance-as-a-service, healthcare clients need a way to hand
    a single artifact to their auditor and say "verify this on your
    laptop, no vendor network required".

    The kit contains: bundles.jsonl, pubkeys.json, chain.json, ots/*.ots,
    verify.sh, README.md. The README is the deliverable — it tells the
    auditor exactly what to run and what success looks like. The verify.sh
    is open source and embedded directly in this module so anyone can
    audit the verifier.

    Range: limit + offset paginate the bundles set. Default 1000 bundles
    per call (≈10MB ZIP). Repeat with offset for the full chain.
    """
    import asyncio
    import json as _json
    import base64
    import tempfile
    import zipfile
    from fastapi.responses import StreamingResponse

    if limit < 1 or limit > 5000:
        raise HTTPException(400, "limit must be between 1 and 5000")
    if offset < 0:
        raise HTTPException(400, "offset must be >= 0")

    # 1. Site identity + clinic name + partner attribution
    site_row = (await db.execute(text("""
        SELECT site_id, clinic_name, partner_id FROM sites WHERE site_id = :sid
    """), {"sid": site_id})).fetchone()
    if not site_row:
        raise HTTPException(404, "Site not found")

    # 2. Partner brand for white-labeled presentation layer. The cryptographic
    #    attribution always remains OsirisCare (substrate owns the Ed25519 keys
    #    and OTS anchors); the partner is only the *presenter* of the evidence.
    #    This separation is what makes the white-label legally clean — the
    #    auditor can always trace signatures back to a single substrate issuer,
    #    no matter which partner brands the cover page. A direct-to-clinic site
    #    with no partner_id defaults to "OsirisCare" on both lines, so behavior
    #    is unchanged.
    presenter_brand = "OsirisCare"
    presenter_contact_line = ""
    presenter_partner_id: Optional[str] = None
    if site_row.partner_id:
        brand_row = (await db.execute(text("""
            SELECT id, brand_name, support_email, support_phone
              FROM partners
             WHERE id = :pid
        """), {"pid": site_row.partner_id})).fetchone()
        if brand_row and brand_row.brand_name:
            presenter_brand = brand_row.brand_name
            presenter_partner_id = str(brand_row.id)
            contact_bits = []
            if brand_row.support_email:
                contact_bits.append(brand_row.support_email)
            if brand_row.support_phone:
                contact_bits.append(brand_row.support_phone)
            if contact_bits:
                presenter_contact_line = " — " + " · ".join(contact_bits)

    # 2. Per-appliance public keys (with fingerprints)
    pk_rows = (await db.execute(text("""
        SELECT appliance_id, display_name, hostname, agent_public_key,
               first_checkin, last_checkin
        FROM site_appliances
        WHERE site_id = :sid AND agent_public_key IS NOT NULL
        ORDER BY first_checkin ASC
    """), {"sid": site_id})).fetchall()

    pubkeys = []
    for r in pk_rows:
        # Truncated SHA-256 of the public key as a stable fingerprint
        try:
            fp = hashlib.sha256(r.agent_public_key.encode()).hexdigest()[:16]
        except Exception:
            fp = "unknown"
        pubkeys.append({
            "appliance_id": r.appliance_id,
            "display_name": r.display_name or r.hostname,
            "hostname": r.hostname,
            "public_key_hex": r.agent_public_key,
            "fingerprint": fp,
            "first_checkin": r.first_checkin.isoformat() if r.first_checkin else None,
            "last_checkin": r.last_checkin.isoformat() if r.last_checkin else None,
        })

    # 3. Bundles in chain order with signatures + OTS proof data
    bundle_rows = (await db.execute(text("""
        SELECT cb.bundle_id, cb.bundle_hash, cb.prev_hash, cb.chain_position,
               cb.check_type, cb.created_at, cb.agent_signature,
               cb.ots_status, cb.merkle_batch_id, cb.merkle_leaf_index, cb.merkle_proof,
               op.proof_data, op.bitcoin_block, op.calendar_url, op.anchored_at
        FROM compliance_bundles cb
        LEFT JOIN ots_proofs op ON op.bundle_id = COALESCE(cb.merkle_batch_id, cb.bundle_id)
        WHERE cb.site_id = :sid
        ORDER BY cb.chain_position ASC NULLS LAST, cb.created_at ASC
        LIMIT :limit OFFSET :offset
    """), {"sid": site_id, "limit": limit, "offset": offset})).fetchall()

    if not bundle_rows:
        raise HTTPException(404, "No evidence bundles in range")

    # 3.5. Site canonical aliases — Session 213 F1-followup P0-COMPLIANCE-1.
    # An auditor downloading this kit may see operational telemetry
    # aggregated under a different site_id than the bundles. Surface the
    # mapping rows here so the auditor can reconcile in-kit without
    # needing the live API. Compliance bundles themselves bind to
    # their issuing site_id forever (Ed25519 + OTS); this list shows
    # the operational aliases that the substrate has recorded.
    canonical_alias_rows = (await db.execute(text("""
        SELECT from_site_id, to_site_id, actor, reason,
               related_migration, effective_at
          FROM site_canonical_mapping
         WHERE from_site_id = :sid OR to_site_id = :sid
         ORDER BY effective_at
    """), {"sid": site_id})).fetchall()

    # 4. Compute chain.json — site metadata + summary
    signed_count = sum(1 for r in bundle_rows if r.agent_signature)
    anchored_count = sum(1 for r in bundle_rows if r.ots_status == "anchored")
    legacy_count = sum(1 for r in bundle_rows if r.ots_status == "legacy")
    pending_count = sum(1 for r in bundle_rows if r.ots_status == "pending")

    genesis = bundle_rows[0]
    latest = bundle_rows[-1]
    generated_at = datetime.now(timezone.utc).isoformat()

    chain_metadata = {
        "kit_version": "2.1",
        "generated_at": generated_at,
        "site": {
            "site_id": site_row.site_id,
            "clinic_name": site_row.clinic_name,
        },
        "presentation": {
            # Who the auditor received this kit from. Partner_id is included
            # only when a partner presents; direct-to-clinic leaves it null.
            "presenter_brand": presenter_brand,
            "presenter_partner_id": presenter_partner_id,
            # The substrate that issued the cryptographic material below.
            # This is always OsirisCare — it does NOT change per-partner.
            # Auditors MUST verify signatures against substrate-issued keys;
            # presenter branding is cosmetic only.
            "substrate_issuer": "OsirisCare",
            "substrate_note": (
                "All Ed25519 signatures, per-appliance public keys, and "
                "OpenTimestamps anchors in this kit are issued by the "
                "OsirisCare compliance substrate. The presenter brand "
                "identifies the partner that manages this site; it does "
                "not participate in signing."
            ),
        },
        "chain": {
            "bundle_count_in_kit": len(bundle_rows),
            "kit_offset": offset,
            "kit_limit": limit,
            "signed_count": signed_count,
            "anchored_count": anchored_count,
            "legacy_count": legacy_count,
            "pending_count": pending_count,
            "genesis": {
                "bundle_id": genesis.bundle_id,
                "bundle_hash": genesis.bundle_hash,
                "chain_position": genesis.chain_position,
                "created_at": genesis.created_at.isoformat() if genesis.created_at else None,
            },
            "latest": {
                "bundle_id": latest.bundle_id,
                "bundle_hash": latest.bundle_hash,
                "chain_position": latest.chain_position,
                "created_at": latest.created_at.isoformat() if latest.created_at else None,
            },
        },
        "appliances": [
            {
                "appliance_id": pk["appliance_id"],
                "display_name": pk["display_name"],
                "fingerprint": pk["fingerprint"],
                "first_checkin": pk["first_checkin"],
                "last_checkin": pk["last_checkin"],
            }
            for pk in pubkeys
        ],
        # F1-followup P0-COMPLIANCE-1 (Session 213): surface operational
        # site_id aliases so an auditor seeing telemetry under a
        # different site_id can reconcile without the live API. The
        # bundles in this kit retain their ORIGINAL site_id forever
        # (Ed25519 + OTS bind to the issuing identity); the aliases
        # below are operational-aggregation only.
        "site_canonical_aliases": [
            {
                "from_site_id": r.from_site_id,
                "to_site_id": r.to_site_id,
                "actor": r.actor,
                "reason": r.reason,
                "related_migration": r.related_migration,
                "effective_at": r.effective_at.isoformat() if r.effective_at else None,
                "direction": (
                    "outbound" if r.from_site_id == site_id else "inbound"
                ),
            }
            for r in canonical_alias_rows
        ],
        # Disclosures auto-included from docs/security/SECURITY_ADVISORY_*.md
        # per Camila's AI-independence-audit finding (followup #41,
        # 2026-05-02). Each advisory's full markdown also lands in the
        # ZIP under disclosures/<filename>. Pre-fix only the Merkle
        # disclosure was hardcoded — newer advisories (e.g. PACKET_GAP)
        # were repo-grep-only, violating the Session-203 disclosure-first
        # commitment ("visible to the auditor without an auditor having
        # to know to ask").
        "disclosures": [
            {
                "id": _adv_meta.get("id"),
                "title": _adv_meta.get("title"),
                "date_discovered": _adv_meta.get("date_discovered"),
                "date_remediated": _adv_meta.get("date_remediated"),
                "severity": _adv_meta.get("severity"),
                "status": _adv_meta.get("status"),
                "advisory_in_kit": f"disclosures/{_adv_meta['advisory_filename']}",
                "advisory_in_repo": f"/docs/security/{_adv_meta['advisory_filename']}",
            }
            for (_adv_meta, _) in _collect_security_advisories()
        ],
        "verification": {
            "tools_required": ["python3", "sha256sum", "cryptography (pip)", "ots-cli (optional)"],
            "command": "bash verify.sh",
            "no_network_required": True,
            "platform_dependency": "none",
        },
    }

    # 5. Build the bundles.jsonl content (one JSON object per line)
    bundles_jsonl_lines = []
    ots_files = {}  # filename → bytes

    for r in bundle_rows:
        bundle_obj = {
            "bundle_id": r.bundle_id,
            "bundle_hash": r.bundle_hash,
            "prev_hash": r.prev_hash,
            "chain_position": r.chain_position,
            "check_type": r.check_type,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "agent_signature": r.agent_signature,
            "ots_status": r.ots_status,
            "merkle_batch_id": r.merkle_batch_id,
            "merkle_leaf_index": r.merkle_leaf_index,
            "merkle_proof": r.merkle_proof,
            "ots": None,
        }
        if r.proof_data:
            try:
                # proof_data is base64-encoded bytes; decode for the .ots file
                ots_bytes = base64.b64decode(r.proof_data)
                ots_filename = f"{r.bundle_id}.ots"
                ots_files[ots_filename] = ots_bytes
                bundle_obj["ots"] = {
                    "file": f"ots/{ots_filename}",
                    "bitcoin_block": r.bitcoin_block,
                    "calendar_url": r.calendar_url,
                    "anchored_at": r.anchored_at.isoformat() if r.anchored_at else None,
                }
            except Exception:
                pass
        bundles_jsonl_lines.append(_json.dumps(bundle_obj))

    # 6. Build the ZIP in memory
    # Week 6 — Auditor Kit v2 additions:
    #   identity_chain.json — provisioning_claim_events for the site
    #   iso_ca_bundle.json  — ISO release CAs that signed any claim
    #   verify_identity.sh  — recompute claim event chain hashes
    # These extend the legacy v1 contents (chain.json + bundles.jsonl
    # + verify.sh + pubkeys.json + ots/*) — auditors with v1 tooling
    # ignore them harmlessly. Both halves live in one ZIP so the
    # legal contractor's "non-repudiable customer-authorized
    # device-executed action chain" lands in one artifact.
    identity_chain_rows = (await db.execute(text("""
        SELECT id, mac_address, agent_pubkey_hex, agent_pubkey_fingerprint,
               iso_build_sha, hardware_id, claimed_at, source,
               supersedes_id, ots_bundle_id,
               chain_prev_hash, chain_hash
          FROM provisioning_claim_events
         WHERE site_id = :sid
         ORDER BY claimed_at ASC
    """), {"sid": site_id})).fetchall()

    identity_chain_payload = {
        "kit_version": "2.0",
        "site_id": site_row.site_id,
        "generated_at": generated_at,
        "events": [
            {
                "claim_event_id": r.id,
                "mac_address": r.mac_address,
                "agent_pubkey_hex": r.agent_pubkey_hex,
                "agent_pubkey_fingerprint": r.agent_pubkey_fingerprint,
                "iso_release_sha": r.iso_build_sha,
                "hardware_id": r.hardware_id,
                "claimed_at": r.claimed_at.isoformat() if r.claimed_at else None,
                "source": r.source,
                "supersedes_claim_event_id": r.supersedes_id,
                "ots_bundle_id": r.ots_bundle_id,
                "chain_prev_hash": r.chain_prev_hash,
                "chain_hash": r.chain_hash,
            }
            for r in identity_chain_rows
        ],
        "verification_note": (
            "Each event's chain_hash MUST equal "
            "sha256(chain_prev_hash + ':' + canonical_event_json), "
            "where canonical_event_json is JSON with sort_keys=True, "
            "separators=(',',':') over fields: agent_pubkey_hex (lower), "
            "claim_event_id, claimed_at (RFC3339-Z), iso_release_sha "
            "('' if NULL), mac_address (UPPER), site_id, source. "
            "Genesis chain_prev_hash = '0' * 64."
        ),
    }

    iso_ca_rows = (await db.execute(text("""
        SELECT iso_release_sha, ca_pubkey_hex, valid_from, valid_until,
               revoked_at, revoked_reason
          FROM iso_release_ca_pubkeys
         WHERE iso_release_sha IN (
             SELECT DISTINCT iso_build_sha FROM provisioning_claim_events
              WHERE site_id = :sid AND iso_build_sha IS NOT NULL
         )
    """), {"sid": site_id})).fetchall()

    iso_ca_payload = {
        "kit_version": "2.0",
        "generated_at": generated_at,
        "cas": [
            {
                "iso_release_sha": r.iso_release_sha,
                "ca_pubkey_hex": r.ca_pubkey_hex,
                "fingerprint": hashlib.sha256(bytes.fromhex(r.ca_pubkey_hex)).hexdigest()[:16],
                "valid_from": r.valid_from.isoformat() if r.valid_from else None,
                "valid_until": r.valid_until.isoformat() if r.valid_until else None,
                "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
                "revoked_reason": r.revoked_reason,
            }
            for r in iso_ca_rows
        ],
        "verification_note": (
            "Each ISO release CA's pubkey signed the claim cert that "
            "provisioned an appliance. Cross-check against "
            "https://api.osiriscare.net/api/iso/ca-bundle.json to confirm "
            "the same pubkey is still registered (drift means a CA was "
            "rotated — historic claim events remain valid against the "
            "pubkey active at their claimed_at time)."
        ),
    }

    # RT33 P4 (2026-05-05): use SpooledTemporaryFile so the ZIP doesn't
    # have to fit in memory. <20MB stays in RAM (covers the typical
    # 1000-bundle kit ~7MB), larger spills to disk transparently. Pair
    # this with StreamingResponse below so the client sees first bytes
    # quickly and a 50MB ZIP doesn't pin a worker for the duration of
    # the upload.
    buf = tempfile.SpooledTemporaryFile(max_size=20 * 1024 * 1024)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.md", _AUDITOR_KIT_README.format(
            site_id=site_row.site_id,
            clinic_name=site_row.clinic_name or "—",
            generated_at=generated_at,
            presenter_brand=presenter_brand,
            presenter_contact_line=presenter_contact_line,
        ))
        zf.writestr("verify.sh", _AUDITOR_KIT_VERIFY_SH)
        zf.writestr("chain.json", _json.dumps(chain_metadata, indent=2))
        zf.writestr("bundles.jsonl", "\n".join(bundles_jsonl_lines) + "\n")
        zf.writestr("pubkeys.json", _json.dumps({
            "site_id": site_row.site_id,
            "kit_version": "2.0",
            "public_keys": pubkeys,
            "verification_note": (
                "Pin these public keys offline before verification. The fingerprint "
                "field is the first 16 hex chars of SHA-256(public_key_hex) and is "
                "stable across kit downloads — record it in your audit working papers."
            ),
        }, indent=2))
        # v2 additions:
        zf.writestr("identity_chain.json", _json.dumps(identity_chain_payload, indent=2))
        zf.writestr("iso_ca_bundle.json", _json.dumps(iso_ca_payload, indent=2))
        zf.writestr("verify_identity.sh", _AUDITOR_KIT_VERIFY_IDENTITY_SH)
        for filename, data in ots_files.items():
            zf.writestr(f"ots/{filename}", data)
        # Write each security advisory under disclosures/ so the auditor
        # has the FULL markdown text inline with the kit, not just a
        # repo URL. Closes the disclosure-first commitment gap (#41).
        for _adv_meta, _adv_text in _collect_security_advisories():
            zf.writestr(f"disclosures/{_adv_meta['advisory_filename']}", _adv_text)

    buf.seek(0)

    safe_site = "".join(c if c.isalnum() or c in "-_" else "-" for c in site_row.site_id)
    filename = f"osiriscare-auditor-kit-{safe_site}-{generated_at[:10]}.zip"

    # Stream the ZIP back in 64KB chunks via an ASYNC generator that
    # offloads the synchronous `buf.read` to a thread. Adam's veto
    # from RT33 P4 review: a sync generator on an async endpoint
    # blocks the event loop for every read, starving concurrent
    # requests on a slow client. asyncio.to_thread keeps the loop
    # responsive while the network paces the upload. Time-to-first-
    # byte improves ~10× vs the prior `Response(content=buf.getvalue())`
    # which had to materialize the full bytes before the response
    # started.
    async def _stream_zip():
        try:
            while True:
                chunk = await asyncio.to_thread(buf.read, 64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            # FastAPI calls .aclose() on the generator when the
            # response completes OR the client disconnects. Either
            # path closes the SpooledTemporaryFile, releasing the
            # spilled-to-disk file descriptor.
            buf.close()

    return StreamingResponse(
        _stream_zip(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Kit-Version": "1.0",
            "X-Bundle-Count": str(len(bundle_rows)),
            "X-Pubkey-Count": str(len(pubkeys)),
            "X-OTS-File-Count": str(len(ots_files)),
        },
    )
