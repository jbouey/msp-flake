# rename_site_immutable_list_drift

**Severity:** sev2
**Display name:** Site-id table has DELETE-block trigger but isn't in immutable list

## What this means (plain English)

Postgres has a table that:
1. Has a `site_id` column.
2. Is protected by a DELETE-blocking trigger (the standard "this table
   is append-only, you can't tamper with old rows" signal).
3. Is NOT listed in `_rename_site_immutable_tables()` — the function
   `rename_site()` consults to know which tables to skip.

This is a chain-of-custody risk. If an operator runs
`rename_site('A', 'B', ...)` and one of these tables holds rows with
`site_id='A'`, `rename_site()` will rewrite them to `site_id='B'`. For
a table that's append-only because of HIPAA §164.316(b)(2)(i) retention
or cryptographic binding (Ed25519, OTS), that rewrite invalidates the
record's auditable identity.

The invariant fires sev2 because it flags POTENTIAL future drift, not
an active violation. The drift becomes real only the next time
`rename_site()` runs.

## Root cause categories

- **New audit-class table added without updating the immutable list.**
  Most common cause. A migration adds a new table with `site_id` plus
  a DELETE-blocking trigger; the developer forgot to also add it to
  `_rename_site_immutable_tables()`.
- **Migration created the trigger but the table is operationally
  active.** Less common — sometimes a table gets a DELETE-block
  defensively but is intended to track per-site state that legitimately
  follows a rename. Rare; deserves explicit confirmation.
- **`_rename_site_immutable_tables()` itself was edited and a row was
  accidentally removed.** Audit `git log` on migration 257 + any
  follow-on migration that touched the function.

## Immediate action

For each table in the violation's `drift_tables` list, decide which
side it belongs on:

```sql
-- 1. What's the trigger blocking?
SELECT trg.tgname,
       p.prosrc
  FROM pg_trigger trg
  JOIN pg_class c ON c.oid = trg.tgrelid
  JOIN pg_proc p ON p.oid = trg.tgfoid
 WHERE c.relname = '<table_name>'
   AND NOT trg.tgisinternal
   AND (trg.tgtype & 8) = 8;  -- DELETE bit

-- 2. How many rows? Are they still under any active site_id?
SELECT site_id, COUNT(*)
  FROM <table_name>
 GROUP BY site_id
 ORDER BY 2 DESC LIMIT 10;
```

**If the table IS append-only (HIPAA / cryptographic / audit):** add
it to `_rename_site_immutable_tables()` in a new migration, mirroring
the existing entries (see migration 257). Include the reason — a
short string explaining why it's immutable.

**If the table is operationally per-site and the DELETE-block is
unintended:** drop the trigger in a new migration. Round-table review
before doing this — DELETE-blocks on operational tables are rare and
were almost always added for a reason.

## Verification

- Panel: invariant row should clear on the next 60s tick after the
  follow-on migration applies.
- CLI:

  ```sql
  SELECT table_name FROM _rename_site_immutable_tables()
   WHERE table_name = '<table_name>';
  ```

  Expected: one row (post-fix).

- Re-run the substrate query (the same SQL the invariant uses).
  **NOTE**: this is the two-pass form (Session 214 P3 close).
  Pass 1 catches triggers on the table itself (regular tables +
  partitioned parents); Pass 2 catches triggers on partition CHILDREN
  and surfaces the PARENT name. For partitioned tables, the trigger
  may be attached to the parent (`relkind='p'`, the mig 191
  appliance_heartbeats pattern) OR — in legacy patterns — to specific
  monthly children. The drift detection surfaces the parent name in
  both cases; investigate at the parent level.

  ```sql
  WITH partition_children AS (
      SELECT i.inhrelid AS child_oid, i.inhparent AS parent_oid
        FROM pg_inherits i
  ),
  trigger_carriers AS (
      -- Pass 1: trigger directly on the table
      SELECT DISTINCT c.relname AS table_name, c.oid AS table_oid
        FROM pg_trigger trg
        JOIN pg_class c ON c.oid = trg.tgrelid
        JOIN pg_proc p ON p.oid = trg.tgfoid
        JOIN pg_namespace n ON n.oid = c.relnamespace
       WHERE n.nspname = 'public'
         AND c.relkind IN ('r', 'p')
         AND NOT trg.tgisinternal
         AND (trg.tgtype & 8) = 8
         AND p.prosrc ILIKE '%RAISE EXCEPTION%'
         AND NOT EXISTS (
             SELECT 1 FROM partition_children pc
              WHERE pc.child_oid = c.oid
         )
      UNION
      -- Pass 2: trigger on a partition child → surface the parent
      SELECT DISTINCT parent.relname AS table_name, parent.oid AS table_oid
        FROM pg_trigger trg
        JOIN pg_class c ON c.oid = trg.tgrelid
        JOIN pg_proc p ON p.oid = trg.tgfoid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN partition_children pc ON pc.child_oid = c.oid
        JOIN pg_class parent ON parent.oid = pc.parent_oid
       WHERE n.nspname = 'public'
         AND c.relkind = 'r'
         AND NOT trg.tgisinternal
         AND (trg.tgtype & 8) = 8
         AND p.prosrc ILIKE '%RAISE EXCEPTION%'
  )
  SELECT DISTINCT tc.table_name
    FROM trigger_carriers tc
    JOIN information_schema.columns sit
      ON sit.table_name = tc.table_name
     AND sit.column_name = 'site_id'
     AND sit.table_schema = 'public'
   WHERE tc.table_name NOT IN (
         SELECT table_name FROM _rename_site_immutable_tables()
   );
  ```

  Expected: zero rows.

## Escalation

This is sev2 (not sev1) because no harm has occurred yet — `rename_site()`
hasn't run against the drifted table. But:

- **DO NOT call `rename_site()` while this invariant is firing.** Wait
  for the immutable list to be updated.
- **If a privileged-access-class operation needs to run urgently and a
  rename is part of it,** page the round-table for sign-off; the
  decision matrix is "is this table's content cryptographically bound
  to its site_id?" — if yes, never let `rename_site()` touch it.

## Related runbooks

- (none — this is a meta invariant about the `rename_site()` function
  itself; it doesn't share a class with operational substrate runbooks)

## Change log

- 2026-04-29 — created — F4-followup from Session 213 round-table
  (mig 257 + canonical_site_id() + rename_site() architectural close).
