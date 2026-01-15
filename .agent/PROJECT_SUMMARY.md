# MSP Compliance Platform - Project Summary

**Last Updated:** 2026-01-15
**Agent Version:** v1.0.34
**Sprint:** Phase 12 - Launch Readiness

---

## What This Is

HIPAA compliance automation platform for healthcare SMBs. Replaces traditional MSP services at 75% lower cost through:
- NixOS-based compliance appliances
- Three-tier auto-healing (L1 deterministic, L2 LLM, L3 human)
- Automated evidence generation for audits
- MCP-based central command dashboard

**Target Market:** 1-50 provider healthcare practices
**Pricing:** $200-3000/mo based on size/tier

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Central Command (VPS)                     │
│  api.osiriscare.net / dashboard.osiriscare.net              │
│  - FastAPI backend + React frontend                         │
│  - PostgreSQL + MinIO (evidence storage)                    │
│  - OAuth integrations (Microsoft, AWS, Google, Okta, Azure) │
└─────────────────────────────────────────────────────────────┘
                              │
                    HTTPS API (evidence, incidents)
                              │
┌─────────────────────────────┴─────────────────────────────┐
│                    Compliance Appliance                    │
│  - NixOS on HP T640 thin client                           │
│  - Python compliance-agent (v1.0.34)                      │
│  - WinRM to Windows servers/workstations                  │
│  - 43 HIPAA runbooks, auto-healing                        │
└───────────────────────────────────────────────────────────┘
                              │
                    WinRM (5985/5986)
                              │
┌─────────────────────────────┴─────────────────────────────┐
│              Customer Windows Infrastructure               │
│  - Domain Controllers                                      │
│  - File Servers                                            │
│  - Workstations (AD-discovered)                           │
└───────────────────────────────────────────────────────────┘
```

---

## Key Features Implemented

### Three-Tier Auto-Healing
| Tier | Coverage | Latency | Cost |
|------|----------|---------|------|
| L1 Deterministic | 70-80% | <100ms | $0 |
| L2 LLM Planner | 15-20% | 2-5s | ~$0.001/incident |
| L3 Human Escalation | 5-10% | Manual | Partner time |

### Compliance Coverage
- **43 HIPAA Runbooks** covering firewall, encryption, access control, audit logging
- **Evidence Generation** with Ed25519 signing and hash chains
- **OTS Time Anchoring** for tamper-evident audit trails
- **Multi-Framework Support** (HIPAA, SOC2, NIST ready)

### Workstation Coverage (Phase 1)
- AD workstation discovery via PowerShell Get-ADComputer
- 5 WMI compliance checks: BitLocker, Defender, Patches, Firewall, Screen Lock
- Per-workstation evidence bundles with HIPAA control mappings

### Cloud Integrations (5 Providers)
| Provider | Resources Collected |
|----------|---------------------|
| AWS | IAM users, EC2, S3, CloudTrail |
| Google Workspace | Users, Devices, OAuth apps |
| Okta | Users, Groups, Apps, Policies |
| Azure AD | Users, Groups, Apps, Devices |
| Microsoft Security | Defender alerts, Intune, Secure Score |

---

## Repository Structure

```
packages/compliance-agent/   # Python agent (main work area)
  src/compliance_agent/      # Core modules
    appliance_agent.py       # Main orchestration loop
    workstation_discovery.py # AD workstation enumeration
    workstation_checks.py    # WMI compliance checks
    auto_healer.py          # Three-tier healing
    level1_deterministic.py # L1 YAML rules
    level2_llm.py           # L2 LLM planner
    evidence.py             # Evidence bundle generation
    runbooks/windows/       # 43 HIPAA runbooks
  tests/                    # pytest tests (778+ passing)

mcp-server/                 # Central MCP server
  central-command/
    backend/               # FastAPI backend
    frontend/              # React dashboard

iso/                       # NixOS ISO build
  appliance-image.nix     # Appliance configuration

modules/                   # NixOS modules
docs/                      # Reference documentation
.agent/                    # Session tracking
```

---

## Current Sprint: Phase 12 - Launch Readiness

### Completed This Session (Session 38-39)
- [x] Workstation discovery config fields added to appliance agent
- [x] WinRM port check for workstation online detection
- [x] Fixed $params_Hostname variable injection bug
- [x] Microsoft Security OAuth integration complete
- [x] VPS deployment automation
- [x] ISO v33 built and deployed

### Known Issues
- Test-NetConnection times out when running from DC to check workstation online status
- Need to optimize or use alternative connectivity check

### Next Steps
1. Build ISO v34 with workstation fixes
2. First compliance packet generation
3. 30-day monitoring period
4. Partner pilot deployment

---

## Quick Commands

```bash
# Run agent tests
cd packages/compliance-agent && source venv/bin/activate
python -m pytest tests/ -v --tb=short

# SSH to physical appliance
ssh root@192.168.88.246

# SSH to VPS
ssh root@178.156.162.116

# Deploy to VPS
ssh root@api.osiriscare.net "/opt/mcp-server/deploy.sh"

# Build ISO on VPS
ssh root@178.156.162.116 "cd /root/msp-iso-build && git pull && nix --extra-experimental-features 'nix-command flakes' build .#nixosConfigurations.osiriscare-appliance.config.system.build.isoImage -o result-v34"
```

---

## Lab Environment

| System | IP | Purpose |
|--------|-----|---------|
| iMac (VirtualBox host) | 192.168.88.50 | VM management |
| Physical Appliance | 192.168.88.246 | Production pilot |
| VM Appliance | 192.168.88.247 | Testing |
| Windows DC (NVDC01) | 192.168.88.250 | Domain controller |
| Windows WS (NVWS01) | 192.168.88.251 | Test workstation |
| VPS | 178.156.162.116 | Central Command |

---

## Related Documentation

| File | Content |
|------|---------|
| `.agent/CONTEXT.md` | Current session state |
| `.agent/TODO.md` | Task tracking |
| `.agent/SESSION_HANDOFF.md` | Session handoff notes |
| `.agent/LAB_CREDENTIALS.md` | Lab credentials |
| `.agent/VPS_DEPLOYMENT.md` | VPS deployment guide |
| `IMPLEMENTATION-STATUS.md` | Full implementation status |
| `docs/ARCHITECTURE.md` | System architecture |
| `docs/HIPAA_FRAMEWORK.md` | Compliance framework |
