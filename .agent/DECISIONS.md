# Architecture Decision Records

**Last Updated:** 2025-12-03

---

## Index

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| 001 | Pull-Only Agent Architecture | ✅ Accepted | 2025-10 |
| 002 | Three-Tier Auto-Healing | ✅ Accepted | 2025-11 |
| 003 | L2 Code Mode vs Tool Mode | ✅ Accepted | 2025-11 |
| 004 | Evidence by Reference | ✅ Accepted | 2025-11 |
| 005 | Five Cognitive Function Agents | 🟡 Proposed | 2025-12 |
| 006 | PHI Scrubbing at Collection | ✅ Accepted | 2025-12 |
| 007 | BitLocker Recovery Key Backup | ✅ Accepted | 2025-12 |

---

## ADR-001: Pull-Only Agent Architecture

**Status:** ✅ Accepted  
**Date:** 2025-10  
**Context:** Agent deployed in healthcare environments needs minimal attack surface.

### Decision

Agent initiates all connections outbound (pull-only). No listening sockets on the agent.

### Consequences

**Positive:**
- No inbound firewall rules needed at client sites
- Reduced attack surface (no exposed services)
- Works behind NAT without port forwarding
- Simpler security audit story

**Negative:**
- Cannot push commands instantly (must wait for poll)
- Polling interval adds latency (60s default)

### Implementation

- Agent polls MCP server every 60s (±10% jitter)
- mTLS client certificates for authentication
- Orders have 15-minute TTL to prevent replay

---

## ADR-002: Three-Tier Auto-Healing

**Status:** ✅ Accepted  
**Date:** 2025-11  
**Context:** Need to handle incidents efficiently while containing costs and maintaining safety.

### Decision

Implement three resolution tiers:
- **L1 Deterministic:** YAML rules, <100ms, $0 cost (70-80%)
- **L2 LLM Planner:** Context-aware, 2-5s, ~$0.001/incident (15-20%)
- **L3 Human:** Rich tickets, minutes-hours (5-10%)

### Consequences

**Positive:**
- Majority of incidents handled instantly at no cost
- LLM only invoked for genuinely complex cases
- Clear escalation path with audit trail
- Data flywheel promotes L2 patterns to L1

**Negative:**
- L1 rules need ongoing curation
- L2 decisions need human review initially

### Implementation

- `level1_deterministic.py` - YAML pattern matching
- `level2_llm.py` - GPT-4o with local fallback
- `level3_escalation.py` - Slack/PagerDuty/Email
- `learning_loop.py` - Automatic L2→L1 promotion

---

## ADR-003: L2 Code Mode vs Tool Mode

**Status:** ✅ Accepted  
**Date:** 2025-11  
**Context:** MCP tool-calling paradigm inefficient for complex remediation.

### Decision

L2 LLM writes Python code instead of selecting from pre-loaded tool definitions.

### Rationale

Tool mode problems:
- Must load all tool definitions into context (thousands of tokens)
- LLM must understand tool schemas before selecting
- Multiple round-trips for complex operations

Code mode benefits:
- **98% token reduction** - Only send incident context, not tool catalog
- LLM writes executable code directly
- Single round-trip for complex multi-step fixes
- Code is auditable and reproducible

### Consequences

**Positive:**
- Dramatic cost reduction ($0.001 vs $0.10 per incident)
- Faster resolution (one LLM call vs multiple)
- Code is self-documenting for evidence

**Negative:**
- Requires RestrictedPython sandbox for safety
- Must validate generated code before execution
- Harder to constrain than predefined tools

### Implementation

- `level2_llm.py` generates Python code
- RestrictedPython sandbox limits available functions
- Allowlist of safe imports and operations
- Generated code logged in evidence bundle

---

## ADR-004: Evidence by Reference

**Status:** ✅ Accepted  
**Date:** 2025-11  
**Context:** Raw logs can be large; passing through LLM wastes tokens and risks PHI exposure.

### Decision

Raw data stored directly to disk/WORM; only summaries/references flow through LLM context.

### Rationale

- Raw logs can be megabytes; LLM context is expensive
- PHI might appear in raw logs; don't want in LLM context
- Evidence needs to be tamper-evident; raw storage better

### Consequences

**Positive:**
- Prevents token bloat in LLM calls
- Reduces PHI exposure risk
- Evidence bundles reference immutable raw data
- WORM storage provides audit trail

**Negative:**
- Must maintain raw data separately
- References must be resolvable for audit

### Implementation

- Raw logs → SQLite/filesystem with hash
- Evidence bundle contains hash references
- LLM sees only: `{check: "backup", status: "FAIL", summary: "..."}`
- Full raw data available via evidence lookup

---

## ADR-005: Five Cognitive Function Agents

**Status:** 🟡 Proposed  
**Date:** 2025-12  
**Context:** Monolithic agent becoming complex; need cleaner separation of concerns.

### Decision

Split agent into five cognitive functions:

| Agent | Function | Responsibility |
|-------|----------|----------------|
| **Scout** | Discovery | Network scanning, device inventory |
| **Sentinel** | Detection | Drift detection, compliance checks |
| **Healer** | Remediation | Three-tier auto-healing |
| **Scribe** | Documentation | Evidence generation, signing |
| **Oracle** | Analysis | Batch pattern analysis, reporting |

### Rationale

- Each function has distinct concerns
- Can scale/deploy independently
- Clearer code organization
- Easier testing per function

### Consequences

**Positive:**
- Separation of concerns
- Independent scaling
- Clearer mental model
- Easier to extend

**Negative:**
- More complexity in orchestration
- Inter-agent communication overhead
- More deployment artifacts

### Implementation Status

Currently monolithic; refactor planned for Phase 3.

---

## ADR-006: PHI Scrubbing at Collection

**Status:** ✅ Accepted  
**Date:** 2025-12  
**Context:** Windows logs may inadvertently contain PHI (patient names in paths, MRNs in error messages).

### Decision

Implement PHI scrubbing in `phi_scrubber.py` before any log data leaves the collection point.

### Patterns Detected

1. Medical Record Numbers (MRN)
2. Social Security Numbers (SSN)
3. Dates of Birth (DOB)
4. Email addresses
5. Phone numbers
6. Patient-related file paths
7. User home directories
8. SQL query data fragments
9. UNC paths with names
10. Active Directory user references

### Consequences

**Positive:**
- PHI never reaches central systems
- Reduces HIPAA breach risk
- Evidence bundles flagged `phi_scrubbed: true`
- Scrubbing is auditable

**Negative:**
- May occasionally over-scrub (false positives)
- Adds processing overhead
- Scrubbed data less useful for debugging

### Implementation

- `phi_scrubber.py` with 10 regex patterns
- Integrated into `windows_collector.py`
- Evidence bundles include `phi_scrubbed` flag
- Original text replaced with `[PHI-REDACTED-{type}]`

---

## ADR-007: BitLocker Recovery Key Backup

**Status:** ✅ Accepted  
**Date:** 2025-12  
**Context:** Enabling BitLocker without backing up recovery keys could lock out systems.

### Decision

Enhanced `RB-WIN-ENCRYPTION-001` runbook to backup recovery keys to two locations before enabling BitLocker:
1. Active Directory (if domain-joined)
2. Local secure file (`C:\BitLockerRecoveryKeys\`)

### Consequences

**Positive:**
- Recovery keys available if needed
- AD backup follows Microsoft best practice
- Local backup provides redundancy
- ACL restricts access to Administrators

**Negative:**
- Local file is a security consideration
- Must protect local backup location
- Adds steps to runbook

### Implementation

```powershell
# Backup to AD
Backup-BitLockerKeyProtector -MountPoint "C:" -KeyProtectorId $keyId

# Backup to local file
$recoveryKey | Out-File "C:\BitLockerRecoveryKeys\$hostname_recovery.txt"
icacls "C:\BitLockerRecoveryKeys" /inheritance:r
icacls "C:\BitLockerRecoveryKeys" /grant:r "BUILTIN\Administrators:(OI)(CI)F"
```

---

## Pending Decisions

### PDR-001: Approval Workflow for Disruptive Actions

**Question:** How should disruptive actions (patching, BitLocker) be approved?

**Options:**
1. Require explicit human approval via Web UI
2. Auto-approve during maintenance window
3. Require approval only for first occurrence, then auto-approve pattern
4. Tiered: auto-approve Professional+, require approval for Essential

**Status:** Needs discussion

---

### PDR-002: Evidence Bundle Signing Implementation

**Question:** Should we sign individual bundles or batch sign daily?

**Options:**
1. Sign each bundle immediately (current plan)
2. Batch sign daily (lower overhead)
3. Sign on upload to WORM only

**Status:** Implementing option 1

---

### PDR-003: Local LLM Fallback

**Question:** Should L2 fall back to local LLM when API unavailable?

**Options:**
1. Yes, run Llama 3 8B locally
2. No, escalate to L3 if API unavailable
3. Hybrid: simple cases local, complex escalate

**Status:** Currently escalates; local fallback scaffolded but not deployed
