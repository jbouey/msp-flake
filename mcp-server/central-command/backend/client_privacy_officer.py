"""F2 — Privacy Officer designation (round-table 2026-05-06).

Closes Janet-the-office-manager's customer-round-table finding:

> "Am I 'Privacy Officer'? Nobody told me that. If you're going to
> print my name on a federal-looking document I need a checkbox at
> signup that says 'Janet Walsh accepts Privacy Officer designation,
> here's the 2-paragraph explainer of what that means.' Don't just
> scrape the field from our account."

The Compliance Attestation Letter (F1) pulls the Privacy Officer name
from a SIGNED ACCEPTANCE attestation row, not a profile field. The
acceptance event writes a chain-anchored Ed25519 attestation bundle
(``client_org_privacy_officer_designated``); revocation writes
``client_org_privacy_officer_revoked``. §164.308(a)(2) — identify the
security official — is satisfied by the dated, accepted, attested,
audit-trailed designation.

Three operations:
    designate(...)  — create new designation; revokes any prior active.
    revoke(...)     — explicit revocation without replacement (rare;
                       reserved for compliance officer departure with
                       no immediate successor — Letter then refuses to
                       render until a new designation is in place).
    get_current(...) — return the active designation row, or None.

Carol-approved contract: F1's letter render call MUST refuse if
get_current() returns None. The signature line is fiction without a
live designation.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import asyncpg

try:
    from .chain_attestation import resolve_client_anchor_site_id
    from .privileged_access_attestation import (
        create_privileged_access_attestation,
    )
except ImportError:  # pytest path
    from chain_attestation import resolve_client_anchor_site_id  # type: ignore
    from privileged_access_attestation import (  # type: ignore
        create_privileged_access_attestation,
    )

logger = logging.getLogger(__name__)


# Bumped by EVERY future revision of the §164.308(a)(2) explainer
# text shown on the designation wizard. Old acceptances retain
# their original explainer_version; never rewritten. F1's verify
# endpoint surfaces this so an OCR investigator can see WHICH
# explainer the designee accepted.
EXPLAINER_VERSION = "v1-2026-05-06"


class PrivacyOfficerError(Exception):
    """Raised on any precondition violation. Callers should map to
    HTTPException with 4xx in the API layer."""


async def get_current(
    conn: asyncpg.Connection, client_org_id: str
) -> Optional[Dict[str, Any]]:
    """Return the currently-active Privacy Officer designation, or
    None if the org has not designated one yet (or revoked the prior
    designation without replacement).

    Used by F1 attestation-letter renderer to (a) gate rendering and
    (b) populate the named-Privacy-Officer sentence Maria asked for.
    """
    row = await conn.fetchrow(
        """
        SELECT id, client_org_id, name, title, email,
               accepted_at, accepting_user_id, accepting_user_email,
               explainer_version, attestation_bundle_id
          FROM privacy_officer_designations
         WHERE client_org_id = $1
           AND revoked_at IS NULL
         LIMIT 1
        """,
        client_org_id,
    )
    return dict(row) if row else None


async def designate(
    conn: asyncpg.Connection,
    client_org_id: str,
    name: str,
    title: str,
    email: str,
    accepting_user_id: str,
    accepting_user_email: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    acceptance_acknowledgement: str = "",
) -> Dict[str, Any]:
    """Create a new Privacy Officer designation.

    Replaces any existing active designation atomically (revoke + new).
    The acceptance writes a chain-anchored Ed25519 attestation bundle
    (``client_org_privacy_officer_designated``).

    The caller MUST be a client-portal user with role='owner' for the
    org. Lower roles (admin/viewer/billing) cannot designate — this
    is the §164.308(a)(2) ownership decision.

    `acceptance_acknowledgement` is the verbatim explainer text the
    designee accepted. Stored implicitly via `explainer_version`
    (the explainer text itself lives in version-controlled source);
    a non-empty acknowledgement string is required as a smoke check
    that the wizard actually displayed the explainer.

    Returns dict with the new designation row.
    """
    # Sanity / round-table customer-bar enforcement.
    if not name or not name.strip():
        raise PrivacyOfficerError("Privacy Officer name is required")
    if not title or not title.strip():
        raise PrivacyOfficerError("Privacy Officer title is required")
    if not email or "@" not in email:
        raise PrivacyOfficerError(
            "Privacy Officer email is required (must be a valid email)"
        )
    if not accepting_user_email or "@" not in accepting_user_email:
        raise PrivacyOfficerError(
            "accepting_user_email required (the user clicking 'I accept')"
        )
    if len(acceptance_acknowledgement.strip()) < 50:
        raise PrivacyOfficerError(
            "acceptance_acknowledgement required — the wizard must "
            "display the §164.308(a)(2) explainer text and the user "
            "must click through. Shorter than 50 chars indicates a "
            "client bypassing the wizard."
        )

    name = name.strip()
    title = title.strip()
    email = email.strip().lower()
    accepting_user_email = accepting_user_email.strip().lower()

    anchor_site_id = await resolve_client_anchor_site_id(conn, client_org_id)

    async with conn.transaction():
        # Revoke any existing active designation. Replacement, not
        # silent overwrite — preserves audit trail.
        existing = await get_current(conn, client_org_id)
        if existing is not None:
            await conn.execute(
                """
                UPDATE privacy_officer_designations
                   SET revoked_at = NOW(),
                       revoked_by_user_id = $2,
                       revoked_by_email = $3,
                       revoked_reason = $4
                 WHERE id = $1
                """,
                existing["id"],
                accepting_user_id,
                accepting_user_email,
                (
                    "Replaced by new designation: "
                    f"{name} <{email}> (title: {title}). Prior designee: "
                    f"{existing['name']} <{existing['email']}>."
                ),
            )

        # Insert new (active) designation.
        new_row = await conn.fetchrow(
            """
            INSERT INTO privacy_officer_designations (
                client_org_id, name, title, email,
                accepting_user_id, accepting_user_email,
                ip_address, user_agent, explainer_version
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::inet, $8, $9)
            RETURNING id, client_org_id, name, title, email,
                      accepted_at, accepting_user_id,
                      accepting_user_email, explainer_version
            """,
            client_org_id,
            name,
            title,
            email,
            accepting_user_id,
            accepting_user_email,
            ip_address,
            user_agent,
            EXPLAINER_VERSION,
        )
        new_id = str(new_row["id"])

        # Chain-anchored Ed25519 attestation. Reason field carries
        # both the designee identity AND the acceptance posture for
        # the auditor-readable record. ≥20 chars is enforced by the
        # underlying helper.
        attestation_reason = (
            f"Privacy Officer designated: {name} <{email}> (title: "
            f"{title}); accepted by {accepting_user_email} per "
            f"§164.308(a)(2); explainer {EXPLAINER_VERSION}; "
            f"designation_id {new_id}."
        )
        attestation = await create_privileged_access_attestation(
            conn=conn,
            site_id=anchor_site_id or f"client_org:{client_org_id}",
            event_type="client_org_privacy_officer_designated",
            actor_email=accepting_user_email,
            reason=attestation_reason,
            origin_ip=ip_address,
        )
        bundle_id = attestation.get("bundle_id")

        # Link the designation row back to the attestation bundle.
        await conn.execute(
            """
            UPDATE privacy_officer_designations
               SET attestation_bundle_id = $2::uuid
             WHERE id = $1
            """,
            new_id,
            bundle_id,
        )

    logger.info(
        "privacy_officer_designated",
        extra={
            "client_org_id": str(client_org_id),
            "designation_id": new_id,
            "designee_name": name,
            "designee_email": email,
            "accepting_user_email": accepting_user_email,
            "explainer_version": EXPLAINER_VERSION,
            "anchor_site_id": anchor_site_id,
            "attestation_bundle_id": str(bundle_id) if bundle_id else None,
            "replaced_prior_designation_id": (
                str(existing["id"]) if existing else None
            ),
        },
    )

    return dict(new_row) | {"attestation_bundle_id": bundle_id}


async def revoke(
    conn: asyncpg.Connection,
    client_org_id: str,
    revoking_user_id: str,
    revoking_user_email: str,
    reason: str,
) -> Optional[Dict[str, Any]]:
    """Revoke the current Privacy Officer designation WITHOUT
    replacement. Writes ``client_org_privacy_officer_revoked`` to
    the chain.

    F1's attestation-letter render path will REFUSE to render once
    revocation is in effect (no current designation = no signature
    line = no letter). The customer is forced to designate a new
    Privacy Officer to resume document generation. This is the
    Carol-approved contract: never print a stale signature.
    """
    if not reason or len(reason.strip()) < 20:
        raise PrivacyOfficerError(
            "Revocation reason required (≥20 chars; describe the "
            "transition or the reason no successor is named yet)"
        )
    if not revoking_user_email or "@" not in revoking_user_email:
        raise PrivacyOfficerError("revoking_user_email required")

    revoking_user_email = revoking_user_email.strip().lower()
    reason = reason.strip()

    anchor_site_id = await resolve_client_anchor_site_id(conn, client_org_id)

    async with conn.transaction():
        existing = await get_current(conn, client_org_id)
        if existing is None:
            return None  # Idempotent: no-op when nothing to revoke.

        attestation = await create_privileged_access_attestation(
            conn=conn,
            site_id=anchor_site_id or f"client_org:{client_org_id}",
            event_type="client_org_privacy_officer_revoked",
            actor_email=revoking_user_email,
            reason=(
                f"Privacy Officer revoked: {existing['name']} "
                f"<{existing['email']}> (designation_id "
                f"{existing['id']}); revocation reason: {reason}"
            ),
        )
        bundle_id = attestation.get("bundle_id")

        await conn.execute(
            """
            UPDATE privacy_officer_designations
               SET revoked_at = NOW(),
                   revoked_by_user_id = $2,
                   revoked_by_email = $3,
                   revoked_reason = $4,
                   revoked_attestation_bundle_id = $5::uuid
             WHERE id = $1
            """,
            existing["id"],
            revoking_user_id,
            revoking_user_email,
            reason,
            bundle_id,
        )

    logger.info(
        "privacy_officer_revoked",
        extra={
            "client_org_id": str(client_org_id),
            "designation_id": str(existing["id"]),
            "revoking_user_email": revoking_user_email,
            "anchor_site_id": anchor_site_id,
            "attestation_bundle_id": str(bundle_id) if bundle_id else None,
        },
    )
    return dict(existing) | {
        "revoked_at": datetime.now(timezone.utc),
        "revoked_attestation_bundle_id": bundle_id,
    }
