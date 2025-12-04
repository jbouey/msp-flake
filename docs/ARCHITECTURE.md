# MSP Platform Architecture

## Overview

**Stack:** NixOS + MCP + LLM
**Target:** Small to mid-sized clinics (NEPA region)
**Service Model:** Auto-heal infrastructure + HIPAA compliance monitoring

## Repository Structure

```
MSP-PLATFORM/
├── packages/
│   └── compliance-agent/     # Python agent (main work area)
│       ├── src/compliance_agent/
│       ├── tests/
│       └── venv/
├── modules/                  # NixOS modules
│   └── compliance-agent.nix
├── mcp-server/              # Central MCP server
│   ├── server.py            # FastAPI with LLM integration
│   ├── tools/               # Remediation tools
│   └── guardrails/          # Safety controls
├── terraform/               # Infrastructure as Code
│   ├── modules/
│   └── clients/
└── docs/                    # This directory
```

## Technical Stack

| Component | Technology | Purpose |
|-----------|------------|---------|
| Infrastructure | NixOS Flakes | Deterministic, auditable configuration |
| Agent | Python 3.13 | Drift detection, healing, evidence |
| Communication | MCP Protocol | Structured LLM-to-tool interface |
| LLM | GPT-4o / Claude | Incident triage, runbook selection |
| Queue | NATS JetStream | Multi-tenant event durability |
| Evidence | WORM S3/MinIO | Tamper-evident storage |
| Signing | Ed25519 | Cryptographic evidence bundles |

## Three-Tier Auto-Healing

```
Incident Flow:
┌─────────────┐
│  Incident   │
└──────┬──────┘
       ▼
┌─────────────────────────────────────────┐
│ L1: Deterministic Rules (YAML)          │
│ • 70-80% of incidents                   │
│ • <100ms response                       │
│ • $0 cost                               │
└──────┬──────────────────────────────────┘
       │ No match
       ▼
┌─────────────────────────────────────────┐
│ L2: LLM Planner                         │
│ • 15-20% of incidents                   │
│ • 2-5s response                         │
│ • ~$0.001/call                          │
└──────┬──────────────────────────────────┘
       │ Uncertain/Risky
       ▼
┌─────────────────────────────────────────┐
│ L3: Human Escalation                    │
│ • 5-10% of incidents                    │
│ • Ticket created                        │
│ • SLA tracking                          │
└─────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│ Data Flywheel                           │
│ • Tracks L2 patterns                    │
│ • Promotes to L1 rules                  │
│ • Continuous improvement                │
└─────────────────────────────────────────┘
```

## Client Flake Configuration

```nix
# client-flake/flake.nix
{
  description = "MSP Client Station";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.05";
  };

  outputs = { self, nixpkgs }:
  let
    mcpServerUrl = "https://mcp.your-msp.com";
    eventQueueUrl = "redis://queue.your-msp.com:6379";
  in {
    nixosConfigurations.msp-client-base = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        ./modules/log-watcher.nix
        ./modules/health-checks.nix
        ./modules/remediation-tools.nix
        {
          networking.firewall.enable = true;

          systemd.services.msp-watcher = {
            description = "MSP Log Watcher";
            after = [ "network.target" ];
            wantedBy = [ "multi-user.target" ];
            serviceConfig = {
              ExecStart = "${self.packages.x86_64-linux.watcher}/bin/watcher";
              Restart = "always";
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
}
```

## MCP Server Structure

```python
# mcp-server/server.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, validator

app = FastAPI()

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
        allowed_tools = ['restart_service', 'clear_cache',
                        'rotate_logs', 'delete_tmp', 'renew_cert']
        if v not in allowed_tools:
            raise ValueError(f'Tool must be one of {allowed_tools}')
        return v

@app.post("/chat")
async def process_incident(incident: IncidentRequest):
    """Main endpoint: receives incident, calls LLM, executes tool"""
    # Rate limit check
    # Call LLM for decision
    # Execute tool with guardrails
    # Return result
```

## Service Catalog

### ✅ In Scope (Infra-Only)

| Layer | Automations |
|-------|-------------|
| OS & services | Restart systemd unit, rotate logs, clear /tmp, renew certs |
| Middleware | Bounce workers, re-index database, clear cache |
| Patching | Apply security updates, reboot off-peak, verify health |
| Network | Flush firewall state, reload BGP, fail over link |
| Observability | Detect pattern, run approved fix, generate evidence |

### ❌ Out of Scope

- End-user devices (laptops, printers)
- SaaS & desktop apps (QuickBooks, Outlook)
- Tier-1 ticket triage ("my mouse is frozen")
- Compliance paperwork (SOC-2 docs, staff training)

## Guardrails & Safety

1. **Validation** - Reject unknown service names
2. **Rate limit** - 5-minute cooldown per host/tool
3. **Dry-run flag** - For high-risk scripts
4. **Fallback** - If incident repeats twice in 15 min, page human
5. **Audit log** - Append every tool call to tamper-evident file

## Key Differentiators

1. Evidence-by-architecture (MCP audit trail inseparable from operations)
2. Deterministic builds (NixOS flakes = cryptographic proof)
3. LLM-driven synthetic testing
4. Metadata-only monitoring (no PHI processing)
5. Auditor-ready evidence packets
6. Enforcement-first dashboards
7. Signed evidence bundles
8. Auto-generated executive reporting
