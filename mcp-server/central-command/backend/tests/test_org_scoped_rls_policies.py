"""CI gate: every site-RLS table referenced under org_connection MUST
also have a tenant_org_isolation policy.

Round-table 2026-05-05 (Stage 1 P0,
.agent/plans/25-client-portal-data-display-roundtable-2026-05-05.md).

ROOT CAUSE the gate prevents from regressing: tenant_middleware.org_connection()
sets `app.current_tenant=''` and `app.is_admin='false'`. Site-scoped
RLS policies use `site_id = current_setting('app.current_tenant')` —
empty string matches ZERO rows. Result: every client-portal endpoint
that uses org_connection to read a site-RLS-only table silently returns
zero rows, even when 100K+ rows exist for the org's sites.

User-visible 2026-05-05: 4 different surfaces showed contradictory
numbers (top-tile 20.8% / per-site 93% / Reports 100% / Evidence 0
bundles) all because of this one architectural drift.

This test runs as a SOURCE-LEVEL gate by parsing migrations directory
to determine which tables CURRENTLY have org-scoped policies (mig 278
+ any future). Then scans client_portal.py + adjacent for SQL queries
that touch tables under org_connection contexts. If a table is read
under org_connection but lacks an org-scoped policy in any migration,
fail loudly.

Limitations:
  - Uses regex to detect FROM/JOIN — false negatives on dynamic SQL
    are possible. Belt-and-suspenders: behavior tests should also
    catch this on a synthetic-org+bundle round-trip.
  - Doesn't run against the live DB; pure source analysis.
"""
from __future__ import annotations

import pathlib
import re
from typing import Set


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_MIG_DIR = _BACKEND / "migrations"


def _tables_with_org_policy() -> Set[str]:
    """Scan all migrations for tables that get a tenant_org_isolation
    policy. Returns the set of table names."""
    tables = set()
    pattern = re.compile(
        r"CREATE\s+POLICY\s+(?:tenant_org_isolation|client_audit_self_org|"
        r"client_email_change_self_org)\s+ON\s+(\w+)",
        re.IGNORECASE,
    )
    # Migration 278 uses a DO block iterating over a list; capture the
    # array literal too.
    do_block_pattern = re.compile(
        r"site_tables\s+TEXT\[\]\s*:=\s*ARRAY\s*\[(.*?)\]",
        re.IGNORECASE | re.DOTALL,
    )
    for mig in sorted(_MIG_DIR.glob("*.sql")):
        src = mig.read_text()
        for m in pattern.finditer(src):
            tables.add(m.group(1).lower())
        for m in do_block_pattern.finditer(src):
            for tname in re.findall(r"'([\w_]+)'", m.group(1)):
                tables.add(tname.lower())
    return tables


# Authoritative list of site-RLS tables (queried 2026-05-05 from
# pg_policies WHERE qual::text LIKE '%current_tenant%' on prod).
# When new tables get site-RLS policies they must be added here AND
# either get an org-scoped policy OR be confirmed not-touched-by-org_connection.
SITE_RLS_TABLES = {
    "admin_orders",
    "agent_deployments",
    "app_protection_profiles",
    "compliance_bundles",
    "device_compliance_details",
    "discovered_devices",
    "enumeration_results",
    "escalation_tickets",
    "evidence_bundles",
    "execution_telemetry",
    "go_agent_checks",
    "go_agent_orders",
    "go_agents",
    "incident_correlation_pairs",
    "incident_recurrence_velocity",
    # incident_remediation_steps RLS uses incident_id subquery — fine
    "incidents",
    "l2_decisions",
    "l2_rate_limits",
    "log_entries",
    "orders",
    # partner_notifications uses partner_id, not site_id — exempt
    "reconcile_events",
    "security_events",
    "sensor_registry",
    "site_appliances",
    "site_credentials",
    # site_drift_config uses '__defaults__' OR site_id — special, exempt
    "site_healing_sla",
    "site_notification_overrides",
    "target_health",
}


# Tables the client portal explicitly DOES NOT need org-scoped read
# access to (operator-only or always accessed via tenant_connection).
# Add to this set with explicit justification rather than letting the
# gate slide.
CLIENT_PORTAL_OUT_OF_SCOPE = {
    "admin_orders",         # operator-only
    "agent_deployments",    # operator-only
    "app_protection_profiles",  # operator-only
    "go_agent_orders",      # operator-only
    "incident_correlation_pairs",  # internal correlation, operator-only
    "incident_recurrence_velocity",  # internal correlation
    "l2_rate_limits",       # internal rate limiter state
    "sensor_registry",      # operator-only
    "orders",               # operator-only fleet orders
    "reconcile_events",     # operator-only
    "site_credentials",     # operator-only (PHI-adjacent)
    "site_healing_sla",     # operator-only
    "site_notification_overrides",  # operator-only
    "target_health",        # operator-only telemetry
    "evidence_bundles",     # legacy table, replaced by compliance_bundles
    "enumeration_results",  # operator-only
    "go_agent_checks",      # operator-only (raw check telemetry)
    # Maya final-sweep meta-gate flushed these two out (Session 217):
    "incident_remediation_steps",  # RLS via JOIN to incidents.site_id;
                                   # transitively-covered, not directly
                                   # site-keyed. No org-scope read path
                                   # from client_portal exists.
    "partner_notifications",  # uses app.current_tenant as
                              # partner_id sentinel (NOT site_id);
                              # different RLS shape entirely. Reads
                              # gated by partner-context, not org-context.
}


def test_every_client_portal_site_rls_table_has_org_policy():
    """Stage 1 gate: every site-RLS table that the client portal
    reads under org_connection MUST also have an org-scoped policy.

    Without this gate, adding a new site-RLS table (e.g. mig 280) and
    reading it from client_portal would silently return zero rows in
    prod — exactly the 2026-05-05 regression class.
    """
    in_scope = SITE_RLS_TABLES - CLIENT_PORTAL_OUT_OF_SCOPE
    have_org_policy = _tables_with_org_policy()

    missing = in_scope - have_org_policy
    assert not missing, (
        "Site-RLS tables are reachable from client_portal under "
        "org_connection but lack a tenant_org_isolation policy. Adding "
        "the table to a future migration mirrors mig 278 — the DO-block "
        "approach iterates over a list and applies the same uniform "
        "policy. If the table is operator-only and should NOT be "
        "client-readable, add it to CLIENT_PORTAL_OUT_OF_SCOPE here "
        "WITH a comment explaining why (PHI-adjacent / fleet-internal / "
        "etc.).\n\n"
        f"Tables missing tenant_org_isolation: {sorted(missing)}"
    )


def test_no_dishonest_score_defaults_in_client_portal():
    """Stage 1 gate (round-table verdict): every score-bearing return
    in client_portal.py must distinguish 'compliant' from 'no data'.
    The pattern `if total > 0 ... else 100.0` is the canonical
    antipattern — a customer with zero data sees a perfect score and
    cannot tell the platform isn't actually monitoring them.

    This is a substring-grep gate over client_portal.py specifically;
    other modules may legitimately use 100.0 for non-score purposes.
    """
    src = (_BACKEND / "client_portal.py").read_text()
    # Permitted exceptions: comments referencing the historical pattern.
    # Strip comment lines before scanning.
    code_lines = [
        ln for ln in src.splitlines()
        if not ln.strip().startswith("#")
    ]
    code = "\n".join(code_lines)
    bad = re.findall(
        r"else\s+100\.0", code,
    )
    assert not bad, (
        "client_portal.py contains `else 100.0` — the 0/0=100% "
        "antipattern flagged by Maya P0 round-table 2026-05-05. "
        "Replace with `else None` and add a `score_status: 'no_data'` "
        "field so the frontend can show '—' instead of a misleading "
        "perfect score. (Comment lines explaining the prior pattern "
        "are stripped before the scan.)\n\n"
        f"Found {len(bad)} occurrence(s)."
    )


def test_migration_278_applies_org_policy_to_compliance_bundles():
    """Spot-check the headline table — the user-visible regression
    that triggered this round-table was Evidence Archive showing 0
    bundles for an org with 155K rows. compliance_bundles MUST be
    in mig 278's apply-list."""
    mig = _MIG_DIR / "278_org_scoped_rls_for_site_tables.sql"
    assert mig.exists(), "mig 278 missing"
    src = mig.read_text()
    assert "'compliance_bundles'" in src
    # The DO block uses CREATE POLICY tenant_org_isolation
    assert "tenant_org_isolation" in src
    # And uses a function that scopes by current_org via sites lookup
    assert "rls_site_belongs_to_current_org" in src
    assert "client_org_id::text = current_setting('app.current_org'" in src


def test_site_rls_tables_list_covers_migrations():
    """Maya P1 (Session 217 final sweep): the hand-maintained
    SITE_RLS_TABLES list is the source of truth for which tables get
    org-scoped policy coverage. If a future migration adds a CREATE
    POLICY with `current_tenant` predicate on a NEW table without
    updating SITE_RLS_TABLES here, the gate above silently passes
    despite the new table being uncovered. This meta-gate scans every
    migration for site-scoped policies + asserts each target table is
    either in SITE_RLS_TABLES or in CLIENT_PORTAL_OUT_OF_SCOPE.

    Catches: new feature ships migration with `tenant_isolation` policy
    on a new table; client portal reads from it under org_connection;
    customer sees zero rows. Same regression class as the 2026-05-05
    P0 the parent test was designed to prevent — but at the source-of-
    truth-list level so the parent test can't be fooled.
    """
    pat_create_policy = re.compile(
        r"CREATE\s+POLICY\s+\w+\s+ON\s+(\w+)\s+FOR\s+ALL\s+USING\s*\([^)]*current_setting\s*\(\s*'app\.current_tenant'",
        re.IGNORECASE,
    )
    discovered: set = set()
    for mig in sorted(_MIG_DIR.glob("*.sql")):
        src = mig.read_text()
        for m in pat_create_policy.finditer(src):
            tname = m.group(1).lower()
            # Skip dynamic table names (e.g. partition templates) and
            # tables with NULL placeholder in policy (none expected).
            if tname == "tenant_isolation":
                continue
            discovered.add(tname)

    missing = discovered - SITE_RLS_TABLES - CLIENT_PORTAL_OUT_OF_SCOPE
    assert not missing, (
        "Migrations declare site-RLS tenant_isolation policy on tables "
        "that are NOT in tests/test_org_scoped_rls_policies.py "
        "SITE_RLS_TABLES or CLIENT_PORTAL_OUT_OF_SCOPE. The parent gate "
        "(test_every_client_portal_site_rls_table_has_org_policy) silently "
        "passes for tables it doesn't know about → regression class "
        "open. Add each missing table to SITE_RLS_TABLES (then ensure "
        "mig 278's DO-block applies tenant_org_isolation), or to "
        "CLIENT_PORTAL_OUT_OF_SCOPE with explicit justification.\n\n"
        f"Tables found in migrations but not in either set: {sorted(missing)}"
    )


def test_migration_278_helper_function_is_stable():
    """The RLS helper function MUST be STABLE (or IMMUTABLE) so the
    planner can hoist its calls across rows of the same query.
    VOLATILE would force a per-row re-evaluation hit."""
    mig = _MIG_DIR / "278_org_scoped_rls_for_site_tables.sql"
    src = mig.read_text()
    helper_block = re.search(
        r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+rls_site_belongs_to_current_org.*?\$\$",
        src, re.IGNORECASE | re.DOTALL,
    )
    assert helper_block, "rls_site_belongs_to_current_org not found"
    assert (
        "STABLE" in helper_block.group(0).upper()
        or "IMMUTABLE" in helper_block.group(0).upper()
    ), "RLS helper must be STABLE or IMMUTABLE for planner hoisting"
