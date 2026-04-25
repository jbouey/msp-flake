# SQL schema vs Python column-name audit

Use with: `Agent(subagent_type="general-purpose", prompt=<this file's contents>)`.

---

Audit the Msp_Flakes Python backend (cwd: /Users/dad/Documents/Msp_Flakes) for SQL column-name mismatches against the actual prod schema.

Background: on 2026-04-25 we shipped four endpoints with broken column references that would have failed on first call:
- `INSERT INTO fleet_orders (id, site_id, appliance_id, ...)` — those columns don't exist; fleet_orders is fleet-wide
- `INSERT INTO admin_audit_log (action, actor, ...)` — column is `username`, not `actor`
- `INSERT INTO admin_audit_log (target_type, target_id, ...)` — actual column is just `target`

All passed CI tests because tests are source-level (string-grep) rather than PG-backed.

There's now a strict CI test (`test_sql_columns_match_schema.py`) that uses a prod-extracted schema fixture (`tests/fixtures/schema/prod_columns.json`) — your job is to find any NEW patterns the static linter hasn't caught yet, OR confirm the fixture is current.

Tasks:

1. **Refresh check:** confirm the fixture matches prod by running:
   ```bash
   ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -t -A -c \"
     SELECT json_object_agg(table_name, columns) FROM (
       SELECT table_name, json_agg(column_name ORDER BY ordinal_position) AS columns
         FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name NOT LIKE 'pg_%'
        GROUP BY table_name) s
   \""
   ```
   Compare with the committed fixture; any drift = stale fixture, refresh needed.

2. **Pattern hunt:** find SQL referencing columns that the linter doesn't catch:
   - Dynamic SQL via f-strings: `f"INSERT INTO {table} ({col_var}) ..."` — bypasses static parser
   - SQLAlchemy ORM mapped columns vs prod schema (model attribute names should match column names)
   - Raw `await conn.execute(text("SELECT col FROM ..."))` SELECTs aren't checked by the linter

3. **Trust-gap audit:** review `SCHEMA_TRUST_GAPS` in `tests/test_sql_columns_match_schema.py` — any table in there should have a justification comment + a manual verification that its INSERTs are correct.

4. **Baseline review:** the linter currently has `INSERT_BASELINE_MAX = 16` and `UPDATE_BASELINE_MAX = 9`. List the 25 violations and bucket them: real bug (fix needed) vs trust-gap candidate (verified manually OK).

Skip test files (`tests/test_*.py`), `venv/`, `archived/`, `.claude/worktrees/`.

Report under 500 words:
- Found mismatches as: `<file>:<line>: <column> in <table> SQL — actual columns: [...]`
- Confidence per finding: HIGH / MEDIUM / LOW
- If clean: "Clean — schema fixture matches prod, no new patterns surfaced"

Read-only — don't write or edit anything.
