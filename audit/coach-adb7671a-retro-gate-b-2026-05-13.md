# Retroactive Class-B 7-lens Gate B — commit `adb7671a`

**Commit:** `adb7671a fix(prod): two P0 outages — dashboard fleet=0 + D1 signature_auth inert`
**Date of review:** 2026-05-13
**Gate A status:** SKIPPED (the protocol gap this retro-Gate-B exists to recover from)
**Reviewer cwd / git context:** `/Users/dad/Documents/Msp_Flakes`, branch `main`, HEAD = adb7671a (the fix is at tip — no follow-on commits between fix and review).
**Pre-push sweep result:** `bash .githooks/full-test-sweep.sh` → **249 passed, 0 skipped, rc=0** (re-run by this retro Gate B, not relying on the commit body's self-claim).

## 250-word summary

`adb7671a` shipped two pre-existing P0 prod outages — `/api/dashboard/fleet` 500'd on any site with zero recent incidents because `round(None, 1)` raised TypeError on the un-guarded `healing_rate`, and D1 heartbeat signature verification was completely inert in production because `from signature_auth import` failed under the package-context cwd. Both bugs were silently broken in steady state; both fixes are surgical and correct in shape; the regression test (`test_healing_metrics_none_guard.py`) covers both the literal post-fix string AND the structural class via an AST walk. Sibling-class scan found **no other unguarded `round()` on nullable rate variables** in the backend — the 33 sibling `round()` callsites either consume non-nullable computed values, are already guarded with `if … is not None else …`, or operate on assigned-default scalars. Sibling-class scan for the bare-import bug found 22 indented `from X import` callsites in backend/; 21 are `from main import …` (works because `main.py` IS at `/app/` per Dockerfile WORKDIR + uvicorn main:app), and `appliance_relocation` at sites.py:4502 already has the correct relative-then-absolute fallback. Only **one** sibling was the same shape (signature_auth at 4231), and that is what got fixed. **Customer-impact: ~13 days of NULL `signature_valid` on every `appliance_heartbeats` row across the fleet — operator-facing detection gap (Counsel Rule 3 sub-class), but compliance-bundle attestation chain UNAFFECTED.** Verdict: **APPROVE-WITH-FIXES** — the fix is sound; the protocol gap (no Gate A) is the lesson; 4 named follow-ups.

---

## Gate A retro — what Gate A WOULD have caught if dispatched

Gate A dispatched against the diff as-designed would have raised these (none of which BLOCK the fix as-shipped, but several would have improved the commit):

1. **Sibling-class scan deferred.** Gate A normally asks "are there any other callsites in the same shape?" The diff fixed 3 callsites in `db_queries.py` plus 1 in `sites.py`. A retroactive scan confirms NO unguarded callsites remain (see Steve §1), but the scan should have been part of the commit body or referenced in a follow-up task. The author DID write an AST gate to prevent regression — that mitigates partially.
2. **No prod-runtime verification cited.** Per the Counsel-grade "Runtime evidence required at close-out" rule (`feedback_runtime_evidence_required_at_closeout.md`), the commit body claims "Pre-push sweep clean" but does NOT cite a prod `curl /api/dashboard/fleet` proving the 500 is gone, nor a prod `SELECT COUNT(*) FROM appliance_heartbeats WHERE signature_valid IS NOT NULL AND observed_at > NOW() - INTERVAL '15 minutes'` proving D1 is now stamping values. Gate A would have demanded the post-deploy runtime check be part of the close-out evidence.
3. **No customer-impact-scope quantification.** Carol's first question — "for how many heartbeats over how many days was signature_valid NULL?" — is not addressed in the commit body. Operator-disclosure framing benefits from the number (see Carol §3).
4. **Substrate-invariant blind-spot acknowledgment missing.** The D1 inert period was undetected by `daemon_heartbeat_unsigned` (queries `agent_signature IS NULL`, not `signature_valid IS NULL`) and `daemon_heartbeat_signature_invalid` (filters `signature_valid IS NOT NULL`). A Gate A fork would have caught that the existing invariants are blind to the exact failure mode just patched. **Follow-up task #FU-1.**
5. **Two-class commit bundling not defended.** Coach precedent is single-class commits; this one bundles healing_rate + signature_auth. The bundling is defensible because both are prod-P0 surfaced from the same investigation (sidebar "0 sites connected" started the chase) — but the commit body did not say so explicitly. (See Coach §4.)
6. **No Gate B follow-up tasks created.** Per Session 220 lock-in, Gate B findings must be addressed as `TaskCreate` follow-ups in the same commit OR closed at Gate B. Neither happened. This retro Gate B creates them (named in §"Follow-up tasks" below).

---

## Per-lens verdicts

### 1. Engineering (Steve) — APPROVE

**Healing_rate fix correctness:** the pre-fix shape `round((resolved/total*100) if total>0 else None, 1)` raised TypeError on every call where `total_incidents == 0`. Post-fix, all three callsites in `db_queries.py` (lines 1883, 1949, 1989) match the sibling `order_rate` guard already in place. The choice of `0.0` as default is correct — for the "zero incidents this period" steady state, `healing_success_rate = 0.0%` is semantically defensible (no remediation needed = no remediation done = 0/0 → "no signal yet, report 0.0"). Alternative `None`-passthrough would propagate to JSON-serialized response and force the frontend to handle null — a wider blast radius. `0.0` localizes the convention.

**Sibling-class scan — `round(.*, 1)`:** ran `grep -rn "round([a-zA-Z_][a-zA-Z_0-9]*, 1)" mcp-server/central-command/backend/*.py` and inspected all 33 callsites. Categorized:

| Callsite | Status |
|---|---|
| `db_queries.py:103,105` (`l1_rate`) | Already guarded `if l1_rate is not None else None` |
| `db_queries.py:968,973,976` (`agent_score`) | Computed from `IF count>0 ELSE 0.0` (non-nullable) |
| `db_queries.py:1367` (`avg`) | Loop value, non-nullable |
| `db_queries.py:1565,1566,1573` (`overall_score`, `patch_mttr_hours`) | Default-zeroed before round |
| `db_queries.py:1753,1833` (`success_rate`) | Computed `if total>0 else 0.0` (non-nullable) |
| `db_queries.py:1883,1884,1949,1950,1989,1990` | **Fixed by this commit** |
| `org_management.py:1571` (`healing_rate`) | **Verified non-nullable** — local default before round (line 1568) |
| `metrics.py:181-417` (5 callsites) | Pydantic-validated params, non-nullable |
| `routes.py:4714,4875` | Computed inline, non-nullable |
| `sites.py:6573` (`coverage_rate`) | Verified non-nullable — local default |
| `assertions.py:624` (`open_min`) | Numeric difference, non-nullable |

**No sibling P0 surfaced.** The AST gate in the regression test covers `healing_rate` + `order_rate` symbol names; if a future PR introduces `auto_heal_rate` or similar, the gate will need an additive entry — **follow-up #FU-2** (broaden the nullable-name set OR re-anchor the gate on "round() inside an IfExp where the conditional touches `total_incidents > 0`").

**Signature_auth import fix correctness:** verified the deploy path:

- Dockerfile `WORKDIR /app`, `CMD uvicorn main:app`
- `main.py` (at `/app/main.py`) does `from dashboard_api.routes import router as dashboard_router`
- `sites.py` lives at `/app/dashboard_api/sites.py`
- `signature_auth.py` is at `/app/dashboard_api/signature_auth.py` (sibling of `sites.py`)
- Therefore `from signature_auth import` from inside `sites.py` searches sys.path which has `/app` but NOT `/app/dashboard_api` → ImportError
- Fix: `try: from .signature_auth import …; except ImportError: from signature_auth import …` — relative first (works in package context), absolute fallback (works in dev when cwd=`backend/`)
- Pattern matches the existing `appliance_relocation` fallback at `sites.py:4502-4508` — convention-consistent.

**Sibling-import scan:** 22 indented bare-imports in backend/, classified:
- 21× `from main import …` — main.py IS at `/app/`, so sys.path resolution works. NOT vulnerable. These work because main.py is on the top-level sys.path.
- 1× `from baa_status import …` in `audit_report.py:213` — wrapped in `try/except Exception` block already; soft-fails like the pre-fix sigauth was. **Flagged as sibling-class follow-up #FU-3** (verify baa_status import path works in prod; if it doesn't, the audit report has a soft-degraded code path).
- The `sites.py:4502` `appliance_relocation` import already uses the relative-first pattern — no fix needed.

**Verdict: APPROVE.** Both fixes are correct, the sibling scan is clean, the AST gate is structurally sound (modulo #FU-2 broadening).

### 2. Database (Maya) — APPROVE

**healing_rate type-flow:** `inc_row.total` and `inc_row.resolved` come from `COUNT(*)` and `COUNT(*) FILTER (...)` — Postgres returns `bigint`, asyncpg maps to Python `int`. The `(resolved / total_incidents * 100)` expression in Python 3 uses true division (`/` returns `float`), so the result is `float` when `total > 0`, `None` otherwise. No integer-division pitfall.

**Could `total_incidents` itself be `None`?** No — `inc_row.total if inc_row else 0` guards `inc_row=None`; once `inc_row` exists, `COUNT(*)` is non-null by Postgres spec. The condition `total_incidents > 0` is type-safe.

**Could `resolved` be `None`?** Same answer — `COUNT(*) FILTER (...)` returns 0, never NULL.

**No upstream issue.** The bug is purely the missing None-guard on the round() side; data-flow upstream is sound.

**Signature_auth DB-side concerns:** the `appliance_heartbeats.signature_valid` column accepts `BOOLEAN NULL`. Pre-fix, every row carried NULL because the verify path threw ImportError → swallowed → `_hb_verify_result = None` → stored as NULL. Post-fix, the column will receive TRUE/FALSE per actual verification. No schema migration needed; the column was correctly nullable from the start (intentional to distinguish "not yet verified / soft-mode" from "verification failed").

**Backfill question:** can historical NULL rows be re-verified? **No.** The `signed_data` payload stored alongside is the canonical payload as the daemon signed it; re-verifying would only confirm crypto math, not detect retroactive compromise. There's no value in backfill — the operator-facing posture is "from this commit forward, signature_valid is meaningful; rows before this are pre-D1-functional and should be treated as un-attested heartbeats, equivalent to pre-D1-ship rows."

**Verdict: APPROVE.** Type-flow analysis confirms no upstream None propagation; no schema or backfill action needed.

### 3. Security (Carol) — APPROVE-WITH-FIXES

**Customer-impact severity:** **moderate, scoped to heartbeat-channel attestation; compliance-bundle chain UNAFFECTED.**

- **What was inert:** every `appliance_heartbeats` row from D1's ship date (~2026-04-30 per `bcd6ea79 feat(identity-week-1): Ed25519 device identity + observe-only signature auth`) through 2026-05-13 has `signature_valid = NULL`. ~13 days of fleet heartbeats.
- **What was NOT affected:** `compliance_bundles.signature_valid` — that's the evidence chain, signed by a separate path (`evidence_chain.py`, server-side Ed25519). The auditor-kit / `/api/evidence` surface is sound. The chain of custody for **evidence** is intact; only **heartbeat-channel** attestation was silently off.
- **Substrate invariants didn't catch it because:**
  - `daemon_heartbeat_unsigned` (sev2) queries `agent_signature IS NULL` — daemon WAS sending signatures (`agent_signature` is non-null in DB), backend just couldn't verify them. False negative.
  - `daemon_heartbeat_signature_invalid` (sev1) filters `signature_valid IS NOT NULL AND signature_valid = FALSE` — explicitly excludes the NULL case. False negative.
  - **The exact failure mode (signatures present, verification path crashing pre-result) has NO substrate invariant.** This is the sev1 detection-gap class. **Follow-up #FU-1.**

**Counsel Rule 3 (chain of custody) implications:** the rule is "no privileged action without attested chain of custody." Heartbeats are NOT privileged actions — they're operational telemetry. The privileged-action chain (`fleet_orders` for signing_key_rotation, enable_emergency_access, etc.) is enforced at the DB level via `enforce_privileged_order_attestation` and is **independent of this bug**. So Counsel Rule 3 is NOT directly violated, but the spirit-of-the-rule (machine-enforce attestation) was compromised at the heartbeat layer.

**Compromise-masking question:** Did D1-inert mask a real compromise? **Almost certainly not** — for a heartbeat signature to be *invalid* (vs NULL), an attacker would need to forge a signature with the wrong key; the backend's INCORRECT path was throwing ImportError, then `except Exception: pass`, then storing NULL. An attacker substituting a forged-signed heartbeat would produce a non-NULL agent_signature that wouldn't have been verified (correct outcome from attacker's POV: their heartbeat lands with signature_valid=NULL, same as everyone else's, so the forgery is INVISIBLE in the data). **This is the actual exposure**: an attacker who replaced a daemon's signing key could have done so undetected during the inert window because legitimate-but-unverified and attacker-but-unverified are indistinguishable rows in the table.

**Materiality assessment for customer disclosure:** moderate-to-low. The substrate doesn't claim "every heartbeat is verified" anywhere in customer-facing artifacts (Auditor Kit, ClientReports, attestation letters). D1 was shipped 2026-04-30 in **observe-only** mode (commit `bcd6ea79`'s subject line literally says "observe-only signature auth"). The enforce-mode commit `d5b640cb feat(sigauth): enforce-mode rejection invariant + forensic logging (#168)` shipped later. **Pre-disclose review with counsel needed if enforce mode was advertised to any customer.** **Follow-up #FU-4.**

**Counsel Rule 4 (orphan coverage) violation:** YES, mild. The detection gap for "signature_valid IS NULL for an appliance that has agent_public_key on file" is an orphan-coverage class. The existing two invariants are SEVERE-NULL (agent_signature IS NULL) and SEVERE-INVALID (signature_valid = FALSE); the missing one is SEVERE-VERIFY-PATH-CRASHED (signature_valid IS NULL despite agent_signature being non-NULL). #FU-1 closes this.

**Verdict: APPROVE-WITH-FIXES.** The fix is correct and high-value; the follow-ups (#FU-1 invariant + #FU-4 counsel review) are required to close the security loop.

### 4. Coach — APPROVE

**Two-class commit bundling defense:**

- The two bugs were surfaced from a single investigation thread (user-reported sidebar "0 sites connected" → triage exposed the 500 → in the same session, audit of recent D1-touching code surfaced the bare-import bug). Bundling is defensible **because**:
  - Both were prod-critical P0s with active customer-visible impact (one operator-visible: dashboard fleet=0; one customer-invisible-but-compliance-impacting: NULL signature_valid).
  - Splitting into two commits would have either (a) deployed the dashboard fix first and left D1 inert for another deploy cycle, or (b) deployed the D1 fix first and left the fleet=0 outage for another cycle. Both options extend a P0's blast radius for procedural cleanliness — net harm.
  - The commit message explicitly delimits the two bugs with `P0 #1` and `P0 #2` headers, treating them as distinct classes in narrative even if a single commit unit.
- **Coach-precedent gap:** the commit body does NOT explicitly defend the bundling. Future commits in this shape should add a one-liner: "Bundled because [reason X]; alternative was [Y]; chose this trade because [Z]."
- **Single-shaped regression test bundled with both is OK** — the test file covers only the healing_rate class (file name says so); D1 ImportError class is not regression-tested except by the implicit "production startup imports work." **#FU-5** could add an import-shape test, but lower priority.

**Verdict: APPROVE.** Bundling defensible; rationale was implicit and should have been explicit; no further action required on bundling itself.

### 5. Auditor (OCR) — APPROVE

**Sample probe:** "auditor asks 'show me your fleet health on 2026-05-13 at 18:00 UTC'."

- **Pre-fix:** GET /api/dashboard/fleet → 500 TypeError. Frontend renders empty state. Auditor sees "service unavailable" — that's an operational-incident class, NOT an evidence-tampering class. The compliance_bundles for that hour are still ingested, signed, OTS-anchored — just not summarized for the operator.
- **Post-fix:** GET /api/dashboard/fleet → 200 with normal payload.

**Attestation-chain integrity:** UNCHANGED. The `compliance_bundles` table — auditor-grade evidence — was never affected by either bug. The Ed25519 chain is intact, OTS anchoring continues, the hash-chain walk in `/api/evidence/sites/{id}/auditor-kit` reproduces verbatim. **No auditor artifact regressed.**

**Operator-facing dashboard outage classification:** **operational-incident-not-attestation-chain-gap.** Per Session 218 RT33 framing, dashboard outages are tracked as availability incidents, not as §164.312 audit-controls gaps. Document this incident in the substrate-health timeline (auto-recorded via `assertions_loop` snapshots) but NOT in `admin_audit_log` as a privileged-chain event.

**Verdict: APPROVE.** No auditor-grade artifact was tampered or made unreproducible; the outage is operational and post-fix invisible in auditor surfaces.

### 6. PM — APPROVE-WITH-FIXES

**Cost-of-violation accounting:**

- **Cost of skipping Gate A this round:** would have been ~3 minutes of fork dispatch + ~5 minutes to read the fork verdict + ~2 minutes to file the follow-up tasks. Total: ~10 minutes. **Cost actually incurred:** the user surfaced the protocol gap mid-session, then this retroactive Gate B (~30 minutes). Net cost of skipping: **+20 minutes**, plus the named follow-ups that should have been created at Gate A time anyway.
- **What was missed by skipping Gate A:** see "Gate A retro" §. Materially the AST-gate broadening (#FU-2) and substrate-invariant gap (#FU-1) would have been caught at Gate A and either fixed in the same commit or filed as follow-ups in the same commit body.
- **Recommendation: every prod fix gets a brief Gate A.** Standardize the "fix template": (a) root cause one-paragraph, (b) sibling-class scan one-paragraph, (c) Gate A fork dispatch with the diff + sibling scan, (d) merge fix + capture Gate B follow-ups in commit body, (e) Gate B re-verification post-deploy with runtime evidence.

**Memory capture:** `feedback_fixes_need_two_gate_protocol_too.md` (new) — the lesson is "fixes are not exempt from the 2-gate protocol; in fact, fixes are MORE in need of Gate A because the sibling-class scan is the highest-value Gate A finding for narrow diffs." **Follow-up #FU-6** to write this memory file.

**Verdict: APPROVE-WITH-FIXES.** Fix is sound; protocol gap is the lesson; capture in memory and standardize fix-template.

### 7. Attorney (in-house counsel) — APPROVE-WITH-FIXES

**Counsel Rule 3 (chain of custody) materiality:** D1 inert is a chain-gap class for **heartbeat-channel attestation**, but heartbeats are not in the privileged-action chain. The privileged action types (signing_key_rotation, bulk_remediation, enable_emergency_access, delegate_signing_key) are independently chained at the `fleet_orders` + `compliance_bundles` layer and were NOT affected. So Counsel Rule 3 is NOT directly violated.

**However:** the master BAA contract draft (Counsel TOP-PRIORITY P0, task #56) is currently citing "every appliance heartbeat is Ed25519-signed and signature-verified" as one of the technical safeguards. If that copy ships before D1 is verified-operational across the full fleet for ≥7 days, the BAA contract makes a representation the runtime hasn't yet sustained. **Follow-up #FU-4** ties D1-operational-soak to BAA-contract-draft sign-off. Counsel-grade copy must wait for soak evidence.

**Disclosure obligation question:** does this period of NULL signature_valid require customer notification? **No, for now**, but with conditions:
- (a) No customer-facing artifact (Auditor Kit, ClientReports, attestation letters, BAA draft) currently asserts "heartbeats are cryptographically verified" in a way that misleads a counterparty. The Ed25519-heartbeat scheme has been in observe-only mode by design; the inert period was not user-perceptible.
- (b) IF the BAA draft, master-services agreement, or any pre-distribution sales material made the representation BEFORE the fix shipped, a disclosure (operator-only memo) may be required. **Run a copy-grep for "heartbeat" + "signed" + "verified" in customer-facing artifacts** — **follow-up #FU-4**.
- (c) If any compromise is later detected during the inert window via independent evidence (host-side log + appliance tamper-attestation), the disclosure obligation re-triggers.

**Counsel Rule 7 (no unauthenticated channel with meaningful context):** N/A — both bugs were in authenticated paths.

**Verdict: APPROVE-WITH-FIXES.** No immediate disclosure obligation; BAA-draft copy must be reconciled with the actual D1 soak state (#FU-4); operator memo for the substrate-team to internalize is sufficient at this layer.

---

## Sibling-class scan results — summary table

| Scan target | Count | Risk findings | Verdict |
|---|---|---|---|
| `round([a-z_]+, 1)` callsites in backend | 33 | 0 unguarded | CLEAN |
| `round([a-z_]+, [0-9])` broader pattern | 60+ | 0 unguarded on nullable rates | CLEAN |
| Indented `from <local> import` in backend/ | 22 | 21× from main (works in prod), 1× appliance_relocation (already fixed), 1× baa_status (deferred review #FU-3) | CLEAN modulo #FU-3 |
| AST gate coverage on regression test | NULLABLE_RATES = {healing_rate, order_rate} | Symbol-name allowlist; future additions need broadening | CLEAN modulo #FU-2 |
| Substrate invariants for D1 signature_valid | 2 (unsigned, invalid) | NULL-when-verify-path-crashed not covered | **GAP — #FU-1** |

---

## Customer-impact severity assessment

- **Healing_rate / dashboard fleet=0:** 100% outage of `/api/dashboard/fleet` for ANY site with zero recent incidents in the lookback window. Operator-facing only. ~immediate detection by user, ~immediate fix. Severity: **P0 operator-visible, P3 customer-disclosure.**
- **D1 signature_auth inert:** ~13 days of NULL `signature_valid` across the entire fleet's `appliance_heartbeats`. Customer-facing (Auditor Kit + ClientReports + BAA draft) MOSTLY unaffected because heartbeat verification is not yet a load-bearing claim in those artifacts. Severity: **P0 substrate-attestation-posture, P2 customer-disclosure conditional on BAA-draft copy review (#FU-4).**

---

## Full pre-push sweep evidence

```
$ bash .githooks/full-test-sweep.sh
✓ 249 passed, 0 skipped (need backend deps)
exit=0
```

Sweep includes the new `test_healing_metrics_none_guard.py` (2 tests). Per Session 220 Gate-B-must-run-full-sweep lock-in: **satisfied.**

---

## Follow-up tasks (Gate B findings — non-advisory)

- **#FU-1 (P0, sev1 detection gap):** New substrate invariant `daemon_heartbeat_signature_unverified` — sev1 — fires when an appliance has `agent_public_key IS NOT NULL` AND ≥3 of last 15 minutes' heartbeats have `agent_signature IS NOT NULL` BUT `signature_valid IS NULL`. Catches the verify-path-crashed class that the existing two invariants miss. Add runbook `substrate_runbooks/daemon_heartbeat_signature_unverified.md`.
- **#FU-2 (P1, regression-test broadening):** Broaden `NULLABLE_RATES` set in `test_healing_metrics_none_guard.py` OR re-anchor the AST gate on "round() inside an IfExp where the conditional touches `total_X > 0`" — current gate is symbol-name-allowlist; future engineers introducing `auto_heal_rate` etc. would bypass.
- **#FU-3 (P2, sibling-import deferred review):** Verify `from baa_status import is_baa_on_file_verified` at `audit_report.py:213` works in prod package context. If it doesn't, audit-report has a soft-degraded path. Quick fix is the same relative-then-absolute pattern.
- **#FU-4 (P0, customer-facing copy reconciliation):** Grep customer-facing artifacts (Auditor Kit README, BAA draft, ClientReports footer, attestation letters, sales pages on osiriscare.com) for "heartbeat" + "signed/verified" claims. If any assert "every heartbeat is cryptographically verified" without the observe-only qualifier, draft an operator memo + tie BAA-draft (task #56) sign-off to D1 ≥7-day operational soak.
- **#FU-5 (P3, low priority):** Add an import-shape regression test that exercises the `from .signature_auth import` package-context path under both cwd configurations. Coverage gap is small (one-line fix), so test priority is low.
- **#FU-6 (P1, memory capture):** Write `feedback_fixes_need_two_gate_protocol_too.md` — the lesson that fixes are not exempt from the 2-gate protocol. Index in `memory/MEMORY.md` under feedback section.

---

## Final verdict on `adb7671a`

**APPROVE-WITH-FIXES.** The fix is sound, the sibling-class scan is clean, the pre-push full sweep is clean (249/0/0), the customer-impact is bounded and operator-visible to substrate-team but not yet customer-facing in a misleading way. The protocol gap (no Gate A first) is the lesson, captured in #FU-6. The detection-gap closure (#FU-1) is the highest-value Gate B finding — without it, the next D1 silent-inert regression takes 13 days to surface again. #FU-4 ties the customer-facing copy in the BAA draft to the actual D1 soak state, closing a small Counsel Rule 1 / Rule 3 exposure.

**No BLOCK. No revert needed.** Ship the follow-ups as named tasks; do NOT bundle them into this Gate B commit body (Gate B verdict is observational; #FU-1 + #FU-4 are their own design + Gate A cycles).
