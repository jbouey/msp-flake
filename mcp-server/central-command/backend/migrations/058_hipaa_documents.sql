-- Migration 058: HIPAA compliance document uploads
--
-- Stores metadata for documents uploaded by clients to support their
-- HIPAA compliance modules (signed BAAs, training certs, policy manuals,
-- officer designation letters, walkthrough photos, etc.).
--
-- Actual files live in MinIO bucket 'hipaa-documents'.
-- Key format: {org_id}/{module_key}/{uuid}_{filename}

CREATE TABLE IF NOT EXISTS hipaa_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    module_key VARCHAR(50) NOT NULL,          -- policies, baas, training, ir_plan, contingency, physical, workforce, officers
    file_name VARCHAR(255) NOT NULL,
    mime_type VARCHAR(100) NOT NULL,
    size_bytes BIGINT NOT NULL,
    minio_key VARCHAR(512) NOT NULL,          -- full key in hipaa-documents bucket
    description TEXT,
    uploaded_by UUID,                         -- client_portal_users.id
    uploaded_by_email VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    deleted_at TIMESTAMPTZ                    -- soft delete (file kept in MinIO for retention)
);

-- Fast lookup for listing documents per org + module
CREATE INDEX IF NOT EXISTS idx_hipaa_docs_org_module
    ON hipaa_documents(org_id, module_key)
    WHERE deleted_at IS NULL;

COMMENT ON TABLE hipaa_documents IS
    'Client-uploaded compliance documents (BAAs, certs, letters, photos). Files in MinIO hipaa-documents bucket.';

-- Rollback:
-- DROP TABLE IF EXISTS hipaa_documents;
