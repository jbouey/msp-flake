# Session 213 - Flywheel Orphan Relocation + F2 F3 Round Table P0

**Date:** 2026-04-29
**Started:** 06:34
**Previous Session:** 212

---

## Goals

- [x] Diagnose post-relocate orphan-row recreation (mig 252+254 didn't stick)
- [x] Trace upstream cause (`_flywheel_promotion_loop` aggregating execution_telemetry)
- [x] Migration 255 — relocate execution_telemetry/incidents/l2_decisions
- [x] QA round-table on flywheel system architecture
- [x] Ship F2 (phantom-site precondition) + F3 (orphan-telemetry invariant)

---

## Progress

### Completed

**Migrations 254 + 255:**
- `254_aggregated_pattern_stats_orphan_cleanup_retry.sql` — full Step 1+2+3 (merge → delete-collisions → rename) for orphan site cleanup. The original 252 left 115 orphan rows because asyncpg simple-query / explicit BEGIN/COMMIT interaction skipped sub-statements after the first.
- `255_relocate_orphan_operational_history.sql` — closes the upstream cycle. Migrates 19,063 `execution_telemetry` rows, 533 `incidents`, 31 `l2_decisions` from `physical-appliance-pilot-1aea78` → `north-valley-branch-2`. Re-runs `aggregated_pattern_stats` Step 1+2+3 cleanup. **INTENTIONALLY skips `compliance_bundles`** (137,168 rows — Ed25519 + OTS-anchored, site_id is part of the cryptographic binding). Idempotent audit-log INSERT with NOT EXISTS guard.

**Round-table verdict on flywheel (NEEDS-IMPROVEMENT, 7 findings):**
- F1 (P0, next session) — Canonical telemetry view (`v_canonical_telemetry`) closes the relocate-orphan class architecturally
- F2 (P0, shipped) — `PhantomSiteRolloutError` precondition in `safe_rollout_promoted_rule` for `scope='site'`
- F3 (P0, shipped) — `flywheel_orphan_telemetry` sev1 invariant
- F4 (P1) — Centralize site rename behind SQL function + CI gate
- F5 (P1) — Deprecate duplicate `_flywheel_promotion_loop` in `background_tasks.py`
- F6 (P3) — Federation tier for eligibility thresholds
- F7 (P3) — Operator diagnostic endpoint `GET /api/admin/sites/{site_id}/flywheel-diagnostic`

**F2 wired (commit `fe3ab3cc`):**
- `flywheel_promote.py::PhantomSiteRolloutError` exception class
- Precondition in `safe_rollout_promoted_rule` for `scope='site'` queries `SELECT 1 FROM site_appliances WHERE site_id=$1 AND deleted_at IS NULL LIMIT 1` and raises if 0 rows
- `routes.py::promote_pattern` translates to HTTP 409 with structured remediation body pointing operator at admin_audit_log
- `tests/test_flywheel_promote_candidate.py::FakeConn.fetchval` returns 1 (healthy-site fixture)

**F3 wired (commit `fe3ab3cc`):**
- `assertions.py::_check_flywheel_orphan_telemetry` queries 24h orphan rows, threshold > 10
- Added to `ALL_ASSERTIONS` (sev1) + `_DISPLAY_METADATA` + `substrate_runbooks/flywheel_orphan_telemetry.md` (three-list lockstep green)
- Total invariant count: **45 → 46**

**Tests:**
- 69 backend tests pass (lockstep + write-warning + flywheel_promote_candidate)
- `test_assertion_metadata_complete` + `test_substrate_docs_present` green
- Smoke import: `len(ALL_ASSERTIONS) == 46`

### Blocked

None.

---

## Files Changed

| File | Change |
|------|--------|
| `mcp-server/central-command/backend/migrations/254_aggregated_pattern_stats_orphan_cleanup_retry.sql` | NEW — full Step 1+2+3 cleanup |
| `mcp-server/central-command/backend/migrations/255_relocate_orphan_operational_history.sql` | NEW — execution_telemetry/incidents/l2_decisions relocate |
| `mcp-server/central-command/backend/flywheel_promote.py` | F2 — `PhantomSiteRolloutError` + precondition |
| `mcp-server/central-command/backend/routes.py` | F2 — 409 translation in `promote_pattern` |
| `mcp-server/central-command/backend/assertions.py` | F3 — `flywheel_orphan_telemetry` sev1 invariant |
| `mcp-server/central-command/backend/substrate_runbooks/flywheel_orphan_telemetry.md` | F3 — runbook (three-list lockstep) |
| `mcp-server/central-command/backend/tests/test_flywheel_promote_candidate.py` | `FakeConn.fetchval` shim |

---

## Continued — F1 + F4 same session

### F1 (P0, shipped commit `fa0b8713`)

**Migration 256** — `canonical_site_id()` STABLE function + `site_canonical_mapping` append-only table. Closes the eligibility-fragmentation class architecturally.

- Auto-resolving chain function: WITH RECURSIVE, depth limit 16, returns input on no-mapping.
- Backfill row: `physical-appliance-pilot-1aea78 → north-valley-branch-2` with actor=jbouey2006@gmail.com.
- DELETE+UPDATE block (departure from mig 151/245 precedent — chain function correctness depends on immutable to_site_id).
- CHECK constraints: actor must match email regex, reason ≥20 chars, no-self.

**main.py hot-path rewrites:**
- Step 1 (`aggregated_pattern_stats`): CTE-wrapped to call `canonical_site_id()` once per row instead of twice.
- Step 3 (`platform_pattern_stats`): JOIN sites via canonical_site_id (was silently EXCLUDING orphan telemetry).

**Round-table integrations (DBA + Principal SWE + Compliance):**
- P0/Compliance: actor=human email enforced via CHECK constraint (CLAUDE.md privileged-access rule)
- P0/SWE: Step 3 patched in this commit (no half-closed deferral)
- P1/DBA: trigger DELETE+UPDATE departure documented in comment block
- P1/SWE: CTE wrap on Step 1
- P1/Compliance: CI gate `tests/test_canonical_not_used_for_compliance_bundles.py` (regex scan within ±5 lines)
- 13 new DB-gated tests + 1 architectural CI gate

### F4 (P1, shipped commit `ac96ddcb`)

**Migration 257** — `rename_site()` SQL function + `_rename_site_immutable_tables()` IMMUTABLE helper.

- Alias-style rename: original `sites` row stays put; canonical_mapping carries read-side aliasing.
- Auto-discovers tables with `site_id` via information_schema; skips immutable list, views, partition children, date-suffixed backups.
- Phase 0 validations: actor email regex, reason ≥20 chars, from≠to, from_site_id exists in sites.
- Phase 1: `statement_timeout=5min` guardrail + `pg_advisory_xact_lock` for serialization.
- Phase 2: SET LOCAL `app.allow_multi_row='true'` (mig 192 row-guard bypass).
- Phase 3: INSERT site_canonical_mapping FIRST.
- Phase 4: dynamic UPDATE per discovered table; returns SETOF (touched_table, rows_affected).
- Phase 5: structured admin_audit_log row.

**Immutable list (round-table P0-2 + P1-1 expanded):**
- Parent identity: `sites` (PK update class is intractable across FK graph — alias via mapping instead)
- Cryptographic: compliance_bundles, compliance_packets, evidence_bundles, ots_proofs, baa_signatures, audit_packages, compliance_attestations, compliance_scores
- Audit-class: admin/client/partner audit_logs, portal_access_log, incident_remediation_steps, fleet_order_completions, sigauth_observations, promoted_rule_events, reconcile_events, appliance_audit_trail, journal_upload_events, promotion_audit_log
- Self-referential: site_canonical_mapping, relocations

**CI gate** (round-table P0-1 — was P0 false-negative):
- Switched from whole-file exemption (silently allowed new violations in 5000-line files) to per-line `# noqa: rename-site-gate — <reason>` markers + ratchet baseline (NOQA_BASELINE_MAX=6).
- Added 6 markers to existing per-appliance MAC-scoped UPDATE site_id sites in routes.py + sites.py (all genuinely per-appliance moves, not site renames).
- 2 tests: violation detector + ratchet (prevents silent accumulation).

**Round-table integrations:**
- P0-1: line-anchored CI gate
- P0-2: `sites` in immutable list
- P1-1: 8 additional audit-class tables
- P1-2: from_site_id existence check
- P2-8: tightened backup-table skip pattern (date-suffix only)
- P2-9: statement_timeout guardrail

10 new DB-gated tests in `test_rename_site_function.py`.

---

## Files Changed (cumulative session)

| File | Change | Commit |
|------|--------|--------|
| `migrations/254_*.sql` | NEW — orphan cleanup retry | `b4ed5a8a` |
| `migrations/255_*.sql` | NEW — operational history relocate | `b4ed5a8a` |
| `migrations/256_canonical_site_mapping.sql` | NEW — F1 architectural close | `fa0b8713` |
| `migrations/257_rename_site_function.sql` | NEW — F4 centralized rename | `ac96ddcb` |
| `flywheel_promote.py` | F2 — `PhantomSiteRolloutError` precondition | `fe3ab3cc` |
| `routes.py` | F2 — 409 translation; F4 — 4 noqa markers | `fe3ab3cc`, `ac96ddcb` |
| `sites.py` | F4 — 2 noqa markers | `ac96ddcb` |
| `assertions.py` | F3 — `flywheel_orphan_telemetry` sev1 | `fe3ab3cc` |
| `substrate_runbooks/flywheel_orphan_telemetry.md` | F3 — runbook | `fe3ab3cc` |
| `main.py` | F1 — Step 1 + Step 3 canonicalization | `fa0b8713` |
| `tests/test_canonical_site_id_function.py` | NEW — F1 (13 tests) | `fa0b8713` |
| `tests/test_canonical_not_used_for_compliance_bundles.py` | NEW — F1 CI gate | `fa0b8713` |
| `tests/test_no_direct_site_id_update.py` | NEW — F4 CI gate | `ac96ddcb` |
| `tests/test_rename_site_function.py` | NEW — F4 (10 tests) | `ac96ddcb` |
| `tests/test_flywheel_promote_candidate.py` | F2 — FakeConn.fetchval shim | `fe3ab3cc` |

---

## Next Session

1. **F5 (P1)** — Resolve duplicate `_flywheel_promotion_loop` (`main.py` + `background_tasks.py`)
2. **F1-followups** — `v_canonical_telemetry` views, `relocate_appliance` auto-INSERT into `site_canonical_mapping`, auditor-kit README amendment
3. **F4-followup** — substrate invariant `rename_site_immutable_list_drift` (auto-detect tables with DELETE-blocking triggers not in immutable list)
4. Through 2026-05-05: monitor `sigauth_enforce_mode_rejections` + `sigauth_post_fix_window_canary` (auto-reopens task #169 on fire)
