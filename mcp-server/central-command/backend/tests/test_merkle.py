"""Tests for Merkle tree construction and verification."""
import hashlib
import pytest
from merkle import (
    _hash_pair,
    build_merkle_tree,
    verify_merkle_proof,
    compute_merkle_root,
)


def _h(data: str) -> str:
    """Helper: SHA256 hex digest of a string."""
    return hashlib.sha256(data.encode()).hexdigest()


# ---------------------------------------------------------------------------
# _hash_pair
# ---------------------------------------------------------------------------

class TestHashPair:
    def test_deterministic(self):
        a = _h("a")
        b = _h("b")
        assert _hash_pair(a, b) == _hash_pair(a, b)

    def test_order_matters(self):
        a = _h("a")
        b = _h("b")
        assert _hash_pair(a, b) != _hash_pair(b, a)


# ---------------------------------------------------------------------------
# Single leaf
# ---------------------------------------------------------------------------

class TestSingleLeaf:
    def test_root_equals_hash(self):
        h = _h("only")
        root, proofs = build_merkle_tree([h])
        assert root == h

    def test_proof_is_empty(self):
        h = _h("only")
        _, proofs = build_merkle_tree([h])
        assert proofs == [[]]


# ---------------------------------------------------------------------------
# Two leaves
# ---------------------------------------------------------------------------

class TestTwoLeaves:
    def test_root(self):
        a, b = _h("a"), _h("b")
        root, _ = build_merkle_tree([a, b])
        assert root == _hash_pair(a, b)

    def test_proofs(self):
        a, b = _h("a"), _h("b")
        _, proofs = build_merkle_tree([a, b])
        # a is left child, sibling b on the right
        assert proofs[0] == [{"hash": b, "side": "right"}]
        # b is right child, sibling a on the left
        assert proofs[1] == [{"hash": a, "side": "left"}]


# ---------------------------------------------------------------------------
# Three leaves (odd — last duplicated)
# ---------------------------------------------------------------------------

class TestThreeLeaves:
    def test_root(self):
        a, b, c = _h("a"), _h("b"), _h("c")
        root, _ = build_merkle_tree([a, b, c])
        # Level 1: [hash(a,b), hash(c,c)]
        left = _hash_pair(a, b)
        right = _hash_pair(c, c)
        assert root == _hash_pair(left, right)

    def test_proofs_verify(self):
        a, b, c = _h("a"), _h("b"), _h("c")
        root, proofs = build_merkle_tree([a, b, c])
        for i, h in enumerate([a, b, c]):
            assert verify_merkle_proof(h, proofs[i], root), (
                f"Proof for leaf {i} failed verification"
            )


# ---------------------------------------------------------------------------
# Four leaves (balanced power-of-2)
# ---------------------------------------------------------------------------

class TestFourLeaves:
    def test_root(self):
        leaves = [_h(str(i)) for i in range(4)]
        root, _ = build_merkle_tree(leaves)
        # Level 1: [hash(0,1), hash(2,3)]
        l1_left = _hash_pair(leaves[0], leaves[1])
        l1_right = _hash_pair(leaves[2], leaves[3])
        assert root == _hash_pair(l1_left, l1_right)

    def test_all_proofs_verify(self):
        leaves = [_h(str(i)) for i in range(4)]
        root, proofs = build_merkle_tree(leaves)
        for i, h in enumerate(leaves):
            assert verify_merkle_proof(h, proofs[i], root)

    def test_proof_depth(self):
        leaves = [_h(str(i)) for i in range(4)]
        _, proofs = build_merkle_tree(leaves)
        for p in proofs:
            assert len(p) == 2  # log2(4) = 2


# ---------------------------------------------------------------------------
# 15 leaves (realistic batch)
# ---------------------------------------------------------------------------

class TestFifteenLeaves:
    def test_all_proofs_verify(self):
        leaves = [_h(f"bundle-{i}") for i in range(15)]
        root, proofs = build_merkle_tree(leaves)
        for i, h in enumerate(leaves):
            assert verify_merkle_proof(h, proofs[i], root), (
                f"Proof for leaf {i} failed"
            )

    def test_proof_depth(self):
        leaves = [_h(f"bundle-{i}") for i in range(15)]
        _, proofs = build_merkle_tree(leaves)
        # 15 leaves → 4 levels (ceil(log2(15)) = 4)
        for p in proofs:
            assert len(p) == 4


# ---------------------------------------------------------------------------
# verify_merkle_proof edge cases
# ---------------------------------------------------------------------------

class TestVerifyMerkleProof:
    def test_valid_proof(self):
        leaves = [_h(str(i)) for i in range(4)]
        root, proofs = build_merkle_tree(leaves)
        assert verify_merkle_proof(leaves[0], proofs[0], root) is True

    def test_tampered_leaf_hash(self):
        leaves = [_h(str(i)) for i in range(4)]
        root, proofs = build_merkle_tree(leaves)
        tampered = _h("TAMPERED")
        assert verify_merkle_proof(tampered, proofs[0], root) is False

    def test_wrong_root(self):
        leaves = [_h(str(i)) for i in range(4)]
        root, proofs = build_merkle_tree(leaves)
        wrong_root = _h("wrong-root")
        assert verify_merkle_proof(leaves[0], proofs[0], wrong_root) is False

    def test_swapped_proof(self):
        """Using leaf 0's proof on leaf 1 should fail."""
        leaves = [_h(str(i)) for i in range(4)]
        root, proofs = build_merkle_tree(leaves)
        assert verify_merkle_proof(leaves[1], proofs[0], root) is False


# ---------------------------------------------------------------------------
# Empty list
# ---------------------------------------------------------------------------

class TestEmptyList:
    def test_raises_value_error(self):
        with pytest.raises(ValueError, match="empty"):
            build_merkle_tree([])


# ---------------------------------------------------------------------------
# compute_merkle_root
# ---------------------------------------------------------------------------

class TestComputeMerkleRoot:
    def test_matches_build_root(self):
        leaves = [_h(str(i)) for i in range(8)]
        root, proofs = build_merkle_tree(leaves)
        for i, h in enumerate(leaves):
            assert compute_merkle_root(h, proofs[i]) == root

    def test_single_leaf(self):
        h = _h("single")
        root, proofs = build_merkle_tree([h])
        assert compute_merkle_root(h, proofs[0]) == h


# ---------------------------------------------------------------------------
# Roundtrip test across multiple sizes
# ---------------------------------------------------------------------------

class TestRoundtrip:
    @pytest.mark.parametrize("n", [1, 2, 3, 4, 7, 15, 100])
    def test_all_proofs_roundtrip(self, n):
        leaves = [_h(f"leaf-{i}") for i in range(n)]
        root, proofs = build_merkle_tree(leaves)
        assert len(proofs) == n
        for i, h in enumerate(leaves):
            assert verify_merkle_proof(h, proofs[i], root), (
                f"n={n}, leaf {i} failed roundtrip verification"
            )
