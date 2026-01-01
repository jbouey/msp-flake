# Malachor MSP Compliance Platform - Agent Context

**Last Updated:** 2025-12-31
**Phase:** Phase 10 - Production Deployment + Appliance Imaging
**Test Status:** 169 passed

---

## What Is This Project?

A HIPAA compliance automation platform for small-to-mid healthcare practices (4-25 providers). Replaces traditional MSPs at 75% lower cost through autonomous infrastructure healing + compliance documentation.

**Core Value Proposition:** Enforcement-first automation that auto-fixes issues in 2-10 minutes rather than alert→ticket→human workflows taking hours.

---

## Current Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                 Central Command (Hetzner VPS)                    │
│                 http://178.156.162.116                           │
│  ┌─────────────┬─────────────┬─────────────┬─────────────────┐  │
│  │  Dashboard  │  MCP Server │  PostgreSQL │   MinIO (WORM)  │  │
│  │  :3000      │  :8000      │  16-alpine  │   :9000/:9001   │  │
│  └─────────────┴─────────────┴─────────────┴─────────────────┘  │
│  React UI │ Learning Loop │ Pattern DB │ Evidence Store          │
└─────────────────────────────────────────────────────────────────┘
                              ▲
                              │ mTLS/HTTPS (pull-only)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Compliance Agent (NixOS)                      │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │           Three-Tier Auto-Healer                           │ │
│  │  L1 Deterministic (70-80%) → L2 LLM (15-20%) → L3 Human   │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              │                                   │
│  ┌──────────┬────────────────┼────────────────┬──────────────┐  │
│  │  drift   │    healing     │    evidence    │   mcp_client │  │
│  │  .py     │    .py         │    .py         │   .py        │  │
│  └──────────┴────────────────┴────────────────┴──────────────┘  │
│                              │                                   │
│  ┌───────────────────────────┴───────────────────────────────┐  │
│  │              Windows Runbooks (WinRM)                      │  │
│  │  executor.py │ 7 HIPAA runbooks                           │  │
│  └────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Technologies

| Component | Technology | Purpose |
|-----------|------------|---------|
| Host OS | NixOS 24.05 | Deterministic, auditable infrastructure |
| Agent | Python 3.13 | Compliance monitoring + self-healing |
| Windows Integration | pywinrm + WinRM | Remote Windows server management |
| LLM Interface | MCP (Model Context Protocol) | Structured LLM-to-tool interface |
| Evidence Storage | SQLite + WORM S3 | Tamper-evident audit trail |
| Crypto | Ed25519 | Order/evidence signing |

---

## Business Model

| Tier | Target | Price | Features |
|------|--------|-------|----------|
| Essential | 1-5 providers | $200-400/mo | Basic auto-healing, 30d retention |
| Professional | 6-15 providers | $600-1200/mo | Signed evidence, 90d retention |
| Enterprise | 15-50 providers | $1500-3000/mo | Blockchain anchoring, 2yr retention |

---

## Current State

### What's Working
- ✅ Three-tier auto-healing (L1/L2/L3)
- ✅ Data flywheel (L2→L1 pattern promotion)
- ✅ Windows compliance collection (7 runbooks)
- ✅ Web UI dashboard on appliance
- ✅ PHI scrubbing on log collection
- ✅ BitLocker recovery key backup
- ✅ Federal Register HIPAA monitoring
- ✅ **Production MCP Server deployed** (Hetzner VPS)
- ✅ Ed25519 order signing
- ✅ MinIO WORM evidence storage
- ✅ Rate limiting (10 req/5min/site)
- ✅ **Central Command Dashboard** (https://dashboard.osiriscare.net)
- ✅ **Learning Loop Infrastructure** - PostgreSQL patterns table
- ✅ **Agent Sync Endpoints** - `/agent/sync`, `/agent/checkin`
- ✅ **Client Portal** - Magic-link auth at /portal
- ✅ **TLS via Caddy** - Auto-certs for all domains
- ✅ **Appliance ISO Infrastructure** - `iso/` directory
- ✅ **Operations SOPs** - 7 SOPs in Documentation page

### What's Pending
- ⚠️ Test ISO build on Linux system
- ⚠️ Set up MinIO object lock retention policy
- ⚠️ First pilot client enrollment

### Current Compliance Score
- Windows Server: 28.6% (2 pass, 5 fail, 1 warning)
- BitLocker: ✅ PASS
- Active Directory: ✅ PASS
- Everything else: ❌ FAIL (expected - test VM not fully configured)

---

## File Locations

| What | Path |
|------|------|
| Project Root | `/Users/dad/Documents/Msp_Flakes` |
| Compliance Agent | `packages/compliance-agent/` |
| Agent Source | `packages/compliance-agent/src/compliance_agent/` |
| **Types (SSoT)** | `packages/compliance-agent/src/compliance_agent/_types.py` |
| **Interfaces** | `packages/compliance-agent/src/compliance_agent/_interfaces.py` |
| Tests | `packages/compliance-agent/tests/` |
| NixOS Module | `modules/compliance-agent.nix` |
| Runbooks | `packages/compliance-agent/src/compliance_agent/runbooks/` |
| Documentation | `packages/compliance-agent/docs/` |
| Agent Context | `.agent/` |
| **Mermaid Diagrams** | `docs/diagrams/` |

### Production Central Command (Hetzner VPS)

| What | Location |
|------|----------|
| Server IP | `178.156.162.116` |
| SSH Access | `ssh root@178.156.162.116` (key auth) |
| Dashboard | `https://dashboard.osiriscare.net` |
| API Endpoint | `https://api.osiriscare.net` |
| MSP Portal | `https://msp.osiriscare.net` |
| MinIO Console | (internal :9001) |
| Server Files | `/opt/mcp-server/` |
| Frontend Files | `/opt/mcp-server/frontend/dist/` |
| Docker Compose | `/opt/mcp-server/docker-compose.yml` |
| Signing Key | `/opt/mcp-server/secrets/signing.key` |
| Init SQL | `/opt/mcp-server/init.sql` |

### Appliance ISO Infrastructure

| What | Location |
|------|----------|
| ISO Config | `iso/appliance-image.nix` |
| Base Config | `iso/configuration.nix` |
| Status Page | `iso/local-status.nix` |
| Provisioning | `iso/provisioning/generate-config.py` |
| Config Template | `iso/provisioning/template-config.yaml` |
| Flake Outputs | `flake-compliance.nix` (appliance-iso, build-iso, test-iso) |

### Source Module Structure

```
src/compliance_agent/
├── __init__.py           # Exports all types and interfaces
├── _types.py             # ALL shared types (single source of truth)
├── _interfaces.py        # ALL module interfaces (protocols/ABCs)
├── agent.py              # Main agent orchestration
├── config.py             # Configuration management
├── drift.py              # Drift detection (6 checks)
├── healing.py            # Self-healing engine
├── auto_healer.py        # Three-tier orchestrator
├── level1_deterministic.py  # L1 YAML rules
├── level2_llm.py         # L2 LLM planner
├── level3_escalation.py  # L3 human escalation
├── incident_db.py        # SQLite incident tracking
├── learning_loop.py      # Data flywheel (L2→L1)
├── evidence.py           # Evidence bundle generation
├── crypto.py             # Ed25519 signing
├── mcp_client.py         # MCP server communication
├── offline_queue.py      # SQLite WAL queue
├── web_ui.py             # FastAPI dashboard
├── phi_scrubber.py       # PHI pattern removal
├── windows_collector.py  # Windows compliance collection
└── runbooks/
    └── windows/
        ├── executor.py   # WinRM execution
        └── runbooks.py   # 7 HIPAA runbooks
```

---

## Quick Commands

```bash
# Activate Python environment
cd /Users/dad/Documents/Msp_Flakes/packages/compliance-agent
source venv/bin/activate

# Run tests (161 passing)
python -m pytest tests/ -v --tb=short

# SSH to compliance appliance
ssh -p 4444 root@174.178.63.139

# Create tunnel for web UI
ssh -f -N -L 9080:192.168.56.103:8080 jrelly@174.178.63.139
# Then: open http://localhost:9080

# Windows DC connection test
python3 -c "
import winrm
s = winrm.Session('http://127.0.0.1:55985/wsman', auth=('MSP\\\\vagrant','vagrant'), transport='ntlm')
print(s.run_ps('whoami').std_out.decode())
"

# Central Command (Production)
ssh root@178.156.162.116                           # SSH to Hetzner VPS
curl https://api.osiriscare.net/health             # Health check
curl https://api.osiriscare.net/runbooks           # List runbooks
curl https://api.osiriscare.net/stats              # Server stats
curl https://api.osiriscare.net/learning/status    # Learning loop status
curl https://api.osiriscare.net/learning/candidates # Promotion candidates

# Dashboard (Production)
open https://dashboard.osiriscare.net              # Central Command Dashboard
open https://msp.osiriscare.net                    # MSP Portal (alias)

# Central Command Management (on Hetzner)
cd /opt/mcp-server && docker compose logs -f mcp-server  # View API logs
cd /opt/mcp-server && docker compose logs -f central-command  # View dashboard logs
cd /opt/mcp-server && docker compose ps                   # Check status
cd /opt/mcp-server && docker compose restart              # Restart all

# Appliance ISO Build (requires Linux)
nix build .#appliance-iso -o result-iso            # Build bootable ISO
nix run .#test-iso                                 # Test in QEMU
python iso/provisioning/generate-config.py --site-id "clinic-001" --site-name "Test Clinic"
```

---

## Related Files

- `NETWORK.md` - VM inventory, network topology
- `CONTRACTS.md` - Interface contracts, data types
- `DECISIONS.md` - Architecture Decision Records
- `TODO.md` - Current tasks and priorities

---

## HIPAA Controls Covered

| Control | Citation | Implementation |
|---------|----------|----------------|
| Audit Controls | §164.312(b) | Evidence bundles, hash chain |
| Access Control | §164.312(a)(1) | Firewall checks, AD monitoring |
| Encryption | §164.312(a)(2)(iv) | BitLocker verification |
| Backup | §164.308(a)(7) | Backup status, recovery key backup |
| Malware Protection | §164.308(a)(5)(ii)(B) | Windows Defender health |
| Patch Management | §164.308(a)(5)(ii)(B) | Patch compliance checks |

---

**For new AI sessions:** Start by reading this file, then check `TODO.md` for current priorities.
