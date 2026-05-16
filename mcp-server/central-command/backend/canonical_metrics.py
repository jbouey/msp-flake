"""Canonical-source registry for customer-facing metrics (Counsel Rule 1).

Counsel's Rule 1 (gold authority, 2026-05-13):

    "No non-canonical metric leaves the building. Every exposed metric
    must have a declared canonical source. Anything non-canonical gets
    hidden or explicitly marked non-authoritative. No deck, dashboard,
    postcard, or auditor artifact may compute against convenience tables."

This module is the SINGLE source of truth for which customer-facing
metric classes have a canonical helper, which legacy callsites are
allowed during the drive-down phase, and which planned metrics exist
in PLANNED_METRICS pending their canonical helpers shipping.

The CI gate `tests/test_canonical_metrics_registry.py` enforces:

  1. Every non-canonical computation listed under `allowlist:` exists
     in the codebase (catch dead-removal drift).
  2. The frozen-baseline ratchet — new violations fail the gate;
     existing baseline drives to zero via per-line
     `# canonical-migration: <metric_class> — <reason>` markers.
  3. PLANNED_METRICS entries must NOT be exposed by any customer-facing
     surface today (gate parity per Task #50 Gate B P0-B fix).

Lockstep peers (Task #50 Gate B P1-4):
  - fleet_cli.PRIVILEGED_ORDER_TYPES
  - privileged_access_attestation.ALLOWED_EVENTS
  - migration v_privileged_types
  - flywheel_state.EVENT_TYPES
  - BAA_GATED_WORKFLOWS (Task #52, pending)
  - BACKEND_THIRD_PARTY_INTEGRATIONS (Task #55 §5 future-eng, pending)
  - This file (CANONICAL_METRICS + PLANNED_METRICS)

Any change to the constants below requires:
  (a) source-grep confirmation of the affected callsites,
  (b) coach pass on the migration PR,
  (c) update of the CI gate's ratchet baseline,
  (d) the per-line `# canonical-migration:` marker on every callsite
      being migrated.

Class-B Gate A: APPROVE-WITH-FIXES (5 P0s applied in v2).
Class-B Gate B: APPROVE-WITH-FIXES (2 P0s + 4 P1s applied in v3).
Class-B Gate B re-fork: APPROVE.

Phase 0+1 implementation (this commit): registry constants + CI gate
skeleton. Phase 2 substrate invariant `canonical_metric_drift`
(display-vs-chain-time) lands in a follow-up commit before drive-down
begins.
"""
from __future__ import annotations

from typing import Any, Dict, List


# ─────────────────────────────────────────────────────────────────────
# CANONICAL_METRICS — registry of customer-facing metrics with their
# canonical helper. The CI gate enforces that every callsite of a
# non-canonical computation appears in the entry's `allowlist` with
# a classification + (for `migrate` class) a per-line migration marker
# at the callsite.
# ─────────────────────────────────────────────────────────────────────

CANONICAL_METRICS: Dict[str, Dict[str, Any]] = {
    "compliance_score": {
        # The ONE canonical helper. Every customer-facing surface MUST
        # delegate to this function. Score-percentage formulas inline
        # in other modules (`passed / max(total, 1) * 100`) are
        # Rule 1 violations unless allowlisted as `operator_only`.
        "canonical_helper": "compliance_score.compute_compliance_score",
        # The module where inline computation is permitted (the helper
        # itself).
        "permitted_inline_in_module": "compliance_score",
        # Allowlist of dotted-path function signatures that perform
        # non-canonical computation today. Each entry must classify
        # as either `migrate` (drive-down target — needs per-line
        # `# canonical-migration:` marker at callsite) or
        # `operator_only` (substrate-internal use only, never reaches
        # customer-facing surfaces).
        "allowlist": [
            # RECLASSIFIED 2026-05-15 (Task #103 Gate A): stateless 7-boolean
            # averager (patching/antivirus/backup/logging/firewall/encryption/
            # network); not a bundle aggregator. Real callers: metrics.py:257+
            # 374 internal wrappers + fleet.py:171 admin get_fleet_overview.
            # Different metric semantics — does NOT delegate to
            # compute_compliance_score.
            {"signature": "metrics.calculate_compliance_score", "classification": "operator_only"},
            # RECLASSIFIED 2026-05-15 (Task #103 Phase 3 Commit 2, implementation-
            # discovery override of Gate A MIGRATE verdict): per-month HISTORICAL
            # snapshot (`_period_start = datetime(year, month, 1)` — see
            # compliance_packet.py:210). Canonical compute_compliance_score takes
            # `window_days` as a CURRENT-state rolling lookback ending NOW, which
            # cannot serve arbitrary historical-month queries. Different metric
            # semantics → different metric class. See PLANNED_METRICS.
            # historical_period_compliance_score for the canonical-pending entry.
            {"signature": "compliance_packet.CompliancePacket._calculate_compliance_score", "classification": "operator_only"},
            # RECLASSIFIED 2026-05-15 (Task #103 Phase 3 Commit 3, implementation-
            # discovery override of Gate A MIGRATE verdict): HIPAA-weighted
            # per-category methodology with partial-credit-for-warnings —
            # `score = ((pass + 0.5*warn) / total) * 100` averaged per category
            # then weighted by HIPAA_CATEGORY_WEIGHTS (encryption + access_control
            # higher weight). Canonical compute_compliance_score uses simple
            # `passed/total*100` with NO partial credit and NO per-category
            # HIPAA weighting. Distinct customer-facing methodology — different
            # metric class. See PLANNED_METRICS.category_weighted_compliance_score
            # for the canonical-pending entry.
            {"signature": "db_queries.get_compliance_scores_for_site", "classification": "operator_only"},
            # RECLASSIFIED 2026-05-15 (same Commit 3): batched all-sites variant
            # of get_compliance_scores_for_site — identical methodology, same
            # metric class. Reclassified together.
            {"signature": "db_queries.get_all_compliance_scores", "classification": "operator_only"},
            # RECLASSIFIED 2026-05-15 (Task #103 Gate A): reads denormalized
            # `compliance_scores` table populated by a separate pipeline (per-
            # framework rollup with score_percentage/is_compliant/at_risk/
            # data_completeness_pct), NOT compliance_bundles. Different
            # metric class — see PLANNED_METRICS.per_framework_compliance_score
            # for the canonical per-framework helper-pending entry.
            {"signature": "frameworks.get_compliance_scores", "classification": "operator_only"},
            # RECLASSIFIED 2026-05-15 (Task #103 Gate A): FastAPI endpoint
            # wrapper around frameworks.get_compliance_scores; same per-
            # framework rollup semantics. Reclassified together with #5.
            {"signature": "frameworks.get_appliance_compliance_scores", "classification": "operator_only"},
            {"signature": "prometheus_metrics.*", "classification": "operator_only"},
        ],
        # Display-time None-passthrough: when canonical helper returns
        # {"score": null, "state": "baa_expired"}, the consumer MUST
        # pass-through None and NOT coerce to 100.0. Per Task #50 Gate
        # A P0 #5 + the D1 Gate A canonical-score sentinel finding.
        "display_null_passthrough_required": True,
    },
    "baa_on_file": {
        "canonical_helper": "baa_status.is_baa_on_file_verified",
        "permitted_inline_in_module": "baa_status",
        "allowlist": [
            # RECLASSIFIED 2026-05-15 (Task #103 Phase 3 Commit 4, implementation-
            # discovery override of original `migrate` classification): row-fetch
            # for compliance-letter rendering — returns dict(id, signer_email,
            # signer_name, signed_at, practice_name). Canonical
            # `is_baa_on_file_verified` returns a BOOLEAN state predicate
            # (verified yes/no). Different output SHAPE, but the function
            # already queries the canonical source-of-truth (`baa_signatures`
            # table JOIN `client_orgs` on LOWER(primary_email)) with the same
            # join logic as the canonical helper. Same canonical source, just
            # row-fetch flavor instead of boolean flavor. Letter rendering
            # NEEDS the signer attribution (auditor evidence chain — §164.504
            # documentation). Not a state-check class.
            {"signature": "client_attestation_letter._get_current_baa", "classification": "operator_only"},
            # partner_portfolio_attestation paths to be added once
            # source-grep confirms callsite shape.
        ],
        "display_null_passthrough_required": False,
    },
    "runbook_id_canonical": {
        # Canonical via SQL JOIN: runbooks.agent_runbook_id (mig 284).
        # The substrate invariant runbook_id_drift (assertions.py:2143)
        # catches new drift between agent IDs and backend IDs.
        "canonical_helper": "SQL JOIN via runbooks.agent_runbook_id (mig 284)",
        "already_gated": True,
        # Cite the test that proves the gate (Gate B P1-2).
        "evidence_test": "assertions.py:2143 (substrate invariant runbook_id_drift)",
        "allowlist": [],
    },
    "l2_resolution_tier": {
        # Canonical via write-gate at l2_decision_recorded (mig 300).
        # The substrate invariant l2_resolution_without_decision_record
        # catches new drift.
        "canonical_helper": "write-gate via l2_decision_recorded (mig 300) + substrate invariant l2_resolution_without_decision_record",
        "already_gated": True,
        "evidence_test": "tests/test_l2_resolution_requires_decision_record.py",
        "allowlist": [],
    },
    # `orders_status_completion`: DEFERRED to its own Class-B Gate A.
    # The canonical resolution rule between orders.status='completed'
    # and execution_telemetry.runbook_id confirmation is a substantive
    # engineering question. Per Task #50 Gate A P0 #1 logic, including
    # a TBD entry is itself a Rule 1 violation — so this metric class
    # is absent from this registry until its own Gate A completes.
    # Phase 2 helper — the canonical CTE-JOIN shape for migrating raw
    # discovered_devices readers to canonical_devices. Use this string
    # constant instead of hand-writing 17 copies; prevents CTE-drift
    # class. Gate B will catch any divergence via grep + AST anchor.
    # Pattern: replace `FROM discovered_devices d WHERE site_id = $N`
    # with `FROM dd_freshest_from_canonical($N) d` (illustrative).
    # Callsites with column needs not covered by the CTE add to the
    # SELECT projection in the CTE inline. COUNT-only readers should
    # skip the CTE and read `SELECT COUNT(*) FROM canonical_devices
    # WHERE site_id = $N` directly (3 callsites: partners.py:2595,
    # routes.py:5322, portal.py:1251).
    "category_weighted_compliance_score": {
        # Re-promoted from PLANNED_METRICS to CANONICAL_METRICS 2026-05-16
        # (Task #103 Fork B follow-up). Fork B's audit found 2 customer-
        # facing endpoints leaking this metric class inline — they
        # duplicate the per-category HIPAA-weighted formula on their
        # OWN bundle scans instead of delegating. Registering them as
        # `migrate` surfaces the leaks to the CI gate so they can't
        # drift further; the proper canonical-helper Class-B Gate A
        # (Fork A spec at audit/coach-103-canonical-helper-extension-
        # designs-2026-05-16.md §2) remains the long-term solution.
        #
        # De-facto canonical TODAY: db_queries.get_compliance_scores_
        # for_site + db_queries.get_all_compliance_scores. These both
        # compute `(pass + 0.5*warn) / total * 100` per category and
        # weight by HIPAA_CATEGORY_WEIGHTS. They are classified
        # operator_only because they're the source-of-truth pattern
        # the leak callsites should delegate to.
        "canonical_helper": (
            "db_queries.get_compliance_scores_for_site (de-facto, "
            "pending Fork A spec at audit/coach-103-canonical-helper-"
            "extension-designs-2026-05-16.md §2 for proper extraction "
            "to compute_category_weighted_score helper)"
        ),
        "allowlist": [
            # De-facto canonical callsites — operator_only because
            # they ARE the source-of-truth implementation today.
            {"signature": "db_queries.get_compliance_scores_for_site", "classification": "operator_only"},
            {"signature": "db_queries.get_all_compliance_scores", "classification": "operator_only"},
            # Leak callsites found by Task #103 Fork B audit 2026-05-16
            # — customer-facing endpoints compute the formula inline
            # instead of delegating. Should refactor to either (a) call
            # the de-facto canonical helpers OR (b) extract a shared
            # _compute_category_weighted_scores(cat_pass, cat_fail,
            # cat_warn, *, category_weights) primitive that all 4
            # callsites share. Fork A spec recommends approach (b).
            {"signature": "routes.get_admin_compliance_health", "classification": "migrate"},
            {"signature": "client_portal.get_site_compliance_health", "classification": "migrate"},
        ],
        "display_null_passthrough_required": False,
    },
    "device_count_per_site": {
        # Canonical: canonical_devices table (mig 319, Task #73 Phase 1).
        # Multi-appliance same-(ip,mac) observations collapse via 60s
        # reconciliation loop. Phase 1 (Task #73) migrated
        # compliance_packet._get_device_inventory + device_sync.
        # get_site_devices. Phase 2 (Task #74) drove the raw-FROM-
        # discovered_devices ratchet to 0 across all 19 originally-
        # flagged callsites (test_no_raw_discovered_devices_count.py
        # BASELINE_MAX = 0 as of 2026-05-15).
        "canonical_helper": "canonical_devices table — read DISTINCT canonical_id per site_id",
        "allowlist": [
            # Operator-only callsites — Prometheus + per-appliance audit trail
            {"signature": "prometheus_metrics.*", "classification": "operator_only"},
            {"signature": "appliance_trace.*", "classification": "operator_only"},
            # Substrate invariants — raw reads are by design
            {"signature": "assertions._check_discovered_devices_freshness", "classification": "operator_only"},
            # Write paths — INSERT/UPDATE/DELETE callsites
            {"signature": "device_sync._compute_*", "classification": "write_path"},
            {"signature": "device_sync.merge_*", "classification": "write_path"},
            {"signature": "health_monitor.*owner_appliance*", "classification": "write_path"},
            # DISTINCT-aggregation callsites — already deduping
            {"signature": "sites.py:5090-5116", "classification": "operator_only"},
            # NOTE 2026-05-15 (Task #103 Phase 3 Commit 5 close-out):
            # The previous 19 `migrate`-class line-anchored entries
            # (partners.py / portal.py / client_portal.py / routes.py /
            # sites.py / background_tasks.py) were REMOVED — verification
            # showed: 4 entries had `canonical-migration: device_count_per_site`
            # markers in place (already migrated by Task #74), 1 entry used
            # the canonical CTE (dd_freshest_from_canonical), and 14 entries
            # pointed at stale lines that had shifted past the original
            # location (BUG 1 site_appliances drive-down + Phase 2 close-out
            # rewrote large swaths of those files). The line-anchored
            # ratchet shape is structurally fragile against ongoing
            # refactors — the canonical migration tracking lives in
            # test_no_raw_discovered_devices_count.py BASELINE_MAX (= 0
            # since Task #74 2026-05-15) and the per-line inline
            # `canonical-migration:` markers, NOT in this allowlist.
            # Future device-count canonical drift will be caught by the
            # sibling ratchet, not by re-adding line-anchored entries here.
        ],
        "display_null_passthrough_required": False,
    },
}


# ─────────────────────────────────────────────────────────────────────
# PLANNED_METRICS — metric classes that exist conceptually but do not
# yet have a canonical helper. The CI gate enforces that NO
# customer-facing surface may expose these until the helper lands +
# the metric migrates into CANONICAL_METRICS via a coach-passed PR.
#
# Per Task #50 Gate B P0-B: the 4 metric classes here are NOT in
# CANONICAL_METRICS because the Gate A P0 #1 logic disqualifying
# `orders_status_completion: TBD` applies equally — TBD entries in
# the active registry are themselves Rule 1 violations.
# ─────────────────────────────────────────────────────────────────────

PLANNED_METRICS: Dict[str, Dict[str, str]] = {
    "appliance_liveness": {
        # Counsel Rule 4 + Rule 1 intersection; multi-device-enterprise
        # scale. Intersects with Task #40 D1 backend-verification.
        "canonical_helper_pending": (
            "needs gate on recent heartbeat AND D1 signature_valid "
            "once Task #40 ships"
        ),
        "blocks_until": "Task #40 D1 backend-verify completes",
    },
    "partner_portfolio_score": {
        # Aggregates compliance-score across N customer sites per
        # partner. MUST delegate per-site to compute_compliance_score();
        # aggregate logic only in canonical helper.
        "canonical_helper_pending": (
            "partner-portfolio aggregator delegating per-site to "
            "compute_compliance_score"
        ),
        "blocks_until": "design + Class-B Gate A on partner-portfolio aggregator",
    },
    "evidence_chain_count": {
        # "N evidence bundles in chain" claims on F1/P-F6 letters +
        # auditor kit.
        "canonical_helper_pending": (
            "count helper reading compliance_bundles with "
            "tenant-scoped filter"
        ),
        "blocks_until": "design + Class-B Gate A on chain-count helper",
    },
    "availability_uptime": {
        # "Appliance uptime over period" claims on F3 quarterly +
        # partner reports.
        "canonical_helper_pending": (
            "uptime calculator on appliance_heartbeats with "
            "per-tenant scoping"
        ),
        "blocks_until": "design + Class-B Gate A on uptime calculator",
    },
    "historical_period_compliance_score": {
        # Per-month historical-snapshot score surfaced by the
        # compliance packet PDF generator (compliance_packet.py:448
        # `CompliancePacket._calculate_compliance_score`). Bounds
        # `checked_at >= period_start AND checked_at < period_end`
        # where `_period_start = datetime(year, month, 1)` — a fixed
        # month boundary, NOT a rolling lookback ending NOW.
        #
        # The current compliance_score canonical helper
        # (`compute_compliance_score(conn, site_ids, window_days=N)`)
        # cannot serve this — it queries `checked_at > NOW() -
        # $2::int * INTERVAL '1 day'`. A historical packet generated
        # months after the period would silently return current-state
        # data instead of period-bounded data.
        #
        # Counsel Rule 1 + Auditor: customer-facing PDF surface, must
        # have its own canonical helper. Until that helper lands, the
        # packet generator's existing per-control 2-level averaging
        # methodology stays in place (methodology_version=2.0 in the
        # packet boilerplate) — that text already discloses the
        # specific algorithm so auditors can verify.
        #
        # Task #103 Phase 3 Commit 2 (2026-05-15) — implementation-
        # discovery finding overriding the original Gate A MIGRATE
        # verdict (the fork's NEAR-canonical observation missed the
        # period-bounded vs rolling-window semantic mismatch).
        "canonical_helper_pending": (
            "extend compute_compliance_score with optional "
            "(period_start, period_end) parameters OR add a sibling "
            "compute_period_compliance_score helper sharing the same "
            "DISTINCT-ON-(site,check,host) latest-per-key shape with "
            "period bounds substituted for window_days"
        ),
        "blocks_until": (
            "design + Class-B Gate A on period-bounded canonical "
            "helper; methodology-version cohort plan for any packet "
            "score formula change"
        ),
    },
    # NOTE 2026-05-16: `category_weighted_compliance_score` was promoted from
    # PLANNED_METRICS to CANONICAL_METRICS following Task #103 Fork B leak
    # audit — see the entry in CANONICAL_METRICS above. The de-facto
    # canonical (db_queries.get_compliance_scores_for_site +
    # get_all_compliance_scores) is the current source of truth; Fork A's
    # spec (audit/coach-103-canonical-helper-extension-designs-2026-05-16.md
    # §2) designs the proper canonical helper extraction that the 2 leak
    # callsites will eventually delegate to.
    "per_framework_compliance_score": {
        # Per-framework score_percentage / is_compliant / at_risk /
        # data_completeness_pct surfaced by /api/frameworks/appliances/
        # {appliance_id}/scores. Reads denormalized `compliance_scores`
        # table (separate pipeline from compliance_bundles). Today's
        # implementation lives in frameworks.get_compliance_scores +
        # get_appliance_compliance_scores — those are classified
        # `operator_only` in CANONICAL_METRICS.compliance_score to
        # prevent confusion with the bundle-aggregator class, but the
        # PER-FRAMEWORK metric class itself needs a canonical helper
        # before any new customer-facing surface can expose it.
        # Counsel's ask (Task #103 Gate A 2026-05-15): "Don't just
        # hide it — register it."
        "canonical_helper_pending": (
            "per-framework canonical helper that delegates to "
            "compliance_scores rollup table OR computes from "
            "evidence_framework_mappings on-the-fly with parity guard"
        ),
        "blocks_until": "design + Class-B Gate A on per-framework helper",
    },
}


def get_metric_classes() -> List[str]:
    """Return the union of CANONICAL_METRICS + PLANNED_METRICS keys.
    Used by the CI gate to validate per-line `canonical-migration`
    markers reference a known metric class.
    """
    return sorted(list(CANONICAL_METRICS.keys()) + list(PLANNED_METRICS.keys()))


def get_canonical_helper(metric_class: str) -> str:
    """Lookup the canonical helper for a metric class. Raises KeyError
    for metric classes that are in PLANNED_METRICS (no helper yet) or
    not in any registry.
    """
    if metric_class not in CANONICAL_METRICS:
        if metric_class in PLANNED_METRICS:
            raise KeyError(
                f"metric class {metric_class!r} is in PLANNED_METRICS — "
                f"no canonical helper yet. "
                f"Pending: {PLANNED_METRICS[metric_class]['canonical_helper_pending']}"
            )
        raise KeyError(f"unknown metric class: {metric_class!r}")
    return CANONICAL_METRICS[metric_class]["canonical_helper"]
