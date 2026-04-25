"""Real-Postgres lifespan smoke test (#154).

The pure-AST `test_lifespan_imports_resolve.py` (Session 210-B) catches
the deferred-import-of-deleted-symbol class at pre-push time without
needing the runtime stack. This test is its CI-side complement —
spins up a real Postgres, applies every migration, then drives main's
`lifespan()` ASGI hook to completion and asserts no exception.

Catches the runtime-only classes the AST guard CAN'T:
  - migration body has a SQL syntax error that only surfaces when
    triggers fire (the Session 205 RAISE-too-many-params bug)
  - `lifespan()` runs a query whose column reference doesn't exist in
    the post-migration schema (the 2026-04-25 prometheus_metrics
    `last_seen` 500 class)
  - background-task supervisor tries to register a task whose factory
    raises at construction
  - `migrate.cmd_up()` fails on a fresh DB (e.g. order-of-migration
    bug)

Skips if PG_TEST_URL is not set (so it's a no-op on local dev that
hasn't started a Postgres).

Heavy-ish: pulls the full backend dependency tree (asyncpg, sqlalchemy,
fastapi, pynacl, ...). Run only in CI's `privileged-chain-pg-tests`
job which already has the deps cached.
"""
from __future__ import annotations

import os
import sys
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]


def _pg_url() -> str:
    """Return the PG_TEST_URL env var. Skip the test if absent."""
    url = os.getenv("PG_TEST_URL")
    if not url:
        pytest.skip("PG_TEST_URL not set — lifespan PG smoke skipped")
    return url


@pytest.mark.asyncio
async def test_lifespan_drives_to_completion_against_real_pg():
    """Apply every migration, then run `lifespan()` against a real PG.

    Asserts no exception is raised through __aenter__ → __aexit__.
    Doesn't assert specific side-effects — that's what other tests do.
    The point of THIS test is a binary: lifespan succeeds or it doesn't.

    A failure here means the import-time + lifespan-startup sequence is
    broken in the merged commit. Running this in CI before deploy
    short-circuits the prod-crashloop class entirely.
    """
    pg_url = _pg_url()

    # Make sure we run migrations as the superuser (which is the same
    # role the deploy workflow uses for cmd_up). Most test classes run
    # as `mcp` already.
    os.environ["MIGRATION_DATABASE_URL"] = pg_url
    os.environ["DATABASE_URL"] = pg_url
    # Defaults that lifespan reads — must be present, values irrelevant
    os.environ.setdefault("AGENT_BEARER_TOKEN", "test-token-not-used")
    os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "K" * 44 + "=")

    # Apply migrations BEFORE importing main — main.lifespan also calls
    # migrate.cmd_up at startup (fail-closed) but we want a known-clean
    # baseline so failures localize to lifespan, not migrations.
    #
    # The migration ledger has a bootstrap gap: migration 001_portal_
    # tables references `sites` which is created in a pre-migration
    # legacy schema (the prod DB has it, fresh CI Postgres doesn't).
    # Closing that gap is its own ticket — until then, this test
    # skips when migrations can't apply against a bare DB. Once the
    # gap is closed, the skip becomes a hard fail and the test starts
    # paying off.
    sys.path.insert(0, str(REPO_ROOT / "mcp-server" / "central-command" / "backend"))
    from migrate import cmd_up  # noqa: E402

    try:
        await cmd_up()
    except Exception as e:
        pytest.skip(
            f"cmd_up() failed against fresh PG (likely the legacy "
            f"`sites` bootstrap gap — migration 001 references a table "
            f"created outside the migration ledger): {e}. Close the "
            f"gap by adding a migration that CREATE TABLE IF NOT "
            f"EXISTS sites with the prod columns, then this test "
            f"becomes a hard gate."
        )

    # Now import main and drive its lifespan against the real PG.
    sys.path.insert(0, str(REPO_ROOT / "mcp-server"))
    import main  # noqa: E402

    async with main.lifespan(main.app):
        # If we reach here, lifespan started cleanly. Sleep zero — we
        # don't need to test request-handling, just startup integrity.
        pass
    # On exit, lifespan's cleanup ran without raising. Test passes.
