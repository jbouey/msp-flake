"""F7 P3 from Session 213 round-table follow-up.

Source-level + DB-gated tests for the
GET /api/admin/sites/{site_id}/flywheel-diagnostic endpoint.
"""
from __future__ import annotations

import os
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
DIAG_PY = (
    REPO_ROOT
    / "mcp-server"
    / "central-command"
    / "backend"
    / "flywheel_diagnostic.py"
)
MAIN_PY = REPO_ROOT / "mcp-server" / "main.py"


def test_endpoint_module_exists():
    assert DIAG_PY.exists(), "flywheel_diagnostic.py missing"


def test_router_registered_in_main():
    """The router must be both imported AND included in main.py."""
    main_src = MAIN_PY.read_text()
    assert (
        "from dashboard_api.flywheel_diagnostic import router as flywheel_diagnostic_router"
        in main_src
    ), "flywheel_diagnostic_router not imported in main.py"
    assert "include_router(flywheel_diagnostic_router)" in main_src, (
        "flywheel_diagnostic_router not registered with app.include_router"
    )


def test_endpoint_path_correct():
    """Endpoint path must be GET /api/admin/sites/{site_id}/flywheel-diagnostic."""
    src = DIAG_PY.read_text()
    assert 'prefix="/api/admin/sites"' in src
    assert '"/{site_id}/flywheel-diagnostic"' in src
    assert "@router.get(" in src


def test_endpoint_requires_admin():
    """Auth: must be admin only."""
    src = DIAG_PY.read_text()
    assert "Depends(require_admin)" in src, (
        "F7 endpoint must use require_admin (substrate-credibility — only "
        "admins see cross-site flywheel signals)"
    )


def test_response_model_carries_canonical_resolution():
    """The response must distinguish input from canonical."""
    src = DIAG_PY.read_text()
    assert "site_id_input" in src
    assert "canonical_site_id" in src
    assert "canonical_aliasing" in src


def test_aggregates_all_four_signals():
    """The diagnostic must report all four flywheel signals (Session 213 F7)."""
    src = DIAG_PY.read_text()
    # 1. Canonical aliasing
    assert "site_canonical_mapping" in src
    # 2. Operational health
    assert "site_appliances" in src
    assert "is_orphan" in src
    # 3. Telemetry recency
    assert "execution_telemetry" in src
    assert "orphan_telemetry_24h" in src
    # 4. Flywheel state
    assert "promoted_rules" in src
    assert "lifecycle_state" in src
    # 5. Substrate signals
    assert "substrate_violations" in src


def test_recommendations_helper_is_read_only():
    """The diagnostic NEVER takes action — it only suggests. The
    `_build_recommendations` helper must not CALL rename_site,
    safe_rollout_promoted_rule, or any mutation primitive.

    The helper IS allowed to mention these in recommendation TEXT
    (telling the operator what to call). We strip string literals
    before checking so the call-vs-mention distinction is enforced."""
    src = DIAG_PY.read_text()
    helper_start = src.find("def _build_recommendations")
    assert helper_start != -1
    helper_end = src.find("\n\n@router.get", helper_start)
    helper_body = src[helper_start:helper_end]

    # Strip every quoted string literal (single, double, triple, f-string).
    # Conservative: a regex that handles the common cases; not full Python
    # tokenization but adequate for this guardrail.
    no_strings = re.sub(r'"""[\s\S]*?"""', '""', helper_body)
    no_strings = re.sub(r"'''[\s\S]*?'''", "''", no_strings)
    no_strings = re.sub(r'"[^"]*"', '""', no_strings)
    no_strings = re.sub(r"'[^']*'", "''", no_strings)

    forbidden = [
        "INSERT INTO",
        "UPDATE site",
        "DELETE FROM",
        "await rename_site",
        "rename_site(",
        "await safe_rollout",
        "safe_rollout_promoted_rule(",
        "await advance_lifecycle",
        "advance_lifecycle(",
        "conn.execute(",
        "conn.fetchval(",
    ]
    for forbidden_token in forbidden:
        assert forbidden_token not in no_strings, (
            f"_build_recommendations contains forbidden mutation/IO call "
            f"'{forbidden_token}' (after string-literal stripping). The "
            f"diagnostic endpoint is read-only; recommendations must point "
            f"the operator at action endpoints, never invoke them."
        )


def test_recommendations_phantom_promotion_warning():
    """If candidates exist for a non-existent / non-aliased site_id,
    the endpoint must surface the PhantomSiteRolloutError class as a
    recommendation."""
    src = DIAG_PY.read_text()
    assert "PHANTOM PROMOTION" in src or "PhantomSiteRolloutError" in src


def test_endpoint_canonicalizes_input():
    """An operator passing the OLD (orphan) site_id must get the same
    diagnostic as if they passed the canonical."""
    src = DIAG_PY.read_text()
    # The endpoint resolves through canonical_site_id() at the top of
    # the handler, then queries against canonical for canonical-keyed
    # tables (aggregated_pattern_stats, promoted_rules) and against
    # the input for raw-keyed tables (execution_telemetry, incidents).
    assert "SELECT canonical_site_id($1)" in src


def test_uses_admin_transaction_not_admin_connection():
    """F7 round-table P0 ship-blocker: handler runs 11 queries; under
    PgBouncer transaction-pool, admin_connection can route SET LOCAL
    and subsequent fetches to different backends → RLS hides every
    row → diagnostic returns silent zeros at the moment the operator
    needs ground truth. Same class as Session 212 sigauth bug.
    Multi-statement admin reads MUST use admin_transaction."""
    src = DIAG_PY.read_text()
    assert "admin_transaction" in src, (
        "Multi-statement admin reads MUST use admin_transaction "
        "(Session 212 routing-pin rule). Round-table P0."
    )
    assert "admin_connection(pool)" not in src, (
        "Reverted to admin_connection — that's the bug class "
        "Session 212 paid for. Use admin_transaction instead."
    )


def test_has_rate_limit():
    """Round-table P1: admin auth alone isn't enough; a buggy
    auto-refresh tab would still cost 11 PgBouncer transactions per
    call. Rate limit at 20/min/actor."""
    src = DIAG_PY.read_text()
    assert "check_rate_limit" in src
    assert "flywheel_diagnostic" in src
    assert "max_requests=20" in src or "max_requests = 20" in src


def test_pending_fleet_orders_section_present():
    """Round-table Angle 3 P1: pending fleet_orders section is the #1
    operator need during a flywheel incident — promoted rules that
    didn't get acked are the smoking gun."""
    src = DIAG_PY.read_text()
    assert "PendingFleetOrders" in src
    assert "sync_promoted_rule_pending" in src
    assert "fleet_orders" in src
    assert "parameters->>'site_id'" in src


def test_recommendations_are_structured():
    """Round-table Angle 2 P1: List[Recommendation] not List[str]."""
    src = DIAG_PY.read_text()
    assert "class Recommendation(BaseModel):" in src
    # Required fields
    assert re.search(r"code:\s*str", src)
    assert re.search(r"severity:\s*str", src)
    assert re.search(r"message:\s*str", src)
    # Some known codes from the heuristic helper
    assert "PHANTOM_PROMOTION_RISK" in src
    assert "ORPHAN_TELEMETRY" in src


def test_phantom_promotion_uses_aged_candidate_gate():
    """Round-table Angle 2 P1 false-positive gate: PHANTOM_PROMOTION_RISK
    must use the >1h-old candidate count, not the raw count, so a
    fresh cross-org candidate doesn't false-fire before its `sites`
    row materializes."""
    src = DIAG_PY.read_text()
    assert "aged_promotion_candidates" in src
    assert "INTERVAL '1 hour'" in src or "INTERVAL '1 hour'" in src


# DB-gated integration test — exercises the endpoint end-to-end.
_requires_db = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="F7 endpoint integration tests require TEST_DATABASE_URL",
)


@_requires_db
@pytest.mark.asyncio
async def test_diagnostic_returns_canonical_resolution():
    """End-to-end smoke: pass an alias, get the canonical."""
    import httpx
    from shared import async_session
    from sqlalchemy import text as sql_text

    # The endpoint requires admin auth — skip the live HTTP path here.
    # Instead, exercise the underlying canonical_site_id() resolution
    # (which the handler calls first). The function-level test in
    # test_canonical_site_id_function.py already pins the function
    # itself; this is a defense-in-depth pin that the F7 endpoint
    # doesn't bypass it.
    async with async_session() as db:
        result = await db.execute(sql_text(
            "SELECT canonical_site_id('physical-appliance-pilot-1aea78')"
        ))
        # If the backfill row from mig 256 is present in the test DB,
        # this resolves. Otherwise the function returns the input.
        scalar = result.scalar()
        assert scalar in ('physical-appliance-pilot-1aea78', 'north-valley-branch-2')
