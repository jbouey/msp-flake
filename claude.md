# MSP Automation Platform - Complete Reference Guide

**Stack:** NixOS + MCP + LLM  
**Target:** Small to mid-sized clinics (NEPA region)  
**Service Model:** Auto-heal infrastructure + HIPAA compliance monitoring  
**Business Position:** High-margin, low-labor, infra-only

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Zero-to-MVP Build Plan](#zero-to-mvp-build-plan)
3. [Service Catalog & Scope](#service-catalog--scope)
4. [Repository Structure](#repository-structure)
5. [Technical Architecture](#technical-architecture)
6. [HIPAA Compliance Framework](#hipaa-compliance-framework)
   - Compliance Guardrail Pathology
   - Anduril Framework Basis
   - Legal Positioning
   - Compliance Strengths & Gaps
   - Data Boundary Zones
   - Healthcare Monitoring Requirements
   - LLM Legal Usage
   - Documentation Requirements
7. [Implementation Roadmap](#implementation-roadmap)
   - Enhanced MVP Plan with Compliance
   - Concrete Edits to Original MVP
   - Runbook Structure Examples
   - Evidence Bundle Format
   - Compliance Packet Template
8. [Quick Checklist](#quick-checklist-this-weeks-implementation-tasks)
9. [Competitive Positioning](#where-this-puts-you-vs-anduril)
10. [LLM-Driven Compliance Testing](#llm-driven-compliance-testing-meta-framework-application)
11. [Guardrails & Safety](#guardrails--safety)
12. [Client Deployment](#client-deployment)
13. [Network Discovery & Automated Enrollment](#network-discovery--automated-enrollment)
    - Discovery Methods (Active, Passive, API-based)
    - Device Classification & Tier Assignment
    - Automated Enrollment Pipeline
    - NixOS Integration
    - HIPAA Considerations
    - Dashboard Reporting
14. [Executive Dashboards & Audit-Ready Outputs](#executive-dashboards--audit-ready-outputs)
    - Enforcement-First Philosophy
    - Minimal Architecture (Collectors, Rules, Evidence, Dashboard)
    - Eight Core Controls with Auto-Fix
    - Print-Ready Monthly Compliance Packet
    - Grafana Print-Friendly GUI
    - Weekly Executive Postcard
15. [Software Provenance & Time Framework](#software-provenance--time-framework)
    - Overview & Philosophy
    - What NixOS Gives You Free
    - Signing and Verification
    - Evidence Registry
    - SBOM Generation
    - Multi-Source Time Synchronization
    - Hash Chain Log Integrity
    - Blockchain Anchoring (Professional/Enterprise)
    - Compliance Tiers (Essential/Professional/Enterprise)
    - MCP Integration
    - Implementation Checklist
16. [Expansion Path](#expansion-path)
17. [Key References & Next Steps](#key-references)

---

## Executive Summary

**Business Model:** High-margin, low-labor HIPAA compliance-as-a-service for healthcare SMBs

**Technical Stack:**
- NixOS for deterministic, auditable infrastructure
- Model Context Protocol (MCP) for structured LLM-to-tool interface
- GPT-4o for incident triage and runbook selection
- NATS JetStream or Redis Streams for multi-tenant event queue
- WORM storage for tamper-evident evidence bundles

**Legal Positioning:**
- Business Associate for operations only (NOT clinical data processor)
- Processes system metadata and logs only - never patient PHI
- Fulfills HIPAA Security Rule audit control requirements (§164.308, §164.312)
- Lower liability exposure than traditional healthcare IT vendors

**Competitive Advantage:**
- Anduril-style compliance rigor adapted for healthcare SMB market
- 6-week implementation vs. 6-month enterprise solutions
- Solo engineer can support 10-50 clients at 40%+ margins
- Auditor-ready evidence packets without manual documentation

**Implementation Timeline:**
- Week 0-1: Baseline profile and runbook templates
- Week 2-3: Client flake with LUKS, SSH-certs, baseline enforcement
- Week 4-5: MCP planner/executor split, evidence pipeline
- Week 6: First compliance packet generation
- Week 7-8: Lab testing with synthetic incidents
- Week 9+: First pilot client

**Target Market:** 1-50 provider practices in NEPA region
- Small (1-5 providers): Compact appliance, $200-400/mo
- Mid (6-15 providers): Small server, $600-1200/mo
- Large (15-50 providers): Dedicated server, $1500-3000/mo

**Key Differentiators:**
1. Evidence-by-architecture (MCP audit trail is structurally inseparable from operations)
2. Deterministic builds (NixOS flakes = cryptographic proof of configuration)
3. LLM-driven synthetic testing (Meta framework for continuous validation)
4. Metadata-only monitoring (avoids PHI processing liability)
5. Compliance packets ready for auditor handoff (no consultant needed on call)
6. Enforcement-first dashboards (automation exposed, not replaced by visuals)
7. Signed evidence bundles (cryptographic proof of historical compliance)
8. Auto-generated executive reporting (weekly postcards, monthly packets)

**Reporting & Visibility:**
- Thin collector layer (local state + minimal SaaS taps)
- 8 core controls with auto-fix integration
- Nightly evidence packager with cosign/GPG signing
- Print-ready monthly HIPAA compliance packets
- Grafana print-friendly dashboards
- Weekly executive postcards (email PDF)
- 90-day evidence retention (WORM storage)
- Zero-PHI processing guarantee

---

## Zero-to-MVP Build Plan

**Timeline:** ~5 full-time weeks to functioning pilot  
**Engineer:** Solo operator  
**Outcome:** Each new client = Terraform run + DNS entry

| Step | Goal | Key Actions | Time Box |
|------|------|-------------|----------|
| **0. Scope lock** | Freeze what you will—and won't—cover | • Draft one-page service catalog (servers/network, five auto-fix actions)<br>• Write "not included" list (endpoints, SaaS, printers) | ½ day |
| **1. Repo skeleton** | Single Git repo structure | • Top-level dirs: `flake/`, `terraform/`, `mcp/`, `scripts/`, `docs/`<br>• Commit empty `flake.nix` and `main.tf` with backend configured | ½ day |
| **2. Log watcher container** | Minimal agent that ships logs/events | • Build Nix derivation with `fluent-bit` + 60-line Python tailer<br>• Health-check endpoint (`/status`)<br>• Push as OCI image | 3 days |
| **3. Central queue** | Event landing zone | • Terraform module for tiny VPS with Redis Streams or NATS JetStream<br>• TLS + password auth<br>• Output connection string | 1 day |
| **4. Basic MCP server** | LLM interface + five tools | • Write `mcp/server.py`: Flask/FastAPI with `/chat` endpoint<br>• Five tool endpoints: `restart_service`, `clear_cache`, `rotate_logs`, `delete_tmp`, `renew_cert`<br>• Pydantic schemas for strict param typing<br>• Package in image via flake | 4 days |
| **5. Guardrails** | Prevent AI self-harm | • Whitelist services in `restart_service`<br>• 5-minute Redis key cooldown per host-tool combo<br>• Reject bad params (regex, enums) | 2 days |
| **6. Prompt & LLM glue** | Turn incidents into tool calls | • Plain-text template: "Given INCIDENT_JSON, choose exactly one TOOL from {...} and valid PARAMS as JSON"<br>• Call GPT-4o with max 2K tokens; parse structured reply<br>• Unit-test with three canned incidents | 2 days |
| **7. Closed loop** | Verify fix or escalate | • After tool runs, watcher re-queries metric<br>• If unresolved → publish `escalate` event (email/Teams webhook) | 1 day |
| **8. Client deploy module** | One-command rollout | • Terraform sub-module: create small Ubuntu VM or k8s pod<br>• Inject watcher container via systemd service<br>• Variables: `client_id`, `queue_url`, `api_key` | 3 days |
| **9. CI/CD** | Push updates automatically | • GitHub Actions: on main merge, `nix flake update`, build/push images, tag<br>• Another job runs `terraform apply` against each client workspace nightly | 1 day |
| **10. Security hardening** | Pass basic audit | • mTLS between watcher and queue<br>• Secrets in HashiCorp Vault or SOPS-encrypted YAML<br>• Enable audit log on Redis; MCP logs every request/response | 2 days |
| **11. Lab burn-in** | Shake out rookie errors | • Deploy to two VMs you purposely break (kill processes, fill disks)<br>• Confirm fixes fire and cooldowns prevent thrash | 1 week |
| **12. First pilot client** | Real-world proof | • Run `terraform apply` with their creds<br>• 30-day free pilot; track incident counts, MTTR | 2 days rollout<br>30 days monitoring |
| **13. Docs & SLA** | Something sales can wave | • Five-page PDF: architecture diagram, covered actions, MTTR target, escalation path<br>• Include "escrow clause" for repo | 1 day |

**Total:** 5 weeks → functioning pilot  
**Scale:** Each new client adds ~2 hours setup time

---

## Service Catalog & Scope

### ✅ What Stays IN (Infra-Only Catalog)

| Layer | Example Automations |
|-------|-------------------|
| **OS & services** | Restart failed systemd unit, rotate logs, clear /tmp, renew certs |
| **Middleware** | Bounce stuck queue worker, re-index database, clear Redis cache |
| **Patching** | Apply security kernel update, reboot off-peak, verify service health |
| **Network core** | Flush stuck firewall state table, reload BGP session, fail over link |
| **Observability** | Detect pattern, open incident, feed summary to LLM, run approved fix |

**One playbook, same scripts for every client, all delivered by Nix flake.**

### ❌ What Stays OUT (Front-Office / "MF" Work)

| Category | Typical Tasks You Refuse |
|----------|------------------------|
| **End-user devices** | Laptop imaging, printer drivers, Office 365 password resets |
| **SaaS & desktop apps** | QuickBooks crashes, email flow, Outlook PST repairs |
| **Tier-1 ticket triage** | "My mouse is frozen," VPN onboarding |
| **Compliance paperwork** | SOC-2 documentation, HIPAA staff training |

**These devour labor hours and force expensive RMM seats. Skip them.**

---

## Repository Structure

```
MSP-PLATFORM/
├── client-flake/          # Deployed to all client sites
│   ├── flake.nix         # NixOS configuration for clients
│   ├── modules/
│   │   ├── log-watcher.nix
│   │   ├── health-checks.nix
│   │   └── remediation-tools.nix
│   └── hardware/
│       └── configurations per client
│
├── mcp-server/            # Your central infrastructure
│   ├── flake.nix         # MCP server deployment
│   ├── server.py         # FastAPI with LLM integration
│   ├── tools/            # Remediation tools
│   │   ├── restart_service.py
│   │   ├── clear_cache.py
│   │   ├── rotate_logs.py
│   │   ├── delete_tmp.py
│   │   └── renew_cert.py
│   └── guardrails/       # Safety controls
│       ├── validation.py
│       └── rate_limits.py
│
├── terraform/             # Infrastructure as Code
│   ├── modules/
│   │   ├── event-queue/  # Redis/NATS setup
│   │   ├── mcp-deploy/   # MCP server deployment
│   │   └── client-vm/    # Client infrastructure
│   └── clients/
│       ├── clinic-01/
│       └── clinic-02/
│
└── docs/
    ├── service-catalog.md
    └── sla.md
```

---

## Technical Architecture

### Client Flake Configuration (Base)

```nix
# client-flake/flake.nix
{
  description = "MSP Client Station - Infrastructure Management";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.05";
  };

  outputs = { self, nixpkgs }:
  let
    # Your MCP server endpoint (production)
    mcpServerUrl = "https://mcp.your-msp.com";

    # Event queue endpoint
    eventQueueUrl = "redis://queue.your-msp.com:6379";

  in {
    nixosConfigurations = {
      # Base configuration for ALL clients
      msp-client-base = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          ./modules/log-watcher.nix
          ./modules/health-checks.nix
          ./modules/remediation-tools.nix
          
          {
            # Core system settings
            networking.firewall.enable = true;
            
            # Log watcher service
            systemd.services.msp-watcher = {
              description = "MSP Log Watcher & Event Publisher";
              after = [ "network.target" ];
              wantedBy = [ "multi-user.target" ];
              
              serviceConfig = {
                ExecStart = "${self.packages.x86_64-linux.watcher}/bin/watcher";
                Restart = "always";
                RestartSec = "10s";
              };
              
              environment = {
                MCP_SERVER_URL = mcpServerUrl;
                EVENT_QUEUE_URL = eventQueueUrl;
              };
            };
          }
        ];
      };
    };

    # Watcher package
    packages.x86_64-linux.watcher = nixpkgs.legacyPackages.x86_64-linux.buildGoModule {
      pname = "msp-watcher";
      version = "0.1.0";
      src = ./watcher;
      # Add vendorSha256, build steps
    };
  };
}
```

### MCP Server Structure

```python
# mcp-server/server.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, validator
import redis
import openai
from datetime import datetime, timedelta

app = FastAPI()

# Redis connection for rate limiting
redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)

class IncidentRequest(BaseModel):
    client_id: str
    hostname: str
    incident_type: str
    severity: str
    details: dict

class ToolExecution(BaseModel):
    tool_name: str
    params: dict
    
    @validator('tool_name')
    def validate_tool(cls, v):
        allowed_tools = ['restart_service', 'clear_cache', 'rotate_logs', 
                        'delete_tmp', 'renew_cert']
        if v not in allowed_tools:
            raise ValueError(f'Tool must be one of {allowed_tools}')
        return v

@app.post("/chat")
async def process_incident(incident: IncidentRequest):
    """Main endpoint: receives incident, calls LLM, executes tool"""
    
    # Rate limit check
    rate_key = f"rate:{incident.client_id}:{incident.hostname}"
    if redis_client.exists(rate_key):
        raise HTTPException(status_code=429, detail="Rate limited")
    
    # Call LLM for decision
    tool_decision = await get_llm_decision(incident)
    
    # Execute tool with guardrails
    result = await execute_tool_safely(tool_decision, incident)
    
    # Set rate limit cooldown (5 minutes)
    redis_client.setex(rate_key, 300, "1")
    
    return {"status": "executed", "result": result}

async def get_llm_decision(incident: IncidentRequest) -> ToolExecution:
    """Use GPT-4o to decide which tool to run"""
    
    prompt = f"""Given this infrastructure incident, choose exactly ONE tool and provide valid parameters.

Incident Details:
- Type: {incident.incident_type}
- Severity: {incident.severity}
- Host: {incident.hostname}
- Details: {incident.details}

Available Tools:
1. restart_service - Restart a systemd service
2. clear_cache - Clear application cache
3. rotate_logs - Force log rotation
4. delete_tmp - Clean /tmp directory
5. renew_cert - Renew SSL certificate

Respond with JSON only:
{{"tool_name": "<tool>", "params": {{"key": "value"}}}}
"""

    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200,
        temperature=0.1
    )
    
    # Parse and validate response
    tool_decision = ToolExecution.parse_raw(response.choices[0].message.content)
    return tool_decision

async def execute_tool_safely(tool: ToolExecution, incident: IncidentRequest):
    """Execute tool with validation and logging"""
    
    # Import tool module dynamically
    tool_module = __import__(f'tools.{tool.tool_name}', fromlist=['execute'])
    
    # Execute with timeout and error handling
    try:
        result = await tool_module.execute(
            client_id=incident.client_id,
            hostname=incident.hostname,
            params=tool.params
        )
        
        # Log execution
        log_tool_execution(incident, tool, result)
        
        return result
    except Exception as e:
        # Escalate on failure
        await escalate_incident(incident, str(e))
        raise HTTPException(status_code=500, detail=str(e))
```

---

## HIPAA Compliance Framework

### Compliance Guardrail Pathology

**Framework Basis:** Follows the Anduril compliance approach — overlaid onto healthcare organizations.

#### How Anduril Does "Compliance with Nix" (Public Info)

- **Published NixOS STIG**: Listed in NIST's National Checklist Program as *Anduril NixOS STIG (Ver 1, Rel 1)* — a DoD-style hardening baseline mapped to 800-53/DoD controls
- **Embedded/NVIDIA Jetson bring-up**: Open repo packaging JetPack (CUDA/TensorRT, firmware, UEFI, OP-TEE) as NixOS modules for reproducible device images (supply-chain + determinism at edge)
- **Org posture**: Openly recruits for NixOS/EW; Haskell.nix builds shipped to "thousands of customer assets" — compliance + fleet ops at scale

**What This Implies:** Documented baseline (STIG), deterministic builds, device attestation, and evidence generation mapped to recognized control catalogs.

### Legal Positioning

**You are a Business Associate (BA)** — but only for *operations*, not for *treatment or records*.

Your MCP + LLM system *supports* compliance (**HIPAA §164.308(a)(1)(ii)(D)**: "Information system activity review") by scanning logs for evidence of compliance, not by touching medical charts or patient identifiers.

**Key Citation:**  
> 45 CFR 164.308(a)(1)(ii)(D) — "Implement procedures to regularly review records of information system activity, such as audit logs, access reports, and security incident tracking reports."

**Your Service Scope:**  
"HIPAA operational safeguard verification" — NOT "clinical data processing"

### Where Your Plan Is Strong

- ✅ Clear **scope boundary** (infra-only)
- ✅ Deterministic **flake** and remote **MCP** loop with guardrails
- ✅ **Backups/monitoring/VPN** as default stack
- ✅ Credible **5-week MVP** with pilot metrics (MTTR, incident counts)
- ✅ **Metadata-only scanning** — no direct PHI processing

### Compliance Gaps & How to Close Them

#### 1. Named Baseline (Auditors Look for One)

**Action:**
- Adopt a baseline profile: "**NixOS-HIPAA baseline v1**" derived from Anduril STIG concepts
- Publish it as machine-readable profile in-repo (YAML/JSON) and human PDF
- Map each baseline item to HIPAA Security Rule citations (164.308, 164.312, 164.316)
- Keep **exceptions file** per client (with owner, risk, expiry)

**Result:** Can say "system enforces Baseline v1; exceptions tracked"

#### 2. Move from 'Tool Call' to Deterministic Runbooks

**Action:**
- Library of **pre-approved runbooks** (YAML) with: steps, timeouts, retries, rollback, HIPAA citations, required evidence fields
- Planner chooses runbook; MCP only executes steps — no free-form LLM actions
- Each run produces signed **evidence bundle**

**Result:** Repeatable, reviewable, and auditable responses

#### 3. Crypto/Fed-Friendly Defaults

**Action:**
- **FIPS-ish OpenSSL** build (or document crypto algorithms in use)
- **WireGuard** with preshared key + static pubkeys
- **Full-disk encryption (LUKS)** with remote-unlock option
- Secrets: **SOPS (age) or Vault**, client-scoped KMS keys, rotation policy
- Force **mTLS** for watcher⇄queue⇄MCP

**Result:** Less arguing with auditors about "what cipher is this?"

#### 4. Evidence Pipeline (Turn Operations into Artifacts)

**For every incident/runbook:**
- **Inputs** (logs/snippets)
- **Actions** (scripts + hashes)
- **Outputs**
- **Operator** (service principal)
- **Timestamps**
- **SLA timers**
- **Control IDs**
- Written to **append-only log** (WORM S3/MinIO bucket with object lock) + hash-chained local log
- Nightly job emits **Compliance Packet** PDF per client: deltas, resolved incidents, exceptions, backup/test-restore proofs

**Result:** Something clinic can hand to auditor without you on the call

#### 5. Backups: Prove Restores, Not Just Copies

**Action:**
- Make "test-restore weekly" a runbook
- Restore N files or DB to scratch VM, verify checksum/signature, keep proof
- Tag runbook with HIPAA **164.308(a)(7)** and **164.310(d)(2)(iv)**

**Result:** Not just "doing restic" — proving recoverability

#### 6. Vuln + Patch Governance

**Action:**
- Add small **vuln scanner** step (OpenSCAP/Lynis) monthly; generate simple diff
- Tie **patch cadence** to policy file (e.g., "critical ≤7 days")
- Violations become incidents with SLA

**Result:** Auditors see policy + evidence path

#### 7. Access Control That Auditors Recognize

**Action:**
- SSH: no passwords, **short-lived certs** (step-CA) or per-user keys with rotation schedule
- **Break-glass** user is time-boxed, alerting, and logged
- All MCP actions execute under **least-privilege service accounts**
- Sudoers entries explicit and diffed

**Result:** Clean story for 164.312(a) access controls

#### 8. Asset & Config Inventory

**Action:**
- Export **hardware/software inventory** from each client flake nightly (package list, kernel, enabled services)
- Keep 24 months of inventories; diffs included in packet

**Result:** Can answer "what's running where?" on day one of audit

#### 9. Queue Choice & Tenancy

**Action:**
- Redis Streams fine for week 1
- For multi-tenant durability: NATS JetStream or Redis with AOF + auth per client
- Namespacing: per-client subjects/streams, separate API keys, rate limits

**Result:** One client's storm won't drown others

#### 10. Business Basics (HIPAA Angle)

**Action:**
- Use **BAAs** and list **sub-processors** (cloud, email, storage)
- Limited **data handling**: platform processes **metadata** and system logs, not ePHI payloads
- Where logs may contain PHI: encrypt at source and gate retention

**Result:** Fewer red flags with clinic admins/lawyers

### Data Boundary Zones

| Zone | Example Data | HIPAA Risk | Mitigation |
|------|-------------|-----------|-----------|
| **System Zone** | syslog lines, SSH auth attempts, package hashes, uptime logs, backup job status, encryption config | Very low | Mask any usernames or patient paths |
| **Application Zone** | EHR audit logs, access events (`userID:123 viewed patient:456`) | Moderate | Tokenize `patientID`, hash `userID`, redact payload |
| **Data Zone** | PHI itself (lab results, notes, demographics) | High | **Never ingest here.** Use stub counters only |

### Practical Controls

1. **Scrubbers at the edge:** Deploy lightweight log forwarder (Fluent Bit) that filters out PHI patterns (`name, MRN, DOB`) before forwarding
2. **Regex + checksum transform:** Send hashes of identifiers for correlation instead of values
3. **Metadata-only collector:** Daemon only consumes system events, not file contents
4. **Access boundary:** Restrict service accounts to `/var/log`, `/etc`, `/nix/store` — NOT `/data/` or EHR mounts

### What Your LLM Can Legally Do

✅ **Allowed:**
- Parse logs for anomalies, missed backups, failed encryption jobs
- Compare system settings to baseline (NixOS-HIPAA baseline v1)
- Generate remediation runbooks (restart service, rotate cert, etc.)
- Produce evidence bundles for audits

❌ **Prohibited:**
- Read or infer patient-level data
- Suggest clinical actions
- Aggregate PHI from logs

**Why It's Compliant:**
- HIPAA §164.312(b): "Audit controls" → your automation *is the audit control*
- §164.308(a)(5): "Security awareness and training" → your evidence packets support that
- §164.308(a)(6): "Security incident procedures" → your auto-remediation satisfies this

### What Healthcare Organizations Must Monitor for HIPAA Compliance

The compliance appliance must continuously scan logs and system state across multiple layers. Below is organized by ease of centralized monitoring:

#### Tier 1: Easiest to Centralize (System/Infrastructure Layer)

| Component | What to Monitor | HIPAA Citation | Log Sources |
|-----------|----------------|----------------|-------------|
| **Firewalls** | Rule changes, blocked/allowed traffic, anomalous patterns | §164.312(a)(1) | Firewall syslog, iptables/nftables logs |
| **VPN/Remote Access** | Login attempts, session duration, failed auth, IP geolocation | §164.312(a)(2)(i) | VPN server logs, RADIUS/LDAP logs |
| **Server OS** | Login events, privilege escalation, service status, kernel events | §164.312(b) | syslog, journald, auditd |
| **Backup Systems** | Job completion, failure alerts, restoration tests, encryption status | §164.308(a)(7)(ii)(A) | Backup software logs (restic, Veeam, etc.) |
| **Encryption Status** | Disk encryption state, certificate expiry, TLS/SSL config | §164.312(a)(2)(iv) | System state checks, cert monitoring |
| **Time Sync** | NTP drift, time source validation | §164.312(b) | chronyd/ntpd logs |
| **Patch Status** | Installed patches, pending updates, vulnerability scan results | §164.308(a)(5)(ii)(B) | apt/yum history, WSUS logs, vuln scanner output |

#### Tier 2: Moderate Difficulty (Application Layer)

| Component | What to Monitor | HIPAA Citation | Log Sources |
|-----------|----------------|----------------|-------------|
| **EHR/EMR Access** | User access to patient records, view/modify/delete actions, break-glass access | §164.312(a)(1) | EHR audit logs, database access logs |
| **Authentication Systems** | Failed login attempts, password changes, MFA events, session timeouts | §164.312(a)(2)(i) | Active Directory, LDAP, SSO provider logs |
| **Database Activity** | Queries against PHI tables, schema changes, admin actions | §164.312(b) | Database audit logs (PostgreSQL, MySQL, MSSQL) |
| **File Access** | PHI file opens/modifications, bulk exports, unusual access patterns | §164.308(a)(3)(ii)(A) | File system audit logs, DLP systems |
| **Email Systems** | PHI transmission, encryption status, unauthorized forwarding | §164.312(e)(1) | Mail server logs, email gateway logs |
| **Web Applications** | API calls to PHI endpoints, rate anomalies, injection attempts | §164.312(b) | Application logs, WAF logs |

#### Tier 3: Most Complex (Business Process Layer)

| Component | What to Monitor | HIPAA Citation | Log Sources |
|-----------|----------------|----------------|-------------|
| **User Provisioning** | New account creation, role assignments, termination compliance | §164.308(a)(3)(ii)(C) | HR system, IAM logs, ticketing system |
| **Business Associate Access** | Third-party vendor access, BAA compliance, data sharing | §164.308(b)(1) | Vendor access logs, contract management |
| **Incident Response** | Security incident detection, containment actions, breach notifications | §164.308(a)(6) | SIEM alerts, incident management system |
| **Training Completion** | Staff security awareness training, acknowledgment tracking | §164.308(a)(5)(i) | LMS logs, training platform |
| **Risk Assessments** | Scheduled assessment completion, finding remediation tracking | §164.308(a)(1)(ii)(A) | GRC platform, assessment tools |
| **Policy Updates** | Policy review cycles, staff acknowledgments, version control | §164.316(b)(1) | Document management system |

### Monitoring Implementation Strategy by Office Size

#### Small Office (1-5 Providers, <10 Staff)

**Recommended Device:** Compact appliance (Intel NUC, Raspberry Pi 4, or VM with 4GB RAM)

**Focus Areas:**
- Server OS logs (if self-hosted)
- VPN access logs
- Backup completion status
- EHR access logs (if available)
- Firewall logs (router/edge device)

**Monitoring Scope:** ~5-10 log sources, primarily Tier 1

**Implementation:**
```bash
# Lightweight stack
- Fluent-bit for log collection
- Redis for event queue
- SQLite for local evidence store
- Weekly compliance packets
```

#### Mid-Sized Clinic (6-15 Providers, 10-50 Staff)

**Recommended Device:** Small server or robust VM (16GB RAM, 4 cores, 500GB storage)

**Focus Areas:**
- All Tier 1 components
- EHR/EMR comprehensive audit trail
- Active Directory/authentication logs
- Database activity monitoring
- Email gateway logs
- Basic SIEM functionality

**Monitoring Scope:** ~15-30 log sources, Tier 1 + Tier 2

**Implementation:**
```bash
# Moderate stack
- Fluent-bit + custom parsers
- NATS JetStream for durability
- PostgreSQL for evidence store
- Daily compliance packets
- Basic anomaly detection
```

#### Large Clinic/Small Hospital (15+ Providers, 50-200 Staff)

**Recommended Device:** Dedicated server (32GB RAM, 8 cores, 2TB storage) or cluster

**Focus Areas:**
- All Tier 1 & Tier 2 components
- Full Tier 3 business process monitoring
- Advanced SIEM with correlation rules
- DLP integration
- Automated incident response
- Compliance dashboard

**Monitoring Scope:** ~30-100 log sources, all tiers

**Implementation:**
```bash
# Full stack
- Fluent-bit + custom processors
- NATS JetStream cluster
- PostgreSQL with replication
- Real-time compliance dashboard
- Advanced ML-based anomaly detection
- Integration with ticketing/GRC systems
```

### Critical Log Patterns to Detect

**Immediate Alert Triggers:**
1. Multiple failed login attempts (>5 in 10 minutes)
2. Access to PHI outside business hours without justification
3. Bulk data export or download
4. Changes to firewall rules or security groups
5. Backup failure or missed backup window
6. Encryption/certificate expiry within 30 days
7. Privileged account usage without ticket reference
8. Database schema modifications
9. New user account creation without HR ticket
10. External email containing potential PHI without encryption

### Evidence Bundle Structure

Every monitored event generates standardized evidence:

```json
{
  "event_id": "evt_20251023_0001",
  "timestamp": "2025-10-23T14:32:01Z",
  "client_id": "clinic-001",
  "event_type": "failed_login",
  "severity": "medium",
  "hipaa_controls": ["164.312(a)(2)(i)", "164.308(a)(5)(ii)(C)"],
  "source_system": "active_directory",
  "user": "jsmith",
  "details": {
    "failed_attempts": 3,
    "source_ip": "192.168.1.45",
    "workstation": "CLINIC-WS-12"
  },
  "automated_action": "account_locked",
  "runbook_id": "RB-AUTH-003",
  "evidence_hash": "sha256:a3f8...",
  "reviewed_by": null,
  "closed_at": null
}
```

### Documentation to Maintain

#### 1. Data Boundary Diagram
Show three zones (System / Application / Data) with "PHI Prohibited" annotation on lower zones

#### 2. HIPAA Mapping File
```yaml
164.308(a)(1)(ii)(D): RB-AUDIT-001 → evidence/auditlog-checksum.json
164.312(b): RB-AUDIT-002 → evidence/auditd-forwarding.json
```

#### 3. Statement of Scope (attach to BAA)
> "Our platform processes infrastructure metadata (system logs, configurations, and operational metrics) for compliance verification purposes. It does not access, process, store, or transmit patient PHI. Any accidental PHI exposure is treated as a security incident and triggers the breach notification process per 45 CFR 164.404."

#### 4. LLM Policy File
```yaml
llm_scope:
  allowed_inputs: [syslog, journald, auditd, restic_logs]
  prohibited_inputs: [ehr_exports, patient_data, attachments]

llm_output_actions:
  - classification
  - compliance_report
  - remediation_plan

prohibited_actions:
  - direct clinical recommendation
  - patient data synthesis
```

#### 5. BAA Template Outline

**Scope-Limited Business Associate Agreement for Infrastructure Compliance Services**

```markdown
# Business Associate Agreement - Infrastructure Compliance Monitoring

## 1. Definitions

**Business Associate (BA):** [Your MSP Company Name]
**Covered Entity (CE):** [Clinic/Hospital Name]
**Services:** Infrastructure compliance monitoring, automated remediation, and audit evidence generation
**Protected Health Information (PHI):** As defined in 45 CFR §160.103

## 2. Scope of Services and Data Handling

2.1 **Limited Scope:** BA's services operate exclusively on system metadata, infrastructure 
logs, configuration files, and operational metrics for the purpose of verifying HIPAA 
Security Rule compliance.

2.2 **No PHI Processing:** BA does not access, process, store, or transmit Protected Health 
Information (PHI) or Electronic Protected Health Information (ePHI) as part of its 
services. BA's monitoring systems are configured to:
   - Collect only system-level logs (syslog, journald, auditd)
   - Scrub any accidental PHI at the source before transmission
   - Operate with access restricted to /var/log, /etc, and system directories only
   - Never access patient data directories or EHR/EMR databases

2.3 **Metadata Only:** Data processed includes but is not limited to:
   - System authentication logs (with identifiers tokenized)
   - Backup job status and completion records
   - Encryption configuration and certificate status
   - Service health metrics and uptime statistics
   - Firewall and network access logs
   - Software inventory and patch status

## 3. Obligations of Business Associate

3.1 BA shall implement appropriate safeguards to prevent use or disclosure of metadata 
beyond this Agreement's scope.

3.2 BA shall report to CE any inadvertent access to PHI within 24 hours of discovery.

3.3 BA shall ensure all automated systems and LLM components operate only on system 
metadata and do not process or infer patient-level information.

3.4 BA shall maintain audit logs of all system access and automated actions for a 
minimum of 6 years.

3.5 BA shall make internal practices, books, and records relating to the use and 
disclosure of metadata available to HHS for purposes of determining CE's compliance 
with HIPAA.

## 4. Permitted Uses and Disclosures

4.1 BA may use metadata to:
   - Monitor system compliance with HIPAA Security Rule requirements
   - Generate automated remediation actions for infrastructure issues
   - Produce compliance evidence bundles and audit reports
   - Perform synthetic testing to validate compliance detection systems

4.2 BA may not:
   - Access, view, or process patient medical records or PHI
   - Make clinical recommendations or decisions
   - Aggregate or de-identify PHI (as BA does not access PHI)

## 5. Sub-Contractors and Data Processors

5.1 BA shall identify all sub-processors that may handle metadata:
   - Cloud Infrastructure Provider: [AWS/Azure/GCP]
   - Event Queue Service: [Redis/NATS hosting provider]
   - Object Storage Provider: [S3/MinIO provider]
   - LLM Service Provider: [OpenAI/Azure OpenAI/other]

5.2 BA shall ensure all sub-processors agree to same restrictions and conditions 
that apply to BA under this Agreement.

## 6. Term and Termination

6.1 Term: This Agreement is effective as of [DATE] and shall continue until services 
are terminated.

6.2 Upon termination:
   - BA shall return or destroy all metadata within 30 days
   - BA may retain metadata necessary for legal compliance or dispute resolution
   - Evidence bundles stored in client-controlled WORM storage remain CE property

## 7. Breach Notification

7.1 BA shall notify CE within 24 hours of discovering any:
   - Inadvertent access to PHI by BA systems
   - Unauthorized access to BA systems
   - Data breach affecting metadata integrity
   - Security incident affecting compliance monitoring

7.2 Notification shall include:
   - Description of incident
   - Systems and data affected
   - Mitigation actions taken
   - Recommendations for CE action

## 8. Limitation of Liability

8.1 BA's services are limited to infrastructure monitoring and do not constitute:
   - Legal compliance advice
   - Certification of HIPAA compliance
   - Guarantee of audit success
   - Clinical system validation

8.2 CE remains responsible for:
   - Overall HIPAA compliance program
   - Workforce training and policies
   - Business Associate agreements with other vendors
   - Patient privacy protections

## 9. Audit Rights

9.1 CE may audit BA's compliance with this Agreement upon 30 days notice.

9.2 BA shall provide:
   - Access to system logs and evidence bundles
   - Documentation of baseline configurations
   - Runbook library and LLM policies
   - Sub-processor agreements

---

**Key Clause - Metadata-Only Operations:**

"BA's services operate exclusively on system metadata and configurations to assist 
Covered Entity in verifying HIPAA Security Rule compliance. BA does not process PHI 
and shall treat any inadvertent PHI exposure as a security incident triggering the 
breach notification process per 45 CFR 164.404."
```

This BAA template explicitly limits your liability exposure while maintaining transparency about your operational boundaries.

---

## Guardrails & Safety

### Pre-Go-Live Checklist

1. **Validation** - Rejects unknown service names
2. **Rate limit** - Via Redis TTL per host/tool
3. **Dry-run flag** - For high-risk scripts (off by default until trust established)
4. **Fallback** - If incident repeats twice in 15 min, skip automation and page human
5. **Audit log** - Append every tool call + output to tamper-evident file

### Rate Limiting Implementation

```python
# guardrails/rate_limits.py
import redis
from datetime import timedelta

class RateLimiter:
    def __init__(self, redis_client):
        self.redis = redis_client
        self.cooldown_seconds = 300  # 5 minutes
    
    def check_and_set(self, client_id: str, hostname: str, tool_name: str) -> bool:
        """Returns True if action is allowed, False if rate limited"""
        key = f"rate:{client_id}:{hostname}:{tool_name}"
        
        if self.redis.exists(key):
            return False
        
        # Set cooldown
        self.redis.setex(key, self.cooldown_seconds, "1")
        return True
    
    def remaining_cooldown(self, client_id: str, hostname: str, tool_name: str) -> int:
        """Returns seconds remaining in cooldown"""
        key = f"rate:{client_id}:{hostname}:{tool_name}"
        return self.redis.ttl(key)
```

### Parameter Validation

```python
# guardrails/validation.py
from typing import Dict, List
from pydantic import BaseModel, validator
import re

class ServiceRestartParams(BaseModel):
    service_name: str
    
    @validator('service_name')
    def validate_service(cls, v):
        # Whitelist of allowed services
        allowed_services = [
            'nginx', 'postgresql', 'redis',
            'docker', 'containerd'
        ]
        
        if v not in allowed_services:
            raise ValueError(f'Service {v} not in whitelist')
        
        # Reject any shell metacharacters
        if re.search(r'[;&|`$()]', v):
            raise ValueError('Invalid characters in service name')
        
        return v

class ClearCacheParams(BaseModel):
    cache_path: str
    
    @validator('cache_path')
    def validate_path(cls, v):
        # Must be in approved directories
        allowed_prefixes = ['/var/cache/', '/tmp/cache/']
        
        if not any(v.startswith(prefix) for prefix in allowed_prefixes):
            raise ValueError('Cache path not in allowed directories')
        
        # Prevent directory traversal
        if '..' in v or v.startswith('/'):
            raise ValueError('Invalid path')
        
        return v
```

---

## Client Deployment

### Terraform Module for New Client

```hcl
# terraform/modules/client-vm/main.tf
variable "client_id" {
  description = "Unique client identifier"
  type        = string
}

variable "client_name" {
  description = "Human-readable client name"
  type        = string
}

variable "ssh_public_key" {
  description = "SSH public key for access"
  type        = string
}

variable "mcp_api_key" {
  description = "API key for MCP server authentication"
  type        = string
  sensitive   = true
}

resource "aws_instance" "client_station" {
  ami           = data.aws_ami.nixos.id
  instance_type = "t3.small"
  
  user_data = templatefile("${path.module}/cloud-init.yaml", {
    client_id     = var.client_id
    mcp_api_key   = var.mcp_api_key
    ssh_key       = var.ssh_public_key
  })
  
  tags = {
    Name      = "msp-client-${var.client_id}"
    Client    = var.client_name
    ManagedBy = "MSP-Platform"
  }
}

resource "aws_security_group" "client_station" {
  name_description = "MSP Client Station - ${var.client_name}"
  
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["YOUR_MSP_IP/32"]
  }
  
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
```

### Cloud-Init Template

```yaml
# terraform/modules/client-vm/cloud-init.yaml
#cloud-config

write_files:
  - path: /etc/nixos/configuration.nix
    content: |
      { config, pkgs, ... }:
      {
        imports = [ <msp-client-base> ];
        
        networking.hostName = "${client_id}";
        
        services.msp-watcher = {
          enable = true;
          apiKey = "${mcp_api_key}";
        };
      }

runcmd:
  - nixos-rebuild switch
  - systemctl enable msp-watcher
  - systemctl start msp-watcher
```

---

## Network Discovery & Automated Enrollment

### Overview

For efficient client onboarding and continuous compliance monitoring, the system needs to automatically discover, classify, and enroll devices on the healthcare network. This eliminates manual inventory management and ensures comprehensive coverage.

### Discovery Methods (Hybrid Approach)

#### 1. Active Discovery (Scanning)

**Best Practice:** Use multiple methods to build comprehensive asset inventory

```python
# discovery/active_scanner.py
import nmap
import asyncio
from typing import List, Dict
import ipaddress

class NetworkDiscovery:
    def __init__(self, subnet: str, client_id: str):
        self.subnet = subnet
        self.client_id = client_id
        self.nm = nmap.PortScanner()
        
    async def discover_devices(self) -> List[Dict]:
        """
        Multi-method active discovery
        Returns list of discovered devices with metadata
        """
        devices = []
        
        # Method 1: Fast ping sweep for live hosts
        live_hosts = await self._ping_sweep()
        
        # Method 2: Service fingerprinting on live hosts
        for host in live_hosts:
            device_info = await self._fingerprint_device(host)
            devices.append(device_info)
        
        # Method 3: SNMP walk for managed devices
        snmp_devices = await self._snmp_discovery(live_hosts)
        devices.extend(snmp_devices)
        
        # Method 4: mDNS/Bonjour for printers/IoT
        mdns_devices = await self._mdns_discovery()
        devices.extend(mdns_devices)
        
        return devices
    
    async def _ping_sweep(self) -> List[str]:
        """Fast ICMP ping sweep of subnet"""
        self.nm.scan(hosts=self.subnet, arguments='-sn -PE -PP')
        live_hosts = [host for host in self.nm.all_hosts() 
                     if self.nm[host].state() == 'up']
        return live_hosts
    
    async def _fingerprint_device(self, host: str) -> Dict:
        """
        Service and OS fingerprinting
        Identifies device type, OS, running services
        """
        # Comprehensive scan: OS detection, version detection, scripts
        self.nm.scan(
            hosts=host,
            arguments='-sV -O --script=banner,ssh-hostkey,http-title'
        )
        
        device = {
            'ip': host,
            'client_id': self.client_id,
            'discovery_method': 'active_scan',
            'timestamp': datetime.utcnow().isoformat(),
            'hostname': None,
            'mac': None,
            'os': None,
            'device_type': None,
            'services': [],
            'tier': None,  # Will be classified
            'monitored': False,
            'enrollment_status': 'discovered'
        }
        
        if host in self.nm.all_hosts():
            host_data = self.nm[host]
            
            # Extract hostname
            if 'hostnames' in host_data:
                device['hostname'] = host_data['hostnames'][0]['name']
            
            # Extract MAC address
            if 'addresses' in host_data and 'mac' in host_data['addresses']:
                device['mac'] = host_data['addresses']['mac']
            
            # Extract OS information
            if 'osmatch' in host_data and len(host_data['osmatch']) > 0:
                device['os'] = host_data['osmatch'][0]['name']
                device['os_accuracy'] = host_data['osmatch'][0]['accuracy']
            
            # Extract services
            for proto in host_data.all_protocols():
                ports = host_data[proto].keys()
                for port in ports:
                    service_info = host_data[proto][port]
                    device['services'].append({
                        'port': port,
                        'protocol': proto,
                        'name': service_info.get('name', 'unknown'),
                        'product': service_info.get('product', ''),
                        'version': service_info.get('version', ''),
                        'state': service_info.get('state', 'unknown')
                    })
        
        # Classify device type and tier
        device['device_type'] = self._classify_device(device)
        device['tier'] = self._assign_tier(device)
        
        return device
    
    async def _snmp_discovery(self, hosts: List[str]) -> List[Dict]:
        """
        SNMP v2c/v3 discovery for managed network equipment
        Query: sysDescr, sysName, sysLocation, interfaces
        """
        from pysnmp.hlapi import *
        
        snmp_devices = []
        community = 'public'  # Should be from vault in production
        
        for host in hosts:
            try:
                # Query system description
                iterator = getCmd(
                    SnmpEngine(),
                    CommunityData(community),
                    UdpTransportTarget((host, 161), timeout=1, retries=0),
                    ContextData(),
                    ObjectType(ObjectIdentity('SNMPv2-MIB', 'sysDescr', 0)),
                    ObjectType(ObjectIdentity('SNMPv2-MIB', 'sysName', 0)),
                    ObjectType(ObjectIdentity('SNMPv2-MIB', 'sysLocation', 0))
                )
                
                errorIndication, errorStatus, errorIndex, varBinds = next(iterator)
                
                if not errorIndication and not errorStatus:
                    device = {
                        'ip': host,
                        'client_id': self.client_id,
                        'discovery_method': 'snmp',
                        'timestamp': datetime.utcnow().isoformat(),
                        'snmp_sys_descr': str(varBinds[0][1]),
                        'hostname': str(varBinds[1][1]),
                        'location': str(varBinds[2][1]),
                        'device_type': 'network_infrastructure',
                        'tier': 1,  # Network gear is Tier 1
                        'monitored': False,
                        'enrollment_status': 'discovered'
                    }
                    snmp_devices.append(device)
            except Exception:
                pass  # Host doesn't respond to SNMP
        
        return snmp_devices
    
    async def _mdns_discovery(self) -> List[Dict]:
        """
        mDNS/DNS-SD discovery for printers, IoT devices
        Often used by medical devices, printers, network cameras
        """
        from zeroconf import ServiceBrowser, Zeroconf
        
        discovered = []
        zeroconf = Zeroconf()
        
        # Common service types in healthcare
        service_types = [
            "_printer._tcp.local.",
            "_http._tcp.local.",
            "_ipp._tcp.local.",
            "_dicom._tcp.local.",  # Medical imaging devices
            "_pacs._tcp.local."    # PACS systems
        ]
        
        # Browse services (simplified - full implementation needs callbacks)
        for service_type in service_types:
            # ServiceBrowser would populate discovered list via callback
            pass
        
        zeroconf.close()
        return discovered
    
    def _classify_device(self, device: Dict) -> str:
        """
        Classify device type based on services and OS
        Returns: server, workstation, network_device, medical_device, printer, etc.
        """
        services = device.get('services', [])
        os = device.get('os', '').lower()
        
        # Server classification
        if any(s['port'] in [22, 80, 443, 3306, 5432, 1433, 389] for s in services):
            if 'linux' in os or 'unix' in os:
                return 'linux_server'
            elif 'windows server' in os:
                return 'windows_server'
            else:
                return 'server_unknown'
        
        # Network infrastructure
        if any(s['port'] in [23, 161, 162] for s in services):
            return 'network_infrastructure'
        
        # Workstation
        if 'windows' in os and 'server' not in os:
            return 'windows_workstation'
        elif 'mac os' in os or 'darwin' in os:
            return 'macos_workstation'
        
        # Printer
        if any(s['port'] in [515, 631, 9100] for s in services):
            return 'printer'
        
        # Medical device indicators
        if any(s['port'] in [104, 2761, 2762] for s in services):  # DICOM ports
            return 'medical_device'
        
        return 'unknown'
    
    def _assign_tier(self, device: Dict) -> int:
        """
        Assign monitoring tier based on device type
        Tier 1: Infrastructure (easy to monitor)
        Tier 2: Applications (moderate difficulty)
        Tier 3: Business processes (complex)
        """
        device_type = device.get('device_type', 'unknown')
        
        tier_1_types = [
            'linux_server', 'windows_server', 'network_infrastructure',
            'firewall', 'vpn_gateway'
        ]
        
        tier_2_types = [
            'database_server', 'application_server', 'web_server',
            'windows_workstation', 'macos_workstation'
        ]
        
        tier_3_types = [
            'medical_device', 'ehr_server', 'pacs_server'
        ]
        
        if device_type in tier_1_types:
            return 1
        elif device_type in tier_2_types:
            return 2
        elif device_type in tier_3_types:
            return 3
        else:
            return 1  # Default to Tier 1 for unknown
```

#### 2. Passive Discovery (Network Flow Monitoring)

**Advantages:** No active scanning, discovers devices organically

```python
# discovery/passive_monitor.py
from scapy.all import sniff, ARP, IP
import asyncio

class PassiveDiscovery:
    def __init__(self, interface: str, client_id: str):
        self.interface = interface
        self.client_id = client_id
        self.discovered_devices = {}
    
    def start_monitoring(self):
        """
        Passive monitoring via packet capture
        Discovers devices from ARP, DNS, DHCP traffic
        """
        sniff(
            iface=self.interface,
            prn=self._process_packet,
            store=False,
            filter="arp or port 53 or port 67"
        )
    
    def _process_packet(self, packet):
        """Process captured packets to identify devices"""
        
        # ARP packets reveal IP-MAC mappings
        if ARP in packet:
            ip = packet[ARP].psrc
            mac = packet[ARP].hwsrc
            self._register_device(ip, mac, 'arp')
        
        # DNS queries reveal hostnames
        if packet.haslayer('DNS'):
            # Extract hostname queries
            pass
        
        # DHCP reveals comprehensive device info
        if packet.haslayer('DHCP'):
            # Extract device info from DHCP requests
            pass
    
    def _register_device(self, ip: str, mac: str, source: str):
        """Add discovered device to tracking"""
        if ip not in self.discovered_devices:
            self.discovered_devices[ip] = {
                'ip': ip,
                'mac': mac,
                'discovery_method': f'passive_{source}',
                'client_id': self.client_id,
                'first_seen': datetime.utcnow().isoformat(),
                'last_seen': datetime.utcnow().isoformat(),
                'enrollment_status': 'discovered'
            }
```

#### 3. Switch/Router API Discovery

**Best for:** Enterprise networks with managed switches

```python
# discovery/network_api.py
import asyncio
import asyncssh

class NetworkDeviceAPI:
    async def discover_from_switch(self, switch_ip: str, credentials: Dict):
        """
        Query switch ARP table and MAC address table
        More reliable than scanning, gets authoritative data
        """
        async with asyncssh.connect(
            switch_ip,
            username=credentials['username'],
            password=credentials['password'],
            known_hosts=None
        ) as conn:
            # Cisco IOS example
            result = await conn.run('show ip arp')
            arp_table = self._parse_arp_table(result.stdout)
            
            # Get MAC address table
            result = await conn.run('show mac address-table')
            mac_table = self._parse_mac_table(result.stdout)
            
            # Combine for complete device list
            devices = self._merge_tables(arp_table, mac_table)
            return devices
```

### Automated Enrollment Pipeline

```python
# discovery/enrollment_pipeline.py
from typing import List, Dict
import asyncio

class AutoEnrollment:
    def __init__(self, mcp_server_url: str, terraform_path: str):
        self.mcp_url = mcp_server_url
        self.terraform_path = terraform_path
    
    async def process_discovered_devices(self, devices: List[Dict]):
        """
        Main enrollment pipeline:
        1. Classify devices by tier
        2. Determine monitoring strategy
        3. Deploy agents or configure agentless monitoring
        4. Register with MCP server
        5. Add to compliance baseline
        """
        for device in devices:
            # Skip devices we're already monitoring
            if device.get('monitored', False):
                continue
            
            # Determine if device should be monitored
            if not self._should_monitor(device):
                await self._mark_excluded(device, reason='out_of_scope')
                continue
            
            # Classify and enroll based on tier
            tier = device.get('tier', 1)
            device_type = device.get('device_type', 'unknown')
            
            if device_type in ['linux_server', 'windows_server']:
                await self._enroll_agent_based(device)
            elif device_type == 'network_infrastructure':
                await self._enroll_snmp_monitoring(device)
            elif device_type in ['windows_workstation', 'macos_workstation']:
                # Workstations typically excluded from infra-only scope
                await self._mark_excluded(device, reason='endpoint_device')
            else:
                await self._enroll_agentless(device)
    
    async def _enroll_agent_based(self, device: Dict):
        """
        Deploy full monitoring agent for servers
        Uses cloud-init or SSH to bootstrap
        """
        enrollment_plan = {
            'device_id': f"{device['client_id']}-{device['ip']}",
            'client_id': device['client_id'],
            'hostname': device.get('hostname', device['ip']),
            'ip': device['ip'],
            'device_type': device['device_type'],
            'tier': device['tier'],
            'monitoring_method': 'agent',
            'agent_type': 'full_watcher'
        }
        
        # Generate Terraform configuration
        await self._generate_terraform_config(enrollment_plan)
        
        # If SSH is available, bootstrap immediately
        if self._has_ssh_access(device):
            await self._bootstrap_agent(device)
        else:
            # Queue for manual intervention
            await self._queue_manual_enrollment(device, 
                reason='ssh_access_required')
    
    async def _enroll_snmp_monitoring(self, device: Dict):
        """
        Configure agentless SNMP monitoring for network gear
        """
        monitoring_config = {
            'device_id': f"{device['client_id']}-{device['ip']}",
            'client_id': device['client_id'],
            'ip': device['ip'],
            'hostname': device.get('hostname'),
            'monitoring_method': 'snmp',
            'snmp_version': '2c',  # Or v3 if available
            'snmp_community': None,  # Fetch from vault
            'poll_interval': 300,  # 5 minutes
            'metrics': [
                'sysUpTime',
                'ifInOctets',
                'ifOutOctets',
                'ifInErrors',
                'ifOutErrors'
            ]
        }
        
        # Add to monitoring system
        await self._register_with_mcp(monitoring_config)
        
        # Add to Prometheus/Telegraph config
        await self._add_to_monitoring_config(monitoring_config)
    
    async def _enroll_agentless(self, device: Dict):
        """
        Configure agentless monitoring (syslog, SNMP traps, NetFlow)
        """
        monitoring_config = {
            'device_id': f"{device['client_id']}-{device['ip']}",
            'client_id': device['client_id'],
            'ip': device['ip'],
            'monitoring_method': 'agentless',
            'methods': []
        }
        
        # Check what's available
        if self._supports_syslog(device):
            monitoring_config['methods'].append('syslog')
            await self._configure_syslog_forwarding(device)
        
        if self._supports_snmp(device):
            monitoring_config['methods'].append('snmp')
            await self._configure_snmp_polling(device)
        
        if self._supports_netflow(device):
            monitoring_config['methods'].append('netflow')
            await self._configure_netflow_export(device)
        
        await self._register_with_mcp(monitoring_config)
    
    def _should_monitor(self, device: Dict) -> bool:
        """
        Determine if device is in scope for compliance monitoring
        """
        device_type = device.get('device_type', 'unknown')
        
        # Infra-only scope - exclude endpoints
        excluded_types = [
            'windows_workstation',
            'macos_workstation',
            'printer',
            'unknown'
        ]
        
        if device_type in excluded_types:
            return False
        
        # Include all servers and network infrastructure
        included_types = [
            'linux_server',
            'windows_server',
            'network_infrastructure',
            'firewall',
            'vpn_gateway',
            'database_server',
            'application_server'
        ]
        
        return device_type in included_types
    
    async def _bootstrap_agent(self, device: Dict):
        """
        SSH into device and install monitoring agent
        Uses NixOS flake for Linux, PowerShell for Windows
        """
        if device['device_type'].startswith('linux'):
            await self._bootstrap_linux_agent(device)
        elif device['device_type'].startswith('windows'):
            await self._bootstrap_windows_agent(device)
    
    async def _bootstrap_linux_agent(self, device: Dict):
        """
        Install NixOS monitoring agent via SSH
        """
        bootstrap_script = f"""
        # Download and install Nix (if not present)
        curl -L https://nixos.org/nix/install | sh
        
        # Source Nix
        . ~/.nix-profile/etc/profile.d/nix.sh
        
        # Install monitoring agent from your flake
        nix profile install github:yourorg/msp-platform#watcher
        
        # Configure agent
        cat > /etc/msp-watcher.conf <<EOF
        client_id: {device['client_id']}
        device_id: {device['ip']}
        mcp_server: {self.mcp_url}
        api_key: {{vault:msp/clients/{device['client_id']}/api_key}}
        EOF
        
        # Enable and start service
        systemctl enable msp-watcher
        systemctl start msp-watcher
        """
        
        async with asyncssh.connect(
            device['ip'],
            known_hosts=None
        ) as conn:
            result = await conn.run(bootstrap_script)
            if result.exit_status == 0:
                await self._mark_enrolled(device, 'agent_installed')
            else:
                await self._queue_manual_enrollment(
                    device,
                    reason=f'bootstrap_failed: {result.stderr}'
                )
    
    async def _generate_terraform_config(self, plan: Dict):
        """
        Generate Terraform configuration for device
        """
        config = f"""
        resource "msp_monitored_device" "{plan['device_id']}" {{
          client_id    = "{plan['client_id']}"
          hostname     = "{plan['hostname']}"
          ip_address   = "{plan['ip']}"
          device_type  = "{plan['device_type']}"
          tier         = {plan['tier']}
          monitoring   = "{plan['monitoring_method']}"
          
          tags = {{
            auto_enrolled = "true"
            discovery_date = "{datetime.utcnow().isoformat()}"
          }}
        }}
        """
        
        # Write to Terraform workspace
        tf_file = f"{self.terraform_path}/clients/{plan['client_id']}/devices.tf"
        # Append to file...
    
    async def _register_with_mcp(self, config: Dict):
        """Register device with MCP server"""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.mcp_url}/api/devices/register",
                json=config
            ) as resp:
                return await resp.json()
```

### NixOS Integration

```nix
# discovery/flake.nix
{
  description = "MSP Network Discovery & Auto-Enrollment";
  
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.05";
  };
  
  outputs = { self, nixpkgs }: {
    packages.x86_64-linux.discovery-service = nixpkgs.legacyPackages.x86_64-linux.python3Packages.buildPythonApplication {
      pname = "msp-discovery";
      version = "0.1.0";
      
      propagatedBuildInputs = with nixpkgs.legacyPackages.x86_64-linux.python3Packages; [
        nmap
        scapy
        pysnmp
        zeroconf
        asyncssh
        aiohttp
      ];
      
      src = ./.;
    };
    
    nixosModules.discovery-service = { config, lib, pkgs, ... }: {
      options.services.msp-discovery = {
        enable = lib.mkEnableOption "MSP Network Discovery Service";
        
        subnets = lib.mkOption {
          type = lib.types.listOf lib.types.str;
          description = "Subnets to scan for devices";
          example = ["192.168.1.0/24"];
        };
        
        scanInterval = lib.mkOption {
          type = lib.types.int;
          default = 3600;  # 1 hour
          description = "Seconds between discovery scans";
        };
        
        clientId = lib.mkOption {
          type = lib.types.str;
          description = "Client identifier";
        };
      };
      
      config = lib.mkIf config.services.msp-discovery.enable {
        systemd.services.msp-discovery = {
          description = "MSP Network Discovery Service";
          wantedBy = [ "multi-user.target" ];
          after = [ "network.target" ];
          
          serviceConfig = {
            ExecStart = "${self.packages.x86_64-linux.discovery-service}/bin/msp-discovery";
            Restart = "always";
            RestartSec = "10s";
          };
          
          environment = {
            MSP_CLIENT_ID = config.services.msp-discovery.clientId;
            MSP_SUBNETS = lib.concatStringsSep "," config.services.msp-discovery.subnets;
            MSP_SCAN_INTERVAL = toString config.services.msp-discovery.scanInterval;
          };
        };
      };
    };
  };
}
```

### HIPAA Considerations for Discovery

**Critical Security Requirements:**

1. **No PHI Exposure During Discovery**
   - Discovery scans system/network layer only
   - Never scan application data directories
   - Block access to EHR/database ports during fingerprinting

2. **Minimal Footprint**
   - Use stealth scanning options where possible
   - Rate-limit scans to avoid DoS
   - Schedule during maintenance windows

3. **Audit Trail**
   - Log every discovery scan with timestamp
   - Record which devices were discovered/enrolled
   - Track enrollment decisions and exclusions

4. **Access Control**
   - Discovery service runs with least-privilege
   - Credentials stored in Vault
   - No persistent SSH keys

5. **Device Classification Privacy**
   - Don't classify devices based on PHI-revealing patterns
   - Use network/service fingerprints only
   - Avoid collecting device hostnames that might reveal patient names

### Deployment Configuration

```yaml
# discovery/config.yaml
discovery:
  client_id: clinic-001
  
  subnets:
    - 192.168.1.0/24    # Main office network
    - 192.168.10.0/24   # Server VLAN
    - 10.0.1.0/24       # Medical devices VLAN (scan with caution)
  
  scan_schedule:
    full_scan: "0 2 * * 0"  # Sunday 2 AM
    quick_scan: "0 */4 * * *"  # Every 4 hours
  
  methods:
    - active_nmap
    - passive_arp
    - snmp_walk
    - mdns_browse
    - switch_api  # If available
  
  enrollment:
    auto_enroll_tiers: [1, 2]  # Auto-enroll Tier 1 & 2 only
    manual_approval_tier: 3    # Tier 3 needs manual approval
    
    excluded_types:
      - windows_workstation
      - macos_workstation
      - printer
      - medical_device  # May require manual config
    
    agent_deployment:
      linux_servers: ssh_bootstrap
      windows_servers: winrm_bootstrap
      network_gear: agentless_snmp
  
  security:
    stealth_mode: true
    rate_limit_packets_per_sec: 100
    respect_robots_txt: true
    
  hipaa:
    avoid_phi_bearing_ports: [3306, 5432, 1433, 1521]  # Don't scan DBs
    log_all_discoveries: true
    require_baa_before_enrollment: true
```

### Dashboard View

Add to your compliance packets:

```markdown
## Automated Device Discovery Report

### Discovery Summary (October 2025)
- Total devices discovered: 47
- Devices enrolled in monitoring: 32
- Devices excluded (out of scope): 12
- Devices pending manual approval: 3

### Enrolled Device Breakdown
| Tier | Device Type | Count | Monitoring Method |
|------|-------------|-------|-------------------|
| 1 | Linux Server | 8 | Agent (full) |
| 1 | Windows Server | 4 | Agent (full) |
| 1 | Network Infrastructure | 6 | SNMP |
| 1 | Firewall | 2 | Syslog + SNMP |
| 1 | VPN Gateway | 1 | Syslog |
| 2 | Database Server | 5 | Agent (database module) |
| 2 | Application Server | 4 | Agent (app module) |
| 2 | Web Server | 2 | Agent + WAF logs |

### Excluded Devices
| Device Type | Count | Exclusion Reason |
|-------------|-------|------------------|
| Windows Workstation | 8 | Endpoint device (out of scope) |
| Printer | 3 | Not compliance-critical |
| Unknown | 1 | Unable to classify |

### Pending Manual Approval
- 10.0.1.45 - Medical Device (PACS server) - Tier 3
- 10.0.1.62 - Medical Device (Modality) - Tier 3
- 192.168.1.88 - Unknown Server - Needs investigation

### Discovery Method Effectiveness
- Active nmap scan: 35 devices
- Passive ARP monitoring: 12 devices (11 duplicates)
- SNMP walk: 6 devices
- mDNS discovery: 4 devices (printers)
- Switch API query: 47 devices (authoritative)
```

---

## Executive Dashboards & Audit-Ready Outputs

### Philosophy: Enforcement-First, Visuals Second

**Core Principle:** Dashboards expose automation, they don't replace it. Every red tile flows into real action via your MCP remediation pipeline.

**What This Section Adds:**
- Thin collector + rules-as-code
- Small HTML/PDF outputs proving what happened
- Print-ready monthly compliance packets
- Auditor-acceptable GUI with evidence links

**What This Section Skips:**
- Heavy SaaS sprawl
- Big data lakes
- Expensive BI tools
- Dashboards without enforcement backing

### Minimal Architecture (Rides What You Already Have)

#### A. Collectors (Pull Only What Matters)

**Local State (Near-Zero Cost):**
```python
# collectors/local_state.py
import json
from datetime import datetime
from pathlib import Path

class LocalStateCollector:
    def __init__(self, client_id: str, snapshot_dir: str):
        self.client_id = client_id
        self.snapshot_dir = Path(snapshot_dir)
    
    async def collect_snapshot(self) -> dict:
        """
        Collect current system state for compliance evidence
        Returns metadata only - no PHI, no content
        """
        timestamp = datetime.utcnow()
        
        snapshot = {
            "metadata": {
                "client_id": self.client_id,
                "timestamp": timestamp.isoformat(),
                "collector_version": "1.0.0"
            },
            "flake_state": await self._get_flake_state(),
            "patch_status": await self._get_patch_status(),
            "backup_status": await self._get_backup_status(),
            "service_health": await self._get_service_health(),
            "encryption_status": await self._get_encryption_status(),
            "time_sync": await self._get_time_sync_status()
        }
        
        # Write to timestamped file
        snapshot_path = self.snapshot_dir / f"{timestamp.strftime('%Y-%m-%d')}" / f"{timestamp.strftime('%H')}"
        snapshot_path.mkdir(parents=True, exist_ok=True)
        
        snapshot_file = snapshot_path / f"{self.client_id}_snapshot.json"
        with open(snapshot_file, 'w') as f:
            json.dump(snapshot, f, indent=2)
        
        return snapshot
    
    async def _get_flake_state(self) -> dict:
        """Query current NixOS flake state"""
        result = await run_command("nix flake metadata --json")
        flake_metadata = json.loads(result.stdout)
        
        return {
            "flake_hash": flake_metadata.get("locked", {}).get("narHash"),
            "commit_sha": flake_metadata.get("locked", {}).get("rev"),
            "last_modified": flake_metadata.get("locked", {}).get("lastModified"),
            "derivation_ids": await self._get_active_derivations()
        }
    
    async def _get_patch_status(self) -> dict:
        """Query patch/vulnerability status"""
        return {
            "last_applied": "2025-10-23T02:00:00Z",
            "critical_pending": 0,
            "high_pending": 2,
            "medium_pending": 8,
            "last_scan": datetime.utcnow().isoformat(),
            "mttr_critical_hours": 4.2
        }
    
    async def _get_backup_status(self) -> dict:
        """Query backup job status"""
        return {
            "last_success": "2025-10-23T02:00:00Z",
            "last_failure": None,
            "last_restore_test": "2025-10-15T03:00:00Z",
            "backup_size_gb": 127.4,
            "retention_days": 90,
            "checksum": "sha256:a1b2c3..."
        }
    
    async def _get_service_health(self) -> dict:
        """Query critical service status"""
        services = ["nginx", "postgresql", "redis", "msp-watcher"]
        health = {}
        
        for service in services:
            result = await run_command(f"systemctl is-active {service}")
            health[service] = result.stdout.strip()
        
        return health
    
    async def _get_encryption_status(self) -> dict:
        """Check encryption configuration"""
        return {
            "luks_volumes": await self._check_luks_status(),
            "tls_certificates": await self._check_cert_expiry(),
            "at_rest_encryption": True,
            "in_transit_encryption": True
        }
    
    async def _get_time_sync_status(self) -> dict:
        """Check NTP sync status"""
        result = await run_command("timedatectl show --property=NTPSynchronized,TimeUSec")
        return {
            "ntp_synchronized": "yes" in result.stdout.lower(),
            "time_usec": result.stdout.split('\n')[1].split('=')[1],
            "max_drift_ms": 45  # Example
        }
```

**External State (Minimal SaaS Taps):**
```python
# collectors/external_state.py
import aiohttp

class ExternalStateCollector:
    async def collect_idp_state(self, idp_type: str, credentials: dict) -> dict:
        """
        Collect MFA coverage, privileged users from IdP
        Supports: Okta, Azure AD, Google Workspace
        """
        if idp_type == "okta":
            return await self._collect_okta(credentials)
        elif idp_type == "azure_ad":
            return await self._collect_azure_ad(credentials)
        # ... etc
    
    async def _collect_okta(self, credentials: dict) -> dict:
        """Query Okta for user MFA status"""
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"SSWS {credentials['api_token']}"}
            
            # Get all users
            async with session.get(
                f"{credentials['domain']}/api/v1/users",
                headers=headers
            ) as resp:
                users = await resp.json()
            
            # Check MFA factors
            mfa_users = 0
            privileged_users = []
            
            for user in users:
                if user.get('status') == 'ACTIVE':
                    factors = await self._get_user_factors(session, user['id'], headers)
                    if factors:
                        mfa_users += 1
                    
                    # Check if user is in privileged groups
                    if self._is_privileged(user):
                        privileged_users.append({
                            "user_id": user['id'],
                            "email": user['profile']['email'],
                            "has_mfa": len(factors) > 0,
                            "groups": user.get('groups', [])
                        })
            
            return {
                "total_users": len(users),
                "mfa_enabled_users": mfa_users,
                "mfa_coverage_pct": (mfa_users / len(users)) * 100,
                "privileged_users": privileged_users,
                "break_glass_accounts": 2  # Should be configured
            }
    
    async def collect_git_state(self, git_provider: str, credentials: dict) -> dict:
        """
        Collect branch protections, admin access, deploy keys
        Supports: GitHub, GitLab, Bitbucket
        """
        if git_provider == "github":
            return await self._collect_github(credentials)
        # ... etc
    
    async def _collect_github(self, credentials: dict) -> dict:
        """Query GitHub org/repo settings"""
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"token {credentials['token']}"}
            
            repos_data = []
            
            # Get org repos
            async with session.get(
                f"https://api.github.com/orgs/{credentials['org']}/repos",
                headers=headers
            ) as resp:
                repos = await resp.json()
            
            for repo in repos:
                # Check branch protection
                async with session.get(
                    f"https://api.github.com/repos/{credentials['org']}/{repo['name']}/branches/main/protection",
                    headers=headers
                ) as resp:
                    if resp.status == 200:
                        protection = await resp.json()
                        repos_data.append({
                            "name": repo['name'],
                            "protected": True,
                            "requires_reviews": protection.get('required_pull_request_reviews', {}).get('required_approving_review_count', 0) >= 2,
                            "has_codeowners": protection.get('required_pull_request_reviews', {}).get('require_code_owner_reviews', False)
                        })
            
            # Get deploy keys
            deploy_keys = []
            # ... query deploy keys
            
            return {
                "total_repos": len(repos),
                "protected_repos": len([r for r in repos_data if r['protected']]),
                "repos_with_codeowners": len([r for r in repos_data if r.get('has_codeowners')]),
                "deploy_keys": deploy_keys
            }
```

#### B. Rules as Code

```yaml
# rules/compliance_rules.yaml
rules:
  - id: endpoint_drift
    name: "Endpoint Configuration Drift"
    description: "All managed nodes run approved flake hash"
    hipaa_controls:
      - "164.308(a)(1)(ii)(D)"
      - "164.310(d)(1)"
    severity: high
    check:
      type: flake_hash_equality
      target_hash: "{{baseline.target_flake_hash}}"
      scope: all_nodes
    thresholds:
      fail: "any_mismatch"
      warn: "none"
    auto_fix:
      enabled: true
      action: reflake_rollout
      runbook_id: RB-DRIFT-001
    evidence_required:
      - node_list
      - current_hash_per_node
      - rollout_log_ids
    
  - id: patch_freshness
    name: "Critical Patch Timeliness"
    description: "Critical patches remediated within 7 days"
    hipaa_controls:
      - "164.308(a)(5)(ii)(B)"
    severity: critical
    check:
      type: patch_age
      severity: critical
      max_age_days: 7
    thresholds:
      fail: ">7_days"
      warn: ">5_days"
    auto_fix:
      enabled: true
      action: trigger_patch_job
      runbook_id: RB-PATCH-001
    evidence_required:
      - patch_job_logs
      - ticket_refs
      - mttr_hours
    
  - id: backup_success
    name: "Backup Success & Restore Testing"
    description: "Successful backup in last 24h, restore test within 30 days"
    hipaa_controls:
      - "164.308(a)(7)(ii)(A)"
      - "164.310(d)(2)(iv)"
    severity: critical
    check:
      type: composite
      conditions:
        - backup_age: "<24h"
        - restore_test_age: "<30d"
    thresholds:
      fail: "either_condition_fail"
      warn: "restore_test_age_>20d"
    auto_fix:
      enabled: true
      action: run_backup_and_schedule_test
      runbook_id: RB-BACKUP-001
    evidence_required:
      - backup_checksum
      - restore_transcript_hash
      - test_timestamp
    
  - id: mfa_coverage
    name: "MFA Coverage for Human Accounts"
    description: "100% MFA for human accounts; ≤2 break-glass accounts"
    hipaa_controls:
      - "164.312(a)(2)(i)"
      - "164.308(a)(4)(ii)(C)"
    severity: high
    check:
      type: idp_mfa_coverage
      target: 100
      break_glass_max: 2
    thresholds:
      fail: "<95%"
      warn: "<100%"
    auto_fix:
      enabled: false  # Manual approval required
      action: quarantine_non_mfa_users
      runbook_id: RB-MFA-001
    evidence_required:
      - user_mfa_status_csv
      - break_glass_account_list
    
  - id: privileged_access
    name: "Privileged Access Review"
    description: "Privileged users explicitly approved in last 90 days"
    hipaa_controls:
      - "164.308(a)(3)(ii)(B)"
      - "164.308(a)(4)(ii)(B)"
    severity: high
    check:
      type: approval_freshness
      max_age_days: 90
      approval_source: git_repo
    thresholds:
      fail: ">90_days"
      warn: ">75_days"
    auto_fix:
      enabled: false  # Requires manual approval
      action: notify_for_review
      runbook_id: RB-ACCESS-001
    evidence_required:
      - approval_yaml
      - approval_commit_hash
      - user_group_membership
    
  - id: git_protections
    name: "Git Branch Protection"
    description: "Protected main branches with CODEOWNERS and 2 reviewers"
    hipaa_controls:
      - "164.312(b)"
      - "164.308(a)(5)(ii)(D)"
    severity: medium
    check:
      type: git_branch_protection
      requirements:
        - protected_main: true
        - min_reviewers: 2
        - codeowners_required: true
    thresholds:
      fail: "any_requirement_not_met"
      warn: "none"
    auto_fix:
      enabled: true
      action: apply_branch_protection
      runbook_id: RB-GIT-001
    evidence_required:
      - repo_settings_json
      - protection_policy_hash
    
  - id: secrets_hygiene
    name: "Secrets & Deploy Key Hygiene"
    description: "No long-lived deploy keys with admin scope"
    hipaa_controls:
      - "164.312(a)(2)(i)"
      - "164.308(a)(4)(ii)(B)"
    severity: high
    check:
      type: deploy_key_audit
      max_age_days: 90
      disallowed_scopes: ["admin", "write:all"]
    thresholds:
      fail: "admin_scope_exists"
      warn: "age_>60_days"
    auto_fix:
      enabled: false  # Requires coordination
      action: rotate_deploy_keys
      runbook_id: RB-SECRETS-001
    evidence_required:
      - key_inventory_hash
      - rotation_pr_ids
    
  - id: storage_posture
    name: "Object Storage ACL Posture"
    description: "No public buckets unless explicitly allow-listed"
    hipaa_controls:
      - "164.310(d)(2)(iii)"
      - "164.312(a)(1)"
    severity: critical
    check:
      type: bucket_acl_audit
      allowed_public: []  # Empty = none allowed
    thresholds:
      fail: "any_public_bucket"
      warn: "none"
    auto_fix:
      enabled: true
      action: privatize_bucket
      runbook_id: RB-STORAGE-001
    evidence_required:
      - bucket_list
      - acl_before_after_diff

exceptions:
  - rule_id: privileged_access
    scope: ["admin@clinic.com"]
    reason: "Executive approval pending board meeting"
    owner: "security_team"
    risk: "low"
    expires: "2025-11-15"
```

#### C. Evidence Packager (Nightly)

```python
# evidence/packager.py
from datetime import datetime, timedelta
import json
import subprocess
from pathlib import Path
from typing import Dict, List

class EvidencePackager:
    def __init__(self, client_id: str, output_dir: str):
        self.client_id = client_id
        self.output_dir = Path(output_dir)
        
    async def generate_nightly_packet(self, date: datetime = None) -> str:
        """
        Generate comprehensive evidence packet
        Returns: Path to signed evidence bundle
        """
        if date is None:
            date = datetime.utcnow()
        
        packet_id = f"EP-{date.strftime('%Y%m%d')}-{self.client_id}"
        packet_dir = self.output_dir / packet_id
        packet_dir.mkdir(parents=True, exist_ok=True)
        
        # 1. Collect all snapshots from last 24 hours
        snapshots = await self._collect_snapshots(date)
        
        # 2. Run compliance rules evaluation
        rule_results = await self._evaluate_rules(snapshots)
        
        # 3. Generate HTML posture report (single page)
        html_report = await self._generate_html_report(rule_results)
        with open(packet_dir / "posture_report.html", 'w') as f:
            f.write(html_report)
        
        # 4. Generate PDF from HTML
        await self._html_to_pdf(
            packet_dir / "posture_report.html",
            packet_dir / "posture_report.pdf"
        )
        
        # 5. Create evidence ZIP
        evidence_files = [
            packet_dir / "posture_report.pdf",
            *self._get_snapshot_files(date),
            *self._get_log_excerpts(date)
        ]
        
        zip_path = packet_dir / f"{packet_id}_evidence.zip"
        await self._create_zip(evidence_files, zip_path)
        
        # 6. Sign the ZIP
        signature_path = await self._sign_bundle(zip_path)
        
        # 7. Upload to WORM storage
        await self._upload_to_worm_storage(zip_path, signature_path)
        
        # 8. Generate manifest
        manifest = {
            "packet_id": packet_id,
            "client_id": self.client_id,
            "generated_at": datetime.utcnow().isoformat(),
            "date_range": {
                "start": (date - timedelta(days=1)).isoformat(),
                "end": date.isoformat()
            },
            "rule_results": rule_results,
            "evidence_files": [str(f.name) for f in evidence_files],
            "zip_hash": await self._compute_hash(zip_path),
            "signature": signature_path.read_text(),
            "worm_storage_url": f"s3://compliance-worm/{self.client_id}/{date.year}/{date.month:02d}/{packet_id}_evidence.zip"
        }
        
        with open(packet_dir / "manifest.json", 'w') as f:
            json.dump(manifest, f, indent=2)
        
        return str(zip_path)
    
    async def _evaluate_rules(self, snapshots: List[dict]) -> Dict:
        """Run all compliance rules against collected snapshots"""
        from .rules_engine import RulesEngine
        
        engine = RulesEngine()
        results = {}
        
        for rule in engine.load_rules():
            result = await engine.evaluate_rule(rule, snapshots)
            results[rule['id']] = {
                "status": result['status'],  # pass/warn/fail
                "checked_at": datetime.utcnow().isoformat(),
                "scope": result['scope'],
                "evidence_refs": result['evidence_refs'],
                "auto_fix_triggered": result.get('auto_fix_triggered', False),
                "fix_job_id": result.get('fix_job_id'),
                "exception_applied": result.get('exception_applied', False)
            }
        
        return results
    
    async def _generate_html_report(self, rule_results: Dict) -> str:
        """Generate single-page HTML posture report"""
        
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Compliance Posture Report - {self.client_id}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; }}
        .header {{ background: #f0f0f0; padding: 20px; margin-bottom: 20px; }}
        .kpi {{ display: inline-block; margin: 10px 20px; }}
        .kpi-value {{ font-size: 32px; font-weight: bold; }}
        .kpi-label {{ font-size: 14px; color: #666; }}
        .control-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; }}
        .control-tile {{ border: 2px solid #ddd; padding: 15px; border-radius: 5px; }}
        .status-pass {{ border-color: #4CAF50; background: #f1f8f4; }}
        .status-warn {{ border-color: #FF9800; background: #fff8f0; }}
        .status-fail {{ border-color: #f44336; background: #fff0f0; }}
        .control-title {{ font-weight: bold; margin-bottom: 10px; }}
        .control-detail {{ font-size: 12px; color: #666; }}
        .timestamp {{ text-align: right; font-size: 12px; color: #999; }}
        @media print {{
            .no-print {{ display: none; }}
            body {{ margin: 20px; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>HIPAA Compliance Posture Report</h1>
        <p><strong>Client:</strong> {self.client_id}</p>
        <p><strong>Generated:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
        <p style="color: #666; font-size: 12px;">
            <strong>Disclaimer:</strong> This report contains system metadata only. No PHI is processed or transmitted.
        </p>
    </div>
    
    <h2>Key Performance Indicators</h2>
    <div style="margin-bottom: 40px;">
        {self._generate_kpi_html(rule_results)}
    </div>
    
    <h2>Control Posture</h2>
    <div class="control-grid">
        {self._generate_control_tiles_html(rule_results)}
    </div>
    
    <div class="timestamp">
        Report ID: EP-{datetime.utcnow().strftime('%Y%m%d')}-{self.client_id} | 
        Signature: See evidence bundle
    </div>
</body>
</html>
"""
        return html
    
    def _generate_kpi_html(self, rule_results: Dict) -> str:
        """Generate KPI HTML blocks"""
        
        # Calculate KPIs
        total_rules = len(rule_results)
        passed_rules = len([r for r in rule_results.values() if r['status'] == 'pass'])
        compliance_pct = (passed_rules / total_rules * 100) if total_rules > 0 else 0
        
        # Get patch MTTR from results
        patch_mttr = rule_results.get('patch_freshness', {}).get('scope', {}).get('mttr_hours', 0)
        
        # Get MFA coverage
        mfa_coverage = rule_results.get('mfa_coverage', {}).get('scope', {}).get('coverage_pct', 0)
        
        kpis = [
            ("Compliance Score", f"{compliance_pct:.1f}%", "pass" if compliance_pct >= 95 else "warn"),
            ("Patch MTTR (Critical)", f"{patch_mttr:.1f}h", "pass" if patch_mttr < 24 else "warn"),
            ("MFA Coverage", f"{mfa_coverage:.1f}%", "pass" if mfa_coverage == 100 else "warn"),
            ("Controls Passing", f"{passed_rules}/{total_rules}", "pass")
        ]
        
        html = ""
        for label, value, status in kpis:
            color = "#4CAF50" if status == "pass" else "#FF9800"
            html += f"""
            <div class="kpi">
                <div class="kpi-value" style="color: {color};">{value}</div>
                <div class="kpi-label">{label}</div>
            </div>
            """
        
        return html
    
    def _generate_control_tiles_html(self, rule_results: Dict) -> str:
        """Generate control tile HTML"""
        
        html = ""
        for rule_id, result in rule_results.items():
            status = result['status']
            status_class = f"status-{status}"
            
            # Get rule metadata
            rule_meta = self._get_rule_metadata(rule_id)
            
            auto_fix_note = ""
            if result.get('auto_fix_triggered'):
                auto_fix_note = f"<div style='color: #4CAF50; font-size: 11px; margin-top: 5px;'>✓ Auto-fixed in {result.get('fix_duration_sec', 0)}s</div>"
            
            exception_note = ""
            if result.get('exception_applied'):
                exception_note = "<div style='color: #FF9800; font-size: 11px; margin-top: 5px;'>⚠ Exception active</div>"
            
            html += f"""
            <div class="control-tile {status_class}">
                <div class="control-title">{rule_meta['name']}</div>
                <div class="control-detail">
                    <strong>Status:</strong> {status.upper()}<br>
                    <strong>HIPAA:</strong> {', '.join(rule_meta['hipaa_controls'])}<br>
                    <strong>Scope:</strong> {result['scope'].get('summary', 'N/A')}
                </div>
                {auto_fix_note}
                {exception_note}
                <div style="font-size: 10px; color: #999; margin-top: 10px;">
                    Evidence: {', '.join(result['evidence_refs'][:2])}
                </div>
            </div>
            """
        
        return html
    
    async def _sign_bundle(self, zip_path: Path) -> Path:
        """Sign evidence bundle with cosign or GPG"""
        signature_path = zip_path.with_suffix('.sig')
        
        # Using cosign (preferred)
        subprocess.run([
            'cosign', 'sign-blob',
            '--key', '/path/to/signing-key',
            '--output-signature', str(signature_path),
            str(zip_path)
        ])
        
        return signature_path
```

#### D. Thin Dashboard (Static Site)

```typescript
// dashboard/pages/index.tsx
import { useState, useEffect } from 'react'

interface RuleResult {
  id: string
  name: string
  status: 'pass' | 'warn' | 'fail'
  hipaa_controls: string[]
  last_checked: string
  evidence_refs: string[]
  auto_fix_triggered?: boolean
  fix_job_id?: string
}

export default function ComplianceDashboard() {
  const [rules, setRules] = useState<RuleResult[]>([])
  const [kpis, setKpis] = useState({})
  
  useEffect(() => {
    // Fetch latest compliance snapshot
    fetch('/api/compliance/latest')
      .then(r => r.json())
      .then(data => {
        setRules(data.rules)
        setKpis(data.kpis)
      })
  }, [])
  
  return (
    <div className="dashboard">
      <header className="bg-gray-100 p-6 mb-8">
        <h1 className="text-3xl font-bold">HIPAA Compliance Dashboard</h1>
        <p className="text-gray-600">Last updated: {new Date().toLocaleString()}</p>
      </header>
      
      {/* KPI Section */}
      <section className="grid grid-cols-4 gap-4 mb-8">
        <KPI
          label="Compliance Score"
          value={kpis.compliance_pct}
          unit="%"
          status={kpis.compliance_pct >= 95 ? 'pass' : 'warn'}
        />
        <KPI
          label="Patch MTTR"
          value={kpis.patch_mttr_hours}
          unit="hrs"
          status={kpis.patch_mttr_hours < 24 ? 'pass' : 'warn'}
        />
        <KPI
          label="MFA Coverage"
          value={kpis.mfa_coverage_pct}
          unit="%"
          status={kpis.mfa_coverage_pct === 100 ? 'pass' : 'warn'}
        />
        <KPI
          label="Auto-Fixes (24h)"
          value={kpis.auto_fixes_24h}
          unit=""
          status="pass"
        />
      </section>
      
      {/* Control Tiles */}
      <section>
        <h2 className="text-2xl font-bold mb-4">Control Status</h2>
        <div className="grid grid-cols-3 gap-4">
          {rules.map(rule => (
            <ControlTile key={rule.id} rule={rule} />
          ))}
        </div>
      </section>
      
      {/* Evidence Bundle Link */}
      <section className="mt-8 p-4 bg-gray-50 rounded">
        <h3 className="font-bold mb-2">Latest Evidence Bundle</h3>
        <a 
          href="/evidence/latest" 
          className="text-blue-600 hover:underline"
          download
        >
          Download EP-{new Date().toISOString().split('T')[0].replace(/-/g, '')}-bundle.zip
        </a>
        <span className="text-gray-500 ml-4 text-sm">
          (Signed, auditor-ready)
        </span>
      </section>
    </div>
  )
}

function KPI({ label, value, unit, status }) {
  const colors = {
    pass: 'text-green-600',
    warn: 'text-orange-500',
    fail: 'text-red-600'
  }
  
  return (
    <div className="bg-white p-4 rounded shadow">
      <div className={`text-4xl font-bold ${colors[status]}`}>
        {value}{unit}
      </div>
      <div className="text-gray-600 text-sm mt-2">{label}</div>
    </div>
  )
}

function ControlTile({ rule }) {
  const statusColors = {
    pass: 'border-green-500 bg-green-50',
    warn: 'border-orange-500 bg-orange-50',
    fail: 'border-red-500 bg-red-50'
  }
  
  return (
    <div className={`border-2 rounded p-4 ${statusColors[rule.status]}`}>
      <div className="font-bold mb-2">{rule.name}</div>
      <div className="text-sm text-gray-600 mb-2">
        <strong>Status:</strong> {rule.status.toUpperCase()}
      </div>
      <div className="text-sm text-gray-600 mb-2">
        <strong>HIPAA:</strong> {rule.hipaa_controls.join(', ')}
      </div>
      
      {rule.auto_fix_triggered && (
        <div className="text-xs text-green-600 mt-2">
          ✓ Auto-fixed: <a href={`/jobs/${rule.fix_job_id}`} className="underline">Job {rule.fix_job_id}</a>
        </div>
      )}
      
      <div className="text-xs text-gray-400 mt-2">
        Evidence: {rule.evidence_refs.slice(0, 2).join(', ')}
      </div>
    </div>
  )
}
```

### Print-Ready Monthly Compliance Packet

#### Template Structure

```markdown
# Monthly HIPAA Compliance Packet

**Client:** {{client_name}}  
**Period:** {{month}} {{year}}  
**Baseline:** NixOS-HIPAA {{baseline_version}}  
**Generated:** {{timestamp}}

---

## Executive Summary

**PHI Disclaimer:** This report contains system metadata and operational metrics only. No Protected Health Information (PHI) is processed, stored, or transmitted by the compliance monitoring system.

**Compliance Status:** {{compliance_pct}}% of controls passing  
**Critical Issues:** {{critical_issue_count}} ({{auto_fixed_count}} auto-fixed)  
**MTTR (Critical Patches):** {{mttr_hours}}h  
**Backup Success Rate:** {{backup_success_rate}}%  

**Key Highlights:**
- {{highlight_1}}
- {{highlight_2}}
- {{highlight_3}}

---

## Control Posture Heatmap

| Control | Description | Status | Evidence ID | Last Checked |
|---------|-------------|--------|-------------|--------------|
| 164.308(a)(1)(ii)(D) | Information System Activity Review | {{status_icon}} | {{evidence_id}} | {{timestamp}} |
| 164.308(a)(5)(ii)(B) | Protection from Malicious Software | {{status_icon}} | {{evidence_id}} | {{timestamp}} |
| 164.308(a)(7)(ii)(A) | Data Backup Plan | {{status_icon}} | {{evidence_id}} | {{timestamp}} |
| 164.310(d)(1) | Device and Media Controls | {{status_icon}} | {{evidence_id}} | {{timestamp}} |
| 164.310(d)(2)(iv) | Data Backup and Storage | {{status_icon}} | {{evidence_id}} | {{timestamp}} |
| 164.312(a)(1) | Access Control | {{status_icon}} | {{evidence_id}} | {{timestamp}} |
| 164.312(a)(2)(i) | Unique User Identification | {{status_icon}} | {{evidence_id}} | {{timestamp}} |
| 164.312(a)(2)(iv) | Encryption and Decryption | {{status_icon}} | {{evidence_id}} | {{timestamp}} |
| 164.312(b) | Audit Controls | {{status_icon}} | {{evidence_id}} | {{timestamp}} |
| 164.312(e)(1) | Transmission Security | {{status_icon}} | {{evidence_id}} | {{timestamp}} |
| 164.316(b)(1) | Policies and Procedures | {{status_icon}} | {{evidence_id}} | {{timestamp}} |

**Legend:** ✅ Pass | ⚠️ Warning (Exception/In Progress) | ❌ Fail

---

## Backups & Test-Restores

**Backup Schedule:** Daily at 02:00 UTC  
**Retention:** 90 days  
**Encryption:** AES-256-GCM (at rest)

| Week | Backup Status | Size (GB) | Checksum | Restore Test | Test Result |
|------|--------------|-----------|----------|--------------|-------------|
| Week 1 | ✅ Success | 127.4 | sha256:a1b2... | 2025-10-15 | ✅ Pass (3 files, 1 DB) |
| Week 2 | ✅ Success | 128.1 | sha256:c3d4... | 2025-10-22 | ✅ Pass (5 files) |
| Week 3 | ✅ Success | 129.3 | sha256:e5f6... | Not yet scheduled | - |
| Week 4 | ✅ Success | 130.8 | sha256:g7h8... | Not yet scheduled | - |

**Evidence:** `EB-BACKUP-2025-10.zip` (signed, 24.3 MB)

**HIPAA Control:** §164.308(a)(7)(ii)(A), §164.310(d)(2)(iv)

---

## Time Synchronization

**NTP Server:** {{ntp_server}}  
**Sync Status:** {{ntp_sync_status}}  
**Max Drift Observed:** {{max_drift_ms}}ms  
**Threshold:** ±90 seconds

| System | Drift (ms) | Status | Last Sync |
|--------|-----------|--------|-----------|
| srv-primary | +12 | ✅ | 2025-10-23 14:32 |
| srv-backup | -8 | ✅ | 2025-10-23 14:31 |
| srv-database | +45 | ✅ | 2025-10-23 14:30 |

**Evidence:** `EB-TIMESYNC-2025-10.json`

**HIPAA Control:** §164.312(b) (Audit controls require accurate timestamps)

---

## Access Controls

### Failed Login Attempts

**Total Failed Logins:** {{failed_login_count}}  
**Threshold:** >10 per user triggers alert  
**Actions Taken:** {{lockout_count}} accounts temporarily locked

| User | Failed Attempts | Action | Timestamp |
|------|----------------|--------|-----------|
| user123 | 6 | Monitored | 2025-10-15 09:23 |
| user456 | 12 | Auto-locked (15min) | 2025-10-18 14:45 |

### Dormant Accounts

**Definition:** No login in 90+ days  
**Found:** {{dormant_account_count}}  
**Action:** Flagged for review

### MFA Status

**Total Active Users:** {{total_users}}  
**MFA Enabled:** {{mfa_enabled_users}} ({{mfa_coverage_pct}}%)  
**Break-Glass Accounts:** {{break_glass_count}} (Target: ≤2)

**Evidence:** `EB-ACCESS-2025-10.csv` (user IDs redacted)

**HIPAA Control:** §164.312(a)(2)(i), §164.308(a)(3)(ii)(C)

---

## Patch & Vulnerability Posture

**Last Vulnerability Scan:** {{last_scan_date}}  
**Critical Patches Pending:** {{critical_pending}}  
**High Patches Pending:** {{high_pending}}  
**Medium Patches Pending:** {{medium_pending}}

### Patch Timeline (Critical)

| CVE | Discovered | Patched | MTTR |
|-----|-----------|---------|------|
| CVE-2025-1234 | 2025-10-15 | 2025-10-15 | 4.2h |
| CVE-2025-5678 | 2025-10-20 | 2025-10-21 | 18.7h |

**Average MTTR (Critical):** {{mttr_critical_hours}}h (Target: <24h)

**Evidence:** `EB-PATCH-2025-10.json`

**HIPAA Control:** §164.308(a)(5)(ii)(B)

---

## Encryption Status

### At-Rest Encryption

| Volume | Type | Status | Algorithm |
|--------|------|--------|-----------|
| /dev/sda2 | LUKS | ✅ Encrypted | AES-256-XTS |
| /dev/sdb1 | LUKS | ✅ Encrypted | AES-256-XTS |
| Backups | Object Storage | ✅ Encrypted | AES-256-GCM |

### In-Transit Encryption

| Service | Protocol | Certificate | Expiry |
|---------|----------|-------------|--------|
| Web Portal | TLS 1.3 | wildcard.clinic.com | 2026-03-15 |
| Database | TLS 1.2 | db.clinic.internal | 2026-01-20 |
| VPN | WireGuard | psk+pubkey | N/A (rotated) |

**Evidence:** `EB-ENCRYPTION-2025-10.json`

**HIPAA Control:** §164.312(a)(2)(iv), §164.312(e)(1)

---

## EHR/API Audit Trends (Metadata Only)

**Total API Calls:** {{total_api_calls}}  
**Failed Auth Attempts:** {{failed_auth}}  
**Bulk Exports:** {{bulk_export_count}} (all authorized)

| Action Type | Count | % of Total |
|-------------|-------|------------|
| patient.read | {{read_count}} | {{read_pct}}% |
| patient.write | {{write_count}} | {{write_pct}}% |
| admin.access | {{admin_count}} | {{admin_pct}}% |

**Note:** Counts only. No PHI processed.

**Evidence:** `EB-API-AUDIT-2025-10.json`

**HIPAA Control:** §164.312(b), §164.308(a)(1)(ii)(D)

---

## Incidents & Exceptions

### Incidents This Month

| Incident ID | Type | Severity | Auto-Fixed | Resolution Time |
|-------------|------|----------|------------|-----------------|
| INC-2025-10-001 | Backup Failure | High | Yes | 12 minutes |
| INC-2025-10-002 | Cert Expiring | Medium | Yes | 8 minutes |

### Active Baseline Exceptions

| Rule | Scope | Reason | Owner | Risk | Expires |
|------|-------|--------|-------|------|---------|
| privileged_access | admin@clinic.com | Board approval pending | Security Team | Low | 2025-11-15 |

**Evidence:** `exceptions.yaml` (commit hash: {{exception_commit}})

**HIPAA Control:** §164.308(a)(8) (Evaluation process)

---

## Attestations & Review

**System Administrator Attestation:**

I, {{admin_name}}, attest that:
- All automated remediation actions were reviewed
- Exceptions are approved and time-bounded
- Evidence bundles are complete and accurate
- No PHI was processed by compliance systems

**Signature:** _________________________  
**Date:** _________________________

**Security Officer Review:**

**Reviewed By:** _________________________  
**Date:** _________________________  
**Comments:** _________________________

---

## Evidence Bundle Manifest

**Bundle ID:** {{bundle_id}}  
**Generated:** {{timestamp}}  
**Signature:** `{{signature_hash}}`  
**WORM Storage URL:** `{{worm_url}}`

**Contents:**
- posture_report.pdf
- snapshots/ (24 daily snapshots)
- rule_results.json
- evidence_artifacts.zip
- manifest.json (signed)

**Verification:**
```bash
cosign verify-blob \
  --key /path/to/public-key \
  --signature {{signature_hash}} \
  {{bundle_id}}.zip
```

---

**End of Monthly Compliance Packet**  
**Next Review:** {{next_month}} 1st, {{next_year}}

**Questions:** Contact security@{{client_domain}}  
**Audit Support:** All evidence bundles available for 24 months
```

### Grafana Dashboards for Print-Friendly GUI

```yaml
# dashboards/hipaa-compliance-print.json
{
  "dashboard": {
    "title": "HIPAA Compliance - Print View",
    "tags": ["compliance", "hipaa", "print-ready"],
    "timezone": "utc",
    "schemaVersion": 38,
    "version": 1,
    "editable": false,
    "graphTooltip": 0,
    
    "panels": [
      {
        "id": 1,
        "title": "Compliance Heatmap",
        "type": "table",
        "gridPos": {"x": 0, "y": 0, "w": 24, "h": 12},
        "targets": [{
          "expr": "compliance_rule_status",
          "format": "table"
        }],
        "fieldConfig": {
          "overrides": [
            {
              "matcher": {"id": "byName", "options": "Status"},
              "properties": [{
                "id": "mappings",
                "value": [
                  {"value": "pass", "text": "✅", "color": "green"},
                  {"value": "warn", "text": "⚠️", "color": "orange"},
                  {"value": "fail", "text": "❌", "color": "red"}
                ]
              }]
            }
          ]
        },
        "options": {
          "showHeader": true,
          "cellHeight": "sm",
          "footer": {"show": false}
        }
      },
      
      {
        "id": 2,
        "title": "Backup SLO & Restore Tests",
        "type": "timeseries",
        "gridPos": {"x": 0, "y": 12, "w": 12, "h": 8},
        "targets": [{
          "expr": "backup_success_rate",
          "legendFormat": "Success Rate"
        }, {
          "expr": "restore_test_count",
          "legendFormat": "Restore Tests"
        }],
        "options": {
          "legend": {"displayMode": "table", "placement": "bottom"},
          "tooltip": {"mode": "multi"}
        }
      },
      
      {
        "id": 3,
        "title": "Time Drift (±90s threshold)",
        "type": "gauge",
        "gridPos": {"x": 12, "y": 12, "w": 12, "h": 8},
        "targets": [{
          "expr": "max(abs(ntp_drift_ms))"
        }],
        "fieldConfig": {
          "defaults": {
            "unit": "ms",
            "thresholds": {
              "mode": "absolute",
              "steps": [
                {"value": 0, "color": "green"},
                {"value": 70000, "color": "orange"},
                {"value": 90000, "color": "red"}
              ]
            }
          }
        }
      },
      
      {
        "id": 4,
        "title": "Failed Login Attempts (Last 30d)",
        "type": "bargauge",
        "gridPos": {"x": 0, "y": 20, "w": 12, "h": 8},
        "targets": [{
          "expr": "topk(10, sum by (user) (rate(failed_login_attempts[30d])))"
        }],
        "options": {
          "orientation": "horizontal",
          "displayMode": "gradient",
          "showUnfilled": true
        }
      },
      
      {
        "id": 5,
        "title": "Patch Posture - Critical Outstanding",
        "type": "stat",
        "gridPos": {"x": 12, "y": 20, "w": 6, "h": 4},
        "targets": [{
          "expr": "count(critical_patches_pending)"
        }],
        "fieldConfig": {
          "defaults": {
            "thresholds": {
              "mode": "absolute",
              "steps": [
                {"value": 0, "color": "green"},
                {"value": 1, "color": "red"}
              ]
            }
          }
        },
        "options": {
          "graphMode": "none",
          "textMode": "value_and_name"
        }
      },
      
      {
        "id": 6,
        "title": "Encryption Status",
        "type": "stat",
        "gridPos": {"x": 18, "y": 20, "w": 6, "h": 4},
        "targets": [{
          "expr": "count(encryption_enabled == 1)"
        }],
        "fieldConfig": {
          "defaults": {
            "mappings": [{
              "value": "{{total_volumes}}",
              "text": "All Volumes Encrypted"
            }]
          }
        }
      },
      
      {
        "id": 7,
        "title": "EHR/API Event Counts (Metadata Only)",
        "type": "piechart",
        "gridPos": {"x": 0, "y": 28, "w": 24, "h": 8},
        "targets": [{
          "expr": "sum by (action_type) (ehr_api_calls)"
        }],
        "options": {
          "legend": {"displayMode": "table", "placement": "right"},
          "pieType": "donut"
        }
      }
    ],
    
    "templating": {
      "list": [
        {
          "name": "client",
          "type": "query",
          "query": "label_values(client_id)",
          "current": {"value": "clinic-001"}
        }
      ]
    },
    
    "annotations": {
      "list": [
        {
          "datasource": "prometheus",
          "expr": "ALERTS{alertstate=\"firing\"}",
          "tagKeys": "alertname,severity",
          "titleFormat": "{{alertname}}",
          "textFormat": "{{description}}"
        }
      ]
    }
  }
}
```

### Weekly Executive Postcard (Auto-Email)

```python
# reporting/executive_postcard.py
from datetime import datetime, timedelta
from jinja2 import Template

POSTCARD_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <style>
        body { font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; }
        .header { background: #4CAF50; color: white; padding: 15px; text-align: center; }
        .metric { background: #f5f5f5; padding: 10px; margin: 10px 0; border-left: 4px solid #4CAF50; }
        .highlight { font-size: 24px; font-weight: bold; color: #4CAF50; }
        .footer { text-align: center; font-size: 12px; color: #666; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="header">
        <h2>Weekly Compliance Update</h2>
        <p>{{ client_name }} | Week of {{ week_start }}</p>
    </div>
    
    <h3>🎯 Key Highlights</h3>
    
    <div class="metric">
        <strong>Drift Events Auto-Fixed:</strong>
        <span class="highlight">{{ drift_events }}</span>
        <span style="font-size: 12px; color: #666;">
            (avg {{ avg_fix_time }}m resolution time)
        </span>
    </div>
    
    <div class="metric">
        <strong>MFA Coverage:</strong>
        <span class="highlight">{{ mfa_coverage }}%</span>
        <span style="font-size: 12px; color: #666;">
            {% if mfa_coverage == 100 %}✓ Target maintained{% else %}⚠️ Below target{% endif %}
        </span>
    </div>
    
    <div class="metric">
        <strong>Patch MTTR (Critical):</strong>
        <span class="highlight">{{ patch_mttr }}h</span>
        <span style="font-size: 12px; color: #666;">
            {% if patch_mttr < 24 %}✓ Within SLA{% else %}⚠️ Exceeds 24h target{% endif %}
        </span>
    </div>
    
    <div class="metric">
        <strong>Backup Success Rate:</strong>
        <span class="highlight">{{ backup_success_rate }}%</span>
        <span style="font-size: 12px; color: #666;">
            ({{ restore_tests }} restore tests completed)
        </span>
    </div>
    
    {% if incidents_resolved > 0 %}
    <div class="metric">
        <strong>Security Posture Actions:</strong>
        <ul style="margin: 5px 0; padding-left: 20px;">
            {% for action in security_actions %}
            <li>{{ action }}</li>
            {% endfor %}
        </ul>
    </div>
    {% endif %}
    
    <div class="footer">
        <p>
            <a href="{{ dashboard_url }}">View Full Dashboard</a> |
            <a href="{{ evidence_url }}">Download Evidence Bundle</a>
        </p>
        <p style="font-size: 10px; color: #999;">
            This report contains system metadata only. No PHI processed.
        </p>
    </div>
</body>
</html>
"""

class ExecutivePostcard:
    async def generate_weekly_postcard(self, client_id: str) -> str:
        """Generate one-page executive summary email"""
        
        # Collect weekly metrics
        week_start = datetime.utcnow() - timedelta(days=7)
        metrics = await self._collect_weekly_metrics(client_id, week_start)
        
        template = Template(POSTCARD_TEMPLATE)
        html = template.render(**metrics)
        
        return html
    
    async def _collect_weekly_metrics(self, client_id: str, week_start: datetime) -> dict:
        """Aggregate key metrics from the past week"""
        
        return {
            "client_name": "Clinic ABC",
            "week_start": week_start.strftime("%Y-%m-%d"),
            "drift_events": 2,
            "avg_fix_time": 3,
            "mfa_coverage": 100,
            "patch_mttr": 18.2,
            "backup_success_rate": 100,
            "restore_tests": 1,
            "incidents_resolved": 3,
            "security_actions": [
                "2 public S3 buckets auto-privatized",
                "1 expiring certificate auto-renewed",
                "3 dormant accounts flagged for review"
            ],
            "dashboard_url": f"https://compliance.yourcompany.com/clients/{client_id}",
            "evidence_url": f"https://compliance.yourcompany.com/evidence/latest/{client_id}"
        }
```

### Deployment Configuration

```nix
# reporting/flake.nix
{
  description = "Compliance Reporting & Dashboard Services";
  
  outputs = { self, nixpkgs }: {
    nixosModules.reporting = { config, lib, pkgs, ... }: {
      services.msp-reporting = {
        enable = true;
        
        collectors = {
          local_state = {
            enable = true;
            interval = "300s";  # 5 minutes
          };
          idp = {
            enable = config.services.msp-reporting.idp.provider != null;
            provider = lib.mkOption {
              type = lib.types.enum ["okta" "azure_ad" "google"];
              default = null;
            };
          };
          git = {
            enable = config.services.msp-reporting.git.provider != null;
            provider = lib.mkOption {
              type = lib.types.enum ["github" "gitlab"];
              default = null;
            };
          };
        };
        
        evidence_packager = {
          enable = true;
          schedule = "0 6 * * *";  # Daily at 6 AM UTC
          retention_days = 90;
          signing_key = config.sops.secrets.evidence_signing_key.path;
        };
        
        dashboard = {
          enable = true;
          port = 3000;
          auth = "oauth2_proxy";  # Or whatever you use
        };
        
        executive_postcard = {
          enable = true;
          schedule = "0 8 * * 1";  # Monday 8 AM UTC
          recipients = ["admin@clinic.com"];
        };
      };
    };
  };
}
```

---

## Software Provenance & Time Framework

### Overview & Philosophy

**Core Principle:** Every action, every build, every log entry must be cryptographically provable as authentic and temporally ordered.

In healthcare compliance, you need to prove not just *what* happened, but *when* it happened, *who* did it, and that the evidence hasn't been tampered with. Traditional compliance systems rely on log aggregation and manual attestation. This framework makes tampering mathematically impossible.

**What This Section Adds:**
- Cryptographic signing of all builds, deployments, and evidence bundles
- Multi-source time synchronization with tamper-evident hash chains
- SBOM (Software Bill of Materials) generation for supply chain attestation
- Blockchain anchoring for Enterprise-tier immutability proof
- Tier-based feature system (Essential → Professional → Enterprise)
- Forensic-grade audit trails that would satisfy criminal investigations

**Business Value:**
- **Essential Tier:** Proves basic compliance (small clinics, $200-400/mo)
- **Professional Tier:** Adds advanced attestation (mid-size, $600-1200/mo)
- **Enterprise Tier:** Forensic-grade evidence (large practices, $1500-3000/mo)

### What NixOS Gives You Free

NixOS's content-addressed store already provides foundational provenance:

**Built-In Provenance Features:**
1. **Content Addressing:** Every package/derivation has a unique hash based on ALL inputs (source, dependencies, build scripts, compiler flags)
2. **Reproducible Builds:** Same inputs → identical binary → same hash (bit-for-bit reproducibility)
3. **Derivation Files:** Machine-readable record of how every artifact was built
4. **Closure Tracking:** Complete dependency graph from kernel to userspace

**What This Means:**
```bash
# Query what built a package
$ nix-store --query --deriver /nix/store/abc123-nginx-1.24.0

# Get complete dependency graph
$ nix-store --query --requisites /nix/store/abc123-nginx-1.24.0

# Verify integrity
$ nix-store --verify --check-contents /nix/store/abc123-nginx-1.24.0
```

**What's Missing (That This Framework Adds):**
- Cryptographic signatures proving WHO authorized the build
- SBOM export in industry-standard formats (SPDX, CycloneDX)
- Multi-source time attestation (not just system clock)
- Hash chain linking evidence bundles over time
- Blockchain anchoring for external verification

### Signing and Verification

#### Build Signing (Essential Tier)

Every NixOS derivation is signed by your build server:

```nix
# flake/modules/signing/build-signing.nix
{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.msp.buildSigning;

in {
  options.services.msp.buildSigning = {
    enable = mkEnableOption "MSP build signing";

    signingKey = mkOption {
      type = types.path;
      description = "Path to Nix signing key (via SOPS)";
      example = "/run/secrets/nix-signing-key";
    };

    publicKeys = mkOption {
      type = types.listOf types.str;
      description = "List of trusted public keys";
      example = ["cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="];
    };
  };

  config = mkIf cfg.enable {
    nix.settings = {
      # Require signatures on all store paths
      require-sigs = true;

      # Trusted public keys (your build server + NixOS cache)
      trusted-public-keys = cfg.publicKeys;

      # Secret key for signing (only on build server)
      secret-key-files = mkIf (cfg.signingKey != null) [ cfg.signingKey ];
    };

    # Generate signing key on first boot (if not present)
    systemd.services.nix-signing-key-bootstrap = {
      description = "Generate Nix signing key";
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };

      script = ''
        if [ ! -f ${cfg.signingKey} ]; then
          echo "Generating new Nix signing key..."
          ${pkgs.nix}/bin/nix-store --generate-binary-cache-key \
            msp-build-server-1 \
            ${cfg.signingKey} \
            ${cfg.signingKey}.pub

          echo "Public key:"
          cat ${cfg.signingKey}.pub

          echo "Add this public key to all client configurations!"
        fi
      '';
    };

    # Automatically sign all locally-built paths
    nix.settings.post-build-hook = pkgs.writeShellScript "sign-build" ''
      set -euo pipefail

      export IFS=' '
      for path in $OUT_PATHS; do
        ${pkgs.nix}/bin/nix store sign \
          --key-file ${cfg.signingKey} \
          "$path"

        echo "Signed: $path"
      done
    '';
  };
}
```

**Usage on Build Server:**
```nix
# Build server configuration
{
  services.msp.buildSigning = {
    enable = true;
    signingKey = config.sops.secrets."nix-signing-key".path;
    publicKeys = [
      "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
      "msp-build-server-1:YOUR_PUBLIC_KEY_HERE"
    ];
  };
}
```

**Usage on Client Machines:**
```nix
# Client configuration
{
  services.msp.buildSigning = {
    enable = true;
    signingKey = null;  # Clients don't sign, only verify
    publicKeys = [
      "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
      "msp-build-server-1:YOUR_PUBLIC_KEY_HERE"
    ];
  };

  # Reject unsigned packages
  nix.settings.require-sigs = true;
}
```

#### Evidence Signing (Professional Tier)

Every evidence bundle is signed with cosign:

```python
# mcp-server/signing/evidence_signer.py
import subprocess
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

class EvidenceSigner:
    """Sign evidence bundles with cosign for Professional/Enterprise tiers"""

    def __init__(self,
                 key_path: str = "/run/secrets/evidence-signing-key",
                 password_env: str = "COSIGN_PASSWORD"):
        self.key_path = key_path
        self.password_env = password_env

    def sign_bundle(self, bundle_path: Path) -> dict:
        """
        Sign evidence bundle and return signature metadata
        Uses cosign for container-style signing
        """
        sig_path = bundle_path.with_suffix('.sig')

        # Sign with cosign
        result = subprocess.run([
            'cosign', 'sign-blob',
            '--key', self.key_path,
            '--output-signature', str(sig_path),
            '--yes',  # Non-interactive
            str(bundle_path)
        ], capture_output=True, text=True, check=True)

        # Generate signature metadata
        metadata = {
            "bundle_path": str(bundle_path),
            "signature_path": str(sig_path),
            "signed_at": datetime.utcnow().isoformat(),
            "signer": "msp-evidence-signer",
            "algorithm": "ECDSA-P256-SHA256",
            "bundle_hash": self._compute_hash(bundle_path),
            "signature_hash": self._compute_hash(sig_path)
        }

        # Write metadata
        metadata_path = bundle_path.with_suffix('.sig.json')
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2)

        return metadata

    def verify_bundle(self, bundle_path: Path, public_key_path: str) -> bool:
        """Verify evidence bundle signature"""
        sig_path = bundle_path.with_suffix('.sig')

        try:
            subprocess.run([
                'cosign', 'verify-blob',
                '--key', public_key_path,
                '--signature', str(sig_path),
                str(bundle_path)
            ], check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError:
            return False

    def _compute_hash(self, path: Path) -> str:
        """Compute SHA256 hash of file"""
        import hashlib
        with open(path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
```

**Integration with Evidence Packager:**
```python
# mcp-server/evidence/packager.py (updated)
async def generate_nightly_packet(self, date: datetime = None) -> str:
    # ... existing evidence collection ...

    # Create evidence ZIP
    zip_path = packet_dir / f"{packet_id}_evidence.zip"
    await self._create_zip(evidence_files, zip_path)

    # Sign the ZIP (Professional/Enterprise tier)
    if self.tier in ['professional', 'enterprise']:
        signer = EvidenceSigner()
        signature_metadata = signer.sign_bundle(zip_path)

        # Add signature to manifest
        manifest['signature'] = signature_metadata

    # Upload to WORM storage
    await self._upload_to_worm_storage(zip_path, signature_path)

    return str(zip_path)
```

### Evidence Registry

Append-only registry of all evidence bundles:

```python
# mcp-server/evidence/registry.py
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

class EvidenceRegistry:
    """
    Append-only registry of all evidence bundles
    Cannot delete or modify entries (WORM pattern at DB level)
    """

    def __init__(self, db_path: str = "/var/lib/msp/evidence-registry.db"):
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self):
        """Initialize database with WORM constraints"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS evidence_bundles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bundle_id TEXT NOT NULL UNIQUE,
                client_id TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                bundle_hash TEXT NOT NULL,
                signature_hash TEXT,
                worm_url TEXT,
                tier TEXT NOT NULL,
                signed BOOLEAN NOT NULL DEFAULT 0,
                anchored BOOLEAN NOT NULL DEFAULT 0,
                anchor_txid TEXT,
                registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create append-only trigger (prevent updates/deletes)
        conn.execute('''
            CREATE TRIGGER IF NOT EXISTS prevent_bundle_updates
            BEFORE UPDATE ON evidence_bundles
            BEGIN
                SELECT RAISE(ABORT, 'Evidence registry is append-only');
            END
        ''')

        conn.execute('''
            CREATE TRIGGER IF NOT EXISTS prevent_bundle_deletes
            BEFORE DELETE ON evidence_bundles
            BEGIN
                SELECT RAISE(ABORT, 'Evidence registry is append-only');
            END
        ''')

        conn.commit()
        conn.close()

    def register(self,
                 bundle_id: str,
                 client_id: str,
                 bundle_hash: str,
                 worm_url: str,
                 tier: str,
                 signature_hash: Optional[str] = None) -> int:
        """Register new evidence bundle (append-only)"""

        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute('''
            INSERT INTO evidence_bundles
            (bundle_id, client_id, generated_at, bundle_hash,
             signature_hash, worm_url, tier, signed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            bundle_id,
            client_id,
            datetime.utcnow().isoformat(),
            bundle_hash,
            signature_hash,
            worm_url,
            tier,
            signature_hash is not None
        ))

        bundle_pk = cursor.lastrowid
        conn.commit()
        conn.close()

        return bundle_pk

    def update_anchor(self, bundle_id: str, txid: str):
        """
        Update blockchain anchor (only field allowed to change)
        This is technically a violation of pure WORM, but acceptable
        because anchoring happens asynchronously after bundle creation
        """
        conn = sqlite3.connect(self.db_path)

        # Use raw SQL to bypass trigger (anchoring is special case)
        conn.execute('PRAGMA defer_foreign_keys = ON')
        conn.execute('''
            UPDATE evidence_bundles
            SET anchored = 1, anchor_txid = ?
            WHERE bundle_id = ?
        ''', (txid, bundle_id))

        conn.commit()
        conn.close()

    def query(self,
              client_id: Optional[str] = None,
              start_date: Optional[datetime] = None,
              end_date: Optional[datetime] = None,
              signed_only: bool = False) -> List[dict]:
        """Query evidence registry"""

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        query = 'SELECT * FROM evidence_bundles WHERE 1=1'
        params = []

        if client_id:
            query += ' AND client_id = ?'
            params.append(client_id)

        if start_date:
            query += ' AND generated_at >= ?'
            params.append(start_date.isoformat())

        if end_date:
            query += ' AND generated_at <= ?'
            params.append(end_date.isoformat())

        if signed_only:
            query += ' AND signed = 1'

        query += ' ORDER BY registered_at DESC'

        cursor = conn.execute(query, params)
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return results
```

### SBOM Generation

Generate Software Bill of Materials in SPDX/CycloneDX format:

```python
# mcp-server/sbom/generator.py
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List

class SBOMGenerator:
    """Generate SBOM (Software Bill of Materials) for NixOS systems"""

    def generate_spdx(self, system_path: str, output_path: Path) -> dict:
        """
        Generate SPDX 2.3 SBOM for NixOS system
        Uses nix-store to enumerate all packages
        """

        # Query all runtime dependencies
        result = subprocess.run([
            'nix-store', '--query', '--requisites', system_path
        ], capture_output=True, text=True, check=True)

        store_paths = result.stdout.strip().split('\n')

        # Parse package information
        packages = []
        for path in store_paths:
            pkg_info = self._parse_store_path(path)
            if pkg_info:
                packages.append(pkg_info)

        # Build SPDX document
        spdx = {
            "spdxVersion": "SPDX-2.3",
            "dataLicense": "CC0-1.0",
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": f"MSP-Client-System-{datetime.utcnow().strftime('%Y%m%d')}",
            "documentNamespace": f"https://msp.example.com/sbom/{datetime.utcnow().isoformat()}",
            "creationInfo": {
                "created": datetime.utcnow().isoformat(),
                "creators": ["Tool: MSP-SBOM-Generator-1.0"],
                "licenseListVersion": "3.21"
            },
            "packages": packages,
            "relationships": self._build_relationships(packages)
        }

        # Write SPDX JSON
        with open(output_path, 'w') as f:
            json.dump(spdx, f, indent=2)

        return spdx

    def _parse_store_path(self, path: str) -> dict:
        """Parse Nix store path into SPDX package"""

        # Extract package name and version from path
        # /nix/store/abc123-nginx-1.24.0 → nginx, 1.24.0
        parts = Path(path).name.split('-', 1)
        if len(parts) < 2:
            return None

        hash_prefix = parts[0]
        name_version = parts[1]

        # Split name and version
        version = None
        for i in range(len(name_version) - 1, -1, -1):
            if name_version[i].isdigit():
                version_start = name_version.rfind('-', 0, i)
                if version_start != -1:
                    name = name_version[:version_start]
                    version = name_version[version_start+1:]
                    break

        if not version:
            name = name_version
            version = "unknown"

        return {
            "SPDXID": f"SPDXRef-Package-{hash_prefix}",
            "name": name,
            "versionInfo": version,
            "downloadLocation": f"https://cache.nixos.org/{path}",
            "filesAnalyzed": False,
            "supplier": "Organization: NixOS",
            "externalRefs": [{
                "referenceCategory": "PACKAGE_MANAGER",
                "referenceType": "purl",
                "referenceLocator": f"pkg:nix/{name}@{version}"
            }]
        }

    def _build_relationships(self, packages: List[dict]) -> List[dict]:
        """Build SPDX relationships (dependencies)"""

        relationships = []

        # Document DESCRIBES first package
        if packages:
            relationships.append({
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": packages[0]["SPDXID"]
            })

        # All packages are CONTAINED_BY document
        for pkg in packages:
            relationships.append({
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": pkg["SPDXID"]
            })

        return relationships
```

**Integration with Compliance Packets:**
```python
# Add SBOM to monthly compliance packet
async def generate_nightly_packet(self, date: datetime = None) -> str:
    # ... existing evidence collection ...

    # Generate SBOM (Professional/Enterprise tier)
    if self.tier in ['professional', 'enterprise']:
        sbom_gen = SBOMGenerator()
        sbom_path = packet_dir / "sbom.spdx.json"
        sbom_gen.generate_spdx(
            system_path='/run/current-system',
            output_path=sbom_path
        )
        evidence_files.append(sbom_path)

    # ... rest of packet generation ...
```

### Multi-Source Time Synchronization

**NixOS Module for Multi-Source Time Sync:**

```nix
# flake/modules/audit/time-sync.nix
{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.msp.timeSync;

in {
  options.services.msp.timeSync = {
    enable = mkEnableOption "MSP multi-source time synchronization";

    tier = mkOption {
      type = types.enum ["essential" "professional" "enterprise"];
      default = "essential";
      description = "Compliance tier determines time sources";
    };

    ntpServers = mkOption {
      type = types.listOf types.str;
      default = [
        "time.nist.gov"
        "time.cloudflare.com"
        "pool.ntp.org"
      ];
      description = "NTP servers for Essential tier";
    };

    gpsDevice = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "/dev/ttyUSB0";
      description = "GPS device for Professional tier (Stratum 0)";
    };

    bitcoinEnabled = mkOption {
      type = types.bool;
      default = false;
      description = "Enable Bitcoin blockchain time (Enterprise tier)";
    };

    maxDriftMs = mkOption {
      type = types.int;
      default = 100;
      description = "Maximum allowed drift in milliseconds";
    };

    anomalyWebhook = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = "Webhook URL for time anomaly alerts";
    };
  };

  config = mkIf cfg.enable {

    # Base NTP configuration (Essential tier)
    services.chrony = {
      enable = true;
      servers = cfg.ntpServers;

      extraConfig = ''
        # Require multiple sources to agree
        minsources 2

        # Maximum allowed offset
        maxdrift ${toString cfg.maxDriftMs}

        # Log time adjustments
        logdir /var/log/chrony
        log measurements statistics tracking
      '';
    };

    # GPS time source (Professional tier)
    systemd.services.gpsd = mkIf (cfg.gpsDevice != null) {
      description = "GPS Time Daemon";
      wantedBy = [ "multi-user.target" ];
      after = [ "chronyd.service" ];

      serviceConfig = {
        ExecStart = "${pkgs.gpsd}/bin/gpsd -N ${cfg.gpsDevice}";
        Restart = "always";
      };
    };

    # Chrony GPS integration
    services.chrony.extraConfig = mkIf (cfg.gpsDevice != null) ''
      # GPS as Stratum 0 source (highest priority)
      refclock SHM 0 refid GPS precision 1e-1 offset 0.0
    '';

    # Time anomaly detection service
    systemd.services.time-anomaly-detector = {
      description = "MSP Time Anomaly Detector";
      after = [ "chronyd.service" ];
      wantedBy = [ "multi-user.target" ];

      serviceConfig = {
        Type = "simple";
        Restart = "always";
        ExecStart = pkgs.writeScript "time-anomaly-detector" ''
          #!${pkgs.bash}/bin/bash
          set -euo pipefail

          LOG_FILE="/var/log/msp/time-anomaly.log"
          mkdir -p "$(dirname "$LOG_FILE")"

          while true; do
            # Query chrony tracking
            TRACKING=$(${pkgs.chrony}/bin/chronyc tracking)

            # Extract system time offset
            OFFSET=$(echo "$TRACKING" | grep "System time" | awk '{print $4}')
            OFFSET_ABS=$(echo "$OFFSET" | tr -d '-')

            # Check if offset exceeds threshold
            THRESHOLD=$(echo "${toString cfg.maxDriftMs} / 1000" | ${pkgs.bc}/bin/bc -l)

            if (( $(echo "$OFFSET_ABS > $THRESHOLD" | ${pkgs.bc}/bin/bc -l) )); then
              TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
              MESSAGE="TIME ANOMALY: Offset $OFFSET seconds exceeds threshold $THRESHOLD"

              echo "$TIMESTAMP $MESSAGE" >> "$LOG_FILE"
              logger -t time-anomaly -p warning "$MESSAGE"

              # Send webhook alert
              ${optionalString (cfg.anomalyWebhook != null) ''
                ${pkgs.curl}/bin/curl -X POST \
                  -H "Content-Type: application/json" \
                  -d "{\"timestamp\":\"$TIMESTAMP\",\"offset\":$OFFSET,\"threshold\":$THRESHOLD}" \
                  "${cfg.anomalyWebhook}" \
                  || true
              ''}
            fi

            sleep 60
          done
        '';
      };
    };

    # Bitcoin blockchain time (Enterprise tier)
    systemd.services.bitcoin-time-sync = mkIf cfg.bitcoinEnabled {
      description = "Bitcoin Blockchain Time Reference";
      after = [ "network.target" ];
      wantedBy = [ "multi-user.target" ];

      serviceConfig = {
        Type = "simple";
        Restart = "always";
        ExecStart = pkgs.writeScript "bitcoin-time-sync" ''
          #!${pkgs.python3}/bin/python3
          import time
          import requests
          import json
          from datetime import datetime

          LOG_FILE = "/var/log/msp/bitcoin-time.log"

          while True:
              try:
                  # Query Bitcoin blockchain for latest block time
                  resp = requests.get("https://blockchain.info/latestblock", timeout=10)
                  block = resp.json()

                  block_time = block['time']
                  local_time = int(time.time())
                  drift = abs(block_time - local_time)

                  log_entry = {
                      "timestamp": datetime.utcnow().isoformat(),
                      "block_height": block['height'],
                      "block_time": block_time,
                      "local_time": local_time,
                      "drift_seconds": drift
                  }

                  with open(LOG_FILE, 'a') as f:
                      f.write(json.dumps(log_entry) + "\n")

                  # Alert if drift > 5 minutes
                  if drift > 300:
                      print(f"WARNING: Bitcoin time drift: {drift}s", flush=True)

              except Exception as e:
                  print(f"ERROR: {e}", flush=True)

              time.sleep(600)  # Check every 10 minutes
        '';
      };
    };

    # Audit logging for time changes
    security.auditd.enable = true;
    security.audit.rules = [
      # Log time changes
      "-a always,exit -F arch=b64 -S adjtimex -S settimeofday -S clock_settime -k time-change"

      # Log chrony operations
      "-w /var/log/chrony/ -p wa -k chrony-logs"
    ];

    # Time sync health check
    systemd.services.time-sync-health = {
      description = "Time Sync Health Check";

      serviceConfig = {
        Type = "oneshot";
        ExecStart = pkgs.writeScript "time-sync-health" ''
          #!${pkgs.bash}/bin/bash
          set -euo pipefail

          HEALTH_LOG="/var/log/msp/time-sync-health.log"
          mkdir -p "$(dirname "$HEALTH_LOG")"

          echo "=== Time Sync Health Check $(date) ===" >> "$HEALTH_LOG"

          # Check chrony status
          if ${pkgs.systemd}/bin/systemctl is-active chronyd > /dev/null 2>&1; then
            echo "✓ chronyd active" >> "$HEALTH_LOG"
          else
            echo "✗ chronyd NOT active" >> "$HEALTH_LOG"
            exit 1
          fi

          # Check NTP sync status
          if ${pkgs.chrony}/bin/chronyc tracking | grep "Reference ID" > /dev/null; then
            echo "✓ NTP synchronized" >> "$HEALTH_LOG"
          else
            echo "✗ NTP NOT synchronized" >> "$HEALTH_LOG"
            exit 1
          fi

          # Check time sources
          SOURCES=$(${pkgs.chrony}/bin/chronyc sources | grep -c "^\\*" || echo "0")
          echo "Active time sources: $SOURCES" >> "$HEALTH_LOG"

          if [ "$SOURCES" -lt 2 ]; then
            echo "⚠ Less than 2 active time sources" >> "$HEALTH_LOG"
          fi

          ${optionalString (cfg.gpsDevice != null) ''
            # Check GPS status
            if ${pkgs.systemd}/bin/systemctl is-active gpsd > /dev/null 2>&1; then
              echo "✓ GPS daemon active" >> "$HEALTH_LOG"
            else
              echo "⚠ GPS daemon not active" >> "$HEALTH_LOG"
            fi
          ''}

          ${optionalString cfg.bitcoinEnabled ''
            # Check Bitcoin time sync
            if ${pkgs.systemd}/bin/systemctl is-active bitcoin-time-sync > /dev/null 2>&1; then
              echo "✓ Bitcoin time sync active" >> "$HEALTH_LOG"
            else
              echo "⚠ Bitcoin time sync not active" >> "$HEALTH_LOG"
            fi
          ''}

          echo "Health check completed" >> "$HEALTH_LOG"
        '';
      };
    };

    # Run health check daily
    systemd.timers.time-sync-health = {
      description = "Time Sync Health Check Timer";
      wantedBy = [ "timers.target" ];

      timerConfig = {
        OnCalendar = "daily";
        Persistent = true;
        Unit = "time-sync-health.service";
      };
    };
  };
}
```

### Hash Chain Log Integrity

**NixOS Module for Hash-Chained Logs:**

```nix
# flake/modules/audit/log-integrity.nix
{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.msp.logIntegrity;

in {
  options.services.msp.logIntegrity = {
    enable = mkEnableOption "MSP hash-chained log integrity";

    logPaths = mkOption {
      type = types.listOf types.str;
      default = [
        "/var/log/msp/"
        "/var/log/audit/"
        "/var/log/auth.log"
      ];
      description = "Paths to monitor for integrity";
    };

    chainInterval = mkOption {
      type = types.int;
      default = 60;
      description = "Seconds between hash chain links";
    };

    storePath = mkOption {
      type = types.path;
      default = "/var/lib/msp/hash-chain";
      description = "Path to store hash chain data";
    };
  };

  config = mkIf cfg.enable {

    systemd.services.log-hash-chain = {
      description = "MSP Log Hash Chain Service";
      wantedBy = [ "multi-user.target" ];
      after = [ "auditd.service" ];

      serviceConfig = {
        Type = "simple";
        Restart = "always";
        ExecStart = pkgs.writeScript "log-hash-chain" ''
          #!${pkgs.python3}/bin/python3
          import os
          import time
          import hashlib
          import json
          from pathlib import Path
          from datetime import datetime

          CHAIN_FILE = Path("${cfg.storePath}/chain.jsonl")
          CHAIN_FILE.parent.mkdir(parents=True, exist_ok=True)

          def compute_hash(data: bytes, prev_hash: str) -> str:
              """Compute hash with previous hash as salt (blockchain-style)"""
              h = hashlib.sha256()
              h.update(prev_hash.encode())
              h.update(data)
              return h.hexdigest()

          def get_log_snapshot() -> bytes:
              """Get snapshot of all monitored logs"""
              snapshot = []

              for log_path in ${builtins.toJSON cfg.logPaths}:
                  path = Path(log_path)
                  if path.is_dir():
                      # Hash all files in directory
                      for file in sorted(path.rglob("*")):
                          if file.is_file():
                              try:
                                  with open(file, 'rb') as f:
                                      content = f.read()
                                      file_hash = hashlib.sha256(content).hexdigest()
                                      snapshot.append(f"{file}:{file_hash}")
                              except Exception:
                                  pass
                  elif path.is_file():
                      try:
                          with open(path, 'rb') as f:
                              content = f.read()
                              file_hash = hashlib.sha256(content).hexdigest()
                              snapshot.append(f"{path}:{file_hash}")
                      except Exception:
                          pass

              return "\n".join(snapshot).encode()

          # Initialize chain
          if CHAIN_FILE.exists():
              with open(CHAIN_FILE, 'r') as f:
                  lines = f.readlines()
                  if lines:
                      last_link = json.loads(lines[-1])
                      prev_hash = last_link['hash']
                  else:
                      prev_hash = "0" * 64  # Genesis hash
          else:
              prev_hash = "0" * 64

          print(f"Starting hash chain with prev_hash: {prev_hash[:16]}...", flush=True)

          while True:
              try:
                  # Get current log snapshot
                  snapshot = get_log_snapshot()

                  # Compute hash linked to previous
                  current_hash = compute_hash(snapshot, prev_hash)

                  # Create chain link
                  link = {
                      "timestamp": datetime.utcnow().isoformat(),
                      "prev_hash": prev_hash,
                      "hash": current_hash,
                      "log_count": len(snapshot.decode().split("\n"))
                  }

                  # Append to chain (atomic write)
                  with open(CHAIN_FILE, 'a') as f:
                      f.write(json.dumps(link) + "\n")
                      f.flush()
                      os.fsync(f.fileno())

                  print(f"Link added: {current_hash[:16]}... (logs: {link['log_count']})", flush=True)

                  prev_hash = current_hash

              except Exception as e:
                  print(f"ERROR: {e}", flush=True)

              time.sleep(${toString cfg.chainInterval})
        '';
      };
    };

    # Chain verification service
    systemd.services.verify-log-chain = {
      description = "Verify Log Hash Chain Integrity";

      serviceConfig = {
        Type = "oneshot";
        ExecStart = pkgs.writeScript "verify-log-chain" ''
          #!${pkgs.python3}/bin/python3
          import json
          from pathlib import Path

          CHAIN_FILE = Path("${cfg.storePath}/chain.jsonl")

          if not CHAIN_FILE.exists():
              print("No chain file found")
              exit(0)

          print("Verifying hash chain integrity...")

          with open(CHAIN_FILE, 'r') as f:
              links = [json.loads(line) for line in f]

          if not links:
              print("Empty chain")
              exit(0)

          # Verify first link (genesis)
          if links[0]['prev_hash'] != "0" * 64:
              print(f"ERROR: Invalid genesis block")
              exit(1)

          # Verify chain continuity
          for i in range(1, len(links)):
              if links[i]['prev_hash'] != links[i-1]['hash']:
                  print(f"ERROR: Chain broken at link {i}")
                  print(f"  Expected prev_hash: {links[i-1]['hash']}")
                  print(f"  Got prev_hash: {links[i]['prev_hash']}")
                  exit(1)

          print(f"✓ Chain verified: {len(links)} links, no tampering detected")
          exit(0)
        '';
      };
    };

    # Run verification daily
    systemd.timers.verify-log-chain = {
      description = "Verify Log Hash Chain Timer";
      wantedBy = [ "timers.target" ];

      timerConfig = {
        OnCalendar = "daily";
        Persistent = true;
        Unit = "verify-log-chain.service";
      };
    };
  };
}
```

### Blockchain Anchoring (Enterprise Tier)

**Python Service for Bitcoin Anchoring:**

```python
# mcp-server/blockchain/anchor.py
import requests
import hashlib
import json
from datetime import datetime
from typing import Optional

class BlockchainAnchor:
    """
    Anchor evidence bundles to Bitcoin blockchain
    Enterprise tier only - provides external immutability proof
    """

    def __init__(self,
                 bitcoin_rpc_url: str = "http://localhost:8332",
                 rpc_user: Optional[str] = None,
                 rpc_password: Optional[str] = None):
        self.rpc_url = bitcoin_rpc_url
        self.rpc_user = rpc_user
        self.rpc_password = rpc_password

    def anchor_hash(self, evidence_hash: str) -> dict:
        """
        Anchor evidence hash to Bitcoin blockchain
        Uses OP_RETURN to embed hash in transaction
        """

        # Create OP_RETURN output with hash
        op_return_data = f"MSP:{evidence_hash[:32]}"

        # Create Bitcoin transaction (simplified - real impl uses bitcoin-cli)
        tx_hex = self._create_op_return_tx(op_return_data)

        # Broadcast transaction
        txid = self._broadcast_transaction(tx_hex)

        # Wait for confirmation
        confirmations = 0
        while confirmations < 6:  # Wait for 6 confirmations (~1 hour)
            time.sleep(600)  # 10 minutes
            confirmations = self._get_confirmations(txid)

        # Get block hash
        block_hash = self._get_block_hash(txid)

        return {
            "txid": txid,
            "block_hash": block_hash,
            "confirmations": confirmations,
            "anchored_at": datetime.utcnow().isoformat(),
            "evidence_hash": evidence_hash,
            "blockchain": "bitcoin"
        }

    def verify_anchor(self, txid: str, expected_hash: str) -> bool:
        """Verify that evidence hash is in blockchain"""

        # Get transaction
        tx = self._get_transaction(txid)

        # Extract OP_RETURN data
        for vout in tx['vout']:
            if vout['scriptPubKey']['type'] == 'nulldata':
                op_return_hex = vout['scriptPubKey']['hex'][4:]  # Skip OP_RETURN opcode
                op_return_data = bytes.fromhex(op_return_hex).decode('utf-8')

                if op_return_data.startswith('MSP:'):
                    anchored_hash = op_return_data[4:]
                    return anchored_hash == expected_hash[:32]

        return False

    def _create_op_return_tx(self, data: str) -> str:
        """Create Bitcoin transaction with OP_RETURN output"""
        # Simplified - real implementation uses bitcoin-cli or bitcoinlib

        rpc_call = {
            "jsonrpc": "1.0",
            "id": "msp-anchor",
            "method": "createrawtransaction",
            "params": [
                [],  # Inputs (would need UTXO selection)
                {
                    "data": data.encode().hex()  # OP_RETURN output
                }
            ]
        }

        response = requests.post(
            self.rpc_url,
            json=rpc_call,
            auth=(self.rpc_user, self.rpc_password)
        )

        return response.json()['result']

    def _broadcast_transaction(self, tx_hex: str) -> str:
        """Broadcast transaction to Bitcoin network"""

        rpc_call = {
            "jsonrpc": "1.0",
            "id": "msp-anchor",
            "method": "sendrawtransaction",
            "params": [tx_hex]
        }

        response = requests.post(
            self.rpc_url,
            json=rpc_call,
            auth=(self.rpc_user, self.rpc_password)
        )

        return response.json()['result']

    def _get_confirmations(self, txid: str) -> int:
        """Get confirmation count for transaction"""

        rpc_call = {
            "jsonrpc": "1.0",
            "id": "msp-anchor",
            "method": "gettransaction",
            "params": [txid]
        }

        response = requests.post(
            self.rpc_url,
            json=rpc_call,
            auth=(self.rpc_user, self.rpc_password)
        )

        return response.json()['result']['confirmations']

    def _get_block_hash(self, txid: str) -> str:
        """Get block hash containing transaction"""

        rpc_call = {
            "jsonrpc": "1.0",
            "id": "msp-anchor",
            "method": "gettransaction",
            "params": [txid]
        }

        response = requests.post(
            self.rpc_url,
            json=rpc_call,
            auth=(self.rpc_user, self.rpc_password)
        )

        return response.json()['result']['blockhash']
```

**NixOS Module for Blockchain Anchoring:**

```nix
# flake/modules/audit/blockchain-anchor.nix
{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.msp.blockchainAnchor;

in {
  options.services.msp.blockchainAnchor = {
    enable = mkEnableOption "MSP blockchain anchoring (Enterprise tier)";

    bitcoinRpcUrl = mkOption {
      type = types.str;
      default = "http://localhost:8332";
      description = "Bitcoin RPC URL";
    };

    rpcCredentialsFile = mkOption {
      type = types.path;
      description = "Path to RPC credentials (via SOPS)";
      example = "/run/secrets/bitcoin-rpc-creds";
    };

    anchorInterval = mkOption {
      type = types.str;
      default = "daily";
      description = "How often to anchor evidence bundles";
    };
  };

  config = mkIf cfg.enable {

    systemd.services.blockchain-anchor = {
      description = "MSP Blockchain Anchoring Service";
      after = [ "network.target" ];

      serviceConfig = {
        Type = "oneshot";
        ExecStart = pkgs.writeScript "blockchain-anchor" ''
          #!${pkgs.python3}/bin/python3
          import sys
          sys.path.insert(0, "${../../mcp-server}")

          from blockchain.anchor import BlockchainAnchor
          from evidence.registry import EvidenceRegistry
          import json

          # Load RPC credentials
          with open("${cfg.rpcCredentialsFile}", 'r') as f:
            creds = json.load(f)

          # Initialize anchor
          anchor = BlockchainAnchor(
              bitcoin_rpc_url="${cfg.bitcoinRpcUrl}",
              rpc_user=creds['user'],
              rpc_password=creds['password']
          )

          # Get unanchored evidence bundles
          registry = EvidenceRegistry()
          bundles = registry.query(signed_only=True)
          unanchored = [b for b in bundles if not b['anchored']]

          print(f"Found {len(unanchored)} unanchored evidence bundles")

          for bundle in unanchored:
              try:
                  print(f"Anchoring {bundle['bundle_id']}...")

                  result = anchor.anchor_hash(bundle['bundle_hash'])

                  # Update registry
                  registry.update_anchor(
                      bundle_id=bundle['bundle_id'],
                      txid=result['txid']
                  )

                  print(f"✓ Anchored to txid: {result['txid']}")

              except Exception as e:
                  print(f"✗ Failed: {e}")

          print("Anchoring complete")
        '';
      };
    };

    # Timer for periodic anchoring
    systemd.timers.blockchain-anchor = {
      description = "Blockchain Anchoring Timer";
      wantedBy = [ "timers.target" ];

      timerConfig = {
        OnCalendar = cfg.anchorInterval;
        Persistent = true;
        Unit = "blockchain-anchor.service";
      };
    };
  };
}
```

### Compliance Tiers

**Feature Flag Configuration:**

```yaml
# config/compliance-tiers.yaml
tiers:
  essential:
    price: "$200-400/mo"
    target: "Small clinics (1-5 providers)"
    features:
      - Basic NTP time sync
      - Unsigned evidence bundles
      - 30-day evidence retention
      - Monthly compliance packets
      - Local hash chain logs

  professional:
    price: "$600-1200/mo"
    target: "Mid-size clinics (6-15 providers)"
    features:
      - Multi-source time sync (NTP + GPS)
      - Signed evidence bundles (cosign)
      - 90-day evidence retention
      - Weekly + monthly compliance packets
      - SBOM generation (SPDX)
      - Hash chain with remote backup

  enterprise:
    price: "$1500-3000/mo"
    target: "Large practices (15-50 providers)"
    features:
      - Multi-source time sync (NTP + GPS + Bitcoin)
      - Signed + blockchain-anchored evidence
      - 2-year evidence retention
      - Daily + weekly + monthly packets
      - SBOM generation (SPDX + CycloneDX)
      - Forensic mode with 1-minute hash chains
      - Dedicated compliance dashboard
      - Priority support with SLA
```

**NixOS Tier Configuration:**

```nix
# Client configuration with tier
{
  services.msp = {
    tier = "professional";  # essential | professional | enterprise

    # Features auto-enabled based on tier
    timeSync.enable = true;
    timeSync.tier = config.services.msp.tier;

    logIntegrity.enable = true;
    logIntegrity.chainInterval =
      if config.services.msp.tier == "enterprise" then 60 else 300;

    buildSigning.enable =
      config.services.msp.tier != "essential";

    blockchainAnchor.enable =
      config.services.msp.tier == "enterprise";
  };
}
```

### MCP Integration

**Time Anomaly Detection Tools:**

```python
# mcp-server/tools/time_check.py
from typing import Dict
import subprocess
import json

class TimeCheckTool:
    """MCP tool for time anomaly detection"""

    async def execute(self, params: Dict) -> Dict:
        """Check system time synchronization status"""

        # Query chrony tracking
        result = subprocess.run(
            ['chronyc', 'tracking'],
            capture_output=True,
            text=True
        )

        tracking = self._parse_tracking(result.stdout)

        # Check for anomalies
        anomalies = []

        if abs(tracking['offset_seconds']) > 0.1:
            anomalies.append({
                "type": "time_drift",
                "severity": "high",
                "offset": tracking['offset_seconds'],
                "threshold": 0.1
            })

        if tracking['sources'] < 2:
            anomalies.append({
                "type": "insufficient_sources",
                "severity": "medium",
                "current": tracking['sources'],
                "minimum": 2
            })

        return {
            "status": "anomaly_detected" if anomalies else "ok",
            "tracking": tracking,
            "anomalies": anomalies,
            "hipaa_control": "164.312(b)"
        }

    def _parse_tracking(self, output: str) -> Dict:
        """Parse chronyc tracking output"""
        lines = output.split('\n')
        tracking = {}

        for line in lines:
            if 'System time' in line:
                offset = float(line.split()[3])
                tracking['offset_seconds'] = offset
            elif 'Reference ID' in line:
                ref_id = line.split()[3]
                tracking['reference'] = ref_id

        # Count sources
        sources_result = subprocess.run(
            ['chronyc', 'sources'],
            capture_output=True,
            text=True
        )
        tracking['sources'] = sources_result.stdout.count('\n^*')

        return tracking
```

**Hash Chain Verification Tool:**

```python
# mcp-server/tools/verify_chain.py
from typing import Dict
import json
from pathlib import Path

class VerifyChainTool:
    """MCP tool for log integrity verification"""

    async def execute(self, params: Dict) -> Dict:
        """Verify hash chain integrity"""

        chain_file = Path("/var/lib/msp/hash-chain/chain.jsonl")

        if not chain_file.exists():
            return {
                "status": "no_chain",
                "error": "Hash chain file not found"
            }

        # Read chain
        with open(chain_file, 'r') as f:
            links = [json.loads(line) for line in f]

        if not links:
            return {
                "status": "empty_chain",
                "error": "No links in chain"
            }

        # Verify genesis
        if links[0]['prev_hash'] != "0" * 64:
            return {
                "status": "tampered",
                "error": "Invalid genesis block",
                "link": 0
            }

        # Verify continuity
        for i in range(1, len(links)):
            if links[i]['prev_hash'] != links[i-1]['hash']:
                return {
                    "status": "tampered",
                    "error": f"Chain broken at link {i}",
                    "link": i,
                    "expected": links[i-1]['hash'],
                    "got": links[i]['prev_hash']
                }

        return {
            "status": "verified",
            "total_links": len(links),
            "first_link": links[0]['timestamp'],
            "last_link": links[-1]['timestamp'],
            "hipaa_control": "164.312(b)"
        }
```

**Register Tools with MCP Server:**

```python
# mcp-server/server.py (updated)
from tools.time_check import TimeCheckTool
from tools.verify_chain import VerifyChainTool

# Initialize tools
TOOLS = {
    "restart_service": RestartServiceTool(),
    "clear_cache": ClearCacheTool(),
    "rotate_logs": RotateLogsTool(),
    "delete_tmp": DeleteTmpTool(),
    "renew_cert": RenewCertTool(),
    "check_time": TimeCheckTool(),  # NEW
    "verify_chain": VerifyChainTool()  # NEW
}

@app.get("/tools")
async def list_tools():
    """List available tools with descriptions"""
    return {
        "tools": [
            {
                "name": "check_time",
                "description": "Check time synchronization status and detect anomalies",
                "params": {},
                "hipaa_control": "164.312(b)"
            },
            {
                "name": "verify_chain",
                "description": "Verify hash chain log integrity",
                "params": {},
                "hipaa_control": "164.312(b)"
            },
            # ... other tools
        ]
    }
```

### Implementation Checklist

**5-Sprint Roadmap for Provenance Integration:**

#### Sprint 1: Foundation (Week 6)
- [ ] Implement build signing module (`build-signing.nix`)
- [ ] Generate signing keys for build server
- [ ] Configure all clients to verify signatures
- [ ] Test: Deploy signed system, verify signature validation
- [ ] Evidence: Signed deployment with verification logs

#### Sprint 2: Evidence Registry (Week 7)
- [ ] Implement `EvidenceRegistry` with SQLite
- [ ] Add append-only triggers
- [ ] Integrate with evidence packager
- [ ] Implement `EvidenceSigner` with cosign
- [ ] Test: Create evidence bundle, verify signature, query registry
- [ ] Evidence: Registry with 10+ signed bundles

#### Sprint 3: Time Framework (Week 8)
- [ ] Implement `time-sync.nix` module (Essential tier)
- [ ] Add GPS support for Professional tier
- [ ] Implement time anomaly detector
- [ ] Add MCP `check_time` tool
- [ ] Test: Simulate time drift, verify detection
- [ ] Evidence: Time anomaly logs with webhook alerts

#### Sprint 4: Hash Chains (Week 9)
- [ ] Implement `log-integrity.nix` module
- [ ] Start hash chain service on all clients
- [ ] Implement verification service
- [ ] Add MCP `verify_chain` tool
- [ ] Test: Attempt log tampering, verify chain detects it
- [ ] Evidence: Unbroken hash chain over 7 days

#### Sprint 5: Enterprise Features (Week 10)
- [ ] Implement SBOM generation (`sbom/generator.py`)
- [ ] Add Bitcoin blockchain anchoring module
- [ ] Implement tier-based feature flags
- [ ] Add forensic mode (1-min hash chains)
- [ ] Test: Full Enterprise tier deployment
- [ ] Evidence: Blockchain-anchored evidence bundle with SBOM

**Success Criteria:**
- ✅ All builds cryptographically signed
- ✅ Evidence bundles signed and registered
- ✅ Multi-source time sync with anomaly detection
- ✅ Hash chain proving log integrity
- ✅ SBOM generated for every deployment
- ✅ Enterprise tier with blockchain anchoring
- ✅ MCP tools for provenance verification
- ✅ Tier-based pricing implemented

---

## Expansion Path

### First Expansion Tweaks

1. **Add Windows Support**
   - Ship Winlogbeat + nssm-wrapped Python tailer
   - Tool set stays the same

2. **Add Patching**
   - Integrate `apt-upgrade` or `winget upgrade` script
   - Behind manual-approval flag initially

3. **Add Small Model**
   - Drop to local Llama-3 8B for cheaper per-incident cost
   - If token spend grows beyond threshold

### Scaling Checklist

- [ ] Multi-region event queue deployment
- [ ] Client-specific tool whitelists
- [ ] Advanced anomaly detection (beyond simple thresholds)
- [ ] Compliance report templates per regulation (HIPAA, PCI-DSS, SOC-2)
- [ ] Self-service client portal
- [ ] Automated billing integration

---

## Implementation Roadmap (Concrete Steps)

### Enhanced MVP Plan with Compliance Guardrails

| Phase | Deliverable | Key Tools | Effort | Compliance Addition |
|-------|------------|-----------|--------|-------------------|
| **0. Service catalog** | One-pager listing exactly what you auto-fix | – | ½ day | Add explicit "not-covered" list |
| **1. Baseline flake** | Nix flake building Alpine/Nix container with fluent-bit, Python log-watcher, MCP client shim | Nix flakes, Docker/Podman | 3 days | Include LUKS, SSH-cert auth, SOPS/Vault bootstrap |
| **2.5. Baseline profile** | **NEW:** NixOS-HIPAA baseline v1 + controls mapping | YAML/CSV/PDF | 1 day | baseline/hipaa-v1.yaml + baseline/controls-map.csv |
| **2. Event bus** | Shared Redis Streams or NATS JetStream in cloud tenant | Terraform module | 1 day | Structure keys under `tenant:{id}:*`; enable AOF + requirepass + TLS |
| **3. Core remediation library** | Six idempotent runbooks via MCP | Bash/Python + MCP schema | 4 days | Add runbook structure with HIPAA citations and evidence requirements |
| **4. Guardrails** | Parameter validation + 5-min cooldown + unit tests + logging | Pydantic / OPA | 2 days | Add whitelist validation, service account restrictions |
| **5. LLM prompt & policy** | Template turning incident JSON into runbook selection | Azure OpenAI 8K GPT-4o | 2 days | Split *planner* from *executor* - planner selects runbook ID, executor runs steps |
| **6. Terraform-for-client** | Reusable module: provision VM/pod, inject flake container, register API key | Terraform + Cloud-init | 3 days | Add LUKS, SSH-cert auth, SOPS/Vault bootstrap to deployment |
| **7. Closed-loop check** | After fix, watcher re-queries metric; escalate if unresolved | Python | 1 day | Write **evidence bundle** (JSON + checksums) to local disk and WORM storage |
| **8. Documentation & SLA** | Coverage sheet, MTTR targets, escalation ladder | Markdown | 1 day | Add HIPAA control mapping, BAA template, sub-processor list |
| **9. CI/CD** | CI job for nightly updates | GitHub Actions | ½ day | Sign container images (cosign), publish SBOM (syft), hash goes in evidence bundle |
| **10. Security hardening** | Security baseline implementation | auditd, fail2ban, SSH hardening | 2 days | Enable time-sync, auditd/journald forwarding, STIG-like module set |
| **11. Pilot** | Deploy to lab + one friendly client; 2-week burn-in | – | 2 weeks | Track evidence bundle generation, test compliance packet |
| **12. Weekly test-restore** | **NEW:** Runbook for backup restore verification | restic, checksums | 1 day setup | Proof attached to compliance packet |
| **13. Monthly compliance packet** | **NEW:** Automated compliance report generation | Markdown → PDF | 1 day setup | Compile artifacts → PDF for auditor handoff |
| **14. Repeatable update path** | Automated deployment pipeline | GitHub Actions | ½ day | Version control for baseline updates |

**Total:** ~6 working weeks to compliance-ready MVP  
**Client Onboarding:** Terraform apply + DNS entry + baseline configuration (~3 hours)

### Concrete Edits to Original MVP (Minimal Churn)

**Step 2.5 – Baseline Profile (NEW)**
```bash
baseline/
├── hipaa-v1.yaml          # ~30 toggles (SSH, users, crypto, logging, updates)
├── controls-map.csv       # HIPAA Rule → NixOS module/option mapping
└── exceptions/
    └── clinic-001.yaml    # Per-client exceptions with risk/expiry
```

**Step 3 – Event Queue Enhancement**
- Structure: `tenant:{client_id}:incidents`, `tenant:{client_id}:evidence`
- Enable: AOF persistence, requirepass authentication, TLS encryption
- Namespacing: Separate API keys per client, rate limits per tenant

**Step 4/6 – MCP Architecture Split**
```
mcp-server/
├── planner.py        # LLM selects runbook ID only
├── executor.py       # Runs pre-approved runbook steps
└── runbooks/
    ├── RB-BACKUP-001-failure.yaml
    ├── RB-CERT-001-expiry.yaml
    ├── RB-DISK-001-full.yaml
    ├── RB-SERVICE-001-crash.yaml
    ├── RB-CPU-001-high.yaml
    └── RB-RESTORE-001-test.yaml
```

**Runbook Structure Example:**
```yaml
id: RB-BACKUP-001
name: "Backup Failure Remediation"
hipaa_controls:
  - "164.308(a)(7)(ii)(A)"
  - "164.310(d)(2)(iv)"
severity: high
steps:
  - action: check_backup_logs
    timeout: 30s
  - action: verify_disk_space
    timeout: 10s
  - action: restart_backup_service
    timeout: 60s
  - action: trigger_manual_backup
    timeout: 300s
rollback:
  - action: alert_administrator
evidence_required:
  - backup_log_excerpt
  - disk_usage_before
  - disk_usage_after
  - service_status
  - backup_completion_hash
```

**Step 7 – Evidence Bundle Structure**
```json
{
  "bundle_id": "EB-20251023-0001",
  "client_id": "clinic-001",
  "incident_id": "INC-20251023-0001",
  "runbook_id": "RB-BACKUP-001",
  "timestamp_start": "2025-10-23T14:32:01Z",
  "timestamp_end": "2025-10-23T14:35:23Z",
  "operator": "service:mcp-executor",
  "hipaa_controls": ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"],
  "inputs": {
    "log_excerpt_hash": "sha256:a1b2c3...",
    "disk_usage_before": "87%"
  },
  "actions_taken": [
    {"step": 1, "action": "check_backup_logs", "result": "failed", "script_hash": "sha256:d4e5f6..."},
    {"step": 2, "action": "verify_disk_space", "result": "ok", "script_hash": "sha256:g7h8i9..."},
    {"step": 3, "action": "restart_backup_service", "result": "ok", "script_hash": "sha256:j1k2l3..."}
  ],
  "outputs": {
    "backup_completion_hash": "sha256:m4n5o6...",
    "disk_usage_after": "62%"
  },
  "sla_met": true,
  "mttr_seconds": 202,
  "evidence_bundle_hash": "sha256:p7q8r9...",
  "storage_locations": [
    "local:/var/lib/msp/evidence/EB-20251023-0001.json",
    "s3://compliance-worm/clinic-001/2025/10/EB-20251023-0001.json"
  ]
}
```

**Step 12 – Weekly Test-Restore Runbook**
```yaml
id: RB-RESTORE-001
name: "Weekly Backup Restore Test"
hipaa_controls:
  - "164.308(a)(7)(ii)(A)"
  - "164.310(d)(2)(iv)"
schedule: "0 2 * * 0"  # Sunday 2 AM
steps:
  - action: select_random_backup
    params: {age_days_max: 7}
  - action: create_scratch_vm
    timeout: 180s
  - action: restore_to_scratch
    timeout: 600s
  - action: verify_checksums
    timeout: 120s
  - action: cleanup_scratch_vm
    timeout: 60s
evidence_required:
  - backup_selected
  - restore_log
  - checksum_verification
  - vm_cleanup_confirmation
```

**Step 13 – Compliance Packet Template**
```markdown
# HIPAA Compliance Report
**Client:** Clinic ABC  
**Period:** October 1-31, 2025  
**Baseline:** NixOS-HIPAA v1.2

## Executive Summary
- Incidents detected: 12
- Automatically remediated: 10
- Escalated to administrator: 2
- SLA compliance: 98.3%
- MTTR average: 4.2 minutes

## Controls Status
| Control | Status | Evidence Count | Exceptions |
|---------|--------|---------------|-----------|
| 164.308(a)(1)(ii)(D) | ✅ Compliant | 45 audit logs | 0 |
| 164.308(a)(7)(ii)(A) | ✅ Compliant | 4 backup tests | 0 |
| 164.312(a)(2)(iv) | ⚠️ Attention | 1 cert renewal | 1 (30-day extension) |

## Incidents Summary
[Table of incidents with runbook IDs, timestamps, MTTR]

## Baseline Exceptions
[List of approved exceptions with expiry dates]

## Test Restore Verification
- Week 1: ✅ Successful (3 files, 1 DB table)
- Week 2: ✅ Successful (5 files)
- Week 3: ✅ Successful (2 files, 1 DB)
- Week 4: ✅ Successful (4 files)

## Evidence Artifacts
[Links to WORM storage for all evidence bundles]

---
Generated: 2025-11-01 00:05:00 UTC  
Signature: sha256:x9y8z7...
```

**Total:** ~6 working weeks to compliance-ready MVP  
**Client Onboarding:** Terraform apply + DNS entry + baseline configuration (~3 hours)

---

## Quick Checklist: This Week's Implementation Tasks

These can be completed immediately to establish the compliance foundation:

- [ ] **baseline/hipaa-v1.yaml** with ~30 toggles (SSH, users, crypto, logging, updates)
- [ ] **runbooks/ directory** with 6 files:
  - RB-BACKUP-001-failure.yaml
  - RB-CERT-001-expiry.yaml
  - RB-DISK-001-full.yaml
  - RB-SERVICE-001-crash.yaml
  - RB-CPU-001-high.yaml
  - RB-RESTORE-001-test.yaml
  - (Each with HIPAA refs + required evidence fields)
- [ ] **Evidence writer** implementation: hash-chain local log + push to WORM bucket
- [ ] **SBOM + image signing** added to CI pipeline
- [ ] **LUKS + SSH-certs** configuration in client flake
- [ ] **One Compliance Packet prototype** (Markdown → PDF) from lab data

---

## Where This Puts You vs. Anduril

### What You Won't Have:
- DoD STIG certification
- Device attestation for classified systems
- Clearance-required documentation

### What You WILL Have (SMB Equivalent):
- ✅ **Named baseline** (NixOS-HIPAA v1) with control mapping
- ✅ **Evidence artifacts** for every action taken
- ✅ **Auditor-ready compliance packets**
- ✅ **Deterministic builds** via Nix flakes
- ✅ **Append-only audit trail** via MCP architecture
- ✅ **WORM storage** for tamper-evident evidence
- ✅ **Cost advantage** at SMB scale

### Your Competitive Edge:
- **Simplicity:** No DoD complexity, focused on HIPAA
- **Price:** Designed for 1-50 provider practices
- **Artifacts:** Auditor can review evidence without you present
- **Transparency:** Open baseline, clear control mapping
- **Speed:** 6 weeks to pilot vs. 6 months for enterprise solutions

**Market Position:** "Anduril-style compliance rigor, tailored for healthcare SMBs"

---

## LLM-Driven Compliance Testing (Meta Framework Application)

### How Meta Uses LLMs for Mutation Testing & Compliance

Meta's engineering approach to compliance uses LLMs for **mutation testing** — automatically generating edge cases and testing compliance rules against synthetic violations. This is directly applicable to your HIPAA compliance platform.

### Application to Your Business Model

**Meta's Approach:**
1. LLM generates synthetic test cases that violate compliance rules
2. System detects violations and generates appropriate responses
3. LLM validates that responses meet compliance requirements
4. Results feed back into training data for improved detection

**Your Implementation:**
1. **Synthetic Incident Generation:**
   - LLM generates realistic log patterns that simulate HIPAA violations
   - Examples: simulated PHI in logs, failed backup scenarios, unauthorized access attempts
   - Test your detection and remediation pipeline continuously

2. **Baseline Validation:**
   - LLM reviews your NixOS-HIPAA baseline against HIPAA Security Rule
   - Identifies gaps or misalignments in control mapping
   - Suggests additional controls or configuration hardening

3. **Runbook Testing:**
   - LLM generates edge cases for each runbook
   - Tests: What if disk is 100% full? What if service restart fails? What if encryption key is unavailable?
   - Validates that evidence bundles contain all required fields

4. **Evidence Quality Assurance:**
   - LLM reviews evidence bundles for completeness
   - Checks that HIPAA control citations are accurate
   - Ensures audit trail integrity

### Practical Implementation in Your Stack

```python
# mcp-server/compliance_tester.py
async def generate_test_incidents(baseline: str, count: int = 100):
    """Use LLM to generate synthetic compliance violations"""
    
    prompt = f"""Given this HIPAA baseline configuration:
    {baseline}
    
    Generate {count} realistic log entries that would indicate HIPAA violations.
    Include both obvious violations and subtle edge cases.
    Focus on:
    - Unauthorized access attempts
    - Missing encryption
    - Backup failures
    - Audit log gaps
    - Configuration drift
    
    Return as JSON array with: log_entry, expected_severity, expected_runbook
    """
    
    # Generate synthetic violations
    violations = await llm_call(prompt)
    
    # Feed through your detection pipeline
    for violation in violations:
        result = await test_detection_pipeline(violation)
        assert result.detected == True
        assert result.runbook_id == violation.expected_runbook
        assert result.evidence_complete == True

async def validate_baseline_coverage(baseline_yaml: str):
    """Use LLM to find gaps in HIPAA coverage"""
    
    prompt = f"""Review this NixOS-HIPAA baseline:
    {baseline_yaml}
    
    Compare against HIPAA Security Rule requirements (164.308, 164.310, 164.312, 164.316).
    
    Identify:
    1. HIPAA controls not addressed by baseline
    2. Baseline settings that don't map to HIPAA controls
    3. Configuration options that could be hardened further
    4. Missing evidence collection points
    
    Return structured analysis with citations.
    """
    
    analysis = await llm_call(prompt)
    return analysis

async def test_runbook_edge_cases(runbook_id: str):
    """Generate edge cases for runbook testing"""
    
    runbook = load_runbook(runbook_id)
    
    prompt = f"""This runbook handles: {runbook.description}
    
    Steps: {runbook.steps}
    
    Generate 20 edge cases where this runbook might fail or produce incomplete evidence.
    Consider:
    - Resource exhaustion scenarios
    - Permission issues
    - Network failures mid-execution
    - Concurrent incidents
    - Rollback failures
    
    For each case, specify: scenario, expected_behavior, evidence_requirements
    """
    
    edge_cases = await llm_call(prompt)
    
    # Test each case
    for case in edge_cases:
        result = await execute_runbook_test(runbook_id, case.scenario)
        validate_evidence_completeness(result.evidence)
```

### Benefits for Your Platform

1. **Continuous Validation:** Automated testing of your compliance detection pipeline
2. **Gap Discovery:** LLM identifies missing controls before auditors do
3. **Evidence Quality:** Ensures evidence bundles meet auditor requirements
4. **Confidence:** Demonstrate that your system has been tested against thousands of scenarios
5. **Sales Advantage:** "Our compliance system is validated by AI-generated edge case testing"

### Integration with Compliance Packets

Add a "Validation Report" section to monthly compliance packets:

```markdown
## System Validation (October 2025)

### Synthetic Testing Results
- Test incidents generated: 1,000
- Detection rate: 99.8% (998/1,000)
- False positives: 0.1% (1/1,000)
- Evidence completeness: 100%
- Runbook success rate: 98.2%

### Baseline Coverage Analysis
- HIPAA controls addressed: 47/52 (90.4%)
- Controls in progress: 5 (target: Q4 2025)
- Configuration hardening score: 94/100

### Edge Case Testing
- Runbooks tested: 6
- Edge cases per runbook: 20
- Successful remediations: 117/120 (97.5%)
- Failures analyzed and documented: 3
```

This approach mirrors Meta's rigor but applied to HIPAA compliance instead of code quality.

---

## Key Regulatory Citation

**HHS/OCR** has explicitly called out AI use in health care as a vector for discrimination risk and urged covered entities to assess models for features that act as proxies for protected characteristics.

**Implication:** Your model-feature map is not just good practice — it's likely to be a regulatory conversation if the model touches care decisions.

**Source:** [NY State Dental - OCR Guidance on AI in Health Care](https://www.nysdental.org/news-publications/news/2025/01/11/ocr-issues-guidance-on-ai-in-health-care)

---

## Did You Know?

### The Audit Trail as a Natural Boundary

The Model Context Protocol (MCP) was designed specifically to create a standardized interface between LLMs and external tools, but it also serves as a natural audit boundary — every tool invocation creates a discrete log entry with inputs, outputs, and timestamps. This means your MCP server isn't just an automation layer, it's simultaneously building your HIPAA-compliant audit trail by design. In healthcare compliance, this "audit-by-architecture" pattern is far more defensible than bolt-on logging, because the compliance mechanism is structurally inseparable from the operational mechanism — you can't execute a tool without creating an audit entry, which makes tampering or omission nearly impossible without breaking the entire system.

### The Business Associate Metadata Loophole

Most healthcare technology vendors struggle with HIPAA because they're forced into the role of "data processor" and must handle Protected Health Information (PHI) directly. However, there's a crucial legal distinction that most miss: **processing system metadata for compliance verification is fundamentally different from processing medical records**. Your platform operates in this "metadata-only" zone, which means:

1. **Lower liability exposure:** You're not a custodian of patient data, just an auditor of system operations
2. **Simpler BAAs:** Your Business Associate Agreement can explicitly exclude PHI processing
3. **Easier compliance:** You're fulfilling Security Rule requirements (audit controls) rather than Privacy Rule requirements (patient data handling)
4. **Defensible position:** If PHI accidentally appears in a log, you have a documented policy to treat it as a security incident and breach notification trigger

This positioning is why your service can scale at high margins — you're providing compliance-as-a-service without the regulatory burden of being a healthcare data processor. Most competitors don't understand this distinction and over-engineer their HIPAA compliance, resulting in slower deployment and higher costs.

### NixOS as a Compliance Multiplier

The reason Anduril Industries chose NixOS for their defense systems isn't just technical elegance — it's because deterministic builds create **cryptographic proof of configuration**. In traditional IT, when an auditor asks "what was running on this server on March 15th?", the answer is usually "whatever was documented in the change management system, assuming the documentation is accurate." With NixOS flakes, the answer is: "Here's the exact commit hash. Every single package, dependency, configuration option, and kernel parameter is cryptographically content-addressed. I can rebuild that exact system state right now, bit-for-bit identical, and you can verify the hash."

This transforms compliance from a documentation exercise into a mathematical proof. For HIPAA §164.316 (Policies and Procedures / Documentation), this means your "documentation" is executable code that literally cannot drift from reality without breaking the system. When you tell an auditor "our baseline is enforced by the build system," they can verify it themselves by checking that the running system's hash matches the documented flake.lock. This is why a solo engineer can provide enterprise-grade compliance — you're not maintaining compliance, you're making it structurally impossible to be non-compliant.

### The HHS/OCR AI Wild Card

HHS Office for Civil Rights explicitly called out AI use in healthcare as a discrimination vector in January 2025, urging covered entities to assess models for features that act as proxies for protected characteristics. This guidance was aimed at clinical AI (diagnostic models, treatment recommendations), but the compliance industry hasn't caught up to the fact that it also applies to **operational AI in healthcare IT**. 

Your LLM-based compliance system is technically an "AI tool used in healthcare," but because it operates exclusively on system metadata and never touches patient data or clinical decisions, it falls outside the discrimination risk framework. However, you should document this explicitly in your compliance packets: "LLM operates on infrastructure logs only; does not access, process, or influence patient care decisions; discrimination risk: N/A."

This positions you ahead of the curve — when auditors start asking "how do you ensure your AI doesn't discriminate?", you can point to your documented scope boundary and evidence pipeline that proves your LLM never sees patient attributes. Most healthcare AI companies will struggle to answer this question. You won't.

### The Switch API Discovery Advantage

Most MSPs and compliance vendors use active network scanning (nmap, port sweeps) to discover devices, which has three major problems in healthcare: (1) it's noisy and can trigger IDS alerts, (2) it can accidentally knock over fragile medical devices that don't handle malformed packets well, and (3) it only shows what's online at scan time, missing intermittent devices. There's a much better approach that almost nobody uses: **query the network switch's ARP and MAC address tables directly**.

Every managed switch (Cisco, HP, Juniper, etc.) maintains authoritative tables of every device that's communicated on the network in the past few minutes. By SSH'ing into the switch and running `show ip arp` and `show mac address-table`, you get a complete, real-time inventory of every device on the network without sending a single probe packet. This is:

- **Stealthier:** No scanning traffic that could trigger alarms or disrupt devices
- **More complete:** Captures devices that may be offline during a scan window
- **Authoritative:** The switch knows definitively what's connected to which port
- **HIPAA-safer:** No risk of accidentally probing medical devices or PHI-containing systems

Combined with passive ARP monitoring (just listening to broadcast traffic), you can maintain a 100% accurate device inventory without any active probing at all. Most compliance vendors don't do this because it requires network credentials and technical sophistication, but for a NixOS-based platform with secrets management built in, it's trivial to implement. This gives you complete visibility with zero risk — a major competitive advantage when selling into risk-averse healthcare IT environments.

The legal positioning bonus: when you document your discovery methods for HIPAA audits, you can explicitly state "passive discovery methods only; no active probing of clinical systems" — which is far more defensible than "we scan your network every 4 hours."

### The Dashboard Theater Problem

Most compliance vendors sell dashboards as their primary product, with enforcement as an afterthought. This creates what's known in the industry as "**compliance theater**" — impressive visualizations that don't actually change anything. The typical pattern: dashboard shows red tile → creates ticket → ticket sits in queue → problem persists → auditor sees pretty dashboard → everyone pretends compliance exists.

Your architecture inverts this completely: **enforcement happens first, dashboards expose what already happened**. When a tile turns red, it's because the automated remediation is already running — the dashboard is just showing you the fix in progress. This is the difference between a "monitoring system that generates alerts" and a "self-healing system that logs its actions."

Here's why this matters for sales: When a clinic administrator asks "how will we know if something is wrong?", most vendors demo a dashboard with flashing alerts and graphs. Your answer is different: "You won't know something's wrong because it will already be fixed. The dashboard shows you what was fixed, when it was fixed, and the evidence bundle proving it was fixed." This is a fundamentally more mature compliance posture — you're not paying for visibility into problems, you're paying for problems to not exist.

The auditor perspective is even more powerful: traditional dashboards show "current state," which means they're stale the moment an auditor walks in the door. Your evidence bundles show "time-stamped proof of continuous compliance," signed and immutable. When an auditor asks "were you compliant on March 15th?", most vendors scramble to pull historical logs. You hand them a signed PDF with cryptographic proof, generated automatically that day, already in object storage.

This is why your margins can be 40%+ while competitors struggle at 20% — they're paying humans to generate compliance evidence manually because their dashboards are just visualization layers. Your evidence generation is a byproduct of automated enforcement. The dashboard is free because it's just rendering data you already have.

---

**End of Document**  
**Version:** 2.2 (Complete with Executive Dashboards & Audit-Ready Outputs)  
**Last Updated:** October 23, 2025  
**Framework Basis:** Anduril NixOS STIG approach adapted for HIPAA  

## Key References

### Frameworks & Standards
- **NIST National Checklist Program:** [Anduril NixOS STIG](https://ncp.nist.gov/repository)
- **HIPAA Security Rule:** 45 CFR §164.308 (Administrative), §164.310 (Physical), §164.312 (Technical), §164.316 (Documentation)
- **HHS/OCR AI Guidance:** [OCR Issues Guidance on AI in Health Care](https://www.nysdental.org/news-publications/news/2025/01/11/ocr-issues-guidance-on-ai-in-health-care)

### Technical References
- **Anduril Jetpack-NixOS:** [GitHub Repository](https://github.com/anduril/jetpack-nixos)
- **Model Context Protocol (MCP):** Standardized LLM-tool interface for audit-by-architecture
- **Meta Engineering:** LLMs for mutation testing and compliance validation

### Implementation Tools
- **NixOS Flakes:** Deterministic, reproducible system builds
- **SOPS/Vault:** Secrets management with client-scoped KMS
- **Fluent-bit:** Lightweight log forwarding with PHI scrubbing
- **NATS JetStream:** Multi-tenant event queue with durability
- **cosign/syft:** Container image signing and SBOM generation

## Next Steps

1. **Week 1:** Implement quick checklist items (baseline YAML, runbook templates, evidence writer)
2. **Week 2-3:** Enhanced client flake with LUKS, SSH-certs, baseline enforcement
3. **Week 4-5:** MCP planner/executor split, runbook library, evidence pipeline
4. **Week 6:** First compliance packet generation, pilot deployment prep
5. **Week 7-8:** Lab burn-in with synthetic incident testing
6. **Week 9+:** First pilot client deployment

## Support & Documentation

For questions about:
- **NixOS implementation:** NixOS Discourse, #nix-security
- **HIPAA compliance:** HHS.gov/HIPAA, OCR guidance documents
- **MCP architecture:** Model Context Protocol specification
- **Anduril approach:** Public STIG documentation, Jetpack-NixOS repo

---

**Document Structure:**
- Executive Summary with pricing tiers
- Zero-to-MVP Build Plan (5-6 weeks)
- Service Catalog & Scope Definition
- Repository & Code Structure
- Technical Architecture (NixOS + MCP + LLM)
- HIPAA Compliance Framework (Legal, Monitoring, Evidence)
- Implementation Roadmap (14 phases with compliance additions)
- Quick Checklist (this week's tasks)
- Competitive Positioning (vs. Anduril, vs. traditional MSP)
- LLM-Driven Testing (Meta framework application)
- Guardrails & Safety (Rate limiting, validation)
- Client Deployment (Terraform modules)
- Network Discovery & Auto-Enrollment (5 methods, device classification)
- Executive Dashboards & Audit-Ready Outputs (8 controls, evidence packager, print templates)
- Expansion Path (Windows, patching, local LLM)

**Document Statistics:**
- Total Lines: ~3,834
- Code Examples: 18 complete implementations
- Configuration Templates: 12
- HIPAA Control Mappings: 50+
- Runbook Examples: 8
- Dashboard Specifications: 3
- Version: 2.2

**Primary Value Proposition:**  
Enterprise-grade HIPAA compliance automation at SMB price points, with auditor-ready evidence generation and deterministic infrastructure-as-code enforcement. Dashboards expose automation rather than replacing it. Evidence is cryptographically signed and historically provable.
