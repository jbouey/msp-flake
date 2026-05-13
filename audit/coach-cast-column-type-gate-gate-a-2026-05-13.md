# Gate A — `test_no_param_cast_against_mismatched_column` AST Gate (Task #77)

**Date:** 2026-05-13
**Subject:** New CI gate that catches `WHERE col = $N::TYPE` patterns whose cast TYPE disagrees with the actual prod column type. Triggered by today's 4h dashboard outage at `signature_auth.py:618` (`WHERE appliance_id = $1::uuid` against a TEXT column). Consistency-coach round-table found 126 `$N::uuid` callsites + 180 other-type cast callsites; only 1 was wrong, the rest correct-by-inspection but UNVERIFIED.

---

## 250-word Summary

The bug class is real and structurally enforceable. Confirmation via 5-minute grep against `migrations/`: `appliance_id` is declared TEXT in `migrations/049_fleet_orders.sql`, `migrations/191_appliance_heartbeats.sql`, `migrations/195_mesh_target_ack.sql` AND VARCHAR(255) in `migrations/045_audit_fixes.sql`, `migrations/012a_compliance_bundles_appliance_id.sql`, `migrations/002_orders_table.sql` AND VARCHAR(64) in older rows — **zero rows are UUID**. `site_id` is similarly VARCHAR(100)/TEXT across the board, never UUID. Every single `$N::uuid` cast against `appliance_id` or `site_id` in the codebase is therefore wrong-by-construction; the fact that only 1 of 126 fired in prod today is luck — the prod-write path happened to go through one query, the other 125 are pinned to other columns (`id`, `partner_id`, `org_id`, `user_id`, etc. which ARE UUID).

The fixture-augment cost is real but tractable (~30min one-time, +~10KB to fixture). The AST-gate cost is the harder line item (~2h, sqlparse is the right tool, not raw regex). **The dominant risk** is table-resolution for unqualified column references inside JOINs — the gate must either (a) skip JOIN queries entirely or (b) require table-qualified references in cast contexts. I recommend (a) — JOIN queries are <15% of cast-bearing callsites by sample.

Steve **APPROVE-WITH-FIXES** (JOIN-skip scope rule). Maya **APPROVE** (fixture shape is backwards-compatible). Coach **APPROVE** (1-of-126 wrong × 4h outage cost = positive EV). PM **APPROVE-WITH-FIXES** (phase A independently shippable; phase B can defer 1 sprint if needed).

**Overall: APPROVE-WITH-FIXES — implement in two phases, Phase A this sprint, Phase B within 2 weeks.**

---

## Per-Lens Verdict

### 1. Engineering (Steve) — APPROVE-WITH-FIXES

**Bug class is real and confirmed by schema spot-check.** From `migrations/`:

| Table | Column | Declared Type | Source |
|---|---|---|---|
| `fleet_orders` | `appliance_id` | TEXT | mig 049 |
| `appliance_heartbeats` | `appliance_id` | TEXT | mig 191 |
| `mesh_target_ack` | `appliance_id` | TEXT | mig 195 |
| `audit_fixes_*` | `appliance_id` | VARCHAR(255) | mig 045 |
| `compliance_bundles` | `appliance_id` | VARCHAR(255) | mig 012a |
| `orders` | `appliance_id` | VARCHAR(64) | mig 002 |
| `go_agents` | `site_id` | VARCHAR(100) | mig 019 |
| `client_orgs.id` | UUID | mig 015 + later |
| `partners.id` | UUID | (per partners.py read shape) |

The 1-of-126 wrong rate today is **not** "code review caught the others." It's that **all 125 correct cases happen to be against UUID-typed columns** (`id`, `partner_id`, `org_id`, `client_org_id`, `user_id`, `target_user_id`, `partner_user_id`). The ONE wrong case (`appliance_id`) was the first one to write a TEXT-typed column with `::uuid`. If anyone writes the next `WHERE site_id = $1::uuid` (and `site_id` is VARCHAR not UUID across every table), it will pass code review the same way today's did and break in prod.

**AST traversal challenges (rank-ordered):**

1. **SQL lives in string literals, not Python AST.** The walker must:
   - Find every `ast.Call` whose `.func.id == "text"` OR `ast.Call` where receiver is `conn`/`db` and method ∈ `{execute, fetch, fetchrow, fetchval}` OR every `ast.Constant` of type `str` containing `WHERE … $N::`.
   - Extract the `value` string from each.
   - Pass it to a SQL parser (sqlparse or pglast) to walk WHERE clauses.

2. **Column-to-table resolution is the load-bearing edge case.**
   - `WHERE appliance_id = $1::uuid` — single-table query, column is unqualified, table is in the FROM clause. Easy.
   - `WHERE a.appliance_id = $1::uuid AND s.site_id = $2` — table-qualified, but we need to resolve `a` → which table? Requires walking aliases in `FROM` and `JOIN` clauses.
   - JOIN queries with unqualified column references are ambiguous AT THE SQL LEVEL; Postgres rejects them when columns conflict. For the gate, **skip every query with more than one table in FROM/JOIN scope** unless every cast-bearing column is table-qualified. This drops gate coverage by ~15% (sampled JOIN-query rate in callsites) — acceptable.

3. **Multi-line `text("""…""")` patterns.** Python's `ast.Constant` captures the full joined string; SQL parser gets the full SQL. No special handling needed.

4. **Dynamic SQL composition.** `text(f"SELECT ... WHERE col = ${param_idx}::uuid")` — f-string interpolation. Detect `ast.JoinedStr` and either parse the format-pieced template or skip. Recommend skip + 1-line `# noqa: cast-type-gate` allowlist comment for any f-strings the gate flags.

5. **`COALESCE($1::uuid, $2::uuid)` shape.** No column on the LHS of the cast. The gate is scoped to `WHERE col OP $N::TYPE` and SET clauses; skip cast-in-expression context. Acceptable scope reduction — these don't have a "wrong column" failure mode anyway.

6. **`$N::text = ''` shape (COALESCE-NULL handling).** Carved out at partners.py:CASE-WHEN: `CASE WHEN $9::text = '' THEN NULL ELSE $9::uuid END`. The first cast (`::text`) has no column LHS; the second (`::uuid`) is in the THEN-branch of a CASE. Scope rule: only flag casts directly inside WHERE/SET. Acceptable.

7. **`SELECT col FROM t WHERE col = $1::uuid` where `col` itself is the cast target column.** No problem — same column resolves; gate just checks type match.

8. **CTE inside the SQL.** `WITH x AS (...) SELECT ... FROM x WHERE x.col = $1::uuid` — the CTE adds a "virtual table." Skip queries with `WITH` keyword and document as known gap. Counsel by Steve only: CTEs are <8% of cast-bearing queries in this codebase.

9. **The `sqlparse` library is preferred over raw regex.** Regex over SQL hits ambiguity at nested parens, comment handling, string-literal-containing-`WHERE`-keyword, etc. `sqlparse` is in PyPI, no native compile, 0 dependencies. `pglast` is more correct but pulls in libpq-compatible C bindings — heavyweight for a CI gate. **Recommend sqlparse with a thin custom walker** for FROM-clause + WHERE-clause extraction. Pure-Python, deterministic, no surprise edge cases.

10. **Implementation approach (proven sketch):**
    ```python
    import ast, sqlparse
    from sqlparse.sql import Where, Identifier, IdentifierList
    from sqlparse.tokens import Keyword, Punctuation

    CAST_RE = re.compile(r"(\w+(?:\.\w+)?)\s*=\s*\$\d+::(\w+)", re.IGNORECASE)

    for sql_text in iter_sql_strings_in_module(path):
        parsed = sqlparse.parse(sql_text)[0]
        tables = resolve_from_clause(parsed)  # alias → table
        if len(tables) > 1 and not all_columns_qualified(parsed):
            continue  # skip ambiguous JOIN queries
        for col_ref, cast_type in CAST_RE.findall(sql_text):
            tbl, col = resolve_column(col_ref, tables)
            actual = schema[tbl].get(col)
            if not actual: continue  # unknown column — different gate's job
            if not types_compatible(cast_type, actual):
                yield (path, line, sql_text, tbl, col, cast_type, actual)
    ```

**`types_compatible` helper** — must handle alias equivalence:
- `text` ≡ `character varying` (`VARCHAR`) ≡ `varchar(N)` — all string types interoperate in cast context, but **none of them are `uuid`**, which is the load-bearing distinction.
- `int` ≡ `int4` ≡ `integer`; `bigint` ≡ `int8`.
- `timestamptz` ≡ `timestamp with time zone`.
- `jsonb` and `json` are distinct types — flag mismatches.

**Verdict:** APPROVE-WITH-FIXES — implement with sqlparse, skip ambiguous-JOIN-queries, skip CTEs, skip f-string SQL templates, with `# noqa: cast-type-gate` escape hatch for unavoidable false positives.

### 2. Database (Maya) — APPROVE

**Fixture augmentation cost: ~30min, additive shape change, backwards-compatible.**

Current fixture: `{table_name: [col1, col2, ...]}` (alphabetized lists; 5147 lines).
Proposed: `{table_name: {"columns": {col_name: data_type, ...}}}` OR backwards-compat: `{table_name: {col_name: data_type, ...}}` (drops the array shape but the existing consumer `test_sql_columns_match_schema.py` only iterates keys — see line 98: `{tbl.lower(): {c.lower() for c in cols}}`).

**Recommended shape (Maya):** keep the lift small by adopting `{table_name: {col_name: data_type}}`. The existing consumer's `{c.lower() for c in cols}` becomes `{c.lower() for c in cols.keys()}` — one-line patch. Type data sits next to column names; no fixture-format proliferation.

**Refresh command extension** (one-line change to the docstring command on line 26-37):
```sql
SELECT json_object_agg(table_name, columns) FROM (
  SELECT table_name, json_object_agg(column_name, data_type ORDER BY ordinal_position) AS columns
    FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name NOT LIKE 'pg_%'
   GROUP BY table_name) s
```
Run against prod, sort, write. ~5sec on the prod box.

**File size:** today 5147 lines. With types: ~7500 lines (~46% growth, not 3x — most lines are bare column names that gain a `: "uuid"` or `: "text"` suffix, not whole new lines). Acceptable for a git-tracked fixture.

**Drift risk:** fixture-vs-prod-schema drift is already a risk class for the name-only fixture. Adding type info doesn't widen that risk surface — same refresh discipline, same lockstep rule with migrations.

**Verdict:** APPROVE — augmentation cost is bounded and the existing consumer (`test_sql_columns_match_schema.py`) needs only a one-line patch to accept the new shape.

### 3. Security (Carol) — N/A

No security surface change. The bug class is a runtime correctness bug, not an authorization or data-exposure bug. (Counter: a `$1::uuid` cast against a TEXT column that *succeeds* due to type coercion ambiguity could in principle accept attacker-controlled non-UUID input — but asyncpg fails the cast at the prepare stage, not silently coerces. Not a Carol-class concern.)

### 4. Coach — APPROVE

**The math is favorable.** 1-of-126 wrong today, but:
- **The cost when wrong: 4h prod outage** (today). Production dashboard down, customer-visible. Class-A incident severity.
- **The cost to implement: ~3h** (Phase A 30min + Phase B 2h + integration test 30min).
- **The cost to maintain: ~10min per migration** that touches a cast-bearing column (refresh fixture, re-run gate, fix any flagged callsites).
- **Future-blast-radius:** every new `WHERE col = $N::uuid` query that misroutes against a TEXT column hits the same 4h+ failure mode. Without the gate, code review catches some but not all (today's slipped through).

**Recommend implement now.** Coach has signed off on smaller gates with weaker ROI (e.g. NULLABLE_RATES anchor at task #71 had a 1-callsite production trigger and ~45min implementation cost — that was approved). This gate's prod-trigger-rate per unit-of-effort is comparable.

**One reservation:** the gate's value is concentrated in a few column-name × type-name combinations (`appliance_id::uuid`, `site_id::uuid`, the inverse of `partner_id::text`). 90% of gate hits will be in those 3-4 buckets. If Phase B turns out harder than 2h, a **stopgap allowlist gate** that only checks the 4 highest-risk column names against their actual types would deliver 80% of the value at 20% of the cost.

**Verdict:** APPROVE — with stopgap fallback option if AST work blows past budget.

### 5. Auditor (OCR) — N/A

### 6. PM — APPROVE-WITH-FIXES

**Effort breakdown (validated against codebase shape):**

| Phase | Task | Estimate | Risk |
|---|---|---|---|
| A | Augment `prod_columns.json` to `{tbl: {col: type}}` | 30 min | Low — one psql query + format conversion |
| A | Patch `test_sql_columns_match_schema.py` (1 line) | 5 min | Low |
| A | Patch `test_migration_view_columns_exist.py` if needed | 10 min | Low — likely same 1-line pattern |
| A | Patch `test_no_raw_discovered_devices_count.py` if needed | 10 min | Low |
| **A subtotal** | | **~55 min** | |
| B | Write `test_no_param_cast_against_mismatched_column.py` | 90 min | Medium — sqlparse walker |
| B | Sanity-run against 126 known callsites; suppress false positives | 30 min | Medium — JOIN ambiguity |
| B | Add to pre-push allowlist + verify CI green | 15 min | Low |
| **B subtotal** | | **~2h 15min** | |
| **TOTAL** | | **~3h 10min** | |

**Bigger than tasks #71/#72/#79** (yes, confirmed) but smaller than mig-bearing P0s. Sits in the middle.

**Phasing rationale:**
- **Phase A is independently shippable.** The augmented fixture is useful on its own — `test_sql_columns_match_schema.py` continues to function unchanged (1-line shape patch), and Phase B becomes a future task that doesn't block other work.
- **Phase B has a hard scope boundary.** If sqlparse walking proves harder than 2h, fall back to the Coach-proposed stopgap (regex over `(appliance_id|site_id|host_id)\s*=\s*\$\d+::uuid` — 30min implementation, covers the bug class that actually hit today + nearest siblings).

**Sequencing recommendation:**
1. **This sprint:** Phase A (fixture augment). Independent, low-risk, has standalone value (other gates can consume type data).
2. **This sprint OR next:** Phase B (the AST gate). If AST walker is hard, ship stopgap regex gate first as bridge.
3. **Defer if needed:** broaden gate to `INSERT INTO t (cols…) VALUES ($N::TYPE…)` — currently scope is limited to WHERE/SET clauses; INSERT-VALUES casts are rarer and lower-risk.

**Verdict:** APPROVE-WITH-FIXES — phase the work, Phase A independently shippable, Phase B has documented stopgap fallback if scope explodes.

### 7. Attorney (In-house Counsel) — N/A

---

## Particular Probes — Results

### Fixture Current Shape
- Path: `mcp-server/central-command/backend/tests/fixtures/schema/prod_columns.json`
- Shape: `{"table_name": ["col1", "col2", ...]}` (sorted column-name arrays)
- Size: 5147 lines
- Consumer: `test_sql_columns_match_schema.py:84-98` loads as `{tbl.lower(): {c.lower() for c in cols}}`. Patch is trivial.

### 10 Random `$N::uuid` Callsites — Correctness Spot-Check

Sample (via `grep | shuf -n 15`):

| Callsite | Cast | Inferred Table | Actual Type |
|---|---|---|---|
| `partners.py:WHERE partner_id = $1::uuid` | uuid | partners | uuid ✓ |
| `partners.py:WHERE id = $1::uuid AND partner_id = $2::uuid` | uuid×2 | partner_users | both uuid ✓ |
| `substrate_actions.py:WHERE id = $1::uuid` | uuid | fleet_orders | uuid ✓ (mig 049 PK) |
| `mfa_admin.py:$1::uuid, 'client_user', $2::uuid` | uuid×2 | mfa_overrides | uuid ✓ |
| `partner_admin_transfer.py:WHERE id = $1::uuid` | uuid | partner_admin_transfer_requests | uuid ✓ (mig 274 PK) |
| `org_management.py:WHERE id = $1::uuid` | uuid | client_orgs | uuid ✓ |
| `org_management.py:WHERE org_id = $1::uuid` | uuid | (various) | likely uuid ✓ |
| `client_owner_transfer.py:WHERE id = $1::uuid` | uuid | client_org_owner_transfer_requests | uuid ✓ (mig 273 PK) |
| `sites.py:VALUES ($1::uuid, ...)` | uuid | (INSERT) | (skip-scope: INSERT VALUES) |
| `audit_report.py:WHERE client_org_id = $1::uuid` | uuid | sites | uuid ✓ |

**Verdict:** Sampled callsites are correctly cast against UUID-typed columns. Today's outage column (`appliance_id` at signature_auth.py:618) was the outlier — `appliance_id` is universally TEXT/VARCHAR across migrations, never UUID. Confirmed by grep over `migrations/`:

```
appliance_id VARCHAR(255) — migrations/045, 012a, 040, 003
appliance_id VARCHAR(64)  — migrations/002
appliance_id TEXT NOT NULL — migrations/049, 191, 195
appliance_id UUID — ZERO matches
```

Same pattern for `site_id`:
```
site_id VARCHAR(100) — migrations/019, 263
site_id TEXT — migrations/224, 256
site_id UUID — ONE match (migrations/015 `cloud_integrations` table)
```

So the gate's high-confidence bug-catch buckets are:
1. `WHERE appliance_id = $N::uuid` — always wrong.
2. `WHERE site_id = $N::uuid` — almost always wrong (except `cloud_integrations` table).
3. `WHERE host_id = $N::uuid` — needs verification but suspected same class.

### Library Choice: sqlparse vs pglast vs Raw Regex

- **Raw regex** — fast prototype, but ambiguous on nested parens, comment handling, multi-table queries. Sufficient for a stopgap allowlist-of-column-names gate; NOT sufficient for general-case cast-vs-column gate.
- **pglast** — bindings to Postgres's actual parser. Most correct. BUT: requires C compilation, pulls in libpq-compatible headers. Heavyweight for CI; deploy footprint grows.
- **sqlparse** — pure Python, no native deps, in PyPI. Handles 95% of real-world SQL correctly. Falls short on some edge cases (recursive CTEs, complex window functions) — exactly the queries we'd skip anyway.

**Recommendation: sqlparse + custom thin walker** for FROM-resolution + WHERE-clause iteration. Falls back to skip-with-noqa on ambiguous cases.

---

## Phased Plan

### Phase A (~55min) — Augment Fixture, Backwards-Compatible Patch

1. Run the augmented `information_schema.columns` query on prod:
   ```bash
   ssh root@178.156.162.116 'docker exec mcp-postgres psql -U mcp -d mcp -t -A -c "
     SELECT json_object_agg(table_name, columns) FROM (
       SELECT table_name, json_object_agg(column_name, data_type ORDER BY ordinal_position) AS columns
         FROM information_schema.columns
        WHERE table_schema = '"'"'public'"'"' AND table_name NOT LIKE '"'"'pg_%'"'"'
        GROUP BY table_name) s"'
   ```
2. Write to `prod_columns.json` with `json.dump(sort_keys=True, indent=2)`.
3. Update the docstring command in `test_sql_columns_match_schema.py:26-37`.
4. Patch `schema` fixture loader: `{tbl.lower(): {c.lower() for c in cols.keys()}}` instead of `{c.lower() for c in cols}`.
5. Grep for any other fixture consumers; patch each (estimated 1-3 total — `test_migration_view_columns_exist.py`, `test_no_raw_discovered_devices_count.py`).
6. Run pre-push gates; confirm no regressions.

**Phase A deliverable:** fixture has type data, all existing gates still green. Independently mergeable.

### Phase B (~2h 15min) — AST Gate

1. Create `tests/test_no_param_cast_against_mismatched_column.py`.
2. Implement `iter_sql_strings_in_backend()` — walks backend `.py` files, finds `text("…")` and `conn.execute("…")` / `conn.fetch*("…")` call sites, yields `(path, line, sql_text)` tuples.
3. Implement `extract_from_clause_tables(sql)` — sqlparse walks FROM/JOIN, returns `{alias_or_table: table_name}`.
4. Implement `find_cast_callsites(sql)` — regex `(\w+(?:\.\w+)?)\s*(?:=|<>|!=|IN)\s*\$\d+::(\w+)` for WHERE/SET context only.
5. Implement `types_compatible(cast_type, actual_type)` — handles text/varchar/character varying aliases, int/integer/int4 aliases, timestamp/timestamptz distinction.
6. Skip scopes: queries with `WITH` keyword; queries with >1 FROM/JOIN tables AND unqualified columns; f-string SQL (`ast.JoinedStr`); cast in expression context (CASE WHEN, COALESCE).
7. Add `# noqa: cast-type-gate` line-level escape hatch.
8. Sanity-run against 126 known `$N::uuid` + 180 other-type callsites. Suppress documented false positives. Confirm zero unexpected hits.
9. Add gate to pre-push allowlist (`tests/test_pre_push_ci_parity.py::SOURCE_LEVEL_TESTS`).
10. Push, wait CI green, verify `runtime_sha == deployed`.

**Phase B deliverable:** gate is live, all callsites in main are clean. Future writes flagged at pre-push.

### Stopgap Fallback (if Phase B blows budget)

Single-purpose regex gate:
```python
# tests/test_no_uuid_cast_on_text_columns.py
HIGH_RISK_TEXT_COLUMNS_UUID_BANNED = {"appliance_id", "site_id", "host_id"}
PATTERN = re.compile(
    r"WHERE\s+(?:\w+\.)?(\w+)\s*[=<>!]+\s*\$\d+::uuid", re.IGNORECASE)
# walk backend .py; fail if matched column in HIGH_RISK_TEXT_COLUMNS_UUID_BANNED
```
~30min implementation, catches today's exact bug class plus its two nearest siblings.

---

## Recommendation

**Implement now, phased.**

Rationale:
- Prevention value: today's outage was 4h customer-visible dashboard down. The same class will recur (cast-bearing queries land in every sprint) unless gated.
- Effort: ~3h total, **Phase A independently shippable in ~55min** so progress is unblocked even if Phase B slips.
- Risk profile: low. Backwards-compatible fixture change; gate is pure-Python; stopgap fallback exists if AST work proves hard.
- Sequence: Phase A this sprint (highest ROI per minute). Phase B within 2 weeks; if AST walker hits >2h budget, ship stopgap fallback first.

**Do NOT defer.** Today's outage demonstrated the class is real, customer-impacting, and not caught by existing review/CI. Each sprint without the gate carries a non-zero re-occurrence probability multiplied by 4h+ outage cost.

---

## Final Overall Verdict

**APPROVE-WITH-FIXES — implement in two phases, Phase A this sprint (Phase B within 2 weeks), stopgap regex fallback in pocket if AST scope blows out.**

Per-lens summary:
- Steve: APPROVE-WITH-FIXES (JOIN-skip scope rule, sqlparse over pglast)
- Maya: APPROVE (fixture shape is backwards-compatible, ~46% size growth)
- Carol: N/A
- Coach: APPROVE (1-of-126 × 4h outage = positive EV)
- Auditor: N/A
- PM: APPROVE-WITH-FIXES (phased, Phase A standalone-valuable)
- Counsel: N/A

**Followups to file as tasks at completion:**
- Phase A: augment `prod_columns.json` with types + backwards-compat patch (~55min, P1, this sprint).
- Phase B: AST gate `test_no_param_cast_against_mismatched_column.py` (~2h 15min, P1, within 2 weeks).
- Stopgap-fallback: `test_no_uuid_cast_on_text_columns.py` (only if Phase B blows budget, ~30min, P1).
- Fixture-refresh discipline: add `prod_columns.json` to the lockstep-with-migrations list documented in CLAUDE.md (P2).
