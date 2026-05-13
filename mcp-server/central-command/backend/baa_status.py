"""Canonical BAA-on-file gate (counsel-grade, Rule 1 + Rule 6).

Counsel-grade rule (2026-05-13, gold authority):

    "BAA on file" claims in customer-facing artifacts (F1 Compliance
    Attestation Letter, P-F6 BA Compliance Letter, audit-report
    readiness checks, client-portal posture displays) MUST gate on a
    FORMAL HIPAA-COMPLETE BAA SIGNATURE, not on the existence of a
    click-through acknowledgment row.

The pre-2026-05-13 platform collected customer signatures on a
5-bullet click-through acknowledgment statement (SignupBaa.tsx
ACKNOWLEDGMENT_TEXT) and stored SHA256 hashes in
`baa_signatures.baa_text_sha256`. Per outside HIPAA counsel's
2026-05-13 review, those rows "likely constitute evidence of intent
and part performance, but are insufficient as a complete HIPAA BAA."

The v1.0-INTERIM master BAA (`docs/legal/MASTER_BAA_v1.0_INTERIM.md`)
is the first HIPAA-complete instrument. Customers re-sign within 30
days of its effective date. Migration 312 added
`baa_signatures.is_acknowledgment_only` to distinguish the two
classes and backfilled `TRUE` for every existing v1.0-2026-04-15
row.

This helper is the SINGLE canonical reader. All 5 callsites that
assert "BAA on file" MUST consume `is_baa_on_file_verified()`. Raw
reads of `client_orgs.baa_on_file` for customer-facing claims are a
Rule 1 (canonical truth) violation — they collapse the distinction
between acknowledgment-of-intent and formal HIPAA BAA.

Gate (both must be TRUE):

  1. `client_orgs.baa_on_file = TRUE`
     (operationally-flipped flag — set by admin after some review
     step; per user 2026-05-13: "we have not flipped anything"
     during testing/demo posture — so this gate is conservative
     today)

  2. At least one `baa_signatures` row exists for the org's
     `primary_email` with `is_acknowledgment_only = FALSE`
     (i.e. customer has signed the v1.0-INTERIM or later formal BAA)

When either gate fails, the helper returns FALSE and the consuming
callsite MUST NOT assert "BAA on file." The customer-facing artifact
should either omit the claim or say "BAA not on file" — whichever is
the artifact's existing not-on-file copy.

Defense-in-depth note: today the platform is in testing/demo
posture so `baa_on_file=FALSE` is the conservative default and no
customer-facing artifact mis-asserts. This helper hardens for the
forward direction: once admins begin flipping `baa_on_file=TRUE`
for real customers, the helper ensures the flip alone is
insufficient — a formal-BAA signature is also required.
"""

from __future__ import annotations

import logging
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)


async def is_baa_on_file_verified(
    conn: asyncpg.Connection,
    client_org_id: str,
) -> bool:
    """Return TRUE iff the org has BOTH the admin-flipped `baa_on_file`
    flag AND at least one formal (non-acknowledgment-only) BAA
    signature for the org's primary email.

    Args:
        conn: asyncpg connection (admin-scoped; this query reads
              client_orgs + baa_signatures, both admin tables).
        client_org_id: UUID of the client_org.

    Returns:
        TRUE  — org has formally-signed BAA on file; customer-facing
                "BAA on file" claims are permitted.
        FALSE — either the admin flag is FALSE, OR the org has only
                click-through acknowledgments and no formal v2.0+
                signature, OR the org doesn't exist.

    This helper does NOT raise on missing org — returns FALSE. The
    intent is fail-closed: if the org is missing or in an unexpected
    state, the conservative answer is "not on file."
    """
    row = await conn.fetchrow(
        """
        SELECT (
            co.baa_on_file = TRUE
            AND EXISTS (
                SELECT 1
                  FROM baa_signatures bs
                 WHERE LOWER(bs.email) = LOWER(co.primary_email)
                   AND bs.is_acknowledgment_only = FALSE
            )
        ) AS verified
          FROM client_orgs co
         WHERE co.id = $1
        """,
        client_org_id,
    )

    if row is None:
        # Org not found — fail-closed.
        logger.warning(
            "is_baa_on_file_verified: client_org_id=%s not found; returning FALSE",
            client_org_id,
        )
        return False

    return bool(row["verified"])


async def baa_signature_status(
    conn: asyncpg.Connection,
    client_org_id: str,
) -> dict:
    """Return a structured BAA status for the org, suitable for
    operator-facing displays and debugging.

    Returns dict with keys:
      - admin_flag (bool)              client_orgs.baa_on_file
      - has_formal_signature (bool)    any non-acknowledgment-only baa_signatures row
      - has_acknowledgment (bool)      any acknowledgment-only baa_signatures row
      - verified (bool)                admin_flag AND has_formal_signature
      - latest_signature_version (str | None)
      - latest_signature_at (datetime | None)

    For customer-facing artifacts, prefer `is_baa_on_file_verified()`
    — this richer status is for operator surfaces only.
    """
    row = await conn.fetchrow(
        """
        SELECT
            co.baa_on_file AS admin_flag,
            COALESCE(BOOL_OR(NOT bs.is_acknowledgment_only), FALSE) AS has_formal_signature,
            COALESCE(BOOL_OR(bs.is_acknowledgment_only), FALSE) AS has_acknowledgment,
            (
                SELECT bs2.baa_version
                  FROM baa_signatures bs2
                 WHERE LOWER(bs2.email) = LOWER(co.primary_email)
                 ORDER BY bs2.signed_at DESC
                 LIMIT 1
            ) AS latest_signature_version,
            (
                SELECT bs2.signed_at
                  FROM baa_signatures bs2
                 WHERE LOWER(bs2.email) = LOWER(co.primary_email)
                 ORDER BY bs2.signed_at DESC
                 LIMIT 1
            ) AS latest_signature_at
          FROM client_orgs co
          LEFT JOIN baa_signatures bs
            ON LOWER(bs.email) = LOWER(co.primary_email)
         WHERE co.id = $1
         GROUP BY co.id, co.baa_on_file, co.primary_email
        """,
        client_org_id,
    )

    if row is None:
        return {
            "admin_flag": False,
            "has_formal_signature": False,
            "has_acknowledgment": False,
            "verified": False,
            "latest_signature_version": None,
            "latest_signature_at": None,
        }

    result = dict(row)
    result["verified"] = bool(result["admin_flag"]) and bool(result["has_formal_signature"])
    return result
