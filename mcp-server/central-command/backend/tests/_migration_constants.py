"""Shared migration-related test constants.

Lifted from `test_lifespan_pg_smoke.py` (Session 211 Phase 3, #186) so
other CI gates that reason about pre-vs-post-205 migration boundaries
can import the same number instead of hardcoding it. Drift between
gates over this constant would silently re-introduce the bootstrap-gap
class the original gate was built to prevent.

If you're tempted to bump this number: don't. The only valid path is a
round-table review confirming a NEW migration legitimately depends on
legacy schema state that exists on prod but not on a fresh DB. New
migrations MUST work on a fresh DB. See test_lifespan_pg_smoke.py for
the full BREAK-GLASS protocol.
"""
from __future__ import annotations


# Last pre-Session-205 migration. 157 = check_type_registry, the first
# migration that runs cleanly on a fresh DB (Session 205 cleanup).
# Pre-205 migrations (000-156) were "backfilled into schema_migrations"
# without their bodies ever running on prod (CLAUDE.md "Pre-Session-205
# files were backfilled once") and presume legacy schema state. The
# bootstrap-gap exemption ONLY applies to migration versions <= this.
SESSION_205_CUTOFF: int = 156
