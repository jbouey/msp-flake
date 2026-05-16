"""Cold-onboarding 2026-05-09 P0 #2 — F1 attestation letter BAA SQL fix.

Pre-fix: `client_attestation_letter._get_current_baa` referenced
`s.id` and `s.signer_email` on `baa_signatures`. The live schema
(Migration 224) names those columns `signature_id` and `email`. Every
self-serve-signed BAA org's first letter download therefore 500'd
inside `asyncpg`.

This source-shape gate pins the SQL so a future PR can't silently
re-introduce the wrong identifiers.

Task #93 v2 Commit 2 (2026-05-16) update: the helper migrated from
email-join (`LOWER(c.primary_email) = LOWER(s.email)`) to FK-join
(`c.id = s.client_org_id`) per mig 321's NOT NULL FK column. The
`signature_id` PK gate stays — that bug-class is independent. The
email-column gate becomes a client_org_id-FK gate, with a sentinel
that the prior email-join shape has NOT re-appeared.
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_LETTER = _BACKEND / "client_attestation_letter.py"
_MIG_224 = _BACKEND / "migrations" / "224_client_signup_and_billing.sql"


def _extract_get_current_baa_sql() -> str:
    """Extract ONLY the triple-quoted SQL string passed to fetchrow
    inside _get_current_baa. Excludes the docstring + comments so the
    shape-sentinels don't false-trip on historical commentary."""
    src = _LETTER.read_text()
    m = re.search(
        r"async def _get_current_baa\(.*?fetchrow\(\s*\"\"\"(.*?)\"\"\"",
        src,
        re.DOTALL,
    )
    assert m, "could not locate _get_current_baa fetchrow SQL"
    return m.group(1)


def test_baa_lookup_uses_signature_id_column():
    """The query must reference the live column `signature_id` (mig
    224), aliased back to `id` for caller-stable dict keys."""
    body = _extract_get_current_baa_sql()
    assert "s.signature_id" in body, (
        "_get_current_baa must select s.signature_id (the live column "
        "name in migrations/224_client_signup_and_billing.sql); using "
        "s.id raises UndefinedColumn on every letter issuance."
    )
    # The pre-fix bug — s.id on baa_signatures — must NOT reappear.
    # Match `s.id` as a whole word (not e.g. `s.signature_id`).
    assert not re.search(r"\bs\.id\b", body), (
        "_get_current_baa references `s.id` — that column does not "
        "exist on baa_signatures. The schema column is `signature_id`. "
        "Use `s.signature_id AS id` to preserve the dict-key contract."
    )


def test_baa_lookup_uses_email_column():
    """The SELECT must reference the live column `email` (mig 224) for
    `signer_email` aliasing — `s.signer_email` was the pre-fix bug
    name. (The JOIN itself migrated from email to client_org_id FK per
    task #93 v2 Commit 2 — see test_baa_lookup_uses_client_org_id_fk_join
    below.)"""
    body = _extract_get_current_baa_sql()
    assert "s.email" in body, (
        "_get_current_baa must reference s.email (live column name) for "
        "the signer_email alias. Pre-fix used s.signer_email which is "
        "undefined on the baa_signatures table per migrations/224."
    )
    # Sentinel: the pre-fix `s.signer_email` shape must NOT re-appear.
    assert "s.signer_email" not in body, (
        "_get_current_baa references `s.signer_email` — that column "
        "does not exist on baa_signatures. The schema column is "
        "`email`; alias it as `s.email AS signer_email`."
    )


def test_baa_lookup_uses_client_org_id_fk_join():
    """Task #93 v2 Commit 2 (2026-05-16): the BAA JOIN must use the FK
    column `s.client_org_id` (mig 321) — NOT the pre-cutover
    `LOWER(c.primary_email) = LOWER(s.email)` email-join shape that
    silently stranded every signed BAA on primary_email rename.
    """
    body = _extract_get_current_baa_sql()
    join_line = next(
        (ln for ln in body.splitlines() if "JOIN client_orgs" in ln),
        "",
    )
    assert "s.client_org_id" in join_line, (
        f"BAA JOIN must use the FK column s.client_org_id (mig 321) — "
        f"line found: {join_line!r}"
    )
    # Sentinel: the pre-cutover email-join shape must NOT re-appear.
    assert "LOWER(s.email)" not in body and "LOWER(c.primary_email)" not in body, (
        "_get_current_baa re-introduced LOWER(s.email) / "
        "LOWER(c.primary_email) — that is the orphan-class shape closed "
        "in task #93 v2 Commit 2. Use `c.id = s.client_org_id` instead."
    )


def test_mig_224_baa_signatures_has_signature_id_and_email():
    """Sentinel: confirm the live schema actually has these columns
    so the gate above isn't pinning a phantom name."""
    src = _MIG_224.read_text()
    assert "signature_id        TEXT         PRIMARY KEY" in src, (
        "Migration 224 baa_signatures schema changed — signature_id is "
        "no longer the PK. The letter SQL fix must be re-evaluated."
    )
    assert "email               TEXT         NOT NULL" in src, (
        "Migration 224 baa_signatures schema changed — email column "
        "renamed/removed. The letter SQL fix must be re-evaluated."
    )
