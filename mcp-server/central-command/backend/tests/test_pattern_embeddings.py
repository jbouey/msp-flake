"""Unit tests for pattern_embeddings.compute_embedding (Phase 15 A-spec).

Round-table QA audit flagged this as uncovered. hash-v1 is the current
production embedding method (pgvector + sentence-transformers deferred
per the design doc). Tests prove the hash is:

  - DETERMINISTIC (same input → same vector)
  - NORMALIZED (unit length)
  - DIMENSION-STABLE (always EMBEDDING_DIM)
  - DISTINCT (different inputs → different vectors)
  - SIMILAR-ENOUGH (semantically close inputs → higher cosine)

If any of these change, pattern similarity lookups in the flywheel
silently break.
"""
from __future__ import annotations

import math

import pytest


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    # both are unit vectors so no normalization needed
    return dot


def test_dimension_is_128():
    from pattern_embeddings import compute_embedding, EMBEDDING_DIM
    vec = compute_embedding(incident_type="firewall_drift", runbook_id="RB-001")
    assert len(vec) == EMBEDDING_DIM == 128


def test_normalization_to_unit_length():
    from pattern_embeddings import compute_embedding
    vec = compute_embedding(
        incident_type="firewall",
        check_type="firewall_baseline",
        runbook_id="RB-FIREWALL-001",
        reasoning="Firewall policy drift detected after system update",
    )
    norm = math.sqrt(sum(v * v for v in vec))
    assert abs(norm - 1.0) < 1e-9, f"vector not unit-length: norm={norm}"


def test_determinism():
    from pattern_embeddings import compute_embedding
    v1 = compute_embedding(
        incident_type="firewall", runbook_id="RB-FIREWALL-001",
        reasoning="firewall rule drift",
    )
    v2 = compute_embedding(
        incident_type="firewall", runbook_id="RB-FIREWALL-001",
        reasoning="firewall rule drift",
    )
    assert v1 == v2


def test_normalization_case_insensitive():
    """Tokenizer lowercases — 'Firewall' and 'firewall' must map to
    the same bucket distribution."""
    from pattern_embeddings import compute_embedding
    v1 = compute_embedding(incident_type="FIREWALL", runbook_id="RB-FIREWALL-001")
    v2 = compute_embedding(incident_type="firewall", runbook_id="rb-firewall-001")
    # The fields themselves are compared lowercase — but the field_toks
    # mechanism uses .lower() too. So embeddings should be identical.
    assert v1 == v2, (
        "Lowercase normalization broken — retrieval will fragment by case"
    )


def test_empty_inputs_return_zero_vector():
    from pattern_embeddings import compute_embedding, EMBEDDING_DIM
    vec = compute_embedding()
    assert len(vec) == EMBEDDING_DIM
    assert all(v == 0.0 for v in vec)


def test_distinct_incidents_produce_distinct_embeddings():
    from pattern_embeddings import compute_embedding
    firewall = compute_embedding(
        incident_type="firewall", runbook_id="RB-FIREWALL-001",
        reasoning="firewall rule drift detected on boot",
    )
    backup = compute_embedding(
        incident_type="backup", runbook_id="RB-BACKUP-001",
        reasoning="backup job failed after retention cleanup",
    )
    # Unit vectors that are NOT identical → cosine < 1.0
    cos = _cosine(firewall, backup)
    assert cos < 0.95, (
        f"Unrelated incidents produced near-identical embeddings "
        f"(cosine={cos:.4f}). Bucket collision rate is too high or "
        f"the tokenization dropped the signal."
    )


def test_similar_incidents_have_higher_cosine_than_unrelated():
    """Two firewall incidents with slightly different phrasing must
    be MORE similar to each other than either is to a backup
    incident. This is the core guarantee the flywheel relies on
    for warm-start nearest-neighbor retrieval."""
    from pattern_embeddings import compute_embedding
    a = compute_embedding(
        incident_type="firewall",
        runbook_id="RB-FIREWALL-001",
        reasoning="firewall rule drift detected on host",
    )
    b = compute_embedding(
        incident_type="firewall",
        runbook_id="RB-FIREWALL-001",
        reasoning="firewall baseline policy drifted from reference",
    )
    c = compute_embedding(
        incident_type="backup",
        runbook_id="RB-BACKUP-001",
        reasoning="backup job failed after retention cleanup",
    )
    sim_ab = _cosine(a, b)
    sim_ac = _cosine(a, c)
    sim_bc = _cosine(b, c)
    assert sim_ab > sim_ac, (
        f"Semantically similar pair {a!r}..{b!r} had lower cosine "
        f"({sim_ab:.3f}) than unrelated pair ({sim_ac:.3f}). "
        f"hash-v1 is failing its semantic-similarity guarantee."
    )
    assert sim_ab > sim_bc, f"sim(a,b)={sim_ab} but sim(b,c)={sim_bc}"


def test_field_weighting_increases_same_field_similarity():
    """compute_embedding weights the literal field tokens 2× so the
    signal from e.g. runbook_id is stronger than from reasoning
    alone. Two patterns with the SAME runbook_id but different
    reasoning should be more similar than two with different
    runbook_ids + similar reasoning."""
    from pattern_embeddings import compute_embedding
    same_runbook_diff_reason = (
        compute_embedding(runbook_id="RB-FIREWALL-001", reasoning="alpha"),
        compute_embedding(runbook_id="RB-FIREWALL-001", reasoning="beta"),
    )
    diff_runbook_same_reason = (
        compute_embedding(runbook_id="RB-FIREWALL-001", reasoning="alpha same"),
        compute_embedding(runbook_id="RB-BACKUP-002", reasoning="alpha same"),
    )
    cos_same_rb = _cosine(*same_runbook_diff_reason)
    cos_diff_rb = _cosine(*diff_runbook_same_reason)
    assert cos_same_rb > cos_diff_rb, (
        f"Field-weight guarantee broken: same runbook={cos_same_rb:.3f} "
        f"vs diff runbook={cos_diff_rb:.3f}. Retrieval will prefer "
        f"lexical matches in reasoning over structural matches in "
        f"runbook/check_type."
    )


def test_vector_is_finite_and_bounded():
    """No NaN, no inf, all components in [-1, 1] (actually [0, 1]
    since we hash counts → all non-negative → after normalization
    all in [0, 1])."""
    from pattern_embeddings import compute_embedding
    vec = compute_embedding(
        incident_type="firewall",
        check_type="drift",
        runbook_id="RB-X",
        reasoning="a " * 100,  # pathological input
    )
    for v in vec:
        assert not math.isnan(v)
        assert not math.isinf(v)
        assert 0.0 <= v <= 1.0


def test_embedding_method_is_hash_v1():
    """Downstream lookup filters by embedding_method. If hash-v1
    string changes, stored + freshly-computed embeddings stop
    matching. This test is the contract."""
    from pattern_embeddings import EMBEDDING_METHOD
    assert EMBEDDING_METHOD == "hash-v1"
