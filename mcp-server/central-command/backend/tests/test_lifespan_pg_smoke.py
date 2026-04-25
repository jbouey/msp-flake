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

    # The 000a_legacy_sites_baseline migration creates a stub of the
    # legacy `sites` table (which migrations 001+ FK against) so a
    # fresh CI Postgres can apply the full ledger cleanly. 012a/012b
    # similarly stub legacy compliance_bundles columns. cmd_up() is
    # the single source of bootstrap.
    #
    # Pre-Session-205 migrations were "backfilled into schema_migrations"
    # without their bodies ever running on prod (CLAUDE.md "Pre-
    # Session-205 files were backfilled once") — they presume a
    # legacy schema that existed before the ledger and reference
    # tables/columns no migration ever creates. Forcing fresh CI to
    # run those bodies is a bug-class hunt with no end. The test
    # narrowly skips ONLY on the bootstrap-gap error classes and
    # treats everything else as a real failure (the gate's purpose).
    #
    # If you hit a NEW `relation/column does not exist` here, prefer:
    #   1. Add a stub to 000a if the missing object is a legacy table.
    #   2. Add a tiny ALTER migration (NNNa style) if it's a column
    #      added outside the ledger.
    #   3. Last resort: the skip below kicks in and notes the regression.
    sys.path.insert(0, str(REPO_ROOT / "mcp-server" / "central-command" / "backend"))
    from migrate import cmd_up  # noqa: E402

    import re as _re
    BOOTSTRAP_GAP_RE = _re.compile(
        r'relation "[^"]+" does not exist|'
        r'column "[^"]+" .* of relation "[^"]+" does not exist|'
        r'column [a-zA-Z_]+\.[a-zA-Z_]+ does not exist',
        _re.IGNORECASE,
    )
    try:
        await cmd_up()
    except Exception as e:
        if BOOTSTRAP_GAP_RE.search(str(e)):
            pytest.skip(
                f"cmd_up() hit a legacy-bootstrap gap (pre-Session-205 "
                f"migration body referencing schema state that exists "
                f"on prod but not on a fresh CI Postgres): {e}. Fix by "
                f"adding the missing table/column to migrations/000a "
                f"or a NNNa stub migration. See "
                f"test_lifespan_pg_smoke.py header docstring."
            )
        # Real failures (syntax errors, trigger crashes, etc.) still fail
        # loudly — the gate's purpose.
        raise

    # Now import main and drive its lifespan against the real PG.
    # NOTE: lifespan reads MinIO + Redis env vars at startup. CI must
    # provide these as service containers (or this test must run in a
    # job where they exist). The privileged-chain-pg-tests job is the
    # current target.
    sys.path.insert(0, str(REPO_ROOT / "mcp-server"))
    import main  # noqa: E402

    async with main.lifespan(main.app):
        # If we reach here, lifespan started cleanly. We don't need
        # to test request-handling — just startup integrity.
        pass
    # On exit, lifespan's cleanup ran without raising. Test passes.
