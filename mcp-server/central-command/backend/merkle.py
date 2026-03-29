"""Merkle tree construction and verification for evidence proof batching.

Used to batch multiple evidence bundle hashes into a single OTS proof
via a Merkle root. Each bundle retains a Merkle path (sibling hashes)
that proves inclusion in the batch without requiring the full tree.
"""
import hashlib
from typing import List, Tuple


def _hash_pair(left: str, right: str) -> str:
    """SHA256 of concatenated hex hashes (left || right)."""
    combined = bytes.fromhex(left) + bytes.fromhex(right)
    return hashlib.sha256(combined).hexdigest()


def build_merkle_tree(hashes: List[str]) -> Tuple[str, List[List[dict]]]:
    """Build a Merkle tree from a list of SHA256 hex hashes.

    Args:
        hashes: List of hex-encoded SHA256 hashes (the leaves).

    Returns:
        Tuple of (root_hash, proofs) where proofs[i] is the Merkle path
        for hashes[i]. Each path is a list of
        {"hash": "...", "side": "left"|"right"}.

    If odd number of leaves at any level, duplicate the last node
    (standard Merkle tree convention).
    """
    if not hashes:
        raise ValueError("Cannot build Merkle tree from empty list")
    if len(hashes) == 1:
        return hashes[0], [[]]

    n = len(hashes)
    proofs: List[List[dict]] = [[] for _ in range(n)]
    positions = list(range(n))  # current position of each original leaf
    current_level = list(hashes)

    while len(current_level) > 1:
        # Pad with duplicate if odd number of nodes
        if len(current_level) % 2 == 1:
            current_level.append(current_level[-1])

        next_level = []
        for i in range(0, len(current_level), 2):
            next_level.append(_hash_pair(current_level[i], current_level[i + 1]))

        # Record sibling for each original leaf's proof path
        for leaf_idx in range(n):
            pos = positions[leaf_idx]
            if pos % 2 == 0:  # left child
                sibling_pos = pos + 1
                proofs[leaf_idx].append({
                    "hash": current_level[sibling_pos],
                    "side": "right",
                })
            else:  # right child
                sibling_pos = pos - 1
                proofs[leaf_idx].append({
                    "hash": current_level[sibling_pos],
                    "side": "left",
                })
            positions[leaf_idx] = pos // 2

        current_level = next_level

    return current_level[0], proofs


def verify_merkle_proof(
    leaf_hash: str,
    proof: List[dict],
    expected_root: str,
) -> bool:
    """Verify a Merkle proof for a leaf hash against an expected root."""
    return compute_merkle_root(leaf_hash, proof) == expected_root


def compute_merkle_root(leaf_hash: str, proof: List[dict]) -> str:
    """Compute the Merkle root from a leaf hash and its proof path."""
    current = leaf_hash
    for step in proof:
        sibling = step["hash"]
        if step["side"] == "right":
            current = _hash_pair(current, sibling)
        else:
            current = _hash_pair(sibling, current)
    return current
