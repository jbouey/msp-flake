# MSP HIPAA Compliance Platform - Complete Technology Stack

**Last Updated:** 2026-01-01
**Version:** Phase 10 - Production Deployment Complete
**Test Status:** 169 tests passing

---

## Executive Summary

HIPAA compliance automation platform for small healthcare practices (1-50 providers). Autonomous infrastructure healing + compliance documentation replaces traditional MSPs at 75% lower cost.

| Metric | Value |
|--------|-------|
| **Target Market** | Healthcare SMBs, NEPA region |
| **Pricing** | $200-3000/month |
| **Resolution Time** | 2-10 minutes (vs hours with traditional MSP) |
| **Auto-Heal Rate** | 85-95% of incidents |

---

## System Architecture

```
                                    INTERNET
                                       │
                 ┌─────────────────────┼─────────────────────┐
                 │                     │                     │
                 ▼                     ▼                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    CENTRAL COMMAND (Hetzner VPS)                         │
│                    178.156.162.116                                       │
│                                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐  │
│  │   Caddy      │  │  Dashboard   │  │  MCP Server  │  │  PostgreSQL │  │
│  │  (Reverse    │  │  React SPA   │  │  FastAPI     │  │  + Redis    │  │
│  │   Proxy)     │  │  :3000       │  │  :8000       │  │  + MinIO    │  │
│  │  :443        │  │              │  │              │  │             │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────┘  │
│                                                                          │
│  Domains:                                                                │
│  - api.osiriscare.net          → MCP Server                             │
│  - dashboard.osiriscare.net    → React Dashboard                        │
│  - msp.osiriscare.net          → Dashboard (alias)                      │
└─────────────────────────────────────────────────────────────────────────┘
                                       ▲
                                       │ mTLS/HTTPS (pull-only)
                                       │
┌──────────────────────────────────────┼──────────────────────────────────┐
│                                      │                                   │
│   ┌──────────────────────────────────┴──────────────────────────────┐   │
│   │                   COMPLIANCE APPLIANCE (NixOS)                   │   │
│   │                                                                  │   │
│   │  ┌────────────────────────────────────────────────────────────┐ │   │
│   │  │              Three-Tier Auto-Healer                        │ │   │
│   │  │                                                            │ │   │
│   │  │   L1 Deterministic    L2 LLM Planner    L3 Human           │ │   │
│   │  │   70-80%, <100ms      15-20%, 2-5s      5-10%              │ │   │
│   │  │   YAML rules          Context-aware     Rich tickets       │ │   │
│   │  │                                                            │ │   │
│   │  │   ┌─────────────────────────────────────────────────────┐  │ │   │
│   │  │   │           Learning Loop (Data Flywheel)             │  │ │   │
│   │  │   │           Auto-promotes L2→L1 at 90%+ success       │  │ │   │
│   │  │   └─────────────────────────────────────────────────────┘  │ │   │
│   │  └────────────────────────────────────────────────────────────┘ │   │
│   │                              │                                   │   │
│   │  ┌────────────┬──────────────┼──────────────┬────────────────┐  │   │
│   │  │   drift    │   healing    │   evidence   │   mcp_client   │  │   │
│   │  │   .py      │   .py        │   .py        │   .py          │  │   │
│   │  └────────────┴──────────────┴──────────────┴────────────────┘  │   │
│   │                              │                                   │   │
│   │  ┌───────────────────────────┴───────────────────────────────┐  │   │
│   │  │            Windows Runbooks (WinRM)                        │  │   │
│   │  │            7 HIPAA compliance runbooks                     │  │   │
│   │  └────────────────────────────────────────────────────────────┘  │   │
│   └──────────────────────────────────────────────────────────────────┘   │
│                                      │                                   │
│                                      │ WinRM (5985)                      │
│                                      ▼                                   │
│   ┌──────────────────────────────────────────────────────────────────┐   │
│   │                    CUSTOMER WINDOWS SERVERS                       │   │
│   │                    (Domain Controllers, File Servers, etc.)       │   │
│   └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│                            CUSTOMER SITE                                 │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Infrastructure Components

### 1. Central Command (Production)

**Server:** Hetzner VPS (178.156.162.116)

| Service | Port | Technology | Purpose |
|---------|------|------------|---------|
| **Caddy** | 443 | Caddy 2.x | Reverse proxy, auto TLS |
| **MCP Server** | 8000 | FastAPI/Python | REST API, agent sync |
| **Dashboard** | 3000 | React/Vite | Central Command UI |
| **PostgreSQL** | 5432 | PostgreSQL 16 | Sites, patterns, incidents |
| **Redis** | 6379 | Redis 7 | Rate limiting, caching |
| **MinIO** | 9000/9001 | MinIO | WORM evidence storage |

**DNS Configuration:**
```
api.osiriscare.net       A    178.156.162.116
dashboard.osiriscare.net A    178.156.162.116
msp.osiriscare.net       A    178.156.162.116
```

**Access:**
```bash
# SSH
ssh root@178.156.162.116

# Docker services
cd /opt/mcp-server && docker compose ps
cd /opt/mcp-server && docker compose logs -f mcp-server

# API health
curl https://api.osiriscare.net/health
```

**Dashboard Credentials:**
| Role | Username | Password |
|------|----------|----------|
| Admin | admin | admin |
| Operator | operator | operator |

---

### 2. Compliance Agent (NixOS Appliance)

**OS:** NixOS 24.05
**Language:** Python 3.13

| Module | Lines | Purpose |
|--------|-------|---------|
| `agent.py` | 450 | Main orchestration loop |
| `drift.py` | 630 | 6 compliance checks |
| `healing.py` | 890 | Self-healing engine |
| `auto_healer.py` | 520 | Three-tier orchestrator |
| `level1_deterministic.py` | 380 | YAML rules engine |
| `level2_llm.py` | 410 | LLM context planner |
| `level3_escalation.py` | 290 | Human escalation |
| `incident_db.py` | 350 | SQLite incident tracking |
| `learning_loop.py` | 420 | Data flywheel |
| `evidence.py` | 400 | Bundle generation |
| `crypto.py` | 340 | Ed25519 signing |
| `mcp_client.py` | 450 | MCP communication |
| `offline_queue.py` | 440 | SQLite WAL queue |
| `phi_scrubber.py` | 180 | PHI pattern removal |
| `windows_collector.py` | 350 | Windows data collection |

**Python Dependencies:**
```
pydantic>=2.5.0          # Configuration, data models
pyyaml>=6.0.1            # YAML parsing
aiohttp>=3.9.0           # Async HTTP
cryptography>=42.0.0     # Ed25519 signing
pywinrm>=0.4.3           # Windows remote management
```

---

### 3. Development/Test Lab

**Host:** Local iMac (192.168.88.50)
**Hypervisor:** VirtualBox 7.x

```
                         ┌─────────────────┐
                         │    INTERNET     │
                         └────────┬────────┘
                                  │
                         ┌────────▼────────┐
                         │  Router/Gateway │
                         │ 174.178.63.139  │
                         │ (External IP)   │
                         └────────┬────────┘
                                  │
                         ┌────────▼────────┐
                         │  Local Network  │
                         │ 192.168.88.0/24 │
                         └────────┬────────┘
                                  │
            ┌─────────────────────┼─────────────────────┐
            │                     │                     │
   ┌────────▼────────┐   ┌───────▼────────┐            │
   │  MacBook Pro    │   │  iMac (Lab)    │            │
   │  (Dev Machine)  │   │  192.168.88.50 │            │
   │                 │   │  VirtualBox    │            │
   └─────────────────┘   └───────┬────────┘            │
                                 │                     │
                         ┌───────▼────────┐            │
                         │  NVDC01        │            │
                         │  192.168.88.250│            │
                         │  northvalley   │            │
                         │  .local        │            │
                         └────────────────┘            │
```

#### iMac Lab Host

| Property | Value |
|----------|-------|
| **IP** | 192.168.88.50 |
| **CPU** | Quad-Core i5 @ 3.5 GHz |
| **RAM** | 28 GB |
| **Disk** | 924 GB (454 GB free) |
| **Username** | jrelly |

**Access:**
```bash
ssh jrelly@192.168.88.50
```

#### North Valley Clinic - Windows AD DC

| Property | Value |
|----------|-------|
| **VM Name** | northvalley-dc |
| **Hostname** | NVDC01 |
| **OS** | Windows Server 2019 Standard |
| **Domain** | northvalley.local |
| **NetBIOS** | NORTHVALLEY |
| **IP** | 192.168.88.250 |
| **WinRM** | http://192.168.88.250:5985 |
| **RAM** | 4 GB |
| **CPU** | 2 cores |
| **Disk** | 60 GB |

**Credentials:**
| Account | Username | Password |
|---------|----------|----------|
| Domain Admin | NORTHVALLEY\Administrator | NorthValley2024! |
| DSRM | (Safe Mode) | NorthValley2024! |

**WinRM Access:**
```python
import winrm
s = winrm.Session('http://192.168.88.250:5985/wsman',
                  auth=('NORTHVALLEY\\Administrator', 'NorthValley2024!'),
                  transport='ntlm')
print(s.run_ps('hostname').std_out.decode())
```

**VM Management:**
```bash
# Check status
ssh jrelly@192.168.88.50 '/Applications/VirtualBox.app/Contents/MacOS/VBoxManage list runningvms'

# Start headless
ssh jrelly@192.168.88.50 '/Applications/VirtualBox.app/Contents/MacOS/VBoxManage startvm "northvalley-dc" --type headless'

# Graceful shutdown
ssh jrelly@192.168.88.50 '/Applications/VirtualBox.app/Contents/MacOS/VBoxManage controlvm "northvalley-dc" acpipowerbutton'
```

---

## Technology Stack Summary

### Languages & Runtimes

| Technology | Version | Purpose |
|------------|---------|---------|
| **Python** | 3.13 | Agent, MCP Server |
| **TypeScript** | 5.x | Dashboard frontend |
| **Nix** | 2.x | Declarative system config |
| **PowerShell** | 5.1+ | Windows runbooks |

### Frameworks & Libraries

| Technology | Purpose |
|------------|---------|
| **FastAPI** | MCP Server REST API |
| **React** | Dashboard SPA |
| **Vite** | Frontend build tool |
| **Pydantic** | Data validation |
| **pywinrm** | Windows remote management |
| **aiohttp** | Async HTTP client |

### Databases & Storage

| Technology | Purpose |
|------------|---------|
| **PostgreSQL 16** | Central Command database |
| **Redis 7** | Rate limiting, caching |
| **SQLite (WAL)** | Agent offline queue, incident DB |
| **MinIO** | WORM evidence storage |

### Infrastructure

| Technology | Purpose |
|------------|---------|
| **NixOS 24.05** | Compliance appliance OS |
| **Docker** | Central Command containers |
| **Caddy** | Reverse proxy, auto TLS |
| **VirtualBox** | Development lab VMs |
| **Windows Server 2019** | Test AD environment |

### Security

| Technology | Purpose |
|------------|---------|
| **Ed25519** | Order/evidence signing |
| **TLS 1.3** | Transport encryption |
| **mTLS** | Agent authentication |
| **NTLM/Kerberos** | Windows authentication |

---

## HIPAA Compliance Checks

| Check | HIPAA Control | Module |
|-------|---------------|--------|
| **Patching** | 164.308(a)(5)(ii)(B) | `drift.py` |
| **AV/EDR** | 164.308(a)(5)(ii)(B) | `drift.py` |
| **Backup** | 164.308(a)(7)(ii)(A) | `drift.py` |
| **Logging** | 164.312(b) | `drift.py` |
| **Firewall** | 164.312(a)(1) | `drift.py` |
| **Encryption** | 164.312(a)(2)(iv) | `drift.py` |

### Windows Runbooks

| Runbook ID | Name | Disruptive |
|------------|------|------------|
| RB-WIN-PATCH-001 | Windows Patch Compliance | Yes |
| RB-WIN-AV-001 | Windows Defender Health | No |
| RB-WIN-BACKUP-001 | Backup Verification | No |
| RB-WIN-LOGGING-001 | Windows Event Logging | No |
| RB-WIN-FIREWALL-001 | Windows Firewall Status | No |
| RB-WIN-ENCRYPTION-001 | BitLocker Encryption | Yes |
| RB-WIN-AD-001 | Active Directory Health | No |

---

## API Endpoints

### Central Command (api.osiriscare.net)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check |
| `/api/sites` | GET/POST | Site management |
| `/api/sites/{site_id}` | GET/PUT | Site details |
| `/api/appliances/checkin` | POST | Agent phone-home |
| `/api/sites/{site_id}/credentials` | POST | Store credentials |
| `/api/dashboard/fleet` | GET | Fleet overview |
| `/api/dashboard/incidents` | GET | Active incidents |
| `/api/learning/status` | GET | Learning loop status |
| `/api/learning/candidates` | GET | Promotion candidates |
| `/runbooks` | GET | List runbooks |
| `/stats` | GET | Server statistics |

---

## Three-Tier Auto-Healing

```
        Incident
            │
            ▼
    ┌───────────────┐
    │   Level 1     │  70-80% of incidents
    │ Deterministic │  <100ms, $0 cost
    │    Rules      │  YAML pattern matching
    └───────┬───────┘
            │ No match
            ▼
    ┌───────────────┐
    │   Level 2     │  15-20% of incidents
    │  LLM Planner  │  2-5s, context-aware
    │   (Hybrid)    │  Local + API fallback
    └───────┬───────┘
            │ Can't resolve
            ▼
    ┌───────────────┐
    │   Level 3     │  5-10% of incidents
    │    Human      │  Rich tickets
    │  Escalation   │  Slack/PagerDuty/Email
    └───────────────┘
            │
            ▼
    ┌───────────────┐
    │ Learning Loop │  Data Flywheel
    │ L2 → L1       │  Auto-promote patterns
    │  Promotion    │  with 90%+ success
    └───────────────┘
```

---

## Security Guardrails

| # | Guardrail | Status | Implementation |
|---|-----------|--------|----------------|
| 1 | Order authentication | ✅ | Ed25519 signatures |
| 2 | Order TTL | ✅ | 15-minute expiration |
| 3 | Maintenance window | ✅ | Disruptive action scheduling |
| 4 | Health check + rollback | ✅ | Post-action verification |
| 5 | Evidence generation | ✅ | Signed JSON bundles |
| 6 | Rate limiting | ✅ | Redis-based (10 req/5min/site) |
| 7 | Runbook whitelisting | ✅ | Only approved runbooks |
| 8 | Dry-run mode | ✅ | Preview without execution |
| 9 | Queue durability | ✅ | SQLite WAL + fsync |
| 10 | mTLS | ✅ | Client cert authentication |

---

## Test Infrastructure

```
tests/
├── test_agent.py                  # 15 tests - Agent lifecycle
├── test_auto_healer.py            # 24 tests - Three-tier auto-healer
├── test_auto_healer_integration.py # 12 tests - Multi-VM scenarios
├── test_healing.py                # 22 tests - Self-healing
├── test_drift.py                  # 25 tests - Drift detection
├── test_queue.py                  # 20 tests - Offline queue
├── test_crypto.py                 # 8 tests - Cryptography
├── test_evidence.py               # 11 tests - Evidence bundles
├── test_mcp_client.py             # 12 tests - MCP communication
├── test_utils.py                  # 9 tests - Utilities
├── test_windows_integration.py    # 3 tests - Live Windows VM
└── conftest.py                    # Shared fixtures
```

**Total: 169 tests passing**

**Run tests:**
```bash
cd /Users/dad/Documents/Msp_Flakes/packages/compliance-agent
source venv/bin/activate
python -m pytest tests/ -v --tb=short
```

---

## Directory Structure

```
/Users/dad/Documents/Msp_Flakes/
├── .agent/                        # Session tracking, context
│   ├── CONTEXT.md                 # Current state
│   ├── NETWORK.md                 # Network topology
│   ├── TODO.md                    # Tasks
│   └── sessions/                  # Session logs
├── packages/
│   └── compliance-agent/          # Python agent
│       ├── src/compliance_agent/  # Source modules
│       ├── tests/                 # pytest tests
│       ├── docs/                  # Agent docs
│       └── venv/                  # Python virtualenv
├── mcp-server/                    # MCP Server source
│   ├── server.py                  # FastAPI server
│   ├── central-command/           # React dashboard
│   └── runbooks/                  # Runbook definitions
├── modules/                       # NixOS modules
├── iso/                           # Appliance ISO build
├── docs/                          # Project documentation
├── deploy/                        # Deployment scripts
└── flake-compliance.nix           # Nix flake
```

---

## Quick Reference Commands

```bash
# === Development ===
cd /Users/dad/Documents/Msp_Flakes/packages/compliance-agent
source venv/bin/activate
python -m pytest tests/ -v --tb=short

# === Central Command (Production) ===
ssh root@178.156.162.116
curl https://api.osiriscare.net/health
open https://dashboard.osiriscare.net

# === Lab VMs ===
ssh jrelly@192.168.88.50
ping 192.168.88.250                # Windows DC

# === Windows DC (WinRM) ===
python3 -c "
import winrm
s = winrm.Session('http://192.168.88.250:5985/wsman',
                  auth=('NORTHVALLEY\\\\Administrator', 'NorthValley2024!'),
                  transport='ntlm')
print(s.run_ps('hostname').std_out.decode())
"
```

---

## Related Documentation

| Document | Purpose |
|----------|---------|
| `.agent/CONTEXT.md` | Current project state |
| `.agent/NETWORK.md` | Network topology, VM inventory |
| `.agent/TODO.md` | Current tasks |
| `docs/ARCHITECTURE.md` | System design |
| `docs/HIPAA_FRAMEWORK.md` | Compliance details |
| `IMPLEMENTATION-STATUS.md` | Phase tracking |

---

**Maintained by:** MSP Automation Team
**Last Build:** Phase 10 Production
