# Gate A — Task #70: BAA-draft sign-off gate on D1 operational soak

**Date:** 2026-05-14
**Gate:** A (pre-execution)
**Reviewers (4-lens + 3 extension):** Steve · Maya · Carol · Coach · Auditor (OCR) · PM · Attorney (in-house counsel)
**Subject:** Task #70 [Gate B FU-4 P0] — tie BAA v1.0-INTERIM sign-off to a measurable ≥7-day D1 operational soak; copy-grep customer-facing artifacts for over-claiming "heartbeat...verified" language.

---

## 250-WORD SUMMARY

The load-bearing question — does `MASTER_BAA_v1.0_INTERIM.md` over-claim? — resolves **NO**. Article 3.2 and Exhibit B.4 say the platform *maintains the category of* "cryptographic attestation chains" and that "evidence bundles are cryptographically signed and chained." Those are **capability/architecture claims about `compliance_bundles`**, which were Ed25519-signed and chained the entire time. The BAA never mentions heartbeats, never says "every heartbeat is verified," never asserts per-event signature verification. D1 (heartbeat *backend verification*) is an internal liveness/compromise-detection mechanism — not a safeguard the BAA describes. The grep of F1 attestation letter, wall cert, quarterly summary, and auditor-kit README confirms the same: every "cryptographically signed" claim scopes to **evidence bundles**, not heartbeats. **No customer-facing artifact over-claims.** FU-4's feared scope (BAA copy implied an inert capability) does not materialize.

Prod evidence is also decisive: post-fix adb7671a (2026-05-13 14:20 EDT), `signature_valid` is 100% non-NULL and 100% TRUE — 182/182 partial-day on the 13th, 2897/2897 on the 14th. The daemon WAS signing the whole 13 days (`agent_signature` non-NULL on every row); only backend *verification* was inert.

**Therefore Task #70 collapses from a P0 to a small P2.** No BAA copy edit. No CI gate is warranted (nothing to gate — the artifacts are already correctly scoped). Recommended mechanism: a **checklist edge on Task #56** plus a tiny **regression CI gate that pins the scoping** (forbids future "heartbeat" + "verified" co-occurrence in customer-facing templates), so a future copy edit can't introduce the over-claim that FU-4 feared. Soak bar: 7 consecutive days ≥99% `signature_valid IS TRUE` across all pubkeyed appliances.

**VERDICT: APPROVE-WITH-FIXES (rescope).**

---

## PER-LENS VERDICT

### 1. Steve (technical mechanism) — APPROVE-WITH-FIXES (rescope)

Three options were posed. Re-scoped against the finding that **no artifact currently over-claims**:

- **(a) CI gate that greps customer-facing artifacts for "heartbeat...verified" and asserts hedging** — there is nothing to assert *today* (no co-occurrence exists). But a **regression gate** is still cheap and correct: a static test that scans `backend/templates/{attestation_letter,wall_cert,quarterly_summary,auditor_kit}/**` + `MASTER_BAA*.md` and **fails if "heartbeat" appears within N tokens of "verif"/"signed"/"cryptographic"**. This is a guard-rail, not a fix — it pins the *current correct state* so a future copy author can't regress into FU-4's feared over-claim. ~30 lines, ratchet baseline 0.
- **(b) Checklist item in Task #56 sign-off** — yes. The master-BAA sign-off (Task #56) should carry an explicit edge: "before v2.0 references any per-event verification capability, confirm D1 soak ≥7d." Today v1.0-INTERIM does NOT reference it, so this is forward-protection for v2.0 drafting.
- **(c) Substrate-fed dashboard "D1 soak: N days clean"** — over-build. The `daemon_heartbeat_signature_unverified` invariant (d042802e, Task #69) already fires sev1 on regression. A standalone soak-counter panel duplicates that. Skip. The soak metric can be a one-shot SQL the Task #56 reviewer runs, not a standing dashboard.

**Most enforceable:** (b) checklist edge as the primary mechanism + (a) thin regression gate as the structural backstop. (c) rejected — minimum-mechanism.

One note: `canonical_metrics.py::PLANNED_METRICS["appliance_liveness"]` already encodes the dependency ("needs gate on recent heartbeat AND D1 signature_valid once Task #40 ships"). That's the *internal* canonical-metric path — distinct from the customer-facing BAA copy. Don't conflate.

### 2. Maya (database / soak metric) — APPROVE

Query shape is correct. Prod result (15-day daily rollup, `appliance_heartbeats`):

```
2026-05-12 | 4273 |    0 |    0 | 4273   <- pre-fix: agent_signature SET, signature_valid NULL
2026-05-13 | 3291 |  182 |  182 | 3291   <- adb7671a deployed 14:20 EDT
2026-05-14 | 2897 | 2897 | 2897 | 2897   <- 100% verified
```
Columns: total | signature_valid IS NOT NULL | signature_valid IS TRUE | agent_signature IS NOT NULL.

Critical nuance for the soak metric: **`agent_signature` was non-NULL every one of the 13 inert days.** The daemon was signing fine; the *backend verifier* was the dead component. So the soak metric MUST gate on `signature_valid IS TRUE` (verification succeeded), NOT on `agent_signature IS NOT NULL` (which would have falsely shown "clean" the entire 13 days). This is exactly the gap `daemon_heartbeat_signature_unverified` was built to close.

**Threshold = "soak clean":** over a trailing 7-day window, for every appliance where `site_appliances.agent_public_key IS NOT NULL` (prod: 3/3 appliances pubkeyed), `COUNT(*) FILTER (WHERE signature_valid IS TRUE) / COUNT(*) >= 0.99`. The 1% slack absorbs the legitimate 15-minute pubkey-rotation grace window + Path-B reconstruct edge cases. A per-appliance floor (not just fleet aggregate) prevents one healthy high-volume appliance from masking one silent appliance — Counsel Rule 4 orphan-coverage. Earliest possible "soak clean" date: **2026-05-20** (7 days after the 2026-05-13 fix; partial-day the 13th counts as day 0).

### 3. Carol (security / evidence bar) — APPROVE-WITH-FIXES

D1 was inert 13 days. The evidence bar must be *runtime-proven*, not code-present. Bar:

1. **7 consecutive days** of `signature_valid IS TRUE >= 99%` per pubkeyed appliance (Maya's metric), measured by SQL against prod `appliance_heartbeats` — cited in the Task #56 sign-off with the actual query output, per the runtime-evidence-required-at-closeout rule.
2. **Zero open `daemon_heartbeat_signature_unverified` violations** at sign-off time (the invariant from d042802e is the regression tripwire — if it's quiet for 7 days, the verifier path is healthy).
3. **Zero open `daemon_heartbeat_signature_invalid` / `daemon_heartbeat_unsigned`** — the sibling invariants. All three must be quiet; they partition the failure space.

Important framing for Carol's lens: the BAA does **not** claim heartbeat verification, so D1 inertia was **not a BAA breach** — it was an internal detection-capability gap. That's the correct severity. But the evidence bar still matters for **v2.0**: if outside counsel's v2.0 hardening adds any language about "continuous verification" or "per-event integrity," D1 soak proof becomes a hard precondition for that sentence shipping. The bar protects v2.0, not v1.0.

### 4. Coach (gate vs doc vs Task #56 edge — minimum mechanism) — APPROVE-WITH-FIXES (rescope)

Minimum-mechanism analysis. The feared artifact (BAA over-claim) does not exist, so the heavy mechanisms (BAA copy revision, blocking CI gate on sign-off) are unjustified. What remains:

- **Task #56 dependency edge** — REQUIRED. Cheapest correct mechanism. A named line in the Task #56 sign-off checklist: "v2.0 must not assert per-event/heartbeat verification language unless D1 soak ≥7d clean (SQL evidence attached)." Zero code.
- **Thin regression CI gate** — JUSTIFIED as a backstop, not a fix. It pins today's correct scoping so the FU-4-feared regression can't sneak in via a future copy edit. ~30 lines. This is the *structural* closure of the class.
- **BAA copy edit** — NOT JUSTIFIED. Article 3.2 is correct as written.
- **Standing dashboard** — NOT JUSTIFIED. Duplicates the existing invariant.

**Coach mechanism ruling: Task #56 edge (primary) + thin regression gate (backstop). Rescope #70 from P0 to P2.** The P0 framing was correct *as a precaution* before the BAA was read; reading it discharges the P0.

### 5. Auditor / OCR (Counsel Rule 9 — determinism + provenance not decoration) — APPROVE

Counsel Rule 9: the BAA must not assert a capability that isn't operationally proven. Tested against Article 3.2 + Exhibit B.4:

- Article 3.2: "...cryptographic attestation chains for evidence-grade observability." — This is a *category-of-safeguard* statement, explicitly hedged: "The categories of safeguards Business Associate maintains include (without limitation)..." and "The specific algorithms, parameters, and implementation details... are documented in Exhibit B... and may be rotated or upgraded over time." Capability claim. Defensible.
- Exhibit B.4: "Cryptographic attestation chains: evidence bundles are cryptographically signed and chained to support tamper-evident audit trails." — Scoped to **evidence bundles** (`compliance_bundles`), which are 232K+ rows, Ed25519-signed, OTS-anchored, and were never inert. This claim is operationally proven *today*. No Rule 9 violation.

The BAA is, if anything, *under*-claiming relative to capability — it never even mentions the heartbeat-signature mechanism. Rule 9 is satisfied. **The provenance the BAA asserts (evidence-bundle signing) is real and continuous; the provenance D1 adds (heartbeat verification) is real as of 2026-05-13 and the BAA doesn't lean on it.**

One auditor note: keep it that way. If v2.0 drafting is tempted to add "every system-activity heartbeat is cryptographically verified" as a §164.308(a)(1)(ii)(D) flex, that sentence MUST NOT ship without the 7-day soak proof. That's the Rule 9 tripwire for v2.0.

### 6. PM (effort + slotting) — APPROVE-WITH-FIXES (rescope)

- **Effort, as-feared (P0):** BAA copy audit + revision + re-signature cycle — multi-day, blocks Task #56.
- **Effort, as-rescoped (P2):** (1) add the Task #56 checklist edge — 10 minutes, doc-only. (2) thin regression CI gate — ~1 hour incl. test + ratchet baseline. Total: **~1 hour + a checklist line.**
- **Slotting vs Task #56:** Task #56 (master BAA outside-counsel cycle, 14-21d, v2.0 target 2026-06-03) is in_progress. The Task #56 edge slots in *now* — it's a precondition note for v2.0 drafting, costs nothing, and v2.0 is 20 days out so there's slack. The regression gate can land any time before v2.0; recommend bundling it with the next BAA-adjacent commit.
- **Soak timeline:** soak-clean earliest 2026-05-20. v2.0 target 2026-06-03. **13 days of margin** — the soak is not on the critical path for v2.0. Comfortable.

No new TaskCreate needed beyond rescoping #70 itself; the two deliverables are small enough to close under #70 directly.

### 7. Attorney / in-house counsel (LOAD-BEARING) — APPROVE (rescope mandatory)

I read `MASTER_BAA_v1.0_INTERIM.md` in full. The dispositive question: does Article 3.2 make a *per-heartbeat verification claim* (over-claim, given 13 days inert) or a *capability claim* (defensible)?

**It is a capability claim. Unambiguously.** Verbatim, Article 3.2: *"The categories of safeguards Business Associate maintains include (without limitation): ...append-only audit logging; and cryptographic attestation chains for evidence-grade observability."* The sentence enumerates *categories of safeguards maintained* — it is a present-tense capability inventory, expressly non-exhaustive ("without limitation"), and expressly subject to rotation ("may be rotated or upgraded over time... without requiring amendment"). It does not say "every heartbeat," does not say "continuously verified," does not say "per-event." The word "heartbeat" appears **nowhere** in the BAA.

Exhibit B.4 is the only other relevant text and it is *narrower*, not broader: it scopes "cryptographically signed and chained" to **"evidence bundles."** Evidence bundles = `compliance_bundles` = the table that was signed + chained + OTS-anchored the entire time. There is no factual gap between what Exhibit B.4 says and what the platform did.

**Conclusion: the BAA does not over-claim. FU-4's precautionary P0 is discharged by reading the document.** D1's 13-day inertia was an internal detection-capability gap, not a misrepresentation in a customer contract. No BAA amendment, no re-signature, no copy edit.

**However** — three counsel conditions on the rescope:

1. **The Task #56 edge is mandatory, not optional.** v2.0 is being hardened by outside counsel right now. If outside counsel, reaching for §164.308(a)(1)(ii)(D) information-system-activity-review strength, drafts language asserting per-event or heartbeat-level verification, that sentence becomes an over-claim *the moment it's written* unless D1 soak is proven. The checklist edge must be in front of the v2.0 drafters before they touch Article 3.2. This is the single highest-value output of Task #70.
2. **The regression gate is counsel-endorsed.** It is cheap insurance against a copy author re-introducing exactly the conflation FU-4 feared. Approve.
3. **The auditor-kit README and F1 letter were also checked and are clean** — both scope "cryptographically signed" to evidence bundles/chain, never to heartbeats. The grep confirms it. No change to any customer-facing artifact.

**Attorney verdict: APPROVE. Rescope #70 P0→P2. Zero contract changes. Ship the Task #56 edge before v2.0 drafting advances.**

---

## DOES THE BAA OVER-CLAIM? (Counsel lens, load-bearing) — **NO.**

`MASTER_BAA_v1.0_INTERIM.md` Article 3.2 + Exhibit B.4 make **capability/architecture claims scoped to evidence bundles** ("the categories of safeguards... include... cryptographic attestation chains"; "evidence bundles are cryptographically signed and chained"). Evidence bundles (`compliance_bundles`) were Ed25519-signed, hash-chained, and OTS-anchored continuously — including throughout the 13-day D1 inertia window. The BAA never references heartbeats, never asserts per-event verification, never uses "every" or "continuously verified" against a per-event noun. **The feared over-claim does not exist.** D1 heartbeat-signature *backend verification* is an internal liveness/compromise-detection capability that no customer-facing artifact leans on.

## RECOMMENDED MECHANISM

**Primary: Task #56 dependency edge (doc, ~10 min).** Add to the master-BAA sign-off checklist: *"v2.0 hardening MUST NOT introduce per-event / heartbeat-level verification language in Article 3.2 or Exhibit B unless D1 heartbeat-signature soak is ≥7 days clean (≥99% `signature_valid IS TRUE` per pubkeyed appliance) with SQL evidence attached and zero open `daemon_heartbeat_signature_{unverified,invalid,unsigned}` violations."*

**Backstop: thin regression CI gate (~1 hr).** New test `test_baa_artifacts_no_heartbeat_verification_overclaim.py` — scans `backend/templates/{attestation_letter,wall_cert,quarterly_summary,auditor_kit}/**` + `docs/legal/MASTER_BAA*.md`; fails if "heartbeat" co-occurs within ~12 tokens of "verif"/"signed"/"cryptographic". Ratchet baseline = 0 (current state is clean). Pins today's correct scoping against future copy regression.

**Rejected:** BAA copy edit (nothing to fix); standing soak dashboard (duplicates `daemon_heartbeat_signature_unverified` invariant from d042802e); blocking CI gate on Task #56 sign-off (over-mechanism — a checklist line suffices).

## SOAK-CLEAN EVIDENCE BAR

Trailing **7 consecutive days**, measured by SQL against prod `appliance_heartbeats`:
- **Per pubkeyed appliance** (`site_appliances.agent_public_key IS NOT NULL`; prod: 3/3): `COUNT(*) FILTER (WHERE signature_valid IS TRUE) / COUNT(*) >= 0.99`. Per-appliance floor, not fleet aggregate (Counsel Rule 4 orphan-coverage).
- Metric gates on `signature_valid IS TRUE` — **NOT** `agent_signature IS NOT NULL` (the latter was non-NULL the entire 13-day inert window and would falsely read "clean").
- Zero open `daemon_heartbeat_signature_unverified`, `daemon_heartbeat_signature_invalid`, `daemon_heartbeat_unsigned` violations at sign-off.
- Evidence cited as actual query output in the Task #56 sign-off (runtime-evidence-required-at-closeout rule).
- Earliest soak-clean date: **2026-05-20** (fix deployed 2026-05-13 14:20 EDT = day 0). v2.0 target 2026-06-03 → 13 days margin; not on critical path.

Current prod state (2026-05-14): 2897/2897 = 100% `signature_valid IS TRUE`. 2026-05-13 partial: 182/182. Pre-fix days: 0% verified despite 100% signed.

## FINAL OVERALL VERDICT

**APPROVE-WITH-FIXES (rescope #70 from P0 → P2).**

The Counsel lens — load-bearing — discharges the FU-4 P0: the BAA does not over-claim. No contract change, no re-signature, no customer-facing copy edit. Task #70's real, much smaller scope:
1. **Task #56 dependency edge** (mandatory, doc-only, ~10 min) — forward-protection for v2.0 drafting.
2. **Thin regression CI gate** (counsel-endorsed backstop, ~1 hr) — pins the currently-correct scoping.

Both deliverables close under #70 directly; no new tasks. Soak bar defined and already 100% in prod — soak-clean on 2026-05-20, comfortably ahead of the v2.0 / Task #56 timeline.

All 7 lenses APPROVE or APPROVE-WITH-FIXES. No BLOCK. No P0 carried. The one mandatory item (Task #56 edge) is a 10-minute doc change with 13 days of schedule margin.
