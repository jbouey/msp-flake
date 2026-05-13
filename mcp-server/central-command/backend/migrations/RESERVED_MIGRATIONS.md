# Reserved migration numbers — backend only

> **Scope:** this ledger covers `mcp-server/central-command/backend/migrations/` only. The `appliance/` and `agent/` repos have no migration number-spaces as of 2026-05-13 (verified empty via `find appliance/ agent/ -name "*.sql"` → zero hits).
>
> **Why this file:** on 2026-05-13 three of six designs reviewed in parallel collided on migration numbers at Gate A (load harness v2 claimed shipped `mig 310`; MTTR soak v2 claimed `mig 311` already reserved by Vault P0 #43; P-F9 v1 claimed `mig 314` already reserved by Task #50). The pattern is structural — designs draft numbers without a coordination surface. This ledger is the coordination surface; CI gate `tests/test_migration_number_collision.py` (Task #59) enforces it.
>
> **Lifecycle:** `reserved` → `in_progress` → `BLOCKED` (waiting on external precondition) → SHIPPED (the migration file lands in `migrations/NNN_*.sql`, this row is **removed in the same commit**).
>
> **Stale-reservation rule:** rows whose `expected_ship` date is more than 30 days past today are flagged by CI as STALE — must ship, mark BLOCKED with a per-row justification, or release the number. Per-row justification shape (in the Notes column): `<!-- stale-justification: reason --> `.
>
> **Hard cap:** at most 30 active rows. Beyond signals coordination breakdown; surface to round-table.

| Number | Status | Claimed-by (design doc) | Claimed-at | Expected ship | Task | Notes |
|--------|--------|------------------------|------------|---------------|------|-------|
| 311 | BLOCKED | (Vault P0 bundle — see audit/coach-vault-phase-c-gate-a-2026-05-12.md) | 2026-05-12 | 2026-05-27 | #43 | BLOCKED on staging precondition + 24-48h reverse-shadow before cut |
| 316 | reserved | (load harness v2.1 — pending re-design per Gate A BLOCK) | 2026-05-13 | 2026-05-23 | #38 | renumbered from claimed 310 (already shipped) |
| 317 | reserved | (P-F9 profitability v2 — pending re-design per Gate A BLOCK) | 2026-05-13 | 2026-05-30 | #58 | `partner_profitability_assumptions` table |
| 318 | reserved | (P-F9 profitability v2 — pending re-design per Gate A BLOCK) | 2026-05-13 | 2026-05-30 | #58 | `partner_profitability_packets` table (companion) |

## How to claim a number

1. **Find the next free number** (next integer past the highest entry in this ledger AND past the highest shipped migration on disk).
2. **Add a row to this table** in the same commit as the design doc that claims it.
3. **Add the explicit marker** on a line BY ITSELF in your design doc, OUTSIDE any code fence:

   `<!-- mig-claim:NNN task:#TT -->`

   (Literal shape: `<` `!` `-` `-` ` mig-claim:` `NNN` ` task:#` `TT` ` ` `-` `-` `>`)

4. **When the migration ships**: in the same commit that adds `migrations/NNN_*.sql`, **remove the matching row from this ledger**. The on-disk SQL is post-ship authority.

## Stale-justification example

If a reservation legitimately needs to age past `expected_ship + 30d`, append the justification to the Notes column:

> `<!-- stale-justification: blocked on outside-counsel review per Task #56 §-Q 3 -->`

## Releasing a reservation

If a design is dropped or the number is no longer needed: remove the row in a commit whose message says `Mig-released: NNN — reason`.

## See also

- `tests/test_migration_number_collision.py` — CI gate (8 tests, incl. Gate A v3 hardenings)
- `audit/reserved-migrations-ledger-design-2026-05-13.md` — design v3 + Gate A trail
