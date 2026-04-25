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
import re
import sys
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]


# Module-level constants — the production test path AND the self-tests
# below both reference these. Without this consolidation the regexes
# would drift between the two and self-tests would give false confidence
# (round-table P1 #183).
SESSION_205_CUTOFF = 156  # last pre-205 migration; 157 = check_type_registry
BOOTSTRAP_GAP_RE = re.compile(
    r'relation "[^"]+" does not exist|'
    r'column "[^"]+" .* of relation "[^"]+" does not exist|'
    r'column [a-zA-Z_.]+\.[a-zA-Z_]+ does not exist',
    re.IGNORECASE,
)
# `migrate.py::apply_migration` raises `RuntimeError("Failed to apply
# NNN_name: ...") from e` (post-#183 P0 fix). Pre-fix the version was
# only printed to stdout and never reached the exception chain — the
# regex never matched in production despite passing self-tests. Now
# the version is structurally embedded.
FAILING_VERSION_RE = re.compile(r"Failed to apply (\d{3})[a-z]?_")


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

    # The 000a_legacy_sites_baseline migration creates stubs of legacy
    # tables (`sites`, `runbooks`, `control_runbook_mapping`); 012a/012b
    # add the columns 013_multi_framework's view references. cmd_up()
    # is the single source of bootstrap.
    #
    # Pre-Session-205 migrations (000-156) were "backfilled into
    # schema_migrations" without their bodies ever running on prod
    # (CLAUDE.md "Pre-Session-205 files were backfilled once"). They
    # presume legacy schema state that exists on prod but not on a
    # fresh CI Postgres. Trying to make every pre-205 body apply
    # cleanly is a bug-class hunt with no end. We tolerate
    # bootstrap-gap failures from THOSE migrations only.
    #
    # **The hard gate (#183):** any migration version > SESSION_205_CUTOFF
    # that fails for ANY reason is a real regression — fails CI. New
    # migrations MUST work on a fresh DB. If you remove a code path
    # that a migration referenced, fix the migration, don't expand the
    # cutoff. Constants live at module scope (above) so self-tests
    # import the SAME regexes — drift between test and prod logic is
    # impossible.
    sys.path.insert(0, str(REPO_ROOT / "mcp-server" / "central-command" / "backend"))
    from migrate import cmd_up  # noqa: E402

    try:
        await cmd_up()
    except Exception as e:
        err_str = str(e)
        version_match = FAILING_VERSION_RE.search(err_str)
        failing_version = int(version_match.group(1)) if version_match else None
        is_bootstrap_class = bool(BOOTSTRAP_GAP_RE.search(err_str))

        if is_bootstrap_class and failing_version is None:
            # Bootstrap-gap classified but the version couldn't be
            # extracted — usually means the failure happened BEFORE
            # apply_migration could wrap it (e.g. ensure_migrations_table
            # failed, advisory lock timed out, or the connection itself
            # broke). Fail loud with a distinct message so an operator
            # knows this is test-harness state, not a migration regression.
            raise AssertionError(
                f"Bootstrap-gap-class error but no migration version "
                f"could be extracted from the error chain. Likely a "
                f"failure BEFORE apply_migration (connection error, "
                f"advisory lock, ensure_migrations_table). Treat as "
                f"test setup broken, not a #183 cutoff event. "
                f"Original error: {e}"
            ) from e

        if is_bootstrap_class and failing_version <= SESSION_205_CUTOFF:
            pytest.skip(
                f"cmd_up() hit a legacy-bootstrap gap in migration "
                f"{failing_version:03d} (pre-Session-205 cutoff = "
                f"{SESSION_205_CUTOFF}). This is the documented "
                f"backfill-only class. To close: add the missing "
                f"table/column to migrations/000a or a NNNa stub "
                f"migration. Error: {e}"
            )

        if is_bootstrap_class:  # implicitly post-205
            # NEW migration triggered a bootstrap gap — must fix in
            # code, not by expanding the cutoff. This is the hard gate.
            # BREAK-GLASS: bumping SESSION_205_CUTOFF is a round-table
            # conversation, not a unilateral edit.
            raise AssertionError(
                f"Migration {failing_version:03d} (POST-Session-205, "
                f"cutoff = {SESSION_205_CUTOFF}) hit a bootstrap-gap "
                f"error on fresh CI Postgres. Pre-205 migrations are "
                f"tolerated because their bodies never ran on prod "
                f"(backfilled into schema_migrations); NEW migrations "
                f"MUST work on a fresh DB. Either fix the migration "
                f"body or add a forward-only ALTER migration that "
                f"creates the missing object BEFORE this migration "
                f"runs. DO NOT bump SESSION_205_CUTOFF without a "
                f"round-table conversation. See test docstring + #183. "
                f"Original error: {e}"
            ) from e
        # Real failures (syntax errors, trigger crashes, type errors,
        # etc.) always fail loudly — the gate's purpose.
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


# ---------------------------------------------------------------------------
# Self-tests for the cutoff logic (#183) — prove the bootstrap-gap
# tolerance has the right scope. Without these, an off-by-one or scope
# regression would silently let post-205 bootstrap gaps through.
# ---------------------------------------------------------------------------


def test_cutoff_tolerates_pre_session_205_bootstrap_gap():
    """Synthetic: a wrapped `Failed to apply 010_x: relation "Y" does
    not exist` error must classify as bootstrap-gap AND pre-205. The
    self-test imports the SAME regexes used by the production logic
    above (no inline re-derivation — that hid the prior P0). The error
    string format matches what migrate.py::apply_migration now wraps
    its exception with after the #183 P0 fix."""
    err_str = (
        "Failed to apply 010_linux_runbooks: column \"runbook_id\" "
        "of relation \"runbooks\" does not exist"
    )
    assert BOOTSTRAP_GAP_RE.search(err_str), "must classify as bootstrap-gap"
    m = FAILING_VERSION_RE.search(err_str)
    assert m, "must extract version from `Failed to apply NNN_` prefix"
    assert int(m.group(1)) == 10
    assert int(m.group(1)) <= SESSION_205_CUTOFF, (
        "must be ≤ Session 205 cutoff (skip path)"
    )


def test_cutoff_rejects_post_session_205_bootstrap_gap():
    """Synthetic: a wrapped `Failed to apply 247_x` error must classify
    as bootstrap-gap BUT post-205 (fail path, not skip). This is the
    hard gate's headline behavior."""
    err_str = (
        "Failed to apply 247_promoted_rules_unique_site_rule: "
        "relation \"promoted_rules\" does not exist"
    )
    assert BOOTSTRAP_GAP_RE.search(err_str)
    m = FAILING_VERSION_RE.search(err_str)
    assert m and int(m.group(1)) == 247
    assert int(m.group(1)) > SESSION_205_CUTOFF, (
        "must be > Session 205 cutoff — flips test from skip to fail"
    )


def test_cutoff_extracts_lettered_suffix_versions():
    """000a, 012a, 012b style versions must extract their numeric prefix
    correctly so the cutoff comparison works."""
    for fname, expected in (
        ("Failed to apply 000a_legacy_sites_baseline:", 0),
        ("Failed to apply 012a_compliance_bundles_appliance_id:", 12),
        ("Failed to apply 012b_compliance_bundles_outcome:", 12),
    ):
        m = FAILING_VERSION_RE.search(fname)
        assert m and int(m.group(1)) == expected, (
            f"version-extraction broken for {fname!r}"
        )


def test_unknown_version_path_distinguishable():
    """Synthetic: when a bootstrap-gap error arrives WITHOUT a
    `Failed to apply NNN_` prefix (e.g. failure before apply_migration),
    the version regex returns no match and the production path raises
    a distinct `Bootstrap-gap-class error but no migration version`
    AssertionError, NOT the post-205 message. This is the round-table
    DBA seat's edge case."""
    err_str = "relation \"sites\" does not exist"
    assert BOOTSTRAP_GAP_RE.search(err_str), "must classify as bootstrap-gap"
    m = FAILING_VERSION_RE.search(err_str)
    assert m is None, (
        "must NOT extract a version when the error came from before "
        "apply_migration wrapped it"
    )
