# Gate A — #126 PG-fixture integration test for `fleet_cli.cmd_create` fan-out
Date: 2026-05-16
Reviewer: 7-lens (Steve / Maya / Carol / Coach / Auditor / PM / Counsel)
Verdict: **APPROVE-WITH-FIXES** (ship the narrowed scope below)

## Scope decision

Driving the full `cmd_create` is the wrong axis: it needs Ed25519 signing key, `privileged_access_attestation` module (writes to chained `compliance_bundles` + `admin_audit_log` rows), interactive nonce prompt, `sys.path.insert("/app")`, etc. Stub-fest erodes the very thing the test is supposed to anchor (real SQL+trigger behavior). **Pragmatic axis: extract the SQL + trigger contracts the fan-out depends on, exercise them directly against PREREQ_SCHEMA.** Match the `test_chain_tamper_detector_pg.py` pattern.

## Recommended ship scope (4 tests, ~150 LOC)

**SHIP:**
- **A** Enumeration SQL — `WHERE site_id=$1 AND deleted_at IS NULL ORDER BY appliance_id`. Seed 5 rows (1 soft-deleted) → expect 4 in stable order. *Catches: column rename in `site_appliances`, accidental ORDER drop, deleted_at predicate drop.*
- **C** Mig 175 trigger ALLOWS N orders citing 1 bundle. Write 1 `privileged_access` bundle for site-X, INSERT 3 fleet_orders each with distinct `target_appliance_id` in params, all citing the same `attestation_bundle_id`. Assert all 3 land. *Locks the 1-bundle:N-orders shape that the fan-out exploits — its disappearance would silently break privileged fan-out.*
- **D** Mig 175 trigger REJECTS a fleet_order citing a non-existent bundle_id. Assert `asyncpg.RaiseError` with `PRIVILEGED_CHAIN_VIOLATION` substring. *Pins the chain-of-custody guarantee at the boundary; canonical negative control.*
- **F** Mig 175 trigger REJECTS a fleet_order whose `parameters.site_id` differs from the bundle's site_id (cross-site attestation re-use). *Pins the site-binding half of the trigger — most subtle weakening path.*

**DEFER (lower marginal value):**
- **B** cross-link UPDATE jsonb shape — already covered by 2 AST gates (`test_cross_link_uses_aggregate_array_not_singular`); PG test would just re-run `jsonb_build_object` against PG itself which is exercising Postgres, not us.
- **E** empty-site error path — pure Python sys.exit; unit-testable without PG.
- **G** UPDATE idempotency — `details || jsonb_build_object(...)` is documented Postgres behavior; out of scope.
- **H** per-iteration distinct `target_appliance_id` — pure Python (`per_params = dict(params)`), unit-test class not PG-fixture class.

## PREREQ_SCHEMA

One string, single `DROP TABLE IF EXISTS … CASCADE` block at top per the #77 rule, single `CREATE` block, single trigger install:

```
DROP TRIGGER IF EXISTS trg_enforce_privileged_chain ON fleet_orders;
DROP FUNCTION IF EXISTS enforce_privileged_order_attestation() CASCADE;
DROP TABLE IF EXISTS fleet_orders CASCADE;
DROP TABLE IF EXISTS compliance_bundles CASCADE;
DROP TABLE IF EXISTS site_appliances CASCADE;

CREATE TABLE site_appliances (
    appliance_id TEXT PRIMARY KEY,
    site_id      TEXT NOT NULL,
    mac_address  TEXT, hostname TEXT, status TEXT,
    last_checkin TIMESTAMPTZ,
    deleted_at   TIMESTAMPTZ
);
CREATE TABLE compliance_bundles (
    bundle_id TEXT PRIMARY KEY, site_id TEXT NOT NULL,
    check_type TEXT NOT NULL DEFAULT 'compliance'
);
CREATE TABLE fleet_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_type TEXT NOT NULL, parameters JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ, created_by TEXT,
    nonce TEXT, signature TEXT, signed_payload TEXT,
    skip_version TEXT
);
-- Then exec the mig 175 CREATE FUNCTION + CREATE TRIGGER body verbatim.
```

Load mig 175's function body by `pathlib.Path(__file__).parents[1] / "migrations/175_privileged_chain_enforcement.sql"` and `.read_text()` — do NOT inline-copy; copy = drift. Strip the `BEGIN;`/`COMMIT;` wrappers (already inside fixture's autocommit conn).

## Per-lens

- **Steve:** APPROVE. Right axis (SQL+trigger), no stub-fest, fast (<2s).
- **Maya:** APPROVE-WITH-FIXES. Tests A/C/D/F lock the chain-of-custody DB layer; require mig 175 loaded from file (drift-resistant), require explicit assertion that error message contains literal `PRIVILEGED_CHAIN_VIOLATION` substring.
- **Carol:** N/A (no UX surface).
- **Coach:** APPROVE-WITH-FIXES. File must be in CI's PG-test path (skip-when-no-PG_TEST_URL like sibling), commit body must cite this Gate A + a Gate B verdict; per `_pg.py` PREREQ_SCHEMA DROP/CREATE rule (Session 220 #77) lint must pass.
- **Auditor:** APPROVE. C+D+F together prove the chain enforcement is real, not just declared.
- **PM:** APPROVE. 4 tests, contained scope, 30min to write, high regression-catch value.
- **Counsel:** APPROVE. Rule 3 (privileged chain) gets a behavioral DB-layer pin to complement the source-shape gates.

## Failure modes this test catches

1. Someone weakens the mig 175 function body (the #4 Session 220 lesson — additive-only) → D + F fail.
2. `site_appliances.deleted_at` column dropped / renamed → A fails.
3. Future migration replaces the EXISTS satisfiability with `UNIQUE(attestation_bundle_id)` on fleet_orders → C fails (catches the most likely accidental break of the fan-out shape).
4. Trigger silently disabled (`ALTER TABLE … DISABLE TRIGGER`) in a migration → D + F fail.
5. ORDER BY removed → A's stability assert fails.

## Anti-scope (DO NOT)

- Do NOT import / drive `cmd_create`. Don't load signing keys. Don't mock the attestation module.
- Do NOT test cross-link UPDATE shape (B), idempotency (G), or empty-site sys.exit (E) — wrong test class.
- Do NOT inline-copy mig 175 SQL — load from file.
- Do NOT skip `DROP FUNCTION` / `DROP TRIGGER` in PREREQ — second test in sweep will hit `DuplicateFunction`.

## Bindings (all P0 — must close before Gate B)

1. PREREQ_SCHEMA loads mig 175 from disk via `read_text()`.
2. DROP block covers function + trigger + all 3 tables.
3. Tests A/C/D/F shipped; B/E/G/H explicitly excluded with one-line comment per.
4. Negative-control assertion uses `PRIVILEGED_CHAIN_VIOLATION` literal substring.
5. `pytestmark = pytest.mark.skipif(not PG_TEST_URL, ...)` at module level.
