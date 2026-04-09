"""Tests for Session 203 Tier 2.1 — full-chain browser verification.

These cover the BACKEND contract that the frontend Web Worker depends on:

  1. The /sites/{site_id}/bundles endpoint must support `order=asc` so the
     worker can walk the chain in chain_position order (otherwise prev_hash
     linkage cannot be verified across batches).
  2. It must support `include_signatures=true` so the worker can read the
     `agent_signature` and `chain_hash` columns. Without these the existing
     Batch 5 hook was silently no-op'ing.
  3. It must continue to default to the old shape (DESC + signatures hidden)
     so the admin UI is not affected.

We also assert that the frontend worker file exists at the expected path
and that it contains the message-protocol identifiers the hook depends on.
This catches the worst class of regression: someone deletes the worker file
and the build still passes because the import is via `new URL(...)`, but the
panel silently does nothing in production.
"""

import ast
import os
import re


_HERE = os.path.dirname(os.path.abspath(__file__))
EVIDENCE_PY = os.path.normpath(os.path.join(_HERE, "..", "evidence_chain.py"))
WORKER_TS = os.path.normpath(
    os.path.join(
        _HERE,
        "..",
        "..",
        "frontend",
        "src",
        "portal",
        "verifyChainWorker.ts",
    )
)
HOOK_TS = os.path.normpath(
    os.path.join(
        _HERE,
        "..",
        "..",
        "frontend",
        "src",
        "portal",
        "useBrowserVerifyFull.ts",
    )
)
PANEL_TSX = os.path.normpath(
    os.path.join(
        _HERE,
        "..",
        "..",
        "frontend",
        "src",
        "portal",
        "FullChainVerifyPanel.tsx",
    )
)


def _load(path: str) -> str:
    with open(path) as f:
        return f.read()


def _get_func(name: str) -> str:
    src = _load(EVIDENCE_PY)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{name} not found")


# =============================================================================
# Backend: bundles endpoint contract
# =============================================================================

class TestBundlesEndpointContract:
    def test_endpoint_accepts_order_param(self):
        body = _get_func("list_evidence_bundles")
        assert "order:" in body and "= \"desc\"" in body

    def test_endpoint_accepts_include_signatures_param(self):
        body = _get_func("list_evidence_bundles")
        assert "include_signatures:" in body
        assert "= False" in body

    def test_asc_order_branch_exists(self):
        body = _get_func("list_evidence_bundles")
        assert "ASC" in body
        assert "chain_position" in body

    def test_signature_columns_returned_when_requested(self):
        """When include_signatures=True the SQL must add agent_signature
        and chain_hash to the SELECT list."""
        body = _get_func("list_evidence_bundles")
        assert "agent_signature" in body
        assert "chain_hash" in body

    def test_signature_columns_off_by_default(self):
        """The default include_signatures=False path must not pull
        signature/chain_hash. We assert via the variable that controls it."""
        body = _get_func("list_evidence_bundles")
        # The conditional column block is what gates this. The literal
        # `cb.agent_signature, cb.chain_hash` only appears under the
        # include_signatures=True branch.
        m = re.search(r'sig_cols\s*=\s*"([^"]*)"\s*if\s+include_signatures', body)
        assert m is not None, "include_signatures conditional not found"

    def test_response_echoes_order(self):
        body = _get_func("list_evidence_bundles")
        assert '"order"' in body

    def test_endpoint_still_uses_evidence_view_guard(self):
        body = _get_func("list_evidence_bundles")
        assert "require_evidence_view_access" in body

    def test_default_order_is_descending_for_backward_compat(self):
        """Admin UI relies on DESC ordering. The default must not change."""
        body = _get_func("list_evidence_bundles")
        # Default value of `order` parameter is the literal "desc"
        assert 'order: str = "desc"' in body


# =============================================================================
# Frontend: worker file exists with the expected protocol
# =============================================================================

class TestVerifyChainWorker:
    def test_worker_file_exists(self):
        assert os.path.isfile(WORKER_TS), f"missing {WORKER_TS}"

    def test_worker_imports_noble_ed25519(self):
        src = _load(WORKER_TS)
        assert "@noble/ed25519" in src

    def test_worker_handles_init_message(self):
        src = _load(WORKER_TS)
        assert "msg.type === 'init'" in src
        assert "publicKeys" in src

    def test_worker_handles_batch_message(self):
        src = _load(WORKER_TS)
        assert "msg.type === 'batch'" in src
        assert "verifyBatch" in src

    def test_worker_handles_finalize_message(self):
        src = _load(WORKER_TS)
        assert "msg.type === 'finalize'" in src

    def test_worker_posts_progress_messages(self):
        src = _load(WORKER_TS)
        assert "type: 'progress'" in src
        assert "bundlesProcessed" in src

    def test_worker_posts_done_summary(self):
        src = _load(WORKER_TS)
        assert "type: 'done'" in src
        assert "summary" in src

    def test_worker_does_chain_hash_check(self):
        src = _load(WORKER_TS)
        assert "sha256Hex" in src
        assert "chain_hash" in src

    def test_worker_does_prev_hash_linkage_check(self):
        """The cross-batch invariant: each bundle's prev_hash matches the
        previous bundle's bundle_hash. Without this, two batches could be
        rearranged and the worker would miss it."""
        src = _load(WORKER_TS)
        assert "prevBundleHash" in src
        assert "GENESIS_PREV_HASH" in src

    def test_worker_uses_verify_async(self):
        src = _load(WORKER_TS)
        assert "verifyAsync" in src

    def test_worker_counts_legacy_bundles_as_missing_not_failed(self):
        """Legacy bundles (no signature) must be honestly reported as
        unsigned, not as failures."""
        src = _load(WORKER_TS)
        assert "signaturesMissing" in src


# =============================================================================
# Frontend: hook + panel exist and reference the worker
# =============================================================================

class TestUseBrowserVerifyFullHook:
    def test_hook_file_exists(self):
        assert os.path.isfile(HOOK_TS)

    def test_hook_spawns_worker_via_vite_worker_import(self):
        """Vite worker pattern: `import X from './worker.ts?worker'` makes
        Vite compile the .ts to a real Web Worker bundle. A plain string
        path or `new URL()` would copy the .ts source verbatim and silently
        fail in production (browsers can't execute TypeScript)."""
        src = _load(HOOK_TS)
        assert "?worker" in src
        assert "verifyChainWorker" in src
        assert "new VerifyChainWorker(" in src

    def test_hook_fetches_public_keys(self):
        src = _load(HOOK_TS)
        assert "/public-keys" in src

    def test_hook_streams_bundles_with_pagination(self):
        src = _load(HOOK_TS)
        assert "limit=" in src
        assert "offset=" in src
        assert "order=asc" in src
        assert "include_signatures=true" in src

    def test_hook_terminates_worker_on_unmount(self):
        src = _load(HOOK_TS)
        assert "worker.terminate" in src

    def test_hook_exposes_start_and_cancel(self):
        src = _load(HOOK_TS)
        assert "start: () =>" in src
        assert "cancel: () =>" in src

    def test_hook_default_does_not_auto_start(self):
        """A 100K-bundle verification should never start by surprise."""
        src = _load(HOOK_TS)
        assert "autoStart" in src


class TestFullChainVerifyPanel:
    def test_panel_file_exists(self):
        assert os.path.isfile(PANEL_TSX)

    def test_panel_has_verify_button(self):
        src = _load(PANEL_TSX)
        assert "Verify entire chain" in src

    def test_panel_shows_progress_counter(self):
        src = _load(PANEL_TSX)
        assert "bundlesProcessed" in src
        assert "totalBundles" in src

    def test_panel_displays_failure_state(self):
        src = _load(PANEL_TSX)
        assert "signaturesFailed" in src
        assert "chainLinksFailed" in src

    def test_panel_links_security_email_on_failure(self):
        src = _load(PANEL_TSX)
        assert "security@osiriscare.net" in src

    def test_panel_references_merkle_disclosure(self):
        src = _load(PANEL_TSX)
        assert "OSIRIS-2026-04-09-MERKLE-COLLISION" in src
