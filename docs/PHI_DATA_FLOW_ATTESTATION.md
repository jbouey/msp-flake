# PHI Data Flow Attestation — OsirisCare Platform

**Document Version:** 1.2
**Date:** 2026-05-16 (originally 2026-03-23; rev 1.1 2026-05-06; rev 1.2 2026-05-16)
**Author:** OsirisCare Engineering
**Review Schedule:** Annually or upon architectural change

<!-- updated 2026-05-16 — Session-220 doc refresh -->

> **Framing (current).** The substrate is **PHI-free by design** — it
> handles compliance metadata, not PHI — and treats that metadata
> conservatively as **PHI-adjacent**. Appliance-side scrubbing is
> defense-in-depth at the egress boundary, not a guarantee that PHI
> can never appear in any byte that ever transits. Incidental PHI
> exposure is treated as a security incident under the breach-
> notification flow per 45 CFR §164.404.
>
> Per **Counsel's Hard Rule 2** (2026-05-13): "No raw PHI crosses
> the appliance boundary — PHI-free Central Command is a compiler
> rule, not a posture preference. Every new data-emitting feature
> MUST answer at merge time: *could raw PHI cross this boundary?*
> If the answer is not a hard no, it does not ship."
>
> **Canonical current authority:** `~/Downloads/OsirisCare_Owners_
> Manual_and_Auditor_Packet.pdf` (Part 2 — Auditor's Need-to-Know,
> §2.7 PHI boundary). Read first.

## Executive Summary

OsirisCare is **designed to be PHI-free** — the substrate operates on
compliance metadata, not on PHI. Outbound data is scrubbed at the
appliance boundary before transiting to Central Command (TLS 1.3 +
WireGuard), with defense-in-depth at the portal layer
(`phi_boundary.py`). This document attests to the data-flow
architecture and scrubbing controls that support that posture; it
does not claim cryptographic certainty that no PHI byte can ever
leak.

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────┐
│                CLIENT SITE (PHI Zone)                │
│                                                     │
│  [EHR] [PACS] [Medical Devices] [Workstations]     │
│         │           │              │                │
│         └───────────┼──────────────┘                │
│                     │ (LAN scans / WMI / Registry)  │
│              ┌──────┴──────┐                        │
│              │  APPLIANCE   │                        │
│              │              │                        │
│              │ ┌──────────┐ │                        │
│              │ │PHI SCRUB │ │  ← ALL egress scrubbed │
│              │ │14 regex  │ │     (21 unit tests)    │
│              │ └────┬─────┘ │                        │
│              │      │       │                        │
│              │ Ed25519 sign │  ← per-appliance key   │
│              │ heartbeats + │     (mig 313 D1)       │
│              │ evidence     │                        │
│              └──────┼───────┘                        │
│                     │                                │
└─────────────────────┼────────────────────────────────┘
                      │ TLS 1.3 + WireGuard
                      │ (scrubbed metadata only)
┌─────────────────────┼────────────────────────────────┐
│         CENTRAL COMMAND (PHI-Free Zone)              │
│                     │                                │
│  ┌─────────┐  ┌─────┴────┐  ┌──────────┐           │
│  │Dashboard│  │ API/DB   │  │  MinIO   │           │
│  │(portals)│  │(postgres)│  │ (WORM)   │           │
│  │+phi_    │  │+PgBouncer│  │+OTS      │           │
│  │boundary │  │+RLS      │  │ anchored │           │
│  └─────────┘  └──────────┘  └──────────┘           │
│                                                     │
│  Substrate Integrity Engine: ~60 invariants / 60s   │
│  Canonical metric registry (Counsel Rule 1)         │
│  BAA enforcement triad (Counsel Rule 6)             │
└─────────────────────────────────────────────────────┘
```

## Scrubbing Controls (phiscrub package)

The `phiscrub` package runs on the appliance and scrubs ALL outbound data before transmission. The following patterns are detected and replaced with `[REDACTED-{hash}]` (`hash_redacted=True`):

| Pattern | Regex | Example |
|---------|-------|---------|
| SSN | `\d{3}[-\s]?\d{2}[-\s]?\d{4}` | 123-45-6789 |
| Medical Record Number | `MRN[:\s#]*\d{4,12}` | MRN: 123456789 |
| Patient ID | `patient[_\s]?id[:\s#]*[A-Za-z0-9\-]{3,20}` | patient_id: ABC-123 |
| Phone Number | `(\d{3})\s*\d{3}[-.]?\d{4}` | (555) 123-4567 |
| Email Address | `[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}` | john@hospital.org |
| Credit Card | `(\d{4}[-\s]?){3}\d{4}` | 4111-2222-3333-4444 |
| Date of Birth | `DOB[:\s]*\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}` | DOB: 03/15/1980 |
| Street Address | 5-digit + street + suffix | 123 Main Street |
| ZIP+4 | `\d{5}-\d{4}` | 18651-2330 |
| Account Number | `account[:\s#]*\d{4,20}` | Account: 123456 |
| Insurance ID | `insurance.*id[:\s]*[A-Za-z0-9\-]{4,20}` | Insurance ID: XYZ-789 |
| Medicare | `medicare[:\s#]*[A-Za-z0-9]{4,}` | Medicare: 1EG4-TE5-MK72 |
| Patient Hostname | `PATIENT\|ROOM\|BED\|WARD\|DR\.\|MR\.\|MS\.` | PATIENT_ROOM_203 |
| PHI File Path | `/patient/\|/ehr/\|/medical/` | /data/patient/123/ |

14 patterns, 21 unit tests at `appliance/internal/phiscrub/`.

## Egress Points Scrubbed

| Egress Path | Scrubbed Fields | Applied In |
|-------------|----------------|------------|
| Incident reports | hostname, expected, actual | incident_reporter.go |
| Evidence bundles | host names, drift finding values | submitter.go |
| Log entries | every message field | shipper.go |
| Checkin requests | hostname, discovery results | phonehome.go, daemon.go |
| Signed heartbeats (D1) | hostname surface in payload | daemon.go (heartbeat path) |
| Network scan results | hostname, expected, actual, details | netscan.go |
| L2 planner input | all text sent to LLM | phi_scrubber.go |

**`journal_api.py` upload audit (Session 219 fix):** `JOURNAL_UPLOAD_UNSCRUBBED` was firing 1×/3-5min pre-fix due to a `jsonb_build_object($N, ...)` cast-inference failure that silently dropped the audit row. Single-callsite fix added `::text` cast — same class as the auth.py + execute_with_retry rule. **All `jsonb_build_object($N, ...)` params now require explicit `::text` / `::int` / `::uuid` casts.**

## Cross-Boundary Workflows (post-2026-05-06)

Three workflows EXIST that move identifying state across an org boundary. **All run opaque-by-default in any unauthenticated channel** per Counsel Rule 7 + Task #42 (Session 218):

| Workflow | Module | Email Mode | BAA Gate |
|----------|--------|------------|----------|
| Client-owner transfer | `client_owner_transfer.py` | Opaque (subject = reference-id only, body redirects to portal) | `owner_transfer` (List 1, runtime-gated) |
| Client-user email rename | `client_user_email_rename.py` | Opaque (`_send_dual_notification`) | n/a — user-scope |
| Cross-org site relocate | `cross_org_site_relocate.py` | Opaque (no clinic/org names in SMTP) | `cross_org_relocate` (List 1, dual-admin counsel-pending) |
| Partner-admin transfer | `partner_admin_transfer.py` | Opaque | Deferred — zero PHI flow per §164.504(e), Task #90 |
| Auditor kit | `evidence_chain.py` | n/a (download) | `evidence_export` (List 1, method-aware) |

**Opaque-to-email ≠ opaque-to-audit.** `admin_audit_log` still captures `actor_kind`, `org_name`, full chain attribution. Subjects are static or `transfer_id[:8]` only; bodies redirect to authenticated portal. CI gate: `tests/test_email_opacity_harmonized.py` (8 gates).

## What is NOT Scrubbed (By Design)

These infrastructure identifiers are intentionally preserved. They are not PHI — they identify devices, software, and compliance state, not patients. Safe Harbor (45 CFR §164.514(b)(2)) is a de-identification standard for patient-record data sets and is **not** the rationale here; the rationale is that infrastructure identifiers fall outside the §164.501 PHI definition entirely:

- IP addresses (network topology)
- MAC addresses (device identification)
- Site IDs (operational routing)
- Agent versions (fleet management)
- Check types (compliance categorization)
- HIPAA control references (framework mapping)
- WireGuard public keys (cryptographic identity)
- Appliance heartbeat signatures (Ed25519, per-appliance public keys)

> **Caveat.** An IP address can become PHI in combination with patient context (e.g. a workstation IP tied by other records to a specific patient encounter). On the OsirisCare substrate the IP is never combined with patient context — there is no patient context on this side of the boundary.

**Partner-facing customer endpoint field allowlist (RT33 P2 Carol veto):** `/api/client/appliances` and `/api/partners/me/appliances` exclude `mac` / `ip` / `daemon_health` from the customer-portal surface (Layer-2 leak prevention). Admin endpoint retains full fields.

## Portal-Layer Defense in Depth

Even if scrubbing failed at the appliance layer, the portal boundary provides additional protection:

- **`phi_boundary.py`** strips `raw_output`, `stdout`, `stderr`, `hostname`, `ip_address`, `username`, `file_path` from all client/partner portal responses.
- **`org_connection` RLS (mig 278, Session 217):** every site-RLS table read by client_portal MUST have a parallel `tenant_org_isolation` policy via `rls_site_belongs_to_current_org(site_id::text)`. CI gate: `tests/test_org_scoped_rls_policies.py`. Pre-fix the customer saw 0 bundles for a 155K-row org (silent for ~months on `compliance_bundles`).
- **Portal endpoints query `site_appliances` directly, NOT the rollup MV (RT33 P2 Steve veto):** PG materialized views don't inherit base-table RLS. The `appliance_status_rollup` MV is faster but reading it bypasses `tenant_org_isolation`.
- **`site_appliances` + `sites` filters (RT33 P1):** every portal query MUST filter `sa.deleted_at IS NULL` ON the JOIN line + `s.status != 'inactive'` (CI gate: `tests/test_client_portal_filters_soft_deletes.py`). Bug 1 close-out (2026-05-15) drove the ratchet 81→0.
- **Admin dashboard** shows scrubbed data (not raw).
- **Evidence check details** sanitized before portal display.

## BUG 1 Close-Out (2026-05-15)

Soft-delete enforcement on `site_appliances` reached ratchet **0** at Session 220 close-out — every portal query JOINing `site_appliances` filters `sa.deleted_at IS NULL` on the JOIN line. Phantom-appliance leak class structurally closed.

## Attestation

This document attests that:

1. **PHI scrubbing** is applied at every customer-data egress point on the appliance enumerated in the Egress Points table (`incident_reporter.go`, `submitter.go`, `shipper.go`, `phonehome.go`, `daemon.go`, `netscan.go`, `phi_scrubber.go`).
2. **Central Command is designed to be PHI-free** — the substrate operates on compliance metadata, treated conservatively as PHI-adjacent. The portal `phi_boundary.py` strips raw_output, stdout, stderr, hostname, ip_address, username, file_path from client/partner-facing responses as defense-in-depth.
3. **The customer–OsirisCare relationship** is governed by a per-customer e-signed BAA (OsirisCare as Business Associate to the customer Covered Entity); see `docs/legal/billing-phi-boundary.md`, the `baa_signatures` append-only table, and the Master BAA v1.0-INTERIM (2026-05-13) — the formal HIPAA-complete instrument.
4. **BAA enforcement runtime** — three CE-mutating workflows (`owner_transfer`, `cross_org_relocate`, `evidence_export`) are runtime-gated via `baa_enforcement.require_active_baa(workflow)`; a 30-day substrate invariant (`sensitive_workflow_advanced_without_baa`, sev1) scans for gaps in the audit-log.
5. **Cross-boundary workflows** ship opaque-by-default in any unauthenticated channel (subjects = reference-id only, bodies redirect to authenticated portal). `tests/test_email_opacity_harmonized.py` is the static gate.
6. **The scrubbing patterns** target the categories most likely to appear in operational telemetry. They do **not** constitute a complete §164.514(b)(2) Safe Harbor de-identification pipeline (different control, different purpose).
7. **The scrubbing code** (`appliance/internal/phiscrub` package) has 14 patterns and 21 unit tests verifying pattern detection and false-positive behavior.
8. **Incidental PHI exposure** that bypasses scrubbing is treated as a security incident under the breach-notification flow per 45 CFR §164.404 and the customer's BAA — not as expected behavior.
9. **Cross-org relocate** (`cross_org_site_relocate.py`) ships behind an attestation-gated feature flag (mig 281 + 282) that returns 503 until outside HIPAA counsel signs off; a dual-admin DB CHECK constraint enforces `lower(approver) <> lower(proposer)`; substrate invariant `cross_org_relocate_chain_orphan` (sev1) is the bypass detector.
10. **Counsel's 7 Hard Rules** (CLAUDE.md 2026-05-13, gold authority) are first-pass filter on every design / Gate A / commit affecting this boundary.

**This attestation is engineering documentation. It is not a legal opinion. Customers and auditors relying on the attestation in a regulatory matter should obtain independent counsel review.**

---

*OsirisCare Engineering — 2026-05-16 (rev 1.2, Session 220 doc refresh)*
