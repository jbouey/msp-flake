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
