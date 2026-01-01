# Session Handoff - MSP Compliance Platform

**Date:** 2026-01-01
**Phase:** Phase 10 - Production Deployment + Lab Infrastructure
**Status:** North Valley Lab fully configured, Windows 10 workstation pending install

---

## Quick Summary

HIPAA compliance automation platform for healthcare SMBs. NixOS appliances phone home to central command, auto-heal infrastructure, generate audit evidence.

**Production URLs:**
- Dashboard: https://dashboard.osiriscare.net
- API: https://api.osiriscare.net
- Portal: https://msp.osiriscare.net

**Lab Environment:**
- DC: 192.168.88.250 (NVDC01.northvalley.local)
- Workstation: 192.168.88.251 (NVWS01 - pending install)
- Credentials: See `.agent/LAB_CREDENTIALS.md`

---

## Today's Session (2026-01-01)

### Completed
1. **North Valley Clinic Lab Environment Build**
   - 9 phases executed and verified on NVDC01
   - File Server: 5 SMB shares (PatientFiles, ClinicDocs, Backups$, Scans, Templates)
   - AD Structure: 6 OUs, 7 security groups, 8 domain users
   - Security: Audit logging, password policy (12 char, 90 day), Defender, Firewall
   - Verification: 8/8 checks passed

2. **Windows 10 Workstation VM Created**
   - VM: northvalley-ws01 on iMac VirtualBox
   - 4GB RAM, 2 CPU, 50GB disk
   - Windows 10 ISO attached, VM running
   - Awaiting manual Windows installation via VirtualBox GUI

3. **Documentation Updates**
   - `.agent/TODO.md` - Updated with lab build progress, task #23 added
   - `.agent/NETWORK.md` - Updated earlier with new lab topology
   - `TECH_STACK.md` - Complete project overview
   - `.agent/LAB_CREDENTIALS.md` - NEW: All lab credentials in one place

### Pending (Next Session)
1. Complete Windows 10 installation on NVWS01 (GUI required)
2. Configure static IP (192.168.88.251), join domain
3. Enable WinRM, test domain user login
4. Test compliance agent runbooks against lab environment

---

## What's Complete

### Production Infrastructure
- Hetzner VPS (178.156.162.116) with Docker Compose
- Caddy reverse proxy with auto-TLS
- PostgreSQL, Redis, MinIO (WORM storage)
- Central Command React dashboard
- Client portal with magic-link auth
- 7 operations SOPs in documentation

### North Valley Lab (192.168.88.x)
- **NVDC01** (192.168.88.250): Windows Server 2019 AD DC
  - Domain: northvalley.local
  - File Server with 5 shares
  - 8 AD users, 7 security groups
  - All security policies configured
  - WinRM enabled and tested
- **NVWS01** (192.168.88.251): Windows 10 Workstation (pending install)

### Compliance Agent
- Three-tier auto-healing (L1/L2/L3)
- Data flywheel (L2→L1 pattern promotion)
- 8 HIPAA control checks
- PHI scrubbing on logs
- Ed25519 evidence signing
- 169 tests passing

---

## What's Pending

### Immediate
1. **Windows 10 workstation install** - Manual via VirtualBox GUI
2. **Test ISO build on Linux** - `nix build .#appliance-iso`
3. **First pilot client** - End-to-end validation

### Short-term
- MinIO Object Lock configuration
- mTLS client certificates for appliances
- Test compliance agent against lab VMs

---

## Key Files

| Purpose | Location |
|---------|----------|
| Project context | `.agent/CONTEXT.md` |
| Current tasks | `.agent/TODO.md` |
| Network/VMs | `.agent/NETWORK.md` |
| **Lab credentials** | `.agent/LAB_CREDENTIALS.md` |
| Phase status | `IMPLEMENTATION-STATUS.md` |
| Master architecture | `CLAUDE.md` |
| Appliance ISO | `iso/` directory |
| Compliance agent | `packages/compliance-agent/` |

---

## Commands

```bash
# Work on compliance agent
cd packages/compliance-agent && source venv/bin/activate
python -m pytest tests/ -v --tb=short

# Lab VM management
ssh jrelly@192.168.88.50  # iMac lab host
ssh jrelly@192.168.88.50 '/Applications/VirtualBox.app/Contents/MacOS/VBoxManage list runningvms'

# WinRM to DC
source venv/bin/activate
python3 -c "
import winrm
s = winrm.Session('http://192.168.88.250:5985/wsman',
                  auth=('NORTHVALLEY\\\\Administrator', 'NorthValley2024!'),
                  transport='ntlm')
print(s.run_ps('hostname').std_out.decode())
"

# VPS management
ssh root@178.156.162.116
cd /opt/mcp-server && docker compose ps
```

---

## Session History

| Date | Focus | Status |
|------|-------|--------|
| 2026-01-01 | North Valley Lab Build (9 phases) + Win10 VM | In Progress |
| 2025-12-31 | Appliance ISO + SOPs | Complete |
| 2025-12-30 | Client portal + deployment | Complete |
| 2025-12-28 | Central Command dashboard | Complete |
| 2025-12-08 | NixOS module updates | Complete |
| 2025-12-04 | Windows VM + guardrails | Complete |

See `.agent/sessions/` for detailed session logs.

---

## Lab Network Diagram

```
192.168.88.0/24 - North Valley Lab Network
├── 192.168.88.1   - Gateway/Router
├── 192.168.88.50  - iMac (VirtualBox host, jrelly@)
├── 192.168.88.250 - NVDC01 (Windows Server 2019 DC)
│   └── Domain: northvalley.local
│   └── WinRM: :5985
│   └── Shares: PatientFiles, ClinicDocs, Backups$, Scans, Templates
└── 192.168.88.251 - NVWS01 (Windows 10 Workstation) [PENDING]
```

---

## For Next Session

1. Read this file + `.agent/CONTEXT.md`
2. Check `.agent/TODO.md` for priorities
3. Complete Windows 10 install on NVWS01
4. Test compliance agent runbooks against lab
