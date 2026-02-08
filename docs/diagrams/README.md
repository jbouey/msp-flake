# MSP Compliance Platform - Architecture Diagrams

This directory contains Mermaid diagrams documenting the MSP HIPAA Compliance Platform architecture.

## Diagrams Overview

### 1. System Architecture (`system-architecture.mermaid`)

**Purpose:** Shows the high-level component relationships across the entire platform.

**Key Components Illustrated:**

| Layer | Components |
|-------|------------|
| **NixOS Base** | Flakes, Modules, Systemd, nftables, timesyncd, auditd |
| **MCP Server** | FastAPI, Redis, LLM Integration, Runbooks, Guardrails |
| **Compliance Agent** | Drift Detector, Three-Tier Healing (L1/L2/L3), Evidence Generator |
| **Backup Services** | WORM Storage, Restic, LUKS, Certificate Manager |
| **Client Modules** | 6 Compliance Checks (Patching, AV/EDR, Backup, Logging, Firewall, Encryption) |
| **Windows Support** | WinRM Client, Windows Collector, HIPAA Runbooks |

**Key Relationships:**
- NixOS provides the deterministic base for the Compliance Agent
- Agent communicates with MCP Server via mTLS
- Three-tier healing escalates from deterministic rules to LLM to human
- Evidence flows to local storage and WORM (S3 Object Lock)

---

### 2. Data Flow (`data-flow.mermaid`)

**Purpose:** Traces data through the system from compliance checks to client reports.

**Four Main Flows:**

#### Flow 1: Compliance Check Initiation
```
Systemd Timer (60s +-jitter) → Agent Loop → Drift Detector → 6 Checks
```

#### Flow 2: Remediation Trigger Propagation
```
Drift Detected → L1 Deterministic (70-80%)
             → L2 LLM Planner (15-20%)
             → L3 Human Escalation (5-10%)
             → Data Flywheel (L2→L1 promotion)
```

#### Flow 3: Audit Log Generation
```
Pre-State Capture → Actions Recorded → Post-State Capture
                 → Evidence Bundle Created → Ed25519 Signed
                 → Local Storage / Offline Queue / WORM Upload
```

#### Flow 4: Client Report Compilation
```
Query Evidence → Aggregate Metrics → Map to HIPAA Controls
             → Generate Compliance Packet → Executive Summary + Detailed Audit
```

**Evidence Bundle Fields:**
- `site_id`, `host_id`, `deployment_mode`
- `check`, `outcome`, `pre_state`, `post_state`
- `actions`, `timestamps`, `hipaa_controls`
- `nixos_revision`, `rollback_available`, `Ed25519 signature`

---

### 3. Deployment Topology (`deployment-topology.mermaid`)

**Purpose:** Illustrates physical/logical deployment across network boundaries.

**Network Zones:**

| Zone | Description | HIPAA Risk |
|------|-------------|------------|
| **MSP Datacenter** | Central MCP Server, Redis, Evidence DB | Managed |
| **Client Site** | On-prem NixOS Appliance | Protected |
| **System Zone** | syslog, SSH, package hashes | Very Low |
| **Application Zone** | EHR audit logs, auth events | Moderate |
| **Data Zone** | Patient records, PHI | High (Never Ingested) |
| **Backup Destinations** | Local NAS, Off-site S3, WORM Vault | Protected |

**Encrypted Communication Paths:**
- **mTLS :443** - Appliance ↔ MCP Server
- **HTTPS :443** - Appliance → OpenAI API
- **S3 :443** - Appliance → WORM Storage (Object Lock)
- **WinRM :5985/:5986** - Appliance → Windows Servers

**Security Controls:**
- nftables egress allowlist (DNS+Timer refresh)
- PHI scrubbing at edge (Fluent Bit filters)
- LUKS encryption at rest
- Ed25519 signing for evidence bundles

---

## Viewing the Diagrams

### Option 1: GitHub/GitLab (Automatic Rendering)
GitHub and GitLab automatically render `.mermaid` files. Simply view them in the web UI.

### Option 2: VS Code Extension
Install the [Mermaid Preview](https://marketplace.visualstudio.com/items?itemName=bierner.markdown-mermaid) extension.

### Option 3: Mermaid CLI
```bash
# Install
npm install -g @mermaid-js/mermaid-cli

# Generate PNG
mmdc -i system-architecture.mermaid -o system-architecture.png

# Generate SVG
mmdc -i data-flow.mermaid -o data-flow.svg -t dark
```

### Option 4: Online Editor
Paste contents into [Mermaid Live Editor](https://mermaid.live)

---

## Diagram Conventions

### Color Coding

| Color | Meaning |
|-------|---------|
| Blue (#4a90d9) | NixOS Infrastructure |
| Green (#28a745) | MCP Server Components |
| Purple (#6f42c1) | Compliance Agent |
| Orange (#fd7e14) | Backup/Storage Services |
| Teal (#20c997) | Client-Facing Modules |
| Red (#dc3545) | High-Risk / PHI Zone |
| Yellow (#ffc107) | Moderate Risk Zone |

### Line Styles
- **Solid lines** - Primary data flow
- **Dashed lines** - Optional/conditional paths
- **X-lines** - Blocked/prohibited access

---

## Related Documentation

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](../ARCHITECTURE.md) | Detailed system design |
| [HIPAA_FRAMEWORK.md](../HIPAA_FRAMEWORK.md) | Compliance requirements |
| [ROADMAP.md](../ROADMAP.md) | Implementation phases |
| [RUNBOOKS.md](../RUNBOOKS.md) | Remediation patterns |

---

## Updating Diagrams

When modifying these diagrams:

1. Keep consistent with the color scheme
2. Update this README if adding new components
3. Validate syntax at [Mermaid Live](https://mermaid.live)
4. Test rendering in GitHub before committing
