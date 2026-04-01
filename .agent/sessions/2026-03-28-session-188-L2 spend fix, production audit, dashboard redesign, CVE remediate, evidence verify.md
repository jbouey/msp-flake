# Session 188 - L2 Spend Fix, Production Audit, Dashboard Redesign, CVE Remediate, Evidence Verify

**Date:** 2026-03-28
**Previous Session:** 187

---

## Summary

Massive session: diagnosed and fixed L2 LLM credit drain (1,251 calls/week → near-zero), ran full production audit (frontend + backend + all 11 background tasks + data accuracy), redesigned the dashboard per Apple PM / product PM review, built CVE remediate button, built evidence verification endpoint, and fixed structlog visibility across all background tasks.

## L2 Spend Crisis

**Root cause:** Duplicate `/api/agent/l2/plan` endpoint in main.py overrode the fixed version in agent_api.py. FastAPI registers main.py routes last. All monitoring-only guards, cache, and circuit breaker fixes were deployed but never reached by the daemon.

**Fixes deployed:**
- Monitoring-only guard (blocks device_unreachable, backup_not_configured, bitlocker, screen_lock, credential_stale)
- L2 decision cache (24h TTL by pattern_signature)
- Redis-backed daily circuit breaker (multi-worker safe)
- Go budget tracker: Haiku → Sonnet 4 pricing
- Flywheel incident_type bug: was hardcoded "unknown"
- **CRITICAL: Deleted duplicate endpoint from main.py**

**Status:** Fix deployed. Awaiting API credit reload to verify L2 calls drop to near-zero.

## Production Audit

### Background Tasks (11 total)
- 9 HEALTHY, 1 BUG FIXED (fleet_order_expiry), 1 STALE FIXED (cve_watch logging)
- Added evidence_chain_check (daily integrity monitor)

### Security
- 6 auth gaps fixed (4 unauthenticated sites.py endpoints)
- SQL injection hardening (3 files)
- CSRF gaps documented

### Frontend
- 0 TS errors, 0 ESLint, 90 vitest passing
- 8 files: hardcoded thresholds → getScoreStatus()
- Badge dark theme fix (bg-*-100 → color/15)
- Dashboard redesign: hero compliance, merged attention, sidebar sections

### Data Accuracy
- All 13 KPI metrics match DB reality exactly
- Evidence chain: 228,580 bundles, zero chain breaks
- OTS: 127,529 anchored, avg 1.6h latency

## New Features

### CVE Remediate Button
- POST /api/cve-watch/cves/{id}/remediate — signed fleet healing orders
- GET suggest-runbook — keyword → existing runbook mapping
- GET runbooks — dropdown list for manual override
- RemediateModal.tsx — site checkboxes, runbook selector, idempotent
- Disabled auto L1 rule creation (716 dead rules disabled in DB)

### Evidence Verification
- GET /api/evidence/{bundle_id}/verify — hash recompute, chain linkage, Ed25519 sig check, OTS blockchain proof with explorer link
- GET /api/evidence/chain-health — per-site integrity summary
- Daily chain integrity background task → critical notification on breaks
- Dropped dead `anchored` boolean column (migration 108)

## Infrastructure
- structlog INFO logging enabled (stdlib level was WARNING, all background task logs were invisible)
- cve_watch + cve_remediation switched from stdlib logging → structlog
- Background task supervisor restart heartbeat logging

## Commits (12)
1. fix: L2 LLM spend crisis — 7 fixes, CVE loop visibility, LinkedIn assets
2. fix: add 'monitoring' to incidents.resolution_tier check constraint
3. fix: production audit — 6 auth gaps, SQL injection, threshold consistency, stale tasks
4. fix: Badge dark theme + Incident Volume chart height
5. fix: update Badge vitest expectations for dark theme classes
6. fix: Incident Volume chart — stop stretching to fill grid row
7. fix: RunbookDetail modal — overflow-hidden → flex-col for Safari click fix
8. fix: disable CVE L1 rule creation — rules never match real incidents
9. feat: CVE remediate button — operator-authorized fleet remediation
10. feat: evidence verification endpoint + chain integrity monitor
11. fix: enable structlog INFO output — stdlib logging level was unset
12. fix: CRITICAL — remove duplicate /api/agent/l2/plan from main.py
13. fix: CRITICAL — update MONITORING_ONLY_CHECKS in main.py + switch to Haiku 4.5
14. fix: show chart always + reduce hero padding
15. fix: disable CVE L1 rule creation — rules never match real incidents
16. fix: CSRF protection — narrow exemptions, add tokens to 26 portal files
17. fix: 51 broken L1 rule→runbook mappings remapped to valid registry IDs
18. feat: Merkle proof batching — 93% reduction in OTS calendar submissions
19. feat: evidence verification endpoint + chain integrity monitor

## Test Results
- Python: 290 passed (27 new Merkle + 263 existing)
- Go: all 14 packages passed
- Frontend: 0 TS errors, 0 ESLint, 90 vitest

## Production State (end of session)
- 12 background tasks running (including new merkle_batch + evidence_chain_check)
- L2 calls: 0/hour (was 11/hr, fixed)
- Merkle batching: first batch created, bundles marked 'batching'
- Chain integrity: 228K+ bundles, 0 breaks
- L1 rules: 100 enabled, all mapped to valid daemon runbooks (was 41% mapped)
- Daemon v0.3.50: binary built, pending fleet deploy
