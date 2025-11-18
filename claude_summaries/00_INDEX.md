# CLAUDE.md Index - Quick Reference Guide

## Overview
The original CLAUDE.md (5,499 lines) has been split into 7 manageable chunks of ~800-900 lines each.

---

## File Map

### claude_part0.md (Lines 1-900)
**Topics:** Executive Summary, MVP Build Plan, Service Catalog, Technical Architecture, HIPAA Framework (Part 1)

**Key Sections:**
- Business model and target market ($200-3000/mo tiered pricing)
- 13-step MVP build plan (5-6 weeks)
- Infrastructure-only service scope
- NixOS + MCP + LLM technical stack
- HIPAA compliance gaps and solutions

**Summary:** [claude_part0_summary.md](claude_part0_summary.md)

---

### claude_part1.md (Lines 901-1800)
**Topics:** Guardrails & Safety, Client Deployment, Network Discovery & Automated Enrollment

**Key Sections:**
- Rate limiting and parameter validation
- Terraform deployment modules
- Multi-method device discovery (active, passive, API-based)
- Device classification and tier assignment
- Automated enrollment pipeline

**Summary:** [claude_part1_summary.md](claude_part1_summary.md)

---

### claude_part2.md (Lines 1801-2700)
**Topics:** Executive Dashboards & Audit-Ready Outputs (Part 1)

**Key Sections:**
- Enforcement-first dashboard philosophy
- Minimal architecture (collectors, rules, evidence, dashboard)
- 8 core controls with HIPAA mapping
- Rules-as-code YAML format
- Evidence packager with signing
- Monthly compliance packet template (Part 1)

**Summary:** [claude_part2_summary.md](claude_part2_summary.md)

---

### claude_part3.md (Lines 2701-3600)
**Topics:** Monthly Compliance Packet Details, Grafana Dashboards, Software Provenance (Part 1)

**Key Sections:**
- Detailed compliance packet sections (backups, time sync, access controls, patches, encryption)
- Grafana print-friendly dashboard JSON
- Weekly executive postcard template
- Software provenance framework overview
- NixOS built-in provenance features
- Build and evidence signing

**Summary:** [claude_part3_summary.md](claude_part3_summary.md)

---

### claude_part4.md (Lines 3601-4500)
**Topics:** Evidence Registry, SBOM, Multi-Source Time Sync, Hash Chains, Blockchain Anchoring

**Key Sections:**
- Append-only evidence registry (SQLite with WORM)
- SBOM generation in SPDX format
- Multi-source time sync (NTP/GPS/Bitcoin by tier)
- Hash chain log integrity
- Bitcoin blockchain anchoring for Enterprise tier

**Summary:** [claude_part4_summary.md](claude_part4_summary.md)

---

### claude_part5.md (Lines 4501-5400)
**Topics:** Compliance Tiers, MCP Integration, Implementation Roadmap, Competitive Positioning

**Key Sections:**
- Tier-based pricing and features (Essential/Professional/Enterprise)
- MCP tools for time and chain verification
- 5-sprint implementation plan
- Enhanced MVP roadmap (14 phases)
- Runbook and evidence bundle formats
- Competitive positioning vs. Anduril

**Summary:** [claude_part5_summary.md](claude_part5_summary.md)

---

### claude_part6.md (Lines 5401-5499)
**Topics:** LLM-Driven Testing, "Did You Know?" Insights, References, Implementation Status

**Key Sections:**
- Meta framework application for synthetic testing
- 6 "Did You Know?" insights (MCP audit boundary, metadata loophole, etc.)
- Key references (NIST, HIPAA, Anduril)
- Current implementation status (Phase 1 complete)

**Summary:** [claude_part6_summary.md](claude_part6_summary.md)

---

## Cross-Cutting Themes

**Repeated Throughout All Chunks:**
1. **Evidence-by-architecture** - Audit trail structurally inseparable from operations
2. **Deterministic builds** - NixOS flakes = cryptographic proof of configuration
3. **Metadata-only monitoring** - Avoids PHI processing liability
4. **Enforcement-first** - Automation before visuals, fix before alert
5. **Solo engineer scalability** - 10-50 clients at 40%+ margins
6. **6-week implementation** - vs. 6-month enterprise solutions
7. **Tier-based pricing** - Essential ($200-400) → Professional ($600-1200) → Enterprise ($1500-3000)
8. **Auditor-ready outputs** - Print-ready packets without consultant on call

---

## Quick Navigation by Topic

**Business Model:** Part 0 (Executive Summary)
**Technical Stack:** Part 0 (Architecture), Part 3 (Provenance)
**HIPAA Compliance:** Part 0 (Framework), Part 2 (Controls)
**Implementation Plan:** Part 0 (MVP), Part 5 (Roadmap)
**Deployment:** Part 1 (Terraform), Part 1 (Discovery)
**Dashboards:** Part 2 (Architecture), Part 3 (Grafana)
**Evidence & Signing:** Part 3 (Signing), Part 4 (Registry)
**Time & Integrity:** Part 4 (Time Sync, Hash Chains)
**Pricing Tiers:** Part 5 (Compliance Tiers)
**Testing:** Part 6 (LLM-Driven Testing)

---

## How to Use This Split

1. **For high-level overview:** Read Part 0 summary
2. **For implementation details:** Parts 1-5 have concrete code and configs
3. **For insights and positioning:** Part 6 has strategic "Did You Know?" sections
4. **For specific topics:** Use the Quick Navigation map above
5. **To reconstruct full document:** All parts are sequential and complete

**Original file preserved:** `CLAUDE.md` remains unchanged in parent directory
