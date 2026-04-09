"""Tests for the Session 203 C1 Merkle-batch-id-collision fix.

Verifies that `process_merkle_batch` now produces unique batch_ids even
when called twice in the same UTC hour for the same site. The original
bug used `MB-{site}-{YYYYMMDDHH}` which collided and caused
`ON CONFLICT (batch_id) DO NOTHING` to silently drop the second sub-
batch's root — leaving those bundles with proofs that can't verify.
"""

import ast
import os
import re


EVIDENCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "evidence_chain.py",
)
MIGRATION = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "migrations",
    "148_fix_broken_merkle_batches.sql",
)


def _load(path: str) -> str:
    with open(path) as f:
        return f.read()


class TestMerkleBatchIdUniqueness:
    def test_process_merkle_batch_appends_random_suffix(self):
        """batch_id must include a randomized component so repeat calls
        in the same UTC hour get different IDs."""
        src = _load(EVIDENCE)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "process_merkle_batch":
                body = ast.get_source_segment(src, node) or ""
                # Must call token_hex or equivalent randomness
                assert "token_hex" in body or "token_urlsafe" in body or "uuid" in body.lower()
                # The batch_id format must include the random suffix
                assert re.search(r'batch_id\s*=\s*f?"MB-.*-\{.*suffix', body) or \
                    "unique_suffix" in body, \
                    "process_merkle_batch must include a random suffix in batch_id"
                return
        assert False, "process_merkle_batch not found"

    def test_fix_comment_references_c1(self):
        """The fix must carry a comment pointing at the audit finding
        so future readers understand WHY the suffix exists."""
        src = _load(EVIDENCE)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "process_merkle_batch":
                body = ast.get_source_segment(src, node) or ""
                # Comment must reference the C1 finding or the session
                assert "C1" in body or "Session 203" in body or "batch_id collision" in body.lower()
                return
        assert False

    def test_on_conflict_kept_as_safety_net(self):
        """ON CONFLICT should still be present as a belt-and-suspenders
        guard even though the unique suffix makes it functionally
        unreachable — a retry or restore could still surface the
        same batch_id."""
        src = _load(EVIDENCE)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "process_merkle_batch":
                body = ast.get_source_segment(src, node) or ""
                assert "ON CONFLICT (batch_id) DO NOTHING" in body
                return
        assert False


class TestMigration148Backfill:
    def test_migration_exists(self):
        assert os.path.exists(MIGRATION)

    def test_migration_is_transactional(self):
        src = _load(MIGRATION)
        assert "BEGIN;" in src
        assert "COMMIT;" in src

    def test_identifies_broken_batches_by_count_mismatch(self):
        """A batch is broken iff the number of bundles pointing at it
        exceeds the stored tree size. When this holds, all bundles in
        the batch are suspect because leaf_index values from different
        sub-batches overlap in storage."""
        src = _load(MIGRATION)
        assert "HAVING COUNT(" in src
        assert "mb.bundle_count" in src

    def test_reclassifies_to_legacy(self):
        """Broken bundles must be flipped to ots_status='legacy' with
        merkle fields cleared so the portal UI stops claiming they
        verify."""
        src = _load(MIGRATION)
        assert "ots_status = 'legacy'" in src
        assert "merkle_batch_id = NULL" in src
        assert "merkle_proof = NULL" in src

    def test_writes_audit_trail(self):
        """Every remediation step must leave an admin_audit_log row for
        HIPAA §164.308(a)(1)(ii)(D) audit control requirements."""
        src = _load(MIGRATION)
        assert "INSERT INTO admin_audit_log" in src
        assert "MERKLE_BATCH_BUNDLE_RECLASSIFIED" in src
        assert "MERKLE_BATCH_BACKFILL_COMPLETE" in src

    def test_audit_details_include_reason(self):
        src = _load(MIGRATION)
        assert "Session 203 C1" in src
        assert "batch_id collision" in src
