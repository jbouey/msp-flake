# Round-table: Client portal data-display architecture (P0)

**Date:** 2026-05-05
**Trigger:** User reported 4 separate inconsistencies on the client
portal post-#23 deploy, logged in as `relltherell@gmail.com` viewing
North Valley Family Practice (org `154072c1`):
  1. Top-tile **Compliance Score = 20.8%** vs site-card **= 93%**
  2. Top-tile **Controls Passed 0/0**, **0 failed**, **0 warnings**
     (vs site-card **1041 Pass / 0 Warn / 54 Fail**)
  3. Compliance Reports → "Current Compliance Score **100.0%**" with
     "**0 Passed / 0 Failed / 0 Warnings / 0 Auto-Fixed**" + per-site
     "0/0 passed = 100.0%" antipattern
  4. Evidence Archive → "**0 total bundles** — No evidence bundles found"
     (despite **155,394 rows existing in `compliance_bundles` for this org**)

Format: 5-seat principal round-table + Maya 2nd-eye consistency.
Status: DESIGN — root cause confirmed, fix requires architectural change.

---

## Verified prod state (2026-05-05)

```
North Valley Family Practice (client_org_id 154072c1-d682-4876-8af1-efa02db50720)
  Site 1: north-valley-branch-2          (status=pending)
    - 18,219 compliance_bundles all-time
    - 17,134 checks in last 24h (16,368 pass, 766 fail, 0 warn)
    - 4 go agents (0 active)
  Site 2: physical-appliance-pilot-1aea78 (status=online, "North Valley Dental")
    - 137,168 compliance_bundles all-time
    - 0 in last 24h (site stopped reporting)
    - 3 go agents (0 active, 41.67% historical compliance)
  Active incidents: 16 unresolved
  Org total: 155,394 compliance_bundles in DB

Yet the user-facing UI shows:
  Top-tile compliance score:  20.8%   (REAL value: ~95.5% bundle / ~73% blended)
  Top-tile controls:          0/0     (REAL: 17,134 checks)
  Top-tile failed/warnings:   0 / 0   (REAL: 766 fail / 0 warn)
  Reports page score:         100.0%  (no source data → 100% antipattern)
  Reports page totals:        0/0     (RLS-filtered to zero)
  Evidence Archive:           0 bundles (REAL: 155,394)
```

Per-site compliance-health endpoint shows 93% with `1041 Pass / 0 Warn /
54 Fail` because it uses a DIFFERENT connection helper.

---

## Camila (DBA) — root cause

**The `org_connection()` helper is fundamentally incompatible with
site-RLS-scoped tables.** This is a one-line cause for FOUR symptoms.

### What `org_connection` does (`tenant_middleware.py:229`)
```python
SET LOCAL app.current_org   = <org_id>
SET LOCAL app.is_admin      = 'false'
SET LOCAL app.current_tenant = ''         # ← deliberately empty
```

### What `compliance_bundles` RLS policy says (`pg_policy`)
```sql
admin_bypass:     current_setting('app.is_admin') = 'true'
tenant_isolation: site_id::text = current_setting('app.current_tenant')
```

### Result
`is_admin='false'` → admin_bypass denies. `current_tenant=''` →
`site_id::text = ''` matches NO rows. **Every SELECT against
`compliance_bundles` under `org_connection` returns zero rows**, even
though the table has 155K rows for this org's sites.

The application-level `WHERE s.client_org_id = $1` in the SQL is
filtering on the JOIN, but RLS filters `compliance_bundles` BEFORE the
join sees it. Net: empty.

### Affected endpoints (all under `org_connection`)
```
client_portal.py:709  /api/client/dashboard           → kpis = 0/0/0/0 → score=100 (fallback)
client_portal.py:1370 /api/client/evidence            → empty list
client_portal.py:1693 /api/client/reports/current     → 0 checks → 100% antipattern
client_portal.py:1101 /api/client/sites/{id}/devices-at-risk → reads incidents (separately RLS'd)
```

The site-level `/api/client/sites/{id}/compliance-health` works because
it uses `tenant_connection(pool, site_id=site_id)` which DOES set
`app.current_tenant`. That's the 93% number.

The 20.8% top-tile is `agent_compliance['avg_compliance']` =
`AVG(overall_compliance_rate)` from `site_go_agent_summaries` —
that table has DIFFERENT RLS (or none), so the query succeeds, and
since bundle_score is None (RLS-filtered), the fallback path
`compliance_score = agent_compliance['avg_compliance']` runs.

### Fix shape
Add a per-table RLS policy that recognizes the `app.current_org` GUC:

```sql
CREATE POLICY tenant_org_isolation ON compliance_bundles FOR ALL
USING (
    current_setting('app.current_org', true) IS NOT NULL
    AND current_setting('app.current_org', true) <> ''
    AND EXISTS (
        SELECT 1 FROM sites s
         WHERE s.site_id = compliance_bundles.site_id
           AND s.client_org_id::text = current_setting('app.current_org', true)
    )
);
```

This composes with the existing `admin_bypass` and `tenant_isolation`
policies (Postgres ORs them — any matching policy permits the row).

**Apply to every table the client portal reads under `org_connection`:**
- compliance_bundles
- incidents
- execution_telemetry
- attestation_chain (auditor-kit)
- discovered_devices
- (audit) any table with RLS policy that only checks `current_tenant`

Camila's flag: every new table created with site-scoped RLS must ALSO
ship the org-scoped policy. Add a CI gate that scans `pg_policy` for
`current_tenant`-only tables.

---

## Brian (Principal SWE) — application-level audit

The data path is also riddled with **dishonest defaults** that mask
upstream failures:

### Failure-mode antipatterns

```python
# 1. /api/client/dashboard — line 798
if bundle_score is not None: ...
elif agent_compliance:
    compliance_score = agent_compliance['avg_compliance']   # ← FALLS THROUGH WITH PARTIAL DATA
else:
    compliance_score = 100.0                                # ← LIES IF NO DATA AT ALL

# 2. /api/client/reports/current — line 1753
score = round((passed / total) * 100, 1) if total > 0 else 100.0   # ← 0/0 = 100% LIE

# 3. /api/client/reports/current — line 1775 (per-site)
"score": round((sp / st) * 100, 1) if st > 0 else 100.0            # ← same

# 4. /api/client/sites/{id}/compliance-health — line 1045
overall = round(overall_sum / cats_with_data, 1) if cats_with_data > 0 else None
# ← THIS ONE IS HONEST (returns None) but the frontend may display 0% or hide
```

The "0/0 = 100%" pattern is **the most dangerous shape** for a
compliance product: a customer with no data displayed sees a "perfect"
score. They have no idea the substrate isn't actually monitoring them.

Brian's veto: every score-bearing endpoint must distinguish between
"compliant" and "no data". The contract should be:
  - If real data exists: real score
  - If NO data: `score: null` + `status: "no_data"` + UI shows "—" or
    "Awaiting first scan" with timestamp of last seen anything

### Belt-and-suspenders fix

In addition to the RLS fix, make every score endpoint:
1. Return `score: null` when source set is empty
2. Return `status: 'no_data' | 'partial' | 'healthy'` so frontend can
   show "—" rather than misleading 100%
3. Include `data_freshness: { source: bundles|agent|incidents, last_at: ts }`
   so the user knows WHY the score is what it is

---

## Linda (PM) — what's missing for the customer

### Current customer experience (verified by user screenshots)
Top tile says 20.8%. Site card says 93%. Reports page says 100.0%.
Evidence Archive says 0 bundles. **Customer cannot tell which is
correct, and three of four are flat-out wrong.**

This is the worst possible experience for a compliance product:
**conflicting numbers undermine trust** more than missing data ever
would. An auditor seeing this would not certify the platform.

### What MUST display correctly (P0)
| Surface | What customer expects to see | Source of truth |
|---|---|---|
| Top tile: compliance_score | Real-time aggregate across all sites | `compliance_bundles` last 24h, blended with agent_compliance, with `null` honest-default |
| Top tile: controls passed/failed/warnings | Real counts | Same query, distinct by (site, check_type, hostname) most-recent |
| Per-site card | Real per-site score from same source | Same SQL, scoped to one site |
| Compliance Reports → Current | Same as top tile but more detail | SAME QUERY — should be a thin view over the same data |
| Compliance Reports → Site Breakdown | Per-site real numbers | SAME QUERY scoped per site |
| Evidence Archive | List + count of compliance_bundles | Direct table read |

### What's MISSING entirely (per user complaint)
1. **No real Compliance Report** — the Reports page is showing the
   "Current snapshot" which is a different concept from a monthly
   audit-ready report. PM needs to decide: do we ship a monthly PDF/HTML
   report? A 90-day report? Both? The customer expectation is that
   "Compliance Reports" is the page they generate WHEN AN AUDITOR ASKS.
2. **No drill-down** — top-tile shows aggregate, but customer can't
   click and see "what 766 checks failed" — they have to navigate to a
   specific site, then to per-device, then per-check.
3. **No trend visualization** at the top tile (it's there per-site)
4. **No "what does 73% mean for me"** explanation — score with no
   context is just a number. Auditor-kit linkage explains the math.

### Linda's flag
The Compliance Reports page as currently shipped is misleading and
should be MARKED experimental until either fixed or hidden. Customer
sees "100.0%" + "0 Passed" — that's a contradiction in terms that
breaks trust on first glance.

---

## Steve (Security) — chain-of-custody implications

### Compliance score IS the cryptographic chain endpoint
Each `compliance_bundles` row is Ed25519-signed + hash-chained +
OTS-anchored (per CLAUDE.md inviolable rule). The dashboard's
`compliance_score` is the user-facing endpoint of that chain.

**If RLS hides 100% of bundles from the customer's view, the chain is
visually broken** — even though the chain itself is intact in the DB.
The customer's auditor would query the platform, see "0 bundles", and
conclude the platform ISN'T producing evidence — when in reality the
substrate is producing 17K checks per 24h that the customer's UI just
can't see.

This is a chain-of-custody DISPLAY break, not a chain break. But the
operational impact is identical from the customer's POV.

### Steve mitigations
1. **The RLS fix MUST also apply to `auditor_kit` endpoints.** If the
   customer downloads the auditor kit, it MUST contain ALL bundles for
   their org's sites — anything less is a partial chain that the
   auditor will reject.
2. **No silent-fallback severity** — if a query under `org_connection`
   returns 0 rows AND `app.is_admin='false'` AND a known table is
   site-RLS'd, that should fire a P0 substrate alert: "client portal
   under-displaying data due to RLS misalignment".
3. **Honest scores in audit-class displays** — same rule as legal lang:
   never lie or imply compliance you don't have. "100% with 0 data" is
   a lie even if technically derived from `0/0 default`.

---

## Adam (CCIE) — operational posture

### How long has this been broken?
Per `git blame`, `org_connection` and the RLS policy on
`compliance_bundles` both predate Session 200. This has been silently
broken for every org accessing the dashboard since whenever the RLS was
added — likely months. North Valley + relltherell@gmail just made it
visible because Jeff was looking at a fresh customer view.

### Why didn't anyone notice?
- Operator (Jeff) views via admin endpoints which use `admin_connection`
  with `is_admin=true` → RLS bypassed → numbers correct
- Customers who logged in saw 100% / 0 bundles and probably assumed
  "we just got onboarded, data hasn't flowed yet"
- No alarm fires when client-portal returns suspiciously-low data

### Adam's fix shape
1. Add a substrate invariant: `client_portal_zero_evidence_with_data`
   sev2. For each org with `compliance_bundles` rows in last 7d, fire
   if a representative client-portal API call returns `total: 0`.
2. Burn-in test in CI: synthetic-org with synthetic bundles, hit
   `/api/client/evidence` with a synthetic session, assert non-empty.

---

## Maya (consistency 2nd-eye) — adversarial review

### PARITY violations
- Three different score endpoints, three different formulas, three
  different defaults: **major PARITY violation**. Customer sees
  contradictory numbers from substrate that should agree.
- The "0/0 = 100%" antipattern appears in TWO endpoints with the SAME
  logic — should be a shared helper that returns `null` consistently.
- `score is None` vs `score: 0` vs `score: 100.0` vs `score: '—'` — UI
  treats these differently in 4 places. **Unified contract required.**

### DELIBERATE_ASYMMETRY (allowed)
- Per-site card has different metric scope (single-site detail) than
  top tile (org aggregate). Different presentations are fine; different
  numbers for the same conceptual metric on the same org are NOT.

### DIFFERENT_SHAPE_NEEDED — Maya pushback
**Maya P0 #1:** the entire "Compliance Reports → Current" page should
be DELETED or rewritten. It duplicates the dashboard top tile but with
worse defaults and zero added value. PM call (Linda): keep + rebuild
as the auditor-export entry point, OR delete + collapse into the
dashboard.

**Maya P0 #2:** Evidence Archive needs an empty-state that
distinguishes "0 bundles because RLS is broken" from "0 bundles because
this is a fresh org". Today: identical UI for both. After fix: the
former should be impossible; the latter should say "Awaiting first scan
— next check expected at <ts>".

**Maya P0 #3:** The compliance_score blend formula
`(bundle * 0.7 + agent * 0.3)` is undocumented in user-facing copy.
Customer sees 73% and has no idea the formula. The copy.ts source-of-
truth should explain the math AND the customer should be able to
toggle "show source breakdown" to see "Drift checks: 95.5% (16,368/17,134) +
Workstation agents: 20.8% (avg 2 sites) → blended 73.1%".

### VETOED items
- Maya VETO any "fall back to 100% with no data" pattern. Lying-to-
  customer with safe-looking number is the worst option.
- Maya VETO removing RLS — the policy is correct in intent; the
  helper (`org_connection`) is the layer that needs fixing.

---

## Implementation plan — staged

### Stage 1 (P0, ship today): RLS org-policy + honest defaults
- **Migration 278** — add `tenant_org_isolation` policy to
  compliance_bundles + execution_telemetry + incidents + any other
  `org_connection`-touched site-RLS table
- **client_portal.py** — replace `100.0` defaults with `None`/`null`
  + add `status: 'no_data' | 'partial' | 'healthy'` field
- **Frontend** — show "—" or "Awaiting first scan" instead of "100%"
  when `score is null`
- **CI gate** — scan `pg_policy` for tables with only `current_tenant`-
  scoped policies + assert there's a corresponding `current_org` policy
  if the table is touched by `org_connection` callers
- **Behavior smoke test** — synthetic-org, hit
  `/api/client/{dashboard,evidence,reports/current}` under
  org_connection auth, assert non-zero counts when bundles exist

### Stage 2 (P1, this week): unified scoring helper
- One `compute_compliance_score(...)` function shared by all three
  surfaces — same window, same statuses, same formula, same defaults
- All three endpoints become thin wrappers
- copy.ts gains `complianceScore.formula.explanation` + UI link to it

### Stage 3 (P2, next sprint): proper Compliance Reports
- PM (Linda) decides: monthly PDF? 90-day rollup? Auditor-export ZIP?
- Whatever ships SUPERSEDES the current "Compliance Reports → Current"
  page (which is a poorly-defined duplicate of the dashboard)

### Stage 4 (P3, ratchet): substrate invariant
- `client_portal_zero_evidence_with_data` sev2 invariant fires when
  an org with bundles in last 7d gets `total: 0` from client-portal
  API. Catches future RLS regressions.

---

## Verdict matrix

| Reviewer | P0s | P1s | Verdict |
|---|---|---|---|
| Camila | 1 (org RLS policy on 4+ tables) | 1 (CI gate) | APPROVE_AFTER_DECISION |
| Brian | 1 (honest defaults) | 1 (unified helper) | APPROVE_AFTER_DECISION |
| Linda | 1 (Reports page is dishonest) | 1 (drill-down + trend at top) | APPROVE_AFTER_DECISION |
| Steve | 1 (chain-of-custody display break) | 2 (audit-kit + alert) | APPROVE_AFTER_DECISION |
| Adam | 1 (substrate invariant) | 0 | APPROVE_AFTER_DECISION |
| Maya | 3 (Reports rewrite/delete + Evidence empty-state + formula transparency) | 0 | NEEDS_DECISION_THEN_APPROVE |

**Status:** All 6 reviewers agree the architectural fix is required.
Maya's P0s are product-decisions for Linda. Stage 1 (RLS + honest
defaults + CI gate) is unanimously P0-ship-now and is what unblocks
the customer-facing trust break TODAY.

---

## Decisions needed before Stage 1 ships

1. **Confirm the RLS policy shape** — Camila's draft uses `EXISTS …
   FROM sites WHERE …` which is correct but is a per-row subquery.
   Acceptable performance for the typical client-portal query
   patterns? (Verified: `compliance_bundles` query is already filtered
   by `cb.site_id = ANY($1)`, so the subquery only runs over the
   matching rows. ~0 perf impact.)
2. **Frontend null-handling shape** — show "—" or "Awaiting first
   scan" or "No data yet"? Linda decision.
3. **Compliance Reports page** — delete + collapse vs rewrite as
   auditor-export entry? Linda decision (P2).

---

## What to ship FIRST (15-min P0)

If we want Jeff to see real numbers on relltherell@gmail right now,
the minimum viable patch is migration 278 with the `tenant_org_isolation`
policy on `compliance_bundles` alone. That single change fixes:
  - Top-tile compliance_score (will show ~73%)
  - Top-tile controls passed/failed/warnings
  - Compliance Reports → Current
  - Evidence Archive (will show 155K bundles)

The honest-defaults + frontend null-handling + CI gate land as a
follow-up commit in the same session.
