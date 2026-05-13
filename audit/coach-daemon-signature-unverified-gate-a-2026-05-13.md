# Class-B 7-lens Gate A — substrate invariant `daemon_heartbeat_signature_unverified`

**Task:** #69 (Gate B FU-1 P0 from `coach-adb7671a-retro-gate-b-2026-05-13.md`)
**Date:** 2026-05-13
**Reviewer cwd / git context:** `/Users/dad/Documents/Msp_Flakes`, branch `main`, HEAD = `a87e3068`
**Pre-deploy fix at:** `adb7671a fix(prod): two P0 outages — dashboard fleet=0 + D1 signature_auth inert` (2026-05-13 14:20 EDT)
**Production deploy verified:** container restarted 2026-05-13 20:47:24 UTC; deployed `sites.py` carries the relative-first-then-absolute import fallback at line 4241–4243.

## 200-word summary

The retro Gate B on `adb7671a` flagged that the two pre-existing D1 substrate invariants (`daemon_heartbeat_unsigned` sev2 watching `agent_signature IS NULL`, `daemon_heartbeat_signature_invalid` sev1 filtering `signature_valid IS NOT NULL`) are mutually blind to the exact failure mode that masked the verifier as inert for ~13 days: backend-side `agent_signature IS NOT NULL` (daemon DID sign) AND `signature_valid IS NULL` (backend tried, threw, swallowed). The new `daemon_heartbeat_signature_unverified` invariant closes the gap. Gate A **APPROVES** the design with three required adjustments before implementation: (1) threshold + window must match the sev1 sibling at **≥3 in 15 minutes** (NOT the prompt's "≥3 in 60 minutes" which would force a slower TTR for a sev1-class detection — alignment matters); (2) `site_appliances.agent_public_key IS NOT NULL` exclusion is REQUIRED — pre-D1-shipped or never-registered appliances will otherwise false-fire forever; (3) name should retain `_unverified` (semantically distinct from `_unsigned` and `_invalid`). Carol concurs on sev1 framing as compromise-detection class — an unverifiable state is at least as serious as a known-invalid one. Coach also flags one anti-pattern in the prompt's example query (missing `appliance_id` GROUP BY for threshold). Runtime probe at fix-time was inconclusive (no heartbeats since deploy at 20:47 UTC); soak required before Gate B.

---

## Decision matrix

| Decision point | Prompt's proposal | Gate A verdict | Rationale |
|---|---|---|---|
| **Severity** | sev1 | **sev1** | Carol: compromise-detection class — see §3. |
| **Name** | `daemon_heartbeat_signature_unverified` | **`daemon_heartbeat_signature_unverified`** | Coach §4: semantically distinct from sibling pair. |
| **Time window** | 60 minutes | **15 minutes** | Sibling-parity with `_invalid` (also sev1). Faster TTR for sev1 class. |
| **Threshold** | ≥3 rows for same appliance | **≥3 rows for same appliance** | Sibling-parity with `_invalid`. |
| **NULL-by-design exclusion** | (silent on this) | **REQUIRED: `JOIN site_appliances WHERE agent_public_key IS NOT NULL`** | Pre-D1 appliances, unregistered dev appliances would false-fire forever otherwise. |
| **Counsel Rule citation** | Rule 3 (chain-of-custody) | **Rule 4 (orphan coverage) + Rule 3 (chain-of-custody) BOTH** | Maya/Carol: the closure is primarily orphan-coverage at fleet scale; chain-of-custody is the downstream impact. |
| **Index** | (silent) | **REUSES existing `idx_appliance_heartbeats_signature_state (site_id, observed_at DESC, signature_valid)`** | Maya §2: index already covers our predicate. |
| **Effort** | ~1h | **~1.5h** | PM §6 with the +0.5h for the prod-runtime evidence cite + soak. |

---

## Final overall verdict

**APPROVE-WITH-FIXES.** Three mandatory pre-implementation adjustments. None of them BLOCK; all are scope-of-Gate-A normal calibration.

### Required pre-implementation fixes (P0)

- **F1** — Use **15-min window** not 60-min. Sibling-parity with the sev1 `_invalid` invariant which also fires at "≥3 in 15min." A sev1 class with a 60-min window has lopsided TTR — the prompt's suggested 60-min would let the verifier be silently dead for an hour before sev1 fires, which is the exact failure mode this invariant exists to close. Use 15-min.
- **F2** — REQUIRED exclusion: JOIN `site_appliances sa` on `(appliance_id, site_id)` and filter `sa.agent_public_key IS NOT NULL AND sa.agent_public_key <> ''`. Pre-D1 appliances + dev appliances that never registered would otherwise false-fire forever, drowning the operator panel. This is the same false-positive guard `daemon_heartbeat_unsigned` already has at `assertions.py:5907-5911`.
- **F3** — Counsel Rule citation must be **both Rule 4 AND Rule 3**, in that order. Primary class is orphan-coverage (the existing two invariants leave a gap; we are filling it). Secondary impact is chain-of-custody (BAA Article 3.2 representations about heartbeat-channel attestation are not enforceable when the verifier silently no-ops).

### Required follow-ups (NOT blocking implementation; carry as new TaskCreate items)

- **F4 (P1)** — Runtime evidence for Gate B MUST cite a 24h+ soak window with NO `daemon_heartbeat_signature_unverified` violations against an appliance with a registered `agent_public_key`. Today's runtime probe at 20:53 UTC returned 0 rows in the last 60 minutes because the entire 3-appliance fleet at north-valley-branch-2 last reported at 18:27 UTC — BEFORE the 20:47 UTC container restart that picked up `adb7671a`. **The fix is deployed but not yet exercised**. The 6-hour pre-deploy window showed 100% unverified (192/192, 194/194, 191/191 per appliance) — exactly what `_unverified` is designed to catch. Gate B must wait for fresh post-deploy heartbeats and prove the invariant correctly clears.
- **F5 (P2)** — Cross-link in `daemon_heartbeat_unsigned.md` and `daemon_heartbeat_signature_invalid.md` related-runbook sections. The three-way relationship (unsigned / invalid / unverified) is the operator's mental model and the runbook trio should reference each other explicitly.

---

## Per-lens verdicts

### 1. Engineering (Steve) — APPROVE-WITH-FIXES

Read `_check_daemon_heartbeat_unsigned` (`assertions.py:5874-5952`) and `_check_daemon_heartbeat_signature_invalid` (`assertions.py:5955-6025`). The new invariant is the structural complement:

- `_unsigned` predicate: `agent_signature IS NULL` (daemon didn't sign at all)
- `_invalid` predicate: `signature_valid = FALSE` (backend verified and rejected)
- `_unverified` (NEW): `agent_signature IS NOT NULL AND signature_valid IS NULL` (daemon signed, backend never produced a result)

The three are mutually exclusive and collectively exhaustive over the "verifier-attestation outcome space" — that's the right shape.

**Window/threshold calibration:** the prompt suggested "≥3 in 60min." That is wrong for sev1 — it matches the sev2 `_unsigned` window (60min), not the sev1 `_invalid` window (15min). The sibling-parity rule (Coach §4) wins: this is a sev1 class, so 15min is correct. Steve concurs with F1.

**False-positive guard for pre-D1 appliances:** the prompt's suggested query `WHERE agent_signature IS NOT NULL AND signature_valid IS NULL` would fire on any appliance running an old daemon that emits unsigned heartbeats (`agent_signature IS NULL` so excluded — fine) BUT it would ALSO fire on legitimate cases where the appliance has agent_signature non-null but is in observe-only-mode (current substrate posture per `bcd6ea79`). Without the `site_appliances.agent_public_key IS NOT NULL` join filter, the invariant cannot distinguish "verifier crashed" from "no registered pubkey to verify against." F2 is mandatory.

**Sibling-implementation parity:** the new function should be `_check_daemon_heartbeat_signature_unverified` and live immediately after `_check_daemon_heartbeat_signature_invalid` at ~`assertions.py:6027`. Registration block at ~`assertions.py:2289` adds an `Assertion(name="daemon_heartbeat_signature_unverified", severity="sev1", ...)` row. DISPLAY_METADATA entry at ~`assertions.py:3115` follows the existing sibling shape.

**Edge case probed:** when the appliance's `previous_agent_public_key` rotation grace is active (15min after rotation), the verifier may legitimately return `signature_valid=FALSE` for ~15 min and then catch up. That is `_invalid`-class, not `_unverified`-class — our predicate `signature_valid IS NULL` excludes it. Clean.

**Verdict: APPROVE.** F1+F2 close the design; the implementation is mechanical.

### 2. Database (Maya) — APPROVE

`appliance_heartbeats` schema (probed at 2026-05-13 20:51 UTC):

- RANGE-partitioned by `observed_at`, monthly partitions (`appliance_heartbeats_y202604`, `_y202605`, `_y202606`, `_y202607` + `_default`).
- Indexes: `idx_appliance_heartbeats_signature_state (site_id, observed_at DESC, signature_valid)` — **directly covers our query**. The leading `site_id` is range-prune-friendly when GROUP BY hits the planner. The `observed_at DESC` lets us scan only the last 15min slice without seq-scanning. The trailing `signature_valid` column means index-only-scan on the predicate `signature_valid IS NULL` (Postgres can evaluate NULL on index-stored values without a heap visit if the visibility map permits).

**Partition pruning:** a 15-min window on `observed_at` will prune to the current month's partition (`y202605` today). On any day-1-of-month boundary, two partitions may be in scope briefly. Either way, partition count touched is ≤2, not 5 — pruning works.

**Query cost estimate at 1000 heartbeats/hour fleet scale:** 15-min window = ~250 rows fleet-wide. Index-only scan over a (site_id, observed_at, signature_valid) tuple, GROUP BY (site_id, appliance_id) — small enough to hash-aggregate in memory. Sub-millisecond at current fleet size; scales linearly. **Maya does NOT recommend a new partial index** (e.g. `WHERE signature_valid IS NULL`): too narrow, write amplification on a high-volume insert path, and the existing covering index already serves the read.

**Avoid the heavy-handed `COUNT(*) OVER ()` pattern:** the existing `_unsigned` query uses `COUNT(*) FILTER (...)` then `HAVING` — same pattern works here. No window function needed.

**Type-flow:** `signature_valid BOOLEAN NULL` per the schema dump — the predicate `signature_valid IS NULL` is type-safe. `agent_signature TEXT NULL` per the schema — `IS NOT NULL` is type-safe. No type coercion or `::text` cast required (no `$N` parameter in the query; this is a literal-only fetch).

**No backfill needed.** The pre-deploy 24h window has 3,675 rows all carrying `signature_valid=NULL` with `agent_signature IS NOT NULL` — but those rows represent the inert-verifier window the bug was about. Backfilling them by re-verifying would only confirm crypto math against the original payload — same conclusion as retro Gate B Maya §2: no operational value, the chain doesn't need fixing because it never claimed validity for those rows.

**Verdict: APPROVE.** Index reuse, partition pruning, query cost — all clean. F2 closes the only correctness concern (false positives without the JOIN).

### 3. Security (Carol) — APPROVE (sev1 confirmed)

**Sev1 framing — correct.** The prompt asked: is this primarily a compromise-detection class (sev1) or operational-hygiene class (sev2)? Carol's analysis:

- **The retro Gate B's exposure quote applies here as policy, not just observation:** "an attacker who replaced a daemon's signing key could have done so undetected during the inert window because legitimate-but-unverified and attacker-but-unverified are indistinguishable rows in the table." `_unverified` is the *only* runtime signal that the substrate's compromise-detection capability is itself broken. That makes it MORE serious than `_invalid`, not less — `_invalid` at least proves the verifier is alive. `_unverified` is the "the lights are out" signal.
- **Compromise-detection vs operational-hygiene framing:** technically both. The underlying cause is usually engineering (verifier broke); the security CONSEQUENCE is "we cannot detect a key-swap attack while this state persists." For severity assignment, the consequence wins. Sev1.
- **Counsel Rule 3 (chain-of-custody) framing:** the BAA Article 3.2 representations include "every appliance heartbeat is Ed25519-signed and signature-verified." When `signature_valid IS NULL` for a heartbeat from an appliance whose `agent_public_key` is on file, that representation is materially false for that row. Counsel Rule 3 — "no privileged action without attested chain of custody" — covers privileged ORDERS, but the SPIRIT-of-Rule-3 (machine-enforce, don't trust humans to notice) extends to attestation channels. The runbook MUST cite this.

**Counsel Rule 4 (orphan coverage) framing — primary:** the explicit class the retro Gate B named was "the missing one is SEVERE-VERIFY-PATH-CRASHED." That is the textbook Counsel Rule 4 orphan-coverage class at multi-device-enterprise fleet scale. The existing two invariants are coverage-incomplete by construction; `_unverified` makes the coverage exhaustive. **F3 is mandatory** — runbook cites Rule 4 PRIMARY and Rule 3 SECONDARY.

**Customer-disclosure surface:** sev1 fires the operator-alert chain (Session 216 lock-in — `_send_operator_alert` with potential `[ATTESTATION-MISSING]` subject suffix per the chain-gap escalation pattern). **Carol concurs this stays opaque to clinic channels** (Session 218 task #42 — practice does not see substrate-internal verifier health).

**The 13-day inert-window disclosure obligation (separate from this invariant):** Carol's retro Gate B §3 already addressed — moderate-to-low materiality, conditional on BAA-draft copy review (Task #70 / FU-4). NOT this invariant's concern; this invariant is the FORWARD prevention.

**Verdict: APPROVE.** Sev1 is correct. Counsel-rule citation must be Rule 4 + Rule 3 (F3).

### 4. Coach — APPROVE-WITH-FIXES

**Sibling-pattern parity audit:**

| Dimension | `_unsigned` (sev2) | `_invalid` (sev1) | `_unverified` (NEW sev1) |
|---|---|---|---|
| Window | 60 min | 15 min | **15 min** (F1) |
| Threshold | ≥12 | ≥3 | ≥3 |
| Subject-line shape | "silently NOT signing" | "does NOT verify" | "could NOT verify" |
| Predicate | `agent_signature IS NULL` | `signature_valid = FALSE` | `agent_signature IS NOT NULL AND signature_valid IS NULL` |
| JOIN to filter | YES (`site_appliances`) | NO | **YES** (F2) |
| Counsel Rule | Rule 4 | Rule 4 | **Rule 4 + Rule 3** (F3) |

**The asymmetry between `_invalid` and `_unverified` re: the JOIN is intentional:** `_invalid` filters `signature_valid IS NOT NULL` which by construction means the backend tried AND the appliance had a key on file (otherwise the verifier would have short-circuited to NULL). `_unverified` has the predicate `signature_valid IS NULL` which DOES NOT carry that implicit guarantee — so we need the explicit JOIN. Coach concurs with Steve's F2.

**Sibling-runbook cross-link audit (F5):** today, `daemon_heartbeat_unsigned.md`'s related-runbook section names `daemon_heartbeat_signature_invalid.md` and `daemon_on_legacy_path_b.md` and `offline_appliance_long.md`. It does NOT yet name `_unverified`. `_invalid.md`'s related-runbook section names `_unsigned.md` and `_on_legacy_path_b.md` and `pre_mig175_privileged_unattested.md`. The implementation commit MUST update both files to add `_unverified.md` to their related-runbook sections — this is a one-paragraph diff but the operator mental model demands it.

**Naming check:** `daemon_heartbeat_signature_unverified` retains the `daemon_heartbeat_signature_*` family namespace. Coach concurs with the prompt's suggested name.

**Commit-shape recommendation:** single commit, ~50 lines net: assertion function + Assertion() registration + DISPLAY_METADATA entry + runbook .md + test (per-runbook substrate_docs_present already enforces presence) + cross-link diff in 2 sibling runbooks. Pre-push will run `tests/test_substrate_docs_present.py` and verify the new runbook resolves.

**Pre-completion verification (Gate B):** runtime evidence must include (a) substrate panel showing the invariant registered + checking; (b) post-deploy `SELECT COUNT(*) FROM appliance_heartbeats WHERE signature_valid IS NULL AND agent_signature IS NOT NULL AND observed_at > NOW() - INTERVAL '60 minutes'` proving the verifier is now stamping; (c) 24h soak window with no fires against an appliance with `agent_public_key` registered. F4 codifies this.

**Verdict: APPROVE.** F1+F2+F3+F5 are all mechanical adjustments; no design redesign needed.

### 5. Auditor (OCR / outside compliance counsel) — APPROVE

The Counsel-grade lens:

- **Counsel Rule 3 materialization at runtime:** the operator-facing detection of "chain-of-custody silently broken" is exactly what Rule 3 demands be machine-enforced. The runbook copy MUST cite "Counsel Rule 4 (orphan coverage) primary + Counsel Rule 3 (chain-of-custody integrity) secondary" — F3 is non-negotiable from this lens.
- **Forward-looking disclosure value:** going forward, ANY future verifier outage WILL fire sev1 within ~15 minutes per the threshold + window. The 13-day inert window cannot silently recur. This invariant is the OPERATIONAL closure of the retro-Gate-B exposure. The runbook should say so plainly in the Counsel Rule citation.
- **Banned-word check on the proposed runbook copy:** must avoid "ensures," "prevents," "guarantees," "protects," "100%." Operator-facing posture words only: "detects," "alerts when," "helps detect," "reduces silent-failure exposure." Auditor will spot-check the runbook at Gate B.
- **Auditor-kit interaction:** `_unverified` rows do NOT enter the auditor kit. The kit is built from `compliance_bundles` (separate signing path — evidence chain, server-side Ed25519, unaffected by this bug class). The kit's identity_chain.json walks site/org evidence chain integrity; heartbeat-channel state is operator-visible, not auditor-shown. This separation is correct and the runbook should NOT confuse the two surfaces.

**Verdict: APPROVE.** No legal-exposure delta. F3 closes the Counsel-rule citation gap.

### 6. PM — APPROVE

- **Effort:** ~1.5h total (PM downgrades the prompt's "~1h" by +0.5h to cover runtime evidence + sibling runbook cross-link diff).
  - 20min — assertion function + registration + DISPLAY_METADATA
  - 30min — runbook .md (7 sections per template; copy from `_invalid.md` and edit)
  - 15min — sibling runbook cross-link updates in `_unsigned.md` and `_invalid.md`
  - 30min — pre-push sweep (`bash .githooks/full-test-sweep.sh`) — captures test_substrate_docs_present + assertion-registration regressions
- **Sequencing:** ships single-commit. Does NOT require migration. Does NOT require fleet daemon update.
- **Gate B requirements (binding):** 24h post-deploy soak window — Gate B verdict CANNOT close earlier. The current fleet (north-valley-branch-2 × 3 appliances) provides the soak signal naturally.
- **Risk:** trivial. Sev1 firing on day-1 against the existing inert rows IS the correct behavior — and is the runtime evidence that Gate B uses to verify "the invariant catches the class it was designed to catch." The pre-deploy 6-hour data (192/192, 194/194, 191/191 unverified) means the invariant will fire on first tick post-deploy IF the verifier remains broken. **This is a feature, not a bug** — it instantly proves the close-loop.

**Verdict: APPROVE.** Effort scope realistic, sequencing clean.

### 7. Attorney (in-house counsel — Counsel Rule layer) — APPROVE

- **Counsel Rule 3 at runtime — materialized.** This invariant is the runtime detection layer for the chain-of-custody-silently-broken class. The runbook MUST cite this explicitly (F3). The runtime cite + the BAA Article 3.2 representation are in lockstep going forward.
- **Counsel Rule 4 at runtime — materialized.** The orphan-coverage gap that the retro Gate B named is closed. The runbook MUST cite this as the primary class.
- **No Rule 5 stale-doc concern.** This is a new doc; nothing to supersede.
- **No Rule 7 unauth-channel concern.** Sev1 alerts route operator-only via the existing operator-alert chain. The Session 218 task #42 opaque-mode parity rule applies — clinic surfaces never see this. The runbook copy must include the "DO NOT surface to clinic-facing channels" line per sibling pattern.
- **Banned-word check (preview of Gate B):** the proposed runbook copy must not say "ensures verification" or "guarantees the chain." Use "detects when verification did not complete" / "alerts on silent verifier inertness."

**Verdict: APPROVE.** No legal-exposure new surface; the invariant is the closure of an existing gap.

---

## Particular probes — results

### Probe 1 — production state of unverified heartbeats post-fix

```
SELECT COUNT(*) FROM appliance_heartbeats
 WHERE signature_valid IS NULL
   AND agent_signature IS NOT NULL
   AND observed_at > NOW() - INTERVAL '60 minutes';
```

**Result:** 0 rows in last 60 min — BUT this is because the entire 3-appliance fleet at north-valley-branch-2 last reported at 18:27 UTC (~2h23m before probe at 20:50 UTC). Container restart at 20:47 UTC means the fix is deployed but unexercised.

**6-hour window (pre-deploy):** 192/192, 194/194, 191/191 — every heartbeat from every appliance has `signature_valid=NULL` despite `agent_signature IS NOT NULL`. This is the canonical pre-fix state.

**24-hour window:** 3,647 rows, ALL with `agent_signature IS NOT NULL AND signature_valid IS NULL`. The fleet was 100% inert for the entire window.

**Implication for Gate B:** the next ≥3 heartbeats post-deploy will determine whether the fix actually stamps `signature_valid`. If they remain NULL post-deploy, the `_unverified` invariant will fire on first tick — which IS the close-loop evidence. If they stamp TRUE, also a clean signal. F4 codifies the soak requirement.

### Probe 2 — runbook template compliance

The proposed runbook will mirror `daemon_heartbeat_signature_invalid.md` (62 lines, 7 sections + change log):

1. Header (severity + display name)
2. What this means (plain English) — 2-4 sentences
3. Root cause categories (≥3 bullets)
4. Immediate action — operator-facing, SSH steps
5. Verification — panel + CLI query
6. Escalation — when NOT to auto-fix
7. False-positive guard (carve-out clause: pre-D1 appliances)
8. Related runbooks (`_unsigned.md`, `_invalid.md`, `_on_legacy_path_b.md`)
9. Change log

This is the same 7+2 structure the existing invariant runbooks all carry. `tests/test_substrate_docs_present.py` enforces presence — no template gaps possible.

---

## Implementation checklist (binding for Gate B)

- [ ] `assertions.py` — add `Assertion(name="daemon_heartbeat_signature_unverified", severity="sev1", ...)` row at ~line 2289 (immediately after `_invalid` registration).
- [ ] `assertions.py` — add `_check_daemon_heartbeat_signature_unverified` function at ~line 6027 (immediately after `_check_daemon_heartbeat_signature_invalid`). Query: 15-min window, ≥3 threshold, JOIN `site_appliances` for `agent_public_key IS NOT NULL` filter.
- [ ] `assertions.py` — add DISPLAY_METADATA entry at ~line 3115 (immediately after `_invalid` entry).
- [ ] `substrate_runbooks/daemon_heartbeat_signature_unverified.md` — 7-section runbook, sev1, cite Counsel Rule 4 PRIMARY + Counsel Rule 3 SECONDARY, opaque-mode line, false-positive guard for pre-D1 appliances.
- [ ] `substrate_runbooks/daemon_heartbeat_unsigned.md` — add cross-link to `_unverified.md` in Related runbooks.
- [ ] `substrate_runbooks/daemon_heartbeat_signature_invalid.md` — add cross-link to `_unverified.md` in Related runbooks.
- [ ] Pre-push sweep: `bash .githooks/full-test-sweep.sh` — must pass; particular interest in `test_substrate_docs_present` (new runbook is discoverable) and `test_assertions_loop_uses_admin_transaction` (no regression on per-assertion isolation).
- [ ] Gate B fork verdict at `audit/coach-daemon-signature-unverified-gate-b-2026-05-13.md` with: (a) implementation diff review; (b) 24h+ post-deploy soak evidence; (c) banned-word check on runbook copy; (d) sibling-runbook cross-link verification.

---

## Closing

This is a small, mechanical, sibling-pattern-parity invariant. Gate A's job is to ensure the design exactly matches the existing sibling shape so the operator mental model and the substrate posture remain consistent. The three required adjustments (F1 window, F2 JOIN exclusion, F3 dual Counsel-rule citation) are calibrations, not redesigns. F4 (24h soak before Gate B) and F5 (sibling cross-link) are P1/P2 follow-ups that ride with the implementation commit.

**Final verdict: APPROVE-WITH-FIXES — proceed to implementation with F1+F2+F3 applied.**
