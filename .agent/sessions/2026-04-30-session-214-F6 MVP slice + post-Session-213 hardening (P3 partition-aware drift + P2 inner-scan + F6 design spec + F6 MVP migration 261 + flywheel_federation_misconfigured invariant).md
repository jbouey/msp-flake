# Session 214 — F6 MVP slice + post-Session-213 hardening

**Date:** 2026-04-30 (overnight continuation from Session 213)
**Started:** 03:15 UTC
**Previous Session:** 213

---

## Goals

- [x] Item 3 — partition-aware drift detection (P3, deferred from Session 213 F4-followup)
- [x] Item 4 — defense-in-depth inner scan within auto-EXEMPT files (P2, round-table deferred)
- [x] Item 1 — F6 design spec (multi-day, design-first)
- [x] Item 2 — confirmed 2026-05-05 sigauth watch is passive
- [x] F6 MVP slice — schema + feature flag scaffolding
- [x] F6 fast-follow — flywheel_federation_misconfigured sev3 substrate invariant

---

## Progress

### Items 3 + 4 — partition-aware drift + inner-scan (commit `86df7625`)

Two-pass UNION query in `_check_rename_site_immutable_list_drift`:
- Pass 1: trigger directly on the table (regular + partitioned parents — relkind expansion from `'r'` to `IN ('r', 'p')` is the actual fix; round-table corrected my narrative)
- Pass 2: trigger on a partition child → surface the PARENT (legacy/manual case)

Defense-in-depth inner scan in `_inner_scan_misuse()` runs on auto-EXEMPT migration files; 3 regex patterns catch actual misuse (UPDATE compliance_bundles + canonical_site_id within 500 chars; INSERT same; reverse-direction). `_strip_sql_comments()` strips `-- ` and `/* */` first so docs co-mention doesn't false-positive.

5 inner-scan self-tests + ratchet wired correctly. Round-table verdict: READY (first pass), 2 polish items (mig 191 narrative correction + runbook Verification SQL update) folded in.

### F6 design spec (commit `86df7625`)

`docs/specs/2026-04-30-f6-federation-eligibility-tier-design.md` — full scoping doc for Federation tier (local/org/platform). Three-week calibration window referenced; 4 round-table risks pre-anticipated; explicit "do NOT bundle with hotfix sessions".

### F6 MVP slice (commit `7dee6d6c`)

Migration 261 — `flywheel_eligibility_tiers` table. 3 seed rows (local/org/platform), ALL `enabled=FALSE`, `calibrated_at=NULL`. Schema-level CHECK constraints enforce calibration discipline (org_isolation_required=TRUE for tier_level >=1; distinct_orgs/sites required when calibrated_at is set).

main.py Step 2 reads `FLYWHEEL_FEDERATION_ENABLED` env var (lenient parser matches `assertions.py::L2_ENABLED` sibling). Triple-gate activation: env=true AND tier.enabled=TRUE AND calibrated_at IS NOT NULL. Default OFF — production behavior unchanged.

Round-table verdict: NEEDS-IMPROVEMENT. 2 P1 + 3 P2 ship-now items integrated:
- P1-1: lenient parser instead of strict `== "true"` (sibling-subsystem mismatch was operator footgun)
- P1-2: NULL out distinct_orgs/sites in higher-tier seeds + CHECK constraints forcing calibration to set them
- P2-3: org_isolation_required column with CHECK (HIPAA boundary at schema level)
- P2-4: logger.info → logger.warning + TODO for Prom counter
- P2-5: reconciled min_distinct_sites=1 on local seed comment

Reviewer said "the structural slice is sound. You did not ship a foot-gun. The OFF-state preservation property holds."

### F6 fast-follow — flywheel_federation_misconfigured invariant (commit `21bdda0a`)

Sev3 substrate invariant fires when env flag is truthy but no tier is enabled+calibrated. Three-list lockstep: Assertion + _DISPLAY_METADATA + runbook. Round-table verdict: READY first pass (1 P3 + 1 P2 sharpening folded in — explicit "no calibration migration shipped yet" callout in runbook Path B).

Total invariant count: **47 → 48**.

---

## Files Changed

| File | Change | Commit |
|------|--------|--------|
| `assertions.py` | partition-aware drift query (Pass 1 + Pass 2 UNION) | `86df7625` |
| `tests/test_canonical_not_used_for_compliance_bundles.py` | inner-scan + comment stripper + 5 self-tests | `86df7625` |
| `substrate_runbooks/rename_site_immutable_list_drift.md` | Verification SQL updated to two-pass form | `86df7625` |
| `docs/specs/2026-04-30-f6-federation-eligibility-tier-design.md` | NEW — F6 design spec | `86df7625` |
| `migrations/261_flywheel_eligibility_tiers.sql` | NEW — F6 MVP table + 3 disabled tiers + CHECK constraints | `7dee6d6c` |
| `mcp-server/main.py` | Step 2 federation read path (lenient parser, triple-gate) | `7dee6d6c` |
| `tests/test_flywheel_federation_scaffold.py` | NEW — 11 source-level + 2 DB-gated tests | `7dee6d6c` |
| `assertions.py` | flywheel_federation_misconfigured Assertion + _DISPLAY_METADATA + check fn + `import os` | `21bdda0a` |
| `substrate_runbooks/flywheel_federation_misconfigured.md` | NEW — runbook for sev3 invariant | `21bdda0a` |

---

## Verification

All deploys verified post-CI:
- mig 260 (telemetry index) applied 2026-04-30 02:51:26Z; planner uses `Index Only Scan using idx_execution_telemetry_site_created` (sub-ms COUNT(*))
- mig 261 (federation scaffold) applied 2026-04-30 07:09:41Z; 3 tiers all disabled+uncalibrated; CHECK constraints reject invalid INSERT correctly; FLYWHEEL_FEDERATION_ENABLED unset in prod (OFF state preserved)
- Drift query returns 0 rows post-fix (substrate is self-consistent on the immutable-list class)
- Empirical: appliance_heartbeats has trigger on the parent (`relkind='p'`), not children — confirms the round-table reviewer's narrative correction. Pass 1's `relkind IN ('r', 'p')` expansion is what catches it; Pass 2 is dormant for this case (exists for legacy/manual cases).

---

## Round-table scoreboard (Session 214)

| # | Round-table | Verdict | Outcome |
|---|---|---|---|
| 1 | P3+P2 hardening (items 3+4) | READY (1st pass) | 2 polish items folded in |
| 2 | F6 MVP slice | NEEDS-IMPROVEMENT (2P1+3P2) | All integrated same-commit |
| 3 | flywheel_federation_misconfigured | READY (1st pass) | 1 P2 sharpening folded in |

**Cumulative across Session 213 + 214: 10 round-tables, 10 approvals.**

---

## Continued — F6 phase 2 (Option A read-only) + thresholds exposure + foundation slice + cross_org_federation_leak BLOCK

### F6 phase 2 Option A — read-only eligibility helpers (commit `a91794ce`)

`flywheel_eligibility_queries.py` — pure functions: `count_tier_local_eligible`, `count_tier_org_eligible` (cross-org isolation property tested), `count_tier_platform_eligible`, `compute_tier_resolution`. Wired into F7 diagnostic endpoint as `tier_resolution` section.

Round-table caught + integrated 4 P1 (per-site freshness gate INSIDE CTE before aggregation; `status != 'inactive'` filter on `sites`; whitespace-tolerant + conn.execute-banned read-only structural test; DB-gated cross-org isolation property test) and 5 P2 (`tier_resolution.notes` Dict; consistent `*_would_be_eligible` naming; `shared.parse_bool_env` single source of truth; regex-based short-circuit test; combined `load_tiers` batch query).

> *"Cross-org HIPAA: SAFE. No sev0 leak. The SQL is correctly scoped."*

### F7 P3 thresholds exposure (commit `d839dd22`)

`TierThresholdsView` Pydantic model + `_thresholds_to_dict` helper — exposes the actual threshold values per tier so an operator can see WHY a count came out the way it did. Two type-drift guard tests (TierThresholds → projected dict; → Pydantic model) — the same class as three-list-lockstep failures we've been bitten by elsewhere.

### F6 phase 2 deferred-enforcement card (commit `7cb01b80`)

Round-table convened on the F6 phase 2 enforcement question with explicit non-operator-posture + HIPAA + sleep-deprivation framing. **Unanimous SHIP_FOUNDATION_SLICE / DEFER_ENFORCEMENT.**

Filed `.agent/plans/f6-phase-2-enforcement-deferred.md` with 5 pre-conditions, required new participants (Security/HIPAA + outside counsel), and the calibration-data foundation as the next safe move.

### F6 phase 2 foundation slice (commit `847db5c3`)

Migration 262 — `flywheel_federation_candidate_daily` table + `promoted_rule_events.tier_at_promotion` column. New module `flywheel_federation_admin.py` with admin-only `GET /api/admin/flywheel/federation-candidates?tier=org|platform` + daily snapshot writer. Background loop `_flywheel_federation_snapshot_loop` registered.

Round-table verified all 7 deploy checks: schema, CHECK constraints (incl. `org_scope_matches_tier` rejecting platform-with-org_id), endpoint 401, daily loop registered.

### cross_org_federation_leak — round-table BLOCKED, NOT shipped

Attempted to ship a sev1 detector for cross-org rule deployment. Round-table identified a **P0 Cartesian-product JOIN bug** in the SQL: `pr.rule_id = fo.parameters->>'rule_id'` matches multiple origin rows because `(site_id, rule_id)` is the natural key, not rule_id alone. Today this is masked (single-tenant prod) but would fire false-positive sev1 ~100% of multi-tenant cross-org orders within 24h of multi-tenant launch — **wolf-crying-wolf failure mode for a privacy detector**.

Reverted the local broken version (never pushed). Captured the design lesson + 7 pre-conditions for the correct invariant in the deferred card (commit `1e2b2c01`):
- UUID-based JOIN (`safe_rollout_promoted_rule` stamps `parameters->>'promoted_rule_id'`, assertion joins on `pr.id::text`)
- Prod-snapshot fixture for two-orgs-shared-rule_id scenario
- `manual_resolve_only=True` flag (or runbook re-ordering)
- SQL-fragment removal from remediation
- `fleet_orders.notes` column verification
- LIMIT bump + sibling defense-in-depth invariants

> *"This is exactly the wolf-crying-wolf failure mode the trip-wire is supposed to PREVENT."*

### Crons set (session-only — re-establish from deferred card if session ends)

- 2026-05-07 09:37 local — 7-day calibration health check
- 2026-05-21 10:23 local — 21-day calibration window close + convene dedicated round-table

---

## Final cumulative scoreboard (Sessions 213 + 214)

**15 round-tables, 14 approvals + 1 BLOCK (handled correctly).**

| Session | Round-tables | Verdicts | P0+P1 caught + integrated |
|---|---|---|---|
| 213 | 6 | 4 NEEDS-IMPROVEMENT (integrated) + 2 READY | 8 P0 + 14 P1 |
| 214 | 9 | 5 NEEDS-IMPROVEMENT (integrated) + 3 READY + 1 SHIP_FOUNDATION_SLICE consensus + **1 BLOCK (reverted)** | 1 P0 BLOCKED + 16 P1 |
| **Total** | **15** | **14 ship + 1 disciplined defer** | **9 P0 + 30 P1** |

**10 migrations** (254-262)
**Architectural primitives:** `canonical_site_id`, `site_canonical_mapping`, `rename_site`, `_rename_site_immutable_tables`, `parse_bool_env`
**Operator endpoints:** F7 diagnostic (8-section + tier_resolution + thresholds) + F6 federation-candidates
**Substrate invariants:** 45 → 48 (cross_org_federation_leak deferred to dedicated session, NOT shipped)
**Deferred-enforcement card:** 5 pre-conditions + 7 design notes for the correct cross_org_federation_leak

---

## Files Changed (cumulative session 214)

| File | Change | Commit |
|------|--------|--------|
| `assertions.py` | partition-aware drift + flywheel_federation_misconfigured invariant | `86df7625`, `21bdda0a` |
| `migrations/261_flywheel_eligibility_tiers.sql` | NEW — F6 MVP table + 3 disabled tiers + CHECK constraints | `7dee6d6c` |
| `migrations/262_flywheel_federation_foundation.sql` | NEW — daily snapshot table + tier_at_promotion column | `847db5c3` |
| `tests/test_canonical_not_used_for_compliance_bundles.py` | inner-scan + 5 self-tests | `86df7625` |
| `substrate_runbooks/rename_site_immutable_list_drift.md` | Verification SQL → two-pass | `86df7625` |
| `substrate_runbooks/flywheel_federation_misconfigured.md` | NEW — sev3 runbook | `21bdda0a` |
| `docs/specs/2026-04-30-f6-federation-eligibility-tier-design.md` | NEW — F6 spec | `86df7625` |
| `tests/test_flywheel_federation_scaffold.py` | NEW — 11 source-level + 2 DB-gated | `7dee6d6c` |
| `mcp-server/main.py` | F6 read-path + federation snapshot loop + lenient parser | `7dee6d6c`, `847db5c3` |
| `flywheel_eligibility_queries.py` | NEW — read-only helpers | `a91794ce` |
| `flywheel_diagnostic.py` | tier_resolution + TierThresholdsView | `a91794ce`, `d839dd22` |
| `tests/test_flywheel_eligibility_queries.py` | NEW — cross-org property test + drift guards | `a91794ce`, `d839dd22` |
| `shared.py` | parse_bool_env helper | `a91794ce` |
| `flywheel_federation_admin.py` | NEW — F6 foundation endpoint + snapshot writer | `847db5c3` |
| `tests/test_flywheel_federation_admin.py` | NEW — endpoint + writer tests | `847db5c3` |
| `.agent/plans/f6-phase-2-enforcement-deferred.md` | NEW + cron schedule + cross_org_federation_leak BLOCK design notes | `7cb01b80`, `14f67527`, `1e2b2c01` |

---

## Next Session

1. **F6 phase 2 ENFORCEMENT** — DEFERRED to dedicated round-table with HIPAA specialist + outside HIPAA counsel + 21 days of calibration data. Pre-conditions in `.agent/plans/f6-phase-2-enforcement-deferred.md`. Cron set for 2026-05-21.
2. **2026-05-07** — 7-day calibration health check (cron set).
3. **2026-05-05** — Sigauth invariant watch (passive — auto-reopens task #169).
4. **(Optional, safe slices not blocked by F6 enforcement)** —
   - `federation_disclosure` event_type three-list lockstep prep (one of the 7 pre-conditions, decoupled from the cross-org-JOIN issue)
   - Auditor-kit federation-event surface (another pre-condition, decoupled)
   - F7 frontend integration (read-only consumer of tier_resolution + federation candidates)
