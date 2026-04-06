# Session 194 — Mesh Discovery + mDNS + Landing Page + Blazytko Patterns

**Date:** 2026-04-04 / 2026-04-05
**Commits:** 15
**Daemon:** v0.3.79 → v0.3.81
**Agent:** v0.4.2 → v0.4.5

## Summary

Cross-subnet mesh peer discovery with 14-point hardening from SWE/PM/CCIE roundtable. mDNS service discovery eliminates DHCP drift breakage. Landing page overhauled (pricing, legal, claims accuracy). Blazytko agentic pipeline patterns adapted for HIPAA compliance (hypothesis-driven L2, validation gates, audit trail, check catalog).

## Major Features

### 1. Cross-Subnet Mesh Peer Discovery
- Backend delivers sibling IPs+MACs in checkin response
- TLS probe with CA verification (TCP fallback)
- Parallel probing, backend-peer expiry, WireGuard IP filtering
- 14-point hardening: all P0/P1/P2 from roundtable review
- Ring convergence monitoring, split-brain detection
- Consumer-router topology handling (mesh_topology config, auto-reclassify alerts)

### 2. DHCP Drift Resilience (3-part solution)
- mDNS: Avahi publishes _osiris-grpc._tcp.local, agent resolves by service name
- Secondary static IP: 169.254.88.1/24 on appliance NIC
- Onboarding gate: forces network_mode decision (static_lease/dynamic_mdns)
- Agent discovery chain: mDNS → link-local → DNS SRV → offline
- Reconnect loop re-resolves after 3 failures

### 3. Landing Page Overhaul
- Pricing: $200 → $499/mo (roundtable consensus)
- Legal: Privacy Policy, ToS, BAA pages (were dead links)
- Claims qualified: reports → monitoring summaries, archive → bundles
- Animated ECG heartbeat in hero section
- Calendly link fixed
- Pricing strategy PDF generated

### 4. Blazytko Roundtable Patterns
- Hypothesis-driven L2 triage: 12 incident types × 3-5 ranked root causes
- L2 validation gate: schema check before execution (14 tests)
- Investigation audit trail: HealingEntry + hypothesis/confidence/reasoning
- Confidence tagging on evidence bundles
- Check catalog: /api/check-catalog — 58 checks, HIPAA mapping, no remediation scripts

### 5. Data Quality
- Workstation cleanup: 17→9 (duplicates, appliance IPs, router, stale artifacts)
- Backend prevention logic runs every checkin

## Migrations
- 120: mesh_topology on sites
- 121: network_mode on sites
- 122: hypotheses JSONB on l2_decisions

## Test Results
- Go daemon: 16 packages, 0 failures (including 14 new validation tests)
- Go agent: 5 packages, 0 failures
- Frontend: tsc 0 errors
- Backend: py_compile clean

## Deployments
- Daemon v0.3.81 fleet order delivered to both appliances
- Agent v0.4.5 deployed to iMac (Go 1.24 amd64 compat)
- 7 backend deploys to VPS
- mDNS verified via avahi-browse on appliance
- Mesh half-formed: .241 sees .0.11 peer, reverse routing blocked by consumer router

## Late Session Additions (commits 14-15)

### Compliance Packets → Complete
- Added 3 new sections: administrative attestations (20 HIPAA controls from Companion), healing pipeline metrics, device inventory
- PHI-free architecture section — highlights no-BAA-needed differentiator
- Migration 123: compliance_attestations table for organizational attestation tracking
- Attestations flow from Compliance Companion portal into packets

### Evidence Download → Functional
- Replaced placeholder URL with MinIO presigned URL (1-hour expiry)
- Falls back to path convention from compliance_bundles if evidence_bundles row missing
- Returns bundle_hash and size_bytes for verification

### PHI Scrub Audit → CLEAN
- Audited all recent compliance bundles in production DB
- No patient data, SSN, DOB, MRN, credentials found
- IPs preserved intentionally (Safe Harbor — infrastructure identifiers)
- "password" hit was false positive (config setting name `password_auth=yes`)
- phiscrub package: 14 patterns, 21 tests, all passing

### NixOS Rebuild
- Built successfully (Avahi extraServiceFiles + appliance-static-ip service)
- Lanzaboote switch error (Secure Boot bootloader) — cosmetic, config applied
- Avahi + 169.254.88.1 deployed manually, both verified live

### Workstation Data Quality
- Cleaned 17→9 real workstations (removed duplicates, appliance IPs, router, stale DHCP artifacts)
- Backend prevention logic runs every checkin cycle
