"""Static + structural tests for the fail→pass auto-resolve path
(Block-3 audit P0.2, 2026-05-01).

The audit found that monitoring-only incidents stayed open for 7
days waiting for the stale sweep — even when the underlying check
had transitioned fail→pass on the very next bundle. This test pins
the new auto-resolve hook in place against regression.

End-to-end DB-level tests live in `test_auto_recovered_resolution_pg.py`
(integration, requires Postgres). This module is source-level only —
asserts the hook exists, references the right tables, and uses the
right resolution_tier value.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
EVIDENCE_CHAIN = (
    REPO_ROOT / "mcp-server" / "central-command" / "backend" / "evidence_chain.py"
)
MIGRATION = (
    REPO_ROOT
    / "mcp-server"
    / "central-command"
    / "backend"
    / "migrations"
    / "264_incidents_auto_recovered_tier.sql"
)


def test_migration_264_extends_resolution_tier_check():
    """Migration 264 must extend incidents.resolution_tier CHECK to
    include 'auto_recovered'. Pre-fix CHECK only allowed
    L1/L2/L3/monitoring; the new hook would CHECK-violate without
    this migration."""
    assert MIGRATION.exists(), "Migration 264 missing"
    src = MIGRATION.read_text()
    assert "auto_recovered" in src, (
        "Migration 264 must add 'auto_recovered' to resolution_tier CHECK"
    )
    assert "DROP CONSTRAINT IF EXISTS incidents_resolution_tier_check" in src, (
        "Migration must DROP the old CHECK before re-adding (idempotency)"
    )
    assert "ADD CONSTRAINT incidents_resolution_tier_check" in src, (
        "Migration must ADD the expanded CHECK"
    )


def test_evidence_chain_has_auto_resolve_hook():
    """The fail→pass hook must exist in submit_evidence, INSIDE the
    same tenant_connection block as the bundle INSERT (atomic
    commit), and BEFORE the post-commit OTS submission block."""
    src = EVIDENCE_CHAIN.read_text()

    # Locate the submit_evidence function
    submit_idx = src.find("async def submit_evidence(")
    assert submit_idx >= 0, "submit_evidence handler not found"
    body = src[submit_idx:]

    # Anchor 1: the INSERT INTO compliance_bundles must come before the hook
    insert_idx = body.find("INSERT INTO compliance_bundles")
    assert insert_idx >= 0, "compliance_bundles INSERT not found"

    # Anchor 2: the auto-resolve hook
    hook_idx = body.find("fail→pass real-time auto-resolve")
    assert hook_idx > insert_idx, (
        "auto-resolve hook must come AFTER the bundle INSERT"
    )

    # Hook must reference the right resolution_tier value
    hook_chunk = body[hook_idx : hook_idx + 5000]
    assert "resolution_tier = 'auto_recovered'" in hook_chunk, (
        "auto-resolve hook must set resolution_tier='auto_recovered'"
    )
    assert "status = 'resolved'" in hook_chunk, (
        "auto-resolve hook must set status='resolved'"
    )
    assert "i.site_id = $1" in hook_chunk, (
        "auto-resolve query must scope by site_id (RLS-correct)"
    )
    assert "i.status = 'open'" in hook_chunk, (
        "auto-resolve must only target status='open' (idempotent)"
    )
    assert "details->>'hostname'" in hook_chunk, (
        "auto-resolve must match by hostname inside details JSONB"
    )

    # Failure must log at ERROR per CLAUDE.md "no silent write failures"
    assert "evidence_auto_recover_failed" in hook_chunk, (
        "auto-resolve failure path must log at ERROR with structured event"
    )
    assert "logger.error" in hook_chunk, "must use logger.error not warning"
    assert "exc_info=True" in hook_chunk, "must include exc_info=True"


def test_auto_resolve_hook_is_atomic_with_bundle_insert():
    """The hook must be inside the same `async with tenant_connection`
    block as the bundle INSERT — guarantees atomic commit. If the
    hook were AFTER the conn-block, a crash between INSERT and
    auto-resolve would leave a fail bundle ingested but the
    corresponding open incidents un-touched (recovery missed)."""
    src = EVIDENCE_CHAIN.read_text()
    submit_idx = src.find("async def submit_evidence(")
    body = src[submit_idx:]

    # Find the tenant_connection block boundary (closing dedent)
    insert_idx = body.find("INSERT INTO compliance_bundles")
    hook_idx = body.find("fail→pass real-time auto-resolve")
    # The next "Post-commit:" comment marks the end of the
    # tenant_connection block; the hook must come BEFORE it.
    post_commit_idx = body.find("# Post-commit:")
    assert post_commit_idx > 0, "Post-commit: comment not found"
    assert insert_idx < hook_idx < post_commit_idx, (
        "hook must be inside the tenant_connection block "
        f"(INSERT at {insert_idx}, hook at {hook_idx}, "
        f"post-commit boundary at {post_commit_idx})"
    )
