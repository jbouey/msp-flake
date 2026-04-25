"""Appliance relocation — compliance chain of custody for physical moves.

HIPAA §164.310(d)(1) — "Implement policies and procedures that govern
the receipt and removal of hardware and electronic media that contain
electronic protected health information into and out of a facility."

An appliance moving between subnets (e.g., office A → satellite clinic B)
is a physical-security event. It could be:
  - planned office relocation  (benign, operator can ack)
  - equipment swap             (benign, operator can ack)
  - theft response             (needs incident response)
  - shadow IT                  (needs policy enforcement)
  - tampering                  (security incident)

Silence is the worst answer. The substrate must:
  1. Detect the move automatically (subnet CIDR change)
  2. Write an attested, hash-chained compliance_bundle
  3. Surface it on the H6 customer-visible privileged-action feed
  4. Raise a substrate invariant if not acknowledged within 24h

Two-bundle pattern:

  [1] DETECTION  (auto, system-signed) → compliance_bundles row with
        check_type = 'appliance_relocation'
        signed_by  = 'central-command-server'
        summary    = { kind: 'relocation_detected', from, to }

  [2] ACKNOWLEDGMENT (human, via privileged_access_attestation) →
        compliance_bundles row with
        check_type = 'privileged_access'
        summary.event_type = 'appliance_relocation_acknowledged'
        actor_email, reason
        prev_bundle_id = bundle_id of [1]

Both live in the same per-site evidence chain. Auditor traces:
detection → acknowledgment → reason → actor. If the acknowledgment
never comes, the chain shows the gap + the substrate invariant fires.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger("appliance_relocation")


RFC1918_IGNORE_PREFIXES = ("169.254.", "10.100.", "127.")


def _primary_subnet(ips: List[str]) -> Optional[str]:
    """Return the /24 prefix of the first LAN IPv4 in the list.
    Ignores link-local (169.254), WireGuard (10.100), loopback (127).
    Returns None if no usable IPv4 found."""
    if not ips:
        return None
    for ip in ips:
        if not ip or ":" in ip:  # skip IPv6
            continue
        if any(ip.startswith(p) for p in RFC1918_IGNORE_PREFIXES):
            continue
        parts = ip.split(".")
        if len(parts) == 4:
            return ".".join(parts[:3])
    return None


def _canonical(payload: Dict[str, Any]) -> str:
    """Deterministic JSON for hashing. Matches
    privileged_access_attestation._canonical."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _sign_bundle_hash(bundle_hash_hex: str) -> Optional[str]:
    """Ed25519-sign the bundle_hash using the central-command signing
    backend. Returns hex signature, or None on any failure (relocation
    detection is important enough to write the bundle even if signing
    fails; the substrate_invariant will still fire on missing ack)."""
    try:
        try:
            from .signing_backend import get_signing_backend, SigningBackendError
        except ImportError:
            from signing_backend import get_signing_backend, SigningBackendError  # type: ignore
        signer = get_signing_backend()
        sig_result = signer.sign(bundle_hash_hex.encode("utf-8"))
        return sig_result.signature.hex()
    except Exception as e:  # pragma: no cover
        logger.error("relocation bundle signing failed: %s", e, exc_info=True)
        return None


async def _get_prev_bundle(
    conn: asyncpg.Connection, site_id: str
) -> Optional[Dict[str, Any]]:
    row = await conn.fetchrow(
        "SELECT bundle_id, bundle_hash, chain_position "
        "FROM compliance_bundles "
        "WHERE site_id = $1 "
        "ORDER BY checked_at DESC LIMIT 1",
        site_id,
    )
    return dict(row) if row else None


async def detect_and_record_relocation(
    conn: asyncpg.Connection,
    site_id: str,
    appliance_id: str,
    mac_address: str,
    hostname: Optional[str],
    previous_ips: List[str],
    current_ips: List[str],
) -> Optional[Dict[str, Any]]:
    """Compare previous vs current IPs for this appliance. If the /24
    of the primary LAN IP changed, write an automatic detection
    compliance_bundle + return its metadata.

    Idempotent within the same checkin cycle: if the caller invokes
    this twice for the same move, two detection bundles will be
    written. That's actually fine — each represents an independent
    observation. A future enhancement could dedupe within a short
    window; today we favor the simpler always-write path.

    Safe to call even when `previous_ips` is empty (first checkin) —
    returns None in that case (no "move" to record).
    """
    prev_subnet = _primary_subnet(previous_ips or [])
    curr_subnet = _primary_subnet(current_ips or [])

    if not prev_subnet or not curr_subnet:
        return None
    if prev_subnet == curr_subnet:
        return None

    now = datetime.now(timezone.utc)

    # Build the checks payload — a structured description of the move.
    # This is what the auditor reads + what the H6 customer feed renders.
    check_record = {
        "kind": "appliance_relocation",
        "event": "relocation_detected",
        "site_id": site_id,
        "appliance_id": appliance_id,
        "mac_address": mac_address,
        "hostname": hostname,
        "from_subnet": f"{prev_subnet}.0/24",
        "to_subnet": f"{curr_subnet}.0/24",
        "previous_ips": list(previous_ips or []),
        "current_ips": list(current_ips or []),
        "detected_at": now.isoformat(),
        "detection_method": "subnet_cidr_diff",
    }
    checks_payload = [check_record]
    summary_payload = {
        "event_type": "appliance_relocation_detected",
        "evidence_class": "appliance_relocation",
        "from_subnet": f"{prev_subnet}.0/24",
        "to_subnet": f"{curr_subnet}.0/24",
        "count": 1,
    }

    # Hash-chain linkage (same pattern as privileged_access_attestation).
    prev = await _get_prev_bundle(conn, site_id)
    prev_bundle_id = prev["bundle_id"] if prev else None
    prev_hash = prev["bundle_hash"] if prev else "0" * 64
    chain_position = (prev["chain_position"] + 1) if prev else 0

    canonical = _canonical(
        {
            "site_id": site_id,
            "checked_at": now.isoformat(),
            "check_type": "appliance_relocation",
            "checks": checks_payload,
            "summary": summary_payload,
            "prev_hash": prev_hash,
            "chain_position": chain_position,
        }
    )
    bundle_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    chain_hash = hashlib.sha256(
        (prev_hash + bundle_hash).encode("utf-8")
    ).hexdigest()

    signature_hex = _sign_bundle_hash(bundle_hash) or ""

    bundle_id = f"AR-{now.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"

    try:
        await conn.execute(
            """
            INSERT INTO compliance_bundles (
                site_id, bundle_id, bundle_hash, check_type, check_result,
                checked_at, checks, summary,
                agent_signature, signed_data, signature_valid,
                prev_bundle_id, prev_hash, chain_position, chain_hash,
                signature, signed_by, ots_status
            ) VALUES (
                $1, $2, $3, 'appliance_relocation', 'detected',
                $4, $5::jsonb, $6::jsonb,
                NULL, $7, true,
                $8, $9, $10, $11,
                $12, 'central-command-server', 'batching'
            )
            """,
            site_id, bundle_id, bundle_hash,
            now,
            json.dumps(checks_payload), json.dumps(summary_payload),
            canonical,
            prev_bundle_id, prev_hash, chain_position, chain_hash,
            signature_hex,
        )
    except Exception as e:
        logger.error(
            "relocation bundle write failed site=%s aid=%s err=%s",
            site_id, appliance_id, e, exc_info=True,
        )
        return None

    # Admin audit-log mirror (for the existing UI; authoritative record
    # is the compliance_bundle above).
    try:
        await conn.execute(
            """
            INSERT INTO admin_audit_log (username, action, target, details, created_at)
            VALUES ($1, 'APPLIANCE_RELOCATION_DETECTED', $2, $3::jsonb, NOW())
            """,
            "system",
            f"appliance:{appliance_id}",
            json.dumps(
                {
                    "bundle_id": bundle_id,
                    "bundle_hash": bundle_hash,
                    "chain_position": chain_position,
                    "from_subnet": f"{prev_subnet}.0/24",
                    "to_subnet": f"{curr_subnet}.0/24",
                    "mac_address": mac_address,
                }
            ),
        )
    except Exception as e:
        logger.warning(
            "relocation admin_audit_log mirror failed for %s: %s", bundle_id, e
        )

    logger.warning(
        "appliance_relocation_detected site=%s aid=%s %s → %s bundle=%s",
        site_id, appliance_id, prev_subnet, curr_subnet, bundle_id,
    )
    return {
        "bundle_id": bundle_id,
        "bundle_hash": bundle_hash,
        "chain_position": chain_position,
        "chain_hash": chain_hash,
        "from_subnet": f"{prev_subnet}.0/24",
        "to_subnet": f"{curr_subnet}.0/24",
    }


async def emit_admin_relocation_bundle(
    conn: asyncpg.Connection,
    *,
    source_site_id: str,
    target_site_id: str,
    mac_address: str,
    relocation_id: int,
    actor: str,
    reason: str,
) -> Optional[str]:
    """Admin-initiated sibling of detect_and_record_relocation.

    Round-table RT-7 (Session 210-B 2026-04-25). Every relocation
    issued via POST /api/sites/{site_id}/appliances/{aid}/relocate
    writes a compliance_bundle so the customer's evidence chain
    reflects the move with the same cryptographic footing as auto-
    detected subnet-diff relocations.

    Differences from the auto-detect path:
      - check_result = 'admin_initiated' (vs 'detected') so the H6
        feed can render "operator-initiated" vs "automatically
        detected" with distinct UX
      - summary carries actor + reason + relocation_id pointing at
        relocations.id (the append-only tracker from Migration 245)
      - bundle_id prefix is 'AR-ADMIN-' so the auditor can grep the
        chain by initiation type

    Bundle is hash-chained to the site's prior evidence (same as
    every other compliance_bundles row). Returns the bundle_id on
    success, or None on failure (logged at error). The relocate
    endpoint logs missing bundles loudly so an evidence-chain gap
    is visible without needing to query.

    Notes:
      - Hash-chains under the SOURCE site_id. The source site is
        where the appliance lived before the move; auditor reading
        that site's chain will see the relocation departure.
      - Could also write a "arrival" bundle under the TARGET site,
        but for now a single bundle on the source side is enough
        — auditor can join via relocation_id.
    """
    now = datetime.now(timezone.utc)

    check_record = {
        "kind": "appliance_relocation",
        "event": "admin_initiated_relocation",
        "source_site_id": source_site_id,
        "target_site_id": target_site_id,
        "mac_address": mac_address,
        "relocation_id": relocation_id,
        "actor": actor,
        "reason": reason,
        "initiated_at": now.isoformat(),
        "method": "POST /api/sites/.../relocate",
    }
    checks_payload = [check_record]
    summary_payload = {
        "event_type": "appliance_relocation_admin_initiated",
        "evidence_class": "appliance_relocation",
        "source_site_id": source_site_id,
        "target_site_id": target_site_id,
        "actor": actor,
        "relocation_id": relocation_id,
        "count": 1,
    }

    prev = await _get_prev_bundle(conn, source_site_id)
    prev_bundle_id = prev["bundle_id"] if prev else None
    prev_hash = prev["bundle_hash"] if prev else "0" * 64
    chain_position = (prev["chain_position"] + 1) if prev else 0

    canonical = _canonical(
        {
            "site_id": source_site_id,
            "checked_at": now.isoformat(),
            "check_type": "appliance_relocation",
            "checks": checks_payload,
            "summary": summary_payload,
            "prev_hash": prev_hash,
            "chain_position": chain_position,
        }
    )
    bundle_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    chain_hash = hashlib.sha256(
        (prev_hash + bundle_hash).encode("utf-8")
    ).hexdigest()

    signature_hex = _sign_bundle_hash(bundle_hash) or ""
    bundle_id = f"AR-ADMIN-{now.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"

    try:
        await conn.execute(
            """
            INSERT INTO compliance_bundles (
                site_id, bundle_id, bundle_hash, check_type, check_result,
                checked_at, checks, summary,
                agent_signature, signed_data, signature_valid,
                prev_bundle_id, prev_hash, chain_position, chain_hash,
                signature, signed_by, ots_status
            ) VALUES (
                $1, $2, $3, 'appliance_relocation', 'admin_initiated',
                $4, $5::jsonb, $6::jsonb,
                NULL, $7, true,
                $8, $9, $10, $11,
                $12, 'central-command-server', 'batching'
            )
            """,
            source_site_id, bundle_id, bundle_hash,
            now,
            json.dumps(checks_payload), json.dumps(summary_payload),
            canonical,
            prev_bundle_id, prev_hash, chain_position, chain_hash,
            signature_hex,
        )
    except Exception as e:
        logger.error(
            "admin_relocation_bundle write failed source=%s mac=%s err=%s",
            source_site_id, mac_address, e, exc_info=True,
        )
        return None

    logger.info(
        "appliance_relocation_admin_initiated source=%s target=%s mac=%s actor=%s bundle=%s",
        source_site_id, target_site_id, mac_address, actor, bundle_id,
    )
    return bundle_id
