# Malachor MSP Compliance Platform - Agent Context

**Last Updated:** 2025-12-03
**Phase:** Phase 2 Complete → Phase 3 Planning
**Test Status:** 161 passed, 7 skipped

---

## What Is This Project?

A HIPAA compliance automation platform for small-to-mid healthcare practices (4-25 providers). Replaces traditional MSPs at 75% lower cost through autonomous infrastructure healing + compliance documentation.

**Core Value Proposition:** Enforcement-first automation that auto-fixes issues in 2-10 minutes rather than alert→ticket→human workflows taking hours.

---

## Current Architecture

```
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

### What's Pending
- ⚠️ Evidence bundle signing (Ed25519)
- ⚠️ Auto-remediation approval policy
- ⚠️ datetime.utcnow() deprecation (907 warnings)
- ⚠️ Web UI Federal Register integration fix

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
