# P-F9 — Partner Profitability Packet (design spec)

- **Date:** 2026-05-09
- **Author:** research-agent (design only — no code)
- **Status:** DRAFT for round-table review (Steve / Maya / Carol / Coach)
- **Sibling artifacts shipped:** P-F5 portfolio-attestation, P-F6 BA-attestation, P-F7 weekly-digest, P-F8 incident-timeline
- **Persona:** Greg-the-MSP-owner. He already knows the substrate works (P-F5/F6 prove that). He wants ONE answer once a month: "is this OsirisCare deployment net-positive for my MSP business?"

---

## 0. Framing

P-F9 is a **business-facing** artifact. It is NOT:
- a HIPAA attestation (P-F5, P-F6 cover that)
- a clinical / ops artifact (P-F7, P-F8 cover that)
- a Stripe statement (Stripe Express dashboard does that better)

It is a **monthly P&L-style snapshot** for the OsirisCare line of business inside the MSP. Numbers come from the substrate's own subscription + commission ledger — NOT from the MSP's own accounting system (we don't see clinic-side revenue).

> **Honest framing line that MUST appear on page 1:**
> "Estimated profitability of the OsirisCare line of business based on substrate-visible subscription + commission data. Excludes your MSP labor cost, clinic-side billing, and any service revenue you bill outside OsirisCare."

That sentence is the legal-language gate (no "guaranteed margin", no "audit-supportive", no "verified profit").

---

## 1. What's IN the packet — 8 metrics

All amounts are integer cents. All `partner_id` filters are mandatory (RLS + WHERE belt-and-suspenders).

### 1.1 Active MRR (cents)
- **Source:**
  ```
  SELECT COALESCE(SUM(plan_price_cents),0)
    FROM subscriptions sub
    JOIN sites s ON s.site_id = sub.site_id
   WHERE s.partner_id = $1
     AND sub.status IN ('active','trialing')
     AND (sub.cancel_at_period_end IS FALSE OR sub.current_period_end > NOW())
  ```
  `plan_price_cents` is derived from `subscriptions.plan` via a static map (essentials=49900, professional=79900, enterprise=129900, pilot=0). Stripe is the source of truth on the `subscriptions` projection; we do NOT call Stripe at render time (cost + latency).
- **Why Greg cares:** the headline number. "What's running this month."

### 1.2 Estimated Monthly Commission (cents)
- **Source:** sum of the most-recent CLOSED `partner_payout_runs` row in the period:
  ```
  SELECT payout_cents, effective_rate_bps, status
    FROM partner_payout_runs
   WHERE partner_id = $1 AND period_start = $2
  ```
  If no row exists yet for the current period (period not closed), use a **provisional** computation via `compute_partner_commission(partner_id, period_start, period_end)` (the SQL function added in mig 233) and **clearly label it `(provisional)`**.
- **Why Greg cares:** this is the dollar amount that will hit his Stripe Connect bank account.

### 1.3 Effective Commission Rate (bps → %)
- **Source:** `partner_payout_runs.effective_rate_bps` (or function output if provisional).
- **Why Greg cares:** the ladder rewards growth. Seeing "3,800 bps = 38%" tells him whether he's near the next ladder rung. The packet should append a one-line "next rung is 4,200 bps at 25 active clinics — you have 22" hint.

### 1.4 Active Clinics (count)
- **Source:**
  ```
  SELECT COUNT(DISTINCT s.client_org_id)
    FROM sites s
    JOIN subscriptions sub ON sub.site_id = s.site_id
   WHERE s.partner_id = $1
     AND s.status != 'inactive'
     AND sub.status IN ('active','trialing')
  ```
  Note `s.status != 'inactive'` per CLAUDE.md RT33 P1 portal-list filter rule.
- **Why Greg cares:** clinic count is the customer-acquisition denominator and drives the commission ladder rung.

### 1.5 Active Sites (count) and Avg Sites-per-Clinic
- **Source:** same join as 1.4 but `COUNT(DISTINCT s.site_id)` and a derived ratio.
- **Why Greg cares:** a clinic with 4 sites is more profitable than 4 single-site clinics on the same plan tier — proxy for stickiness.

### 1.6 Plan Mix (rows: pilot / essentials / professional / enterprise)
- **Source:**
  ```
  SELECT sub.plan, COUNT(*) AS n, SUM(plan_price_cents) AS mrr_cents
    FROM subscriptions sub JOIN sites s ON s.site_id = sub.site_id
   WHERE s.partner_id = $1 AND sub.status IN ('active','trialing')
   GROUP BY sub.plan
  ```
- **Why Greg cares:** upsell-target view. "I have 12 essentials → upsell 3 to professional = +$900 MRR".

### 1.7 Churn-Risk Watchlist (subscriptions where `cancel_at_period_end=true` OR `status IN ('past_due','unpaid','incomplete')`)
- **Source:**
  ```
  SELECT s.site_id, sub.plan, sub.status, sub.current_period_end, sub.cancel_at_period_end
    FROM subscriptions sub JOIN sites s ON s.site_id = sub.site_id
   WHERE s.partner_id = $1
     AND ( sub.cancel_at_period_end = true
        OR sub.status IN ('past_due','unpaid','incomplete') )
   ORDER BY sub.current_period_end ASC
  ```
  Show **opaque site labels** (SHA-256 prefix of site_id, same convention as P-F7's top_noisy_sites — round-table 2026-05-06 opaque-mode rule).
- **Why Greg cares:** "where do I stop the bleeding this week?" — actionable, not just diagnostic.

### 1.8 12-Month Rolling Trend (one mini-chart, MRR + commission + clinic-count by month)
- **Source:**
  ```
  SELECT period_start, mrr_cents, payout_cents, active_clinic_count
    FROM partner_payout_runs
   WHERE partner_id = $1 AND period_start >= (CURRENT_DATE - INTERVAL '12 months')
   ORDER BY period_start
  ```
- **Why Greg cares:** "are we growing or stagnant?" One glance. ReportLab Platypus → tiny embedded sparkline (matplotlib `Agg` backend → PNG → `Image` flowable; same pattern P-F7 uses if it has charts, else use 12 numeric cells in a table — cheaper and PDF-deterministic).

> **Strong recommendation: numeric table over a chart.** Charts are a determinism risk (matplotlib version drift across CPython releases will change pixel output). A 12-cell numeric strip is byte-deterministic and Maya-clean.

---

## 2. What's OUT of scope (explicit exclusions)

Each must be written into the packet's footer or a "Not included" sidebar so a reader doesn't misread silence as zero.

- **PHI** — substrate boundary. Never present.
- **Per-clinic revenue** — the substrate sees Stripe subscription state, not what the MSP bills the clinic. Greg's clinic-side margin is private to him.
- **MSP labor cost / per-tech utilization** — we don't have payroll. P-F7 already shows incident-volume-per-site (a labor-time proxy); we do NOT multiply by an assumed hourly rate (that's a decision, not a fact).
- **Stripe payout details (transfer ids, bank-statement reconciliation, fee breakdown)** — those live in Greg's Stripe Express dashboard. We link, we don't duplicate.
- **Forecast / projection** — see §6 Not-Doing-list. Counsel-style framing: "estimated", "current period", never "projected" or "expected next month".
- **Cohort / lifetime-value math** — too easy to mislead (LTV needs assumed retention; we have <12 months of data).
- **Cross-partner benchmarking** — would require breaking partner-tenant isolation. Hard NO.
- **Tax-ready statements** — this is a management report, not a 1099 substitute.

---

## 3. One-page layout sketch

```
┌─────────────────────────────────────────────────────────────────────┐
│ {presenter_brand} logo                          MONTHLY PROFITABILITY │
│                                                  Period: 2026-04-01  │
│                                                       to 2026-04-30  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│   ACTIVE MRR          ESTIMATED COMMISSION       ACTIVE CLINICS     │
│    $14,371                   $5,460                    18           │
│                          (3800 bps · 38.0%)        (29 sites)       │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│  PLAN MIX                                                           │
│   Pilot         …  3   $0                                           │
│   Essentials    … 12   $5,988                                       │
│   Professional  …  3   $2,397                                       │
│   Enterprise    …  0   $0                                           │
├─────────────────────────────────────────────────────────────────────┤
│  CHURN-RISK WATCHLIST  (acted-upon = good)                          │
│   site:a3f9…  professional  past_due  period_end=2026-05-12         │
│   site:c102…  essentials    cancel_at_period_end                    │
├─────────────────────────────────────────────────────────────────────┤
│  12-MONTH TREND                                                     │
│  Month        MRR     Commission   Clinics                          │
│  2025-05    $ 8,200    $3,116        11                             │
│  …                                                                  │
│  2026-04    $14,371    $5,460        18                             │
├─────────────────────────────────────────────────────────────────────┤
│  Honest-framing line. Generated 2026-05-09T14:22:08Z.               │
│  Not a Stripe statement; Stripe Express dashboard is authoritative. │
│  X-Partner-Profitability-SHA256: 7a3b8e…                            │
└─────────────────────────────────────────────────────────────────────┘
```

- Header parity with siblings (presenter_brand, period).
- 3-card hero KPI strip (MRR / Commission / Clinics).
- Body: Plan Mix table + Churn-Risk Watchlist + 12-month numeric trend.
- Footer: framing-line, generated_at, deterministic SHA-256 envelope header.

### 3.1 Determinism contract (parity with auditor-kit, RT33 round-table)

- `generated_at` MUST derive from chain-head latest (`MAX(partner_payout_runs.computed_at)` for this partner) — NOT wall-clock — when payout-runs exist; fall back to wall-clock ONLY when no run has ever been computed (provisional first-month case), and label that obviously.
- `kit_version` = `1.0` pinned across response header + payload.
- ReportLab Platypus (matches P-F5/P-F7 pattern) — DO NOT use WeasyPrint for this artifact unless the rest of P-F5/F6 are migrated; mixing renderers across siblings creates PDF-determinism diffs.
- `X-Partner-Profitability-SHA256` = SHA-256 of the rendered PDF bytes, set in response headers (parity with P-F6's `X-Attestation-Hash`).

---

## 4. Adversarial concerns (5)

### 4.1 Steve — "MRR" misdefinition is the #1 liability
Including `trialing` subscriptions in headline MRR overstates revenue. Including `past_due` understates it. **Decision:** count `active` and `trialing` in MRR (Stripe convention), but show `past_due` separately in the watchlist. Never blend cancelled sub revenue from the period — `WHERE canceled_at IS NULL OR canceled_at > period_end`. Add unit tests pinning each of {active, trialing, past_due, canceled, incomplete} to its expected column.

### 4.2 Carol — RLS leak path, partner_payout_runs already has RLS but `subscriptions` does NOT (mig 224)
The `subscriptions` table joins to `sites` for the partner_id filter. If a future refactor drops the explicit `s.partner_id = $1` clause and relies on RLS, **partner A could see partner B's subscriptions** because subscriptions has no RLS. Mandate: every query in `partner_profitability.py` hard-filters `s.partner_id = $1` AND uses `admin_transaction()` (Session 212 rule — multi-statement admin path). Pin a regression test that runs the helper under partner-RLS context and asserts zero rows for cross-partner reads.

### 4.3 Maya — legal-language traps
Forbidden in this packet: "guaranteed", "expected", "projected", "audit-supportive", "verified profit", "ROI". Allowed: "estimated", "active", "current period", "based on subscription state". Reuse the auditor-kit `cleanAttentionTitle()` style approach — pin a static-allowlist test on the rendered PDF text. The honest-framing line in §0 is the load-bearing copy.

### 4.4 Coach — "commission" double-count between provisional + closed runs
If the period is mid-month, the row in `partner_payout_runs` may not yet exist; we'd compute provisionally. If a later refresh of the SAME packet happens AFTER the closed run lands, the number changes. **Decision:** every packet snapshots the value AS OF its `generated_at`. Two pulls in the same period CAN show different numbers — disclose this in a footnote ("Commission is provisional until period close on {period_end}; the final payout run will appear in next month's packet."). Determinism contract still holds within a single render.

### 4.5 Steve — Stripe-Connect onboarding-incomplete edge case
For partners where `stripe_connect_status != 'payouts_enabled'`, commission is being COMPUTED but not PAID. Showing them a $5,460 commission number without flagging that the money won't actually move is misleading. **Mandate:** if `partners.stripe_connect_status NOT IN ('charges_enabled','payouts_enabled')`, prepend a yellow callout: "Commission is being accrued but Stripe Connect onboarding is incomplete — payouts will start once you finish setup at {portal-link}."

---

## 5. Cadence + auth + endpoint shape

### 5.1 Endpoint
- `GET /api/partners/me/profitability/monthly.pdf?period=YYYY-MM` (default = previous closed month)
- File location: new module `mcp-server/central-command/backend/partner_profitability.py` + thin handler in `partners.py` matching the P-F6 / P-F7 wrapper pattern (~30 lines).

### 5.2 Auth
- **`require_partner_role("admin", "billing")`** — admin AND billing role. This is the FIRST partner artifact where billing role gets through (CLAUDE.md RT31 site-state class doesn't apply — this is partner-org-state, but read-only and revenue-scoped, so billing has a legitimate need-to-know; this is the line item billing-role exists for).
- **NOT** `tech` — technicians don't need MSP-level revenue figures. Pin in `test_no_partner_mutation_uses_bare_require_partner` companion / new positive test `test_profitability_endpoint_admin_or_billing_only`.
- Read-only (no state mutation, no chain attestation, no migration needed beyond what mig 233 + 235 already give us).

### 5.3 Cadence
- **On-demand** is sufficient. No "issue-once-per-month" semantics — the packet is re-rendered live from the ledger on each call (parity with P-F6 which renders live from the roster, vs. P-F5 which mints a new attestation each call).
- Recommend a frontend banner "Pull next month's packet on or after {period_end + 2 days}" so Greg knows when the closed `partner_payout_runs` row will be available.
- No email-send-on-schedule in v1 — operator-alert hooks are for chain-gap incidents, not P&L.

### 5.4 Rate-limit
- 10/hr per (partner, user) — same shape as P-F7 weekly-digest. Higher than P-F5 attestation (5/hr) because no chain-write happens; lower than P-F8 timeline (60/hr) because this is a heavier render.
- Bucket key: `caller_key=f"partner_user:{caller_user_id}"` (per-identity isolation, matches RT33 round-table parity).

### 5.5 Audit log
- Every successful render writes `admin_audit_log` with `action='partner_profitability_render'`, `username = partner_user.email`, `target = partner_id`, `details = {period_start, period_end, mrr_cents, payout_cents}`. Best-effort write — failure logs ERROR but does not 500 the customer. Mirrors auditor-kit access-log pattern from RT33.

---

## 6. NOT-DOING list for v1 (P-F9.5 candidates)

- **Forecasting / projection** ("at this growth rate you'll hit $X by Y") — fundamentally a model output, not a fact. Defer to a future P-F9.5 with EXPLICIT model-card disclosure.
- **Cohort retention curves** — needs ≥12 months of data per cohort. Re-evaluate Q4 2026.
- **Per-tech profitability** (which tech closes the most upsells, etc.) — adjacent to labor-cost which is out of scope.
- **Interactive web dashboard version** — design says PRINTABLE PDF for parity with siblings. Frontend can show a card summary, but the artifact is the PDF.
- **Cross-period diff narrative** ("MRR up $1,200 vs. last month because…") — LLM-generated narrative is a determinism + hallucination liability. Numbers only in v1.
- **External billing-system integration** (QuickBooks export, etc.) — out. Greg can take the numbers and key them in.
- **Multi-currency** — `partner_payout_runs.currency` defaults `usd` and we have no non-US partners. If/when an EU/UK partner onboards, revisit.
- **Tax forms** (1099-K substitute) — Stripe issues these from Express accounts. We do NOT generate any tax-shaped artifact.
- **Per-clinic profitability** — clinic-side billing is private to the MSP. We can show per-clinic *MRR-to-MSP* but cannot show *margin* without labor cost.

---

## 7. Round-table-at-gates checklist (CLAUDE.md feedback rule)

Before any code lands, get APPROVE/DENY from:

- **Steve (principal SWE):** MRR definition (4.1), Stripe Connect edge case (4.5), determinism contract (3.1).
- **Maya (legal/copy):** honest-framing line (§0), forbidden-terms allowlist (4.3).
- **Carol (security):** RLS belt-and-suspenders (4.2), audit-log path (5.5), opaque site labels (1.7).
- **Coach (consistency):** sibling-parity headers (`X-Partner-Profitability-SHA256` mirror), rate-limit shape, role-gate decision (admin + billing, not tech).

Each gate writes its verdict into the design doc inline before code begins.

---

## 8. Open questions for the human reviewer

1. **Plan-price map source of truth?** Right now I propose a static dict in `partner_profitability.py` matching the four Stripe `lookup_keys`. Alternative: query Stripe at render time (latency + cost). Or: add a `plan_price_cents` column to `subscriptions` (schema change). My preferred answer is static dict + a unit test that asserts the dict matches `stripe.Price.list(lookup_keys=[...])` in CI when STRIPE_SECRET_KEY is present.
2. **Should the trend table use `partner_payout_runs` (computed monthly) or recompute MRR-snapshots from `subscriptions.created_at` history?** Former is fast + canonical; latter could cover months before mig 235 landed. Recommend former — accept that history starts when Stripe Connect went live.
3. **Currency display:** US-only confirmed? If yes, hard-pin `currency='usd'` and reject any `partner_payout_runs.currency != 'usd'` row with a banner "non-USD currency detected — packet not yet localized."
4. **Does the partner portal have a "Profitability" nav slot, or do we add one in the frontend along with the endpoint?** The latter is straightforward but adds a UI scope item — confirm before including in the P-F9 estimate.

---

## File path
`/Users/dad/Documents/Msp_Flakes/audit/p-f9-partner-profitability-design-2026-05-09.md`
