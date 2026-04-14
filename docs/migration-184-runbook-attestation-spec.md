# Migration 184 — Runbook Attestation + Class-Level Consent

**Status:** Pre-enterprise-launch P0 — NOT YET BUILT
**Scope:** 3–5 days implementation
**Dependencies:** Migration 181 (flywheel spine) must remain in enforce mode
**Authoring session:** 206 (2026-04-13)

## Why this exists

Today the platform proves *that* a remediation ran (Ed25519-signed evidence
bundle, hash-chained, OTS-anchored). It does NOT prove the customer
**legally authorized that category of action** at the time it ran. An
auditor or plaintiff can ask: "Who said you could restart their DNS
service at 3am?" — and we have a signed log of the action but not a
signed consent record tying a named customer rep to that category of
action with revocation rights.

Class-level consent (not per-runbook click-through) closes this gap.
The customer signs once for a class like `DNS-ROTATION` — every runbook
in that class inherits the authorization, revocable in real time.

Positioning: **"Cryptographically consented. Revocable in real time.
Attributable to the signer."**

## Tables (4)

### 1. `runbook_classes`

```sql
CREATE TABLE runbook_classes (
    class_id          TEXT PRIMARY KEY,          -- e.g. 'DNS-ROTATION'
    display_name      TEXT NOT NULL,
    description       TEXT NOT NULL,
    risk_level        TEXT NOT NULL CHECK (risk_level IN ('low','medium','high')),
    hipaa_controls    TEXT[] NOT NULL DEFAULT '{}',
    example_actions   JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);
```

Seed with ~12 classes covering all current L1/L2 actions (service-restart,
dns-rotation, firewall-rule, cert-rotation, backup-retry, patch-install,
group-policy-reset, defender-exclusion, persistence-cleanup,
account-disable, log-archive, config-sync).

### 2. `runbook_registry`

```sql
CREATE TABLE runbook_registry (
    runbook_id      TEXT NOT NULL,
    version         INT NOT NULL,
    class_id        TEXT NOT NULL REFERENCES runbook_classes(class_id),
    script_sha256   TEXT NOT NULL,           -- must match filesystem at exec time
    signed_by       TEXT NOT NULL,           -- named human email
    signed_at       TIMESTAMPTZ DEFAULT NOW(),
    deprecated_at   TIMESTAMPTZ,
    supersedes      TEXT,                    -- prior runbook_id (for migrations)
    PRIMARY KEY (runbook_id, version)
);
CREATE INDEX ix_runbook_registry_active
    ON runbook_registry(runbook_id) WHERE deprecated_at IS NULL;
```

Version bumps on script-body change. Script SHA is verified at execution
time — if the file on disk drifts from the registry, execution blocks.

### 3. `runbook_class_consent`

```sql
CREATE TABLE runbook_class_consent (
    consent_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id              TEXT NOT NULL REFERENCES sites(site_id),
    class_id             TEXT NOT NULL REFERENCES runbook_classes(class_id),
    consented_by_email   TEXT NOT NULL,          -- named customer rep
    consented_at         TIMESTAMPTZ DEFAULT NOW(),
    client_signature     BYTEA NOT NULL,         -- Ed25519 over deterministic payload
    client_pubkey        BYTEA NOT NULL,
    consent_ttl_days     INT DEFAULT 365,
    revoked_at           TIMESTAMPTZ,
    revocation_reason    TEXT,
    evidence_bundle_id   TEXT NOT NULL,          -- FK to compliance_bundles
    UNIQUE (site_id, class_id, revoked_at)       -- at most one active consent per class
);
```

Signed over `sha256(site_id || class_id || consented_by_email || consented_at || ttl)`.
Every state change (grant / amend / revoke / reinstate) writes a new
`compliance_bundles` row with `check_type='runbook_consent'` — inherits
the existing hash chain + OTS anchoring.

### 4. `consent_amendments`

```sql
CREATE TABLE consent_amendments (
    amendment_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    consent_id           UUID NOT NULL REFERENCES runbook_class_consent(consent_id),
    change_type          TEXT NOT NULL CHECK (change_type IN ('scope_expand','scope_reduce','revoke','reinstate')),
    diff_json            JSONB NOT NULL,
    requested_by_email   TEXT NOT NULL,          -- partner who asked
    approved_by_email    TEXT NOT NULL,          -- customer rep who signed
    approved_at          TIMESTAMPTZ DEFAULT NOW(),
    evidence_bundle_id   TEXT NOT NULL
);
```

## Ledger event types (added to `promoted_rule_events`)

- `runbook.consented` — new `runbook_class_consent` row
- `runbook.amended` — `consent_amendments` row inserted
- `runbook.revoked` — `revoked_at` populated
- `runbook.executed_with_consent` — a runbook ran, and the matching
  consent row was verified as part of the execution

The last one ties back into the spine: the orchestrator cannot mark a
`promoted_rule` as `graduated` until at least one successful
`executed_with_consent` event lands for a site in its rollout set.

## Pre-execution checks (in L1 / L2 engine + `advance_lifecycle`)

Every remediation path MUST verify all four before running:

1. `runbook_registry` has a non-deprecated row with `script_sha256`
   matching the on-disk script
2. `runbook_class_consent` exists for `(site_id, class_id)` with
   `revoked_at IS NULL`
3. `NOW() - consented_at < consent_ttl_days * INTERVAL '1 day'` — not
   expired
4. If any check fails AND `emergency=true`, execution proceeds ONLY with
   a signed `emergency_reason` attestation bundle — caller must be a
   named human, never `system`/`fleet-cli`

## Contract tests (6)

Place under `backend/tests/`:

1. **`test_runbook_fails_without_consent.py`** — insert a rule that
   matches a class with no active consent → execution rejected with
   `NO_CLASS_CONSENT`
2. **`test_runbook_fails_after_revoke.py`** — consent exists, then
   `UPDATE … SET revoked_at=NOW()` → next execution blocks with
   `CONSENT_REVOKED`
3. **`test_consent_amendment_chain.py`** — grant → amend (scope_reduce)
   → verify diff_json + old consent archived → amend (reinstate) →
   consent usable again
4. **`test_consent_signature_verifies.py`** — build deterministic
   payload, sign with test Ed25519 key, verify server accepts; mutate
   one byte → server rejects with `BAD_SIGNATURE`
5. **`test_runbook_script_sha_mismatch_blocks.py`** — registry row has
   SHA X, disk script hash is Y → execution blocks with `SCRIPT_DRIFT`
6. **`test_emergency_path_leaves_attestation.py`** — emergency=true
   with missing consent → bundle written with
   `check_type='emergency_override'` + `emergency_reason` + actor email

## UI scope (minimal for launch)

- **Client portal** — `/portal/consent` page listing all `runbook_classes`
  with status: `not requested`, `pending`, `active`, `revoked`. Sign
  button opens modal with Ed25519 signing flow (browser `crypto.subtle`).
- **Partner portal** — `/partner/site/{id}/consent` page: request
  consent (emails customer), view current consent state, view amendment
  history.
- **Operator** — `/admin/runbook-registry` — register a new runbook
  (uploads script, computes SHA, assigns class, signs).

## Rollout plan

**Phase 1 (day 1–2):** migration + tables + backfill seed classes +
unit tests for consent sign/verify

**Phase 2 (day 2–3):** pre-execution checks wired into L1/L2 engines
in shadow mode (log-only, never block). Write ledger events. Monitor
for 24h.

**Phase 3 (day 3–4):** flip to enforce for one low-risk class
(e.g., `LOG-ARCHIVE`). 24h observation. Expand to all classes.

**Phase 4 (day 4–5):** UI + runbook registry population for all ~50
existing runbooks. Contract tests in CI gate.

## Anti-goals

- **NOT** per-runbook click-through (user-fatigue — causes consent
  theater instead of real consent)
- **NOT** a new signing key — reuse site's existing Ed25519 infra
- **NOT** a new evidence table — plug into `compliance_bundles` so the
  hash chain + OTS anchoring come for free
- **NOT** retroactive — existing remediations keep their audit trail;
  this starts fresh for all new executions post-enforce-flip

## Open questions (decide before coding)

1. Consent renewal UX — email 30 days before TTL, or auto-extend with
   low-friction click? Lean toward email + explicit re-sign.
2. Who can revoke — only the original signer, or any named customer
   rep with portal access? Lean toward any rep, with audit trail.
3. Class granularity — 12 classes is the current sketch. If too coarse
   (e.g., `DNS-ROTATION` covers both add and delete), split. Easier to
   split later than to merge.

## Tracking

- TaskCreate id recorded in session state (see claude-progress.json
  session 206)
- Memory pointer: `project_migration_184_runbook_attestation.md`
- This doc: `docs/migration-184-runbook-attestation-spec.md`
