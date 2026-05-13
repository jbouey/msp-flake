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
            {"signature": "metrics.calculate_compliance_score", "classification": "migrate"},
            {"signature": "compliance_packet.ComplianceReport._calculate_compliance_score", "classification": "migrate"},
            {"signature": "db_queries.get_compliance_scores_for_site", "classification": "migrate"},
            {"signature": "db_queries.get_all_compliance_scores", "classification": "migrate"},
            {"signature": "frameworks.get_compliance_scores", "classification": "migrate"},
            {"signature": "frameworks.get_appliance_compliance_scores", "classification": "migrate"},
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
            {"signature": "client_attestation_letter._get_baa_signature_row", "classification": "migrate"},
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
