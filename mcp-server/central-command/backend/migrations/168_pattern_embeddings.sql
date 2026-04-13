-- Migration 168: Pattern embeddings table (Phase 7)
--
-- Stores a 128-d vector representation per unique (incident_type,
-- check_type, runbook_id) pattern. L2 planner uses this to find the
-- 5 nearest historical patterns and inject them as few-shot context,
-- warm-starting novel incidents from their statistical cousins.
--
-- We use `float4[]` (Postgres array type) as the storage — not pgvector
-- — because pgvector isn't installed on the current Postgres image.
-- At current scale (< 100 unique patterns) a sequential scan with a
-- cosine-similarity plpgsql function is sub-millisecond. When scale
-- demands (>1K patterns) we migrate to pgvector + HNSW index:
--
--    CREATE EXTENSION vector;
--    ALTER TABLE pattern_embeddings ALTER COLUMN embedding TYPE vector(128);
--    CREATE INDEX ... USING hnsw (embedding vector_cosine_ops);
--
-- The cosine_similarity() plpgsql function below is independent of the
-- storage type, so the L2 planner's neighbor-lookup SQL doesn't change.

BEGIN;

CREATE TABLE IF NOT EXISTS pattern_embeddings (
    id                 BIGSERIAL    PRIMARY KEY,
    pattern_key        VARCHAR(255) NOT NULL UNIQUE,
    incident_type      VARCHAR(100),
    check_type         VARCHAR(100),
    runbook_id         VARCHAR(255),
    embedding          FLOAT4[]     NOT NULL,
    embedding_method   VARCHAR(32)  NOT NULL DEFAULT 'hash-v1',
                      -- When we later plug in sentence-transformers
                      -- or an embedding API, bump this to 'st-v1',
                      -- 'voyage-v1', etc. so lookup can filter by
                      -- method when mixed generations coexist.
    source_text        TEXT,
    source_sites       INTEGER      NOT NULL DEFAULT 0,
    source_occurrences INTEGER      NOT NULL DEFAULT 0,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pattern_embeddings_method
    ON pattern_embeddings (embedding_method);

CREATE INDEX IF NOT EXISTS idx_pattern_embeddings_incident_type
    ON pattern_embeddings (incident_type)
    WHERE incident_type IS NOT NULL;

-- Cosine similarity plpgsql function. Handles NULL / zero-magnitude
-- defensively. Returns 0 when either input is zero-norm.
CREATE OR REPLACE FUNCTION cosine_similarity_arr(a FLOAT4[], b FLOAT4[])
RETURNS FLOAT8 LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
DECLARE
    i INTEGER;
    n INTEGER;
    dot FLOAT8 := 0;
    na  FLOAT8 := 0;
    nb  FLOAT8 := 0;
BEGIN
    IF a IS NULL OR b IS NULL THEN
        RETURN 0;
    END IF;
    n := LEAST(array_length(a, 1), array_length(b, 1));
    IF n IS NULL OR n = 0 THEN
        RETURN 0;
    END IF;
    FOR i IN 1..n LOOP
        dot := dot + a[i] * b[i];
        na  := na  + a[i] * a[i];
        nb  := nb  + b[i] * b[i];
    END LOOP;
    IF na = 0 OR nb = 0 THEN
        RETURN 0;
    END IF;
    RETURN dot / (sqrt(na) * sqrt(nb));
END;
$$;

COMMIT;
