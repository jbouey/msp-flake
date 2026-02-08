# MSP Compliance Agent

**Pull-Only Compliance Attestation Agent for Healthcare SMBs**

[![Tests](https://img.shields.io/badge/tests-453%20passed-brightgreen)]()
[![Python](https://img.shields.io/badge/python-3.13-blue)]()
[![HIPAA](https://img.shields.io/badge/HIPAA-monitoring-green)]()
[![ISO](https://img.shields.io/badge/ISO-v18-blue)]()

**Last Updated:** 2026-01-05 (Session 11)
**Agent Version:** 1.0.9
**Production Status:** HP T640 appliance deployed, 17,000+ evidence bundles

## Positioning & Safety

This product is an **evidence-grade compliance attestation substrate**. It provides:

- **Observability:** Continuous drift detection and posture measurement across HIPAA controls
- **Evidence capture:** Cryptographically signed, append-only evidence bundles with Bitcoin-anchored timestamps
- **Human-authorized remediation:** Tiered remediation workflows (L1 deterministic, L2 LLM-assisted, L3 human escalation) where L1/L2 rules are operator-configured and L3 requires explicit human decision

This is **not** a coercive enforcement or autonomous control platform. Remediation occurs via explicit operator-configured rules, externally executed runbooks, or human-escalated decisions. The system measures and attests compliance posture; it does not unilaterally impose infrastructure changes. Any L1/L2 remediation behavior is configurable, bounded by maintenance windows, and subject to health-check rollback.

## Overview

The MSP Compliance Agent is a pull-only HIPAA compliance monitoring and evidence-capture agent for healthcare SMBs. It runs at each client site and:

1. **Polls MCP server** for orders (pull-only, no listening sockets)
2. **Detects drift** from baseline configuration (6 compliance checks)
3. **Executes operator-authorized remediation** with rollback capability (L1/L2/L3 tiered)
4. **Generates evidence** bundles for all actions (Ed25519 signed)
5. **Operates offline** with durable queue when MCP unavailable
6. **Manages Windows servers** via WinRM runbooks

## Quick Start

```bash
# Clone and setup
cd /Users/dad/Documents/Msp_Flakes/packages/compliance-agent
source venv/bin/activate

# Run tests
python -m pytest tests/ -v --tb=short

# Run agent (requires config)
python -m compliance_agent.agent --config /path/to/config.yaml
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Compliance Agent                          │
│                                                              │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐       │
│  │  drift  │  │ healing │  │evidence │  │  agent  │       │
│  │   .py   │  │   .py   │  │   .py   │  │   .py   │       │
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘       │
│       │            │            │            │              │
│       └────────────┴────────────┴────────────┘              │
│                          │                                   │
│  ┌───────────────────────┼───────────────────────┐         │
│  │              Core Services                     │         │
│  │  mcp_client │ crypto │ offline_queue │ config │         │
│  └───────────────────────┴───────────────────────┘         │
│                          │                                   │
│  ┌───────────────────────▼───────────────────────┐         │
│  │           Windows Runbooks (WinRM)             │         │
│  │     executor.py │ runbooks.py (7 runbooks)    │         │
│  └───────────────────────────────────────────────┘         │
│                          │                                   │
│  ┌───────────────────────▼───────────────────────┐         │
│  │           Provisioning (First Boot)            │         │
│  │    provisioning.py │ QR code │ config.yaml    │         │
│  └───────────────────────────────────────────────┘         │
└─────────────────────────────────────────────────────────────┘
                           │
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
      ┌──────────┐  ┌──────────┐  ┌──────────┐
      │   MCP    │  │  Redis   │  │  WORM    │
      │  Server  │  │  Queue   │  │ Storage  │
      └──────────┘  └──────────┘  └──────────┘
```

## Three-Tier Remediation Architecture

The agent implements a tiered incident resolution system with human escalation:

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

| Level | Handles | Response Time | Cost |
|-------|---------|---------------|------|
| L1 Deterministic | 70-80% | <100ms | $0 |
| L2 LLM Planner | 15-20% | 2-5s | ~$0.001/incident |
| L3 Human | 5-10% | Minutes-hours | Human time |

## Modules

| Module | Description |
|--------|-------------|
| `agent.py` | Main orchestration loop with graceful shutdown |
| `auto_healer.py` | Three-tier incident resolution orchestrator |
| `level1_deterministic.py` | YAML-based deterministic rules engine |
| `level2_llm.py` | LLM context-aware planner (local/API/hybrid) |
| `level3_escalation.py` | Human escalation with rich tickets |
| `learning_loop.py` | Pattern learning for L2→L1 promotion |
| `incident_db.py` | SQLite incident history for context |
| `drift.py` | 6 compliance drift detection checks |
| `healing.py` | Remediation engine with rollback |
| `evidence.py` | Evidence bundle generation & signing |
| `mcp_client.py` | MCP server communication (mTLS) |
| `offline_queue.py` | SQLite WAL queue for offline operation |
| `crypto.py` | Ed25519 signature verification |
| `provisioning.py` | First-boot QR/code provisioning |
| `config.py` | Configuration management (27 options) |
| `models.py` | Pydantic data models |
| `utils.py` | Utility functions |

## Drift Detection (6 Checks)

| Check | HIPAA Control | Description |
|-------|---------------|-------------|
| Patching | 164.308(a)(5)(ii)(B) | OS/software patch status |
| AV/EDR | 164.308(a)(5)(ii)(B) | Antivirus/endpoint protection |
| Backup | 164.308(a)(7)(ii)(A) | Backup completion & age |
| Logging | 164.312(b) | Audit logging status |
| Firewall | 164.312(a)(1) | Firewall configuration |
| Encryption | 164.312(a)(2)(iv) | Disk/data encryption |

## Windows Runbooks (7 Runbooks)

| Runbook | Description |
|---------|-------------|
| `RB-WIN-PATCH-001` | Windows Update compliance |
| `RB-WIN-AV-001` | Windows Defender health |
| `RB-WIN-BACKUP-001` | Backup verification |
| `RB-WIN-LOGGING-001` | Event logging audit policy |
| `RB-WIN-FIREWALL-001` | Firewall status |
| `RB-WIN-ENCRYPTION-001` | BitLocker encryption |
| `RB-WIN-AD-001` | Active Directory health |

## Configuration

```yaml
# config.yaml
site_id: clinic-001
host_id: server-01
deployment_mode: direct  # or reseller

mcp_url: https://mcp.example.com
poll_interval: 60

# mTLS (via SOPS/Vault)
client_cert_file: /run/secrets/client-cert.pem
client_key_file: /run/secrets/client-key.pem
signing_key_file: /run/secrets/signing-key

# Paths
state_dir: /var/lib/msp-compliance-agent
baseline_path: /etc/msp/baseline.yaml

# Maintenance Window
maintenance_window: "02:00-04:00"
allow_disruptive_outside_window: false
```

## Guardrails

| # | Guardrail | Status |
|---|-----------|--------|
| 1 | Order authentication (Ed25519) | ✅ |
| 2 | Order TTL (15 min default) | ✅ |
| 3 | Maintenance window gating | ✅ |
| 4 | Health check + rollback on failure | ✅ |
| 5 | Evidence generation | ✅ |
| 6 | Rate limiting | ✅ |
| 7 | Runbook whitelisting | ✅ |
| 8 | Dry-run mode | 🟡 |
| 9 | Queue durability (SQLite WAL) | ✅ |
| 10 | mTLS authentication | ✅ |

## Testing

```bash
# Full test suite (161 tests)
python -m pytest tests/ -v --tb=short

# Core tests only
python -m pytest tests/test_agent.py tests/test_healing.py tests/test_drift.py -v

# Auto-healer tests (24 tests)
python -m pytest tests/test_auto_healer.py -v

# Integration tests (simulated VMs, 12 tests)
python -m pytest tests/test_auto_healer_integration.py -v

# Windows integration (requires VM)
export WIN_TEST_HOST="192.168.56.10"
export WIN_TEST_USER="vagrant"
export WIN_TEST_PASS="vagrant"
python tests/test_windows_integration.py

# Integration tests with real VMs
python tests/test_auto_healer_integration.py --real-vms
```

## Documentation

- [AUTO_HEALING.md](docs/AUTO_HEALING.md) - Three-tier auto-healing architecture
- [TECH_STACK.md](docs/TECH_STACK.md) - Technology stack details
- [CREDENTIALS.md](docs/CREDENTIALS.md) - Access credentials
- [TESTING.md](docs/TESTING.md) - Test guide
- [WINDOWS_TEST_SETUP.md](docs/WINDOWS_TEST_SETUP.md) - Windows VM setup

## Dependencies

```
pydantic>=2.5.0
pyyaml>=6.0.1
aiohttp>=3.9.0
cryptography>=42.0.0
pywinrm>=0.4.3
pytest>=8.0
pytest-asyncio>=1.0
```

## License

Proprietary - MSP Compliance Platform

## Status

**Phase 12 - Launch Readiness** - Production pilot deployed, 453 tests passing

### Current (2026-01-05)
- **ISO v18** deployed to HP T640 physical appliance
- **Agent v1.0.9** with all healing fixes integrated
- **17,000+ evidence bundles** collected with Ed25519 signatures
- **Credential-pull architecture** - no credentials stored on appliance
- **Three-tier healing verified** - Windows firewall chaos test passed
- **North Valley Lab** - Windows DC + Workstation for compliance testing

### Session 11 (2026-01-05)
- Built ISO v18 with healing integration fixes
- VPS garbage collection freed 109GB
- Transferred ISO to iMac for deployment
- Physical appliance checking in every 60s

### Session 10 (2026-01-05)
- Fixed L1 `execute()` to properly check action success
- Fixed `_handle_drift_healing()` to use `auto_healer.heal()` correctly
- Fixed `_heal_run_windows_runbook()` to use `WindowsExecutor.run_runbook()`
- Verified Windows firewall auto-healing on physical appliance

### Session 9 (2026-01-04)
- Implemented credential-pull architecture (RMM pattern)
- Credentials fetched from Central Command each check-in cycle
- No credentials stored on disk - stolen device safety

### Earlier Milestones
- Partner/reseller infrastructure (Phase 11)
- QR code provisioning for appliances
- Ed25519 evidence signing (agent + server)
- MinIO WORM storage with 7-year retention
- Client portal with magic-link auth
