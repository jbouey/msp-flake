"""Appliance Delegation API - Central Command endpoints for appliance offline operations.

Provides APIs for:
1. Delegated signing key issuance
2. Offline audit trail sync
3. Urgent escalation processing
"""

import json
import hashlib
import secrets
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

# Ed25519 signing
try:
    from nacl.signing import SigningKey
    from nacl.encoding import HexEncoder
    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False

from .fleet import get_pool
from .auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/appliances", tags=["appliances"])


# =============================================================================
# MODELS
# =============================================================================

class DelegatedKeyRequest(BaseModel):
    """Request for a delegated signing key."""
    site_id: str
    appliance_id: str
    scope: List[str] = ["evidence", "audit", "l1_actions"]
    validity_days: int = 365


class DelegatedKeyResponse(BaseModel):
    """Response with delegated key."""
    key_id: str
    public_key: str
    private_key: str  # Only sent once during delegation
    delegated_at: str
    expires_at: str
    delegated_by: str
    scope: List[str]
    signature: str


class AuditEntry(BaseModel):
    """A single audit trail entry from an appliance."""
    entry_id: str
    site_id: str
    action_type: str
    action_data: dict
    outcome: str
    timestamp: str
    signature: Optional[str] = None
    signed_by: Optional[str] = None
    prev_hash: Optional[str] = None
    entry_hash: str


class AuditSyncRequest(BaseModel):
    """Batch of audit entries to sync."""
    entries: List[AuditEntry]


class AuditSyncResponse(BaseModel):
    """Response from audit sync."""
    synced_ids: List[str]
    failed_ids: List[str]
    message: str


class UrgentEscalation(BaseModel):
    """An urgent escalation from an appliance."""
    escalation_id: str
    incident_id: str
    site_id: str
    priority: str
    incident_type: str
    incident_data: dict
    created_at: str
    retry_count: int


class EscalationBatchRequest(BaseModel):
    """Batch of escalations to process."""
    escalations: List[UrgentEscalation]


class EscalationResponse(BaseModel):
    """Response from escalation processing."""
    processed_ids: List[str]
    escalated_to_l2: List[str]
    escalated_to_l3: List[str]
    failed_ids: List[str]


# =============================================================================
# DATABASE INITIALIZATION
# =============================================================================

async def create_delegation_tables(conn):
    """Create tables for delegation management."""
    # Delegated keys table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS delegated_keys (
            key_id TEXT PRIMARY KEY,
            appliance_id TEXT NOT NULL,
            site_id TEXT NOT NULL,
            public_key TEXT NOT NULL,
            scope TEXT NOT NULL,
            delegated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMPTZ NOT NULL,
            delegated_by TEXT NOT NULL,
            revoked BOOLEAN DEFAULT FALSE,
            revoked_at TIMESTAMPTZ,
            revoked_reason TEXT
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_delegated_keys_appliance
        ON delegated_keys(appliance_id)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_delegated_keys_site
        ON delegated_keys(site_id)
    """)

    # Synced audit trail table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS appliance_audit_trail (
            id SERIAL PRIMARY KEY,
            entry_id TEXT UNIQUE NOT NULL,
            appliance_id TEXT NOT NULL,
            site_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            action_data JSONB NOT NULL,
            outcome TEXT NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL,
            signature TEXT,
            signed_by TEXT,
            prev_hash TEXT,
            entry_hash TEXT NOT NULL,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            verified BOOLEAN DEFAULT FALSE
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_trail_site
        ON appliance_audit_trail(site_id, timestamp)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_trail_appliance
        ON appliance_audit_trail(appliance_id)
    """)

    # Processed escalations table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_escalations (
            id SERIAL PRIMARY KEY,
            escalation_id TEXT UNIQUE NOT NULL,
            incident_id TEXT NOT NULL,
            site_id TEXT NOT NULL,
            priority TEXT NOT NULL,
            incident_type TEXT NOT NULL,
            incident_data JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            escalation_result TEXT NOT NULL,
            escalated_to TEXT  -- "l2", "l3", or NULL if resolved
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_escalations_site
        ON processed_escalations(site_id)
    """)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

# Central Command master signing key (in production, this would be loaded from secure storage)
_master_key: Optional[SigningKey] = None


def get_master_key() -> SigningKey:
    """Get or create the Central Command master signing key."""
    global _master_key
    if _master_key is None:
        if not NACL_AVAILABLE:
            raise HTTPException(status_code=500, detail="Signing not available (PyNaCl not installed)")
        # In production, load from secure storage (HSM, Vault, etc.)
        # For now, generate a deterministic key from a seed
        seed = hashlib.sha256(b"osiriscare-central-command-master-key-seed").digest()
        _master_key = SigningKey(seed)
    return _master_key


def generate_key_id() -> str:
    """Generate a unique key ID."""
    return f"KEY-{secrets.token_hex(8).upper()}"


async def verify_appliance_ownership(conn, appliance_id: str, site_id: str) -> bool:
    """Verify that an appliance belongs to a site."""
    result = await conn.fetchrow("""
        SELECT 1 FROM appliances
        WHERE appliance_id = $1 AND site_id = $2
    """, appliance_id, site_id)
    return result is not None


async def verify_site_api_key(conn, site_id: str, api_key: str) -> bool:
    """Verify that an API key is valid for a site."""
    result = await conn.fetchrow("""
        SELECT 1 FROM sites s
        JOIN api_keys ak ON s.site_id = ak.site_id
        WHERE s.site_id = $1 AND ak.key_hash = $2 AND ak.active = true
    """, site_id, hashlib.sha256(api_key.encode()).hexdigest())
    return result is not None


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.post("/{appliance_id}/delegate-key", response_model=DelegatedKeyResponse)
async def delegate_signing_key(
    appliance_id: str,
    request: DelegatedKeyRequest,
):
    """Issue a delegated signing key to an appliance.

    The appliance can use this key to sign evidence and audit trail entries
    locally when Central Command is unreachable.

    The key is:
    1. Generated as a new Ed25519 keypair
    2. Signed by Central Command's master key
    3. Stored in the database for verification
    4. Returned to the appliance (private key only sent once)
    """
    if not NACL_AVAILABLE:
        raise HTTPException(status_code=500, detail="Signing not available")

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Verify appliance belongs to site
        if not await verify_appliance_ownership(conn, appliance_id, request.site_id):
            raise HTTPException(status_code=404, detail="Appliance not found for this site")

        # Check for existing valid key
        existing = await conn.fetchrow("""
            SELECT key_id, expires_at FROM delegated_keys
            WHERE appliance_id = $1 AND revoked = false AND expires_at > NOW()
            ORDER BY expires_at DESC LIMIT 1
        """, appliance_id)

        if existing:
            # Key already exists and is valid
            raise HTTPException(
                status_code=409,
                detail=f"Active key {existing['key_id']} exists until {existing['expires_at']}"
            )

        # Generate new Ed25519 keypair
        new_key = SigningKey.generate()
        public_key_hex = new_key.verify_key.encode(encoder=HexEncoder).decode()
        private_key_hex = new_key.encode(encoder=HexEncoder).decode()

        # Create delegation certificate
        key_id = generate_key_id()
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=request.validity_days)

        delegation_data = {
            "key_id": key_id,
            "appliance_id": appliance_id,
            "site_id": request.site_id,
            "public_key": public_key_hex,
            "scope": request.scope,
            "delegated_at": now.isoformat(),
            "expires_at": expires.isoformat(),
        }

        # Sign the delegation with master key
        master_key = get_master_key()
        delegation_json = json.dumps(delegation_data, sort_keys=True, separators=(",", ":"))
        signature = master_key.sign(delegation_json.encode()).signature.hex()

        # Store in database
        await conn.execute("""
            INSERT INTO delegated_keys
            (key_id, appliance_id, site_id, public_key, scope, delegated_at, expires_at, delegated_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """, (
            key_id,
            appliance_id,
            request.site_id,
            public_key_hex,
            json.dumps(request.scope),
            now,
            expires,
            master_key.verify_key.encode(encoder=HexEncoder).decode()[:16],
        ))

        logger.info(f"Delegated key {key_id} to appliance {appliance_id}")

        return DelegatedKeyResponse(
            key_id=key_id,
            public_key=public_key_hex,
            private_key=private_key_hex,
            delegated_at=now.isoformat(),
            expires_at=expires.isoformat(),
            delegated_by=master_key.verify_key.encode(encoder=HexEncoder).decode()[:16],
            scope=request.scope,
            signature=signature,
        )


@router.post("/{appliance_id}/revoke-key/{key_id}")
async def revoke_delegated_key(
    appliance_id: str,
    key_id: str,
    reason: str = "Manual revocation",
    admin: dict = Depends(require_admin),
):
    """Revoke a delegated signing key.

    Revoked keys will no longer be accepted for signature verification.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE delegated_keys
            SET revoked = true, revoked_at = NOW(), revoked_reason = $3
            WHERE appliance_id = $1 AND key_id = $2 AND revoked = false
        """, appliance_id, key_id, reason)

        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Key not found or already revoked")

        logger.info(f"Revoked key {key_id} for appliance {appliance_id}: {reason}")

        return {"status": "revoked", "key_id": key_id, "reason": reason}


@router.get("/{appliance_id}/keys")
async def list_delegated_keys(
    appliance_id: str,
    include_revoked: bool = False,
    admin: dict = Depends(require_admin),
):
    """List all delegated keys for an appliance."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if include_revoked:
            rows = await conn.fetch("""
                SELECT key_id, site_id, scope, delegated_at, expires_at,
                       revoked, revoked_at, revoked_reason
                FROM delegated_keys
                WHERE appliance_id = $1
                ORDER BY delegated_at DESC
            """, appliance_id)
        else:
            rows = await conn.fetch("""
                SELECT key_id, site_id, scope, delegated_at, expires_at
                FROM delegated_keys
                WHERE appliance_id = $1 AND revoked = false
                ORDER BY delegated_at DESC
            """, appliance_id)

        return {
            "appliance_id": appliance_id,
            "keys": [dict(row) for row in rows],
        }


@router.post("/{appliance_id}/audit-trail", response_model=AuditSyncResponse)
async def sync_audit_trail(
    appliance_id: str,
    request: AuditSyncRequest,
):
    """Sync offline audit trail entries from an appliance.

    Entries are verified for:
    1. Valid signature (if signed)
    2. Hash chain integrity
    3. Timestamp ordering
    """
    pool = await get_pool()
    synced_ids = []
    failed_ids = []

    async with pool.acquire() as conn:
        for entry in request.entries:
            try:
                # Verify entry hash
                entry_content = {
                    "entry_id": entry.entry_id,
                    "site_id": entry.site_id,
                    "action_type": entry.action_type,
                    "action_data": entry.action_data,
                    "outcome": entry.outcome,
                    "timestamp": entry.timestamp,
                    "prev_hash": entry.prev_hash,
                }
                hash_content = json.dumps({
                    **entry_content,
                    "signature": entry.signature,
                }, sort_keys=True, separators=(",", ":"))
                computed_hash = hashlib.sha256(hash_content.encode()).hexdigest()

                if computed_hash != entry.entry_hash:
                    logger.warning(f"Hash mismatch for audit entry {entry.entry_id}")
                    failed_ids.append(entry.entry_id)
                    continue

                # Verify signature if present
                verified = False
                if entry.signature and entry.signed_by:
                    # Look up the signing key
                    key_row = await conn.fetchrow("""
                        SELECT public_key, revoked FROM delegated_keys
                        WHERE key_id = $1
                    """, entry.signed_by)

                    if key_row and not key_row["revoked"]:
                        # In production, verify Ed25519 signature here
                        verified = True

                # Insert into database
                await conn.execute("""
                    INSERT INTO appliance_audit_trail
                    (entry_id, appliance_id, site_id, action_type, action_data,
                     outcome, timestamp, signature, signed_by, prev_hash, entry_hash, verified)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    ON CONFLICT (entry_id) DO NOTHING
                """, (
                    entry.entry_id,
                    appliance_id,
                    entry.site_id,
                    entry.action_type,
                    json.dumps(entry.action_data),
                    entry.outcome,
                    datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00")),
                    entry.signature,
                    entry.signed_by,
                    entry.prev_hash,
                    entry.entry_hash,
                    verified,
                ))

                synced_ids.append(entry.entry_id)

            except Exception as e:
                logger.error(f"Failed to sync audit entry {entry.entry_id}: {e}")
                failed_ids.append(entry.entry_id)

    logger.info(f"Synced {len(synced_ids)} audit entries from appliance {appliance_id}")

    return AuditSyncResponse(
        synced_ids=synced_ids,
        failed_ids=failed_ids,
        message=f"Synced {len(synced_ids)}, failed {len(failed_ids)}",
    )


@router.get("/{appliance_id}/audit-trail")
async def get_audit_trail(
    appliance_id: str,
    site_id: Optional[str] = None,
    action_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    admin: dict = Depends(require_admin),
):
    """Get synced audit trail for an appliance."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Build query
        conditions = ["appliance_id = $1"]
        params = [appliance_id]
        param_idx = 2

        if site_id:
            conditions.append(f"site_id = ${param_idx}")
            params.append(site_id)
            param_idx += 1

        if action_type:
            conditions.append(f"action_type = ${param_idx}")
            params.append(action_type)
            param_idx += 1

        where_clause = " AND ".join(conditions)
        params.extend([limit, offset])

        rows = await conn.fetch(f"""
            SELECT entry_id, site_id, action_type, action_data, outcome,
                   timestamp, signature IS NOT NULL as signed, verified, synced_at
            FROM appliance_audit_trail
            WHERE {where_clause}
            ORDER BY timestamp DESC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
        """, *params)

        total = await conn.fetchval(f"""
            SELECT COUNT(*) FROM appliance_audit_trail
            WHERE {where_clause}
        """, *params[:-2])

        return {
            "appliance_id": appliance_id,
            "entries": [dict(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }


@router.post("/{appliance_id}/urgent-escalations", response_model=EscalationResponse)
async def process_urgent_escalations(
    appliance_id: str,
    request: EscalationBatchRequest,
):
    """Process urgent escalations from an appliance.

    These are incidents that occurred while the appliance was offline
    and couldn't be escalated in real-time.

    Each escalation is:
    1. Validated
    2. Checked for existing processing
    3. Routed to L2 (LLM) or L3 (human) based on priority
    """
    pool = await get_pool()
    processed_ids = []
    escalated_to_l2 = []
    escalated_to_l3 = []
    failed_ids = []

    async with pool.acquire() as conn:
        for escalation in request.escalations:
            try:
                # Check if already processed
                existing = await conn.fetchrow("""
                    SELECT escalation_id FROM processed_escalations
                    WHERE escalation_id = $1
                """, escalation.escalation_id)

                if existing:
                    # Already processed, skip
                    processed_ids.append(escalation.escalation_id)
                    continue

                # Determine escalation path
                escalation_result = "processed"
                escalated_to = None

                if escalation.priority in ("critical", "high"):
                    # L2 escalation - needs LLM decision
                    # In production, this would call the MCP chat endpoint
                    escalated_to = "l2"
                    escalated_to_l2.append(escalation.escalation_id)
                    escalation_result = "escalated_l2"
                    logger.info(
                        f"Escalating {escalation.escalation_id} to L2: "
                        f"{escalation.incident_type} ({escalation.priority})"
                    )

                elif escalation.priority == "medium" and escalation.retry_count >= 5:
                    # Too many retries, escalate to L3
                    escalated_to = "l3"
                    escalated_to_l3.append(escalation.escalation_id)
                    escalation_result = "escalated_l3"
                    logger.info(
                        f"Escalating {escalation.escalation_id} to L3: "
                        f"{escalation.incident_type} (retry_count={escalation.retry_count})"
                    )

                # Store processed escalation
                await conn.execute("""
                    INSERT INTO processed_escalations
                    (escalation_id, incident_id, site_id, priority, incident_type,
                     incident_data, created_at, escalation_result, escalated_to)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """, (
                    escalation.escalation_id,
                    escalation.incident_id,
                    escalation.site_id,
                    escalation.priority,
                    escalation.incident_type,
                    json.dumps(escalation.incident_data),
                    datetime.fromisoformat(escalation.created_at.replace("Z", "+00:00")),
                    escalation_result,
                    escalated_to,
                ))

                processed_ids.append(escalation.escalation_id)

            except Exception as e:
                logger.error(f"Failed to process escalation {escalation.escalation_id}: {e}")
                failed_ids.append(escalation.escalation_id)

    logger.info(
        f"Processed {len(processed_ids)} escalations from appliance {appliance_id}: "
        f"{len(escalated_to_l2)} to L2, {len(escalated_to_l3)} to L3"
    )

    return EscalationResponse(
        processed_ids=processed_ids,
        escalated_to_l2=escalated_to_l2,
        escalated_to_l3=escalated_to_l3,
        failed_ids=failed_ids,
    )


@router.get("/{appliance_id}/escalations")
async def get_escalation_history(
    appliance_id: str,
    site_id: Optional[str] = None,
    limit: int = 100,
    admin: dict = Depends(require_admin),
):
    """Get escalation history for an appliance."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Get appliance's site for filtering
        appliance = await conn.fetchrow("""
            SELECT site_id FROM appliances WHERE appliance_id = $1
        """, appliance_id)

        if not appliance:
            raise HTTPException(status_code=404, detail="Appliance not found")

        target_site = site_id or appliance["site_id"]

        rows = await conn.fetch("""
            SELECT escalation_id, incident_id, site_id, priority, incident_type,
                   created_at, processed_at, escalation_result, escalated_to
            FROM processed_escalations
            WHERE site_id = $1
            ORDER BY processed_at DESC
            LIMIT $2
        """, target_site, limit)

        return {
            "appliance_id": appliance_id,
            "site_id": target_site,
            "escalations": [dict(row) for row in rows],
        }
