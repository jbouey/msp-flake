# MSP Compliance Agent - Technology Stack

**Last Updated:** 2025-11-23
**Version:** Phase 2 Complete - Three-Tier Auto-Healing
**Test Status:** 161 passed, 7 skipped

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        MSP Compliance Platform                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                     Compliance Agent (NixOS)                         │   │
│  │                                                                       │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │   │
│  │  │  agent   │ │  drift   │ │ healing  │ │ evidence │ │  models  │  │   │
│  │  │  .py     │ │  .py     │ │  .py     │ │  .py     │ │  .py     │  │   │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────────┘  │   │
│  │       │            │            │            │                      │   │
│  │  ┌────▼────────────▼────────────▼────────────▼─────┐               │   │
│  │  │           Three-Tier Auto-Healer                 │               │   │
│  │  │  ┌────────────┐ ┌────────────┐ ┌─────────────┐  │               │   │
│  │  │  │    L1      │ │    L2      │ │     L3      │  │               │   │
│  │  │  │Deterministic│ │LLM Planner│ │  Escalation │  │               │   │
│  │  │  └──────┬─────┘ └─────┬──────┘ └──────┬──────┘  │               │   │
│  │  │         │             │               │          │               │   │
│  │  │  ┌──────▼─────────────▼───────────────▼──────┐  │               │   │
│  │  │  │    Learning Loop (Data Flywheel)          │  │               │   │
│  │  │  │    incident_db.py │ learning_loop.py      │  │               │   │
│  │  │  └───────────────────────────────────────────┘  │               │   │
│  │  └─────────────────────────────────────────────────┘               │   │
│  │                          │                                          │   │
│  │  ┌───────────────────────▼───────────────────────────────────────┐ │   │
│  │  │                Core Services                                   │ │   │
│  │  │  mcp_client │ crypto │ offline_queue │ config                 │ │   │
│  │  └───────────────────────────────────────────────────────────────┘ │   │
│  │                          │                                          │   │
│  │  ┌───────────────────────▼───────────────────────────────────────┐ │   │
│  │  │              Windows Runbooks (WinRM)                          │ │   │
│  │  │  executor.py │ runbooks.py (7 HIPAA runbooks)                 │ │   │
│  │  └───────────────────────────────────────────────────────────────┘ │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                              │                                              │
│               ┌──────────────┼──────────────┐                              │
│               ▼              ▼              ▼                              │
│         ┌──────────┐  ┌──────────┐  ┌──────────┐                          │
│         │   MCP    │  │  Redis   │  │  WORM    │                          │
│         │  Server  │  │  Queue   │  │ Storage  │                          │
│         └──────────┘  └──────────┘  └──────────┘                          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Three-Tier Auto-Healing Architecture

The agent implements an intelligent incident resolution system with automatic learning:

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

### Resolution Levels

| Level | Handles | Response Time | Cost | Implementation |
|-------|---------|---------------|------|----------------|
| **L1 Deterministic** | 70-80% | <100ms | $0 | `level1_deterministic.py` |
| **L2 LLM Planner** | 15-20% | 2-5s | ~$0.001/incident | `level2_llm.py` |
| **L3 Human** | 5-10% | Minutes-hours | Human time | `level3_escalation.py` |

### Data Flywheel

The self-learning system (`learning_loop.py`) continuously improves:

1. **Track** - All L2 decisions and outcomes stored in `incident_db.py`
2. **Analyze** - Identify patterns with 90%+ success rate
3. **Promote** - Automatically generate L1 rules from successful L2 patterns
4. **Improve** - Reduce latency and cost over time

---

## Core Technology Stack

### Languages & Runtimes

| Technology | Version | Purpose |
|------------|---------|---------|
| **Python** | 3.13.3 | Primary agent language |
| **NixOS** | 24.05+ | Host operating system |
| **PowerShell** | 5.1+ | Windows runbook execution |
| **Nix** | 2.x | Package management & declarative config |

### Python Dependencies

```
# Core
pydantic>=2.5.0          # Configuration validation, data models
pyyaml>=6.0.1            # YAML configuration parsing
aiohttp>=3.9.0           # Async HTTP client for MCP

# Cryptography
cryptography>=42.0.0     # Ed25519 signing, TLS
nacl                     # Ed25519 signature verification

# Testing
pytest>=8.0              # Test framework
pytest-asyncio>=1.0      # Async test support
pytest-cov>=7.0          # Coverage reporting

# Windows Integration
pywinrm>=0.4.3           # WinRM client for Windows servers

# Database
sqlite3 (stdlib)         # Offline queue persistence (WAL mode)
```

### Infrastructure Services

| Service | Port | Purpose |
|---------|------|---------|
| **MCP Server** | 8000 | Central control plane (FastAPI) |
| **Redis** | 6379 | Event queue, rate limiting |
| **SQLite** | - | Local offline queue (WAL mode) |
| **WinRM** | 5985 | Windows remote management |

---

## Module Reference

### Three-Tier Auto-Healer Modules

| Module | Description |
|--------|-------------|
| `auto_healer.py` | Orchestrator for L1/L2/L3 resolution |
| `level1_deterministic.py` | YAML-based deterministic rules engine |
| `level2_llm.py` | LLM context-aware planner (local/API/hybrid) |
| `level3_escalation.py` | Human escalation with rich tickets |
| `incident_db.py` | SQLite incident history with pattern tracking |
| `learning_loop.py` | Self-learning system for L2→L1 promotion |

### Core Compliance Agent Modules

| Module | Description |
|--------|-------------|
| `agent.py` | Main orchestration loop with graceful shutdown |
| `healing.py` | Self-healing engine with 6 remediation actions |
| `drift.py` | Drift detection (6 compliance checks) |
| `evidence.py` | Evidence bundle generation & signing |
| `mcp_client.py` | MCP server communication (mTLS) |
| `offline_queue.py` | SQLite WAL queue for offline operation |
| `crypto.py` | Ed25519 signature verification |
| `config.py` | Configuration management (27 options) |
| `models.py` | Pydantic data models |
| `utils.py` | Utility functions |

### Windows Runbooks

| Module | Description |
|--------|-------------|
| `runbooks/windows/executor.py` | WinRM execution engine |
| `runbooks/windows/runbooks.py` | 7 Windows compliance runbooks |

---

## Compliance Checks (Drift Detection)

| Check | HIPAA Control | Description |
|-------|---------------|-------------|
| **Patching** | 164.308(a)(5)(ii)(B) | OS/software patch status |
| **AV/EDR** | 164.308(a)(5)(ii)(B) | Antivirus/endpoint protection |
| **Backup** | 164.308(a)(7)(ii)(A) | Backup completion & age |
| **Logging** | 164.312(b) | Audit logging status |
| **Firewall** | 164.312(a)(1) | Firewall configuration |
| **Encryption** | 164.312(a)(2)(iv) | Disk/data encryption |

---

## Windows Runbooks

| Runbook ID | Name | HIPAA Control | Disruptive |
|------------|------|---------------|------------|
| `RB-WIN-PATCH-001` | Windows Patch Compliance | 164.308(a)(5)(ii)(B) | Yes |
| `RB-WIN-AV-001` | Windows Defender Health | 164.308(a)(5)(ii)(B), 164.312(b) | No |
| `RB-WIN-BACKUP-001` | Backup Verification | 164.308(a)(7)(ii)(A), 164.310(d)(2)(iv) | No |
| `RB-WIN-LOGGING-001` | Windows Event Logging | 164.312(b), 164.308(a)(1)(ii)(D) | No |
| `RB-WIN-FIREWALL-001` | Windows Firewall Status | 164.312(a)(1), 164.312(e)(1) | No |
| `RB-WIN-ENCRYPTION-001` | BitLocker Encryption | 164.312(a)(2)(iv), 164.312(e)(2)(ii) | Yes |
| `RB-WIN-AD-001` | Active Directory Health | 164.312(a)(1), 164.308(a)(3)(ii)(C) | No |

---

## Security Architecture

### Guardrails Implemented

| # | Guardrail | Status | Implementation |
|---|-----------|--------|----------------|
| 1 | Order authentication | ✅ | Ed25519 signature verification |
| 2 | Order TTL | ✅ | 15-minute default expiration |
| 3 | Maintenance window | ✅ | Disruptive actions only in window |
| 4 | Health check + rollback | ✅ | Post-action verification |
| 5 | Evidence generation | ✅ | JSON bundles + signatures |
| 6 | Rate limiting | ✅ | Redis-based cooldown |
| 7 | Validation | ✅ | Runbook whitelisting |
| 8 | Dry-run mode | 🟡 | Scaffolded |
| 9 | Queue durability | ✅ | SQLite WAL + fsync |
| 10 | mTLS | ✅ | Client cert authentication |

### Cryptography

| Purpose | Algorithm | Library |
|---------|-----------|---------|
| Order signing | Ed25519 | nacl/cryptography |
| Evidence signing | Ed25519 | cryptography |
| Transport | TLS 1.2+ | aiohttp/ssl |
| Disk encryption | LUKS/BitLocker | OS-level |

---

## Test Infrastructure

### Test Suite Summary

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
├── test_windows_integration.py    # 3 tests - Windows runbooks (live VM)
└── conftest.py                    # Shared fixtures
```

**Total: 161 passed, 7 skipped**

### Test Categories

| Category | Tests | Description |
|----------|-------|-------------|
| **Auto-Healer** | 24 | L1/L2/L3 resolution, incident DB, learning loop |
| **Integration** | 12 | Multi-VM scenarios, data flywheel, pattern tracking |
| **Core** | 122 | Agent, drift, healing, queue, crypto, evidence |
| **Windows** | 3 | Live WinRM tests against Windows Server VM |

### Test Environment

| Component | Details |
|-----------|---------|
| **Windows VM** | VirtualBox, Windows Server 2016, via SSH tunnel |
| **NixOS VMs** | 2 NixOS VMs on remote Mac |
| **Python** | 3.13.3 via Homebrew |
| **Test Runner** | pytest with pytest-asyncio |

---

## Development Environment

### Required Tools

```bash
# macOS
brew install python@3.13 redis nix

# Python packages
pip install pydantic pyyaml aiohttp cryptography nacl pywinrm
pip install pytest pytest-asyncio pytest-cov
```

### Directory Structure

```
/Users/dad/Documents/Msp_Flakes/
├── packages/
│   └── compliance-agent/
│       ├── src/
│       │   └── compliance_agent/
│       │       ├── __init__.py
│       │       ├── agent.py
│       │       ├── config.py
│       │       ├── crypto.py
│       │       ├── drift.py
│       │       ├── evidence.py
│       │       ├── healing.py
│       │       ├── mcp_client.py
│       │       ├── models.py
│       │       ├── offline_queue.py
│       │       ├── utils.py
│       │       └── runbooks/
│       │           └── windows/
│       │               ├── executor.py
│       │               └── runbooks.py
│       ├── tests/
│       ├── docs/
│       └── venv/
├── modules/
│   └── compliance-agent.nix
├── mcp-server/
└── flake-compliance.nix
```

---

## Deployment Modes

### Direct Mode

- Agent connects directly to MCP server
- Suitable for single-tenant deployments
- Full control over infrastructure

### Reseller Mode

- Multi-tenant with reseller_id partitioning
- Webhook integration for RMM/PSA
- Syslog forwarding support

---

## Communication Protocols

### Agent → MCP Server

```
Protocol: HTTPS with mTLS
Port: 443 (production) / 8000 (dev)
Auth: Client certificate + Ed25519 order signatures
Direction: Pull-only (agent initiates all connections)
```

### Agent → Windows Servers

```
Protocol: WinRM over HTTP/HTTPS
Port: 5985 (HTTP) / 5986 (HTTPS)
Auth: NTLM / Kerberos
Transport: pywinrm
```

### Agent → Local Storage

```
Database: SQLite with WAL mode
Path: /var/lib/msp-compliance-agent/queue.db
Durability: PRAGMA synchronous=FULL
```

---

## Evidence Pipeline

### Bundle Structure

```json
{
  "bundle_id": "EB-20251123-001",
  "site_id": "clinic-001",
  "host_id": "server-01",
  "timestamp": "2025-11-23T14:32:01Z",
  "check": "patching",
  "outcome": "success",
  "hipaa_controls": ["164.308(a)(5)(ii)(B)"],
  "pre_state": { "critical_pending": 2 },
  "post_state": { "critical_pending": 0 },
  "actions": ["applied 2 critical patches"],
  "signature": "base64-ed25519-signature"
}
```

### Storage

| Location | Purpose | Retention |
|----------|---------|-----------|
| Local SQLite | Offline queue | Until uploaded |
| MCP Server | Central collection | 90 days |
| WORM Storage | Audit archive | 2+ years |

---

## Performance Targets

| Metric | Target | Notes |
|--------|--------|-------|
| Poll interval | 60s ± 10% | Jitter prevents thundering herd |
| Order TTL | 15 minutes | Prevents replay attacks |
| Evidence upload | < 5s | Per bundle |
| Drift check | < 30s | All 6 checks |
| Healing action | < 5 min | Varies by action |

---

## Related Documentation

- [AUTO_HEALING.md](./AUTO_HEALING.md) - Three-tier auto-healing architecture
- [DATA_FLYWHEEL.md](./DATA_FLYWHEEL.md) - Self-learning system documentation
- [CREDENTIALS.md](./CREDENTIALS.md) - Access credentials
- [TESTING.md](./TESTING.md) - Test guide
- [WINDOWS_TEST_SETUP.md](./WINDOWS_TEST_SETUP.md) - Windows VM setup
- [CLAUDE.md](../../../CLAUDE.md) - Master plan & architecture

---

**Maintained by:** MSP Automation Team
