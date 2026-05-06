# Plan 33 — Portal appliance visibility + ghost-data cleanup + auditor-kit streaming

**Date:** 2026-05-05
**Driver:** User observation — "central command shows 1 site, client + partner portals show different data; auditor kit still slow."
**Round-table:** RT33 (PM Linda + Sam Principal SWE + Carol CCIE + Dana DBA + Steve Sec + Adam Perf + Maya Consistency).
**Posture:** enterprise-grade, no deferrals, Maya 2nd-eye each commit.

## Problem cluster

1. **No appliance representation in client portal.** Customer can't see the appliances they're paying for.
2. **Partner portal has per-site topology only.** No fleet roll-up; can't answer "which appliances are offline across my book."
3. **Ghost data in client portal sites list.**
   - `client_portal.py:839` LEFT JOIN site_appliances has NO `deleted_at IS NULL` filter → MAX(sa.last_checkin) pulls dead appliances.
   - `client_portal.py:826` sites query has NO `s.status != 'inactive'` filter → soft-deleted sites visible.
   - `client_portal.py:3450` install-instructions endpoint same defect.
   - Migration 067 may have created "Unassigned Sites" placeholder org from orphan site_appliances rows at bootstrap.
4. **Auditor-kit slow.** `evidence_chain.py:4676` materializes entire bundle set + base64 OTS files synchronously in request thread. Not a window-days issue (intentionally all-time for chain integrity); architecture issue.

## Round-table consensus (RT33)

10 tickets. Maya 2nd-eyes each at land time.

| # | Ticket | Severity | Phase |
|---|---|---|---|
| 0a | Ghost-data: add `sa.deleted_at IS NULL` to `client_portal.py:839` + `:3450`. | **P0** | 1 |
| 0b | Ghost-data: add `s.status != 'inactive'` to `client_portal.py:826` sites list. | **P0** | 1 |
| 0c | CI gate: `test_client_portal_filters_soft_deletes` — AST-walks queries; fails if `LEFT JOIN site_appliances` lacks `deleted_at IS NULL` or sites SELECT lacks status filter. | **P0** | 1 |
| 0d | One-shot SQL audit on prod: any `client_orgs` with 0 active sites? Document; do NOT auto-delete. | P1 | 1 |
| 1 | `GET /api/client/appliances` — org-scoped, rollup-MV-backed, returns `{appliance_id, display_name, status, last_checkin, agent_version}`. Cursor pagination, hard-cap 50. NO mac, NO ip, NO daemon_health (Carol veto). | **P0** | 2 |
| 2 | Verify `tenant_org_isolation` policy on `site_appliances` — confirmed in mig 278. Pin in `test_org_scoped_rls_policies.py` SITE_RLS_TABLES. | **P0** | 2 |
| 3 | `GET /api/partners/me/appliances` — fleet view across all partner sites, single JOIN, rollup-MV-backed. Cursor pagination, hard-cap 100. Server-side filter: `status`, `site_id`, `version`. | **P0** | 3 |
| 4 | `ClientAppliances.tsx` list component, plugged into ClientDashboard. `StatusBadge` from `constants/status.ts`. Display-name fallback helper. | **P0** | 2 |
| 5 | `PartnerFleetAppliances.tsx` fleet table with filter + cursor pagination + virtualization. | **P0** | 3 |
| 6 | CI gate: `test_no_operator_class_actions_on_portal_appliance_endpoints`. | **P0** | 2/3 |
| 7 | CI gate: `test_portal_appliance_endpoints_use_rollup_mv`. | P1 | 2/3 |
| 8 | Auditor-kit streaming: `StreamingResponse` + zipfile streaming write + lazy bundle iteration. Replaces synchronous in-memory ZIP. | **P0** | 4 |
| 9 | Status summary card on ClientDashboard ("3/3 healthy, oldest checkin 2m ago"). | P1 | 2 |
| 10 | Polling intervals constant: 30s client / 15s partner. | P2 | 5 |

## Adversarial holes flagged

- **Steve:** RLS verified server-side, but JS frontend filter assumptions must NOT be load-bearing. Backend returns RLS-scoped result; frontend may add cosmetic filters but NEVER security.
- **Carol:** client portal MUST NOT expose `daemon_health`, `mac_address`, `ip_addresses`. Field allowlist in endpoint.
- **Dana:** every new query reads from `appliance_status_rollup` MV, not live `site_appliances`. CI gate enforces.
- **Adam:** keep first-paint <500ms for 200 appliances. Use `@tanstack/react-virtual`.
- **Maya:** `display_name → hostname → appliance_id` fallback in ONE helper. Status vocabulary uses existing `StatusBadge`. NO new lexicon.

## Commit cadence

- **Commit 1:** Phase 1 — ghost-data filters + CI gate. 1 file + 1 test. Maya 2nd-eye.
- **Commit 2:** Phase 2 — client-portal appliances endpoint + RLS pin + frontend list + summary card. 3 files + 2 tests. Maya 2nd-eye.
- **Commit 3:** Phase 3 — partner-portal fleet endpoint + frontend table + actions-CI-gate. 3 files + 2 tests. Maya 2nd-eye.
- **Commit 4:** Phase 4 — auditor-kit streaming. 1 file + manual test. Adam 2nd-eye on perf.
- **Commit 5:** Phase 5 — polling-interval constants. Trivial.

Each commit: pre-push gates green, push, CI green, /api/version verify before next phase.
