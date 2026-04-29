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

## Next Session

1. **F1 (P0)** — Canonical telemetry view `v_canonical_telemetry` so future relocates don't need a manual migration
2. **F4 (P1)** — Centralize site rename behind a SQL function + CI gate that fails any direct UPDATE site_id outside it
3. **F5 (P1)** — Resolve duplicate `_flywheel_promotion_loop` (`main.py` + `background_tasks.py`)
4. Verify mig 255 deploy on prod, run cleanup once, and confirm `flywheel_orphan_telemetry` clears
5. Through 2026-05-05: monitor `sigauth_enforce_mode_rejections` + `sigauth_post_fix_window_canary` (auto-reopens task #169 on fire)
