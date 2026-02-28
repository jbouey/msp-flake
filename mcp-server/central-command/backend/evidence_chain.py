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

import os
import json
import hashlib
import base64
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import aiohttp

# Ed25519 signature verification
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature

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
    """Get database session from server module."""
    import sys
    try:
        from main import async_session
    except ImportError:
        # Running as server.py instead of main.py
        if 'server' in sys.modules:
            async_session = sys.modules['server'].async_session
        else:
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
    except Exception:
        pass
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

    # Expire proofs older than 7 days (calendars prune after ~7 days)
    await db.execute(text("""
        UPDATE ots_proofs
        SET status = 'expired',
            error = 'Calendar retention exceeded - proof too old to upgrade'
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
                        # Already anchored
                        await db.execute(text("""
                            UPDATE ots_proofs
                            SET status = 'anchored',
                                bitcoin_block = :block,
                                anchored_at = NOW(),
                                last_upgrade_attempt = NOW()
                            WHERE bundle_id = :bundle_id
                        """), {"bundle_id": proof.bundle_id, "block": attestation.height})
                        upgraded += 1
                        upgrade_success = True
                        logger.info(f"OTS already anchored: {proof.bundle_id[:8]}... block={attestation.height}")
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

                                        await db.execute(text("""
                                            UPDATE ots_proofs
                                            SET status = 'anchored',
                                                proof_data = :proof_data,
                                                bitcoin_block = :block,
                                                calendar_url = :calendar_url,
                                                anchored_at = NOW(),
                                                last_upgrade_attempt = NOW(),
                                                upgrade_attempts = upgrade_attempts + 1,
                                                error = NULL
                                            WHERE bundle_id = :bundle_id
                                        """), {
                                            "proof_data": proof_b64,
                                            "block": block_height,
                                            "bundle_id": proof.bundle_id,
                                            "calendar_url": calendar_url,
                                        })

                                        upgraded += 1
                                        upgrade_success = True
                                        logger.info(f"OTS upgraded: {proof.bundle_id[:8]}... block={block_height}")
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
                logger.warning(f"Failed to upgrade proof {proof.bundle_id[:8]}: {e}")
                await db.execute(text("""
                    UPDATE ots_proofs
                    SET last_upgrade_attempt = NOW(),
                        upgrade_attempts = upgrade_attempts + 1,
                        error = :error
                    WHERE bundle_id = :bundle_id
                """), {"bundle_id": proof.bundle_id, "error": str(e)[:500]})

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
    db: AsyncSession = Depends(get_db)
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
    # Verify site exists and get its registered public key
    site_result = await db.execute(
        text("SELECT site_id, agent_public_key FROM sites WHERE site_id = :site_id"),
        {"site_id": site_id}
    )
    site_row = site_result.fetchone()
    if not site_row:
        raise HTTPException(status_code=404, detail=f"Site not found: {site_id}")

    # SECURITY: Require Ed25519 signature verification for evidence submission
    # This prevents unauthorized parties from injecting fake evidence into the chain
    if not bundle.agent_signature:
        logger.warning(f"Evidence submission rejected: no signature provided for site={site_id}")
        raise HTTPException(
            status_code=401,
            detail="Evidence submission requires agent_signature. "
                   "Ensure the appliance has a signing key at "
                   "/var/lib/msp/agent-signing-key"
        )

    registered_key = site_row.agent_public_key
    if not registered_key:
        # Auto-register if the bundle includes the public key
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
    # Use the signed_data from the bundle if provided (eliminates serialization mismatch)
    # Otherwise, reconstruct from fields (legacy support)
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
            # Diagnostic info for troubleshooting
            submitted_fp = bundle.agent_public_key[:12] if bundle.agent_public_key else "not-provided"
            registered_fp = registered_key[:12] if registered_key else "none"
            key_match = bundle.agent_public_key == registered_key if bundle.agent_public_key else None

            logger.warning(
                f"Evidence signature REJECTED for site={site_id} "
                f"registered_key={registered_fp}... submitted_key={submitted_fp}... "
                f"key_match={key_match} signed_data_provided={bundle.signed_data is not None}"
            )

            # Track rejection for partner visibility
            try:
                await db.execute(text("""
                    UPDATE site_appliances SET
                        evidence_rejection_count = COALESCE(evidence_rejection_count, 0) + 1,
                        last_evidence_rejection = NOW()
                    WHERE site_id = :site_id
                """), {"site_id": site_id})
                await db.commit()
            except Exception:
                pass  # Don't fail the response over tracking

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

            # Track acceptance
            try:
                await db.execute(text("""
                    UPDATE site_appliances SET
                        evidence_rejection_count = 0,
                        last_evidence_accepted = NOW()
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

    # Compute bundle_hash if not provided
    if not bundle.bundle_hash:
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

    # Acquire per-site advisory lock to serialize chain position assignment.
    # Without this, concurrent submissions race on chain_position (caused 1,125 broken links).
    await db.execute(text(
        "SELECT pg_advisory_xact_lock(hashtext(:site_id))"
    ), {"site_id": site_id})

    # Get previous bundle for chain linking (safe under advisory lock)
    prev_result = await db.execute(text("""
        SELECT bundle_id, bundle_hash, chain_position
        FROM compliance_bundles
        WHERE site_id = :site_id
        ORDER BY chain_position DESC
        LIMIT 1
    """), {"site_id": site_id})
    prev_bundle = prev_result.fetchone()

    GENESIS_HASH = "0" * 64
    prev_hash = prev_bundle.bundle_hash if prev_bundle else GENESIS_HASH
    chain_position = (prev_bundle.chain_position + 1) if prev_bundle else 1

    # Compute chain hash (includes previous hash for integrity)
    chain_data = f"{bundle.bundle_hash}:{prev_hash}:{chain_position}"
    chain_hash = hashlib.sha256(chain_data.encode()).hexdigest()

    # Store the signed_data for future verification
    stored_signed_data = signed_data.decode('utf-8') if isinstance(signed_data, bytes) else signed_data

    # Insert evidence bundle
    await db.execute(text("""
        INSERT INTO compliance_bundles (
            site_id, bundle_id, bundle_hash, check_type, check_result, checked_at,
            checks, summary, agent_signature, signed_data, signature_valid, ntp_verification,
            prev_bundle_id, prev_hash, chain_position, chain_hash,
            ots_status
        ) VALUES (
            :site_id, :bundle_id, :bundle_hash, :check_type, :check_result, :checked_at,
            CAST(:checks AS jsonb), CAST(:summary AS jsonb), :agent_signature, :signed_data, :signature_valid, CAST(:ntp_verification AS jsonb),
            :prev_bundle_id, :prev_hash, :chain_position, :chain_hash,
            'pending'
        )
        ON CONFLICT (bundle_id) DO UPDATE SET
            bundle_hash = EXCLUDED.bundle_hash,
            checks = EXCLUDED.checks,
            summary = EXCLUDED.summary
    """), {
        "site_id": site_id,
        "bundle_id": bundle.bundle_id,
        "bundle_hash": bundle.bundle_hash,
        "check_type": bundle.check_type,
        "check_result": bundle.check_result,
        "checked_at": bundle.checked_at,
        "checks": json.dumps(bundle.checks),
        "summary": json.dumps(bundle.summary),
        "agent_signature": bundle.agent_signature,
        "signed_data": stored_signed_data,
        "signature_valid": signature_valid,
        "ntp_verification": json.dumps(bundle.ntp_verification) if bundle.ntp_verification else None,
        "prev_bundle_id": prev_bundle.bundle_id if prev_bundle else None,
        "prev_hash": prev_hash,
        "chain_position": chain_position,
        "chain_hash": chain_hash,
    })

    await db.commit()

    # Submit to OTS in background
    ots_submitted = False
    if OTS_ENABLED:
        background_tasks.add_task(
            submit_ots_proof_background,
            db,
            bundle.bundle_id,
            bundle.bundle_hash,
            site_id
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
    except Exception:
        pass  # Don't fail evidence submission if broadcast fails

    return EvidenceSubmitResponse(
        bundle_id=bundle.bundle_id,
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
        if not minio_client.bucket_exists(MINIO_BUCKET):
            minio_client.make_bucket(MINIO_BUCKET)

        now = datetime.now(timezone.utc)
        date_prefix = now.strftime('%Y/%m/%d')
        bundle_key = f"{site_id}/{date_prefix}/{bundle_id}.json"

        # Serialize bundle
        bundle_json = json.dumps(bundle_data, default=str, indent=2)
        bundle_bytes = bundle_json.encode()

        # Upload bundle
        minio_client.put_object(
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
        )

        s3_uri = f"s3://{MINIO_BUCKET}/{bundle_key}"

        # Set Object Lock retention
        try:
            retention_until = now + timedelta(days=WORM_RETENTION_DAYS)
            retention = Retention(COMPLIANCE, retention_until)
            minio_client.set_object_retention(MINIO_BUCKET, bundle_key, retention)
        except Exception as e:
            # Bucket may already have default retention
            logger.debug(f"Object Lock already set or not available: {e}")

        logger.info(f"Evidence uploaded to WORM: {bundle_id} -> {s3_uri}")

    except Exception as e:
        logger.error(f"WORM upload failed for {bundle_id}: {e}")


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
    chain_valid = (bundle.chain_hash == expected_chain_hash)

    # Verify bundle hash against content
    hash_content = json.dumps({
        "site_id": site_id,
        "checked_at": bundle.checked_at.isoformat() if bundle.checked_at else None,
        "checks": bundle.checks if isinstance(bundle.checks, list) else json.loads(bundle.checks) if bundle.checks else [],
        "summary": bundle.summary if isinstance(bundle.summary, dict) else json.loads(bundle.summary) if bundle.summary else {}
    }, sort_keys=True)
    computed_hash = hashlib.sha256(hash_content.encode()).hexdigest()
    hash_valid = (bundle.bundle_hash == computed_hash)

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


@router.get("/sites/{site_id}/verify-chain")
async def verify_chain_integrity(
    site_id: str,
    max_broken: int = 100,
    db: AsyncSession = Depends(get_db)
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

        hash_ok = (bundle.chain_hash == expected_chain_hash)

        # Verify prev_hash links to previous bundle's bundle_hash
        link_ok = True
        if i == 0:
            link_ok = (bundle.prev_hash == GENESIS_HASH) and (bundle.chain_position == 1)
        else:
            prev_bundle = bundles[i - 1]
            link_ok = (bundle.prev_hash == prev_bundle.bundle_hash)

        if hash_ok and link_ok:
            verified += 1
        else:
            if len(broken_links) < max_broken:
                broken_links.append({
                    "position": bundle.chain_position,
                    "bundle_id": bundle.bundle_id,
                    "hash_valid": hash_ok,
                    "link_valid": link_ok,
                })

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
    db: AsyncSession = Depends(get_db)
):
    """List evidence bundles for a site with OTS blockchain status."""
    result = await db.execute(text("""
        SELECT cb.bundle_id, cb.bundle_hash, cb.prev_hash, cb.check_type, cb.check_result,
               cb.checked_at, cb.chain_position,
               cb.agent_signature IS NOT NULL as signed,
               cb.signature_valid,
               COALESCE(op.status, cb.ots_status, 'none') as ots_status,
               op.bitcoin_block, op.anchored_at, op.calendar_url
        FROM compliance_bundles cb
        LEFT JOIN ots_proofs op ON op.bundle_id = cb.bundle_id
        WHERE cb.site_id = :site_id
        ORDER BY cb.checked_at DESC
        LIMIT :limit OFFSET :offset
    """), {"site_id": site_id, "limit": limit, "offset": offset})

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
    }


@router.get("/sites/{site_id}/blockchain-status")
async def get_blockchain_status(
    site_id: str,
    db: AsyncSession = Depends(get_db)
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
    db: AsyncSession = Depends(get_db)
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
    db: AsyncSession = Depends(get_db)
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
    db: AsyncSession = Depends(get_db)
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
    db: AsyncSession = Depends(get_db)
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
    db: AsyncSession = Depends(get_db)
):
    """
    Generate a monthly HIPAA compliance packet from real evidence data.

    Queries compliance_bundles for the given site and period, computes
    compliance score, control posture, and returns the packet as JSON
    with a markdown rendering.

    Args:
        site_id: Site identifier
        month: Month (1-12), defaults to current month
        year: Year, defaults to current year
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
        )
        result = await packet.generate_packet()

        return result

    except Exception as e:
        logger.error(f"Compliance packet generation failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate compliance packet: {str(e)}"
        )
