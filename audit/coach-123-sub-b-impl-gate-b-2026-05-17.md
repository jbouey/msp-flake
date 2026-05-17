# Gate B — #123 Sub-B impl — 2026-05-17

**Commits:** 2a74edba → e9b3183c → c26731f1 (prod c26731f1)
**Verdict:** APPROVE-WITH-FIXES

## Steve (architect)

- **admin_transaction used:** VERIFIED at `bulk_bearer_revoke_api.py:138` (`async with admin_transaction(pool) as conn`). Multi-statement path correctly avoids `admin_connection`. No `admin_connection(pool)` callsite in module.
- **FOR UPDATE present:** VERIFIED at `bulk_bearer_revoke_api.py:149` (`FOR UPDATE` on SELECT lookup, ORDER BY appliance_id deterministic).
- **Idempotency partition shape:** to_flip + already_revoked are BOTH passed into the attestation via `target_appliance_ids=appliance_ids` (line 219, full dedup-sorted input set) AND `approvals=[{...to_flip, already_revoked}]` (lines 220-225). UPDATEs at lines 238-256 fire ONLY when `to_flip` is non-empty. Correct: auditor sees full operator intent; physical writes are minimal.
- **Concurrent-call safety:** FOR UPDATE on the SELECT serializes two parallel revoke calls for overlapping appliance_ids — caller B blocks at lookup until caller A's transaction commits. Caller B then sees those rows as `bearer_revoked=TRUE` → falls into `already_revoked[]`, writes fresh attestation, no double-flip. CORRECT.
- **Order of operations:** Attestation write (line 212) precedes column flips (line 238). On PrivilegedAccessAttestationError, the `async with` exits via raise → admin_transaction rolls back → no UPDATE lands. CORRECT.
- **One observation, not a finding:** `target_appliance_ids` is the *deduped+sorted input set* not `to_flip ∪ already_revoked` (the live-set). If any input ID is `not_actionable`, the 404 fires before line 219 — so in the success branch `appliance_ids == sorted(to_flip + already_revoked)`. Equivalent, but worth knowing if someone refactors the 404-on-any-miss into a partial-success branch.

## Maya (security/HIPAA)

- **404 unification:** VERIFIED at line 192. Response body is the literal string `"one or more appliance_ids not found at this site"` — no per-ID enumeration, no distinction between missing vs soft-deleted. PASS. The denormalized `not_actionable[]` lands ONLY in `admin_audit_log.details` (line 180), an admin-only sink — existence-oracle defense intact.
- **BAA deferral shape:** `baa_enforcement.py:114-131` matches the precedent paragraph shape (decision-not-defer, §-test citation, audit-trail location, follow-up framing). EXPLICITLY acknowledges CE-row mutation per Gate A re-check P0-2 caveat at line 118-120 ("Unlike partner_admin_transfer's zero-CE-state shape, bulk_bearer_revoke DOES mutate CE-customer rows"). PASS.
- **Dedup+sort on attacker input:** `appliance_ids = sorted(set(req.appliance_ids))` at line 131. Pydantic `max_length=50` (line 84) caps blast radius BEFORE dedup, so the set-collapse can't amplify or exploit. No exploit window. The regex `^[a-fA-F0-9-]{32,40}$` (line 66) is permissive (32-40 hex+dash) but the SQL is parametrized + cast to `::text[]`; no injection surface.
- **Audit-on-404 INSERT visibility:** `admin_audit_log` is admin-only — caller cannot read it back via this endpoint. The 404 body does NOT echo `not_actionable[]`. PASS.
- **Rate-limit on bulk_bearer_revoke:** **MISSING.** `count_recent_privileged_events` is referenced ONLY in `fleet_cli.py:454,464` (CLI 3/site/week cap). The endpoint does NOT invoke it. A compromised admin session can call `revoke-bearers` 50-at-a-time in an unbounded loop. Vault precedent (`vault_key_approval_api.py`) also omits the cap — so this is sibling-precedent, not a regression — but the endpoint's surface (revokes live customer-appliance access) is far more disruptive than vault key approval. **CAVEATED P1**, not P0, because (a) it requires admin compromise, (b) `admin_audit_log` records every call, (c) Pydantic max_length=50 caps per-call blast radius. Recommend a 3/site/week cap mirroring fleet_cli, ALONGSIDE re-adding the cap to vault_key_approval_api.

## Carol (test/CI)

- **Pre-push sweep:** 286 passed / 0 skipped (full-test-sweep.sh from worktree root).
- **Schema-analyzer false-positive class (c26731f1):** the prose-in-docstring trap (module docstring parsed as SQL) is a NEW class. The analyzer at `test_sql_columns_match_schema.py` is greedy on `^.*UPDATE\s+\w+\s+SET` even inside `"""..."""`. The fix trims the docstring; the underlying class is not closed. **P1:** add a `# sql-skip` directive convention OR make the analyzer skip lines inside string literals (AST-aware).
- **chain_attestation_no_inline_duplicates (e9b3183c) needed parallel update to `scripts/check_privileged_chain_lockstep.py`?** Verified by reading the checker: it scans `fleet_cli.PRIVILEGED_ORDER_TYPES`, `ALLOWED_EVENTS`, and mig SQL `v_privileged_types`. It does NOT reference `chain_attestation.py` allowlist. NO parallel update needed. PASS.
- **Source-shape gate catches kwarg removal:** `test_attestation_uses_target_appliance_ids_kwarg` at `test_bulk_bearer_revoke_endpoint.py:159` does `assert "target_appliance_ids=appliance_ids" in src`. Removing the kwarg would fire. PASS for that specific regression. WEAKNESS: if someone passes a different variable name (`target_appliance_ids=req.appliance_ids` would FAIL the test even though semantically identical to `appliance_ids` via line 131). Minor false-positive risk, acceptable.
- **The 3 CI-fix commit class:** sentinels caught the endpoint correctness, but neither (i) chain_attestation_no_inline_duplicates nor (ii) test_sql_columns_match_schema were in the curated pre-push SOURCE_LEVEL_TESTS array — both ran ONLY at CI. The full-test-sweep.sh DOES run them but was apparently skipped during iter-1 commit prep. **P1:** add both tests to SOURCE_LEVEL_TESTS so any new privileged-class endpoint catches them in the fast lane (~45s).
- **Schema fixtures parity:** `api_keys` cols include `active`, `key_hash`, `key_prefix`. `site_appliances` includes `bearer_revoked`. All 4 sidecars consistent — no schema migration in Sub-B, no fixture refresh needed. PASS.

## Coach (consistency-coach)

- **Sub-A P1-5 idempotency runbook doc:** VERIFIED. `substrate_runbooks/bearer_revoked_without_attestation.md` lines 103-105: "The endpoint is idempotent on the already-TRUE column; it writes the missing `compliance_bundles` row + admin_audit_log row, the invariant clears on the next 60s tick." Recovery procedure works against the as-shipped endpoint. PASS.
- **Deferred items in commit body — are they enumerated as TaskCreate?** The commit body lists three deferrals: (1) `test_bulk_bearer_revoke_endpoint_pg.py` PG-integration, (2) Sub-C frontend UI, (3) fleet_cli wire-up. None are visible as TaskCreate followups in this session's tool transcript. **P1:** create explicit TaskCreate rows for each before marking Sub-B complete, per Gate B "P1 must be carried as named TaskCreate followups" rule.
- **Mig 329 ledger row:** `git show 16906008 -- RESERVED_MIGRATIONS.md` returns empty (no change in that commit). Grep of current ledger for "329" returns empty. The row was either removed in the Sub-A shipping commit (cleanly) OR was never claimed there. Either way: ledger is CLEAN at HEAD. PASS.
- **Sub-A Gate B P1-3 synthetic UPDATE gate:** NOT shipped alongside Sub-B. Still outstanding. Sub-B did not create a new opportunity (no `synthetic` writes added). Carry forward.
- **Sub-A Gate B P0 (Go list reconciliation):** VERIFIED CLOSED. `appliance/internal/orders/processor.go:527` `dangerousOrderTypes` does NOT contain `bulk_bearer_revoke`. `tests/test_privileged_order_four_list_lockstep.py:79` has `"bulk_bearer_revoke"` in `PYTHON_ONLY`. PASS — sibling-precedent with mig 305 delegate_signing_key is now uniform.
- **Schema-fixture refresh (api_keys.active touched):** verified all 4 sidecars consistent without regeneration; no INSERT or DDL in Sub-B, only UPDATE on existing columns. NOT NEEDED.

## P0 (must close before marking Sub-B complete)

None. Foundation is sound; the 3 fix-iteration commits closed real CI gaps without diluting the design.

## P1 (named follow-up tasks this session)

1. **Add 3/site/week rate-limit to bulk_bearer_revoke endpoint** mirroring `fleet_cli.py:454,464`. Compromised-admin nuclear-loop defense. Also retrofit to `vault_key_approval_api.py` (same gap).
2. **Add `test_chain_attestation_no_inline_duplicates.py` + `test_sql_columns_match_schema.py` to `test_pre_push_ci_parity.py::SOURCE_LEVEL_TESTS`** — both caught Sub-B regressions only at CI, burning 2 of 3 deploy iterations. Closes the privileged-endpoint-introduction class structurally.
3. **TaskCreate the 3 deferred items from commit body**: (i) `test_bulk_bearer_revoke_endpoint_pg.py` PG-integration (real-DB invariant-clears smoke), (ii) Sub-C frontend UI, (iii) fleet_cli wire-up to call the new endpoint instead of raw psql.
4. **Schema-analyzer docstring false-positive class** — add `# sql-skip` convention OR AST-aware skip of `"""..."""` blocks in `test_sql_columns_match_schema.py`. The c26731f1 fix is workaround; root cause is the analyzer's greedy regex.
5. **Carry forward Sub-A Gate B P1-3** (synthetic UPDATE gate) — not closed by Sub-B; still outstanding ratchet-0 baseline opportunity.

## Recommendation

APPROVE-WITH-FIXES. Implementation matches design v2 + Gate A re-check P0 closures (admin_transaction, FOR UPDATE, ::text[] cast, 404 unification, target_appliance_ids kwarg, BAA deferral with CE-row mutation acknowledged). The 3 CI-fix commits resolved real regressions cleanly. No P0; the rate-limit gap (P1-1) is the only semi-load-bearing item — recommend closing before Sub-C frontend UI ships so the operator-clickable surface inherits the cap.
