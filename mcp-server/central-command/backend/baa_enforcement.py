"""BAA-expiry machine-enforcement layer (Task #52, Counsel Priority #1
— Counsel Rule 6: "No legal/BAA state may live only in human memory.
BAA state gates functionality, not just paperwork.").

The v1.0-INTERIM master BAA (`docs/legal/MASTER_BAA_v1.0_INTERIM.md`)
Exhibit C names this mechanism verbatim: a `BAA_GATED_WORKFLOWS`
lockstep constant + CI gate + substrate invariant that enforces
workflow blocks for Covered Entities who have NOT executed the
formal v1.0-INTERIM signature, starting 30 days after the effective
date (cliff: 2026-06-12).

ARCHITECTURE — 3-list lockstep (mirrors the privileged-chain triad):
  List 1  `BAA_GATED_WORKFLOWS`        — the canonical workflow set.
  List 2  the enforcing callsites     — every workflow key passed to
          a recognized enforcement entrypoint in this module.
  List 3  the `sensitive_workflow_advanced_without_baa` sev1 substrate
          invariant in assertions.py — runtime bypass detector.
  CI gate `tests/test_baa_gated_workflows_lockstep.py` asserts List 1
  and List 2 stay in lockstep.

ENFORCEMENT PREDICATE: `baa_status.baa_enforcement_ok()` — checks
formal-signature-exists-for-current-version + not-date-expired. It
DOES NOT require the admin-flipped `client_orgs.baa_on_file` flag
(Gate A P0-2 — reusing `is_baa_on_file_verified()` would block every
org in demo posture the instant this deploys).

SCOPE (Task #52 Gate A, Counsel lens):
  ENFORCED v1   — owner_transfer, cross_org_relocate, evidence_export.
                  Three workflows with clean client_org_id resolution.
  DEFERRED      — see `_DEFERRED_WORKFLOWS`. partner_admin_transfer is
                  a partner-internal role swap with no client_org_id
                  (the enforcement predicate is client-org-scoped);
                  new_site_onboarding / new_credential_entry endpoints
                  were not located in the Gate A review (P1-3). Each
                  carries a named follow-up task. Ingest-blocking is
                  deferred per the Counsel lens to the Task #37 queue
                  (BAA Exhibit C: "pending inside-counsel verdict").

FAIL-CLOSED CARVE-OUTS (Gate A P0-3 + Carol carve-out list — these
MUST NOT be gated, or the org is deadlocked out of the very flow that
would fix the problem):
  - the entire BAA-signing flow + all GET/read endpoints
    ("existing-data access remains unaffected" — BAA Article 8.3);
  - admin-context callers — the platform operator acting, not the CE;
    admin actions are NOT blocked but ARE audit-logged as a bypass so
    the substrate invariant can distinguish them;
  - the auditor-kit's admin + legacy `?token=` branches — an external
    auditor has no org session and is not the party that signs the
    BAA; blocking them is a §164.524 access-right violation.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request

import baa_status

logger = logging.getLogger(__name__)

# Re-export so callers have one import for the version constant.
CURRENT_REQUIRED_BAA_VERSION = baa_status.CURRENT_REQUIRED_BAA_VERSION


# ─────────────────────────────────────────────────────────────────
# List 1 — the canonical gated-workflow set
# ─────────────────────────────────────────────────────────────────
#
# Every member MUST be passed as a string literal to one of the
# recognized enforcement entrypoints below (require_active_baa,
# enforce_or_log_admin_bypass, baa_gate_passes) in at least one
# backend .py. Pinned by tests/test_baa_gated_workflows_lockstep.py.
BAA_GATED_WORKFLOWS = frozenset({
    "owner_transfer",       # client_owner_transfer.py — initiate + ack
    "cross_org_relocate",   # cross_org_site_relocate.py — initiate (admin)
    "evidence_export",      # evidence_chain.py — auditor-kit download
})

# Workflows named in BAA Exhibit C but DEFERRED out of v1, each with a
# reason + a named follow-up task. The lockstep CI gate recognizes
# these and does NOT require an enforcing callsite for them.
_DEFERRED_WORKFLOWS = {
    "partner_admin_transfer": (
        "Partner-internal admin role swap — no client_org_id to resolve; "
        "baa_enforcement_ok() is client-org-scoped. Needs its own Gate A "
        "for the partner BA-subcontractor-agreement predicate. Follow-up "
        "task filed."
    ),
    "new_site_onboarding": (
        "Endpoint not located in the Task #52 Gate A review (P1-3). "
        "Follow-up task filed to locate + wire the site-create mutation."
    ),
    "new_credential_entry": (
        "Endpoint not located in the Task #52 Gate A review (P1-3). "
        "Follow-up task filed to locate + wire the credential-add mutation."
    ),
    "ingest": (
        "Ingest-blocking deferred per the Task #52 Gate A Counsel lens — "
        "BAA Exhibit C says ingest enforcement is 'pending inside-counsel "
        "verdict'; blocking /api/appliances/checkin risks orphaning a "
        "paying customer's appliance fleet mid-window. Routed to the "
        "Task #37 counsel queue."
    ),
}


def assert_workflow_registered(workflow: str) -> None:
    """Raise if `workflow` is neither an active gated workflow nor a
    documented deferred one — a typo'd or unregistered workflow key
    must fail loudly, not silently no-op the gate."""
    if workflow in BAA_GATED_WORKFLOWS:
        return
    if workflow in _DEFERRED_WORKFLOWS:
        raise RuntimeError(
            f"BAA workflow '{workflow}' is DEFERRED, not active — it "
            f"must not be wired to an enforcement entrypoint yet. "
            f"Reason: {_DEFERRED_WORKFLOWS[workflow]}"
        )
    raise RuntimeError(
        f"unregistered BAA-gated workflow: '{workflow}'. Add it to "
        f"BAA_GATED_WORKFLOWS (or _DEFERRED_WORKFLOWS) in "
        f"baa_enforcement.py."
    )


def baa_403_detail(workflow: str) -> Dict[str, str]:
    """The 403 body for a blocked workflow. Generic by design (Gate A
    P1-4 / Counsel Rule 7): it does NOT name the org, and it is
    identical whether the org exists-without-BAA or doesn't exist —
    an unauthenticated prober learns nothing. It points the caller at
    the cure (the signing flow) and reassures that reads are
    unaffected (BAA Article 8.3)."""
    return {
        "error": "BAA_NOT_ON_FILE",
        "message": (
            "This action requires a current signed Business Associate "
            "Agreement. Existing data access is unaffected. Sign at "
            "/portal/baa to continue."
        ),
        "workflow": workflow,
    }


async def baa_gate_passes(
    conn,
    client_org_id: str,
    workflow: str,
) -> bool:
    """Core predicate: does `client_org_id` satisfy the BAA gate for
    `workflow`? Validates the workflow is registered + active, then
    delegates to `baa_status.baa_enforcement_ok()`. Fail-closed:
    a missing org returns FALSE inside `baa_enforcement_ok`.

    This is the single recognized enforcement entrypoint for inline
    checks (the auditor-kit method-aware path uses it directly)."""
    assert_workflow_registered(workflow)
    return await baa_status.baa_enforcement_ok(conn, client_org_id)


async def enforce_or_log_admin_bypass(
    conn,
    client_org_id: Optional[str],
    workflow: str,
    *,
    actor_user_id: Optional[str],
    actor_email: str,
    request: Request,
    target: str,
) -> bool:
    """Admin-context enforcement (Gate A Carol carve-out #3).

    Admin callers are the platform OPERATOR, not the Covered Entity —
    BAA Exhibit C's enforcement is scoped to CE self-service actions,
    so admin actions are NOT blocked. But an admin advancing a
    sensitive workflow for an org with NO active BAA MUST be
    audit-logged as a bypass, so the `sensitive_workflow_advanced_
    without_baa` substrate invariant can distinguish a legitimate
    operator action from an un-gated code-path leak.

    Returns the gate result (TRUE = org has active BAA). Never raises
    for the gate result itself — admin is never blocked. Writes an
    `admin_audit_log` row with action='baa_enforcement_bypass' when
    the gate would have failed.
    """
    assert_workflow_registered(workflow)
    if client_org_id is None:
        # No org to check (e.g. orphan site) — record and proceed.
        ok = False
    else:
        ok = await baa_status.baa_enforcement_ok(conn, client_org_id)
    if not ok:
        try:
            await conn.execute(
                """
                INSERT INTO admin_audit_log (
                    user_id, username, action, target, details, ip_address
                ) VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6)
                """,
                actor_user_id,
                actor_email,
                "baa_enforcement_bypass",
                target,
                json.dumps({
                    "workflow": workflow,
                    "client_org_id": client_org_id,
                    "reason": (
                        "admin advanced a BAA-gated workflow for an org "
                        "with no active BAA — operator carve-out, logged "
                        "for the sensitive_workflow_advanced_without_baa "
                        "substrate invariant"
                    ),
                }),
                request.client.host if request.client else None,
            )
        except Exception:
            # No silent write failures (CLAUDE.md) — but an audit-log
            # failure must NOT block a legitimate admin operation.
            logger.error(
                "baa_enforcement_bypass audit-log write failed for "
                "workflow=%s org=%s",
                workflow, client_org_id, exc_info=True,
            )
    return ok


async def check_baa_for_evidence_export(
    auth: Dict[str, Any],
    site_id: str,
) -> None:
    """Method-aware `evidence_export` gate (Gate A P0-3 + Carol
    carve-out #4) for the auditor-kit download endpoint.

    `require_evidence_view_access` resolves five branches. The BAA
    gate applies to exactly TWO of them:
      - `client_portal` — the Covered Entity's own portal session;
        org_id is on the auth dict. GATED.
      - `partner_portal` — the managing partner's session; the BAA
        belongs to the client_org that owns the site, resolved here.
        GATED.
    It does NOT apply to:
      - `admin` — the platform operator, not the CE (carve-out #3);
      - `portal` / legacy `?token=` — an EXTERNAL AUDITOR with no org
        session. Blocking them because the CE hasn't re-signed would
        itself be a §164.524 individual-access-right violation — the
        BA actively impeding the CE's own HIPAA obligation. Carve-out
        is legally mandatory, not convenience.

    Raises 403 `BAA_NOT_ON_FILE` when a gated caller's org has no
    active BAA. Returns None (proceed) otherwise.
    """
    method = (auth or {}).get("method", "unknown")
    if method not in ("client_portal", "partner_portal"):
        return  # admin / legacy-token / unknown — carved out

    try:
        from .fleet import get_pool
        from .tenant_middleware import admin_transaction
    except ImportError:  # pragma: no cover — package-context fallback
        from fleet import get_pool  # type: ignore
        from tenant_middleware import admin_transaction  # type: ignore

    pool = await get_pool()
    async with admin_transaction(pool) as conn:
        if method == "client_portal":
            org_id = auth.get("org_id")
        else:  # partner_portal — resolve the site's owning client_org
            org_id = await conn.fetchval(
                """
                SELECT s.client_org_id::text
                  FROM sites s
                 WHERE s.site_id = $1
                """,
                site_id,
            )
        if not org_id:
            # No resolvable org — fail-closed for a gated caller.
            raise HTTPException(
                status_code=403, detail=baa_403_detail("evidence_export")
            )
        ok = await baa_gate_passes(conn, str(org_id), "evidence_export")
    if not ok:
        raise HTTPException(
            status_code=403, detail=baa_403_detail("evidence_export")
        )


def require_active_baa(workflow: str):
    """Dependency factory for the CLIENT-OWNER auth context (Gate A —
    the clean-resolution case; currently `owner_transfer`).

    Mirrors `partners.py::require_partner_role`. Stacks on
    `require_client_owner`, resolves `client_org_id` from the client
    session, fail-closed checks `baa_gate_passes()`, and raises 403
    `BAA_NOT_ON_FILE` when the org has no active BAA.

    For non-client-owner contexts (admin, evidence-view), use the
    inline entrypoints `enforce_or_log_admin_bypass()` /
    `baa_gate_passes()` directly — those contexts resolve the org
    differently and (for admin) carve out rather than block.
    """
    assert_workflow_registered(workflow)

    # Imported lazily — client_portal imports a wide surface and an
    # eager import here risks a circular import at module load.
    try:
        from .client_portal import require_client_owner
        from .fleet import get_pool
        from .tenant_middleware import admin_transaction
    except ImportError:  # pragma: no cover — package-context fallback
        from client_portal import require_client_owner  # type: ignore
        from fleet import get_pool  # type: ignore
        from tenant_middleware import admin_transaction  # type: ignore

    async def _check(user: dict = Depends(require_client_owner)) -> dict:
        org_id = str(user["org_id"])
        pool = await get_pool()
        async with admin_transaction(pool) as conn:
            ok = await baa_gate_passes(conn, org_id, workflow)
        if not ok:
            raise HTTPException(status_code=403, detail=baa_403_detail(workflow))
        return user

    return Depends(_check)
