"""ISO release CA endpoints — Week 2 of the composed identity stack.

Two routes:

  GET  /api/iso/ca-bundle.json   public, lists all currently-valid
                                 ISO release CAs (read-only)

  POST /api/provision/claim-v2   ISO-CA-signed first-boot enrollment.
                                 Validates the embedded claim cert,
                                 verifies the CSR signature with the
                                 daemon's freshly-generated pubkey,
                                 inserts a provisioning_claim_events
                                 row, returns a soak-mode api_key for
                                 backward compatibility.

Trust model:

  * `iso_release_ca_pubkeys` (Migration 210) is the registry of
    Ed25519 CAs the build pipeline mints — one per ISO release.
    Public half goes into the table; private half is short-lived
    and consumed by the build pipeline only.
  * Each ISO embeds a claim cert at /etc/installer/claim.cert that
    is a signed JSON document binding the ISO release SHA to its CA
    pubkey. The cert is valid for `valid_days` from issue.
  * At first boot the daemon generates an Ed25519 keypair, builds a
    CSR (the request payload), signs the CSR with its OWN new
    private key, and submits along with the embedded claim cert.
  * Backend validates:
      1. claim cert signature against the registered CA pubkey
         for the embedded `iso_release_sha`
      2. CSR signature against the agent_pubkey supplied in the
         CSR (proves key possession)
      3. iso_release_ca row is non-revoked + within validity window
  * On success: writes provisioning_claim_events (source='claim'),
    upserts site_appliances.agent_public_key, returns api_key for
    soak-mode bearer compatibility.

This route is INTENTIONALLY UNAUTHENTICATED — that's the whole point
of the ISO CA. Authentication is the cert + CSR signature. It does
NOT require an existing site_appliances row; it's the path that
CREATES one.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .fleet import get_pool
from .tenant_middleware import admin_connection
from . import iso_ca_helpers as helpers
from . import identity_chain as chain

logger = logging.getLogger("iso_ca")

router = APIRouter(prefix="/api", tags=["iso-ca"])


# ---------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------


class CABundleEntry(BaseModel):
    iso_release_sha: str = Field(..., min_length=40, max_length=64)
    ca_pubkey_hex: str = Field(..., min_length=64, max_length=64)
    fingerprint: str
    valid_from: str
    valid_until: str


class CABundleResponse(BaseModel):
    """Public bundle. Auditors + customers fetch this to verify any
    claim event independently — `iso_release_sha` + `ca_pubkey_hex`
    is everything needed to verify a claim cert offline."""
    version: int = 1
    fetched_at: str
    cas: list[CABundleEntry]


class ClaimCertPayload(BaseModel):
    iso_release_sha: str = Field(..., min_length=40, max_length=64)
    ca_pubkey_hex: str = Field(..., min_length=64, max_length=64)
    issued_at: str
    valid_until: str
    version: int


class ClaimCert(BaseModel):
    payload: ClaimCertPayload
    signature_b64: str
    algorithm: str = "ed25519"


class ClaimV2Request(BaseModel):
    site_id: str = Field(..., min_length=1, max_length=50)
    mac_address: str = Field(..., min_length=11, max_length=17)
    agent_pubkey_hex: str = Field(..., min_length=64, max_length=64)
    hardware_id: Optional[str] = Field(None, max_length=255)
    nonce: str = Field(..., min_length=32, max_length=32)
    timestamp: str
    claim_cert: ClaimCert
    csr_signature_b64: str


class ClaimV2Response(BaseModel):
    """Returned to the daemon on successful enrollment.

    api_key
        Soak-mode bearer key. Daemons during Weeks 2-4 still send
        the bearer alongside the new signature headers so legacy
        backend code paths keep working. Week 5+ the api_key field
        is dropped.
    appliance_id
        Server-derived stable identifier for this appliance.
    fingerprint
        16-hex-char fingerprint of the registered pubkey. Daemons
        cross-check this against their local fingerprint to detect
        registration mistakes.
    claim_event_id
        Numeric id of the provisioning_claim_events row written.
    """
    appliance_id: str
    fingerprint: str
    claim_event_id: int
    api_key: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _validate_claim_cert(conn, cert: ClaimCert) -> helpers.CAValidation:
    """Look up the CA pubkey for the cert's iso_release_sha and run
    the pure-function validator. Window + revocation checked here
    (DB-side); signature checked in helpers."""
    row = await conn.fetchrow(
        """
        SELECT ca_pubkey_hex, valid_from, valid_until, revoked_at
          FROM iso_release_ca_pubkeys
         WHERE iso_release_sha = $1
        """,
        cert.payload.iso_release_sha,
    )
    if row is None:
        return helpers.CAValidation(False, "unknown_iso_release", cert.payload.iso_release_sha)
    if row["revoked_at"] is not None:
        return helpers.CAValidation(False, "ca_revoked", str(row["revoked_at"]))
    now = datetime.now(timezone.utc)
    if not (row["valid_from"] <= now <= row["valid_until"]):
        return helpers.CAValidation(False, "ca_outside_validity_window",
                                    f"now={now}, range={row['valid_from']}→{row['valid_until']}")
    return helpers.validate_cert_signature(
        cert_payload=cert.payload.model_dump(),
        cert_signature_b64=cert.signature_b64,
        expected_ca_pubkey_hex=row["ca_pubkey_hex"],
    )


def _verify_csr_signature(req: ClaimV2Request) -> bool:
    return helpers.verify_csr_signature(
        site_id=req.site_id,
        mac_address=req.mac_address,
        agent_pubkey_hex=req.agent_pubkey_hex,
        hardware_id=req.hardware_id,
        nonce=req.nonce,
        timestamp=req.timestamp,
        claim_cert_payload=req.claim_cert.payload.model_dump(),
        csr_signature_b64=req.csr_signature_b64,
    )


def _fingerprint(pub_hex: str) -> str:
    return helpers.fingerprint(pub_hex)


async def _resolve_chain_tip(conn, site_id: str) -> str:
    """Return the most recent chain_hash for `site_id` across BOTH
    compliance_bundles and provisioning_claim_events. The two tables
    share one cryptographic chain — auditors verify them as one
    sequence — so the tip is whichever has the latest insertion
    timestamp. GENESIS if neither table has a row yet.
    """
    row = await conn.fetchrow(
        """
        SELECT chain_hash FROM (
            SELECT chain_hash, created_at AS ts
              FROM compliance_bundles
             WHERE site_id = $1 AND chain_hash IS NOT NULL
            UNION ALL
            SELECT chain_hash, claimed_at AS ts
              FROM provisioning_claim_events
             WHERE site_id = $1 AND chain_hash IS NOT NULL
        ) AS combined
        ORDER BY ts DESC
        LIMIT 1
        """,
        site_id,
    )
    if row and row["chain_hash"]:
        return row["chain_hash"]
    return chain.GENESIS_HASH


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/iso/ca-bundle.json", response_model=CABundleResponse)
async def get_iso_ca_bundle() -> CABundleResponse:
    """Public endpoint listing all currently-valid ISO release CAs.

    Customers + auditors fetch this to verify a claim cert against
    the same registered pubkey the backend uses. If we ever silently
    rotated a CA without publishing it, a customer that fetched the
    bundle before the rotation would see the discrepancy.

    Read-only and unauthenticated by design — the data isn't a secret."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        rows = await conn.fetch(
            """
            SELECT iso_release_sha, ca_pubkey_hex, valid_from, valid_until
              FROM iso_release_ca_pubkeys
             WHERE revoked_at IS NULL
               AND valid_until > NOW()
             ORDER BY valid_from DESC
            """
        )
    cas = [
        CABundleEntry(
            iso_release_sha=r["iso_release_sha"],
            ca_pubkey_hex=r["ca_pubkey_hex"],
            fingerprint=_fingerprint(r["ca_pubkey_hex"]),
            valid_from=r["valid_from"].isoformat(),
            valid_until=r["valid_until"].isoformat(),
        )
        for r in rows
    ]
    return CABundleResponse(
        version=1,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        cas=cas,
    )


@router.post("/provision/claim-v2", response_model=ClaimV2Response)
async def claim_v2(req: ClaimV2Request, request: Request) -> ClaimV2Response:
    """ISO-CA-signed first-boot enrollment.

    On success this writes a `claim` row to provisioning_claim_events,
    upserts site_appliances.agent_public_key, and returns a freshly-
    minted api_key (the trigger from Migration 209 deactivates any
    prior active key for this appliance automatically).

    All failures return 401 with a machine-parsable code so the
    daemon can decide whether to back off, retry, or surface to the
    operator. We log liberally because this is an unauthenticated
    public endpoint and an audit trail is the only rate-limiting we
    have during soak."""

    pool = await get_pool()
    mac_norm = req.mac_address.upper()
    appliance_id = f"{req.site_id}-{mac_norm}"
    client_ip = request.client.host if request.client else None

    async with admin_connection(pool) as conn:
        # 1. Validate the claim cert against the registered CA.
        validation = await _validate_claim_cert(conn, req.claim_cert)
        if not validation.ok:
            logger.warning(
                "claim-v2 cert validation failed",
                extra={
                    "reason": validation.reason,
                    "detail": validation.detail,
                    "site_id": req.site_id,
                    "mac": mac_norm,
                    "client_ip": client_ip,
                },
            )
            raise HTTPException(
                status_code=401,
                detail={"error": "claim cert invalid", "code": validation.reason},
            )

        # 2. Verify the CSR signature (proves daemon holds the private
        #    half of agent_pubkey_hex).
        if not _verify_csr_signature(req):
            logger.warning(
                "claim-v2 CSR signature failed",
                extra={"site_id": req.site_id, "mac": mac_norm, "client_ip": client_ip},
            )
            raise HTTPException(
                status_code=401,
                detail={"error": "csr signature invalid", "code": "bad_csr_signature"},
            )

        # 3. Compute fingerprint + insert claim event. Note: the
        #    Migration 210 BEFORE-INSERT trigger does NOT exist —
        #    only DELETE/UPDATE are guarded — so a plain INSERT here
        #    is correct.
        fp = _fingerprint(req.agent_pubkey_hex)
        claim_event = await conn.fetchrow(
            """
            INSERT INTO provisioning_claim_events (
                site_id, mac_address,
                agent_pubkey_hex, agent_pubkey_fingerprint,
                iso_build_sha, hardware_id,
                claim_signature_b64, claimed_at,
                source, notes
            ) VALUES (
                $1, $2,
                $3, $4,
                $5, $6,
                $7, NOW(),
                'claim', $8::jsonb
            )
            RETURNING id, claimed_at
            """,
            req.site_id,
            mac_norm,
            req.agent_pubkey_hex.lower(),
            fp,
            req.claim_cert.payload.iso_release_sha,
            req.hardware_id,
            req.csr_signature_b64,
            json.dumps({
                "client_ip": client_ip,
                "request_timestamp": req.timestamp,
                "request_nonce": req.nonce,
                "ca_pubkey_fingerprint": _fingerprint(req.claim_cert.payload.ca_pubkey_hex),
            }),
        )
        claim_event_id = claim_event["id"]
        claimed_at = claim_event["claimed_at"]

        # 3b. Week 3 — extend the joined hash chain. Resolve the
        # current tip (across compliance_bundles + provisioning_claim_events
        # for this site), compute new chain_hash, and UPDATE the row
        # we just inserted. The Migration 210 immutability trigger
        # explicitly permits chain_prev_hash + chain_hash to be set
        # post-INSERT.
        prev_hash = await _resolve_chain_tip(conn, req.site_id)
        event_canonical = chain.canonical_event_bytes(
            claim_event_id=claim_event_id,
            site_id=req.site_id,
            mac_address=mac_norm,
            agent_pubkey_hex=req.agent_pubkey_hex.lower(),
            iso_release_sha=req.claim_cert.payload.iso_release_sha,
            claimed_at_iso=claimed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            source="claim",
        )
        new_hash = chain.chain_hash(prev_hash, event_canonical)
        await conn.execute(
            """
            UPDATE provisioning_claim_events
               SET chain_prev_hash = $1, chain_hash = $2
             WHERE id = $3
            """,
            prev_hash, new_hash, claim_event_id,
        )

        # 4. Upsert site_appliances row + register the pubkey. This
        #    intentionally happens AFTER the claim event so the
        #    audit trail captures the binding before any operational
        #    state lands.
        await conn.execute(
            """
            INSERT INTO site_appliances (
                site_id, appliance_id, mac_address, hostname,
                agent_public_key, status, first_checkin, created_at
            ) VALUES (
                $1, $2, $3, COALESCE(NULLIF($4, ''), 'pending'),
                $5, 'pending', NULL, NOW()
            )
            ON CONFLICT (appliance_id) DO UPDATE
            SET agent_public_key = EXCLUDED.agent_public_key,
                mac_address      = EXCLUDED.mac_address
            """,
            req.site_id,
            appliance_id,
            mac_norm,
            "",  # hostname populated by checkin, not by claim
            req.agent_pubkey_hex.lower(),
        )

        # 5. Mint a soak-mode api_key. Migration 209's BEFORE-INSERT
        #    trigger auto-deactivates any prior active key for this
        #    (site_id, appliance_id), so there's never more than one
        #    active row.
        raw_api_key = secrets.token_urlsafe(32)
        api_key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()
        await conn.execute(
            """
            INSERT INTO api_keys (
                site_id, appliance_id, key_hash, key_prefix, description, active, created_at
            ) VALUES ($1, $2, $3, $4, 'Auto-generated during claim-v2', true, NOW())
            """,
            req.site_id,
            appliance_id,
            api_key_hash,
            raw_api_key[:8],
        )

        logger.info(
            "claim-v2 success",
            extra={
                "site_id": req.site_id,
                "appliance_id": appliance_id,
                "fingerprint": fp,
                "claim_event_id": claim_event_id,
                "iso_release_sha": req.claim_cert.payload.iso_release_sha,
                "client_ip": client_ip,
            },
        )

    return ClaimV2Response(
        appliance_id=appliance_id,
        fingerprint=fp,
        claim_event_id=claim_event_id,
        api_key=raw_api_key,
    )
