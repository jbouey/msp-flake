-- Migration 048: HIPAA Administrative Compliance Modules
-- Creates tables for 10 HIPAA gap-closing modules:
-- SRA, Policies, Training, BAAs, IR Plan + Breach Log,
-- Contingency Plans, Workforce Access, Physical Safeguards,
-- Officer Designation, Gap Analysis

-- ============================================================
-- 1. SRA (Security Risk Assessment)
-- ============================================================
CREATE TABLE IF NOT EXISTS hipaa_sra_assessments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES client_orgs(id),
    title TEXT NOT NULL DEFAULT 'Annual Security Risk Assessment',
    status TEXT NOT NULL DEFAULT 'in_progress',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    overall_risk_score NUMERIC(5,2),
    total_questions INTEGER DEFAULT 0,
    answered_questions INTEGER DEFAULT 0,
    findings_count INTEGER DEFAULT 0,
    created_by UUID REFERENCES client_users(id),
    evidence_bundle_id TEXT
);

CREATE TABLE IF NOT EXISTS hipaa_sra_responses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id UUID NOT NULL REFERENCES hipaa_sra_assessments(id) ON DELETE CASCADE,
    question_key TEXT NOT NULL,
    category TEXT NOT NULL,
    hipaa_reference TEXT NOT NULL,
    response TEXT,
    risk_level TEXT DEFAULT 'not_assessed',
    remediation_plan TEXT,
    remediation_due DATE,
    remediation_status TEXT DEFAULT 'open',
    notes TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(assessment_id, question_key)
);

-- ============================================================
-- 2. Policy Documents
-- ============================================================
CREATE TABLE IF NOT EXISTS hipaa_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES client_orgs(id),
    policy_key TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'draft',
    hipaa_references TEXT[] DEFAULT '{}',
    approved_by TEXT,
    approved_at TIMESTAMPTZ,
    effective_date DATE,
    review_due DATE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    evidence_bundle_id TEXT,
    UNIQUE(org_id, policy_key, version)
);

-- ============================================================
-- 3. Training Compliance
-- ============================================================
CREATE TABLE IF NOT EXISTS hipaa_training_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES client_orgs(id),
    employee_name TEXT NOT NULL,
    employee_email TEXT,
    employee_role TEXT,
    training_type TEXT NOT NULL,
    training_topic TEXT NOT NULL,
    completed_date DATE,
    due_date DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    certificate_ref TEXT,
    trainer TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 4. BAA (Business Associate Agreements)
-- ============================================================
CREATE TABLE IF NOT EXISTS hipaa_baas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES client_orgs(id),
    associate_name TEXT NOT NULL,
    associate_type TEXT NOT NULL,
    contact_name TEXT,
    contact_email TEXT,
    signed_date DATE,
    expiry_date DATE,
    auto_renew BOOLEAN DEFAULT false,
    status TEXT NOT NULL DEFAULT 'pending',
    phi_types TEXT[],
    services_description TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 5. Incident Response Plan + Breach Log
-- ============================================================
CREATE TABLE IF NOT EXISTS hipaa_ir_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES client_orgs(id),
    title TEXT NOT NULL DEFAULT 'Incident Response Plan',
    content TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'draft',
    last_tested DATE,
    next_review DATE,
    approved_by TEXT,
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS hipaa_breach_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES client_orgs(id),
    incident_date DATE NOT NULL,
    discovered_date DATE NOT NULL,
    description TEXT NOT NULL,
    phi_involved BOOLEAN DEFAULT false,
    individuals_affected INTEGER DEFAULT 0,
    breach_type TEXT,
    notification_required BOOLEAN DEFAULT false,
    hhs_notified BOOLEAN DEFAULT false,
    hhs_notified_date DATE,
    individuals_notified BOOLEAN DEFAULT false,
    individuals_notified_date DATE,
    root_cause TEXT,
    corrective_actions TEXT,
    status TEXT NOT NULL DEFAULT 'investigating',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 6. Contingency / DR Plan
-- ============================================================
CREATE TABLE IF NOT EXISTS hipaa_contingency_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES client_orgs(id),
    plan_type TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    rto_hours INTEGER,
    rpo_hours INTEGER,
    last_tested DATE,
    next_test_due DATE,
    test_result TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    approved_by TEXT,
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 7. Workforce Access Lifecycle
-- ============================================================
CREATE TABLE IF NOT EXISTS hipaa_workforce_access (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES client_orgs(id),
    employee_name TEXT NOT NULL,
    employee_role TEXT,
    department TEXT,
    access_level TEXT NOT NULL,
    systems TEXT[] DEFAULT '{}',
    start_date DATE NOT NULL,
    termination_date DATE,
    access_revoked_date DATE,
    status TEXT NOT NULL DEFAULT 'active',
    supervisor TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 8. Physical Safeguards
-- ============================================================
CREATE TABLE IF NOT EXISTS hipaa_physical_safeguards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES client_orgs(id),
    category TEXT NOT NULL,
    item_key TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'not_assessed',
    hipaa_reference TEXT,
    notes TEXT,
    last_assessed DATE,
    assessed_by TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(org_id, category, item_key)
);

-- ============================================================
-- 9. Privacy/Security Officer Designation
-- ============================================================
CREATE TABLE IF NOT EXISTS hipaa_officers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES client_orgs(id),
    role_type TEXT NOT NULL,
    name TEXT NOT NULL,
    title TEXT,
    email TEXT,
    phone TEXT,
    appointed_date DATE NOT NULL,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(org_id, role_type)
);

-- ============================================================
-- 10. Gap Analysis / Questionnaire Responses
-- ============================================================
CREATE TABLE IF NOT EXISTS hipaa_gap_responses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES client_orgs(id),
    questionnaire_version TEXT NOT NULL DEFAULT 'v1',
    section TEXT NOT NULL,
    question_key TEXT NOT NULL,
    hipaa_reference TEXT NOT NULL,
    response TEXT,
    maturity_level INTEGER DEFAULT 0,
    notes TEXT,
    evidence_ref TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(org_id, questionnaire_version, question_key)
);

-- ============================================================
-- Indexes
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_sra_org ON hipaa_sra_assessments(org_id);
CREATE INDEX IF NOT EXISTS idx_sra_responses_assessment ON hipaa_sra_responses(assessment_id);
CREATE INDEX IF NOT EXISTS idx_policies_org ON hipaa_policies(org_id, status);
CREATE INDEX IF NOT EXISTS idx_training_org ON hipaa_training_records(org_id, status);
CREATE INDEX IF NOT EXISTS idx_baas_org ON hipaa_baas(org_id, status);
CREATE INDEX IF NOT EXISTS idx_breach_org ON hipaa_breach_log(org_id);
CREATE INDEX IF NOT EXISTS idx_contingency_org ON hipaa_contingency_plans(org_id);
CREATE INDEX IF NOT EXISTS idx_workforce_org ON hipaa_workforce_access(org_id, status);
CREATE INDEX IF NOT EXISTS idx_physical_org ON hipaa_physical_safeguards(org_id);
CREATE INDEX IF NOT EXISTS idx_officers_org ON hipaa_officers(org_id);
CREATE INDEX IF NOT EXISTS idx_gap_org ON hipaa_gap_responses(org_id);

-- Record migration
INSERT INTO schema_migrations (version, description)
VALUES (48, 'HIPAA administrative compliance modules')
ON CONFLICT (version) DO NOTHING;
