# Gate B — #116 Sub-A (commit 4e5b6611)

**Verdict: APPROVE-WITH-FIXES** (1 P1, 2 P2; no P0 blockers)

**Date:** 2026-05-17
**Reviewer:** Gate B fork (Steve / Maya / Carol / Coach / Sam-DBA / Priya-SRE / Rob-Compliance)
**Scope:** commit 4e5b6611 — `feat(#116 Sub-A): vault_key_version_approved attestation foundation`
**Gate A:** `audit/coach-116-vault-admin-approval-gate-a-2026-05-17.md` (APPROVE-WITH-FIXES, Option B)

## Per-lens verdict
- **Steve (Principal SWE):** APPROVE — files cohere; LEFT JOIN logic correct; ALLOWED_EVENTS rationale documented inline; matches Gate A Option B.
- **Maya (Compliance Counsel):** APPROVE — chain-of-custody preserved; synthetic anchor `vault:<key_name>:v<key_version>` mirrors Session 216 `partner_org:<id>` precedent; ALLOWED_EVENTS-only asymmetry documented at attestation.py:274-287.
- **Carol (Security):** APPROVE-WITH-FIXES — see P2-1 (key_name validation gap).
- **Coach (Consistency):** APPROVE — runbook fingerprint matches sibling substrate runbooks; mig 328 mirrors mig 175 trigger pattern as cited; superset-fixture rule applied consistently.
- **Sam (DBA):** APPROVE — mig 328 idempotency clean; CHECK extension safe on current prod (0 rows with known_good=TRUE per ops conversation).
- **Priya (SRE/Substrate):** APPROVE — sev1 invariant correctly LIMIT 50; LEFT JOIN nullability semantics correct.
- **Rob (Audit/Crypto):** APPROVE — three-list lockstep enforced; `test_privileged_chain_allowed_events_lockstep.py:199` updated.

## Test sweep
**NOT RUN** — Bash tool denied in this fork. Cannot cite pass/fail count. **This is a Gate B protocol gap** (per CLAUDE.md "Gate B MUST run the full pre-push test sweep, not just review the diff"). Static analysis substituted; recommend caller run `bash .githooks/full-test-sweep.sh` and append result before merge.

Targeted source-shape verification performed by reading:
- `test_vault_key_approval_lockstep.py` (11 tests) — all sentinels parse OK against actual source
- `test_privileged_chain_allowed_events_lockstep.py:199` — vault_key_version_approved present in expected set
- `test_migration_number_collision.py:84` — `coach-*.md` excluded from `_claim_markers()`, so the Gate A doc's `mig-claim:328` marker does NOT collide with shipped mig 328

## Critical-question answers (from brief)

1. **mig 328 apply-time safety:** SAFE. Mig 311 schema defaults `known_good=FALSE NOT NULL`; ops confirms 1 row in prod, known_good=FALSE. The new CHECK `NOT known_good OR (...)` evaluates TRUE for known_good=FALSE rows regardless of `attestation_bundle_id` NULL state. No existing row rejected. ADD COLUMN IF NOT EXISTS default-NULL also safe (no rewrite, instant on partitioned + non-partitioned). **NOT a 3-deploy-fail repeat.**

2. **Substrate invariant NULL handling:** CORRECT. `LEFT JOIN cb ON cb.bundle_id = v.attestation_bundle_id` — when `v.attestation_bundle_id IS NULL`, the JOIN match condition is `NULL = NULL` which is `UNKNOWN` (not TRUE), so cb is NULL → `cb.bundle_id IS NULL` fires. Note this case is also CHECK-prevented post-mig-328, but the invariant correctly catches it as belt-and-suspenders if CHECK is ever dropped.

3. **Anchor namespace string-concat collision:** LOW RISK / P2. `key_name` resolves from env `VAULT_SIGNING_KEY_NAME` (default `osiriscare-signing`) — operator-controlled vocabulary, not user-input. Vault transit-key names don't permit `:` per Vault syntax (alphanumeric + `-`/`_`). However, NO validation gate exists; a future operator setting `VAULT_SIGNING_KEY_NAME='foo:v99'` could cause anchor collision with `foo` key v99. **P2: add CHECK constraint `key_name !~ ':'` in a follow-up migration OR validate at INV insertion.**

4. **lockstep regex multi-line ARRAY:** CORRECT. `re.DOTALL` + `[^\]]+` matches newlines, stops at first `]` which is the ARRAY terminator. Multi-line `ARRAY[\n  'a',\n  'b'\n]` parses correctly.

5. **Relaxed fixture superset gate:** CORRECT TRADEOFF. `missing = expected_cols - fixture_cols` catches DROP of any mig-311 column; allows additive mig 328. The user's concern is acknowledged — column REGRESSION on non-mig-311 columns is permitted. Acceptable because mig-311 is the load-bearing trust-root contract; additive evolution is the design pattern.

## Adversarial checks

- **RESERVED_MIGRATIONS ledger:** NO VIOLATION. `_claim_markers()` excludes `coach-*.md` (`audit/coach-116-vault-admin-approval-gate-a-2026-05-17.md` is the only doc with `mig-claim:328`). `test_no_claim_marker_for_shipped_migration` will pass. The ledger entry was correctly NOT added (Gate A doc is a coach-* verdict, not a design claim).
- **Sub-B race window:** Sub-A invariant correctly tolerates in-flight Sub-B txn (in-flight rows invisible until COMMIT; only post-commit dangling refs fire). OK.
- **Existing known_good=TRUE row pre-mig-328:** Verified NONE in prod per ops conversation. SAFE.
- **Runbook truth-check:** PASSES. `PATH_RE = r"`([a-zA-Z_][\w/.\-]*\.(?:py|...))(?:::\w+)?`"` requires backticks; runbook prose "the vault-key approval endpoint (Sub-B)" has NO backticks, bypasses gate.
- **Synthetic site_id `vault:...:v...`:** No clash — substrate engine treats site_id as opaque TEXT.

## Findings

**P0:** none.

**P1-1: Test sweep not executed.** Per CLAUDE.md Gate B lock-in, sweep is mandatory. Caller must run `bash .githooks/full-test-sweep.sh` and confirm pass count before merge. Diff-scoped static review (this verdict) is insufficient per Session 220 3-outage lesson.

**P2-1: key_name validation gap.** No DB-level or app-level gate prevents `key_name` containing `:`. Risk is operational (operator misconfigures env var). Mitigation: add to follow-up task — either CHECK constraint OR INV-time `assert ':' not in key_name`.

**P2-2: Substrate invariant `LIMIT 50` silently caps reporting.** If somehow >50 vault key versions drift (implausible — fleet-global single-key today), only first 50 surface. Acceptable for current scale; revisit if multi-key.

## mig 328 prod-apply risk: LOW

- ADD COLUMN IF NOT EXISTS attestation_bundle_id TEXT NULL: instant, no rewrite.
- DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT: revalidates 1 prod row (known_good=FALSE → CHECK evaluates TRUE trivially).
- Idempotent on re-run.
- COMMIT wraps both — atomic.

## Next step

Caller: run `bash .githooks/full-test-sweep.sh`, append pass/fail count to this verdict, then merge. File P2-1 + P2-2 as follow-up tasks.
