# Gate A Verdict (REDO) — Vault P0 Bundle Re-Implementation
Date: 2026-05-13
Reviewer: Gate A fork after 3-failure revert (Opus 4.7, fresh context, 4-lens adversarial)
Scope: re-implementation of the 4 Vault Phase C P0s reverted from `9fa26a54 → 44b02eb0 → 686a9b76`. Implementation only — NOT the env-flip.
Reference: `feedback_vault_phase_c_revert_2026_05_12.md`, `audit/coach-vault-phase-c-gate-a-2026-05-12.md`.

## Verdict: **BLOCK**

Three of the four lenses raise P0. The brief itself contains a factual error about iter-1's root cause (the iter-1 log clearly says `attempted relative import with no known parent package`, NOT "test fixture missing mig 177 column" — that was iter-2's distinct surface). The brief's "MUST update ALL `*_pg.py` fixtures" mitigation is incomplete: 6 fixtures contain `CREATE TABLE fleet_orders`, ZERO currently carry the `signing_method` column. If the re-implementation lands without updating ALL 6 in lockstep, iter-2's "column does not exist" error returns the moment the second pg-test file imports a call site that writes the column. The proposed Vault probe still has design ambiguity around lazy-init that the brief acknowledges but does not bind — recommending "either (a) eager init OR (b) HTTP probe OR (c) fast-path" is not a Gate A decision; it's a deferred decision. Pick one and gate it. **Re-fork after these closures.**

## Mitigations verified for prior 3 failure classes

- **Iter 1 root** (actual: relative-import inside try-block, surfaced as silent INSERT skip): **VERIFIED** in brief's Coach §1 + §5. The dual-import pattern matching `fleet_updates.py:19` is the correct fix. **But:** the brief itself misattributes iter-1 in the "Mandatory reading" §3 ("iter 1 — pg-tests, mis-diagnosed as ImportError"). It WAS the ImportError. The CI log is conclusive (`flywheel_promote.py:872 sync_promoted_rule INSERT failed ... attempted relative import with no known parent package`). Re-implementation MUST treat this as the iter-1 root, NOT secondary. The retro Gate B's iter-2 commit `44b02eb0` fixed exactly this — re-implementation must port that fix in the FIRST commit.
- **Iter 2 root** (actual: `column "signing_method" of relation "fleet_orders" does not exist` — surfaced AFTER the ImportError was fixed): **PARTIALLY VERIFIED.** Brief acknowledges fixture lockstep at Steve §4 but mitigation is "verify ALL `*_pg.py` fixtures" without enumerating which. Grep result: **6 fixtures** need the column, **none** currently have it. The 5 the brief recommends checking is wrong by 1 (`test_flywheel_spine_pg.py` also has the table at line 104). Re-implementation must update **6** fixtures in lockstep + a CI gate (`tests/test_pg_fixture_fleet_orders_column_parity.py`) that asserts every `*_pg.py` `CREATE TABLE fleet_orders` block contains `signing_method`. Without the CI gate this class WILL silently recur the next time a column is added to fleet_orders.
- **Iter 3 root** (actual: `/health` 120s timeout on container start; startup invariant blocking on Vault probe): **NOT VERIFIED.** Brief Steve §1 names `asyncio.wait_for(..., timeout=N)` and "recommend ≤5s" — this is necessary but insufficient. The actual iter-3 code at `startup_invariants.py:225-307` (commit `9fa26a54`) has TWO blocking calls: (a) `get_signing_backend()` which builds the singleton (Vault AppRole login = network roundtrip), (b) `vault_backend.key_version_and_pubkey()` (Transit metadata read). Wrapping ONLY (b) is insufficient — (a) is where the iter-3 hang likely originated because the singleton hadn't been built yet at first-call. The re-design MUST wrap the **entire** vault-touching block (lines 221-302 in the reverted code), not just the probe. **BLOCK pending explicit closure** on which call(s) the `wait_for` wraps.

---

## Findings by lens

### Steve (correctness) — 3 P0, 2 P1

**P0-STEVE-1 (BLOCK):** `asyncio.wait_for` scope is unspecified. The Vault-touching block has 3 network-touching operations: `get_signing_backend()` singleton-build (includes AppRole login), `vault_backend.key_version_and_pubkey()` (Transit read), and the two `conn.execute` INSERTs (local PgBouncer — bounded). Brief recommends "≤5s" but doesn't say wrap-scope. **Required closure:** wrap the ENTIRE block from `get_signing_backend()` through `key_version_and_pubkey()` in ONE `asyncio.wait_for(..., timeout=5.0)`. On timeout: log + return `InvariantResult("INV-SIGNING-BACKEND-VAULT", False, "vault probe timeout >5s — see runbook")`. Do NOT block startup.

**P0-STEVE-2 (BLOCK):** Brief Steve §2 ("Lazy-init traps") offers 3 options (a/b/c) and asks Gate A to verify "the re-design must either (a) eagerly initialize... (b) probe via HTTP... or (c) have a fast-path that doesn't touch Vault at all when `SIGNING_BACKEND=file`"). Option (c) is already in the reverted code (`if signing_backend in ("vault", "shadow")`). But the brief leaves (a) vs (b) unresolved. **Required closure:** pick (a). Eager-init the singleton at `lifespan` startup via a SEPARATE step before `check_all_invariants` runs, with its own `asyncio.wait_for(..., timeout=5.0)`. This (i) warms the singleton so the first customer-touching sign doesn't pay cold-start, (ii) makes the invariant's `get_signing_backend()` call a hot-path (no network), and (iii) lets the invariant focus on the comparison logic. Bind this in the design BEFORE code.

**P0-STEVE-3 (BLOCK):** Bootstrap-row INSERT on every drift triggers a side-effect. The reverted code at `startup_invariants.py:284-294` runs `INSERT ... ON CONFLICT ... DO UPDATE SET last_observed_at=NOW()` on EVERY drift detection, including the very container that drifted. This means a single rogue-rotation-then-recovery cycle leaves the rogue version persisted with `last_observed_at` regularly bumped — making forensic analysis harder. **Required closure:** on drift, INSERT the new version row ONCE (ON CONFLICT DO NOTHING — preserve the original `first_observed_at`), but do NOT bump `last_observed_at` on subsequent failed-drift observations. Move `last_observed_at` updates to the known-good path only. Pin in the design + ASSERTION_METADATA documentation.

**P1-STEVE-4:** Brief Carol §5 asks about `admin_audit_log` placement. The reverted mig 311 in commit `9fa26a54` does not show in the diff snippet — verify it has a post-COMMIT audit-log INSERT matching mig 310's shape. Carry as P1: confirm by reading the as-shipped mig 311 against mig 310 line-for-line before re-push.

**P1-STEVE-5:** Singleton-warm at lifespan adds a hard dependency on Vault reachability at startup. If Vault is unreachable, the warm step times out, the invariant reports `ok=False`, but the container starts. The first customer-touching sign call will then re-pay the cold-start tax AND fail the AppRole login. **Carry as TaskCreate:** document this trade-off in the runbook; consider a `VAULT_PROBE_REQUIRED` env flag that gates whether timeout = ok=False vs container-startup-block. Default: ok=False (non-blocking).

### Maya (HIPAA / §164.528) — 1 P0, 1 P1

**P0-MAYA-1 (BLOCK):** Bootstrap-mode behavior is a customer-visible disclosure surface. The reverted code at `startup_invariants.py:271-282` returns `ok=True` with `detail=BOOTSTRAP...` text containing a SQL UPDATE statement quoting the key_name + key_version. If this `detail` field surfaces into `/api/admin/substrate-health` (the brief doesn't say whether it does), the SQL fragment is operationally guidance that's appropriate for admin eyes — BUT a partner-tech-role staring at substrate health (per RT31 site-state class) sees this as a leak of internal mechanism. **Required closure:** verify `INV-SIGNING-BACKEND-VAULT` detail field is admin-only-readable, NOT partner-portal-exposed. Add a role-gate test (`test_inv_signing_backend_vault_detail_admin_only`) in the same commit.

**P1-MAYA-2:** No SECURITY_ADVISORY change is in scope for this re-implementation (per brief — this is infra, not cutover). Confirmed. The advisory remains a P0 for the eventual env-flip, NOT this bundle. Note for the record.

### Carol (DBA / migration safety) — 2 P0, 2 P1

**P0-CAROL-1 (BLOCK):** The brief mandates the CHECK constraint `(known_good=TRUE) ⇒ (approved_by NOT NULL AND approved_at NOT NULL)` — this matches the retro Gate B finding in commit `44b02eb0`. **But:** the brief specifies `CHECK (NOT known_good OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))`. This SQL is correct on writes BUT Postgres also allows `known_good` to be NULL (no NOT NULL constraint mentioned). With a 3-valued logic, `NOT NULL OR (...)` evaluates to NULL on `known_good=NULL`, which Postgres CHECK treats as PASSING (CHECK only rejects on FALSE). **Required closure:** add `known_good BOOLEAN NOT NULL DEFAULT FALSE` explicitly to the column definition. The brief does not specify this. Pin in mig 311.

**P0-CAROL-2 (BLOCK):** Brief Carol §4 says the substrate-invariant SQL "even at 10 orders/day, this scans <1 row — no index needed." This is correct for query performance but misses the partial-index drift class from the original Gate A's P1-CAROL-3: mig 177's existing `idx_fleet_orders_signing_method WHERE signing_method <> 'file'` is partial-on-'file'. After this P0 bundle ships, `signing_method='vault'` becomes the post-cutover common case; the partial index becomes effectively full-table. Trivial for 10 orders/day, but the assumption inversion is now baked-in by THIS commit's write path. **Required closure:** mig 311 must NOT alter the existing index, but the commit body MUST cite this as a known-deferred task (post-cutover Phase D). Add `tasks #50` if not already tracked.

**P1-CAROL-3:** `current_signing_method()` helper in the reverted `signing_backend.py:435-460` calls `get_signing_backend()` inside the function body. Every fleet_order INSERT now triggers this — if the singleton isn't warmed (Steve P0-2), the FIRST INSERT after a container restart pays the AppRole-login cost. With Steve P0-2 enforcing lifespan-warm, this becomes a hot-path call. Verify post-warm.

**P1-CAROL-4:** Mig 311's `admin_audit_log` INSERT placement: brief asks "should it be in the COMMIT block or after?" Mig 310's pattern (verified by `git show 7d96d7df -- mig 310` not run here but called out in commit body) places it AFTER COMMIT. Mig 311 should match. Carry as a CI gate verification in `test_migration_audit_log_after_commit.py` if such a gate exists; otherwise add to followup list.

### Coach (lockstep / banned shapes / consistency) — 3 P0, 2 P1

**P0-COACH-1 (BLOCK):** Brief Coach §1 mandates "module-level imports for `current_signing_method` in all 6 INSERT-callsite files (NOT inline try/except inside the INSERT function)." This is the iter-1 root-cause fix. **But:** the brief says "6 INSERT callsites" yet the commit `9fa26a54` body lists 6 sites (`fleet_updates.py × 2, flywheel_promote.py, cve_watch.py, sites.py × 2`) and the retro-fix commit `44b02eb0` says 4 needed the dual-import fix (the 5th, `fleet_updates.py:19`, was already module-level). **Required closure:** the re-implementation MUST use module-level imports at ALL 6 files — NOT dual-import-inside-try, NOT inline imports. The `44b02eb0` dual-import pattern was a TRIAGE fix to unblock CI; the correct shape is module-level only. The brief's Coach §1 wording ("NOT inline try/except inside the INSERT function. Use `fleet_updates.py:19` pattern as the gold standard") is right; verified `fleet_updates.py:15-18` uses `from .fleet import ...` at module-level WITHOUT try/except. Code review must confirm zero `try: from .signing_backend ... except ImportError: from signing_backend` blocks in the final diff. CI gate: `tests/test_no_dual_import_for_signing_method.py` (AST scan).

**P0-COACH-2 (BLOCK):** Brief Coach §4 ("the proposed implementation plan explicitly cites this Gate A + a planned Gate B") is necessary but not sufficient. **Required closure:** the FIRST commit of the re-implementation MUST cite verdict file path `audit/coach-vault-p0-bundle-gate-a-redo-2026-05-13.md` in the commit body, and the LAST commit MUST cite the as-yet-unwritten Gate B verdict file path. Failure to cite either = automatic Gate B BLOCK per Session 220 lock-in. Add a literal placeholder line `Gate A: audit/coach-vault-p0-bundle-gate-a-redo-2026-05-13.md (APPROVE-WITH-FIXES per below)` in the first commit body.

**P0-COACH-3 (BLOCK):** Brief Coach §5 lists banned shapes (`||-INTERVAL`, `jsonb_build_object` unannotated, etc.) but OMITS the most important one for this bundle: **bootstrap-row INSERT with naked `$2, $3` parameters** in the reverted `startup_invariants.py:262-269`. The INSERT uses `VALUES ($1, $2, $3, encode(decode($3, 'hex'), 'base64'))` — `$2` is `key_version` (int), `$3` is `pubkey_hex` (text). asyncpg's prepare phase under PgBouncer can flake on type inference of `$2::int` (the Session 219 `jsonb_build_object` class rule). **Required closure:** add explicit casts: `VALUES ($1::text, $2::int, $3::text, encode(decode($3::text, 'hex'), 'base64'))`. Pin in the design.

**P1-COACH-4:** Brief Coach §3 mandates `test_substrate_docs_present.py + test_assertion_metadata_complete.py both pass after additions`. Verified runbook file `substrate_runbooks/signing_backend_drifted_from_vault.md` was in commit `9fa26a54` (per deploy log). Re-implementation must port it byte-for-byte. Verify pass via local invocation of both gates BEFORE Gate B fork runs.

**P1-COACH-5:** Brief Coach §6 mentions HTTPS cert handling. `signing_backend.py:60` already has `VAULT_SKIP_VERIFY` env; default `"true"`. WG-internal self-signed Vault works today. Note for the record.

---

## Required pre-execution closures (P0) — 8 items

1. **(Steve-1)** Wrap `get_signing_backend()` + `key_version_and_pubkey()` in ONE `asyncio.wait_for(..., timeout=5.0)`. On TimeoutError: ok=False, detail="vault probe timeout >5s". DO NOT block startup.
2. **(Steve-2)** Add lifespan-warm step BEFORE `check_all_invariants` runs: eager `get_signing_backend()` with its own `asyncio.wait_for(..., timeout=5.0)`. Documents cold-start mitigation.
3. **(Steve-3)** Bootstrap-INSERT on drift uses `ON CONFLICT DO NOTHING` (NOT `DO UPDATE SET last_observed_at=NOW()`). Move `last_observed_at` bumps to known-good path only.
4. **(Maya-1)** Verify + assert via test that `INV-SIGNING-BACKEND-VAULT` detail field is admin-only-readable.
5. **(Carol-1)** `vault_signing_key_versions.known_good BOOLEAN NOT NULL DEFAULT FALSE` — explicit NOT NULL.
6. **(Coach-1)** ALL 6 INSERT callsites use MODULE-LEVEL import of `current_signing_method`. Zero dual-import-inside-try blocks. Add CI gate `tests/test_no_dual_import_for_signing_method.py` AST scan.
7. **(Coach-2)** First commit body cites THIS Gate A verdict path; last commit cites the planned Gate B path.
8. **(Coach-3)** Bootstrap-row INSERT params use explicit casts: `$1::text, $2::int, $3::text`. Pin Session 219 jsonb_build_object class rule extension.

**Additional cross-cutting closure (NEW — not in brief):**
- All 6 `*_pg.py` fixtures containing `CREATE TABLE fleet_orders` get `signing_method TEXT NOT NULL DEFAULT 'file'` added IN THE SAME COMMIT as the write-path change. Files: `test_startup_invariants_pg.py:48`, `test_privileged_chain_adversarial_pg.py:55`, `test_privileged_chain_triggers_pg.py:56`, `test_fleet_intelligence_api_pg.py:91`, `test_promotion_rollout_pg.py:78`, `test_flywheel_spine_pg.py:104`. Add CI gate `tests/test_pg_fixture_fleet_orders_column_parity.py` to lock the class.

## Carry-as-followup (P1) — 5 items

- P1-Steve-4: verify mig 311 admin_audit_log placement matches mig 310 post-COMMIT shape.
- P1-Steve-5: document VAULT_PROBE_REQUIRED env trade-off in runbook.
- P1-Carol-3: verify post-lifespan-warm that `current_signing_method()` is a hot-path call.
- P1-Carol-4: CI gate for migration audit-log-after-COMMIT pattern.
- P1-Coach-4: byte-for-byte port of `substrate_runbooks/signing_backend_drifted_from_vault.md`.

## Recommended implementation order + commit boundaries

**Commit 1** (write-path infrastructure — does NOT need Vault reachable):
- 6 `*_pg.py` fixtures: add `signing_method TEXT NOT NULL DEFAULT 'file'` to `CREATE TABLE fleet_orders` blocks.
- `signing_backend.py`: add `current_signing_method()` helper.
- 6 INSERT callsites: module-level import + `signing_method` in INSERT VALUES (`fleet_updates.py` already module-level; others need import added at module top).
- CI gates: `test_no_dual_import_for_signing_method.py` + `test_pg_fixture_fleet_orders_column_parity.py`.
- Body cites this Gate A.
- Local sweep: 241+ pass. pg-tests pass.

**Commit 2** (DB substrate + invariants):
- Mig 311 with explicit `known_good BOOLEAN NOT NULL DEFAULT FALSE`, `CHECK (NOT known_good OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))`, append-only trigger, post-COMMIT audit-log INSERT.
- `startup_invariants.py` INV-SIGNING-BACKEND-VAULT: `asyncio.wait_for(timeout=5.0)` around entire vault block, bootstrap ON CONFLICT DO NOTHING, explicit `$N::type` casts.
- `main.py` lifespan: add eager `get_signing_backend()` warm step with its own `asyncio.wait_for(timeout=5.0)` BEFORE invariants check.
- `assertions.py`: substrate invariant `signing_backend_drifted_from_vault` (sev2) + ASSERTION_METADATA entry.
- `substrate_runbooks/signing_backend_drifted_from_vault.md`: port from commit `9fa26a54`.
- Admin-only role-gate test for INV detail field.
- Local sweep + pg-tests pass.

**Both commits land in the SAME push** so CI sees them together (avoid iter-2's "fix-forward without root-cause" trap).

## Gate B brief for the parent session

After implementation lands locally and BEFORE push:

1. **Fork a fresh-context Gate B** with the same 4 lenses (Steve / Maya / Carol / Coach).
2. **Mandatory verification artifacts** the Gate B fork MUST cite in its verdict:
   - `grep -c "signing_method" mcp-server/central-command/backend/tests/*_pg.py` returns ≥6 (one match per fixture file).
   - `grep -rn "try:\s*from \.signing_backend import current_signing_method" mcp-server/central-command/backend/` returns ZERO hits.
   - `grep -n "asyncio.wait_for" mcp-server/central-command/backend/startup_invariants.py` returns at least 1 hit inside the INV-SIGNING-BACKEND-VAULT block.
   - `bash .githooks/full-test-sweep.sh` runs (NOT just curated sweep) — cite pass/fail count. Session 220 lock-in: Gate B MUST run full sweep, not just review diff.
   - `python3 -m pytest mcp-server/central-command/backend/tests/test_promotion_rollout_pg.py -v` passes locally if PG_TEST_URL is set (skip-with-note otherwise; cite that pg-test verification will only happen in CI and that's the iter-2 class risk).
   - `grep -nE "VALUES \(\\\$1[^:]" mcp-server/central-command/backend/startup_invariants.py` returns ZERO hits in the vault block (all params cast).
3. **Three failure-class proof obligations** — Gate B verdict MUST explicitly state for each:
   - Iter-1 (relative-import): "VERIFIED — all 6 callsites use module-level import; CI gate `test_no_dual_import_for_signing_method.py` enforces."
   - Iter-2 (fixture column drift): "VERIFIED — all 6 `*_pg.py` fixtures contain `signing_method` column; CI gate `test_pg_fixture_fleet_orders_column_parity.py` enforces."
   - Iter-3 (startup hang): "VERIFIED — `asyncio.wait_for(timeout=5.0)` wraps the entire Vault-touching block at lifespan warm AND invariant probe; on timeout, container starts with INV-SIGNING-BACKEND-VAULT ok=False."
4. **If ANY of the 3 proofs reads "partial", "best-effort", or "TODO"** — Gate B BLOCKS. No fix-forward without re-Gate-A.
5. **Commit body in the final commit MUST cite both gate verdicts.** Without both citations, Gate B BLOCKS regardless of substantive findings.
6. **Post-merge runtime verification** (per CLAUDE.md Session 215 #77): `curl https://msp.osiriscare.io/api/version` → assert `runtime_sha == disk_sha == merged commit SHA` BEFORE claiming shipped. Then `curl /health` → assert `status=ok`. Then `docker compose exec mcp-server python -c "from startup_invariants import check_all_invariants; ..."` to print live INV-SIGNING-BACKEND-VAULT state.

---

**End of verdict.** Re-fork Gate A only AFTER the 8 P0 closures are bound in the design + at least 1 commit-shape proof (e.g. branch + `git log -p` of the planned commits in a worktree). Approval-without-fixes is unreachable for this bundle given the prior failure cascade.
