"""Pattern embeddings — deterministic hash-based vector representation
for L2 warm-start nearest-neighbor lookup.

Phase 7 of the advanced flywheel. Approach:

1. Tokenize (incident_type, check_type, runbook_id, reasoning) into
   lowercased word/n-gram tokens.
2. For each token, hash into one of N buckets with a stable hash.
3. Accumulate counts per bucket; L2-normalize to unit length.
4. Store as FLOAT4[] in pattern_embeddings.

This is a **bag-of-hashed-tokens** approach — deterministic, reproducible,
zero external dependency, computable offline. Similar incidents map to
similar bucket distributions → cosine similarity is meaningful even
without a learned embedding model.

When pgvector + sentence-transformers (or an embedding API) is
integrated, the `embedding_method` column allows mixed generations
to coexist and lookup can filter by method.

Usage:
    vec = compute_embedding(incident_type='firewall', check_type='firewall',
                            runbook_id='RB-FIREWALL-001',
                            reasoning='Firewall policy drift detected')
    neighbors = await find_nearest_patterns(conn, vec, k=5)
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

EMBEDDING_DIM = 128
EMBEDDING_METHOD = "hash-v1"

# Tokenization: split on non-alphanumeric, lowercase, drop stop/empty.
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_]*")
_STOP = {
    "the", "a", "an", "and", "or", "is", "was", "are", "be", "to",
    "of", "for", "on", "in", "at", "by", "with", "from",
}


def _tokens(text: str) -> List[str]:
    if not text:
        return []
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP]


def _bigrams(tokens: Sequence[str]) -> List[str]:
    """Also hash bigrams so phrase structure contributes to the embedding."""
    return [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]


def _bucket(token: str, dim: int = EMBEDDING_DIM) -> int:
    """Stable hash into [0, dim)."""
    h = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(h, "big") % dim


def compute_embedding(
    incident_type: Optional[str] = None,
    check_type: Optional[str] = None,
    runbook_id: Optional[str] = None,
    reasoning: Optional[str] = None,
    extra_text: Optional[str] = None,
    dim: int = EMBEDDING_DIM,
) -> List[float]:
    """Compute a deterministic hash-based embedding for a pattern.

    Returns a unit-length vector of length `dim` as a plain list[float].
    """
    parts: List[str] = []
    if incident_type:
        parts.append(incident_type)
    if check_type:
        parts.append(check_type)
    if runbook_id:
        parts.append(runbook_id)
    if reasoning:
        parts.append(reasoning)
    if extra_text:
        parts.append(extra_text)

    joined = " ".join(parts)
    toks = _tokens(joined)
    # Fields themselves also contribute as whole-token signals (weighted 2×)
    field_toks = []
    for field_value in (incident_type, check_type, runbook_id):
        if field_value:
            field_toks.append(field_value.lower())
    all_features = toks + _bigrams(toks) + field_toks * 2

    vec = [0.0] * dim
    for t in all_features:
        vec[_bucket(t, dim)] += 1.0

    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


async def upsert_pattern_embedding(
    conn,
    pattern_key: str,
    incident_type: Optional[str],
    check_type: Optional[str],
    runbook_id: Optional[str],
    reasoning: Optional[str] = None,
    source_occurrences: int = 1,
    source_sites: int = 1,
) -> None:
    """Upsert a pattern_embeddings row. Idempotent; safe to call on every
    L2 decision or promotion event."""
    vec = compute_embedding(
        incident_type=incident_type,
        check_type=check_type,
        runbook_id=runbook_id,
        reasoning=reasoning,
    )
    src_text = " | ".join(
        filter(None, [incident_type, check_type, runbook_id, (reasoning or "")[:200]])
    )
    await conn.execute(
        """
        INSERT INTO pattern_embeddings (
            pattern_key, incident_type, check_type, runbook_id,
            embedding, embedding_method, source_text,
            source_sites, source_occurrences, updated_at
        ) VALUES ($1, $2, $3, $4, $5::float4[], $6, $7, $8, $9, NOW())
        ON CONFLICT (pattern_key) DO UPDATE SET
            incident_type = EXCLUDED.incident_type,
            check_type = EXCLUDED.check_type,
            runbook_id = EXCLUDED.runbook_id,
            embedding = EXCLUDED.embedding,
            source_text = EXCLUDED.source_text,
            source_sites = EXCLUDED.source_sites,
            source_occurrences = EXCLUDED.source_occurrences,
            updated_at = NOW()
        """,
        pattern_key, incident_type, check_type, runbook_id,
        vec, EMBEDDING_METHOD, src_text,
        source_sites, source_occurrences,
    )


async def find_nearest_patterns(
    conn,
    query_vec: Sequence[float],
    k: int = 5,
    min_similarity: float = 0.3,
    exclude_pattern_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return the k nearest patterns to `query_vec` by cosine similarity.

    `min_similarity` filters out patterns that are semantically unrelated.
    Empty list is a valid "no neighbors" signal — L2 falls back to
    cold-start behavior.
    """
    # Two query shapes — keep it simple. Parameters: $1 = vector,
    # $2 = exclude_pattern_key (only when provided).
    vec = list(query_vec)
    base_select = f"""
        SELECT pattern_key, incident_type, check_type, runbook_id,
               source_sites, source_occurrences, source_text,
               cosine_similarity_arr(embedding, $1::float4[]) AS similarity
        FROM pattern_embeddings
        WHERE embedding_method = '{EMBEDDING_METHOD}'
    """
    if exclude_pattern_key:
        sql = base_select + """
          AND pattern_key <> $2
        ORDER BY similarity DESC
        LIMIT 50
        """
        rows = await conn.fetch(sql, vec, exclude_pattern_key)
    else:
        sql = base_select + """
        ORDER BY similarity DESC
        LIMIT 50
        """
        rows = await conn.fetch(sql, vec)
    # Apply min_similarity filter + k cap client-side so we can use the
    # same query for different thresholds without re-planning.
    filtered = [dict(r) for r in rows if r["similarity"] >= min_similarity]
    return filtered[:k]


async def find_neighbors_for_incident(
    conn,
    incident_type: str,
    check_type: Optional[str] = None,
    k: int = 5,
    min_similarity: float = 0.3,
) -> List[Dict[str, Any]]:
    """Convenience wrapper — compute the embedding for an incident on the
    fly and look up its neighbors."""
    vec = compute_embedding(
        incident_type=incident_type,
        check_type=check_type,
    )
    return await find_nearest_patterns(
        conn, vec, k=k, min_similarity=min_similarity,
    )
