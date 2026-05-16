# Sessions 219-220 (2026-05-09 — 2026-05-16)

Two themes: (1) **enterprise-grade ratchet hardening** — 6 CI baselines driven
to 0 across the codebase; (2) **Counsel Rule 1 close-out** — canonical-source
registry shipped, leak audits, registry-integrity gates, and a structural
finding that "MIGRATE" Gate-A verdicts can be implementation-discovery-
overridden when sub-loops reveal distinct metric classes.

---

## Session 219 (2026-05-09 — 2026-05-11)

### L2-resolution audit gap closure (mig 300, Task #104)

Substrate invariant `l2_resolution_without_decision_record` (sev2) caught
26 north-valley-branch-2 incidents tagged `resolution_tier='L2'` with no
matching `l2_decisions` row — a ghost-L2 audit gap violating the data
flywheel + attestation chain.

Root cause: `agent_api.py:1338` + `main.py:4530` both swallowed
`record_l2_decision()` exceptions and continued setting
`resolution_tier='L2'` anyway. Fix: introduce `l2_decision_recorded: bool`
set inside the `try` block immediately after the record call. Gate
refuses to set L2 without the audit row — escalates to L3 instead.

Backfill mig 300 inserts synthetic `l2_decisions` rows with
`pattern_signature='L2-ORPHAN-BACKFILL-MIG-300'` + `llm_model='backfill_
synthetic'` so auditors can distinguish historical from prospective.

Rule: NEVER set `resolution_tier='L2'` (Python literal OR SQL UPDATE)
without an `l2_decision_recorded` reference within 80 lines above.
Pinned by `tests/test_l2_resolution_requires_decision_record.py` (3 tests:
source-walk + positive + negative control).

### Privileged-chain extension: delegate_signing_key (mig 305)

Weekly audit cadence found `appliance_delegation.py:258 POST
/delegate-key` was zero-auth — anyone could mint an Ed25519 signing key
bound to any caller-supplied appliance_id, then sign evidence chain
entries. Functionally equivalent to `signing_key_rotation` which was
already privileged.

Added to all 3 lockstep lists: `fleet_cli.PRIVILEGED_ORDER_TYPES`,
`privileged_access_attestation.ALLOWED_EVENTS`, mig 305
`v_privileged_types`. Plus Python-only allowlist entry in
`tests/test_privileged_order_four_list_lockstep.py::PYTHON_ONLY` (backend-
only — daemon never receives it as a fleet_order).

Prod audit at fix time: 1 historical row in `delegated_keys`, synthetic
test data, already expired — zero customer exposure.

### L1 escalate-action false-heal class

9 builtin Go rules in `appliance/internal/healing/builtin_rules.go` use
`Action: "escalate"`. Pre-fix the daemon's escalate handler
(`healing_executor.go:92`) returned `{"escalated": true, "reason": ...}`
with NO `"success"` key. `l1_engine.go:328` defaulted
`result.Success = true`. `daemon.go:1706` hardcoded `"L1"` in
`ReportHealed`. Backend `main.py:4870` persisted daemon-supplied tier
without server-side check.

Net effect: **1,137 prod L1-orphans** across 3 chaos-lab check_types
(rogue_scheduled_tasks 510, net_unexpected_ports 404,
net_host_reachability 223) over 90 days.

Two-layer fix shipped:
- **Layer 1 daemon** (`healing_executor.go:106-110` explicit `success:
  false` on escalate + `l1_engine.go:335-350` fail-closed defaults on
  BOTH `missing-key` AND `output==nil` paths).
- **Layer 2 backend** (`main.py:4870` downgrades `resolution_tier='L1' →
  'monitoring'` when `check_type in MONITORING_ONLY_CHECKS`).

Substrate invariant `l1_resolution_without_remediation_step` (sev2)
detects regressions. Go AST ratchet
`appliance/internal/daemon/action_executor_success_key_test.go`
enumerates every case in `makeActionExecutor` switch + requires explicit
`"success":` key OR trusted helper.

Commit-order rule: backend Layer 2 ships FIRST (live in ~5min) as
safety net for the asynchronous daemon fleet-update window
(hours/days). Mig 306 backfill (1,137 rows L1→L3/monitoring per class)
requires its OWN Gate A — Maya §164.528 retroactive PDF impact deep-dive
(task #117).

### Substrate per-assertion `admin_transaction` cascade-fail closure (`57960d4b`)

Pre-fix the Substrate Integrity Engine held ONE `admin_connection` for
all 60+ assertions per 60s tick. One `asyncpg.InterfaceError` poisoned
the conn — every subsequent assertion in the tick blinded. Defensive
`conn_dead` flag from commit `b55846cb` was a band-aid masking the
cascade-fail class.

Fix: per-assertion `admin_transaction(pool)` blocks at
`assertions.py::run_assertions_once`. One InterfaceError costs 1
assertion (1.6% tick fidelity), not all 60+ (100%). `_ttl_sweep` moves
to its OWN independent `admin_transaction` block — removed the
`if errors == 0` short-circuit that silently dropped sigauth reclaim on
any tick with even one transient error. CI gate
`tests/test_assertions_loop_uses_admin_transaction.py` (5 tests) pins
the design. Runtime verified post-deploy: 5 consecutive ticks logged
`errors=0 sigauth_swept=3`.

### Adversarial 2nd-eye review locked-in (TWO-GATE protocol)

Locked in 2026-05-11: any new system / deliverable / design doc / soak /
chaos run MUST receive a fork-based 4-lens adversarial review (Steve /
Maya / Carol / Coach) BEFORE execution OR completion. The fork runs via
`Agent(subagent_type="general-purpose")` with a fresh context window — the
author CANNOT play the lenses themselves.

Two-gate refinement also locked in: review runs at BOTH gates per
deliverable — **Gate A (pre-execution)** + **Gate B (pre-completion)**.
Both fork-based. Both demand a written verdict at
`audit/coach-<topic>-<gate>-YYYY-MM-DD.md`. P0 findings from EITHER
gate must close before advancing; "acknowledged / deferred to v2" does
not satisfy a P0 unless the deferral itself passes a Gate B fork
review.

Full mechanics: `feedback_round_table_at_gates_enterprise.md` +
`feedback_consistency_coach_pre_completion_gate.md`.

---

## Session 220 (2026-05-12 — 2026-05-16)

### Master BAA v1.0-INTERIM shipped (Task #56)

The platform's foundational legal exposure — counsel-grade HIPAA-core
compliance instrument derived from 45 CFR §164.504(e)(1) sample BAA —
became binding on customers via e-signature in the OsirisCare signup
flow. `docs/legal/MASTER_BAA_v1.0_INTERIM.md`, effective 2026-05-13,
decay-after 14 days (interim), superseded by counsel-hardened v2.0
target 2026-06-03.

Engineering preconditions for v2.0 drafting documented at
`docs/legal/v2.0-hardening-prerequisites.md` — PRE-1: no per-event /
per-heartbeat / "continuously verified" verification language unless
D1 heartbeat-signature backend verification has ≥7-day clean soak
(≥99% `signature_valid IS TRUE` per pubkeyed appliance, zero open
`daemon_heartbeat_signature_{unverified,invalid,unsigned}` violations).
v1.0-INTERIM does NOT over-claim — every signed-claim scopes to
evidence bundles. CI backstop `tests/test_baa_artifacts_no_heartbeat_
verification_overclaim.py` (baseline 0) pins that scoping.

### BAA enforcement 3-list lockstep (Tasks #52, #91, #92, #98, #99, #90, #97)

Counsel Rule 6 machine-enforcement. Triad:

- **List 1** = `baa_enforcement.BAA_GATED_WORKFLOWS` (active:
  `owner_transfer`, `cross_org_relocate`, `evidence_export`, +
  `new_site_onboarding` + `new_credential_entry`)
- **List 2** = enforcing callsites — `require_active_baa(workflow)`
  factory for client-owner context, `enforce_or_log_admin_bypass(...)`
  for admin carve-out path (logs `baa_enforcement_bypass` to
  `admin_audit_log`, never blocks), `check_baa_for_evidence_export(_auth,
  site_id)` for method-aware auditor-kit branches
- **List 3** = `sensitive_workflow_advanced_without_baa` sev1 substrate
  invariant (`assertions.py`)

CI gate `tests/test_baa_gated_workflows_lockstep.py` pins List 1 ↔
List 2. `auditor_kit_download` audit rows denormalize `site_id` +
`client_org_id` at write time (evidence_chain.py) so the invariant SQL
skips the JOIN.

Predicate `baa_status.baa_enforcement_ok()` is DELIBERATELY SEPARATE
from `is_baa_on_file_verified()` — does NOT require
`client_orgs.baa_on_file=TRUE` (demo posture is FALSE everywhere;
reusing it would block every org on deploy). `baa_version` comparison
is numeric (`_parse_baa_version` tuple) NOT lexical — v10.0 > v2.0
holds.

`_DEFERRED_WORKFLOWS` (intentionally NOT gated): `partner_admin_
transfer` (Task #90 Gate A 2026-05-15 confirmed via Counsel
§164.504(e) test: zero PHI flow, partner-internal role swap). `ingest`
(Exhibit C "pending inside-counsel verdict", Task #37 counsel queue).
Cliff 2026-06-12; all 5 active workflows now have both build-time
(lockstep CI gate) + runtime (substrate invariant scan) coverage.

### Synthetic-site MTTR soak ground-truth (Task #66 B1)

Mig 315 introduced `sites.synthetic BOOLEAN NOT NULL DEFAULT FALSE` +
mig 323 added `substrate_violations.synthetic BOOLEAN NOT NULL DEFAULT
FALSE` with derived-at-INSERT-time marker (`$3 LIKE 'synthetic-%'` in
`assertions.py::run_assertions_once`). The synthetic site is
`status='inactive'` quarantined; substrate engine MUST tick on it for
ground-truth verification (noqa-allowlisted at 2 invariant scans), but
customer-facing readers filter `synthetic IS NOT TRUE`.

Universal-filter ratchet at `tests/test_mttr_soak_filter_universality.
py` hard-locked at 0 (was 19 mid-session) after extending exclusion-
pattern recognizer (column-to-column joins like `client_org_id = co.id`,
correlated subqueries, `{where_clause}` f-string sigils, `NOT LIKE
'synthetic-%'`) + adding real filters at routes.py:3338
(fleet-posture site_incidents CTE) + background_tasks.py:1294
(recurrence_velocity flywheel).

Defense-in-depth pins:
- `tests/test_auditor_kit_refuses_synthetic_site.py` —
  download_auditor_kit endpoint refuses synthetic-site to non-admin
  callers (opaque 404, no `synthetic` word in detail)
- `tests/test_l2_orphan_invariant_includes_synthetic_sites.py` — L2
  invariant SQL must NOT filter `synthetic IS NOT TRUE` (positive
  control); substrate_violations INSERT MUST derive `synthetic` inline
  from `$3 LIKE 'synthetic-%'`

### BUG 1 site_appliances ratchet 81 → 0 (Tasks #66/74 close + drive)

`tests/test_no_unfiltered_site_appliances_select.py` BASELINE_MAX
driven from 81 (post-fix floor) to 0 via systematic close-out:
- **Marker sweep** on operator-only / forensic / audit files
  (db_delete_safety_check, prometheus_metrics, retention_verifier,
  chain_tamper_detector, audit_package, ops_health, fleet_updates,
  evidence_chain, provisioning, agent_api, flywheel_state, mesh_targets,
  frameworks, cve_watch, db_queries, protection_profiles — 16+
  inline `# noqa: site-appliances-deleted-include` markers with
  rationale)
- **Real filter adds** on customer-facing endpoints (routes.py:441
  /api/sites/{site_id}/appliances list + 3628 attention-required +
  3708 site detail stats + 5095 multi-site fleet score CTE + 6828
  admin "stop appliances" fleet-order + 8702 site detail agents)
- **Behavioral filter adds** on health_monitor (5 notification scans
  gained `AND sa.deleted_at IS NULL` — stops alerting on already-
  soft-deleted appliances; operator already chose to delete them)
- **sites.py × 23** (PK lookups, checkin-handler system paths
  needing orphan-detection visibility, admin destructive ops, dynamic-
  where pattern marker)
- **Test infra**: `_NOQA_PATTERN` extended to accept SQL-string
  `-- noqa:` markers (same convention as compliance_status gate);
  `_DELETED_AT_WINDOW_LINES` bumped 6 → 8 to accommodate longer
  SELECT column lists between marker and FROM

100% closure (BUG 1 root-cause class fully closed). Any new bare-FROM-
site_appliances without filter or marker fails CI hard.

### Counsel Rule 1 canonical-source registry close-out (Tasks #50, #103)

**Phase 0/1/2** (#50) shipped registry + sampler + decorator +
substrate-invariant infrastructure. **Phase 3** (#103) drove the
26-entry `migrate` baseline to 0 via systematic per-entry inspection.

Key finding — implementation-discovery override of Gate A MIGRATE
verdict for **all 7 non-device-count entries**:

| Entry | Gate A said | Reality (post-impl inspection) | Action |
|---|---|---|---|
| metrics.calculate_compliance_score | MIGRATE | Stateless 7-boolean averager, not bundle aggregator | RECLASSIFY operator_only |
| compliance_packet._calculate_compliance_score | MIGRATE | Per-month historical snapshot, canonical helper is current-rolling-window only | RECLASSIFY operator_only + register `historical_period_compliance_score` PLANNED |
| db_queries.get_compliance_scores_for_site | MIGRATE | HIPAA-weighted per-category w/ partial-credit-for-warnings | RECLASSIFY operator_only + register `category_weighted_compliance_score` (initially PLANNED, then promoted back to CANONICAL with shared primitive — see below) |
| db_queries.get_all_compliance_scores | MIGRATE | Same as above (multi-site batched) | RECLASSIFY operator_only |
| frameworks.get_compliance_scores | MIGRATE | Reads denormalized `compliance_scores` rollup table | RECLASSIFY operator_only + register `per_framework_compliance_score` PLANNED |
| frameworks.get_appliance_compliance_scores | MIGRATE | FastAPI endpoint wrapper around #5 | RECLASSIFY operator_only |
| client_attestation_letter._get_current_baa | MIGRATE | Row-fetch flavor of canonical baa_status; queries same source table | RECLASSIFY operator_only |

19 `device_count_per_site` line-anchored entries were ALL stale
post-Phase-2 close-out (4 had `canonical-migration:` markers, 1 used
canonical CTE, 14 line-shifted past relevant code). Removed — device-
count canonical migration tracking lives in
`test_no_raw_discovered_devices_count.py` BASELINE (=0 since Task #74)
+ per-line `canonical-migration:` markers.

**Net result**: Phase 3 closed BASELINE 26→0. Three new PLANNED_METRICS
entries register the un-canonicalized score classes
(`historical_period_compliance_score`, `category_weighted_compliance_
score`, `per_framework_compliance_score`).

### Registry-integrity drift gate (`7da14e1b`)

Gate A fork found 2 registry-signature drifts (silent rot since Phase
0 #50 landing):
- `compliance_packet.ComplianceReport._calculate_compliance_score` →
  class is `CompliancePacket`, NOT `ComplianceReport`
- `client_attestation_letter._get_baa_signature_row` → function is
  `_get_current_baa`, NOT `_get_baa_signature_row`

Both fixed. New CI gate
`test_allowlist_signatures_resolve_to_real_symbols` walks every
`migrate`-class signature via `importlib` + source-grep fallback (for
modules that can't import in test context due to relative-import
chains). Catches symbol-resolution chain-breaks at PR time. Sanity-
checked: temporarily re-introducing the `ComplianceReport` typo causes
loud failure with the exact symbol-resolution chain-break message.

### category_weighted leak audit (Task #103 Fork B) + canonical primitive shipped

Fork B post-Phase-3 leak audit found 2 customer-facing endpoints
computing `category_weighted_compliance_score` INLINE instead of
delegating:
- `routes.get_admin_compliance_health` (admin endpoint, HIPAA-weighted)
- `client_portal.get_site_compliance_health` (client endpoint,
  unweighted variant)

Closed via **canonical primitive extraction** (Fork A spec §2 approach
b, minimal first pass): `compliance_score.compute_category_weighted_
overall(cat_pass, cat_fail, cat_warn, *, category_weights,
partial_credit_warning=0.5)`. All 4 callsites delegate. `category_
weighted_compliance_score` promoted PLANNED → CANONICAL with all 4
callsites classified `operator_only`. Counsel Rule 1: one canonical
source for the formula.

### Other Session 220 closures

- **compliance_status reader ratchet 7 → 0** (Task #23) — BUG 3 close-
  out. `_NOQA_PATTERN` extended to recognize SQL-string `-- noqa:`
  markers; device_sync.py:1378 deprecated-column SELECT removed (OR-
  fallback collapsed to literal `'unknown'`); device_sync.py:1328
  endpoint structurally migrated to live-compute via canonical
  aggregation rule (matching `db_queries.get_per_device_compliance`).

- **discovered_devices reader ratchet 5 → 0** (Task #74 Phase 2 close)
  — 5 residual readers classified KEEP-RAW (write-path PK lookup OR
  per-appliance scan-target filter OR operator-only mesh debug). Inline
  `canonical-migration: device_count_per_site` markers added; window
  bumped 5 → 8 lines to accommodate SELECT column lists.

- **Legal.tsx /legal/baa v1.0-INTERIM** (Task #100) — replaced stale
  6-bullet "best practice" summary with v1.0-INTERIM-accurate copy.
  New CI gate `test_legal_baa_page_reflects_current_version.py` pins
  v1.0-INTERIM literal + effective-date literal + bans "best
  practice" stale framing + requires signup-flow OR contact-email
  handoff.

- **SyntaxWarning future-proof** — 4 backend SQL triple-quoted strings
  with `\d` / `\[` patterns converted to raw triple-quoted (`r"""..."""`)
  to clear Python SyntaxWarnings that will become SyntaxError in a
  future Python version.

- **gitignore .claude/worktrees/** — per the
  `feedback_parallel_fork_isolation.md` rule, fork worktrees are
  disposable per-fork snapshots; the directory accumulated ~30+
  entries across the session and polluted `git status`.

---

## Lessons / structural rules locked in this period

### Implementation discovery overrides Gate A misclassification

Phase 3's pattern: all 7 non-device-count `migrate` entries in the
compliance_score class were Gate-A-misclassified — they implement
DISTINCT metric classes from the canonical bundle aggregator. The
Gate A fork's NEAR-canonical observation missed the time-anchor /
weighting / shape differences.

The implementation sub-loop (reading the actual code per-entry
before refactoring) is the second line of defense after Gate A. When
the sub-loop reveals a Gate A misclassification, the override path is:
(a) reclassify the entry to the correct class, (b) register the
distinct class as PLANNED_METRICS with its own helper-pending entry,
(c) commit body documents the implementation-discovery finding so
future Gate A's can learn the pattern.

Rule: don't force-migrate entries into the wrong canonical class. The
"long-lasting enterprise solution" is to properly model the metric
class, not to break methodology semantics.

### Line-anchored ratchet entries are fragile

Phase 3 verification showed 14 of 19 `device_count_per_site` line-
anchored entries pointed at stale lines that had shifted past their
original targets (BUG 1 + Phase 2 close-out rewrote large swaths of
partners.py / routes.py / sites.py / client_portal.py / background_
tasks.py). Registry rot accumulated silently for weeks.

Rule: prefer function-name anchors (`module.function_name`) over
line-anchors (`module.py:LINE`) in ratchet allowlists. When a line-
anchor is unavoidable, add a periodic re-verification mechanism (Fork
C's ratchet-fragility audit confirmed this class is bounded —
test_no_unfiltered_site_appliances_select.py is the main remaining
risk; most other gates use dynamic line discovery).

### Fork stalls cost time; tighter scope is the cure

The unified Phase 3 Gate A stalled at the 600s watchdog mid-verdict.
The narrower compliance_score-slice-only refork (6 entries vs 26)
completed in ~75s with a comprehensive 7-lens verdict.

Rule: scope a Gate A fork to ≤6-8 deliverable units. If the brief
spans more, split into slices and run sequential forks (each ~5-10
min). The cumulative protocol completes faster than one stalled
unified fork.

### Registry signatures need a resolves-to-real-symbol gate

The 2 silent registry-signature drifts (
`compliance_packet.ComplianceReport` and `_get_baa_signature_row`)
existed since Phase 0 and were only caught by the Gate A fork's
verification sweep. Existing CI gates only checked field shape, not
signature resolvability.

Rule: any registry that points at code symbols needs a CI test that
walks the symbol via `importlib` (with source-grep fallback for
relative-import-context modules). Sibling-precedent for cross-cutting
"the data points at real code" gate.

### Counsel Rule 1 spirit is "register the class, don't hide it"

Phase 3's reclassification of 7 compliance_score entries as
operator_only could have left the distinct metric classes unaccounted-
for. Counsel's ask at the Gate A — "Don't just hide it — register it"
— drove the addition of 3 PLANNED_METRICS entries (historical_period,
category_weighted, per_framework). The CI gate then prevents customer-
facing surfaces from exposing the planned classes without going
through the canonical helper that's blocking-until each class's own
Class-B Gate A.

Rule: when reclassifying an entry from migrate → operator_only because
of a metric-class distinction, ALSO register the distinct class as
PLANNED_METRICS so the canonical-source gate has continued coverage.

### Audit fork verdicts are deliverable artifacts

This period landed 4-6 audit forks (privileged-chain audit, ratchet
inventory, stale-docstring sweep, canonical-helper extension specs,
leak audit, ratchet-fragility audit). All worktree-isolated. Verdicts
written to `audit/coach-<topic>-<gate>-YYYY-MM-DD.md`. Several were
archived to repo `mcp-server/central-command/backend/audit/` for
future Class-B Gate A reference.

Rule: fork verdicts that recommend specific design work (canonical-
helper extensions, future Class-B Gate As) should be archived to the
repo's `audit/` directory so they don't get lost when the fork
worktree is reclaimed.
