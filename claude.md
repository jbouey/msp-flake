# MSP Compliance Platform

## What This Is

HIPAA compliance automation for healthcare SMBs. NixOS + MCP + LLM.
Auto-heal infrastructure, generate audit evidence, replace traditional MSPs at 75% lower cost.

**Target:** 1-50 provider practices in NEPA region
**Pricing:** $200-3000/mo based on size/tier

## Current State

**Agent Version:** v1.0.34 | **Sprint:** Phase 12 - Launch Readiness

- See `.agent/PROJECT_SUMMARY.md` for full project overview
- See `.agent/CONTEXT.md` for session state
- See `IMPLEMENTATION-STATUS.md` for phase tracking

## Directory Structure

```
packages/compliance-agent/   # Python agent (main work area)
  src/compliance_agent/      # Core modules
  tests/                     # pytest tests (778+ passing)
  venv/                      # Python 3.13 virtualenv
modules/                     # NixOS modules
mcp-server/                  # Central MCP server
docs/                        # Detailed reference docs
.agent/                      # Session tracking
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

## Don't Forget

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
