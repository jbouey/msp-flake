# Gate B verdict — zero-auth hardening Commit 1 (2026-05-11)

**Verdict:** **BLOCK** — mig 305 silently weakens `enforce_privileged_order_attestation`.

## Gate A directive compliance
- **P0-1** (three-list lockstep): ✓ — `python3 scripts/check_privileged_chain_lockstep.py` exits 0; all 3 lists + expected-set test contain `delegate_signing_key`.
- **P0-2** (prod audit): ✓ — pre-verified, 1 expired test row.
- **P0-3** (scope split): ✓ — only delegation endpoints in this commit; no heartbeat/provisioning/discovery/sensors changes.
- **P0-4** (`auth_site_id` replaces `request.site_id`): ✓ — `appliance_delegation.py:298` (`verify_appliance_ownership(...auth_site_id)`), `:335` (`"site_id": auth_site_id` in signed dict), `:357` (INSERT param) all use bearer-bound site.
- **P1-3** (key_id redact): ✓ — `appliance_delegation.py:315` uses `str(existing['key_id'])[:8] + "…"`.
- **P1-4** (audit-log row on 403): ✓ — `shared.py:485-505` writes `admin_audit_log` `cross_site_spoof_attempt` row inside `admin_transaction`; failure logs ERROR + still raises (lines 506-518).
- **P2-3** (lift to shared.py): ✓ — `shared.py:452`; `agent_api.py:65` imports from `.shared`; comment block at `agent_api.py:81-84`.

## Adversarial findings

### P0 — mig 305 weakens chain-enforcement function (Maya)
Mig 305 redefines `enforce_privileged_order_attestation()` with a **shorter body** than mig 223's. **Three regressions:**

1. **`parameters->>'site_id'` check removed** (mig 223:50, 63-68). Mig 223 required `site_id` to be present AND validated that the referenced `compliance_bundles` row is for the **same site** (mig 223:73 `AND site_id = v_site_id`). Mig 305:49-53 only checks bundle existence — a partner could attest privileged action with a bundle from a **different customer's site**. This is a chain-of-custody hole.
2. **Error prefix changed** from `PRIVILEGED_CHAIN_VIOLATION:` (mig 223:54) → generic text (mig 305:45). Any downstream alerting / log-scan / SIEM rule keyed on that prefix silently breaks.
3. **HINT clause dropped** (mig 223:59) — operator-debuggability regression.

`enforce_privileged_order_immutability` is NOT redefined by mig 305, so `CREATE OR REPLACE` leaves it intact — that part is fine.

**Required fix:** mig 305 must re-include mig 223's full body (site_id check + PRIVILEGED_CHAIN_VIOLATION prefix + HINT) and ONLY change `v_privileged_types` to append `'delegate_signing_key'`. The lockstep checker proves list parity but does NOT diff function bodies — this is a new test class (P1 follow-up: pin function body shape).

### P1 — `username=auth_site_id` shape inconsistent with privileged-chain convention (Maya)
`shared.py:501` writes `username=auth_site_id` (raw site_id literal like `north-valley-branch-2`). The CLAUDE.md privileged-chain rule says actor MUST be a named human email — but for appliance-initiated spoof the actor IS the appliance. Convention across the codebase (see `target=f"appliance:{auth_site_id}"` on line 502) uses `appliance:<id>` prefix. **Recommend:** `username=f"appliance:{auth_site_id}"` for grep-ability + parity with target. Not chain-breaking but a SIEM-friction issue.

### P1 — legacy stale-site_id callers will 403 after rename (Steve)
Python-agent legacy caller (`local_resilience.py:807` per Gate A) sends `request.site_id` from local config. If the site was renamed (canonical_site_id mapping in mig 256), legacy caller still sends old name, auth_site_id is the canonical new name → permanent 403. **Recommend:** in `_enforce_site_id`, resolve both sides through `canonical_site_id()` before comparing, OR document the migration requirement explicitly. Not blocking — this only bites renamed sites.

### P2 — per-entry enforce loop is correct, no flood-write (Carol)
Re-read confirms `appliance_delegation.py:464-465`: enforce loop raises on FIRST mismatch and exits the request — only 1 audit_log row per spoofing request, not N. False alarm in the brief.

### P2 — local imports inside `_enforce_site_id` (Steve)
`shared.py:481-482` imports `get_pool` + `admin_transaction` locally. Confirmed circular-import workaround — `fleet.py` imports `shared` (verified via grep). Acceptable, but add a one-line comment noting the reason. Pool DoS concern: 403 frequency is bounded by attacker-controlled traffic; one connection acquire per spoof is dwarfed by normal checkin volume.

### P2 — `test_appliance_delegation_auth_pinned` shape parity (Coach)
4 tests, AST-based, mirrors the `test_l*_resolution_requires_*` shape (path discovery + positive/negative controls). Final test reads mig file as text (correct — can't import a `.sql`). All 4 load-bearing.

## Pre-push sweep result
- `tests/test_appliance_delegation_auth_pinned.py` — pass
- `tests/test_privileged_chain_allowed_events_lockstep.py` — pass
- `tests/test_l1_resolution_requires_remediation_step.py` — pass
- `tests/test_l2_resolution_requires_decision_record.py` — pass
- `tests/test_substrate_docs_present.py` — pass
- `tests/test_pre_push_ci_parity.py` — pass
- **80 + 4 = 84 tests pass, 0 fail.** Lockstep checker exits 0.

## Per-lens analysis
- **Steve:** auth migration mechanically sound (13 awaits, zero orphans). Local-import workaround acceptable. Stale-site_id legacy-caller risk is P1.
- **Maya:** **the migration is the blocker.** Chain-of-custody primitive can't be silently shortened; site_id binding in trigger is the moat against cross-customer attestation reuse.
- **Carol:** audit-log shape good; per-entry loop bounded correctly; `username` value should be `appliance:<id>` for SIEM hygiene.
- **Coach:** test scaffolding follows sibling pattern; no missing-companion-file class introduced; pre-push sweep clean.

## Recommendation
**BLOCK.** Do NOT commit until:
1. **(P0)** Rewrite `migrations/305_delegate_signing_key_privileged.sql` so the function body equals mig 223's body verbatim except for the extra array entry. Add a comment block explicitly stating "mig 223 body preserved; only v_privileged_types changed." Run `psql` dry-run against staging to confirm behavior identical pre/post.
2. **(P1)** Change `shared.py:501` `username=auth_site_id` → `username=f"appliance:{auth_site_id}"`.
3. **(P1, deferred to followup TaskCreate)** Add `tests/test_privileged_chain_function_body_shape.py` that diffs the deployed function body against a pinned canonical to prevent future silent regressions of this class.
4. **(P1, deferred)** Document or fix the canonical_site_id legacy-caller class.

After fixes, re-run Gate B (lockstep + pinned tests + visual diff of mig 305 vs 223). If clean → APPROVE.
