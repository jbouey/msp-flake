# MSP Compliance Platform

## What This Is

HIPAA compliance automation for healthcare SMBs. NixOS + MCP + LLM.
Auto-heal infrastructure, generate audit evidence, replace traditional MSPs at 75% lower cost.

**Target:** 1-50 provider practices in NEPA region
**Pricing:** $200-3000/mo based on size/tier

## Zero-Friction Appliance Deployment

**Golden Flake Architecture:** The NixOS flake (`flake.nix`) is the single source of truth for appliance configuration. It is hardware-agnostic and deployable to ANY x86_64 hardware via `nixos-install`.

**DO NOT use `dd` disk images.** VM-built disk images cause firmware mismatches, ESP mount timeouts, and hardware-specific failures when written to different physical hardware.

**Correct Deployment Flow:**
```
┌─────────────────────────────────────────────────────────────────┐
│  1. Boot Installer ISO (USB)                                    │
│     └─ iso/appliance-image.nix builds the installer             │
│                                                                 │
│  2. msp-auto-install service runs automatically                 │
│     └─ Detects internal drive (skips USB/removable)             │
│     └─ Partitions: GPT with ESP (512MB) + root (remaining)      │
│     └─ Formats: FAT32 (ESP) + ext4 (root)                       │
│     └─ Runs nixos-install --flake .#appliance                   │
│                                                                 │
│  3. Reboot to installed system                                  │
│     └─ compliance-agent starts, calls home to Central Command   │
│     └─ MAC address lookup provisions appliance identity         │
│                                                                 │
│  4. Ready for production                                        │
└─────────────────────────────────────────────────────────────────┘
```

**Key Files:**
- `iso/appliance-image.nix` - Installer ISO with auto-install service
- `iso/configuration.nix` - Base appliance configuration
- `flake.nix` - Defines `nixosConfigurations.appliance`

**Build & Deploy:**
```bash
# On VPS: Build installer ISO
nix build .#appliance-iso

# Write to USB (on Mac)
sudo dd if=result/iso/*.iso of=/dev/diskN bs=4m status=progress

# Boot target hardware from USB - installation is automatic
```

## Current State

**Quick start:** `python3 .agent/scripts/context-manager.py status`

**Primary context file:** `.agent/claude-progress.json` (JSON - single source of truth)

**Session management:**
```bash
python3 .agent/scripts/context-manager.py status       # View state
python3 .agent/scripts/context-manager.py new-session 86 description
python3 .agent/scripts/context-manager.py end-session
python3 .agent/scripts/context-manager.py compact      # Archive old sessions
```

**Reference docs:** `.agent/reference/` (credentials, network, decisions)

## Directory Structure

```
packages/compliance-agent/   # Python agent (main work area)
  src/compliance_agent/      # Core modules
  tests/                     # pytest tests (839 passing)
  venv/                      # Python 3.13 virtualenv
modules/                     # NixOS modules
mcp-server/                  # Central MCP server
  central-command/           # Dashboard backend + frontend
docs/                        # Detailed reference docs
.agent/                      # Session tracking
.claude/skills/              # Knowledge index + docs/
```

## Key Commands

```bash
# Work on compliance agent
cd packages/compliance-agent && source venv/bin/activate

# Run tests
python -m pytest tests/ -v --tb=short

# Single test file
python -m pytest tests/test_agent.py -v

# Preflight check
./scripts/preflight.sh
```

## Reference Documentation

| Doc | Content |
|-----|---------|
| `docs/ARCHITECTURE.md` | System design, three-tier healing, MCP structure |
| `docs/HIPAA_FRAMEWORK.md` | Compliance details, BAA template, monitoring tiers |
| `docs/ROADMAP.md` | Build plan, phases, implementation status |
| `docs/RUNBOOKS.md` | Remediation patterns, evidence bundles |
| `docs/DISCOVERY.md` | Network discovery, device enrollment |
| `docs/DASHBOARDS.md` | Executive reporting, compliance packets |
| `docs/PROVENANCE.md` | Signing, time sync, hash chains, SBOM |

## Knowledge Index (Passive Context)

Quick lookup - read full docs on demand via `.claude/skills/docs/<category>/`.

```
AREA     | KEY PATTERNS                          | DOC PATH
---------|---------------------------------------|----------------------------------
auth     | bcrypt-12, PKCE, session cookie       | docs/security/security.md
test     | @pytest.mark.asyncio, AsyncMock       | docs/testing/testing.md
hipaa    | 6 drift checks, EvidenceBundle, L1    | docs/hipaa/compliance.md
backend  | L1→L2→L3, FastAPI router, Depends     | docs/backend/backend.md
db       | asyncpg pool, WAL, 26 migrations      | docs/database/database.md
nixos    | A/B partition, health gate, nftables  | docs/nixos/infrastructure.md
frontend | useQuery, useMutation, 51 hooks       | docs/frontend/frontend.md
api      | /api REST, gRPC proto, OAuth flow     | docs/api/api.md
perf     | gather, memo, virtual scroll, batch   | docs/performance/performance.md
```

**Pattern Retrieval:** When working on a specific area, READ the full doc:
- Security → `.claude/skills/docs/security/security.md`
- Tests → `.claude/skills/docs/testing/testing.md`
- HIPAA → `.claude/skills/docs/hipaa/compliance.md`
- Backend → `.claude/skills/docs/backend/backend.md`
- Database → `.claude/skills/docs/database/database.md`
- NixOS → `.claude/skills/docs/nixos/infrastructure.md`
- React → `.claude/skills/docs/frontend/frontend.md`
- API → `.claude/skills/docs/api/api.md`
- Performance → `.claude/skills/docs/performance/performance.md`

Full compressed index: `.claude/skills/INDEX.md`

## Three-Tier Auto-Healing

```
Incident → L1 Deterministic (70-80%, <100ms, $0)
        → L2 LLM Planner (15-20%, 2-5s, ~$0.001)
        → L3 Human Escalation (5-10%)
        → Data Flywheel (promotes L2→L1)
```

## Type System (compliance-agent)

```python
# Single source of truth
from compliance_agent._types import (
    Incident, EvidenceBundle, ComplianceCheck,
    CheckStatus, Severity, CheckType,
    now_utc  # Use instead of datetime.utcnow()
)
```

## Lab Credentials (MUST READ)

**ALWAYS check `.agent/LAB_CREDENTIALS.md` for all lab access credentials.**

Quick reference:
| System | IP | Username | Password |
|--------|-----|----------|----------|
| iMac Host | 192.168.88.50 | jrelly | (SSH key) |
| Windows DC | 192.168.88.250 | NORTHVALLEY\Administrator | NorthValley2024! |
| Windows WS | 192.168.88.251 | localadmin | NorthValley2024! |
| Physical Appliance | 192.168.88.246 | root | (SSH key) |
| VPS | 178.156.162.116 | root | (SSH key) |

## Don't Forget

- **READ `.agent/LAB_CREDENTIALS.md` for ALL lab credentials**
- Run tests before AND after changes
- Update `.agent/CONTEXT.md` with session changes
- iMac gateway (NEPA clinic): `192.168.88.50` (jrelly@)
- Physical appliance: `192.168.88.246` (root@)
- Use `now_utc()` not `datetime.utcnow()` (deprecated)

## Session Handoff

When done, update:
1. `.agent/TODO.md` - Mark completed items
2. `.agent/sessions/YYYY-MM-DD-description.md` - Create session log

Template: `.agent/sessions/SESSION_TEMPLATE.md`
