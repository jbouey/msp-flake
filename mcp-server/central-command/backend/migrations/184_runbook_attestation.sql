-- Migration 184 — Runbook attestation + class-level consent (Phase 1).
--
-- Landing the SCHEMA + SEEDS only. Pre-execution checks + UI + enforce
-- wire-up come in later phases per docs/migration-184-runbook-attestation-spec.md.
--
-- Why: today we prove WHAT happened (Ed25519 bundle + hash chain +
-- OTS). We don't prove the customer legally authorized the CATEGORY
-- of action. Class-level consent (one sign per class, not per click)
-- closes that gap. Positioning: "Cryptographically consented.
-- Revocable in real time. Attributable to the signer."

BEGIN;

-- ── 1. runbook_classes ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS runbook_classes (
    class_id          TEXT PRIMARY KEY,
    display_name      TEXT NOT NULL,
    description       TEXT NOT NULL,
    risk_level        TEXT NOT NULL CHECK (risk_level IN ('low','medium','high')),
    hipaa_controls    TEXT[] NOT NULL DEFAULT '{}',
    example_actions   JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

-- ── 2. runbook_registry ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS runbook_registry (
    runbook_id      TEXT NOT NULL,
    version         INT NOT NULL,
    class_id        TEXT NOT NULL REFERENCES runbook_classes(class_id),
    script_sha256   TEXT NOT NULL,
    signed_by       TEXT NOT NULL,
    signed_at       TIMESTAMPTZ DEFAULT NOW(),
    deprecated_at   TIMESTAMPTZ,
    supersedes      TEXT,
    PRIMARY KEY (runbook_id, version)
);
CREATE INDEX IF NOT EXISTS ix_runbook_registry_active
    ON runbook_registry(runbook_id) WHERE deprecated_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_runbook_registry_class
    ON runbook_registry(class_id);

-- ── 3. runbook_class_consent ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS runbook_class_consent (
    consent_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id              TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    class_id             TEXT NOT NULL REFERENCES runbook_classes(class_id),
    consented_by_email   TEXT NOT NULL,
    consented_at         TIMESTAMPTZ DEFAULT NOW(),
    client_signature     BYTEA NOT NULL,
    client_pubkey        BYTEA NOT NULL,
    consent_ttl_days     INT DEFAULT 365,
    revoked_at           TIMESTAMPTZ,
    revocation_reason    TEXT,
    evidence_bundle_id   TEXT NOT NULL,
    -- Only ONE active (non-revoked) consent per (site, class). Postgres
    -- treats NULLs as distinct so the UNIQUE works: once revoked_at is
    -- set the row no longer blocks a fresh consent.
    UNIQUE (site_id, class_id, revoked_at)
);
CREATE INDEX IF NOT EXISTS ix_consent_active
    ON runbook_class_consent(site_id, class_id) WHERE revoked_at IS NULL;

-- ── 4. consent_amendments ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS consent_amendments (
    amendment_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    consent_id           UUID NOT NULL REFERENCES runbook_class_consent(consent_id) ON DELETE CASCADE,
    change_type          TEXT NOT NULL CHECK (change_type IN ('scope_expand','scope_reduce','revoke','reinstate')),
    diff_json            JSONB NOT NULL,
    requested_by_email   TEXT NOT NULL,
    approved_by_email    TEXT NOT NULL,
    approved_at          TIMESTAMPTZ DEFAULT NOW(),
    evidence_bundle_id   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_consent_amendments_consent
    ON consent_amendments(consent_id);

-- ── 5. Extend promoted_rule_events.event_type CHECK ───────────────
-- Per CLAUDE.md spine principle: any new event_type must extend the
-- CHECK in lockstep with code. Only the event-type-set changes here;
-- lifecycle_state machine is untouched.
-- Drop-and-recreate pattern is safe because the table is append-only.
DO $$
DECLARE
    current_check text;
BEGIN
    SELECT pg_get_constraintdef(c.oid)
      INTO current_check
      FROM pg_constraint c
      JOIN pg_class t ON t.oid = c.conrelid
     WHERE t.relname = 'promoted_rule_events'
       AND c.conname = 'promoted_rule_events_event_type_check';

    IF current_check IS NOT NULL
       AND position('runbook.consented' in current_check) = 0 THEN
        ALTER TABLE promoted_rule_events
            DROP CONSTRAINT promoted_rule_events_event_type_check;
        ALTER TABLE promoted_rule_events
            ADD CONSTRAINT promoted_rule_events_event_type_check
            CHECK (event_type IN (
                -- lifecycle events (spine — migration 181)
                'proposed','shadow_entered','approved','rollout_started','rollout_acked',
                'canary_failed','auto_disabled','regime_warning','operator_re_enabled',
                'operator_acknowledged','graduated','retired','zombie_site','regime_absolute_low',
                'stage_change','reviewer_note',
                -- consent events (migration 184)
                'runbook.consented','runbook.amended','runbook.revoked','runbook.executed_with_consent'
            ));
    END IF;
END $$;

-- ── 6. Seed runbook_classes ────────────────────────────────────────
-- Twelve classes covering the current L1/L2 runbook universe. Partners
-- request consent per-class; practice managers sign per-class. New
-- runbooks register under an existing class when possible; a new
-- class requires an explicit partner + legal sign-off.
INSERT INTO runbook_classes (class_id, display_name, description, risk_level, hipaa_controls, example_actions)
VALUES
    ('SERVICE_RESTART',
     'Restart system services',
     'Restart a failed Windows/Linux service (DNS, WMI, Defender, etc). No config change; only a state reset.',
     'low',
     ARRAY['164.308(a)(1)(ii)(D)'],
     '["restart Dnscache","restart Windows Defender","restart systemd-resolved"]'::jsonb),
    ('DNS_ROTATION',
     'DNS configuration changes',
     'Rotate DNS servers, update hosts file entries, or refresh DNS configuration for split-horizon deployments.',
     'medium',
     ARRAY['164.312(a)(1)','164.312(e)(1)'],
     '["append to /etc/hosts","update DNS server list","flush dnscache"]'::jsonb),
    ('FIREWALL_RULE',
     'Firewall rule changes',
     'Add, remove, or modify Windows/Linux firewall rules to close observed exposures.',
     'medium',
     ARRAY['164.312(c)(1)','164.308(a)(1)(ii)(D)'],
     '["block inbound SMB from WAN","enable Windows Defender Firewall","drop rule for TCP 3389 from !RFC1918"]'::jsonb),
    ('CERT_ROTATION',
     'Certificate renewal',
     'Renew or rotate x509 certificates on appliances and managed services before expiry.',
     'medium',
     ARRAY['164.312(e)(1)','164.312(e)(2)(ii)'],
     '["rotate Caddy TLS cert","renew internal CA","trigger acme-challenge"]'::jsonb),
    ('BACKUP_RETRY',
     'Backup retries + verification',
     'Re-run a failed backup job, verify backup integrity hashes, or promote a stale backup set.',
     'low',
     ARRAY['164.308(a)(7)(ii)(A)','164.308(a)(7)(ii)(B)','164.310(d)(2)(iv)'],
     '["retry ShadowProtect","verify restore chain","promote secondary backup set"]'::jsonb),
    ('PATCH_INSTALL',
     'Security patch installation',
     'Install vetted OS or vendor security patches on approved-deploy windows.',
     'high',
     ARRAY['164.308(a)(5)(ii)(B)'],
     '["install Windows CVE-YYYY-NNNN patch","apt upgrade openssl","yum update kernel"]'::jsonb),
    ('GROUP_POLICY_RESET',
     'Group Policy / config baseline restore',
     'Restore Windows GPO or Linux config to a pinned baseline when drift is detected.',
     'medium',
     ARRAY['164.308(a)(1)(ii)(D)','164.312(a)(1)'],
     '["gpupdate /force with pinned template","restore /etc/security/limits.conf"]'::jsonb),
    ('DEFENDER_EXCLUSION',
     'Endpoint protection exclusion changes',
     'Add or remove a Defender / EDR / AV exclusion path. Flagged high-risk because malicious use creates blind spots.',
     'high',
     ARRAY['164.308(a)(5)(ii)(B)','164.312(b)'],
     '["remove rogue exclusion","audit Defender exclusions","add approved backup staging path"]'::jsonb),
    ('PERSISTENCE_CLEANUP',
     'Remove unauthorized persistence',
     'Remove scheduled tasks, services, registry run keys, and WMI subscriptions that match indicator-of-compromise patterns.',
     'high',
     ARRAY['164.308(a)(6)','164.308(a)(1)(ii)(D)','164.312(b)'],
     '["delete rogue scheduled task","remove unauthorized service","clean WMI persistence subscription"]'::jsonb),
    ('ACCOUNT_DISABLE',
     'Disable/revoke user accounts',
     'Disable AD/local accounts based on policy violations, known indicators, or offboarding signals.',
     'medium',
     ARRAY['164.308(a)(3)(ii)(B)','164.308(a)(4)(ii)(B)'],
     '["disable AD user","lock local account","revoke SSH key from authorized_keys"]'::jsonb),
    ('LOG_ARCHIVE',
     'Log rotation + archive',
     'Rotate, compress, and archive event logs to long-term storage per retention policy.',
     'low',
     ARRAY['164.308(a)(1)(ii)(D)','164.312(b)','164.316(b)(2)(i)'],
     '["archive Security event log","rotate /var/log/auth.log","upload to S3 lifecycle bucket"]'::jsonb),
    ('CONFIG_SYNC',
     'Configuration sync to policy',
     'Push the current site policy to the appliance (L1 rules, check schedules, runbook mappings).',
     'low',
     ARRAY['164.308(a)(1)(ii)(D)'],
     '["sync L1 rules from Central Command","push check schedule update","reload promoted rule set"]'::jsonb)
ON CONFLICT (class_id) DO NOTHING;

COMMIT;
