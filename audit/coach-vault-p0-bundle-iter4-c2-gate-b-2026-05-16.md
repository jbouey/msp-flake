# Gate B verdict — Vault P0 iter-4 Commit 2 (8014979d)
Date: 2026-05-16
Reviewer: fork-based 7-lens (general-purpose subagent, fresh context)
Verdict: **APPROVE-WITH-FIXES** (P0 closures all real; 2 P1 follow-ups required)

## P0 closure verification

- **P0-A (fixture/prod parity):** CONFIRMED.
  - `vault_signing_key_versions` present in `tests/fixtures/schema/prod_columns.json` line 5449 (10 cols: id, key_name, key_version, pubkey_hex, pubkey_b64, first_observed_at, last_observed_at, known_good, approved_by, approved_at).
  - Present in `prod_column_types.json` line 5449 with matching types (bigint, text, integer, text, text, timestamptz×3, boolean, text, timestamptz).
  - `migrations/311_vault_signing_key_versions.sql` shape matches fixture exactly. UNIQUE (key_name, key_version) + partial idx on known_good + CHECK constraint all present.

- **P0-B (asyncio.to_thread wrap):** CONFIRMED.
  - `startup_invariants.py:309-312` outer `asyncio.wait_for(_check_signing_backend_vault(conn), timeout=VAULT_PROBE_OUTER_TIMEOUT_S)`.
  - `_check_signing_backend_vault:364` wraps `get_signing_backend()` in `asyncio.to_thread`.
  - `_check_signing_backend_vault:411` wraps the `_probe_sync` closure (which contains `primary._login_if_needed()` + `primary._client.get(...)`) in `asyncio.to_thread`. All sync surfaces covered.

- **P0-C (inner socket timeout):** CONFIRMED.
  - `VAULT_PROBE_OUTER_TIMEOUT_S = 5.0` (line 73), `VAULT_PROBE_INNER_TIMEOUT_S = 4.0` (line 74). 1.0s buffer.
  - `_probe_sync` line ~402: `primary._client.get(..., timeout=VAULT_PROBE_INNER_TIMEOUT_S)` — inner constant actually passed.

- **P0-D (silent swallow eliminated):** CONFIRMED.
  - `signing_backend.py:_FALLBACK_COUNT` module global declared.
  - `current_signing_method` `except Exception as e:` branch: increments `_FALLBACK_COUNT`, sets `_FALLBACK_LAST_REASON`, calls `logger.error("current_signing_method_fallback", extra={...}, exc_info=True)`.
  - Accessors `get_signing_backend_fallback_count()` + `get_signing_backend_fallback_last_reason()` exported.

- **P0-E (ledger row removed):** CONFIRMED.
  - `grep -E '^\| 311' RESERVED_MIGRATIONS.md` returns no matches (exit 1).
  - `migrations/311_vault_signing_key_versions.sql` exists on disk. Ledger lifecycle clean.

## Test sweep
- `bash .githooks/full-test-sweep.sh` → **273 passed, 0 skipped, exit 0**. Matches author's 273/273 claim.

## iter-1/2/3 root-cause class re-check

- **Class 1 (pg-test fixture drift):** PARTIAL CONCERN, P1.
  - 6 pg-fixture files create `fleet_orders` (incl. `test_startup_invariants_pg.py:48`); NONE create `vault_signing_key_versions`. However the INV runs and reaches the DB write path only when `SIGNING_BACKEND != 'file' OR VAULT_ADDR != ""` AND the backend's `_primary.name == 'vault'`. CI env almost certainly has neither set, so the INV returns at line 354 (skip branch) before touching the table.
  - **Real residual risk:** the pg test imports `import startup_invariants` (top-level), but `_check_signing_backend_vault` line 362 uses `from .signing_backend import` (RELATIVE). If a future CI/dev sets `SIGNING_BACKEND=vault` while exercising `test_startup_invariants_pg.py`, the relative import raises `ImportError` → caught by outer `except Exception` at line 323 → invariant returns ok=False with a non-obvious detail. Diagnostic, not fatal — but `test_all_invariants_green_when_fully_set_up` would then FAIL with no fixture-level remediation possible. **P1: add `vault_signing_key_versions` to pg fixture schema + pin env to file/no-VAULT in conftest.**
  - The full-test-sweep excludes `*_pg.py` so this won't surface in pre-push; CI server-side runs the pg suite.

- **Class 2 (startup INV timeout):** RESOLVED.
  - Outer 5.0s wait_for + to_thread wrap + inner httpx 4.0s timeout. Worst case at TCP-SYN hang: kernel keeps `_login_if_needed`'s socket stuck, httpx hits 4.0s socket timeout → raises → caught by inner try at line 421 → returns ok=False in ~4s. If inner timeout itself were swallowed, outer asyncio.wait_for cancels the to_thread coroutine at 5.0s.
  - One residual concern: `_login_if_needed` may have its own internal httpx client whose timeout is NOT the per-request override (line 401's timeout applies only to the `.get()` call). If AppRole login at line 397 hangs at TCP layer with httpx's default 5s+ timeout, the entire to_thread coroutine still blocks until httpx returns, but outer wait_for cancels the asyncio await at 5.0s — the thread itself may leak briefly. Acceptable: lifespan continues, ok=False set. Not P0; document as known thread-leak class.
  - Lifespan eager-warm DEFERRED per commit body. The current asyncio.wait_for + to_thread does bound startup. Safe to defer.

- **Class 3 (fix-forward without re-Gate-A):** RESOLVED.
  - Commit body cites all three Gate A docs (redo-2 2026-05-13, iter4 2026-05-16) + `.agent/plans/vault-p0-bundle-redesign-2026-05-13.md`. This commit is binding to the iter-4 design, not seat-of-pants.

## Counsel's 7 Rules

- **Rule 1 (canonical metric):** PASS. INV detail surfaces only via operator `/admin/substrate-health`. No customer-facing metric.
- **Rule 3 (privileged chain):** WEAK — see P1 below. Approval (setting `known_good=TRUE` + `approved_by` + `approved_at`) is direct DB UPDATE per current design. The mig 311 CHECK constraint enforces approval-shape but NOT chain-of-custody. An admin with DB access could approve any observed key. Not a privileged fleet_order, no attested chain. For Vault-key-pinning this is arguably the right call (no appliance involvement) but the operator approval action is functionally equivalent to "trusting this Vault rotation" and deserves at least an `admin_audit_log` row — currently nothing in this commit creates that audit row. **P1.**
- **Rule 7 (no unauth context):** PASS. `/health` line 2642-2650 returns `{"status": "ok"}` only. INV detail wired only into `/admin/substrate-health` path (commit body claim consistent with main.py:1640 enforce-only wiring; CI gate deferred but current behavior is safe by absence of public surface).

## Per-lens findings

- **Steve (auth/security):** Substrate invariant correctly compares observed-vs-configured. Shadow-mode carve-out present and correct. Fallback counter closes the false-negative blind spot. CHECK constraint prevents accidental approval without attribution. Concerned about lack of admin endpoint for approval (no UPDATE handler shipped in this commit — operators must hand-craft SQL until follow-up).
- **Maya (compliance/auditor):** INV result detail is operator-only, no PHI/customer leak. Audit gap: known_good approval has no audit-log writer.
- **Carol (DB/perf):** Mig 311 well-formed: BIGSERIAL PK, NOT NULL where required, partial index on `known_good = TRUE` is correct (small index). `IF NOT EXISTS` makes mig idempotent. Bootstrap INSERT uses ON CONFLICT DO NOTHING per Gate A P0 #3 — correct semantics for first-observed telemetry.
- **Coach (process):** TWO-GATE protocol honored — Gate A iter-4 BLOCK with 5 P0s, all 5 closed structurally + verified above, sentinel tests pin each closure. Full sweep 273/273 ran + cited. Commit body cites Gate A; this Gate B verdict file exists at the canonical path.
- **Auditor:** Mig file content matches fixture; substrate runbook present at `substrate_runbooks/signing_backend_drifted_from_vault.md`. Determinism contract not at risk (operator-only data).
- **PM:** Two deferred items (lifespan eager-warm + admin-only `/health` CI gate) clearly named in commit body — acceptable carry-forward. **Both should be TaskCreate'd if not already.**
- **Counsel:** Rule 3 weak-but-acceptable for this iteration; Rule 7 clean; Rule 6 N/A.

## Findings

### P0 (BLOCK)
- None.

### P1 (MUST-fix-or-task)
- **P1-a:** Add `vault_signing_key_versions` CREATE TABLE to `test_startup_invariants_pg.py` fixture (and any other pg fixture that may exercise the INV path with `SIGNING_BACKEND=vault`). Pin `monkeypatch.setenv("SIGNING_BACKEND", "file")` + delenv `VAULT_ADDR` in the conn fixture so the skip branch is deterministic, OR add the table so the probe-then-write path is exercised. Without this, a future CI env change re-introduces a Class-1-equivalent silent fixture-drift class.
- **P1-b:** Add admin approval endpoint + `admin_audit_log` row writer for the `known_good`/`approved_by` UPDATE. Today operators have no shipped surface to mark a row approved — they must hand-craft SQL, which then leaves no audit trail. Counsel Rule 3 weak-spot. TaskCreate as follow-up commit before any Vault-primary cutover.

### P2 (consider)
- **P2-a:** `_login_if_needed` thread-leak class — if AppRole login hangs at TCP layer beyond 5.0s the outer wait_for cancels the asyncio await but the underlying thread may persist briefly. Acceptable for now; consider an httpx-level client-wide default timeout in `signing_backend.py` to bound this independently.
- **P2-b:** Commit-body deferred items (lifespan eager-warm + INV-detail admin-only CI gate) should be tracked as named TaskCreate items if not already, per the TWO-GATE rule that "P1 from EITHER gate MUST be closed OR carried as named TaskCreate followup items in the same commit."

## Final
**APPROVE-WITH-FIXES.** All 5 Gate A P0s closed structurally and verified; sweep 273/273 green; iter-1/2/3 root-cause classes addressed (Class 2 fully, Class 3 fully, Class 1 mitigated via skip branch but pg-fixture parity needs a follow-up). Two P1 follow-ups (pg fixture + audit-row for known_good approval) MUST land as TaskCreate items in the same commit chain. Two deferred items from commit body should also be TaskCreate'd. No P0 blockers — ship.

Verdict file: `audit/coach-vault-p0-bundle-iter4-c2-gate-b-2026-05-16.md`
