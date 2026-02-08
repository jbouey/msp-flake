# Compliance Agent - Claude Code Instructions

**Location:** `packages/compliance-agent/`
**Role:** Python agent that runs on NixOS compliance appliance

---

## Quick Context

This is ONE component of the larger MSP_FLAKES platform. Read these files first:

1. **Project state:** `../../.agent/claude-progress.json` (single source of truth)
2. **Network/VMs:** `../../.agent/reference/NETWORK.md`
4. **Architecture decisions:** `../../.agent/DECISIONS.md`
5. **Master architecture:** `../../CLAUDE.md`
6. **LAB CREDENTIALS:** `../../.agent/LAB_CREDENTIALS.md` ← **ALWAYS CHECK THIS FOR CREDENTIALS**

---

## This Package Structure

```
packages/compliance-agent/
├── src/compliance_agent/
│   ├── __init__.py           # Exports
│   ├── _types.py             # ALL shared types (SINGLE SOURCE OF TRUTH)
│   ├── _interfaces.py        # ALL interfaces (protocols/ABCs)
│   ├── agent.py              # Main orchestration loop
│   ├── config.py             # Configuration (27 options)
│   ├── drift.py              # 6 compliance checks
│   ├── healing.py            # Remediation engine
│   ├── auto_healer.py        # Three-tier L1/L2/L3 orchestrator
│   ├── level1_deterministic.py  # L1 YAML rules
│   ├── level2_llm.py         # L2 LLM planner
│   ├── level3_escalation.py  # L3 human escalation
│   ├── incident_db.py        # SQLite incident tracking
│   ├── learning_loop.py      # Data flywheel (L2→L1 promotion)
│   ├── evidence.py           # Evidence bundle generation
│   ├── crypto.py             # Ed25519 signing
│   ├── mcp_client.py         # MCP server communication
│   ├── offline_queue.py      # SQLite WAL queue
│   ├── web_ui.py             # FastAPI dashboard
│   ├── phi_scrubber.py       # PHI pattern removal
│   ├── windows_collector.py  # Windows data collection
│   └── runbooks/windows/     # 7 HIPAA runbooks
├── tests/                    # pytest tests (885+ passing)
├── docs/                     # Agent-specific documentation
└── venv/                     # Python 3.13 virtualenv
```

---

## Current Priorities (from TODO.md)

See `.agent/claude-progress.json` for current priorities.

---

## Key Commands

```bash
# Activate venv
source venv/bin/activate

# Run tests
python -m pytest tests/ -v --tb=short

# Run single test file
python -m pytest tests/test_agent.py -v

# Check for deprecation warnings
python -m pytest tests/ 2>&1 | grep -c "DeprecationWarning"

# Quick import check
python -c "from compliance_agent._types import Incident; print('OK')"
```

---

## Type System

**IMPORTANT:** `_types.py` is the single source of truth for all types.

```python
# Correct import pattern
from compliance_agent._types import (
    Incident, EvidenceBundle, ComplianceCheck,
    CheckStatus, Severity, CheckType,
    now_utc  # Use instead of datetime.utcnow()
)

from compliance_agent._interfaces import (
    IDriftDetector, IHealer, IAutoHealer
)
```

**DO NOT** define duplicate types in other modules. Import from `_types.py`.

---

## Three-Tier Auto-Healing

```
Incident → L1 Deterministic (70-80%, <100ms, $0)
        → L2 LLM Planner (15-20%, 2-5s, ~$0.001)
        → L3 Human Escalation (5-10%)
        → Data Flywheel (promotes L2 patterns to L1)
```

---

## Test Status

- **891+ passed** (v1.0.55)
- See `docs/TESTING.md` for full test guide
- Use `now_utc()` not `datetime.utcnow()`

---

## Session Handoff

When done working, update:
1. `../../.agent/TODO.md` - Mark completed items
2. `../../.agent/sessions/YYYY-MM-DD-description.md` - Create session log

Use template: `../../.agent/sessions/SESSION_TEMPLATE.md`
