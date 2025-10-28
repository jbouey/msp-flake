# MSP Automation Platform

**High-margin, low-labor HIPAA compliance-as-a-service for healthcare SMBs**

[![NixOS](https://img.shields.io/badge/NixOS-24.05-blue.svg)](https://nixos.org)
[![HIPAA](https://img.shields.io/badge/HIPAA-Security%20Rule-green.svg)](https://www.hhs.gov/hipaa)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

---

## Overview

Enterprise-grade HIPAA compliance automation at SMB price points. This platform provides deterministic infrastructure management, automated remediation, and auditor-ready evidence generation for healthcare organizations.

**Stack:** NixOS + Model Context Protocol (MCP) + LLM
**Target:** Small to mid-sized healthcare practices (1-50 providers)
**Model:** Infrastructure-only, auto-heal + compliance monitoring
**Position:** Business Associate for operations (NOT clinical data processor)

---

## Key Features

### 🔒 **Compliance-by-Architecture**
- **NixOS-HIPAA Baseline v1**: Named, versioned compliance profile with HIPAA Security Rule mappings
- **Deterministic Builds**: Cryptographic proof of configuration via NixOS flakes
- **Evidence-by-Design**: Every action generates tamper-evident audit trail
- **WORM Storage**: Immutable evidence bundles with cryptographic signatures

### 🤖 **Automated Remediation**
- **LLM-Driven Triage**: GPT-4o selects pre-approved runbooks from incident data
- **Guardrails-First**: Rate limiting, parameter validation, service whitelisting
- **Closed-Loop Verification**: Post-fix validation and automatic escalation
- **Six Core Runbooks**: Backup failures, cert expiry, disk full, service crashes, high CPU, restore testing

### 📊 **Audit-Ready Outputs**
- **Monthly Compliance Packets**: Automated PDF generation with control status, evidence artifacts
- **Weekly Executive Postcards**: One-page email summaries for administrators
- **Print-Friendly Dashboards**: Grafana views designed for regulatory handoff
- **90-Day Evidence Retention**: Historical compliance proof with cryptographic verification

### 🔐 **Security Hardening**
- **Full-Disk Encryption**: LUKS with remote unlock capability
- **SSH Certificate Auth**: Short-lived certificates via step-CA, no password authentication
- **Secrets Management**: SOPS/age with client-scoped KMS keys and rotation policies
- **Time Synchronization**: NTP enforcement for audit log integrity

### 🌐 **Network Discovery**
- **Multi-Method Discovery**: Active scanning, passive monitoring, SNMP, mDNS, switch API queries
- **Automated Classification**: Device tier assignment (Tier 1: Infrastructure, Tier 2: Applications, Tier 3: Business processes)
- **Auto-Enrollment Pipeline**: Automatic agent deployment or agentless monitoring configuration
- **HIPAA-Safe Scanning**: Metadata-only collection, no PHI exposure risk

---

## Business Model

| Client Size | Providers | Monthly Fee | Infrastructure Cost | Margin |
|-------------|-----------|-------------|---------------------|--------|
| **Small** | 1-5 | $200-400 | ~$50 | 75%+ |
| **Medium** | 6-15 | $600-1200 | ~$150 | 75%+ |
| **Large** | 15-50 | $1500-3000 | ~$500 | 83%+ |

**Value Proposition:**
- 6-week implementation vs. 6-month enterprise solutions
- Solo engineer can support 10-50 clients at 40%+ margins
- Evidence packets eliminate consultant fees during audits
- Metadata-only processing avoids PHI liability exposure

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Client Site (NixOS)                     │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │ Log Watcher  │  │ Health Checks│  │   Remediation│     │
│  │   (Fluent)   │  │  (Systemd)   │  │     Tools    │     │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘     │
│         │                  │                  │              │
│         └──────────────────┼──────────────────┘              │
│                            │                                 │
└────────────────────────────┼─────────────────────────────────┘
                             │ TLS + mTLS
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                   Central Infrastructure                    │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────────────────────────────────────────────────┐  │
│  │          Event Queue (NATS JetStream/Redis)          │  │
│  │          Multi-tenant, per-client namespacing        │  │
│  └────────────────────────┬─────────────────────────────┘  │
│                           │                                 │
│  ┌────────────────────────▼─────────────────────────────┐  │
│  │              MCP Server (Planner + Executor)         │  │
│  │  ┌────────────┐  ┌─────────────┐  ┌──────────────┐  │  │
│  │  │  Planner   │→ │  Runbook    │→ │   Executor   │  │  │
│  │  │ (GPT-4o)   │  │  Selection  │  │   (Steps)    │  │  │
│  │  └────────────┘  └─────────────┘  └──────────────┘  │  │
│  │         ▲                                  │          │  │
│  │         │          Guardrails              ▼          │  │
│  │         │     (Rate limits, validation)    │          │  │
│  │  ┌──────┴──────────────────────────────────▼───────┐ │  │
│  │  │            Evidence Writer                      │ │  │
│  │  │  (Hash-chain logs + WORM storage)              │ │  │
│  │  └────────────────────────────────────────────────┘ │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│              Compliance Reporting & Dashboards              │
├─────────────────────────────────────────────────────────────┤
│  • Nightly Evidence Packager (PDF + signed ZIP)            │
│  • Monthly Compliance Packets (auditor-ready)              │
│  • Weekly Executive Postcards (email summaries)            │
│  • Grafana Dashboards (print-friendly)                     │
└─────────────────────────────────────────────────────────────┘
```

---

## HIPAA Compliance Framework

### Legal Positioning

**Role:** Business Associate (BA) for **operations only**, NOT clinical data processor

**Scope:** Infrastructure compliance monitoring and automated remediation
**Data Processed:** System metadata and operational logs ONLY (no PHI)
**Controls Addressed:** HIPAA Security Rule audit and operational safeguards

**Key Citation:**
> 45 CFR 164.308(a)(1)(ii)(D) — "Implement procedures to regularly review records of information system activity, such as audit logs, access reports, and security incident tracking reports."

### HIPAA Controls Mapped

| Control | Description | Implementation |
|---------|-------------|----------------|
| **164.308(a)(1)(ii)(D)** | Information System Activity Review | MCP audit trail, evidence bundles |
| **164.308(a)(5)(ii)(B)** | Protection from Malicious Software | Patch automation, vulnerability scanning |
| **164.308(a)(7)(ii)(A)** | Data Backup Plan | Automated backups with restore testing |
| **164.310(d)(1)** | Device and Media Controls | NixOS baseline enforcement |
| **164.310(d)(2)(iv)** | Data Backup and Storage | Encrypted backups with verification |
| **164.312(a)(1)** | Access Control | SSH certificate auth, MFA enforcement |
| **164.312(a)(2)(i)** | Unique User Identification | User access logging and review |
| **164.312(a)(2)(iv)** | Encryption and Decryption | LUKS full-disk encryption, TLS in-transit |
| **164.312(b)** | Audit Controls | Structured audit logs, time synchronization |
| **164.312(e)(1)** | Transmission Security | mTLS, WireGuard VPN |
| **164.316(b)(1)** | Policies and Procedures | Baseline-as-code, runbook library |

### Data Boundary Zones

| Zone | Data Types | HIPAA Risk | Mitigation |
|------|-----------|-----------|-----------|
| **System** | syslog, auth logs, package hashes, uptime | Very Low | Mask usernames, redact paths |
| **Application** | EHR audit metadata, access events | Moderate | Tokenize IDs, hash identifiers |
| **Data** | PHI content (lab results, notes) | High | **NEVER INGESTED** |

---

## Quick Start

### Prerequisites

```bash
# Install Nix (if not already installed)
curl -L https://nixos.org/nix/install | sh

# Clone repository
git clone git@github.com:jbouey/msp-flake.git
cd msp-flake
```

### Development Environment

```bash
# Enter development shell
nix develop

# Run integration tests
./test_integration.sh

# Build client flake
nix build .#nixosConfigurations.msp-client-base.config.system.build.toplevel
```

### Deploy First Client (Lab)

```bash
# Initialize Terraform
cd terraform/examples/complete-deployment
terraform init

# Deploy infrastructure
terraform apply -var="client_id=lab-001" -var="client_name=Lab Test"

# Verify deployment
terraform output connection_string
```

---

## Repository Structure

```
msp-flake/
├── baseline/                   # HIPAA compliance baseline
│   ├── hipaa-v1.yaml          # NixOS-HIPAA baseline profile
│   ├── controls-map.csv       # HIPAA → NixOS control mappings
│   └── exceptions/            # Per-client exceptions
│
├── flake/                      # NixOS client configuration
│   ├── flake.nix              # Main flake definition
│   └── Modules/
│       ├── base.nix           # Core system configuration
│       ├── encryption.nix     # LUKS full-disk encryption
│       ├── ssh-hardening.nix  # SSH certificate auth
│       ├── secrets.nix        # SOPS/age secrets management
│       └── timesync.nix       # NTP time synchronization
│
├── mcp/                        # MCP server implementation
│   ├── server.py              # FastAPI MCP server
│   ├── planner.py             # LLM-based runbook selection
│   ├── executor.py            # Runbook execution engine
│   └── guardrails/
│       ├── rate_limits.py     # Per-client rate limiting
│       └── validation.py      # Parameter validation
│
├── runbooks/                   # Pre-approved remediation runbooks
│   ├── RB-BACKUP-001-failure.yaml
│   ├── RB-CERT-001-expiry.yaml
│   ├── RB-DISK-001-full.yaml
│   ├── RB-SERVICE-001-crash.yaml
│   ├── RB-CPU-001-high.yaml
│   └── RB-RESTORE-001-test.yaml
│
├── evidence/                   # Evidence generation
│   └── evidence_writer.py     # Hash-chain evidence bundler
│
├── discovery/                  # Network discovery & enrollment
│   ├── scanner.py             # Multi-method device discovery
│   ├── classifier.py          # Device tier classification
│   └── enrollment.py          # Auto-enrollment pipeline
│
├── terraform/                  # Infrastructure as Code
│   ├── modules/
│   │   ├── client-vm/         # Client deployment module
│   │   └── event-queue/       # NATS/Redis queue module
│   └── examples/
│       └── complete-deployment/
│
├── scripts/                    # Utility scripts
│   ├── generate-sbom.sh       # SBOM generation (syft)
│   └── sign-image.sh          # Image signing (cosign)
│
├── .github/workflows/          # CI/CD automation
│   ├── build-and-sign.yml     # Build, sign, push images
│   └── update-flake.yml       # Nightly flake updates
│
└── docs/
    ├── PILOT_DEPLOYMENT.md    # Pilot deployment guide
    └── PROJECT_CONTEXT.md     # Technical deep-dive
```

---

## Implementation Status

### ✅ Completed (Weeks 1-3)

- [x] NixOS-HIPAA baseline v1 with control mappings
- [x] Core runbook library (6 runbooks)
- [x] MCP planner/executor split architecture
- [x] Evidence writer with hash-chain audit trail
- [x] Guardrails (rate limiting, parameter validation)
- [x] Network discovery and auto-enrollment
- [x] Terraform infrastructure modules
- [x] CI/CD workflows (build, sign, SBOM)
- [x] Integration test harness

### 🚧 In Progress (Week 4)

- [ ] Client flake hardening (LUKS, SSH-certs, SOPS)
- [ ] Evidence bundle signing (cosign)
- [ ] WORM storage integration
- [ ] Compliance packet PDF generation
- [ ] Synthetic incident testing (LLM-driven)

### 📋 Planned (Weeks 5-6)

- [ ] Lab burn-in testing
- [ ] First pilot client deployment
- [ ] Grafana dashboard templates
- [ ] Weekly executive postcard automation
- [ ] Documentation finalization

---

## Roadmap

### Phase 1: MVP (Weeks 1-6) ✅ On Track
- Core automation platform
- Basic compliance evidence
- Single-client deployment capability

### Phase 2: Pilot (Weeks 7-8)
- Lab testing with synthetic failures
- First pilot client onboarding
- Evidence packet validation
- Performance tuning

### Phase 3: Production (Weeks 9-12)
- Multi-client deployment
- Automated billing integration
- Self-service client portal
- Advanced anomaly detection

### Phase 4: Scale (Q2 2026)
- Windows support (Winlogbeat)
- Patch automation integration
- Local LLM option (Llama-3 8B)
- Additional compliance frameworks (PCI-DSS, SOC-2)

---

## Competitive Positioning

### vs. Traditional MSPs
- **Automation:** 98% automated vs. manual ticket-driven
- **Evidence:** Cryptographic proof vs. manual documentation
- **Margins:** 75%+ vs. 20-30%
- **Scale:** One engineer supports 50 clients vs. 1:10 ratio

### vs. Enterprise Compliance Platforms
- **Time to Deploy:** 6 weeks vs. 6 months
- **Cost:** $200-3000/mo vs. $5000-50000/mo
- **Complexity:** SMB-focused vs. enterprise bureaucracy
- **Transparency:** Open baseline vs. proprietary black box

### vs. Anduril (Defense Sector)
- **Market:** Healthcare SMBs vs. DoD/classified systems
- **Certification:** HIPAA-aligned vs. DoD STIG certified
- **Approach:** Same deterministic build philosophy, adapted for healthcare compliance

---

## Technology Stack

### Core Infrastructure
- **NixOS 24.05**: Deterministic Linux distribution
- **NATS JetStream**: Multi-tenant event streaming
- **Redis**: Rate limiting and caching
- **PostgreSQL**: Evidence storage (optional)
- **MinIO/S3**: WORM object storage

### Development Tools
- **Python 3.11+**: MCP server, automation scripts
- **Terraform**: Infrastructure as Code
- **GitHub Actions**: CI/CD automation
- **Docker/Podman**: Container runtime

### Security & Compliance
- **cosign**: Container image signing
- **syft**: SBOM generation
- **SOPS/age**: Secrets encryption
- **step-CA**: SSH certificate authority
- **LUKS**: Full-disk encryption

### Monitoring & Reporting
- **Grafana**: Compliance dashboards
- **Prometheus**: Metrics collection
- **Fluent-bit**: Log forwarding
- **WeasyPrint**: PDF generation

### LLM Integration
- **OpenAI GPT-4o**: Incident triage and runbook selection
- **Model Context Protocol (MCP)**: Structured LLM-tool interface
- **Azure OpenAI**: Enterprise alternative

---

## Development Progress

| Week | Focus Area | Status |
|------|-----------|--------|
| **Week 1** | Foundation & Baseline | ✅ Complete |
| **Week 2** | Infrastructure & Automation | ✅ Complete |
| **Week 3** | Integration & Testing | ✅ Complete |
| **Week 4** | Client Hardening & Evidence | 🚧 In Progress |
| **Week 5** | Compliance Packets & Dashboards | 📋 Planned |
| **Week 6** | Lab Testing & Refinement | 📋 Planned |

---

## Contributing

This is a private commercial project. For collaboration inquiries, contact the repository owner.

---

## License

Proprietary - All Rights Reserved

---

## Acknowledgments

- **Anduril Industries**: NixOS STIG approach and deterministic build philosophy
- **NixOS Community**: Flakes ecosystem and security modules
- **Anthropic**: Model Context Protocol specification
- **Meta Engineering**: LLM-driven compliance testing methodology

---

## Contact

**Repository:** [github.com/jbouey/msp-flake](https://github.com/jbouey/msp-flake)
**Documentation:** See `/docs` directory for detailed guides

---

**Last Updated:** October 27, 2025
**Current Version:** 0.3.0 (Week 4 Development)
