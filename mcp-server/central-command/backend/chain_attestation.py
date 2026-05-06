"""Shared chain-attestation primitives.

Round-table 32 (2026-05-05) closure of Maya P2 from the Session 217
final sweep. Pre-fix, 5 backend modules each carried near-identical
implementations of:
  - Ed25519 attestation emission with anchor-namespace resolution
  - Operator-alert hook with chain-gap escalation pattern
  - Client-org-to-anchor-site_id resolver (with synthetic fallback)

Any change to the chain-gap rule (severity ladder, attestation-missing
subject suffix, etc.) had to be made in 5 places. This module is now
the single source of truth.

Anti-regression CI gate: `tests/test_chain_attestation_no_inline_duplicates.py`
fails if any new module re-introduces inline duplicates of the helpers.

Public API (callers should use ONLY these — no inline shims):

    resolve_client_anchor_site_id(conn, org_id) -> str
    emit_privileged_attestation(conn, **kwargs) -> tuple[bool, Optional[str]]
    send_chain_aware_operator_alert(**kwargs) -> None
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

from .privileged_access_attestation import (
    PrivilegedAccessAttestationError,
    create_privileged_access_attestation,
)

logger = logging.getLogger(__name__)


async def resolve_client_anchor_site_id(
    conn: asyncpg.Connection, org_id: str,
) -> str:
    """Resolve client_org events to an anchor site_id.

    The anchor-namespace convention (CLAUDE.md inviolable rule from
    Session 216): client-org events anchor at the org's primary site
    (oldest by created_at). When no sites exist yet, fall back to the
    synthetic `client_org:<id>` namespace so the chain has SOME anchor.

    Auditor kits walk client-org chains by either the real site_id or
    the synthetic prefix; both shapes are valid.
    """
    row = await conn.fetchrow(
        """
        SELECT site_id FROM sites
         WHERE client_org_id = $1::uuid
         ORDER BY created_at ASC LIMIT 1
        """,
        org_id,
    )
    return row["site_id"] if row else f"client_org:{org_id}"


async def emit_privileged_attestation(
    conn: asyncpg.Connection,
    *,
    anchor_site_id: str,
    event_type: str,
    actor_email: str,
    reason: str,
    approvals: Optional[List[Dict[str, Any]]] = None,
    origin_ip: Optional[str] = None,
    duration_minutes: Optional[int] = None,
    target_user_id: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """Best-effort Ed25519 attestation. Returns ``(failed, bundle_id)``.

    The tuple-return is more precise than the legacy ``bundle_id is None``
    pattern: callers can distinguish "attestation succeeded but bundle_id
    happened to be None" from "attestation actually failed". Downstream
    operator-alert hooks should pass `failed` directly into
    `send_chain_aware_operator_alert(attestation_failed=...)`.

    `approvals` defaults to a single-stage entry derived from the
    event_type if not provided. For state-machine events with multiple
    stages (owner-transfer current_ack → target_accept), pass the full
    approvals list explicitly.

    Logs at ERROR on every failure path — chain-gap signal MUST be
    visible to operations even when the operator-alert path is also
    broken.
    """
    if approvals is None:
        approvals = [{
            "stage": event_type.split("_")[-1],
            "actor": actor_email,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]
        if target_user_id is not None:
            approvals[0]["target_user_id"] = target_user_id

    try:
        att = await create_privileged_access_attestation(
            conn,
            site_id=anchor_site_id,
            event_type=event_type,
            actor_email=actor_email,
            reason=reason,
            origin_ip=origin_ip,
            duration_minutes=duration_minutes,
            approvals=approvals,
        )
        return False, att.get("bundle_id")
    except PrivilegedAccessAttestationError:
        logger.error(
            "chain_attestation_failed",
            exc_info=True,
            extra={
                "event_type": event_type,
                "actor_email": actor_email,
                "anchor_site_id": anchor_site_id,
            },
        )
        return True, None
    except Exception:
        logger.error(
            "chain_attestation_unexpected",
            exc_info=True,
            extra={
                "event_type": event_type,
                "actor_email": actor_email,
                "anchor_site_id": anchor_site_id,
            },
        )
        return True, None


def send_chain_aware_operator_alert(
    *,
    event_type: str,
    severity: str,
    summary: str,
    details: Dict[str, Any],
    actor_email: Optional[str],
    site_id: str,
    attestation_failed: bool,
) -> None:
    """Operator-visibility alert with the chain-gap escalation pattern.

    CLAUDE.md inviolable rule (Session 216): if the upstream attestation
    failed, severity escalates to ``P0-CHAIN-GAP`` and the subject is
    suffixed with ``[ATTESTATION-MISSING]``. The `attestation_failed`
    flag flows into the details payload so auditor-kit consumers can
    cross-walk a missing-attestation event to the operator alert.

    Best-effort. Failures dispatching the email are logged at ERROR but
    don't propagate to the caller — the rest of the response path
    should not be blocked by mail-server hiccups.
    """
    try:
        from .email_alerts import send_operator_alert
        if attestation_failed:
            severity = "P0-CHAIN-GAP"
            summary = f"{summary} [ATTESTATION-MISSING]"
        details = {**details, "attestation_failed": attestation_failed}
        send_operator_alert(
            event_type=event_type,
            severity=severity,
            summary=summary,
            details=details,
            site_id=site_id,
            actor_email=actor_email,
        )
    except Exception:
        logger.error(
            "operator_alert_dispatch_failed",
            exc_info=True,
            extra={"event_type": event_type, "site_id": site_id},
        )
