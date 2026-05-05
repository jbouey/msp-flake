# Round-table: Client portal perf + Auditor Kit 404 (P0)

**Date:** 2026-05-05 (post Session 217 ship)
**Trigger:** User reported (1) auditor-kit returns `{"detail":"Not Found"}`,
(2) client portal initial load + Reports page load is "tremendously
long". Both shipped during round-table 25 closure (Stage 2 + Stage 3).

## Diagnosis

### Bug #1 — auditor-kit 404 (P1, trivial fix)

Stage 3 frontend (`ClientReports.tsx:237`) hits:
```
/api/client/sites/{site_id}/auditor-kit
```
Backend route (`evidence_chain.py:4556`):
```
@router.get("/sites/{site_id}/auditor-kit")  # router prefix=/api/evidence
→ /api/evidence/sites/{site_id}/auditor-kit
```

I extended `require_evidence_view_access` to recognize the new
`osiris_client_session` cookie shape (commit `cfa24659`) but I forgot
to update the frontend URL. The auth gate works; the URL is wrong.

**Fix:** one-line change in ClientReports.tsx — `/api/client/...` →
`/api/evidence/...`.

### Bug #2 — slow dashboard + reports load (P0, architectural)

`EXPLAIN ANALYZE` of the canonical `compute_compliance_score()` query
under North Valley's 2 sites:

```
Execution Time: 4749.615 ms
JIT:
  Functions: 106
  Inlining: 76 ms
  Optimization: 450 ms
  Emission: 300 ms
  Total JIT: 836 ms
Function Scan on jsonb_array_elements: 155,538 loops
```

Why slow:
- `compliance_bundles` partitioned, 155K bundles for the org
- `jsonb_array_elements(cb.checks)` unnests ~5 checks per bundle =
  ~777K rows
- `DISTINCT ON (site_id, check_type, hostname) ORDER BY … checked_at DESC`
  forces a sort over the full unnest output
- JIT compilation is itself ~836ms because the plan is huge (6 partitions
  scanned)

**Fired three times per page load:**
- `/api/client/dashboard` (dashboard top tile)
- `/api/client/reports/current` (Reports page)
- `/api/client/sites/{id}/compliance-health` (per-site card)

Combined ~15s on cold cache. Customer-unacceptable.

The Stage 2 unification *intentionally* uses latest-per-check all-time
because "compliance is a state, not a moving average" (Maya verdict).
The all-time correctness comes at runtime cost.

---

## Camila (DBA)

**Quick win:** time-bound the canonical query to the last 90 days. A
check that hasn't been observed in 90 days is functionally
unmonitored — its "state" is stale and a customer asking "what's my
compliance" expects recent state. Drops the unnest from ~777K rows
to ~50K rows × 5 = ~250K, and lets the partition pruner skip ancient
partitions entirely.

**Right architectural answer (deferred):** materialized
`latest_check_per_site_check_hostname` view, maintained by trigger on
`compliance_bundles INSERT`. All reads then O(1). But that's a
multi-step migration with its own round-table:
  - storage cost (per-org × per-check × per-host row count)
  - rebuild strategy when checks/hosts retire
  - retention policy for historical "what did the score look like 3
    months ago" queries

Camila's call: ship the 90-day window NOW (P0 customer experience).
Materialized view goes on the followup list.

**Plus:** add explicit `idx_compliance_bundles_site_checked_desc` on
`(site_id, checked_at DESC)` if not already present at the partition
level — partition-pruning + index-only scan should make the bounded
query <500ms even on 100K-bundle orgs.

## Brian (Principal SWE)

Three changes:
1. Add `window_days: int = 90` parameter to
   `compute_compliance_score(conn, site_ids, *, include_incidents=False, window_days=90)`.
   Default to 90, allow callers (e.g. auditor kit) to override to None
   for all-time.
2. Add `WHERE cb.checked_at > NOW() - INTERVAL '$X days'` to the
   `unnested` CTE.
3. Surface the window in the response so the frontend can show
   "Snapshot: last 90 days" copy if needed.

**Brian veto:** do NOT introduce a Redis cache as the fix. Caching a
broken query under-treats the symptom; bound the query first. Cache
is a Stage-2-of-this-round-table optimization if even 90-day perf is
unacceptable.

## Linda (PM)

Customer-facing copy must clarify the window. The dashboard tile says
"Compliance Score" with no time-bound; users assume "right now". After
the change, the response carries `window_description: "Latest result
per check, last 90 days"` — frontend should surface this on hover/click
so a customer who sees 89% knows what it represents.

For audit-export contexts (auditor kit, evidence archive download),
the all-time view remains. The customer-facing tile is necessarily a
rollup; the auditor kit is the source of truth.

## Steve (Security)

Chain-of-custody is **unaffected**. Compliance bundles older than 90
days are still:
- written + Ed25519-signed + hash-chained (immutable)
- queryable in the auditor kit (all-time)
- reachable via /api/client/evidence (paginated all-time list)

The 90-day window is a **rollup window for the dashboard headline only**,
not a retention window. Safe.

Steve's flag: the old `else 100.0` antipattern is gone (Stage 1) so
even an org with zero recent activity will correctly show
`overall_score: null + status: 'no_data'` instead of a fake 100%.
Verified via the existing test_no_dishonest_score_defaults gate.

## Adam (CCIE)

4.7s × 3 endpoints on every dashboard load is operations-class
unacceptable. After the window: target <800ms total dashboard load.
If the 90-day-bounded query is still >500ms, add the partition-aware
index Camila called out as a same-day follow-up.

Add a sev2 substrate invariant `client_portal_query_p95_breached`
that fires when the canonical query's median runtime crosses 1s
(would have caught this regression on the day Stage 2 shipped).
Deferred to a follow-up; not blocking this fix.

## Maya (consistency 2nd-eye)

### PARITY checks
- ✅ Same window applied to all three surfaces (dashboard / reports /
  per-site) — they share the helper
- ✅ Auditor kit (offline-verifiable artifact) keeps the all-time
  view via `window_days=None` override — chain integrity preserved
- ✅ Reports page UI surfaces `window_description` so the customer
  understands the bound

### DELIBERATE_ASYMMETRY (allowed)
- Headline tile = bounded rollup, auditor kit = all-time. Different
  framing for different consumers — explicit in copy.

### DIFFERENT_SHAPE_NEEDED
- **Maya P0:** the `Snapshot` interface in `ClientReports.tsx`
  + `KPIs` interface in `ClientDashboard.tsx` need `window_description`
  added to their TypeScript types so the frontend can render the
  hover/info tip without falling back to undefined.

### VETOED items
- Redis cache (Brian veto stands)
- Reducing the canonical to 24h or 7d — too aggressive, drops checks
  that legitimately run weekly (e.g. backup verification)

### Pinned via tests
- New `test_compliance_score_default_window_is_90_days` — guards the
  default so a future change can't silently revert it without test
  failure
- Existing `test_no_ad_hoc_score_formula_in_endpoints` continues to
  enforce the helper is the single compute path

---

## Verdict

Unanimous APPROVE: **ship the 90-day default window on
compute_compliance_score**, fix the auditor-kit 404 in the same
commit. Followup tasks: partition index + materialized view +
substrate p95 invariant.

## Implementation checklist

- [x] `compliance_score.py::compute_compliance_score(window_days=90)` —
      new param with documented default
- [x] WHERE filter applied in `unnested` CTE; bypass when `window_days
      is None`
- [x] Add `window_description` to ComplianceScore dataclass + propagate
      through dashboard + reports responses
- [x] Frontend `ClientReports.tsx`: change `/api/client/sites/...` →
      `/api/evidence/sites/...`
- [x] Frontend `KPIs` + `Snapshot` types: add `window_description`
- [x] New CI gate `test_compliance_score_default_window_is_90_days`
- [x] Verify p95 < 800ms via prod EXPLAIN ANALYZE post-deploy
- [ ] Followup: partition index (Camila P1)
- [ ] Followup: materialized view (Camila P2)
- [ ] Followup: substrate p95 invariant (Adam P2)
