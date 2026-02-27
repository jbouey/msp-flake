# Session 129 — Tiered Flywheel Promotion + L2 Mode Toggle

**Date:** 2026-02-24
**Commit:** `2afea53` → `main`
**Deploy:** CI/CD auto-deploy + manual migration run + backend restart

## Summary

Built three-tier delegation model for scaling L2→L1 pattern promotions across 50-100+ clients, plus per-appliance L2 mode control and critical flywheel bug fixes.

## Bug Fixes (Flywheel Audit)

1. **Pattern signature fix** — Was `incident_type:incident_type:hostname` (duplicated field), fixed to `incident_type:runbook_id:hostname` in 4 locations (main.py x2, telemetry.go x2)
2. **ON CONFLICT DO NOTHING → DO UPDATE** — Pattern occurrence counts never accumulated
3. **Removed auto-promotion Path 1** — Manual-only for now

## Per-Appliance L2 Mode Toggle

3-state control: `auto` | `manual` | `disabled`. Migration 056, Go daemon gating, backend PATCH endpoint, frontend segmented control in SiteDetail ApplianceCard.

## Tier 1 — Platform Auto-Promote

Migration 057 `platform_pattern_stats` table. Flywheel Steps 3-4: aggregate L2 patterns across all sites/orgs, auto-promote to `l1_rules` with `source='platform'` when 5+ orgs, 90%+ success, 20+ occurrences. Syncs to all appliances via `/agent/sync`.

## Tier 2 — Client Self-Service

`POST /api/client/promotion-candidates/{id}/approve` and `/reject` — gated by `healing_tier='full_coverage'`. ClientHealingLogs.tsx: full_coverage sees Approve+Reject+Forward, standard sees Forward only.

## Tier 3 — Partner Bulk Management

`POST /api/partners/me/learning/candidates/bulk-approve` and `bulk-reject` (up to 50). PartnerLearning.tsx: checkbox column, Select All, floating bulk action bar, client endorsement badges, healing tier badges, endorsed filter.

## Files Changed (18 files, +1313/-98)

| File | Change |
|------|--------|
| `migrations/056_appliance_l2_mode.sql` | NEW — l2_mode on site_appliances |
| `migrations/057_platform_pattern_aggregation.sql` | NEW — platform_pattern_stats table |
| `main.py` | Pattern sig fix, removed auto-promote, Steps 3-4 |
| `client_portal.py` | Client approve/reject endpoints |
| `learning_api.py` | Bulk approve/reject, client_endorsed in response |
| `sites.py` | L2 mode PATCH endpoint |
| `ClientHealingLogs.tsx` | Approve/Reject UI for full_coverage |
| `PartnerLearning.tsx` | Checkbox selection, bulk action bar, badges |
| `SiteDetail.tsx` | L2ModeToggle component |
| `api.ts`, `useFleet.ts`, `hooks/index.ts` | L2 mode API + hook |
| `checkin/db.go`, `models.go` | FetchL2Mode |
| `daemon/daemon.go`, `phonehome.go` | L2 mode gating |
| `l2planner/telemetry.go` | Pattern signature fix |

## Next Session

1. Verify flywheel loop runs and populates platform_pattern_stats
2. Test client approve flow end-to-end on portal
3. Test partner bulk approve with multiple candidates
