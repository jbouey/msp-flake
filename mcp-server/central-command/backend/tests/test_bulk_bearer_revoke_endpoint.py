"""#123 Sub-B — source-shape sentinels for bulk_bearer_revoke_api.

TIER-1 (no DB, no asyncpg, no pynacl) — runs in local pre-push sweep.

Pins:
  1. Endpoint exists at expected path with require_admin dep
  2. Banned-actor list mirrors vault precedent
  3. max_length=50 blast-radius cap pinned in schema
  4. min_length=20 reason pinned
  5. uses ::text[] cast (P0-1) NOT ::uuid[]
  6. uses admin_transaction NOT admin_connection (PgBouncer routing)
  7. SQL uses summary->>'event_type' shape matching invariant
  8. 404 unification for missing + soft-deleted (P0-3)
  9. BAA deferral registered in _DEFERRED_WORKFLOWS (P0-2)
 10. Router wired in main.py
 11. Extended event_type literal parity (Sub-A Gate B P1-4 carryover)
"""
from __future__ import annotations

import pathlib
import re


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_MODULE = _BACKEND / "bulk_bearer_revoke_api.py"
_MAIN = _BACKEND.parent.parent / "main.py"
_BAA = _BACKEND / "baa_enforcement.py"
_ASSERTIONS = _BACKEND / "assertions.py"


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


def test_module_exists():
    assert _MODULE.exists(), f"bulk_bearer_revoke_api.py missing: {_MODULE}"


def test_endpoint_path_and_admin_gate():
    src = _read(_MODULE)
    assert '@router.post("/sites/{site_id}/appliances/revoke-bearers")' in src, (
        "Endpoint must be POST /sites/{site_id}/appliances/revoke-bearers "
        "(under /api/admin prefix from router)."
    )
    assert "require_admin" in src, (
        "Endpoint MUST have require_admin dependency (admin-only). "
        "Counsel Rule 3 + zero-auth audit baseline."
    )
    assert "Depends(require_admin)" in src, (
        "require_admin must be wired via Depends() at the parameter site."
    )


def test_router_prefix_is_api_admin():
    src = _read(_MODULE)
    assert 'APIRouter(prefix="/api/admin"' in src, (
        "Router prefix MUST be /api/admin — full endpoint path becomes "
        "/api/admin/sites/{site_id}/appliances/revoke-bearers."
    )


def test_banned_actor_list_mirrors_vault_precedent():
    src = _read(_MODULE)
    # Must include the same banned values as vault_key_approval_api.py:87-89.
    for banned in ('"system"', '"admin"', '"operator"', '"fleet-cli"'):
        assert banned in src, (
            f"_BANNED_ACTORS missing {banned!r} — privileged-chain rule "
            f"\"actor MUST be a named human email\" requires the full list."
        )


def test_blast_radius_cap_and_reason_validation():
    src = _read(_MODULE)
    assert "max_length=50" in src, (
        "max_length=50 blast-radius cap missing — mirror #118 fan-out "
        "shape. 250-appliance fleet = 5-call sequence."
    )
    assert "min_length=20" in src, (
        "reason min_length=20 missing — Counsel Rule 3 audit-actor + "
        "operational-reason requirement."
    )
    assert "EmailStr" in src, (
        "actor_email must be EmailStr (validated email) per Gate A v2 + "
        "vault precedent."
    )


def test_uses_text_cast_not_uuid():
    """Gate A v2 P0-1: appliance_id is text/varchar in prod — using
    ::uuid[] triggers the 2026-05-13 signature_auth.py:618 outage class
    pinned by tests/test_no_uuid_cast_on_text_column.py.
    """
    src = _read(_MODULE)
    assert "::text[]" in src, (
        "SQL must cast to ::text[] (appliance_id is text/varchar in "
        "prod_column_types.json). NEVER ::uuid[]."
    )
    # Strip comments + docstrings before checking for ::uuid[] (the
    # rule banner in docstrings legitimately references the banned
    # cast as "NEVER ::uuid[]"). Only CODE occurrences are regressions.
    code_only = re.sub(r"#[^\n]*", "", src)
    code_only = re.sub(r'"""[\s\S]*?"""', "", code_only)
    assert "::uuid[]" not in code_only, (
        "REGRESSION: ::uuid[] appears in CODE — Gate A v2 P0-1 "
        "outage class. Replace with ::text[]."
    )


def test_uses_admin_transaction_not_admin_connection():
    """CLAUDE.md "admin_transaction for multi-statement admin paths"
    rule — PgBouncer transaction-pool would route SET LOCAL +
    subsequent statements to different backends under
    admin_connection. Multi-statement = MUST use admin_transaction.
    """
    src = _read(_MODULE)
    assert "admin_transaction(pool)" in src, (
        "Multi-statement admin path MUST use admin_transaction "
        "(PgBouncer routing rule)."
    )
    # Anti-pattern check: admin_connection() in this module would be
    # the #138 sweep regression class.
    assert "admin_connection(pool)" not in src, (
        "REGRESSION: admin_connection() in multi-statement endpoint — "
        "#138 anti-pattern. Use admin_transaction."
    )


def test_select_uses_for_update_lock():
    """Gate A v2 P1-1: TOCTOU defense — SELECT must lock the rows
    against concurrent revoke calls (vault precedent
    vault_key_approval_api.py:141).
    """
    src = _read(_MODULE)
    assert "FOR UPDATE" in src, (
        "SELECT must use FOR UPDATE to close the TOCTOU window "
        "between lookup + UPDATE. Concurrent calls would race."
    )


def test_attestation_uses_target_appliance_ids_kwarg():
    """The writer (privileged_access_attestation.py:411,481-489)
    stores summary.target_appliance_ids[] when the kwarg is passed.
    The Sub-A sev1 invariant reads
    `summary->'target_appliance_ids' ? appliance_id::text`.
    Endpoint MUST pass the kwarg or the invariant cannot match
    individual appliances → false-attestation class.
    """
    src = _read(_MODULE)
    assert "target_appliance_ids=appliance_ids" in src, (
        "create_privileged_access_attestation must be called with "
        "target_appliance_ids=appliance_ids — the sev1 invariant in "
        "assertions.py:2495-2497 queries the summary array. Without "
        "this kwarg, the writer stores only count=1 and the invariant "
        "cannot match individual appliances (false-positive flood)."
    )
    assert 'event_type="bulk_bearer_revoke"' in src, (
        "event_type literal MUST be 'bulk_bearer_revoke' (P0-4 + "
        "Sub-A Gate B P1-4 parity)."
    )


def test_404_unification_for_missing_and_soft_deleted():
    """Gate A v2 P0-3 existence-oracle fix: both missing AND
    soft-deleted appliance_ids return identical 404 body. Distinction
    logged to admin_audit_log.details only (admin-context).
    """
    src = _read(_MODULE)
    # The partition logic should NOT raise a different status for
    # soft-deleted. Check the response detail is uniform.
    assert "status_code=404" in src, "Must return 404 for not-actionable IDs"
    # The partition uses `r["deleted_at"] is None` Python-side after
    # the SELECT (we fetch deleted_at regardless and partition in
    # Python so the audit row gets the full distinction). Either
    # SQL-side filter OR Python-side partition is acceptable; the
    # invariant is that soft-deleted rows don't end up in live_set.
    assert (
        "deleted_at IS NULL" in src
        or "r[\"deleted_at\"] is None" in src
        or "r['deleted_at'] is None" in src
    ), (
        "Must partition live vs soft-deleted rows (either SQL filter "
        "OR Python-side partition on deleted_at)."
    )
    assert "not found at this site" in src, (
        "404 detail must use uniform message that doesn't distinguish "
        "missing from soft-deleted (existence-oracle defense)."
    )
    # The 409-distinct branch from v1 must NOT exist.
    assert "soft-deleted (refusing" not in src, (
        "REGRESSION: v1 design's 409-distinct branch detected. "
        "P0-3 fix requires unified 404."
    )


def test_idempotency_partition_to_flip_vs_already_revoked():
    """Gate B P1-5 idempotency requirement: re-running on already-
    revoked appliance writes a fresh attestation WITHOUT flipping
    the already-TRUE column. Both go into the attestation's
    target_appliance_ids (auditor sees full operator intent).
    """
    src = _read(_MODULE)
    assert "to_flip" in src and "already_revoked" in src, (
        "Idempotency partition (to_flip[] vs already_revoked[]) "
        "missing. P1-5 requirement."
    )
    # The UPDATE must be guarded on to_flip[] non-empty so the
    # already-revoked-only case doesn't issue a no-op WHERE FALSE.
    assert "if to_flip:" in src, (
        "UPDATEs must be gated on `if to_flip:` so the all-already-"
        "revoked case skips the column-flip but still writes the "
        "attestation (retroactive-attestation procedure)."
    )


def test_baa_deferral_registered():
    """Gate A v2 P0-2: bulk_bearer_revoke MUST be in
    baa_enforcement._DEFERRED_WORKFLOWS with rationale matching the
    partner_admin_transfer / ingest precedent shape.
    """
    src = _read(_BAA)
    assert '"bulk_bearer_revoke"' in src, (
        "bulk_bearer_revoke not in baa_enforcement._DEFERRED_WORKFLOWS "
        "— Gate A v2 P0-2. The endpoint will raise on first call via "
        "the lockstep check."
    )
    # Rationale must cite key elements per Gate A re-check.
    assert "§164.308(a)(4)" in src, (
        "Deferral rationale must cite §164.308(a)(4) workforce-access "
        "(the regulatory basis for emergency-revocation)."
    )
    assert "Ed25519 attestation" in src, (
        "Deferral rationale must name where the audit trail lives "
        "(NOT the BAA gate) — Counsel Rule 3 compliance."
    )


def test_router_wired_in_main():
    src = _read(_MAIN)
    assert "from dashboard_api.bulk_bearer_revoke_api import router as bulk_bearer_revoke_router" in src, (
        "Router import missing from main.py — endpoint will 404."
    )
    assert "app.include_router(bulk_bearer_revoke_router)" in src, (
        "Router not registered in main.py — endpoint will 404."
    )


def test_rate_limit_per_site_per_week():
    """#123 Sub-B Gate B P1-1: per-site rate-limit cap defends against
    a compromised admin spinning the nuclear button. Mirrors
    fleet_cli.PRIVILEGED_RATE_LIMIT_PER_WEEK=3.
    """
    src = _read(_MODULE)
    assert "_RATE_LIMIT_PER_WINDOW = 3" in src, (
        "Per-site rate limit (3 per 7-day window) missing — Gate B "
        "P1-1 nuclear-loop defense."
    )
    assert "count_recent_privileged_events" in src, (
        "Endpoint must call count_recent_privileged_events to enforce "
        "the rate limit."
    )
    assert "status_code=429" in src, (
        "Rate-limit hit must return 429 (not silently pass)."
    )
    assert 'event_type="bulk_bearer_revoke"' in src, (
        "Rate-limit count must filter on event_type='bulk_bearer_revoke' "
        "(NOT global privileged_access count — other event types are "
        "their own concern)."
    )


def test_event_type_literal_parity_end_to_end():
    """Sub-A Gate B P1-4 carryover, extended to cover the end-to-end
    write path through this endpoint:
      writer (this endpoint) → attestation module → DB → invariant SQL

    All three must use the IDENTICAL literal 'bulk_bearer_revoke'.
    """
    endpoint_src = _read(_MODULE)
    invariant_src = _read(_ASSERTIONS)

    LITERAL = "'bulk_bearer_revoke'"

    # Endpoint writer
    assert '"bulk_bearer_revoke"' in endpoint_src, (
        "Endpoint must call create_privileged_access_attestation with "
        'event_type="bulk_bearer_revoke" verbatim.'
    )
    # Invariant reader (already verified by Sub-A test; included here
    # for end-to-end story)
    assert LITERAL in invariant_src, (
        "Invariant lost the literal — Sub-A regression."
    )
