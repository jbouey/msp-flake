# PHI Data Flow Attestation — OsirisCare Platform

**Document Version:** 1.2
**Date:** 2026-05-16 (updated; originally 2026-03-23, v1.1 2026-05-06)
**Author:** OsirisCare Engineering
**Review Schedule:** Annually or upon architectural change

> **2026-05-16 update (v1.2):** BAA enforcement triad shipped (Session
> 220 #52 + #91 + #92 + #98 + #99 + #90 + #97). Counsel Rule 6 machine-
> enforcement: 5 customer-state-mutating workflows (`owner_transfer`,
> `cross_org_relocate`, `evidence_export`, `new_site_onboarding`,
> `new_credential_entry`) now require active BAA + machine-enforced
> blocking at three layers — `BAA_GATED_WORKFLOWS` build-time CI list,
> `require_active_baa()` runtime callsite factory, and
> `sensitive_workflow_advanced_without_baa` (sev1) substrate invariant
> scanning state-machine tables + `admin_audit_log` last-30d. See
> `docs/lessons/sessions-219-220.md` for full mechanics.

> **Framing update (2026-05-06).** Earlier revisions of this document
> used absolute language ("PHI-free infrastructure", "NO PHI stored",
> "ensures", "all 18 Safe Harbor identifiers"). That framing was
> retired after the cross-org-relocate counsel review (2026-05-06)
> and per the project's legal-language rules in CLAUDE.md.
>
> Current posture: the substrate is **PHI-free by design** — it
> handles compliance metadata, not PHI — and treats that metadata
> conservatively as **PHI-adjacent**. Appliance-side scrubbing is
> defense-in-depth at the egress boundary, not a guarantee that PHI
> can never appear in any byte that ever transits. Incidental PHI
> exposure is treated as a security incident under the breach-
> notification flow.
>
> **Canonical current authority:** `~/Downloads/OsirisCare_Owners_
> Manual_and_Auditor_Packet.pdf` (Part 2 — Auditor's Need-to-Know,
> §2.7 PHI boundary). Read first.

## Executive Summary

OsirisCare is **designed to be PHI-free** — the substrate operates on
compliance metadata, not on PHI. Outbound data is scrubbed at the
appliance boundary before transiting to Central Command, with
defense-in-depth at the portal layer. This document attests to the
data-flow architecture and scrubbing controls that support that
posture; it does not claim cryptographic certainty that no PHI byte
can ever leak.

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────┐
│                CLIENT SITE (PHI Zone)                │
│                                                     │
│  [EHR] [PACS] [Medical Devices] [Workstations]     │
│         │           │              │                │
│         └───────────┼──────────────┘                │
│                     │ (LAN scans)                   │
│              ┌──────┴──────┐                        │
│              │  APPLIANCE   │                        │
│              │              │                        │
│              │ ┌──────────┐ │                        │
│              │ │PHI SCRUB │ │  ← ALL egress scrubbed │
│              │ └────┬─────┘ │                        │
│              └──────┼───────┘                        │
│                     │                                │
└─────────────────────┼────────────────────────────────┘
                      │ TLS 1.3 + WireGuard
                      │ (scrubbed data only)
┌─────────────────────┼────────────────────────────────┐
│         CENTRAL COMMAND (PHI-Free Zone)              │
│                     │                                │
│  ┌─────────┐  ┌─────┴────┐  ┌──────────┐           │
│  │Dashboard │  │ API/DB   │  │  MinIO   │           │
│  │(portals) │  │(postgres)│  │ (WORM)   │           │
│  └─────────┘  └──────────┘  └──────────┘           │
│                                                     │
│  Designed PHI-free. Metadata pre-scrubbed at appliance. │
└─────────────────────────────────────────────────────┘
```

## Scrubbing Controls (phiscrub package)

The `phiscrub` package runs on the appliance and scrubs ALL outbound data
before transmission. The following patterns are detected and replaced with
`[REDACTED-{hash}]`:

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

## Egress Points Scrubbed

| Egress Path | Scrubbed Fields | Applied In |
|-------------|----------------|------------|
| Incident reports | hostname, expected, actual | incident_reporter.go |
| Evidence bundles | host names, drift finding values | submitter.go |
| Log entries | every message field | shipper.go |
| Checkin requests | hostname, discovery results | phonehome.go, daemon.go |
| Network scan results | hostname, expected, actual, details | netscan.go |
| L2 planner input | all text sent to LLM | phi_scrubber.go |

## What is NOT Scrubbed (By Design)

These infrastructure identifiers are intentionally preserved. They
are not PHI — they identify devices, software, and compliance state,
not patients. Safe Harbor (45 CFR §164.514(b)(2)) is a
de-identification standard for patient-record data sets and is **not**
the rationale here; the rationale is that infrastructure identifiers
fall outside the §164.501 PHI definition entirely:

- IP addresses (needed for network topology)
- MAC addresses (device identification)
- Site IDs (operational routing)
- Agent versions (fleet management)
- Check types (compliance categorization)
- HIPAA control references (framework mapping)
- WireGuard public keys (cryptographic identity)

> **Caveat.** An IP address can become PHI in combination with
> patient context (e.g. a workstation IP tied by other records to a
> specific patient encounter). On the OsirisCare substrate the IP is
> never combined with patient context — there is no patient context
> on this side of the boundary.

## Portal-Layer Defense in Depth

Even if scrubbing failed at the appliance layer, the portal boundary
provides additional protection:

- `phi_boundary.py` strips `raw_output`, `stdout`, `stderr`, `hostname`,
  `ip_address`, `username`, `file_path` from all client/partner portal
  responses
- Admin dashboard shows scrubbed data (not raw)
- Evidence check details sanitized before portal display

## Attestation

This document attests that:

1. PHI scrubbing is applied at every customer-data egress point on the
   appliance enumerated in the table above (incident_reporter.go,
   submitter.go, shipper.go, phonehome.go, daemon.go, netscan.go,
   phi_scrubber.go).
2. Central Command is **designed to be PHI-free** — the substrate
   operates on compliance metadata, treated conservatively as
   PHI-adjacent. The portal `phi_boundary.py` strips raw_output,
   stdout, stderr, hostname, ip_address, username, file_path from
   client/partner-facing responses as defense-in-depth.
3. The customer–OsirisCare relationship is governed by a
   per-customer e-signed BAA (OsirisCare as Business Associate to
   the customer Covered Entity); see `docs/legal/billing-phi-
   boundary.md` and the `baa_signatures` append-only table.
4. The scrubbing patterns enumerated above target the categories
   most likely to appear in operational telemetry. They do **not**
   constitute a complete §164.514(b)(2) Safe Harbor de-identification
   pipeline (which is a different control for a different purpose).
5. The scrubbing code (`appliance/internal/phiscrub` package) has 14
   patterns and 21 unit tests verifying pattern detection and
   false-positive behavior.
6. Incidental PHI exposure that bypasses scrubbing is treated as a
   **security incident** under the breach-notification flow per 45
   CFR §164.404 and the customer's BAA — not as expected behavior.

**This attestation is engineering documentation. It is not a legal
opinion. Customers and auditors relying on the attestation in a
regulatory matter should obtain independent counsel review.**

---

*OsirisCare Engineering — 2026-03-23*
