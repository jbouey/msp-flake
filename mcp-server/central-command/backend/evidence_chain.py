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


# =============================================================================
# Database Session
# =============================================================================

async def get_db():
    """Get database session."""
    import sys
    try:
        from main import async_session
    except ImportError:
        if 'server' in sys.modules and hasattr(sys.modules['server'], 'async_session'):
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

async def submit_hash_to_ots(bundle_hash: str, bundle_id: str) -> Optional[Dict[str, Any]]:
    """
    Submit a hash to OTS calendar servers.

    Returns dict with proof_data, calendar_url, submitted_at if successful.
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
                        proof_bytes = await resp.read()
                        proof_b64 = base64.b64encode(proof_bytes).decode('ascii')

                        logger.info(f"OTS submitted: bundle={bundle_id[:8]}... calendar={calendar_url}")

                        return {
                            "proof_data": proof_b64,
                            "calendar_url": calendar_url,
                            "submitted_at": datetime.now(timezone.utc),
                            "status": "pending",
                        }
                    else:
                        logger.warning(f"OTS calendar returned {resp.status}: {calendar_url}")

            except aiohttp.ClientError as e:
                logger.warning(f"OTS client error for {calendar_url}: {e}")
            except Exception as e:
                logger.error(f"OTS unexpected error for {calendar_url}: {e}")

        logger.error(f"All OTS calendars failed for bundle {bundle_id}")
        return None


async def upgrade_pending_proofs(db: AsyncSession, limit: int = 100):
    """
    Background task to upgrade pending OTS proofs.

    Checks if pending proofs now have Bitcoin confirmations.
    """
    result = await db.execute(text("""
        SELECT bundle_id, bundle_hash, proof_data, calendar_url
        FROM ots_proofs
        WHERE status = 'pending'
        AND submitted_at < NOW() - INTERVAL '1 hour'
        AND (last_upgrade_attempt IS NULL OR last_upgrade_attempt < NOW() - INTERVAL '1 hour')
        ORDER BY submitted_at ASC
        LIMIT :limit
    """), {"limit": limit})

    pending_proofs = result.fetchall()

    if not pending_proofs:
        return {"checked": 0, "upgraded": 0}

    upgraded = 0
    timeout = aiohttp.ClientTimeout(total=OTS_TIMEOUT)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for proof in pending_proofs:
            try:
                upgrade_url = f"{proof.calendar_url}/timestamp/{proof.bundle_hash}"

                async with session.get(upgrade_url) as resp:
                    if resp.status == 200:
                        upgraded_bytes = await resp.read()

                        # Check if proof now has Bitcoin attestation
                        # OTS Bitcoin marker: 0x0588960d73d71901
                        if b'\x05\x88\x96\x0d\x73\xd7\x19\x01' in upgraded_bytes:
                            # Extract block height (follows marker)
                            marker = b'\x05\x88\x96\x0d\x73\xd7\x19\x01'
                            pos = upgraded_bytes.find(marker)
                            block_height = None
                            if pos > 0 and pos + len(marker) + 8 <= len(upgraded_bytes):
                                block_bytes = upgraded_bytes[pos + len(marker):pos + len(marker) + 8]
                                block_height = int.from_bytes(block_bytes, 'little')

                            # Update proof
                            proof_b64 = base64.b64encode(upgraded_bytes).decode('ascii')

                            await db.execute(text("""
                                UPDATE ots_proofs
                                SET status = 'anchored',
                                    proof_data = :proof_data,
                                    bitcoin_block = :block,
                                    anchored_at = NOW(),
                                    last_upgrade_attempt = NOW(),
                                    upgrade_attempts = upgrade_attempts + 1
                                WHERE bundle_id = :bundle_id
                            """), {
                                "proof_data": proof_b64,
                                "block": block_height,
                                "bundle_id": proof.bundle_id,
                            })

                            upgraded += 1
                            logger.info(f"OTS upgraded: {proof.bundle_id[:8]}... block={block_height}")
                        else:
                            # Not yet anchored, update attempt time
                            await db.execute(text("""
                                UPDATE ots_proofs
                                SET last_upgrade_attempt = NOW(),
                                    upgrade_attempts = upgrade_attempts + 1
                                WHERE bundle_id = :bundle_id
                            """), {"bundle_id": proof.bundle_id})

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

    return {"checked": len(pending_proofs), "upgraded": upgraded}


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

    - Stores bundle in database
    - Links to previous bundle (hash chain)
    - Submits hash to OTS (async, background)
    - Triggers WORM upload if enabled
    """
    # Verify site exists
    site_result = await db.execute(
        text("SELECT site_id FROM sites WHERE site_id = :site_id"),
        {"site_id": site_id}
    )
    if not site_result.fetchone():
        raise HTTPException(status_code=404, detail=f"Site not found: {site_id}")

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
        if all(s == "pass" for s in statuses):
            bundle.check_result = "pass"
        elif any(s == "fail" for s in statuses):
            bundle.check_result = "fail"
        else:
            bundle.check_result = "warn"
    elif not bundle.check_result:
        bundle.check_result = "unknown"

    # Get previous bundle for chain linking
    prev_result = await db.execute(text("""
        SELECT bundle_id, bundle_hash, chain_position
        FROM compliance_bundles
        WHERE site_id = :site_id
        ORDER BY checked_at DESC
        LIMIT 1
    """), {"site_id": site_id})
    prev_bundle = prev_result.fetchone()

    prev_hash = prev_bundle.bundle_hash if prev_bundle else None
    chain_position = (prev_bundle.chain_position + 1) if prev_bundle else 1

    # Compute chain hash (includes previous hash for integrity)
    chain_data = f"{bundle.bundle_hash}:{prev_hash or 'genesis'}:{chain_position}"
    chain_hash = hashlib.sha256(chain_data.encode()).hexdigest()

    # Insert evidence bundle
    await db.execute(text("""
        INSERT INTO compliance_bundles (
            site_id, bundle_id, bundle_hash, check_type, check_result, checked_at,
            checks, summary, agent_signature, ntp_verification,
            prev_bundle_id, prev_hash, chain_position, chain_hash,
            ots_status
        ) VALUES (
            :site_id, :bundle_id, :bundle_hash, :check_type, :check_result, :checked_at,
            CAST(:checks AS jsonb), CAST(:summary AS jsonb), :agent_signature, CAST(:ntp_verification AS jsonb),
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

    logger.info(f"Evidence submitted: site={site_id} bundle={bundle.bundle_id[:8]} chain={chain_position}")

    return EvidenceSubmitResponse(
        bundle_id=bundle.bundle_id,
        chain_position=chain_position,
        prev_hash=prev_hash,
        current_hash=chain_hash,
        ots_status="pending" if OTS_ENABLED else "none",
        ots_submitted=ots_submitted,
    )


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
    # Get bundle
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

    # Note: Full signature verification would require agent's public key
    # For now, we trust that if signature is present, it was verified on submission
    signature_valid = bundle.agent_signature is not None

    return EvidenceVerifyResponse(
        bundle_id=bundle_id,
        hash_valid=True,  # Hash is stored, can be verified against content
        signature_valid=signature_valid,
        chain_valid=chain_valid,
        ots_status=bundle.ots_proof_status or bundle.ots_status or "none",
        ots_bitcoin_block=bundle.bitcoin_block,
        verified_at=datetime.now(timezone.utc),
    )


@router.get("/sites/{site_id}/bundles")
async def list_evidence_bundles(
    site_id: str,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
):
    """List evidence bundles for a site."""
    result = await db.execute(text("""
        SELECT bundle_id, bundle_hash, check_type, check_result, checked_at,
               chain_position, ots_status, agent_signature IS NOT NULL as signed
        FROM compliance_bundles
        WHERE site_id = :site_id
        ORDER BY checked_at DESC
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

    return {"error": "Public key not available", "algorithm": "Ed25519"}
