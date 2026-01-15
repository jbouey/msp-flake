# MSP Platform Architecture

**Last Updated:** 2026-01-15 (Session 40 - Go Agent Implementation)

## Overview

**Stack:** NixOS + MCP + LLM
**Target:** Small to mid-sized clinics (NEPA region)
**Service Model:** Auto-heal infrastructure + HIPAA compliance monitoring

## Production Infrastructure

```
                         ┌─────────────────┐
                         │    INTERNET     │
                         └────────┬────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        │                         │                         │
        ▼                         ▼                         ▼
┌───────────────┐       ┌─────────────────┐       ┌─────────────────┐
│   Clients     │       │  Central        │       │   Operators     │
│  (Appliances) │       │  Command        │       │  (Dashboard)    │
└───────┬───────┘       │  178.156.162.116│       └────────┬────────┘
        │               └────────┬────────┘                │
        │                        │                         │
        │    ┌───────────────────┼───────────────────┐     │
        │    │                   │                   │     │
        ▼    ▼                   ▼                   ▼     ▼
┌─────────────────────────────────────────────────────────────┐
│                    Caddy Reverse Proxy                      │
│                    (Auto TLS via Let's Encrypt)             │
└─────────────────────────────────────────────────────────────┘
        │                        │                        │
        ▼                        ▼                        ▼
┌───────────────┐       ┌───────────────┐       ┌───────────────┐
│ api.osiris    │       │ dashboard.    │       │ msp.osiris    │
│ care.net      │       │ osiriscare.net│       │ care.net      │
│ :8000         │       │ :3000         │       │ :3000         │
└───────────────┘       └───────────────┘       └───────────────┘
        │                        │                        │
        └────────────────────────┼────────────────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        │                        │                        │
        ▼                        ▼                        ▼
┌───────────────┐       ┌───────────────┐       ┌───────────────┐
│  PostgreSQL   │       │    Redis      │       │    MinIO      │
│  :5432        │       │    :6379      │       │  :9000-9001   │
│  (Sites, etc) │       │  (Cache)      │       │  (Evidence)   │
└───────────────┘       └───────────────┘       └───────────────┘
```

### Production Endpoints

| Service | URL | Purpose |
|---------|-----|---------|
| API | https://api.osiriscare.net | MCP API, phone-home, sites |
| Dashboard | https://dashboard.osiriscare.net | Central Command UI |
| Alternate | https://msp.osiriscare.net | Dashboard alias |

### Appliance Phone-Home Flow

```
┌─────────────────┐     Every 60s      ┌─────────────────┐
│  Client Site    │ ──────────────────▶│  Central        │
│  NixOS Appliance│   POST /checkin    │  Command        │
└─────────────────┘                    └────────┬────────┘
                                                │
                                       ┌────────▼────────┐
                                       │  Updates:       │
                                       │  - last_checkin │
                                       │  - live_status  │
                                       │  - appliance    │
                                       │    metadata     │
                                       └─────────────────┘
```

**Status Calculation:**
- `online`: Last checkin < 5 minutes
- `stale`: Last checkin 5-15 minutes
- `offline`: Last checkin > 15 minutes
- `pending`: Never checked in

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
│   ├── central-command/     # Dashboard & API
│   │   ├── backend/         # FastAPI (Python)
│   │   └── frontend/        # React + Vite
│   ├── app/                 # Production Docker app
│   │   ├── main.py          # FastAPI application
│   │   └── dashboard_api/   # Sites, phone-home, etc.
│   ├── docker-compose.yml   # Production stack
│   └── Caddyfile            # Reverse proxy config
├── terraform/               # Infrastructure as Code
│   ├── modules/
│   └── clients/
└── docs/                    # This directory
```

## Partner/Reseller Infrastructure

Partners (MSPs) can white-label the platform and provision their own clients:

```
┌─────────────────┐     Create       ┌─────────────────┐
│  Partner        │ ───────────────▶ │  Provision      │
│  Dashboard      │   Provision Code │  Code + QR      │
└─────────────────┘                  └────────┬────────┘
                                              │
                                              ▼
┌─────────────────┐     Scan QR      ┌─────────────────┐
│  Physical       │ ───────────────▶ │  POST           │
│  Appliance      │   or enter code  │  /api/provision │
│  (First Boot)   │                  │  /claim         │
└─────────────────┘                  └────────┬────────┘
                                              │
                                              ▼
                                     ┌─────────────────┐
                                     │  Creates:       │
                                     │  - Site record  │
                                     │  - Appliance ID │
                                     │  - config.yaml  │
                                     └─────────────────┘
```

### Partner API Modules

| Module | Purpose |
|--------|---------|
| `partners.py` | Partner management, QR code generation |
| `discovery.py` | Network discovery, asset classification |
| `provisioning.py` | Appliance first-boot provisioning |
| `portal.py` | Client portal with magic link auth |

See [Partner Documentation](partner/README.md) for complete API reference.

## Role-Based Access Control (RBAC)

Central Command uses a three-tier permission system:

| Role | Dashboard | Execute Actions | Manage Users | Audit Logs |
|------|-----------|-----------------|--------------|------------|
| Admin | Full | Full | Yes | Yes |
| Operator | Full | Yes | No | No |
| Readonly | Full | No | No | No |

### User Management Flow

```
┌─────────────────┐     Send Invite     ┌─────────────────┐
│  Admin          │ ────────────────▶   │  Email Service  │
│  (Users Page)   │                     │  (SMTP)         │
└─────────────────┘                     └────────┬────────┘
                                                 │
                                                 ▼
┌─────────────────┐     Click Link      ┌─────────────────┐
│  New User       │ ◀────────────────── │  Invite Email   │
│  (SetPassword)  │                     │  with Token     │
└────────┬────────┘                     └─────────────────┘
         │
         ▼
┌─────────────────┐
│  Set Password   │ ──▶ Account Created
│  Page           │
└─────────────────┘
```

### User Management API

| Endpoint | Method | Access | Description |
|----------|--------|--------|-------------|
| `/api/users` | GET | Admin | List all users |
| `/api/users/invite` | POST | Admin | Send invite email |
| `/api/users/invites` | GET | Admin | List pending invites |
| `/api/users/{id}` | PUT | Admin | Update user role/status |
| `/api/users/me` | GET | Any | Get current user profile |
| `/api/users/me/password` | POST | Any | Change own password |
| `/api/users/invite/validate/{token}` | GET | Public | Validate invite token |
| `/api/users/invite/accept` | POST | Public | Accept invite + set password |

## Technical Stack

| Component | Technology | Purpose |
|-----------|------------|---------|
| Infrastructure | NixOS Flakes | Deterministic, auditable configuration |
| Agent | Python 3.13 | Drift detection, healing, evidence |
| Communication | MCP Protocol | Structured LLM-to-tool interface |
| LLM | GPT-4o / Claude | Incident triage, runbook selection |
| Queue | Redis Streams | Multi-tenant event durability |
| Evidence | WORM S3/MinIO | Tamper-evident storage |
| Signing | Ed25519 | Cryptographic evidence bundles |
| Dashboard | React + Vite | Central Command UI |
| API | FastAPI | REST endpoints, phone-home |
| Database | PostgreSQL 16 | Sites, incidents, evidence metadata |
| Reverse Proxy | Caddy | Auto TLS, HTTPS termination |
| Hosting | Hetzner VPS | Production infrastructure |

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
│ • Tracks L2 patterns via /agent/patterns│
│ • Promotes to L1 rules (5+ occurrences) │
│ • Success rate tracking (90%+ required) │
│ • Continuous improvement                │
└─────────────────────────────────────────┘
```

## Go Agent for Workstation-Scale Compliance

Session 40 introduced the Go Agent architecture to solve the WinRM polling scalability problem (25-50+ workstations per site).

### Push-Based Architecture

```
Windows Workstations          NixOS Appliance            Central Command
┌─────────────────┐          ┌─────────────────────┐     ┌───────────────┐
│  Go Agent       │  gRPC    │  Python Agent       │HTTPS│               │
│  (10MB .exe)    │─────────▶│  - gRPC Server      │────▶│  Dashboard    │
│                 │  :50051  │  - Sensor API :8080 │     │  API          │
│  6 WMI Checks:  │          │  - Three-tier heal  │     │               │
│  - BitLocker    │          └─────────────────────┘     └───────────────┘
│  - Defender     │
│  - Firewall     │          Offline Queue:
│  - Patches      │          SQLite WAL for network
│  - ScreenLock   │          resilience (queues events
│  - Services     │          when appliance unreachable)
│                 │
│  RMM Detection: │
│  - ConnectWise  │
│  - Datto        │
│  - NinjaRMM     │
└─────────────────┘
```

### Capability Tiers (Server-Controlled)

| Tier | Value | Description | Use Case |
|------|-------|-------------|----------|
| MONITOR_ONLY | 0 | Reports drift only | MSP-deployed (default) |
| SELF_HEAL | 1 | Can fix drift locally | Direct clients (opt-in) |
| FULL_REMEDIATION | 2 | Full automation | Trusted environments |

### Go Agent Files

| Component | Location | Purpose |
|-----------|----------|---------|
| Protocol | `agent/proto/compliance.proto` | gRPC service definition |
| Entry Point | `agent/cmd/osiris-agent/main.go` | Flag parsing, config loading |
| Checks | `agent/internal/checks/*.go` | 6 compliance checks |
| Transport | `agent/internal/transport/grpc.go` | gRPC client with mTLS |
| Offline Queue | `agent/internal/transport/offline.go` | SQLite WAL queue |
| RMM Detection | `agent/internal/checks/rmm.go` | Detect and report RMM tools |

### Central Command Integration

The frontend provides a Go Agents dashboard at `/sites/:siteId/agents`:

| Feature | Description |
|---------|-------------|
| Fleet Summary | Compliance %, active/offline counts, tier distribution |
| Agent List | Expandable rows with check results |
| Tier Control | Dropdown to change capability tier |
| RMM Detection | Shows detected RMM tools per agent |
| Actions | Run Check, Remove Agent |

### Database Schema

```sql
-- Go agents table
go_agents (agent_id, site_id, hostname, capability_tier, status, ...)

-- Check results
go_agent_checks (agent_id, check_type, status, hipaa_control, ...)

-- Site summaries (auto-updated via trigger)
site_go_agent_summaries (site_id, total_agents, active_agents, ...)

-- Command queue
go_agent_orders (order_id, agent_id, order_type, status, ...)
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
