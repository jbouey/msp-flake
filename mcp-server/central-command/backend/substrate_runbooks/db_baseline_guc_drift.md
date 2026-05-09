# db_baseline_guc_drift

**Severity:** sev2
**Display name:** DB GUC drift — RLS posture compromised

## What this means (plain English)

A load-bearing Postgres GUC (`app.is_admin`, `app.current_tenant`,
`app.current_org`, or `app.current_partner_id`) has drifted from
the tenant-safety baseline. The substrate's RLS posture depends
on these defaults to keep tenant data isolated by default.

If this fires, tenant isolation has silently flipped to permissive.
A bug or migration has set a value that grants more access than
intended.

## Root cause categories

- A migration set `ALTER DATABASE ... SET app.is_admin='true'`
  (the load-bearing flip — RLS bypass enabled by default).
- A connection-pool init script ran `SET app.current_tenant='X'`
  which leaked across PgBouncer transaction-pool boundaries and
  was preserved on the connection.
- An operator psql session set a GUC interactively for debugging
  and forgot to RESET it.

## Immediate action

1. Identify the drifted GUC (in `details.guc`).
2. Run `SHOW <guc>` against psql connected through PgBouncer to
   confirm the runtime state.
3. If the value is wrong: `RESET <guc>` (session-level) OR
   `ALTER DATABASE mcp RESET <guc>` (database-level, persistent).
4. Investigate WHICH migration / hotfix introduced the drift —
   `SELECT * FROM admin_audit_log WHERE action LIKE '%alter%' OR
   action LIKE '%guc%' ORDER BY created_at DESC LIMIT 20`.

## Verification

Substrate engine re-checks every 60s. Once the GUC matches
baseline, this invariant clears.

## Escalation

If the GUC reverts to drift after RESET, a migration or boot-time
script is actively setting it. Do NOT silence the invariant —
find and fix the source. Sustained `app.is_admin='true'` is a
RLS-bypass posture; multi-tenant isolation is OFF.

## Related runbooks

- `client_portal_zero_evidence_with_data.md` — sibling: catches
  org-scope RLS misalignment from the OTHER direction (RLS
  posture too restrictive, customer sees zero rows).

## Related

- Phase 1 audit: `audit/multi-tenant-phase1-concurrent-write-stress-2026-05-09.md` F-P1-4
- Mig 234 — original tenant-safety default of `app.is_admin='false'`
- CLAUDE.md `admin_transaction()` rule for multi-statement admin paths

## Change log

- **2026-05-09:** Created. Phase 1 multi-tenant audit F-P1-4 closure.
