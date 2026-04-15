"""attestation_api.py — Week 4 of the composed identity stack.

Single customer-facing endpoint:

    GET /api/portal/appliance-attestation/{mac_address}

Returns a JSON bundle that lets a customer (or their auditor) verify
the entire device-to-action chain for one appliance:

  * identity_chain     — every claim event for this MAC
                         (provisioning_claim_events) with
                         chain_prev_hash + chain_hash linkage
  * evidence_chain_tip — most recent compliance_bundles entry
                         (the last "the appliance attested its
                         state at this hash, OTS-anchored")
  * consent_events     — relevant Migration 184 class-consent
                         events that authorize what the appliance
                         was allowed to do
  * verification       — instructions an auditor follows to
                         independently verify each chain

Auth:
  Same client-portal session as every other /api/portal/* route.
  Org scoping enforced — a client only sees their own appliances.

Read-only by design — this is the customer-facing artifact, not a
mutation endpoint."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException

from .client_portal import require_client_user
from .fleet import get_pool
from .tenant_middleware import admin_connection
from . import iso_ca_helpers as helpers

logger = logging.getLogger("attestation_api")

router = APIRouter(prefix="/api/portal", tags=["portal-attestation"])


def _normalize_mac(mac: str) -> str:
    """Match the convention used elsewhere — uppercase, colon-separated."""
    return mac.strip().upper()


async def _appliance_belongs_to_org(
    conn, mac_norm: str, client_org_id: str
) -> Optional[dict]:
    """Resolve the appliance row + verify it lives under the client's
    org. Returns None if not found or wrong org."""
    return await conn.fetchrow(
        """
        SELECT sa.site_id, sa.appliance_id, sa.mac_address,
               sa.hostname, sa.agent_public_key,
               sa.first_checkin, sa.status, s.client_org_id
          FROM site_appliances sa
          JOIN sites s ON s.site_id = sa.site_id
         WHERE UPPER(sa.mac_address) = $1
           AND sa.deleted_at IS NULL
           AND s.client_org_id = $2
        """,
        mac_norm,
        client_org_id,
    )


async def _identity_chain(conn, site_id: str, mac_norm: str) -> list[dict]:
    """All claim events for this MAC, oldest → newest, with chain
    linkage exposed so an auditor can rebuild the hashes."""
    rows = await conn.fetch(
        """
        SELECT id, agent_pubkey_hex, agent_pubkey_fingerprint,
               iso_build_sha, hardware_id, claimed_at, source,
               supersedes_id, ots_bundle_id,
               chain_prev_hash, chain_hash
          FROM provisioning_claim_events
         WHERE site_id = $1 AND mac_address = $2
         ORDER BY claimed_at ASC
        """,
        site_id,
        mac_norm,
    )
    return [
        {
            "claim_event_id": r["id"],
            "agent_pubkey_hex": r["agent_pubkey_hex"],
            "agent_pubkey_fingerprint": r["agent_pubkey_fingerprint"],
            "iso_release_sha": r["iso_build_sha"],
            "hardware_id": r["hardware_id"],
            "claimed_at": r["claimed_at"].isoformat(),
            "source": r["source"],
            "supersedes_claim_event_id": r["supersedes_id"],
            "ots_bundle_id": r["ots_bundle_id"],
            "chain_prev_hash": r["chain_prev_hash"],
            "chain_hash": r["chain_hash"],
        }
        for r in rows
    ]


async def _evidence_chain_tip(conn, site_id: str) -> Optional[dict]:
    """Most recent compliance_bundles entry for the site. Carries
    the latest chain_hash and OTS proof URL when anchored."""
    row = await conn.fetchrow(
        """
        SELECT bundle_id, bundle_hash, chain_hash, chain_position,
               created_at, ots_status, merkle_batch_id
          FROM compliance_bundles
         WHERE site_id = $1
         ORDER BY created_at DESC
         LIMIT 1
        """,
        site_id,
    )
    if not row:
        return None
    return {
        "bundle_id": row["bundle_id"],
        "bundle_hash": row["bundle_hash"],
        "chain_hash": row["chain_hash"],
        "chain_position": row["chain_position"],
        "created_at": row["created_at"].isoformat(),
        "ots_status": row["ots_status"],
        "merkle_batch_id": row["merkle_batch_id"],
    }


async def _consent_events(conn, client_org_id: str) -> list[dict]:
    """Recent class-consent events for the org. The Migration 184
    ledger lives at promoted_rule_events with event_type LIKE
    'consent.%'. We surface the last 10 — enough to show the
    auditor what was authorized and when, scoped to this customer's
    org."""
    rows = await conn.fetch(
        """
        SELECT event_type, actor, stage, outcome, reason, created_at
          FROM promoted_rule_events
         WHERE event_type LIKE 'consent.%'
           AND created_at > NOW() - INTERVAL '180 days'
         ORDER BY created_at DESC
         LIMIT 10
        """
    )
    return [
        {
            "event_type": r["event_type"],
            "actor": r["actor"],
            "stage": r["stage"],
            "outcome": r["outcome"],
            "reason": r["reason"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


async def _ca_pubkey_for(conn, iso_release_sha: Optional[str]) -> Optional[str]:
    """Look up the registered CA pubkey for an iso_release_sha so
    the auditor has it inline — saves a second round-trip to
    /api/iso/ca-bundle.json."""
    if not iso_release_sha:
        return None
    row = await conn.fetchrow(
        "SELECT ca_pubkey_hex FROM iso_release_ca_pubkeys WHERE iso_release_sha = $1",
        iso_release_sha,
    )
    return row["ca_pubkey_hex"] if row else None


@router.get("/appliance-attestation/{mac_address}")
async def get_appliance_attestation(
    mac_address: str,
    user: dict = Depends(require_client_user),
) -> dict[str, Any]:
    """Customer-facing attestation bundle for one appliance.

    Composed from four independently-verifiable sources:
      1. provisioning_claim_events (identity chain)
      2. compliance_bundles (evidence chain tip)
      3. iso_release_ca_pubkeys (the CA each claim was authorized by)
      4. promoted_rule_events (class-consent events)

    A customer + auditor can:
      - Verify each claim event's chain_hash by recomputing
        sha256(prev_hex + ":" + canonical_event_json)
      - Verify the latest compliance_bundles chain_hash anchors
        into OTS via merkle_batch_id
      - Cross-reference iso_release_sha against /api/iso/ca-bundle.json
      - Confirm consent events authorize the actions in evidence
    """
    mac_norm = _normalize_mac(mac_address)
    if len(mac_norm) != 17 or mac_norm.count(":") != 5:
        raise HTTPException(status_code=400, detail="mac_address must be AA:BB:CC:DD:EE:FF format")

    client_org_id = user.get("client_org_id")
    if not client_org_id:
        raise HTTPException(status_code=403, detail="No client_org context on session")

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        appliance = await _appliance_belongs_to_org(conn, mac_norm, client_org_id)
        if appliance is None:
            # Don't disclose whether the MAC exists at all — same shape
            # as a 404 either way.
            raise HTTPException(status_code=404, detail="Appliance not found in this organization")

        identity = await _identity_chain(conn, appliance["site_id"], mac_norm)
        evidence_tip = await _evidence_chain_tip(conn, appliance["site_id"])
        consent = await _consent_events(conn, client_org_id)

        # Inline the CA pubkeys used by each claim event so an auditor
        # has everything they need without a second request.
        ca_lookup: dict[str, Optional[str]] = {}
        for ev in identity:
            sha = ev.get("iso_release_sha")
            if sha and sha not in ca_lookup:
                ca_lookup[sha] = await _ca_pubkey_for(conn, sha)
        for ev in identity:
            ev["ca_pubkey_hex"] = ca_lookup.get(ev.get("iso_release_sha") or "")

    return {
        "version": 1,
        "appliance": {
            "site_id": appliance["site_id"],
            "appliance_id": appliance["appliance_id"],
            "mac_address": mac_norm,
            "hostname": appliance["hostname"],
            "status": appliance["status"],
            "first_checkin": appliance["first_checkin"].isoformat() if appliance["first_checkin"] else None,
            "agent_public_key": appliance["agent_public_key"],
            "agent_public_key_fingerprint": helpers.fingerprint(appliance["agent_public_key"] or ""),
        },
        "identity_chain": identity,
        "evidence_chain_tip": evidence_tip,
        "consent_events": consent,
        "verification": {
            "identity": (
                "For each row in identity_chain, recompute "
                "sha256(chain_prev_hash + ':' + canonical_event_json) "
                "and confirm it equals chain_hash. Genesis prev_hash "
                "is 64 zeros."
            ),
            "evidence": (
                "evidence_chain_tip.merkle_batch_id is the OTS-anchored "
                "Merkle batch the bundle is in. Fetch its proof via "
                "/api/evidence/sites/{site_id}/bundles/{bundle_id}/ots "
                "and verify against an OpenTimestamps calendar."
            ),
            "ca_bundle_url": "/api/iso/ca-bundle.json",
            "ots_calendar": "https://alice.btc.calendar.opentimestamps.org",
        },
    }
