# CLAUDE.md - Master Summary

**Document Overview:** Complete reference guide for MSP Automation Platform
**Total Length:** 5,499 lines across 7 chunks
**Last Updated:** October 23, 2025
**Version:** 2.2

---

## Part 0: Executive Summary & Foundation (Lines 1-900)

**Main Topics:** Executive Summary, MVP Build Plan, Service Catalog, Technical Architecture, HIPAA Compliance Framework (Part 1)

**Key Ideas:**
- **Business Model:** High-margin HIPAA compliance-as-a-service for healthcare SMBs using NixOS + MCP + LLM stack
- **Target Market:** 1-50 provider practices in NEPA region with tiered pricing ($200-3000/mo based on size)
- **Legal Positioning:** Business Associate for operations only - processes metadata/logs, never patient PHI
- **MVP Timeline:** 5-6 weeks from start to functioning pilot with 13 concrete implementation steps
- **Service Scope:** Infrastructure-only (servers, network, OS) - explicitly excludes endpoints, SaaS, desktop support
- **Technical Stack:** NixOS flakes for deterministic builds, MCP server with LLM, Redis/NATS for event queue
- **Compliance Gaps to Address:** Named baseline, deterministic runbooks, crypto defaults, evidence pipeline, backup verification
- **Competitive Edge:** Anduril-style compliance rigor adapted for healthcare SMBs vs. 6-month enterprise solutions

**Repeated Themes:**
- Deterministic/auditable infrastructure via NixOS flakes
- "Evidence-by-architecture" - audit trail structurally inseparable from operations
- Solo engineer can support 10-50 clients at 40%+ margins
- Metadata-only monitoring avoids PHI processing liability

**Code Examples:** Client flake configuration, MCP server structure with FastAPI/Pydantic

---

## Part 1: Guardrails & Deployment (Lines 901-1800)

**Main Topics:** Guardrails & Safety, Client Deployment, Network Discovery & Automated Enrollment

**Key Ideas:**
- **Guardrails:** Rate limiting (5-min cooldown per host/tool), parameter validation with Pydantic, whitelist enforcement
- **Client Deployment:** Terraform modules for VM/pod provisioning with cloud-init for automated setup
- **Network Discovery Methods:**
  - Active scanning (nmap for service fingerprinting, SNMP for managed devices, mDNS for IoT)
  - Passive monitoring (ARP/packet capture without active probing)
  - Switch/Router API (query authoritative ARP/MAC tables via SSH)
- **Device Classification:** Automatic tier assignment (Tier 1: infrastructure, Tier 2: applications, Tier 3: medical devices)
- **Automated Enrollment:** Pipeline from discovery → classification → agent deployment → MCP registration
- **HIPAA Safety:** Discovery avoids PHI-bearing ports, minimal footprint, audit trail for all scans

**Repeated Themes:**
- Multi-method discovery for comprehensive asset inventory without manual tracking
- Agent-based monitoring for servers, agentless (SNMP/syslog) for network gear
- Automated vs. manual approval based on device tier
- Switch API discovery is stealthier and more reliable than active scanning

**Code Examples:**
- Python NetworkDiscovery class with nmap/SNMP/mDNS integration
- AutoEnrollment pipeline with agent bootstrapping
- NixOS module for discovery service

---

## Part 2: Executive Dashboards (Lines 1801-2700)

**Main Topics:** Executive Dashboards & Audit-Ready Outputs (Part 1)

**Key Ideas:**
- **Philosophy:** "Enforcement-First, Visuals Second" - dashboards expose automation, don't replace it
- **Minimal Architecture:**
  - **Collectors:** Local state (flake status, patches, backups) + minimal SaaS taps (IdP, Git)
  - **Rules as Code:** YAML-based compliance rules with HIPAA control mapping, auto-fix integration
  - **Evidence Packager:** Nightly generation of signed evidence bundles (ZIP with PDF report)
  - **Thin Dashboard:** Static site showing real-time compliance posture
- **8 Core Controls:** Each rule maps to HIPAA citations with auto-fix runbooks
- **Evidence Bundle Structure:** Cryptographically signed ZIPs with manifest, uploaded to WORM storage
- **Monthly Compliance Packet Template:** Print-ready PDF with control posture, backup verification, time sync, access controls, patch status

**Repeated Themes:**
- Thin collector layer (not heavy SaaS/data lakes)
- Rules-as-code with explicit HIPAA control mapping
- Signed evidence bundles for auditor handoff
- HTML → PDF generation for print-friendly reports
- Metadata-only processing (no PHI)

**Code Examples:**
- LocalStateCollector (Python) for flake/patch/backup status
- ExternalStateCollector for Okta/GitHub API integration
- compliance_rules.yaml with 8 core controls
- EvidencePackager with signing and WORM upload

---

## Part 3: Compliance Packets & Provenance (Lines 2701-3600)

**Main Topics:** Monthly Compliance Packet Details, Grafana Dashboards, Weekly Executive Postcard, Software Provenance & Time Framework (Part 1)

**Key Ideas:**
- **Monthly Compliance Packet Sections:**
  - Control posture heatmap with HIPAA control status
  - Backup verification with test-restore proofs
  - Time synchronization with NTP drift monitoring
  - Access controls (failed logins, dormant accounts, MFA coverage)
  - Patch timeline with MTTR tracking
  - Encryption status (LUKS volumes, TLS certificates)
  - Incidents and exceptions log
- **Grafana Print-Friendly Dashboard:** 7 panels with compliance heatmap, backup SLO, time drift gauge, failed logins, patch status
- **Weekly Executive Postcard:** One-page HTML email with key highlights, sent Monday mornings
- **Software Provenance Framework:** Cryptographic signing, SBOM generation, multi-source time sync, hash chains, blockchain anchoring
- **NixOS Built-In Provenance:** Content addressing, reproducible builds, derivation files, closure tracking

**Repeated Themes:**
- Print-ready outputs for auditor handoff
- Cryptographic proof vs. documentation
- Time-stamped evidence bundles
- Tier-based features (Essential/Professional/Enterprise)

**Code Examples:**
- Monthly packet Markdown template with HIPAA control tables
- Grafana dashboard JSON with 7 panel definitions
- Executive postcard HTML template with Jinja2
- NixOS build signing module

---

## Part 4: Evidence & Integrity (Lines 3601-4500)

**Main Topics:** Evidence Registry, SBOM Generation, Multi-Source Time Sync, Hash Chain Log Integrity, Blockchain Anchoring

**Key Ideas:**
- **Evidence Registry:** Append-only SQLite database with WORM constraints (cannot update/delete entries)
- **SBOM Generation:** Creates Software Bill of Materials in SPDX 2.3 format by parsing NixOS store paths
- **Multi-Source Time Synchronization:**
  - Essential tier: NTP only (3+ servers required)
  - Professional tier: NTP + GPS (Stratum 0 source)
  - Enterprise tier: NTP + GPS + Bitcoin blockchain time
- **Time Anomaly Detection:** Monitors drift >100ms, alerts via webhook, logs all adjustments
- **Hash Chain Log Integrity:** Links log snapshots cryptographically (blockchain-style), detects tampering
- **Blockchain Anchoring:** Bitcoin OP_RETURN transactions for external immutability proof (Enterprise tier only)

**Repeated Themes:**
- Append-only/WORM patterns for tamper-evident evidence
- Tier-based feature flags (Essential → Professional → Enterprise)
- Cryptographic proof of time and integrity
- Forensic-grade audit trails

**Code Examples:**
- EvidenceRegistry with SQLite triggers preventing updates/deletes
- SBOMGenerator parsing Nix store paths to SPDX JSON
- NixOS time-sync module with GPS/Bitcoin integration
- Hash chain service linking log snapshots
- BlockchainAnchor using Bitcoin OP_RETURN

---

## Part 5: Implementation & Positioning (Lines 4501-5400)

**Main Topics:** Compliance Tiers, MCP Integration, Implementation Roadmap, Competitive Positioning

**Key Ideas:**
- **Compliance Tiers:**
  - Essential ($200-400/mo): Basic NTP, unsigned bundles, 30-day retention
  - Professional ($600-1200/mo): GPS time sync, signed bundles, SBOM, 90-day retention
  - Enterprise ($1500-3000/mo): Bitcoin time, blockchain anchoring, 2-year retention, forensic mode
- **MCP Integration:** Two new tools (`check_time`, `verify_chain`) for time anomaly and hash chain verification
- **5-Sprint Implementation Plan:** Foundation → Evidence Registry → Time Framework → Hash Chains → Enterprise Features
- **Enhanced MVP Roadmap:** 14 phases adding compliance to original 13-step plan
- **Runbook Structure:** YAML files with HIPAA control citations, steps, rollback, evidence requirements
- **Evidence Bundle Format:** JSON with incident_id, runbook_id, actions taken, HIPAA controls, MTTR, storage locations
- **Competitive Positioning vs. Anduril:** SMB-focused, no DoD complexity, HIPAA instead of STIG, faster deployment

**Repeated Themes:**
- Tier-based pricing with feature flags in NixOS config
- Evidence-by-architecture (operations generate artifacts)
- 6-week implementation vs. 6-month enterprise solutions
- Solo engineer supporting 10-50 clients at 40% margins

**Code Examples:**
- Tier configuration YAML with feature breakdown
- NixOS tier-based module enables
- MCP TimeCheckTool and VerifyChainTool
- 5-sprint checklist with success criteria

---

## Part 6: Testing & Insights (Lines 5401-5499)

**Main Topics:** LLM-Driven Compliance Testing, "Did You Know?" Insights, Key References, Implementation Status

**Key Ideas:**
- **LLM-Driven Testing (Meta Framework):**
  - Generate synthetic HIPAA violations for continuous testing
  - Validate baseline coverage against Security Rule requirements
  - Test runbook edge cases (resource exhaustion, permission issues, concurrent incidents)
  - Benefits: Gap discovery before auditors, evidence quality assurance, thousands of test scenarios
- **"Did You Know?" Insights:**
  - **MCP Audit Boundary:** Protocol creates audit trail by design (structurally inseparable)
  - **Metadata Loophole:** Processing system metadata ≠ processing PHI (lower liability, simpler BAAs)
  - **NixOS Compliance Multiplier:** Flake hashes = cryptographic proof of configuration
  - **HHS/OCR AI Warning:** Document that LLM operates on metadata only, not patient data
  - **Switch API Discovery:** Query ARP/MAC tables directly (stealthier, more complete than scanning)
  - **Dashboard Theater Problem:** Most vendors show dashboards without enforcement; you invert this
- **Implementation Status (Nov 2025):** Phase 1 complete (NixOS agent, guardrails, tests), Phase 2 starting

**Repeated Themes:**
- "Audit-by-architecture" vs. bolt-on logging
- Cryptographic proof vs. documentation
- Metadata-only monitoring avoids PHI liability
- Enforcement-first, visuals second

**Code Examples:**
- Python functions for LLM-driven synthetic testing
- Validation report section for monthly packets

---

## Cross-Cutting Themes (All Parts)

**Core Principles Repeated Throughout:**

1. **Evidence-by-Architecture**
   - MCP audit trail structurally inseparable from operations
   - Operations automatically generate compliance artifacts
   - Cannot execute without creating evidence

2. **Deterministic Builds**
   - NixOS flakes = cryptographic proof of configuration
   - Content-addressed packages with reproducible builds
   - Flake hash proves exact system state

3. **Metadata-Only Monitoring**
   - Avoids PHI processing liability
   - Business Associate for operations, not clinical data
   - Simpler BAAs, lower legal exposure

4. **Enforcement-First Philosophy**
   - Automation before visuals
   - Fix before alert
   - Dashboards expose what already happened

5. **Solo Engineer Scalability**
   - 10-50 clients at 40%+ margins
   - Terraform + Nix for automation
   - Minimal manual intervention

6. **Rapid Implementation**
   - 6-week implementation vs. 6-month enterprise
   - 13-14 phase MVP plan
   - 5-sprint provenance framework

7. **Tier-Based Pricing**
   - Essential: $200-400/mo (1-5 providers)
   - Professional: $600-1200/mo (6-15 providers)
   - Enterprise: $1500-3000/mo (15-50 providers)

8. **Auditor-Ready Outputs**
   - Print-ready monthly compliance packets
   - Weekly executive postcards
   - Signed evidence bundles with WORM storage
   - No consultant needed on audit calls

---

## Technical Stack Summary

**Core Technologies:**
- **NixOS:** Deterministic builds, reproducible systems, cryptographic provenance
- **MCP (Model Context Protocol):** Structured LLM-to-tool interface with audit trail
- **GPT-4o:** Incident triage and runbook selection
- **Redis/NATS JetStream:** Multi-tenant event queue with durability
- **WORM Storage:** Tamper-evident evidence bundles (S3 object lock)
- **cosign/syft:** Container signing and SBOM generation
- **Terraform:** Infrastructure-as-code for client deployment
- **Grafana:** Print-friendly compliance dashboards
- **SQLite:** Append-only evidence registry

**Languages & Frameworks:**
- **Python:** MCP server, evidence packager, discovery services
- **Nix:** System configuration, module definitions
- **YAML:** Compliance rules, runbook definitions, tier configs
- **Markdown/HTML:** Compliance packets, executive reports

---

## Quick Navigation by Topic

**Business & Strategy:**
- Business model & pricing: Part 0, Part 5
- Competitive positioning: Part 0, Part 5, Part 6
- Legal positioning & BAAs: Part 0, Part 6

**Implementation:**
- MVP build plan: Part 0
- Implementation roadmap: Part 5
- 5-sprint provenance plan: Part 5
- Current status: Part 6

**Technical Architecture:**
- Core stack: Part 0
- Client deployment: Part 1
- MCP integration: Part 5, Part 6
- NixOS modules: Part 1, Part 3, Part 4

**HIPAA Compliance:**
- Framework & gaps: Part 0
- 8 core controls: Part 2
- Compliance packets: Part 2, Part 3
- Evidence bundles: Part 2, Part 3, Part 4

**Automation & Monitoring:**
- Network discovery: Part 1
- Guardrails & safety: Part 0, Part 1
- Rules-as-code: Part 2
- Auto-fix runbooks: Part 0, Part 5

**Dashboards & Reporting:**
- Executive dashboards: Part 2, Part 3
- Grafana setup: Part 3
- Weekly postcards: Part 3
- Monthly packets: Part 2, Part 3

**Provenance & Security:**
- Software provenance: Part 3, Part 4
- Build signing: Part 3
- Evidence registry: Part 4
- SBOM generation: Part 4
- Time synchronization: Part 4
- Hash chains: Part 4
- Blockchain anchoring: Part 4

**Testing & Validation:**
- LLM-driven testing: Part 6
- Synthetic violations: Part 6
- Edge case generation: Part 6

---

## Key Differentiators vs. Competition

**vs. Traditional MSPs:**
- Infrastructure-only (no endpoint/SaaS support)
- Automated compliance vs. manual documentation
- Evidence-by-architecture vs. bolt-on logging
- 40% margins vs. 20% industry average

**vs. Enterprise Compliance Vendors:**
- 6-week implementation vs. 6-month
- $200-3000/mo vs. $10k-50k/mo
- SMB-focused vs. enterprise-focused
- Solo engineer vs. team of consultants

**vs. Anduril (Defense):**
- HIPAA vs. DoD STIG
- Healthcare SMBs vs. classified systems
- Metadata-only vs. device attestation
- Lower barrier to entry

---

## Success Metrics

**MVP Phase (Weeks 0-9):**
- ✅ 13-step build plan completed
- ✅ First pilot client deployed
- ✅ Incident detection and auto-fix working
- ✅ Evidence bundles generated nightly

**Phase 1 Complete (Nov 2025):**
- ✅ NixOS compliance agent (27 config options)
- ✅ Pull-only architecture (no listening sockets)
- ✅ 10 guardrails implemented
- ✅ VM integration tests passing

**Phase 2 Status (As of 2025-11-21):**
- ✅ MCP Server running on VM (FastAPI + Redis)
- ✅ 5/7 Runbooks loaded and operational
- ✅ VM infrastructure: Mac host + 2 NixOS VMs
- ✅ SSH access chain configured
- ✅ Cachix binary cache configured
- 🟡 Agent core implementation (pending client wiring)
- 🟡 Drift detection & self-healing
- 🟡 Evidence generation pipeline
- 🟡 2 runbooks need YAML fixes (RB-DISK-001, RB-RESTORE-001)

**Long-Term Goals:**
- 10-50 clients supported by solo engineer
- 40%+ profit margins maintained
- <4 hour MTTR for critical patches
- 100% MFA coverage for all clients
- 95%+ compliance score across all controls
- Zero manual compliance documentation

---

## Document Statistics

**Total Content:**
- 5,499 lines
- ~190k characters
- 18 complete code implementations
- 12 configuration templates
- 50+ HIPAA control mappings
- 8 runbook examples
- 3 dashboard specifications

**Split Structure:**
- 7 chunks (6 × 900 lines + 1 × 99 lines)
- 7 individual summaries (10-15 lines each)
- 1 master index with cross-references
- 1 consolidated master summary (this document)

---

## Next Steps

**Immediate (This Week):**
- Baseline YAML with 30 toggles
- 6 runbook YAML files with HIPAA citations
- Evidence writer with hash-chain + WORM
- SBOM + image signing in CI
- LUKS + SSH-certs in client flake
- First compliance packet prototype

**Short-Term (Weeks 6-10):**
- Sprint 1: Build signing foundation
- Sprint 2: Evidence registry with cosign
- Sprint 3: Multi-source time sync
- Sprint 4: Hash chain implementation
- Sprint 5: Enterprise features (blockchain, SBOM)

**Long-Term (Months 3-6):**
- First 5 pilot clients deployed
- Expansion to Windows support
- Patching automation with approval workflows
- Local LLM option (Llama-3 8B)
- Self-service client portal

---

## Current Infrastructure Status (2025-11-21)

### VM Environment
| Component | Status | Access |
|-----------|--------|--------|
| Mac Host | Running | `ssh dad@174.178.63.139` |
| MCP Server VM | Running | `ssh -p 4445 root@localhost` (from Mac) |
| Test Client VM | Running | `ssh -p 4444 root@localhost` (from Mac) |
| MCP API | Healthy | `curl http://localhost:8001/health` (from Mac) |
| Redis | Running | Port 6379 on MCP server |

### Components Working
- FastAPI MCP server (server.py)
- 5/7 YAML runbooks loaded
- Health/runbooks API endpoints
- SSH key authentication
- VirtualBox port forwarding

### Known Issues
- 2 runbooks fail YAML parsing (RB-DISK-001, RB-RESTORE-001) - need escaped characters
- Server must be started manually (NixOS read-only filesystem)
- Client VM not yet wired to MCP server

---

## Design Review Checklist

**For another agent reviewing this design:**

### Architecture Questions
- [ ] Is pull-only architecture sufficient for all use cases?
- [ ] Are 10 guardrails comprehensive enough?
- [ ] Is the tier-based pricing model appropriate?

### Implementation Questions
- [ ] Is the runbook YAML format well-designed?
- [ ] Should evidence bundles use different format than JSON?
- [ ] Is SQLite appropriate for evidence registry at scale?

### Security Questions
- [ ] Is mTLS sufficient for agent-to-MCP communication?
- [ ] Should runbooks be signed as well as evidence?
- [ ] Is the metadata-only approach truly PHI-safe?

### Scalability Questions
- [ ] Can Redis handle 50+ clients without clustering?
- [ ] Is 60-second poll interval appropriate?
- [ ] How do we handle VM resource constraints?

### Key Documents to Review
1. `CLAUDE.md` - Master architecture document (5,499 lines)
2. `modules/compliance-agent.nix` - NixOS module (546 lines, 27 options)
3. `/var/lib/mcp-server/server.py` - MCP server implementation
4. `flake-compliance.nix` - Production flake configuration
5. `VM-ACCESS-GUIDE.md` - Infrastructure access instructions
6. `MCP-SERVER-STATUS.md` - Current server status

---

**End of Master Summary**
**Last Updated:** 2025-11-21
**Source:** CLAUDE.md v2.2 (5,499 lines)
**Individual Summaries:** See `claude_summaries/` directory
**Full Chunks:** See `claude_chunks/` directory
**Infrastructure Docs:** See `VM-ACCESS-GUIDE.md`, `MCP-SERVER-STATUS.md`, `CACHIX-SETUP.md`
