# Vault P0 Bundle — Design Binding for the 8 P0s

**Date:** 2026-05-13
**Gate A verdict driving this doc:** `audit/coach-vault-p0-bundle-gate-a-redo-2026-05-13.md` — BLOCK with 8 P0s + cross-cutting fixture-parity
**Predecessor:** Reverted chain `9fa26a54..686a9b76`; case study at `memory/feedback_vault_phase_c_revert_2026_05_12.md`

Each P0 is bound to concrete code shape, file path, and the prior-failure class it mitigates. Re-fork Gate A reads this doc + grades whether the bindings are sufficient.

## Commit boundary

Per Gate A: **2 commits in 1 push**, in this order:

- **Commit 1** — write-path, no Vault dependency (safe to ship even if Vault is offline):
  - 6 `*_pg.py` fixture column adds (column-drift mitigation, lockstep)
  - `signing_backend.py::current_signing_method()` helper
  - 6 INSERT-callsite updates (module-level imports — NOT try/except)
  - 2 new CI gates (`test_pg_fixture_fleet_orders_column_parity.py`, `test_no_dual_import_for_signing_method.py`)
  - No DB migration, no substrate invariant.
  - Validates write-path independently of Vault state.

- **Commit 2** — DB + invariants (depends on Commit 1's write path being live):
  - `migrations/311_vault_signing_key_versions.sql` (table + triggers + CHECK)
  - `startup_invariants.py::INV-SIGNING-BACKEND-VAULT` with `asyncio.wait_for` + lifespan-warm
  - `assertions.py::signing_backend_drifted_from_vault` substrate invariant + ASSERTION_METADATA + runbook
  - Lifespan eager-warm step in `mcp-server/main.py`

Both commits go in **one push** so CI evaluates them as a unit. Avoids the iter-2 fix-forward trap.

## P0 closure bindings (Gate A's 8)

### P0 #1 — `asyncio.wait_for(timeout=5.0)` wraps the ENTIRE vault block

**File:** `startup_invariants.py`
**Shape:** the entire Vault-probe section inside `INV-SIGNING-BACKEND-VAULT` is one coroutine wrapped in a single outer `await asyncio.wait_for(_vault_probe(conn), timeout=5.0)`. Timeout = 5.0s. On `asyncio.TimeoutError`: log + return `ok=False detail="vault probe exceeded 5s — startup proceeding non-blocking"`.

**Why 5.0s, not lower:** WireGuard tunnel handshake + Vault TLS handshake + Transit read. Internal latency is ~10-50ms when healthy; 5s allows a 100x safety margin without making startup feel slow.

**Critical:** `get_signing_backend()` itself may block on AppRole login. Steve P0 #1 explicitly said "wrapping only the second [call] leaves the first as the actual hang surface." → the timeout MUST cover the singleton-build AND the key-version read AND the public-key read, all in one coroutine.

**Mitigates:** iter-3 root cause (`/health` timeout at 120s because INV blocked startup).

### P0 #2 — Lifespan eager-warm step BEFORE invariants check

**File:** `mcp-server/main.py` (lifespan startup section)
**Shape:** before `check_all_invariants()` is called, add an eager warm of the signing backend with its own timeout:

```python
# Vault eager-warm — bound the lazy-init hang explicitly so the
# subsequent INV-SIGNING-BACKEND-VAULT probe has a known-state singleton.
try:
    from dashboard_api.signing_backend import get_signing_backend
    await asyncio.wait_for(
        asyncio.to_thread(get_signing_backend),
        timeout=5.0,
    )
except (asyncio.TimeoutError, Exception) as e:
    logger.error("vault_eager_warm_failed", error=str(e), exc_info=True)
    # Container starts anyway; INV will fire ok=False detail
```

`get_signing_backend` is currently synchronous — wrap in `asyncio.to_thread`. The eager warm:
- runs ONCE per container lifecycle
- bounded by 5s timeout
- failure is non-fatal (matches startup_invariants.py "CREDIBILITY event, not availability event")

**Mitigates:** iter-3 root cause (singleton-build was the actual hang).

### P0 #3 — Bootstrap-INSERT uses `ON CONFLICT DO NOTHING`, not DO UPDATE

**File:** `startup_invariants.py` inside `_check_signing_backend_vault`
**Shape:** when no `known_good=TRUE` row exists, bootstrap-INSERT the observed (key_name, key_version) with `known_good=FALSE`. **NEVER UPDATE on conflict.** If a row already exists for that (key_name, key_version), the operator has either approved it or is in the middle of approving — we must not touch the row.

```sql
INSERT INTO vault_signing_key_versions
    (key_name, key_version, pubkey_hex, pubkey_b64)
VALUES
    ($1::text, $2::int, $3::text, encode(decode($3::text, 'hex'), 'base64'))
ON CONFLICT (key_name, key_version) DO NOTHING
```

**Mitigates:** Steve P0 #3 — prior `DO UPDATE SET last_observed_at = NOW()` was a side-effect that mutated `last_observed_at` on every restart, masking real "this version observed for the first time today" signals.

### P0 #4 — Test that INV detail is admin-only-readable

**File:** new `tests/test_inv_signing_backend_vault_admin_only.py`
**Shape:** unit test verifying `InvariantResult.detail` returned by the INV is consumed only via the admin-context `/api/admin/substrate-health` endpoint, not the public `/health` endpoint. Test reads `main.py` AST for the `/health` handler and asserts it does NOT call `check_all_invariants()` or expose `InvariantResult.detail`.

**Mitigates:** Maya P0 — operational detail (Vault version, key fingerprint) in INV detail field could leak via a careless health endpoint. Make the constraint a CI gate.

### P0 #5 — `known_good BOOLEAN NOT NULL DEFAULT FALSE` explicit in mig 311

**File:** `migrations/311_vault_signing_key_versions.sql`
**Shape:** the column declaration is `known_good BOOLEAN NOT NULL DEFAULT FALSE` (already in the reverted version, re-validate). The CHECK constraint `(NOT known_good OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))` requires `known_good` to be NOT NULL because `NOT NULL` is in the `NOT (known_good)` expression — a `NULL known_good` would make the CHECK evaluate to UNKNOWN which is treated as true. Explicit NOT NULL prevents this gap.

**Mitigates:** Carol P0 #1 — even though it was implicit in the reverted version, Gate A wants it explicit so a future ALTER doesn't drop it.

### P0 #6 — All 6 callsites MODULE-LEVEL import + CI gate

**Files:**
- `fleet_updates.py:19` (already module-level — gold pattern)
- `flywheel_promote.py` (move from inside function to top-of-file)
- `cve_watch.py` (same)
- `sites.py` (same — appears twice, but ONE module-level import suffices)
- New `tests/test_no_dual_import_for_signing_method.py` — AST gate: any function-body `from ... signing_backend ... import current_signing_method` fails CI

**Shape of CI gate:**
```python
import ast
import pathlib

_BACKEND = pathlib.Path(__file__).resolve().parent.parent

def test_no_function_body_import_of_current_signing_method():
    violations = []
    for py_path in _BACKEND.rglob("*.py"):
        if "tests" in py_path.parts or "migrations" in py_path.parts:
            continue
        try:
            tree = ast.parse(py_path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for inner in ast.walk(node):
                    if isinstance(inner, ast.ImportFrom):
                        if inner.module and "signing_backend" in inner.module:
                            for alias in inner.names:
                                if alias.name == "current_signing_method":
                                    violations.append(
                                        f"{py_path.name}:{inner.lineno} "
                                        f"function-body import of current_signing_method"
                                    )
    assert not violations, "\n".join(violations)
```

**Mitigates:** iter-1 root cause (try/except hides ImportError → silent INSERT skip). Module-level imports fail fast at module load, before any test fixture runs.

### P0 #7 — Commit body cites this Gate A verdict path

**File:** commit messages on both Commit 1 + Commit 2
**Shape:** each commit body explicitly cites:
- `audit/coach-vault-p0-bundle-gate-a-redo-2026-05-13.md` (Gate A — this re-fork)
- `audit/coach-vault-p0-bundle-gate-b-redo-2026-05-13.md` (Gate B — to be written before commit)
- `memory/feedback_vault_phase_c_revert_2026_05_12.md` (revert case study)
- `.agent/plans/vault-p0-bundle-redesign-2026-05-13.md` (THIS doc — the design binding)

**Mitigates:** Coach P0 #2 — no fix-forward without per-iteration citation. The audit trail is the meta-defense.

### P0 #8 — Explicit `$N::type` casts in bootstrap-INSERT params

**File:** `startup_invariants.py` Vault INV
**Shape:** every `$N` in the bootstrap INSERT carries an explicit `::text` / `::int` cast:

```sql
INSERT INTO vault_signing_key_versions
    (key_name, key_version, pubkey_hex, pubkey_b64)
VALUES
    ($1::text, $2::int, $3::text, encode(decode($3::text, 'hex'), 'base64'))
ON CONFLICT (key_name, key_version) DO NOTHING
```

**Mitigates:** CLAUDE.md's `jsonb_build_object($N, ...)` rule + asyncpg prepare-phase type inference under PgBouncer. Same class as the journal_api.py:178 closure.

## Cross-cutting: fixture-parity + CI gate

### 6 `*_pg.py` fixtures get the column

**Files (verified via grep):**
- `tests/test_startup_invariants_pg.py:48`
- `tests/test_privileged_chain_adversarial_pg.py:55`
- `tests/test_privileged_chain_triggers_pg.py:56`
- `tests/test_fleet_intelligence_api_pg.py:91`
- `tests/test_promotion_rollout_pg.py:78`
- `tests/test_flywheel_spine_pg.py:104`

Each fixture's `CREATE TABLE fleet_orders` adds:
```sql
signing_method TEXT NOT NULL DEFAULT 'file'
```

### New CI gate `tests/test_pg_fixture_fleet_orders_column_parity.py`

**Shape:** AST-grep across all `*_pg.py` files for `CREATE TABLE fleet_orders` blocks; assert each contains `signing_method TEXT NOT NULL DEFAULT 'file'`. Fail with a fix-up suggestion.

```python
import pathlib, re
_TESTS = pathlib.Path(__file__).resolve().parent

def test_every_fleet_orders_fixture_has_signing_method():
    missing = []
    for pg in sorted(_TESTS.glob("*_pg.py")):
        src = pg.read_text()
        for m in re.finditer(r"CREATE TABLE fleet_orders\b[^;]+;", src, re.DOTALL):
            block = m.group(0)
            if "signing_method" not in block:
                missing.append(f"{pg.name} — CREATE TABLE fleet_orders missing signing_method column")
    assert not missing, "\n".join(missing) + (
        "\n\nFix: add `signing_method TEXT NOT NULL DEFAULT 'file'` to the column list."
    )
```

**Mitigates:** iters 1+2 root cause class — schema-drift between test fixtures and prod migrations.

## Gate B brief (what the next fork must verify)

After Commit 1 + Commit 2 are staged but BEFORE push:

1. Run `bash .githooks/full-test-sweep.sh` — cite exact count. Compare against current baseline (241 + new tests).
2. `grep -c "signing_method" mcp-server/central-command/backend/tests/*_pg.py` — must return ≥6.
3. `grep -rn "try:\s*from \.signing_backend" mcp-server/central-command/backend/` — must return 0.
4. `grep -n "asyncio.wait_for" mcp-server/central-command/backend/startup_invariants.py` — must return ≥1 line inside the INV block.
5. `grep -n "asyncio.wait_for\|asyncio.to_thread.*get_signing_backend" mcp-server/main.py` — must return the eager-warm.
6. `grep -n "ON CONFLICT (key_name, key_version) DO NOTHING" mcp-server/central-command/backend/startup_invariants.py` — must match.
7. `grep -n "::text\|::int" mcp-server/central-command/backend/startup_invariants.py` — bootstrap-INSERT casts present.
8. Verify both commit bodies cite the 4 audit paths.
9. Run the 2 new CI gates standalone — expect 0 violations.

**Verdict file:** `audit/coach-vault-p0-bundle-gate-b-redo-2026-05-13.md`. APPROVE only if all 9 verifications pass.

## Lessons-applied checklist (from revert case study)

- [✓] Fixture-vs-prod schema drift: 6 fixtures updated + CI gate
- [✓] Startup-invariant timeout: `asyncio.wait_for(5s)` on full Vault block + eager-warm
- [✓] Fix-forward discipline: 2-commit single-push, Gate B mandatory before push
- [✓] Local sweep blind spots: Gate B brief lists 9 specific grep verifications beyond the sweep
