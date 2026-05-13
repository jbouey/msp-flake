# Canonical-Source Registry — Design Draft v3 (Task #50, Counsel Priority #4, Rule 1)

> **v3 changes (Gate B APPROVE-WITH-FIXES, 2 P0s + 4 P1s applied 2026-05-13):**
> **P0-A:** Phase 2 substrate invariant `canonical_metric_drift` reframed from display-time-vs-fresh-helper-recompute (proves nothing — same helper, same data) to **display-time-vs-chain-time** using the most recent signed `compliance_bundles` row as the comparison source. This satisfies Article 3.2 cryptographic-attestation-chain claim without double-building. **P0-B:** Moved 4 TBD entries (`appliance_liveness`, `partner_portfolio_score`, `evidence_chain_count`, `availability_uptime`) from `CANONICAL_METRICS` to `PLANNED_METRICS` — TBD entries in the active registry are themselves Rule 1 violations (same shape as the `orders_status_completion` disqualification). **P1-1:** §7 numbering fixed (the second §7 is now §8). **P1-2:** Added `evidence_test:` field to `already_gated` entries citing the proof test. **P1-3:** Collapsed `non_canonical_function_signatures` + `operator_only_modules` into single `allowlist` with `classification` field per entry. **P1-4:** Documented `canonical_metrics.py` as a named lockstep-peer.
>
> **v2 changes (Gate A APPROVE-WITH-FIXES, 5 P0s applied 2026-05-13):**
> (1) Dropped `orders_status_completion: TBD` entry — TBD entries are themselves Rule 1 violations; deferred to its own Gate A with the canonical resolution rule. (2) Promoted substrate invariant `canonical_metric_drift` from Phase 5 → Phase 2 (parallel with CI gate; static AST gate alone is not Article 3.2 attestation-grade). (3) Replaced `(file, line)` tuples with function-name + AST-node match — line numbers drift within sprints. (4) Expanded scope: added `appliance_liveness`, `partner_portfolio_score`, `evidence_chain_count`, `availability_uptime` metric_classes (NOTE: moved to `PLANNED_METRICS` in v3). (5) CI-asserted per-tenant correctness — every customer-facing endpoint passes tenant-scoped `site_ids` derived from authenticated principal; display-time `None`-passthrough required for the `score=null` BAA-expired sentinel.


> **Counsel Rule 1 (gold authority, 2026-05-13):** *"No non-canonical metric leaves the building. Every exposed metric must have a declared canonical source. Anything non-canonical gets hidden or explicitly marked non-authoritative. No deck, dashboard, postcard, or auditor artifact may compute against convenience tables."*

> **Counsel-cited concrete examples of broken truth paths:** `runbook_id` (agent uses internal IDs that differ from backend IDs), L2 resolution recording (mig 300 + ghost-L2 audit gap), `orders.status` completion state.

> **Multi-device-enterprise lens:** at N customers / M appliances, a non-canonical metric is N×M false claims simultaneously. The credibility risk scales linearly with the fleet. Canonical-source enforcement at multi-tenant scale is one of the three weak areas counsel rated as audit-attack surface.

---

## §1 — Scope: customer-facing metrics

A metric is "customer-facing" if it appears in any of:

- **F-series printable artifacts** — F1 Compliance Attestation Letter, F2 PO Designation, F3 Quarterly Summary, F4 verify-endpoint, F5 Wall Certificate, P-F5..P-F9 partner series
- **Client portal** — dashboard, reports, compliance-health views, evidence chain views
- **Partner portal** — partner-portfolio attestation, per-site drill-down
- **Auditor kit** — downloadable evidence ZIP
- **External-API responses** — `/api/version`, `/api/client/*` endpoints, `/verify/{hash}` public-verify
- **Webhooks + email + SMS notifications** — anywhere customer-org-state is asserted
- **Operator deck / marketing copy** — pricing pages, "as audited" claims, customer-success references

A metric is "internal" if it appears ONLY in admin-substrate dashboards (`/admin/substrate-health`, internal Prometheus, operator runbooks). Internal metrics are out of scope for Rule 1 (counsel: "internal noise is fine; discoverable representations are the risk").

---

## §2 — Current state — enumeration of customer-facing metric paths

### A. Compliance score

**Canonical helper:** `compliance_score.compute_compliance_score()` at `compliance_score.py:157`.

| Path | File:line | Status |
|---|---|---|
| `compute_compliance_score()` canonical | `compliance_score.py:157` | ✅ CANONICAL |
| `client_portal.py:759` consumes canonical | `client_portal.py:759` | ✅ DELEGATES |
| `client_portal.py:1197` consumes canonical | `client_portal.py:1197` | ✅ DELEGATES |
| `client_portal.py:1854` consumes canonical | `client_portal.py:1854` | ✅ DELEGATES |
| `client_attestation_letter.py:199` consumes canonical | `client_attestation_letter.py:199` | ✅ DELEGATES |
| `metrics.py:191 calculate_compliance_score()` | `metrics.py:191` | ❓ POTENTIALLY LEGACY — investigate callers |
| `compliance_packet.py:437 _calculate_compliance_score()` | `compliance_packet.py:437` | ❓ INLINE COMPUTATION — likely needs delegation |
| `db_queries.py:502` inline `comp_passed / max(comp_total, 1) * 100` | `db_queries.py:502` | ❌ INLINE — must delegate OR be marked non-authoritative |
| `db_queries.py:606 get_compliance_scores_for_site()` | `db_queries.py:606` | ❓ POTENTIALLY LEGACY |
| `db_queries.py:832 get_all_compliance_scores()` | `db_queries.py:832` | ❓ POTENTIALLY LEGACY |
| `frameworks.py:216 get_compliance_scores()` | `frameworks.py:216` | ❓ POTENTIALLY LEGACY |
| `frameworks.py:425 get_appliance_compliance_scores()` | `frameworks.py:425` | ❓ POTENTIALLY LEGACY |

### B. BAA on file (newly canonical today 2026-05-13)

**Canonical helper:** `baa_status.is_baa_on_file_verified()` at `baa_status.py:51` (created today).

| Path | File:line | Status |
|---|---|---|
| `is_baa_on_file_verified()` canonical | `baa_status.py:51` | ✅ CANONICAL (today) |
| `audit_report.py` consumes canonical | `audit_report.py:200+` | ✅ DELEGATES (today) |
| `client_portal.py:5786` row-derived `baa_dated_at IS NOT NULL` | `client_portal.py:5786` | ✅ ROW-DERIVED (today, no longer hardcoded) |
| `client_attestation_letter.py` queries `baa_signatures` JOIN `client_orgs` | `client_attestation_letter.py:121` | ❓ Does NOT consume helper; needs migration |
| `partner_portfolio_attestation.py` — any BAA-on-file claim | unverified | ❓ Needs scan |
| `partner_baa_roster` table-derived claim | unverified | ❓ Needs scan |

### C. Drift-check / pass-fail counts

**Canonical helper (proposed):** TBD. Counsel cited the broader pattern; we need a canonical helper.

| Path | Status |
|---|---|
| Compliance score consumes pass/fail counts indirectly via canonical helper | ✅ |
| Direct pass/fail count queries in customer-facing endpoints | ❓ Needs enumeration |

### D. Runbook_id mapping (counsel-cited broken truth)

**Truth state per CLAUDE.md:** Agent uses internal IDs (L1-SVC-DNS-001) that differ from backend IDs (RB-AUTO-SERVICE_). Migration 284 added `agent_runbook_id` column + backfill. Substrate invariant catches new drift (assertions.py:2143).

| Path | Status |
|---|---|
| Migration 284 reconciliation column | ✅ ACTIVE |
| Substrate invariant `runbook_id_drift` | ✅ ACTIVE (caught RT-DM Issue #1) |
| Customer-facing surfaces use `agent_runbook_id` consistently | ❓ Needs scan |

### E. L2 resolution tier (counsel-cited)

**Truth state per CLAUDE.md (Session 219 mig 300):** `resolution_tier='L2'` requires `l2_decision_recorded` gate. Substrate invariant `l2_resolution_without_decision_record` (sev2) catches violations. 26 historical orphans backfilled via mig 300.

| Path | Status |
|---|---|
| L2 decision gate at write time | ✅ ENFORCED (Session 219) |
| Substrate invariant catches orphans | ✅ ACTIVE |
| Customer-facing L2 count metrics | ❓ Should pass-through the gate-verified count; needs scan |

### F. Orders.status completion state (counsel-cited — DEFERRED to own Gate A)

**Truth state per CLAUDE.md:** `execution_telemetry.runbook_id` + `orders.status` are independent state machines that can disagree.

**v2 status (Gate A P0 #1 fix):** the canonical resolution rule between `orders.status='completed'` and `execution_telemetry.runbook_id` confirmation is itself a substantive engineering question that requires its own Class-B Gate A. Including a `TBD` entry in this registry is itself a Rule 1 violation (a non-canonical state being represented as registry-tracked). This metric class is **deferred** to a separate work item; will be added to the registry once the canonical resolution rule lands.

### G. Appliance liveness (added in v2 per Gate A P0 #4)

**Truth state:** appliance "online" claims at multi-device-enterprise scale are as load-bearing as compliance-score claims. Counsel Rule 4 (orphan coverage) intersects with Rule 1 (canonical truth) here.

**Canonical helper (proposed):** TBD — currently `appliance_heartbeats.last_seen_at` is the de-facto source; needs explicit canonical helper that gates on (a) recent heartbeat AND (b) D1 signature_valid (once Task #40 ships).

### H. Partner portfolio score (added in v2 per Gate A P0 #4)

**Truth state:** partner-portfolio attestation (P-F5) aggregates compliance-score across N customer sites per partner. At multi-tenant scale (N customers × M sites per partner), portfolio-level non-canonical computation is a Rule 1 violation per partner.

**Canonical helper (proposed):** partner-portfolio aggregator that delegates per-site to `compute_compliance_score()`; aggregate logic in canonical helper.

### I. Evidence chain count (added in v2 per Gate A P0 #4)

**Truth state:** "N evidence bundles in chain" claims surface on F1/P-F6 letters and the auditor kit.

**Canonical helper (proposed):** count helper that reads `compliance_bundles` with tenant-scoped filter; never inline-computed in F-series templates.

### J. Availability uptime (added in v2 per Gate A P0 #4)

**Truth state:** "appliance uptime over period" claims surface on F3 quarterly summary and partner-side reports.

**Canonical helper (proposed):** uptime calculator that reads `appliance_heartbeats` with explicit per-tenant scoping and the same canonical-recent-heartbeat gate as appliance_liveness.

---

## §3 — Proposed canonical-source registry

A single module `mcp-server/central-command/backend/canonical_metrics.py` declares the canonical helper for each customer-facing metric class, plus the list of permitted alternate paths:

```python
# canonical_metrics.py — Rule 1 enforcement
#
# Each entry: metric_class → canonical_helper + permitted_callers.
# CI gate (test_canonical_metrics_registry.py) verifies:
#   1. Every callsite of a non-canonical computation is in permitted_callers
#      AND has a delegation comment pointing at the canonical helper, OR
#   2. The callsite is in OPERATOR_ONLY_PATHS (substrate-internal use only,
#      never reaches customer-facing surfaces).

# CANONICAL_METRICS = entries with a canonical helper. Gate enforces.
# PLANNED_METRICS = entries with no canonical helper yet. Gate enforces
# only that no customer-facing surface exposes them until they migrate
# into CANONICAL_METRICS.

CANONICAL_METRICS = {
    "compliance_score": {
        "canonical_helper": "compliance_score.compute_compliance_score",
        "permitted_inline_in_module": "compliance_score",  # helper itself
        # Single allowlist per Gate B P1-3 — collapses
        # `non_canonical_function_signatures` + `operator_only_modules`
        # into one list with explicit classification per entry.
        # Function-name + AST-node match (Gate A P0 #3); never
        # (file, line) tuples.
        "allowlist": [
            {"signature": "metrics.calculate_compliance_score", "classification": "migrate"},
            {"signature": "compliance_packet.ComplianceReport._calculate_compliance_score", "classification": "migrate"},
            {"signature": "db_queries.get_compliance_scores_for_site", "classification": "migrate"},
            {"signature": "db_queries.get_all_compliance_scores", "classification": "migrate"},
            {"signature": "frameworks.get_compliance_scores", "classification": "migrate"},
            {"signature": "frameworks.get_appliance_compliance_scores", "classification": "migrate"},
            {"signature": "prometheus_metrics.*", "classification": "operator_only"},
        ],
        # Display-time None-passthrough (Gate A P0 #5): when canonical
        # helper returns {"score": null, "state": "baa_expired"}, the
        # consumer MUST pass-through None and NOT coerce to 100.0.
        "display_null_passthrough_required": True,
    },
    "baa_on_file": {
        "canonical_helper": "baa_status.is_baa_on_file_verified",
        "permitted_inline_in_module": "baa_status",
        "allowlist": [
            {"signature": "client_attestation_letter._get_baa_signature_row", "classification": "migrate"},
            # partner_portfolio_attestation paths added once source-grep confirms
        ],
        "display_null_passthrough_required": False,
    },
    "runbook_id_canonical": {
        "canonical_helper": "(SQL JOIN via runbooks.agent_runbook_id column, mig 284)",
        "already_gated": True,
        # Gate B P1-2: cite the evidence test that proves the gate.
        "evidence_test": "assertions.py:2143 (substrate invariant runbook_id_drift)",
        "allowlist": [],
    },
    "l2_resolution_tier": {
        "canonical_helper": "(write-gate via l2_decision_recorded; substrate invariant l2_resolution_without_decision_record sev2)",
        "already_gated": True,
        "evidence_test": "tests/test_l2_resolution_requires_decision_record.py",
        "allowlist": [],
    },
    # `orders_status_completion`: DEFERRED to own Gate A per §2.F.
    # Not present in this registry — TBD entries are themselves Rule 1
    # violations.
}

# PLANNED_METRICS — designed but no canonical helper yet. Gate enforces:
# NO customer-facing surface may expose these until the helper lands +
# the metric migrates into CANONICAL_METRICS via a coach-passed PR.
PLANNED_METRICS = {
    "appliance_liveness": {
        # Counsel Rule 4 + Rule 1 intersection; multi-device-enterprise
        # scale; intersects with Task #40 D1 backend-verification.
        "canonical_helper_pending": "needs gate on recent heartbeat AND D1 signature_valid once Task #40 ships",
        "blocks_until": "Task #40 D1 backend-verify completes",
    },
    "partner_portfolio_score": {
        # Aggregates compliance-score across N customer sites per partner.
        # MUST delegate per-site to compute_compliance_score(); aggregate
        # logic only in canonical helper.
        "canonical_helper_pending": "partner-portfolio aggregator delegating per-site",
        "blocks_until": "design + Class-B Gate A on partner-portfolio aggregator",
    },
    "evidence_chain_count": {
        # "N evidence bundles in chain" claims on F1/P-F6 letters + auditor kit.
        "canonical_helper_pending": "count helper reading compliance_bundles with tenant-scoped filter",
        "blocks_until": "design + Class-B Gate A on chain-count helper",
    },
    "availability_uptime": {
        # "Appliance uptime over period" claims on F3 quarterly + partner reports.
        "canonical_helper_pending": "uptime calculator on appliance_heartbeats with per-tenant scoping",
        "blocks_until": "design + Class-B Gate A on uptime calculator",
    },
}
```

**Gate B P1-4 — lockstep-peer documentation:** `canonical_metrics.py` becomes a named lockstep-peer alongside:
- `fleet_cli.PRIVILEGED_ORDER_TYPES`
- `privileged_access_attestation.ALLOWED_EVENTS`
- migration `v_privileged_types`
- `flywheel_state.EVENT_TYPES`
- `BAA_GATED_WORKFLOWS` (once Task #52 ships)
- `BACKEND_THIRD_PARTY_INTEGRATIONS` (once Task #55 §3 ships)
- This `CANONICAL_METRICS` + `PLANNED_METRICS` constant

Pattern: any change to the constants in this file requires lockstep with the CI gate + substrate invariant + customer-facing surface enumeration.

### Per-tenant correctness assertion (Gate A P0 #5 fix)

Every customer-facing endpoint that returns a canonical metric MUST:
1. Derive `site_ids` from the authenticated principal (auth context), NOT from request body.
2. Pass tenant-scoped `site_ids` to the canonical helper.
3. CI-assert this via `test_canonical_metric_endpoints_tenant_scoped.py` — an AST gate that detects metric-returning endpoints and verifies they extract `site_ids` from the auth principal pattern (e.g. `Depends(require_client_user)` → `current_user.client_org_id` → `site_ids_for_org(client_org_id)`), never from a request-body field that could be a forgery vector.

## §4 — Multi-device-enterprise lens — fleet-wide metric guarantees

At multi-tenant scale, the registry's load-bearing claim is: **for any customer-facing metric M, the value returned for tenant T is computed by the canonical helper using ONLY data tagged with tenant T.** This is per-tenant correctness on top of canonical-source enforcement.

Engineering implications:
- The CI gate must reject any path that computes a customer-facing metric WITHOUT going through the canonical helper.
- The substrate invariant must detect drift between the canonical helper's output and any cached/snapshot value.
- Per-tenant correctness is verified by the existing RLS posture (`tenant_org_isolation` + per-tenant pool patterns).

## §5 — CI gate design — `test_canonical_metrics_registry.py`

```python
# Skeleton (final implementation TBD post-Gate-A approval)

import ast
import pathlib

from canonical_metrics import CANONICAL_METRICS

def test_no_inline_score_computation_outside_canonical():
    """For every metric class in CANONICAL_METRICS, verify that all
    inline computations live in `permitted_inline_paths` (the helper
    itself) OR `operator_only_paths` (substrate-internal). Any callsite
    that computes inline outside those allowlists fails the gate.
    """
    backend = pathlib.Path(__file__).resolve().parent.parent
    violations = []
    for metric_class, spec in CANONICAL_METRICS.items():
        # AST scan for inline patterns (e.g. `passed / total * 100`)
        # vs. helper-call patterns. Concrete pattern set per metric_class.
        ...
    assert not violations, (
        f"Rule 1 (canonical-source) violations:\n  " + "\n  ".join(violations)
    )

def test_customer_facing_endpoints_use_canonical_helper():
    """For every endpoint that returns customer-facing JSON, verify
    that any metric in CANONICAL_METRICS is sourced via the canonical
    helper or via a database column whose canonical-helper-derived
    value was the source-of-truth at write time.
    """
    ...

def test_non_canonical_paths_have_migration_marker():
    """Paths listed under `non_canonical_paths_to_migrate` MUST have
    an inline comment `# canonical-migration: <metric_class>` AND
    a TaskCreate followup. Without these markers, the path is in
    indeterminate state and gate fails.
    """
    ...
```

The gate ships with **ratchet**: today's violations are recorded as baseline; new violations fail the gate; baseline violations must drive to zero via named TaskCreate followups.

## §6 — Implementation order (post-Class-B Gate A; phased per v2)

**Phase 0:** ship `canonical_metrics.py` registry constant (§3).

**Phase 1:** ship `test_canonical_metrics_registry.py` CI gate + `test_canonical_metric_endpoints_tenant_scoped.py` per-tenant correctness gate. Ratchet at today's count via frozen-baseline pattern (mirror `NOQA_BASELINE_MAX = 6` precedent in `test_no_direct_site_id_update.py`).

**Phase 2 (promoted from Phase 5 per Gate A P0 #2; reframed per Gate B P0-A):** ship substrate invariant `canonical_metric_drift` (sev2) — periodically compares customer-facing endpoint responses against the **chain-time attested value** (the most recent signed `compliance_bundles` row for that metric class + tenant). This is NOT a display-vs-fresh-helper-recompute comparison (same helper, same data = same answer — proves nothing). Instead, it's a display-vs-chain-time comparison: when a customer-facing surface returns a metric, the invariant verifies it matches the value attested in the most recent Ed25519-signed evidence bundle. Cryptographic-attestation-chain claim materialized per master BAA Article 3.2 without double-building (the chain already exists; the invariant reads its head). This MUST land BEFORE drive-down begins so runtime detection is available DURING the migration window. Static AST gate alone is not Article 3.2 attestation-grade.

**Phase 3:** drive-down — migrate `non_canonical_function_signatures` callsites one-by-one to delegate to canonical helper. Each migration is a small PR with required `# canonical-migration: <metric_class> — <reason>` inline marker; coach pass on each; per-line marker enables incremental ratchet decrement. Realistic timeline: 3-5 sprints (per Gate A PM finding).

**Phase 4:** scope expansion. Once Tasks #40 (D1 backend-verify) and #52 (BAA-gated workflows) close, the TBD canonical helpers for `appliance_liveness` and `partner_portfolio_score` + `availability_uptime` + `evidence_chain_count` land and join the registry.

**Phase 5:** (deferred per v2 §2.F) Add `orders_status_completion` metric_class once its own Gate A on the canonical resolution rule completes.

## §7 — Open questions for Class-B Gate B (post-v2)

- (a) Is `compliance_score.compute_compliance_score()` truly the only canonical path, or are there legacy 3rd-party-API-shaped endpoints (e.g. `frameworks.py:216 get_compliance_scores()`) that need their own canonical helper or are subsumed by the existing one? Source-grep needed to enumerate the call-shape of the listed `non_canonical_function_signatures`.
- (b) For each `(file:legacy_path)` in `non_canonical_function_signatures`: does it (i) need to delegate to canonical helper, (ii) get marked `operator_only` (substrate-internal use only), or (iii) get deleted as dead code?
- (c) v2 §2.C "Drift-check / pass-fail counts" — does this need to be a separate metric class in the registry, or is it subsumed by `compliance_score` (since the score is computed from pass/fail counts)?
- (d) The phase-2 substrate invariant `canonical_metric_drift` — does it sample customer-facing endpoint responses via in-process function calls (cheap, no network) or via real HTTP requests against the live API (more auditor-grade but adds load)?

## §8 — REJECTED proposals (per Gate A)

- **v1 §7(e) per-computation audit-log emission** — REJECTED by Gate A as double-build vs the existing `compliance_bundles` Ed25519 chain (which is already evidence of every customer-facing metric). Not added to v2.

— Engineering, on behalf of Counsel Priority #4 / Rule 1
   2026-05-13
