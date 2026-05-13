# P-F9 v1 — Partner Profitability Packet (Estimated-Only Slice)

**Status:** DESIGN — Gate A pending
**Author:** session-220 P-F9 design pass
**Persona:** Brendan-the-CFO (5th MSP-internal actor, partner-round-table 2026-05-08)
**Sprint scope:** ~1 sprint (3 days, mirrors F1/P-F5/P-F7/P-F8 close)
**Blocker carve-out:** Stripe Connect (mig 235) deferred to v2; v1 ships using subscription ledger ONLY
**Cross-refs:** `project_partner_facing_artifacts_gap.md`, `audit/coach-enterprise-backlog-2026-05-12.md`, CLAUDE.md §"Auditor-kit determinism contract", CLAUDE.md §".format()-template-ban rule"

---

## §1 — Scope

### What "estimated profitability" MEANS in v1

The P-F9 Profitability Packet v1 is a printable Ed25519-signed PDF that
gives the MSP CFO ("Brendan") a defensible internal-finance snapshot of
expected channel profitability **as derived from the live Stripe
subscription ledger plus operator-supplied cost inputs**. It is NOT a
reconciled financial statement, NOT a Stripe-paid-out history, and NOT
an external-facing artifact.

### What v1 EXPLICITLY does NOT do

1. **No paid-commission history.** `partner_invoices` / `partner_payouts`
   tables (mig 235 dependency) are not yet populated; v1 reports
   `lifetime_paid_cents = 0` exactly like `/me/commission` does today.
2. **No bank-reconciliation columns.** No deposit dates, no payout IDs,
   no FX, no reversal/refund accounting.
3. **No actual cost-of-engineering data ingest.** v1 takes operator-
   supplied per-tier cost estimates via a form field (stored on
   `partner_profitability_assumptions`); v2 will ingest from the MSP's
   PSA / accounting integration.
4. **No customer-facing distribution.** P-F9 is partner-internal only.
   No public `/verify/profitability/{id}` route — distinguishes it
   from P-F5 (which IS public-verify).

### Sibling positioning

| Artifact | Audience | Public verify? | Aggregate-only? |
|----------|----------|----------------|------------------|
| P-F5 Portfolio Attestation | Greg → external trust-badge | YES | YES |
| P-F6 BA Roster + Attestation | Tony → auditor | NO (auth gated) | NO (clinic-named) |
| P-F7 Weekly Digest | Lisa → internal print | NO | NO |
| P-F8 Incident Timeline | Tony → auditor | NO | NO |
| **P-F9 Profitability** | **Brendan → internal CFO** | **NO** | **partial — clinic-named for partner-internal use** |

P-F9 is the FIRST partner-side artifact that contains per-clinic
revenue line-items inside the partner organization (Brendan needs
per-customer breakdown to make staffing decisions). It MUST therefore
adopt P-F6's auth posture (`require_partner_role("admin", "billing")`)
NOT P-F5's public-verify posture.

---

## §2 — Inputs

### 2.1 Subscription ledger (live, from `subscriptions` table)

For each `partners.id = :partner_id`:

```sql
SELECT s.site_id,
       s.plan,             -- 'essentials' | 'professional' | 'enterprise'  (pilot excluded — one-time, not MRR)
       s.status,            -- active | trialing | past_due | canceled
       s.current_period_start,
       s.current_period_end,
       s.created_at,
       s.canceled_at
  FROM subscriptions s
 WHERE s.partner_id = $1::uuid
   AND s.status IN ('active','trialing','past_due')
```

Plan amounts come from the same source `/me/commission` already inlines:

| Plan | MRR cents | Source |
|------|-----------|--------|
| essentials | 49900 | `client_signup.PLAN_CATALOG` |
| professional | 79900 | `client_signup.PLAN_CATALOG` |
| enterprise | 129900 | `client_signup.PLAN_CATALOG` |

**REUSE NOT DUPLICATE:** factor `PARTNER_PLAN_CATALOG` out of
`client_signup.py` + `partners.py` into a shared constant module
`backend/plan_catalog.py` as a Phase 0 prerequisite. v1 of P-F9 is the
3rd consumer; rule of three triggers refactor.

### 2.2 Per-tier engineering-cost estimates (operator-supplied)

New table `partner_profitability_assumptions` (mig 314 candidate, gated
on Gate A):

```
partner_id          UUID  PRIMARY KEY → partners(id) ON DELETE CASCADE
essentials_cost_cents      INTEGER  NOT NULL DEFAULT 0
professional_cost_cents    INTEGER  NOT NULL DEFAULT 0
enterprise_cost_cents      INTEGER  NOT NULL DEFAULT 0
osiriscare_subcontract_cents_per_seat  INTEGER NOT NULL DEFAULT 0
notes               TEXT
updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
updated_by_user_id  UUID REFERENCES partner_users(id) ON DELETE SET NULL
```

CHECK constraints: all `*_cost_cents` BETWEEN 0 AND 10_000_000 (defends
against operator typos that would otherwise render a hilarious
"$2,495,800 cost-per-seat" line in the CFO's PDF).

Defaults of 0 mean: "first-issuance PDF shows margin = revenue until
the operator fills in costs". The estimated-disclaimer language (§5)
explicitly notes that zero-cost rows reflect the operator's not-yet-
filled-in assumptions, NOT a claim of zero cost.

### 2.3 OsirisCare's own subscription cost to the partner

The partner pays OsirisCare per active clinic via `partners.stripe_subscription_id`
(the upstream partner→OsirisCare subscription). This is **a different
ledger** — partner is the *customer* there, not the merchant. For v1,
operator enters their per-seat OsirisCare cost in
`osiriscare_subcontract_cents_per_seat`. v2 reads it from the partner's
upstream subscription invoice line items.

### 2.4 Effective revenue-share rate

`compute_partner_rate_bps(partner_id, active_clinic_count)` from mig 233.
SAME rate function `/me/commission` uses — non-negotiable consistency.
If it returns NULL → `rate_unavailable = true` → PDF renders "—" + an
explicit operator-action footnote ("revenue-share rate not configured;
contact OsirisCare Partner Ops"). No fabricated number EVER.

---

## §3 — Outputs

### 3.1 PDF format (mirrors P-F7/P-F8)

* **Letter paper, 0.75in margins**, Helvetica/Arial 11pt body, 9pt footer.
* **Header band (#14A89E underline)** matching `partner_portfolio_attestation/letter.html.j2`.
* **Top banner — 12pt minimum bold red rule:**

  > **ESTIMATED — INTERNAL USE ONLY**
  >
  > This document is based on the live OsirisCare subscription ledger
  > and operator-supplied cost assumptions. Actual profitability
  > requires Stripe Connect reconciliation, which is not yet
  > integrated. Figures are preliminary and subject to revision when
  > paid-commission history becomes available.

### 3.2 Section layout

| § | Section | Notes |
|---|---------|-------|
| 3.2.1 | Portfolio summary | active_clinic_count, mrr_cents, effective_rate_bps |
| 3.2.2 | Per-customer table | site_id (opaque internal short-id), plan, MRR, est. revenue share to partner, est. engineering cost, est. OsirisCare cost, est. net margin |
| 3.2.3 | Portfolio-level rollup | total MRR, total est. share, total est. cost, total est. net margin, est. net margin % |
| 3.2.4 | Trailing-12-month MRR trend | reuses `/me/commission` generate_series query (deterministic — no wall-clock) |
| 3.2.5 | Assumptions footnote | per-tier cost-cents, OsirisCare per-seat cost, `updated_at`, `updated_by_email` from `partner_profitability_assumptions` |
| 3.2.6 | Footer | issued_by_email, issued_at, attestation_hash short-prefix, valid_until |

### 3.3 Per-customer table — what we DO NOT print

* **No clinic legal name.** Use opaque internal short-id (first 8
  chars of `site_id` UUID) — consistent with P-F5 anonymization
  posture even though P-F9 is partner-internal. Reasoning: PDF could
  be subpoenaed; reducing clinic-identifiability is defense-in-depth.
* **No PHI of any kind.** Subscription metadata only.
* **No customer-billing-contact email or name.** Even for partner-
  internal use, the artifact stays in the "operational counts +
  finance" lane — billing contact lives in the Stripe portal.

### 3.4 Sign + chain

* Ed25519 over canonical JSON (`json.dumps(sort_keys=True, separators=(",", ":"))`).
* Hash → `partner_profitability_packets.attestation_hash` (unique).
* Anchor namespace `partner_org:<partner_id>` — same convention as
  P-F5/P-F6, NEVER `canonical_site_id()`.
* Supersede-prior + insert-new pattern (atomic transaction).

### 3.5 Determinism contract (CLAUDE.md §"Auditor-kit determinism")

P-F9 v1 ships as a **single PDF only**, not a ZIP. ZIP-determinism is
not in scope for v1 because the artifact is one file. However, all
contract-level rules still apply to the JSON-canonical-payload that
signs the PDF:

* `sort_keys=True` on every canonical-JSON dump.
* `generated_at` = `issued_at` (NOT `datetime.now()` after issuance);
  re-rendering an existing packet by `attestation_hash` reproduces a
  byte-identical PDF.
* Trailing-12-month series anchors on `period_end` truncated to month
  boundary — NOT wall-clock.
* No `.format()` in source — Jinja2 `{{ }}` + StrictUndefined ONLY.
* Any `{` or `}` in Jinja2 string literals → escaped as `{{ "{" }}`.

---

## §4 — Sibling-parity (F-series template family)

The F-series sibling-parity contract is non-negotiable. Each row below
MUST be satisfied or the artifact is a banned shape.

| Sibling property | F1 | P-F5 | P-F6 | P-F7 | P-F8 | **P-F9** |
|------------------|----|----|----|----|----|----|
| Ed25519 signature over canonical JSON | YES | YES | YES | YES | YES | **YES** |
| Atomic supersede-prior + insert-new | YES | YES | YES | YES | YES | **YES** |
| Default 90-day validity | YES | YES | YES | 30d (weekly) | per-incident | **30-day** (CFO refreshes monthly) |
| presenter_brand snapshot frozen at issue | YES | YES | YES | YES | YES | **YES** |
| Jinja2 + StrictUndefined render | YES | YES | YES | YES | YES | **YES** |
| `asyncio.to_thread` WeasyPrint render | YES | YES | YES | YES | YES | **YES** |
| `_sanitize_partner_text()` against brand-injection | YES | YES | YES | YES | YES | **YES** |
| Banned-words guard (Master BAA Art. 3.2) | YES | YES | YES | YES | YES | **YES** |
| Public `/verify/...` SECURITY DEFINER fn | YES | YES | NO | NO | NO | **NO** (CFO-internal) |
| Anchor namespace | site_id | partner_org | partner_org | partner_org | site_id | **partner_org** |
| Auditor-kit-determinism contract (sort_keys, no wall-clock) | n/a | applies | applies | applies | applies | **applies** |
| `require_partner_role(...)` gate | n/a | "admin" | "admin","tech" | "admin","tech","billing" | "admin","tech" | **"admin","billing"** (matches `/me/commission`) |

**Header parity rule (`feedback_multi_endpoint_header_parity.md`):** P-F9
issuance endpoint MUST emit the same response headers as P-F5/P-F7/P-F8
artifact-issuance endpoints (`X-Attestation-Hash`, `X-Attestation-Id`,
`X-Valid-Until`, `X-Artifact-Version`). CI gate (proposed task) ratchets
all 4 partner artifact endpoints against this header set.

---

## §5 — Customer-facing-copy rules

P-F9 is partner-internal so "customer-facing" here means "Brendan + his
CPA + potentially a future buyer doing due diligence on the MSP". The
adversarial reader is: a future M&A diligence lawyer with subpoena
power.

### 5.1 Banned words (Master BAA Article 3.2 + CLAUDE.md legal-language rule)

Must NOT appear anywhere in the PDF copy or in any Jinja2 fragment:

> ensures, prevents, protects, guarantees, audit-ready, PHI never
> leaves, 100%, accurate, precise, exact, certified-net-margin

### 5.2 Required hedging vocabulary

Each numeric column header MUST carry one of these prefixes:

* **"Estimated"** — for any computed margin/cost/share figure.
* **"Preliminary"** — for the overall portfolio rollup.
* **"Based on subscription ledger only"** — top-banner disclaimer.

### 5.3 Required disclosure — Stripe Connect dependency

12pt minimum, top of every page:

> Actual profitability requires Stripe Connect integration, which is
> not yet activated for this OsirisCare partner account. Until that
> reconciliation channel ships, all "paid" figures reflect expected
> revenue derived from the live subscription ledger, not deposited
> funds. See "v2 path" in the OsirisCare partner documentation.

### 5.4 Operator-supplied cost transparency

The assumptions footnote (§3.2.5) MUST display:

* Each cost figure verbatim (`$X,XXX/mo per essentials seat`, etc.).
* `updated_by_email` and `updated_at` from the assumptions row — auditor
  trail that the operator owns the cost numbers, not OsirisCare.
* If any tier cost is 0, an explicit line: *"Cost not yet supplied by
  operator for this tier; figures above treat cost as $0 pending
  operator input."*

### 5.5 Opaque-mode parity (CLAUDE.md §"Opaque-mode email parity")

Email notifications wrapping P-F9 issuance MUST be opaque-mode (no
clinic names, no dollar figures in subject or body — redirect to portal
auth). Same posture as `client_owner_transfer.py` `_send_initiator_confirmation_email`.

---

## §6 — Future v2 path

| v1 column | v2 column | Source |
|-----------|-----------|--------|
| Estimated monthly commission | **Verified paid commission** | `partner_invoices` (Stripe Connect mig 235+) |
| `lifetime_paid_cents = 0` | Actual lifetime paid | `partner_payouts.amount_cents` SUM |
| Operator-supplied per-tier cost | PSA-integrated cost | `partner_psa_integrations` (proposed) |
| `osiriscare_subcontract_cents_per_seat` (operator-typed) | Partner's upstream Stripe invoice line items | `stripe.Invoice.list(customer=partners.stripe_customer_id)` |
| Banner: "ESTIMATED — INTERNAL USE ONLY" | Banner: "VERIFIED — RECONCILED PROFITABILITY" | swap on `kind = "v2"` |

v2 re-issues as `partner_profitability_packets.kind = 'verified'` (the
v1 row stays in place as immutable history); banner text + canonical-
JSON `kind` field flip together. CI gate ensures v1 and v2 versions
never co-issue for the same partner at the same `period_end`.

**v2 is gated on:** mig 235 (`partners.stripe_account_id` Stripe Connect
account-id) populated for ≥1 partner AND `partner_invoices` populated
for ≥1 month of paid history. Until both: v1 is the only issuance path.

---

## §7 — Phased implementation

### Phase 0 — Design + template (0.5 day) **← THIS DOC closes this phase**

* This file (`p-f9-estimated-profitability-design-2026-05-13.md`).
* Gate A fork-based adversarial review (Steve/Maya/Carol/Coach lenses)
  — verdict file at `audit/coach-p-f9-estimated-profitability-gate-a-2026-05-13.md`.
* PARTNER_PLAN_CATALOG refactor: extract from `client_signup.py` +
  `partners.py` to `backend/plan_catalog.py` — Phase 0 prereq (3rd
  consumer triggers the refactor; rule of three).

### Phase 1 — Data-source backend (1 day)

* Mig 314: `partner_profitability_assumptions` table + CHECK constraints.
* Mig 315: `partner_profitability_packets` table mirroring `partner_portfolio_attestations` (mig 289) shape; aggregate counts + per-customer JSONB blob, partial unique idx on `superseded_by_id IS NULL`.
* Endpoint `GET /api/partners/me/profitability-assumptions` + `PUT /api/partners/me/profitability-assumptions` (role: "admin","billing"). Logs each update with previous values to `admin_audit_log` (CFO can subpoena their own assumption history).
* Helper `compute_estimated_profitability(partner_id, period_end, conn)` in `backend/partner_profitability.py` — single function the issuance endpoint + a future weekly-digest section can both consume.

### Phase 2 — PDF generator + chain-anchor + endpoint (1.5 days)

* `backend/partner_profitability_packet.py` mirroring `partner_portfolio_attestation.py` (442 LOC reference shape).
* Template `backend/templates/partner_profitability_packet/letter.html.j2` + `__init__.py` registered with template registry (StrictUndefined).
* Endpoint `POST /api/partners/me/profitability-packet/issue` → returns PDF stream + headers (parity with P-F5/P-F7/P-F8).
* Endpoint `GET /api/partners/me/profitability-packet/{attestation_hash}` → re-fetch existing packet (byte-identical re-render).
* CI test: `tests/test_p_f9_profitability_packet.py`
  * `test_canonical_json_sort_keys_true`
  * `test_no_banned_words_in_rendered_html`
  * `test_estimated_banner_present_at_12pt_minimum`
  * `test_zero_cost_footnote_present_when_assumption_is_zero`
  * `test_rate_unavailable_renders_em_dash_not_fabricated_number`
  * `test_re_issue_with_same_period_byte_identical_pdf` (determinism)
  * `test_endpoint_role_gate_billing_admin_only`
  * `test_endpoint_emits_parity_headers`
  * `test_no_format_strings_in_python_source` (AST scan against `_AUDITOR_KIT_README` regression class)
* Gate B fork-based review at `audit/coach-p-f9-estimated-profitability-gate-b-2026-05-13.md` BEFORE marking complete. MUST run full pre-push test sweep (CLAUDE.md §"Gate B MUST run the full pre-push test sweep").

---

## §8 — Open questions for user-gate

1. **Per-customer table — site_id-prefix vs sequential row-number?**
   Even the 8-char UUID prefix re-anchors a clinic across re-issues
   (PDF leaked to a buyer becomes a longitudinal identifier). v2-safe
   alternative: sequential `row_number()` per packet, randomized per
   issuance. Which posture does the user want?

2. **"Pilot" plan inclusion.** Pilot is one-time ($299) and not in
   MRR. Should pilot show as a separate "one-time revenue" mini-table
   above the MRR table, or be excluded entirely from v1?

3. **Trailing-12-month chart — text-table OR embedded SVG?**
   P-F7 Weekly Digest currently text-only. Embedded SVG (WeasyPrint
   supports) is the prettier CFO artifact but adds determinism risk
   (must pin font + render-mode for byte-identity).

4. **Cost-assumption versioning.** When the operator changes
   `essentials_cost_cents` from $200 → $250, does the next-issued P-F9
   silently use the new number, or does each packet snapshot the
   assumptions row at issuance time so historical packets render
   identically forever? Recommendation: snapshot at issuance time
   (consistent with `presenter_brand_snapshot` pattern), but user
   confirms.

5. **Mig 315 `kind` column.** Pre-bake `kind TEXT NOT NULL DEFAULT 'estimated' CHECK (kind IN ('estimated','verified'))` for the v2 flip path, or add `kind` in v2's migration? Recommendation: pre-bake — cheaper to ship the constraint now than to backfill later.

6. **OsirisCare-cost-per-seat single-rate vs per-tier-rate.** Today
   `osiriscare_subcontract_cents_per_seat` is one flat number. If
   OsirisCare's wholesale price-to-partner ladder ever introduces
   tier-specific wholesale rates, this single column becomes wrong.
   Pre-bake 3 columns (essentials/professional/enterprise wholesale)
   even though v1 uses one number? Recommendation: yes, pre-bake.

7. **Determinism strictness — re-render delta tolerance.** Do we
   guarantee BYTE-identical re-renders of an existing packet (high
   bar; requires pinned WeasyPrint version + font hashes), or only
   semantic-identical (same numbers, same structure, possibly
   different WeasyPrint internal IDs)? Auditor-kit holds byte-
   identical; P-F9 isn't auditor-kit so semantic-identical may be
   acceptable. User confirms.

8. **Subpoena / discovery posture.** Should the issuance endpoint
   write a `profitability_packet_issued` event to `admin_audit_log`
   visible to OsirisCare staff (gives OsirisCare visibility into MSP
   internal-finance cadence — useful for support but potentially
   adversarial if MSPs view their cost assumptions as competitive
   data)? Recommendation: log issuance metadata (partner_id, hash,
   issued_at) but NOT the cost figures or per-customer detail.

---

## §9 — References (source-of-truth files inventoried)

* `mcp-server/central-command/backend/partner_portfolio_attestation.py` — sibling pattern (442 LOC)
* `mcp-server/central-command/backend/partner_ba_compliance.py` — sibling pattern (610 LOC)
* `mcp-server/central-command/backend/templates/partner_portfolio_attestation/letter.html.j2` — Jinja2 + WeasyPrint reference
* `mcp-server/central-command/backend/partners.py:3275` (`/me/commission`) — reuse rate function + monthly breakdown query
* `mcp-server/central-command/backend/client_signup.py:87` (`PLAN_CATALOG`) — refactor target for Phase 0 prereq
* `mcp-server/central-command/backend/billing.py:48` — Stripe lookup_keys reference
* `mcp-server/central-command/backend/migrations/233_partner_revenue_tiers.sql` — `compute_partner_rate_bps()`
* `mcp-server/central-command/backend/migrations/289_partner_portfolio_attestations.sql` — table-shape reference for mig 315
* `mcp-server/central-command/backend/migrations/290_partner_baa_roster.sql` — referenced by audit; not consumed by P-F9 v1
* CLAUDE.md §"Auditor-kit determinism contract" — applies to canonical-JSON portion
* CLAUDE.md §".format()-template-ban rule" — applies to template authoring
* CLAUDE.md §"Two-gate adversarial review" — both Gate A + Gate B mandatory
* `~/.claude/projects/.../memory/feedback_round_table_at_gates_enterprise.md` — fork-based 4-lens review

---

**Next action:** route this design to Gate A fork-based adversarial review (Steve/Maya/Carol/Coach), verdict written to `audit/coach-p-f9-estimated-profitability-gate-a-2026-05-13.md`. NO migration application, NO endpoint code, NO template authoring until Gate A returns APPROVE or APPROVE-WITH-FIXES with P0 closure.
