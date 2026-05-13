# Gate B retro-2 — P0 fixes verification
Date: 2026-05-12

## Verdict: APPROVE

## Sweep: 241 passed, 0 skipped (need backend deps)

## P0 Steve #1 — relative-import fix
- [✓] All 4 in-scope callsites match the dual-import pattern from `startup_invariants.py:225-227` byte-for-byte:
  - `flywheel_promote.py:853-856` — try `from .signing_backend import current_signing_method` / except ImportError → `from signing_backend import current_signing_method  # type: ignore`
  - `sites.py:2200-2203` — same shape
  - `sites.py:3152-3155` — same shape
  - `cve_watch.py:366-369` — same shape
- [✓] No drift — all four use `ImportError` (not bare except), `# type: ignore` comment present, relative-form attempted first.
- [✓] No missed callsites in the FUNCTION-LEVEL class. The grep surfaced one additional hit (`fleet_updates.py:19`) but that is a MODULE-LEVEL import symmetric with `from .fleet`, `from .auth`, `from .order_signing`, `from .tenant_middleware` (all bare relative imports on adjacent lines). Module-level package imports load via the same package-context as the rest of fleet_updates and are NOT in the same dual-context class as the function-level re-imports triggered by deferred call paths. The prior retro brief explicitly scoped this fix to the 4 function-level callsites — `fleet_updates.py:19` was not in scope and is not a defect.
- Reference imports (e.g. `partner_portfolio_attestation.py:231-233`, `shared.py:330-332`, `appliance_relocation.py:86-88`) all use the same dual-import pattern for function-level callsites — design parity confirmed.

## P0 Steve #2 — approval-pair CHECK
- [✓] Syntactically valid. CHECK constraint `vault_signing_key_versions_approval_pair` declared inside the `CREATE TABLE IF NOT EXISTS vault_signing_key_versions` statement (lines 51-53) — applies on fresh table creation and is enforced by Postgres on every INSERT/UPDATE.
- [✓] Correctly enforces shape via implication `NOT known_good OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)`:
  - `known_good = FALSE`, both approved_* NULL → TRUE OR ... = TRUE ✓ (allowed)
  - `known_good = FALSE`, approved_by set, approved_at NULL → TRUE OR ... = TRUE ✓ (allowed — pre-approval staging)
  - `known_good = TRUE`, both NULL → FALSE OR (FALSE AND FALSE) = FALSE ✗ (blocked)
  - `known_good = TRUE`, approved_by set, approved_at NULL → FALSE OR (TRUE AND FALSE) = FALSE ✗ (blocked)
  - `known_good = TRUE`, both set → FALSE OR (TRUE AND TRUE) = TRUE ✓ (allowed)
- Closes the UPDATE-privilege fast-track described in the inline comment (lines 45-50). Complements the immutable-update trigger (lines 59-64) by adding cross-column shape enforcement that the per-column trigger cannot express.

## Coach sweep
- 241 passed, 0 skipped (need backend deps). Same green count as the post-revert baseline — no new test failures introduced by the fixes.
- No banned shapes detected in the diff: no `except Exception: pass` on DB writes, no new ad-hoc `.format()` templates, no raw `db.execute()` bypassing `execute_with_retry`, no NOW() in partial-index predicates, no Python f-string subjects in email helpers, no `datetime.now()` in kit-internal paths.

## Allowed-to-ship
yes
