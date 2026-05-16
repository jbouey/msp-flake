# HIPAA Compliance Framework

> **Last verified:** 2026-05-16 (Session 220 doc refresh).
>
> This doc captures architectural posture (PHI-free-by-design,
> metadata-only, scrubbers-at-edge) at a foundational level AND
> the current Counsel-driven governance overlay. Where this doc
> conflicts with the canonical auditor packet, the packet wins.
>
> **Canonical current authority:** `~/Downloads/OsirisCare_Owners_
> Manual_and_Auditor_Packet.pdf` (Part 2 — Auditor's Need-to-Know).
> Counsel's 7 Hard Rules (CLAUDE.md, 2026-05-13) are gold authority
> over this doc where they conflict.

<!-- updated 2026-05-16 — Session-220 doc refresh -->

## Counsel's 7 Hard Rules (2026-05-13, gold authority)

Outside HIPAA counsel laid these down 2026-05-13 as first-pass filter
on every design / Gate A / commit:

1. **No non-canonical metric leaves the building** (canonical registry — see ARCHITECTURE.md).
2. **No raw PHI crosses the appliance boundary** (compiler rule, not posture preference).
3. **No privileged action without attested chain of custody** (4-element chain, mig 175 + 305).
4. **No segmentation design that creates silent orphan coverage** (orphan = sev1, not warning).
5. **No stale document may outrank the current posture overlay.**
6. **No legal/BAA state may live only in human memory** (BAA-gating triad — see below).
7. **No unauthenticated channel gets meaningful context by default** (opaque emails, RT21).

Plus (8) subprocessors by actual data flow, (9) determinism/provenance, (10) no clinical authority implication.

Full enumeration in CLAUDE.md "Counsel's 7 Hard Rules" + worked examples in `.claude/projects/.../memory/feedback_enterprise_counsel_seven_rules.md`.

## Legal Positioning

OsirisCare is a **Business Associate (BA)** — but only for *operations*, not for *treatment or records*.

The MCP + LLM substrate *supports* compliance (**HIPAA §164.308(a)(1)(ii)(D)**: "Information system activity review") by scanning logs for evidence of compliance, not by touching medical charts or patient identifiers.

**Service Scope:** "HIPAA operational safeguard verification" — NOT "clinical data processing".

**Subcontracting chain:** Clinic (CE) → MSP partner (BA) → OsirisCare (Subcontractor BA). Per-customer e-signed BAA in `baa_signatures` append-only table (mig 312 — acknowledgment-only flag; mig 321 — `client_org_id` FK).

### Master BAA v1.0-INTERIM (2026-05-13, formal HIPAA-complete instrument)

Master BAA v1.0-INTERIM shipped 2026-05-13 as the formal §164.504(e)-complete instrument. **Earlier click-through "acknowledgments-of-intent" had a term-certainty gap** (memorialized in `project_no_master_baa_contract.md` — the most urgent legal finding). v1.0-INTERIM does NOT over-claim: every "signed-claim" scopes to evidence bundles, NOT to per-heartbeat verification.

**MASTER_BAA v2.0 target: 2026-06-03.** Per Task #70, drafting is gated on engineering-evidence preconditions in `docs/legal/v2.0-hardening-prerequisites.md`:

- **PRE-1:** no per-event / per-heartbeat / "continuously verified" language unless D1 heartbeat-signature backend verification has a ≥7-day clean soak (≥99% `signature_valid IS TRUE` per pubkeyed appliance, zero open `daemon_heartbeat_signature_{unverified,invalid,unsigned}` violations).

CI backstop `tests/test_baa_artifacts_no_heartbeat_verification_overclaim.py` (baseline 0) pins the scoping.

### BAA Enforcement Triad (Counsel Rule 6 — Session 220 #52 + #91 + #92)

Every CE-mutating workflow MUST be runtime-gated against an active per-org BAA OR explicitly registered as a `_DEFERRED_WORKFLOWS` carve-out with counsel-traceable justification:

**List 1** — `baa_enforcement.BAA_GATED_WORKFLOWS` (active): `owner_transfer`, `cross_org_relocate`, `evidence_export`. (Deferred: `partner_admin_transfer` — zero PHI flow per §164.504(e); `ingest` — pending Task #37.)

**List 2** — enforcing callsites: `require_active_baa(workflow)`, `enforce_or_log_admin_bypass(...)`, `check_baa_for_evidence_export(...)`.

**List 3** — substrate invariant `sensitive_workflow_advanced_without_baa` (sev1) scans state-machine tables + `admin_audit_log auditor_kit_download` rows for 30-day BAA gaps.

CI gate `tests/test_baa_gated_workflows_lockstep.py` pins List 1 ↔ List 2.

## §164.504(e) Framing (current)

Counsel adversarial review 2026-05-06 **retired** any "same-BA inapplicability" framing as attackable. Current framing: **permitted-use scope under EACH BAA, regardless of vendor identity.** Specifically applied in `cross_org_site_relocate.py` (BAA-counsel-pending feature flag).

## §164.528 Framing (current)

Counsel correction 2026-05-06: legal test for the disclosure accounting is **substantive completeness + retrievability**, NOT cryptographic immutability. The Ed25519 / hash-chain / OTS chain is integrity *hardening* on top of the substantive record. The substantive accounting lives in `admin_audit_log` + state-machine request rows + `auditor_kit_download` audit rows.

**Auditor kit framing:** `audit-supportive technical evidence`, NOT a §164.528 disclosure accounting. The README + ClientReports + PracticeHomeCard ship IDENTICAL §164.528 disclaimer copy.

## §164.312(a)(2)(iii) Automatic Logoff

15-minute idle-session timeout wired in BOTH portal contexts; CI-pinned.

## Framework Basis

- **Published NixOS STIG** — listed in NIST's National Checklist Program
- **Deterministic builds** — reproducible device images
- **Evidence generation** — mapped to recognized control catalogs (HIPAA Security Rule)
- **Substrate Integrity Engine** — ~60 invariants every 60s catch RLS misalignment, chain gaps, partition misses, BAA gaps (see ARCHITECTURE.md "Substrate Integrity Engine")

## Compliance Strengths

- Clear scope boundary (infra-only, PHI-free-by-design)
- Deterministic flake with guardrails
- Backups/monitoring/VPN as default
- Ed25519 + OTS chain on every evidence bundle (~245K bundles, partitioned monthly per mig 138)
- Per-appliance signing keys (`site_appliances.agent_public_key`)
- Privileged-Access Chain of Custody — 4-element chain enforced at CLI + API + DB trigger
- Substrate metadata treated **conservatively as PHI-adjacent** (incidental exposure → §164.404 breach-notification flow, not normal operation)

## Data Boundary Zones

| Zone | Example Data | HIPAA Risk | Mitigation |
|------|-------------|-----------|-----------|
| **System** | syslog, SSH attempts, package hashes, backup status | Very low | Mask usernames/paths; scrub at appliance egress |
| **Application** | EHR audit logs (metadata only), access events | Moderate | Tokenize IDs, redact payload; phiscrub package (14 patterns) |
| **Data** | PHI (lab results, notes, demographics) | High | Out of substrate scope; not ingested by design (incidental → security-incident flow) |

## Practical Controls

1. **Scrubbers at appliance egress (`appliance/internal/phiscrub`)** — 14 regex patterns + 21 unit tests; runs on incident reports, evidence bundles, log entries, checkin requests, network scans, L2 planner input. See `docs/PHI_DATA_FLOW_ATTESTATION.md` for the full pattern table.
2. **Portal-layer defense-in-depth (`phi_boundary.py`)** — strips `raw_output`, `stdout`, `stderr`, `hostname`, `ip_address`, `username`, `file_path` from client/partner portal responses.
3. **Metadata-only collector** — system events only, no file contents.
4. **Access boundary** — restricted to `/var/log`, `/etc`, `/nix/store`; NOT `/data/` or EHR mounts.
5. **PHI-adjacent posture** — incidental exposure handled via §164.404 breach-notification flow.

## What Your LLM Can Do (L2 Planner)

**Allowed:**
- Parse scrubbed logs for anomalies, missed backups, failed encryption.
- Compare settings to baseline.
- Generate remediation runbooks.
- Produce evidence bundles.

**Prohibited:**
- Read or infer patient-level data.
- Suggest clinical actions.
- Aggregate PHI from logs.

**L2 audit gate (Session 219 mig 300):** `resolution_tier='L2'` MUST NOT be set without a corresponding `l2_decisions` row. Substrate invariant `l2_resolution_without_decision_record` (sev2) catches ghost-L2 orphans. Mig 300/301/302 backfilled historical orphans with `pattern_signature='L2-ORPHAN-BACKFILL-MIG-300'` and `llm_model='backfill_synthetic'`.

## Monitoring Requirements by Tier

### Tier 1: Infrastructure

| Component | What to Monitor | HIPAA Citation |
|-----------|----------------|----------------|
| Firewalls | Rule changes, blocked traffic | §164.312(a)(1) |
| VPN | Login attempts, failed auth | §164.312(a)(2)(i) |
| Server OS | Login events, privilege escalation | §164.312(b) |
| Backups | Job completion, encryption status | §164.308(a)(7)(ii)(A) |
| Encryption | Disk encryption, cert expiry | §164.312(a)(2)(iv) |
| Time Sync | NTP drift, source validation | §164.312(b) |
| Patching | Pending updates, vuln scan results | §164.308(a)(5)(ii)(B) |

### Tier 1.5: Workstation Compliance (Go Agent)

| Component | What to Check | HIPAA Citation |
|-----------|--------------|----------------|
| BitLocker | Drive encryption enabled | §164.312(a)(2)(iv) |
| Windows Defender | Real-time protection active | §164.308(a)(5)(ii)(B) |
| Patch Status | Recent updates within 30 days | §164.308(a)(5)(ii)(B) |
| Firewall | All profiles enabled | §164.312(a)(1) |
| Screen Lock | Inactivity timeout set | §164.312(a)(2)(iii) |

**Implementation:** AD-based discovery + WMI/Registry compliance checks via daemon + push from Go workstation agents.

### Tier 2: Application

| Component | What to Monitor | HIPAA Citation |
|-----------|----------------|----------------|
| EHR/EMR Access | Patient record access, break-glass | §164.312(a)(1) |
| Authentication | Failed logins, MFA events | §164.312(a)(2)(i) |
| Database | Schema changes (PHI queries excluded by design) | §164.312(b) |
| File Access | Bulk export indicators | §164.308(a)(3)(ii)(A) |
| Email | PHI transmission, encryption | §164.312(e)(1) |

### Tier 3: Business Process

| Component | What to Monitor | HIPAA Citation |
|-----------|----------------|----------------|
| User Provisioning | Account creation, termination | §164.308(a)(3)(ii)(C) |
| BA Access | Vendor access, BAA compliance | §164.308(b)(1) |
| Incident Response | Detection, containment | §164.308(a)(6) |
| Training | Awareness completion | §164.308(a)(5)(i) |
| Risk Assessment | Remediation tracking | §164.308(a)(1)(ii)(A) |

## Critical Alert Triggers

1. Multiple failed logins (>5 in 10 min)
2. PHI access outside business hours (where instrumented)
3. Bulk data export indicator
4. Firewall rule changes
5. Backup failure
6. Cert expiry within 30 days
7. Privileged account usage without chain-of-custody attestation (mig 175 trigger rejects)
8. Database schema modifications
9. New user without HR ticket
10. External email with PHI indicator

## LLM Policy File

```yaml
llm_scope:
  allowed_inputs: [syslog, journald, auditd, restic_logs, scrubbed_incident_payloads]
  prohibited_inputs: [ehr_exports, patient_data, attachments]

llm_output_actions:
  - classification
  - compliance_report
  - remediation_plan

prohibited_actions:
  - direct clinical recommendation
  - patient data synthesis
  - resolution_tier='L2' without matching l2_decisions row  # mig 300 gate
```

## BAA Template (Key Clauses)

**Scope-Limited Business Associate Agreement** — see `docs/legal/billing-phi-boundary.md` for the current text; v1.0-INTERIM is the formal HIPAA-complete instrument as of 2026-05-13.

### Metadata-Only Operations

> "BA's services are designed to operate exclusively on system metadata and configurations to assist Covered Entity in verifying HIPAA Security Rule compliance. BA's substrate is designed to be PHI-free and BA treats compliance metadata conservatively as PHI-adjacent. Any inadvertent PHI exposure shall be treated as a security incident triggering the breach notification process per 45 CFR §164.404."

### No PHI Processing (by design)

- Collect only system-level logs (syslog, journald, auditd).
- Scrub PHI patterns at appliance egress before transmission (defense-in-depth).
- Access restricted to `/var/log`, `/etc`, system directories.
- No access path to patient data directories or EHR databases — incidental exposure is a security incident.

### Sub-Processors (classified by actual data flow per Counsel Rule 8)

| Subprocessor | Role | Data Flow |
|--------------|------|-----------|
| Hetzner | VPS hosting | Scrubbed metadata only |
| Caddy | TLS termination | Encrypted in/out |
| PostgreSQL (self-hosted) | Application DB | Scrubbed metadata |
| MinIO (self-hosted) | Evidence WORM | Signed evidence bundles |
| OpenAI / Anthropic | L2 planner | Scrubbed incident payload only (phiscrub gate) |
| Stripe | Billing | PHI-free at CHECK level — see `docs/legal/billing-phi-boundary.md` |
| OpenTimestamps | OTS anchor | Bundle hashes only |
| Vault (Hetzner WG 10.100.0.3) | Transit signing (shadow mode) | Non-exportable keys |

### Breach Notification

BA notifies CE within 24 hours of:
- Inadvertent PHI access by systems.
- Unauthorized access to BA systems.
- Data breach affecting metadata.
- Security incident affecting monitoring.
- Substrate invariant `sensitive_workflow_advanced_without_baa` firing without operator-acknowledged carve-out.

## Documentation Requirements

1. **Data Boundary Diagram** — three zones with PHI-prohibited annotation (see `docs/PHI_DATA_FLOW_ATTESTATION.md`).
2. **HIPAA Mapping File** — control → runbook → evidence mapping (`check_type_registry` mig 157 is the canonical source).
3. **Statement of Scope** — attached to BAA.
4. **LLM Policy File** — allowed/prohibited actions.
5. **Exceptions File** — per-client with owner, risk, expiry.
6. **Migration Ledger** — `RESERVED_MIGRATIONS.md` pre-claim protocol.
7. **Substrate runbook stubs** — one per invariant under `backend/substrate_runbooks/`.

## Regulatory Reference

**HHS/OCR AI Guidance** — called out AI use in healthcare as vector for discrimination risk. OsirisCare's model-feature map is not just good practice — it is likely to be a regulatory conversation if the model touches care decisions. **Counsel Rule 10:** the platform never implies clinical authority. L2 planner is restricted to operational remediation; clinical inference is prohibited input/output.
