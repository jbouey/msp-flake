# Coach perf-efficiency sweep — client portal — 2026-05-08

User directive: *"the issue is the time to load is brutal across the entire client portal specifically attestation parts and wherever it's checking data."*

Adversarial audit on the client-portal load path. Findings below are severity-ranked; each cites the file:line + measured cost where known.

## Verdict summary

| Surface | Hot path | Severity | Root cause |
|---------|----------|----------|------------|
| `/client/dashboard` page load | `/api/client/dashboard` | **CRITICAL** | `compute_compliance_score` 2.4s on 155K bundles per CLAUDE.md profile + no caching |
| `/client/dashboard` page load | client-side fetch | **MAJOR** | No React Query: every nav re-fetches dashboard fresh |
| `/client/attestations` page mount | `/api/client/privacy-officer` | MINOR | Single fetch, fast endpoint, but no caching → re-fetches on every nav |
| `/client/reports` "current" tab | `/api/client/reports/current` | **MAJOR** | Same `compute_compliance_score` call → same 2.4s cost. No cache shared with dashboard. |
| `/client/sites/:id/compliance-health` | same helper | MAJOR | Per-site call, same 2.4s class |
| Auditor kit download | `/api/evidence/sites/{id}/auditor-kit` | INFO | StreamingResponse correct (Session 218); not a perf issue |

## Critical finding — REC-1

`compliance_score.py:124` is THE source of truth for the customer-facing compliance number (CLAUDE.md "Canonical compliance-score helper Session 217 RT25 + RT30"). It runs `jsonb_array_elements(cb.checks)` over every `compliance_bundles` row in window (default 30 days), then `DISTINCT ON (site_id, check_type, hostname) ORDER BY checked_at DESC`.

**Profiled cost** per CLAUDE.md: **4.7s → 2.4s** on a 155K-bundle org after the round-table-30 window-bounding optimization. Still 2.4s is brutal for the perceived-dashboard-load latency.

Three customer-facing surfaces (`/api/client/dashboard`, `/api/client/reports/current`, `/api/client/sites/{id}/compliance-health`) ALL invoke this with the same `(site_ids, window_days)` and pay the full 2.4s independently. **No cache.**

**Fix:** in-memory 60s TTL cache shared across surfaces. Cache key includes the sorted `site_ids` tuple to preserve RLS-isolation. Auditor-export `window_days=None` BYPASSES (auditor needs fresh chain reads + are infrequent).

**Estimated impact:** dashboard perceived latency 2.4s × 3 = 7.2s → 2.5s + ~50ms × 2 = ~2.6s on cold path; ~150ms total on warm-cache path. Maria opens dashboard, navigates to Reports, drills to a site — all three paint within ~150ms after first warm.

## Major findings — REC-2 + REC-3

**REC-2 — ClientDashboard React Query migration.** `ClientDashboard.tsx:113` uses raw `useState + useEffect + fetch`. Every tab navigation back to `/client/dashboard` re-fires three fetches (dashboard / notifications / agent-info). React Query is already in the project (admin pages use it; CLAUDE.md frontend section pins the pattern). Convert to `useQuery({queryKey, staleTime: 60_000})` so navigation within stale-window paints instantly.

**REC-3 — ClientAttestations PO-designation cache.** `ClientAttestations.tsx:227` (post-D6 wave-1) fetches `/api/client/privacy-officer` on every mount. ~50ms cost. PO designation changes very rarely (~once at org setup). 5-minute staleTime is safe.

## Cross-cutting patterns

- **P-A — No backend caching anywhere.** Codebase has no Redis/in-memory cache layer for repeated reads. `compute_compliance_score` is the most-egregious because of cost. Introduce process-local TTL cache helper (`backend/perf_cache.py` with `cache_get` / `cache_set` / `cached_call`); upgrade to Redis when Central Command scales out.
- **P-B — No React Query in client-portal.** All 6 client pages use raw `useState + useEffect + fetch`. Sprint-N+3 task: migrate ClientReports + ClientEvidence + ClientCompliance + ClientHealingLogs.

## Recommendations (this commit)

REC-1 backend score cache. Single highest-impact change. Closes the user's "brutal load time" complaint at its root cause for the dashboard / reports / per-site triad.

REC-2 + REC-3 frontend React Query queued for next iteration. Frontend changes are additive + can ship in a follow-up commit while REC-1 deploys + customers feel the immediate backend speedup.

## Round-table 2nd-eye on REC-1

- **Steve:** APPROVE — bounded; no semantic change; auditor-export path stays uncached.
- **Adam-DBA:** APPROVE — 2.4s → 50ms via in-memory; reduces DB load; partition pruning preserved.
- **Maya:** APPROVE-with-condition — cache key MUST include site_ids tuple (not just org_id) to prevent cross-org leak via shared cache. RLS still authoritative at fetch.
- **Carol:** N/A — no copy.
- **Coach:** APPROVE — sibling pattern (Redis-style TTL caching) is standard; no API contract change.
- **Sarah-PM:** APPROVE — addresses Maria's "brutal load" complaint at its root cause.

## Deferred to Sprint-N+3

- REC-2 ClientDashboard React Query.
- REC-3 ClientAttestations PO-designation cache.
- `site_compliance_summary` materialization (multi-site dashboard).
- Migrate the other 5 client pages to React Query.
- `perf_cache.py` Redis upgrade when Central Command scales out.
