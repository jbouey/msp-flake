# AGENTS.md — mcp-server/central-command/backend

Scoped to the FastAPI backend + asyncpg + SQLAlchemy code under this directory. Root invariants live in [`/AGENTS.md`](../../../AGENTS.md) and [`/CLAUDE.md`](../../../CLAUDE.md) — read those first.

## Entry points

| You're about to... | Read first |
|---|---|
| Add a route to an existing router | `routes.py`, `sites.py`, `agent_api.py` (pick by prefix) |
| Add a new migration | `migrations/` (latest: `241_drop_appliance_provisioning_api_key.sql`) and `migrate.py` |
| Touch an auth-adjacent endpoint | `auth.py` + `privileged_access_api.py` + root CLAUDE.md §"Privileged-Access Chain of Custody" |
| Touch evidence / audit tables | `evidence_chain.py` + [ADR 2026-04-24](../../../docs/adr/2026-04-24-source-of-truth-hygiene.md) |
| Emit a user-facing error string | `constants/copy.ts` is the single source — do not hardcode |

## Local invariants (non-negotiable in this directory)

- **`execute_with_retry()` for every SQLAlchemy query through PgBouncer.** Raw `db.execute()` causes `DuplicatePreparedStatementError`. The retry helper lives in `shared.py`. New code uses `execute_with_retry(db, text(...), params)`.
- **`_enforce_site_id(auth_site_id, request_site_id, endpoint_name)` on every endpoint with `Depends(require_appliance_bearer)`.** Prevents an appliance holding a site-A token from writing to site-B. `agent_api.py` has 13 hardened endpoints — copy that pattern.
- **asyncpg savepoint invariant.** Every `conn.execute/fetch*` inside `tenant_connection()` / `admin_connection()` whose failure is caught non-fatally MUST be inside `async with conn.transaction():` (or SQLAlchemy `db.begin_nested()`). Python `try/except` does NOT reset Postgres transaction state — a bare query on a poisoned transaction aborts every subsequent statement on the same connection.
- **No silent write failures.** `except Exception: pass` on DB writes is banned. `logger.warning` on write failures is banned — use `logger.error(..., exc_info=True, extra={...})`. Reads may eat exceptions; writes log-and-raise. Log shipper alerts off ERROR.
- **CSRF-exempt paths live in one list.** Machine-to-machine endpoints (checkin, witness, provision, devices/sync) go in `csrf.py` `EXEMPT_PATHS`. If you add a daemon-facing endpoint and it returns 403/500, start there.
- **Dual pools are intentional.** SQLAlchemy (`shared.py`, pool_size=20) for admin CRUD via `Depends(get_db)`. asyncpg (`fleet.py`, min=2/max=25) for RLS-enforced queries via `tenant_connection()` / `admin_connection()`. Both go through PgBouncer. Cannot consolidate — RLS needs `SET LOCAL`.

## Migration file conventions

- Filename: `NNN_short_description.sql` where `NNN` is a zero-padded integer one greater than the current max.
- End the file with a literal `COMMIT;` on its own line — the migrator runs each file in an advisory-locked transaction.
- Do NOT `SELECT apply_migration(...)` or manually INSERT into `schema_migrations`. The migrator self-records via `apply_migration()` after the file's DDL runs cleanly.
- Migrations run FAIL-CLOSED on startup via `main.py` lifespan → `migrate.cmd_up()`. A pending or failing migration is `SystemExit(2)`.
- For multi-row UPDATEs through the `mcp_app` role, either filter by a unique column or `SET LOCAL app.allow_multi_row='true'` inside a transaction (row-guard from migration 192 / 208).

## Three invariant documents (never stale)

1. [ADR 2026-04-24 — Source-of-Truth Hygiene](../../../docs/adr/2026-04-24-source-of-truth-hygiene.md) — before adding a new column, field, or in-memory copy of an existing value.
2. [Post-mortem PROCESS.md](../../../docs/postmortems/PROCESS.md) — Sev-2+ incidents = 24 h to published post-mortem.
3. [Root CLAUDE.md](../../../CLAUDE.md) — privileged-access chain of custody, three-list lockstep, deploy-via-git-push.

## Verify before claiming done

```bash
cd mcp-server/central-command/backend
python -m pytest tests/ -v --tb=short
```

The pre-push hook (`.githooks/pre-push`) enforces this. Don't `--no-verify`.
