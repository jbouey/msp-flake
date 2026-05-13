# Gate A — Load-Testing Harness Design (Task #97)

**Date:** 2026-05-12.
**Reviewed packet:** `.agent/plans/40-load-testing-harness-design-2026-05-12.md`.
**Gate type:** A (pre-execution; design-only — no code shipped).
**Lenses:** Steve (engineering), Maya (customer/legal), Carol (security), Coach (consistency).

## Verdict

**BLOCK — redesign needed.**

Three P0s independently necessitate revision before implementation may proceed:
endpoint paths in §"In-scope endpoints" don't match the actual route table (P0-1),
the isolation pattern is incompatible with `compliance_bundles` Ed25519 + OTS +
RLS + partitioning invariants (P0-2), and the design duplicates rather than
reuses the soak-isolation pattern that plan-24 already established and that
mig 303 already shipped (P0-3). Five P1s and three P2s follow.

Re-spin the doc addressing P0-1..P0-3 + P1-1..P1-5, resubmit for Gate A v2.

---

## Steve (engineering)

### P0-1 — Wave-1 endpoint paths don't exist as written
Two of the five wave-1 endpoints (§"In-scope endpoints", lines 30–36) name
paths that aren't real:

- **`/api/appliances/order`** (line 32) does not exist. The actual order-poll
  endpoint is `GET /api/appliances/orders/{site_id}` (agent_api.py:521 under
  router prefix). The singular `/order` only appears in `csrf.py:81` as a
  legacy exempt entry — nothing routes there.
- **`/api/evidence/sites/{id}/submit`** (line 34) does not exist. The actual
  appliance submit path is `POST /evidence/upload` (agent_api.py:2519) under
  the agent router. The `sites/{id}` shape exists for the auditor-kit + admin
  retrieval routes, not for submission.

This is not a typo — a k6 script written verbatim against the design would
404 on 2 of 5 scenarios. **Fix:** name the actual routes and their auth
flows, with code-line citations, before approving.

### P1-1 — Wave-1 priority is missing several high-volume bearer endpoints
`csrf.py EXEMPT_PREFIXES` (lines 108–129) lists the machine-to-machine
surface. Checking the design's wave-1 against actual cadence:

Missing high-volume:
- **`/api/agent/executions`** (agent_api.py:1999) — written every L1 fire,
  every L2 decision, every healing attempt. On a 100-appliance fleet at the
  observed L1 rate (~0.3/min/appliance per agent_api throughput data), this
  is ~30 POSTs/min — comparable to checkin.
- **`/agent/patterns`** (agent_api.py:1721) + **`/api/agent/sync/pattern-stats`**
  (agent_api.py:1808) — hourly aggregated pattern sync. Lower req/s but
  payload-heavy; throughput-vs-payload-size is a different curve than checkin.
- **`/api/devices/sync`** + **`/api/logs/`** — log + device inventory paths
  ARE machine-to-machine + are not mentioned. Logs in particular are the
  highest payload-volume class (logshipper).
- **`/incidents`** (agent_api.py:705) — every L3 escalation. Lower rate but
  the highest fan-out (writes incidents row + substrate trigger + alertmanager
  webhook).

The design's claim "Hot machine-to-machine paths only" (line 28) is correct
in framing but undercounted. **Fix:** justify exclusion of each of the above
explicitly, or include them.

### P1-2 — k6 vs vegeta: tool choice is fine, comparison table is shallow
k6 is the right tool — the scenarios DSL + Prometheus integration carry the
SLA-grade work. The packet's reasoning is sound. BUT the comparison table
omits the real tradeoff: **k6 scripts are JS, which means scenario logic
runs in goja (a JS VM)** and CPU-bound payload generation (e.g. signing
synthetic Ed25519 evidence bundles) doesn't scale linearly past ~500 VUs on
a 2-vCPU box. For Scenario C "ramp to 10×" this matters. **Fix:** name the
VU ceiling explicitly + plan for distributed execution if the slow-ramp goal
requires >500 concurrent VUs. Don't approve a CX22 (2 vCPU/4GB) without this
math — it may underspec.

### P2-1 — `/health` as a scenario at 1000 req/s is a noise generator
`/health` doesn't touch the DB or pgbouncer (line 36 rationale acknowledges
this). Treating it as a 1000-req/s scenario tests Uvicorn raw throughput +
the load balancer in front of it, not the application. That data is fine to
collect but should be labeled clearly as "infrastructure baseline, not
application baseline." Worth mentioning so the SLA doc doesn't conflate the
two ceilings.

---

## Maya (customer / legal)

### P0-2 — Isolation pattern is incompatible with compliance_bundles invariants
The design's §"Staging vs production" (lines 76–83) proposes:
> "persists to a SEPARATE `load_test_checkins` table (or partition)"

This works for the checkin endpoint, but **`/evidence/upload` writes to
`compliance_bundles`**, which is bound by three invariants that block the
proposed pattern:

1. **Ed25519 + OTS chain immutability.** Every bundle hash-chains to the
   prior bundle for the same site. A synthetic 100-fleet × 50 req/s means
   ~180,000 bundles/hr inserted; if they share a chain with real bundles
   (same site) they corrupt the chain; if they have their own chain (new
   site) they still write into the live monthly partition.
2. **Per-month partitioning (mig 138).** The default partition catches
   overflow. Load-test writes into `compliance_bundles_2026_05` (the live
   partition) — even with a separate `site_id`, they share storage,
   `pg_class.reltuples`, vacuum cadence, and partition-size growth with
   real customer rows. The very class of bugs Session 219 hit
   (`COUNT(*) timeout class`) gets worse.
3. **RLS policies — both `tenant_isolation` and `org_isolation`.** A
   synthetic site without a real `client_org_id` either fails the RLS
   policies (insert blocked) or is admin-bypassed (admin_bypass policy)
   — neither matches the design's "separate table" framing. Worse: if
   admin_bypass is the path, the load-test rows are visible to every
   admin-context aggregation, contaminating fleet metrics.

**Fix:** Either (a) carve out a dedicated `load_test_bundles` table (not a
partition, not a synthetic-site row in compliance_bundles), or (b) drop
`/evidence/upload` from wave-1 entirely and load-test only the non-evidence
paths in this round. The "header + separate site_id" pattern is fine for
checkin + orders + journal, but it's not enough for the cryptographically
anchored tables.

### P1-3 — Pre-flight kill-switch is unspecified
A 60-min Scenario C (slow ramp to 10×) against prod with no operator-callable
abort is the worst-case shape. Design says "Run against staging or against
a snapshot DB" as anti-goal (line 64) but lines 78–82 say the chosen target
IS prod with marker isolation. The two contradict each other for Scenario C.

**Fix:** Specify a kill-switch — concretely:
- An env-var-flagged abort file on the k6 box (`/run/load-test/abort`)
  that scripts poll between iterations; if present, scripts exit 0
  without continuing.
- A backend kill flag: `/api/admin/load-test-status` returning `{"abort": true}`
  reread every 30s by k6. Abort if upstream says so.
- An AlertManager rule "load_test_5xx_storm > 50/min" that flips the kill
  flag automatically.

Without these, a misconfigured Scenario C burns ~30min before a human can
intervene — the exact class of outage we're trying to prevent.

### P1-4 — Customer-visible blast radius is unquantified
Lines 49–52 (Scenario A pass criteria) include "0 5xx" but don't speak to
what real customers see during the run. 100 simulated appliances at 100
req/s for 30 min is a sustained 6,000 req/min hitting the SAME Uvicorn
workers + PgBouncer pool that 3 real appliances + N admin users share.
A misconfigured run that pushes PgBouncer to saturation degrades real
customer requests too.

**Fix:** quantify "acceptable degradation for real traffic during a run."
Concretely: "real-customer p95 must stay <500ms during load-test scenarios"
as an external SLA, measured via a separate probe (real appliance heartbeat
latency) NOT the load-test box itself.

### P2-2 — Auditor-kit determinism + load-test data interaction
The auditor-kit determinism contract (CLAUDE.md line 252) pins kit hash to
chain progression + OTS state. If load-test data accidentally writes into
`compliance_bundles` for ANY real site, kit hashes for that site change
between downloads — visible to auditors as a tamper-evidence violation.
This reinforces P0-2: load-test data must NEVER enter `compliance_bundles`
under any site_id that corresponds to a real customer.

---

## Carol (security)

### P1-5 — Bearer token storage + revocation unspecified
Design §Gate A asks #3 (line 100) acknowledges the question but the answer
("rotate a dedicated synthetic-appliance bearer that's keyed to
`synthetic-load-test` site_id only; revocable independently") is a one-liner
counter-argument, not a spec. Concrete questions:
- Where is the token stored? 1Password vault item? Vault Transit
  (sibling pattern)? Both are acceptable. Local filesystem on CX22 is NOT.
- Rotation cadence? Manual? Auto every 7d?
- Revocation path? Drop a row in `appliance_bearer_revocations`? Set
  `site_appliances.bearer_revoked=true`? The path matters — site_appliances
  doesn't currently have that column.
- Audit log? Every load-test run should log "run_id + actor + token_id"
  to `admin_audit_log` (same pattern as auditor_kit_download).

**Fix:** Spec all four explicitly. Anchor to existing patterns
(Vault rollout uses Ed25519 non-exportable; 1Password owns shares).

### P2-3 — Blast radius if CX22 is compromised
The bearer is scoped to `synthetic-load-test` site_id, so an attacker
controlling the load-test box can:
1. Mint arbitrary checkins for the synthetic site → fills load_test_checkins
   table → cost to us is disk/DB load. Manageable.
2. Mint synthetic compliance bundles → IF P0-2 isn't fixed, contaminates
   `compliance_bundles` chain. IF P0-2 IS fixed (separate table), this is
   contained.
3. Use the synthetic site as a pivot to attempt cross-site spoofing —
   `_enforce_site_id()` in agent_api.py blocks this (Session 202 lockdown).
   GOOD — pattern is already defense-in-depth.
4. WG peer .4 access to internal network — the CX22 sits on the WG mesh
   alongside Vault + VPS. **Audit needed:** what other services on WG
   peers .1/.2/.3 are accessible from .4? If Vault Transit signing is
   reachable, that's the higher-value target than the load-test bearer.

**Fix:** Spec WG-peer firewall rules on the CX22 — outbound to
`central-command.osiriscare.com:443` only; inbound denied; explicitly
NOT able to reach Vault host's Transit API.

---

## Coach (consistency / sibling-pattern audit)

### P0-3 — Duplicates plan-24's soak-isolation pattern instead of reusing it
Plan-24 (mig 303, shipped 2026-05-11) established the canonical
synthetic-data isolation pattern for this repo:
- Synthetic site row: `synthetic-mttr-soak`.
- Per-row marker: `details->>'soak_test' = 'true'`.
- Partial index: `WHERE details->>'soak_test' = 'true'` (mig 303 line 80–82).
- Filter discipline: every aggregation query gets a one-line
  `WHERE details->>'soak_test' != 'true'`.

This design (§"Staging vs production", lines 76–83) proposes a DIFFERENT
isolation pattern:
- Synthetic site row: `synthetic-load-test` (different name).
- Per-row marker: header `X-Load-Test: true` (header, not column).
- Storage: "separate `load_test_checkins` table (or partition)" — neither
  matches plan-24's marker-column-in-existing-table approach.

**Two parallel patterns is the antipattern.** Either:
(a) Generalize plan-24's `soak_test` marker to a broader `synthetic_run_type`
    enum column (e.g. `details->>'synthetic'='mttr_soak'|'load_test'`) and
    have BOTH soak + load-test write to the same partial-indexed shape, OR
(b) Carve out separate tables for BOTH (mig 303 retroactively gets a
    `synthetic_mttr_soak_incidents` table, this design gets
    `load_test_checkins`) — but that's 2x the migration churn + 2x the
    operator-aggregation discipline.

(a) is cleaner. **Fix:** unify with plan-24's pattern before approval.

### P1-2-coach — Scope split between #94 and #97 is clean, BUT the design
doesn't pin the seam.
Plan-24 line 13 says `"❌ OUT: Load testing (req/s throughput) — separate
task #97."` This design line 5 says `"Companion: task #94 plan-24
(substrate-MTTR soak — DIFFERENT scope; this is HTTP throughput)."` Good.

But the design also says (lines 11–12):
> "#98 SLA soak needs a load floor … inject HTTP traffic alongside synthetic
> incidents."

That sentence implies #97 wave-1 is a DEPENDENCY of #94 v2 (re-run with
load floor) — but #94 v2 hasn't been redesigned yet. Sequence is unclear:
does #97 ship first as standalone, then #94 v2 layers on top? Or does
#94 v2 design assume #97 is live? The companion-task cross-reference needs
to pin the order explicitly with a Gantt-style dependency note.

### P2-coach — Phase 5 readiness (#95) doesn't mandate k6 specifically
I didn't find a Phase 5 readiness doc that names k6 vs vegeta vs Locust.
The design's tool-choice section is making the call cold without that
anchor — which is fine, but the packet should say so explicitly rather
than implying #95 mandates a tool.

---

## Findings summary

| ID    | Severity | Lens   | Topic                                                          |
|-------|----------|--------|----------------------------------------------------------------|
| P0-1  | P0       | Steve  | Two of 5 wave-1 endpoint paths don't exist as written          |
| P0-2  | P0       | Maya   | Isolation pattern violates `compliance_bundles` Ed25519/OTS/RLS|
| P0-3  | P0       | Coach  | Duplicates plan-24's soak-isolation pattern instead of reusing |
| P1-1  | P1       | Steve  | Missing several high-volume bearer endpoints (executions, etc.)|
| P1-2  | P1       | Steve  | k6 VU ceiling on CX22 unspecified for Scenario C 10× ramp      |
| P1-3  | P1       | Maya   | Pre-flight kill-switch unspecified (env file + backend flag)   |
| P1-4  | P1       | Maya   | Real-customer degradation SLA during runs unquantified         |
| P1-5  | P1       | Carol  | Bearer storage + rotation + revocation + audit-log unspecified |
| P1-2c | P1       | Coach  | #97 ↔ #94 v2 ↔ #98 sequencing pin missing                      |
| P2-1  | P2       | Steve  | `/health` at 1000 req/s labels as infra baseline, not app      |
| P2-2  | P2       | Maya   | Auditor-kit determinism interaction with load-test data        |
| P2-3  | P2       | Carol  | CX22 WG-peer firewall rules + Vault reachability undefined     |

---

## What approval would require (Gate A v2 acceptance criteria)

1. **P0-1:** Replace wave-1 endpoint list with exact route paths cited
   from `agent_api.py` / `journal_api.py` / etc. Every path must be
   `grep`-verifiable in the current tree.
2. **P0-2:** Either drop `/evidence/upload` from wave-1, OR spec a
   `load_test_bundles` table (separate from `compliance_bundles`)
   with no chain/OTS coupling.
3. **P0-3:** Unify with plan-24's `details->>'<synthetic_kind>'='true'`
   marker pattern. One isolation discipline across mig 303 + #97.
4. **P1-1..P1-5:** Each addressed with concrete spec (no one-liner
   counter-arguments — the v1 design's Gate-A-asks section had four
   one-liners that the user explicitly called out 2026-05-11 as the
   "author-written counter-arguments don't count" antipattern).
5. **P1-2c:** Cross-task sequencing note pinning #97 → #94 v2 → #98.
6. **Round-table at v2:** Run a fresh fork-based Gate A on the revised
   packet — author-written counter-arguments in the design don't satisfy
   the round-table rule (CLAUDE.md, "fork-based 4-lens adversarial review
   ... author CANNOT play the lenses themselves").

Downstream implementation (CX22 spin-up, k6 scripts, first soak) is
**BLOCKED** until v2 lands and passes Gate A v2.
