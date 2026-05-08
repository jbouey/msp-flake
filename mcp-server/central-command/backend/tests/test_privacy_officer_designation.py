"""Source-shape + contract gates for F2 Privacy Officer designation
(round-table 2026-05-06).

The Compliance Attestation Letter (F1) pulls the Privacy Officer name
from this module's signed acceptance attestation. If the registration
contract drifts (event names, allow-list lockstep, owner-only auth,
RLS policy, signed-acceptance flow), F1 either (a) renders a fictional
signature, or (b) raises 500 at customer-download time. Neither is
acceptable.

These tests exercise the SOURCE shape — they don't open a Postgres
connection (so they run on the dev box without backend deps). The
runtime contract (DB trigger checks, RLS isolation, attestation-bundle
chain hash) lives in `tests/test_privacy_officer_pg.py` (planned;
runs server-side). Together they form the F2 contract gate.
"""
from __future__ import annotations

import pathlib
import re
import sys

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# ---------------------------------------------------------------- ALLOWED_EVENTS lockstep


def test_two_new_event_types_in_allowed_events():
    """privileged_access_attestation.ALLOWED_EVENTS must contain BOTH
    privacy-officer events. Otherwise create_privileged_access_
    attestation() raises and the designation flow is broken."""
    src = (_BACKEND / "privileged_access_attestation.py").read_text()
    assert '"client_org_privacy_officer_designated"' in src, (
        "client_org_privacy_officer_designated MUST be in ALLOWED_EVENTS"
    )
    assert '"client_org_privacy_officer_revoked"' in src, (
        "client_org_privacy_officer_revoked MUST be in ALLOWED_EVENTS"
    )


def test_event_types_used_in_designation_module():
    """The two event names must appear verbatim in the module that
    calls create_privileged_access_attestation. Catches silent
    rename drift between the allow-list and the caller."""
    src = (_BACKEND / "client_privacy_officer.py").read_text()
    assert '"client_org_privacy_officer_designated"' in src
    assert '"client_org_privacy_officer_revoked"' in src


# ---------------------------------------------------------------- migration shape


def test_migration_creates_designations_table():
    """287_privacy_officer_designations.sql must exist and create the
    expected table + 1-active-at-a-time partial unique index + RLS."""
    mig = (
        _BACKEND
        / "migrations"
        / "287_privacy_officer_designations.sql"
    )
    assert mig.exists(), (
        "Migration 287 must exist — F2 data layer is load-bearing for F1"
    )
    src = mig.read_text()

    assert "CREATE TABLE IF NOT EXISTS privacy_officer_designations" in src
    # Required columns by name (Carol contract).
    for col in (
        "client_org_id",
        "name",
        "title",
        "email",
        "accepted_at",
        "accepting_user_id",
        "accepting_user_email",
        "explainer_version",
        "attestation_bundle_id",
        "revoked_at",
        "revoked_by_user_id",
        "revoked_reason",
        "ip_address",
        "user_agent",
    ):
        assert col in src, f"Migration 287 missing required column: {col!r}"

    # 1-active-per-org invariant.
    assert (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_po_designations_org_active"
        in src
    ), "Partial unique index on revoked_at IS NULL must enforce 1 active"
    assert "WHERE revoked_at IS NULL" in src

    # Revocation field consistency check (no half-filled revocations).
    assert "po_revocation_fields_consistent" in src, (
        "CHECK constraint must enforce revocation fields land together"
    )

    # Reason length bar matches privileged-access ≥20 convention.
    assert "po_revoked_reason_minlen" in src
    assert "LENGTH(revoked_reason) >= 20" in src

    # RLS — tenant_org_isolation policy required (matches mig 085, 087).
    assert "ENABLE ROW LEVEL SECURITY" in src
    assert "CREATE POLICY tenant_org_isolation" in src
    assert (
        "client_org_id::text = current_setting('app.current_org', true)"
        in src
    )


# ---------------------------------------------------------------- API endpoints


def test_three_api_endpoints_registered():
    """Three endpoints in client_portal.py: GET (read), POST designate,
    POST revoke. Owner-only on the two mutating endpoints."""
    src = (_BACKEND / "client_portal.py").read_text()
    assert '@auth_router.get("/privacy-officer")' in src
    assert '@auth_router.post("/privacy-officer/designate")' in src
    assert '@auth_router.post("/privacy-officer/revoke")' in src


def test_designate_endpoint_is_owner_only():
    """Mutating designate endpoint MUST gate to require_client_owner.
    Lower roles (admin/viewer) cannot designate — §164.308(a)(2)
    designation is the OWNER's decision."""
    src = (_BACKEND / "client_portal.py").read_text()
    # Find the designate function body.
    idx = src.find("async def designate_privacy_officer(")
    assert idx > 0, "designate_privacy_officer endpoint missing"
    sig_block = src[idx : idx + 400]
    assert "Depends(require_client_owner)" in sig_block, (
        "designate endpoint MUST use require_client_owner — admin "
        "role cannot designate Privacy Officer."
    )


def test_revoke_endpoint_is_owner_only():
    """Same posture for revoke."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def revoke_privacy_officer(")
    assert idx > 0
    sig_block = src[idx : idx + 400]
    assert "Depends(require_client_owner)" in sig_block


def test_get_endpoint_is_user_readable():
    """GET endpoint visible to all client users (read-only) — staff
    need to see WHO the Privacy Officer is. Mutation gated above."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def get_privacy_officer_designation(")
    assert idx > 0
    sig_block = src[idx : idx + 400]
    assert "Depends(require_client_user)" in sig_block, (
        "GET endpoint MUST be readable by client users (not gated to owner)"
    )


# ---------------------------------------------------------------- module contract


def test_module_exposes_designate_revoke_get_current():
    """Public surface stable for F1 import."""
    src = (_BACKEND / "client_privacy_officer.py").read_text()
    assert re.search(r"^async def designate\(", src, re.MULTILINE), (
        "client_privacy_officer.designate(...) must be defined"
    )
    assert re.search(r"^async def revoke\(", src, re.MULTILINE)
    assert re.search(r"^async def get_current\(", src, re.MULTILINE)
    assert "class PrivacyOfficerError" in src


def test_designate_requires_minimum_acceptance_acknowledgement():
    """The wizard MUST display the §164.308(a)(2) explainer text and
    the user must accept it. Empty / suspiciously-short
    acknowledgement = client bypassing the wizard."""
    src = (_BACKEND / "client_privacy_officer.py").read_text()
    assert (
        "len(acceptance_acknowledgement.strip()) < 50" in src
    ), (
        "designate() must reject acceptance_acknowledgement shorter "
        "than 50 chars — bypass-detection."
    )


def test_designate_writes_chain_attestation():
    """Every designation MUST emit a chain-anchored attestation bundle.
    Without it the audit trail is just an audit_log row — not the
    cryptographic record §164.308(a)(2) auditors recognize."""
    src = (_BACKEND / "client_privacy_officer.py").read_text()
    assert "create_privileged_access_attestation" in src
    # Anchor at org's primary site_id (per Anchor-namespace convention).
    assert "resolve_client_anchor_site_id" in src
    assert 'f"client_org:{client_org_id}"' in src, (
        "Synthetic client_org:<id> fallback when org has no site yet "
        "(matches Session 216 anchor convention)"
    )


def test_designate_replacement_is_atomic():
    """Replacement = revoke prior + insert new, in ONE transaction.
    Otherwise a concurrent reader could see zero-or-two designations
    in the gap. Window bumped to 8000 to accommodate Carol MUST-1 +
    MUST-4 explainer-hash + self-attestation precondition checks
    that grew the function body."""
    src = (_BACKEND / "client_privacy_officer.py").read_text()
    idx = src.find("async def designate(")
    assert idx > 0
    body = src[idx : idx + 8000]
    assert "async with conn.transaction():" in body, (
        "designate() must wrap revoke-prior + insert-new in a single "
        "DB transaction — atomic replacement contract."
    )


def test_designate_requires_explainer_hash_match():
    """Carol MUST-1 + Maya P2-B (round-table 2026-05-06): server-
    side hash compare against the canonical explainer text. Closes
    the 'user submits 50 spaces' bypass — only way to produce the
    correct hash is to have actually loaded the explainer file."""
    src = (_BACKEND / "client_privacy_officer.py").read_text()
    assert "_load_explainer_text" in src
    assert "_expected_explainer_hash" in src
    assert "accepted_explainer_sha256" in src
    # Hash compare logic.
    assert "submitted_sha != expected_sha" in src
    # Canonical explainer file must exist on disk for v1-2026-05-06.
    explainer_dir = (
        _BACKEND / "templates" / "privacy_officer_explainer"
    )
    assert explainer_dir.exists(), (
        "Canonical explainer directory missing — Carol MUST-1 "
        "requires version-controlled explainer text"
    )
    v1 = explainer_dir / "v1-2026-05-06.md"
    assert v1.exists(), (
        "Canonical v1-2026-05-06 explainer file missing — "
        "EXPLAINER_VERSION points at a non-existent file"
    )
    v1_text = v1.read_text()
    assert "164.308(a)(2)" in v1_text
    assert "Privacy Officer" in v1_text


def test_designate_requires_authorization_self_attestation():
    """Carol MUST-4 (round-table 2026-05-06): owner self-attests
    they have governing-document authority to designate the
    Privacy Officer. Closes the LLC-manager-vs-officer delegation
    question without OsirisCare interpreting entity formation
    documents."""
    src = (_BACKEND / "client_privacy_officer.py").read_text()
    assert "is_authorized_self_attestation" in src
    assert "if not is_authorized_self_attestation:" in src
    assert "governing documents" in src.lower()


def test_explainer_endpoint_registered():
    """GET /api/client/privacy-officer/explainer must return text +
    sha256 + version. The wizard fetches this, displays text, and
    submits the sha256 back on POST /designate."""
    src = (_BACKEND / "client_portal.py").read_text()
    assert '@auth_router.get("/privacy-officer/explainer")' in src
    assert "get_explainer_text_and_hash" in src


def test_revoke_requires_minimum_reason_length():
    """≥20 chars reason matches the privileged-access convention.
    Catches lazy 'just doing it' revocations that destroy F1 letter
    generation without explanation."""
    src = (_BACKEND / "client_privacy_officer.py").read_text()
    assert "len(reason.strip()) < 20" in src, (
        "revoke() must reject reason shorter than 20 chars"
    )


def test_revoke_is_idempotent_returning_none():
    """revoke() with no active designation must return None (not
    raise). Idempotent revoke = safe to retry on transient failures."""
    src = (_BACKEND / "client_privacy_officer.py").read_text()
    idx = src.find("async def revoke(")
    body = src[idx : idx + 3000]
    assert "if existing is None:" in body
    assert "return None" in body


def test_get_current_filters_to_active_only():
    """get_current() returns the row with revoked_at IS NULL.
    Returning a revoked row would leak a stale name into F1's
    Letter — Carol's 'never print a stale signature' contract."""
    src = (_BACKEND / "client_privacy_officer.py").read_text()
    idx = src.find("async def get_current(")
    body = src[idx : idx + 1500]
    assert "WHERE client_org_id = $1" in body
    assert "AND revoked_at IS NULL" in body


# ---------------------------------------------------------------- audit log parity


def test_designate_endpoint_writes_client_audit_log():
    """Mutating endpoint must write a client_audit_log row for
    §164.308(a)(1)(ii)(D) parity (matches every other mutating
    client-portal endpoint)."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def designate_privacy_officer(")
    body = src[idx : idx + 4000]
    assert '_audit_client_action' in body
    assert '"PRIVACY_OFFICER_DESIGNATED"' in body


def test_revoke_endpoint_writes_client_audit_log():
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def revoke_privacy_officer(")
    body = src[idx : idx + 4000]
    assert '_audit_client_action' in body
    assert '"PRIVACY_OFFICER_REVOKED"' in body
