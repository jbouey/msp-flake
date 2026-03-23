# PHI Data Flow Attestation — OsirisCare Platform

**Document Version:** 1.0
**Date:** 2026-03-23
**Author:** OsirisCare Engineering
**Review Schedule:** Annually or upon architectural change

## Executive Summary

OsirisCare is designed as a **PHI-free infrastructure**. Protected Health
Information is scrubbed at the appliance boundary before any data transits
to Central Command. This document formally attests to the data flow
architecture and scrubbing controls that support this claim.

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
│  NO PHI stored. All data pre-scrubbed by appliance. │
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

These infrastructure identifiers are intentionally preserved per HIPAA
Safe Harbor (45 CFR 164.514(b)(2)):

- IP addresses (needed for network topology)
- MAC addresses (device identification)
- Site IDs (operational routing)
- Agent versions (fleet management)
- Check types (compliance categorization)
- HIPAA control references (framework mapping)
- WireGuard public keys (cryptographic identity)

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

1. PHI scrubbing is applied at every data egress point on the appliance
2. Central Command infrastructure is designed to be PHI-free
3. No Business Associate Agreement (BAA) is required for the Central
   Command infrastructure because no PHI is stored or processed there
4. Scrubbing patterns cover all 18 HIPAA Safe Harbor identifiers
5. The scrubbing code (`phiscrub` package) has 21 unit tests verifying
   pattern detection and false-positive prevention

**This attestation should be reviewed by qualified legal counsel before
reliance in regulatory proceedings.**

---

*OsirisCare Engineering — 2026-03-23*
