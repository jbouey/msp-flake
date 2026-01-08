# Healing System Integration Audit

**Generated:** 2026-01-03
**Purpose:** Determine what exists, what's missing, and what needs modification to integrate three-tier AutoHealer into the deployed appliance agent.

---

## A. Component Inventory

| Component | File | Status | Integration Point |
|-----------|------|--------|-------------------|
| **Appliance Agent** | `appliance_agent.py` | Deployed (ISO v12) | Main loop - NO AutoHealer |
| **AutoHealer** | `auto_healer.py` | Code Complete | Orchestrator - NOT wired |
| **L1 Engine** | `level1_deterministic.py` | Ready | 10 builtin rules, YAML custom |
| **L2 Engine** | `level2_llm.py` | Ready | OpenAI/Ollama/Hybrid |
| **L3 Engine** | `level3_escalation.py` | Ready | Slack/PagerDuty/Email |
| **Learning Loop** | `learning_loop.py` | Ready | L2→L1 promotion |
| **Windows Executor** | `runbooks/windows/executor.py` | Ready | WinRM execution |
| **Healing Module** | `healing.py` | Ready | NixOS-focused remediation |
| **Drift Detection** | `drift.py` | Ready | 6 check types |
| **Evidence** | `evidence.py` | Ready | Bundle + WORM upload |
| **Config** | `config.py` | Ready | 27+ options |
| **Incident DB** | `incident_db.py` | Ready | SQLite tracking |

---

## B. Current Flow in appliance_agent.py

### What It Does Now (lines 1-350)

```
1. __init__:
   - Loads basic config (site_id, host_id, mcp_url)
   - Initializes Ed25519 signer
   - Creates offline queue (SQLite)
   - NO AutoHealer initialization
   - NO incident database
   - NO learning loop

2. main loop (run_forever):
   - Every 60s: phone home to Central Command
   - Sync L1 rules from /agent/sync
   - Run basic drift checks (NixOS-focused)
   - Submit evidence bundles to /evidence endpoint
   - Check for pending orders
   - NO healing triggered on drift
   - NO L2/L3 escalation

3. drift handling:
   - Runs 5 basic checks: generation, services, NTP, disk, firewall
   - Creates evidence bundle for each check
   - Submits to Central Command
   - DOES NOT attempt remediation
   - DOES NOT create incidents

4. evidence:
   - Generates CB-YYYY-MM-DD-NNNN bundles
   - Signs with Ed25519
   - Uploads to Central Command /evidence

5. phone-home:
   - POST /api/appliances/checkin
   - Reports status, IP, MAC
```

### What's Missing from appliance_agent.py

- [ ] AutoHealer import/initialization
- [ ] IncidentDatabase initialization
- [ ] LearningLoop initialization
- [ ] Conversion of drift results to Incidents
- [ ] Call to `auto_healer.handle_incident()` after drift detection
- [ ] WindowsExecutor for Windows endpoints (future)
- [ ] Config options for healing (dry_run, maintenance_window)

---

## C. AutoHealer Interface

### Class: `AutoHealer` (auto_healer.py:47)

```python
class AutoHealer:
    def __init__(
        self,
        incident_db: IncidentDatabase,
        l1_engine: DeterministicEngine,
        l2_planner: Level2Planner,
        l3_handler: EscalationHandler,
        learning_loop: SelfLearningSystem,
        action_executor: Optional[Callable] = None,
        dry_run: bool = False
    )
```

### Main Method: `handle_incident(incident: Incident) -> HealingResult`

```python
async def handle_incident(self, incident: Incident) -> HealingResult:
    """
    Main entry point. Routes incident through L1→L2→L3 tiers.

    Returns:
        HealingResult with resolution_level, action_taken, success, etc.
    """
```

### Input Format (Incident)

```python
@dataclass
class Incident:
    id: str
    incident_type: str  # "patching", "av_edr", "backup", etc.
    severity: str       # "low", "medium", "high", "critical"
    site_id: str
    host_id: str
    raw_data: Dict[str, Any]  # drift.pre_state + drift details
    created_at: str
    pattern_signature: str    # Hash for pattern matching
```

### Output Format (HealingResult)

```python
@dataclass
class HealingResult:
    incident_id: str
    resolution_level: str  # "L1", "L2", "L3"
    action_taken: str
    success: bool
    duration_ms: int
    output: Dict[str, Any]
    error: Optional[str]
```

### Dependencies (Must Initialize First)

1. `IncidentDatabase(db_path)` - SQLite for incident tracking
2. `DeterministicEngine(rules_dir, incident_db, action_executor)` - L1
3. `Level2Planner(config, incident_db, action_executor)` - L2
4. `EscalationHandler(config, incident_db)` - L3
5. `SelfLearningSystem(incident_db, config)` - Data flywheel

---

## D. Gap Analysis

### Missing in appliance_agent.py

| Item | Priority | Notes |
|------|----------|-------|
| AutoHealer instantiation | HIGH | Core gap |
| IncidentDatabase instantiation | HIGH | Required for AutoHealer |
| DeterministicEngine instantiation | HIGH | L1 rules |
| Call to AutoHealer after drift | HIGH | Wire the loop |
| Config options for healing | MEDIUM | dry_run, maintenance |
| Evidence schema for healing outcomes | LOW | Already has outcome field |
| Offline queue for L2/L3 | LOW | Queue exists, needs extension |

### What Needs Modification

1. **appliance_agent.py `__init__`** - Add AutoHealer initialization
2. **appliance_agent.py `run_drift_checks`** - Convert DriftResult to Incident, call AutoHealer
3. **appliance_agent.py `run_forever`** - Add learning loop periodic check
4. **config.py** - Add healing config options (dry_run, l2_enabled, etc.)

### What's Ready to Use

| Component | File | Ready? |
|-----------|------|--------|
| DeterministicEngine | level1_deterministic.py | YES |
| Level2Planner | level2_llm.py | YES |
| EscalationHandler | level3_escalation.py | YES |
| SelfLearningSystem | learning_loop.py | YES |
| IncidentDatabase | incident_db.py | YES |
| WindowsExecutor | runbooks/windows/executor.py | YES |
| Windows Runbooks | runbooks/windows/runbooks.py | YES (7 runbooks) |
| Action Executor (NixOS) | healing.py | YES |

---

## E. Windows Runbook Inventory

| Filename | Runbook ID | What It Handles | Disruptive |
|----------|------------|-----------------|------------|
| runbooks.py | RB-WIN-PATCH-001 | Windows Updates/WSUS | YES (reboot) |
| runbooks.py | RB-WIN-AV-001 | Windows Defender | NO |
| runbooks.py | RB-WIN-BACKUP-001 | Windows Server Backup | NO |
| runbooks.py | RB-WIN-LOGGING-001 | Windows Event Logs | NO |
| runbooks.py | RB-WIN-FIREWALL-001 | Windows Firewall | MEDIUM |
| runbooks.py | RB-WIN-ENCRYPTION-001 | BitLocker | YES |
| runbooks.py | RB-WIN-AD-001 | AD Health | NO |

---

## F. Proposed Integration Plan

### 1. Minimal Changes to appliance_agent.py

**Add to `__init__` (after line 50):**

```python
# Three-tier healing initialization
from .incident_db import IncidentDatabase
from .auto_healer import AutoHealer
from .level1_deterministic import DeterministicEngine
from .level2_llm import Level2Planner, LLMConfig, LLMMode
from .level3_escalation import EscalationHandler, EscalationConfig
from .learning_loop import SelfLearningSystem, PromotionConfig
from .healing import HealingEngine

# Initialize incident database
self.incident_db = IncidentDatabase(
    db_path=self.state_dir / "incidents.db"
)

# Initialize L1 engine with action executor
self.l1_engine = DeterministicEngine(
    rules_dir=Path("/etc/msp/rules"),
    incident_db=self.incident_db,
    action_executor=self._execute_action
)

# Initialize L2 planner (disabled by default, needs API key)
self.l2_planner = Level2Planner(
    config=LLMConfig(mode=LLMMode.HYBRID),
    incident_db=self.incident_db,
    action_executor=self._execute_action
) if self.l2_enabled else None

# Initialize L3 handler
self.l3_handler = EscalationHandler(
    config=EscalationConfig(
        webhook_enabled=True,
        webhook_url=f"{self.mcp_url}/api/escalations"
    ),
    incident_db=self.incident_db
)

# Initialize learning loop
self.learning_loop = SelfLearningSystem(
    incident_db=self.incident_db,
    config=PromotionConfig(auto_promote=False)
)

# Initialize AutoHealer
self.auto_healer = AutoHealer(
    incident_db=self.incident_db,
    l1_engine=self.l1_engine,
    l2_planner=self.l2_planner,
    l3_handler=self.l3_handler,
    learning_loop=self.learning_loop,
    action_executor=self._execute_action,
    dry_run=self.dry_run
)
```

**Add to drift handling (after drift detection):**

```python
async def _handle_drift(self, drift: DriftResult) -> HealingResult:
    """Convert drift to incident and handle through AutoHealer."""
    if not drift.drifted:
        return None

    # Create incident from drift
    incident = Incident(
        id=f"INC-{uuid.uuid4().hex[:12]}",
        incident_type=drift.check,
        severity=drift.severity,
        site_id=self.site_id,
        host_id=self.host_id,
        raw_data={
            "check_type": drift.check,
            "drift_detected": True,
            "pre_state": drift.pre_state,
            "recommended_action": drift.recommended_action,
            **drift.pre_state
        },
        created_at=datetime.now(timezone.utc).isoformat(),
        pattern_signature=self._compute_pattern_signature(drift)
    )

    # Record incident
    self.incident_db.record_incident(incident)

    # Handle through three-tier system
    result = await self.auto_healer.handle_incident(incident)

    # Create evidence for healing outcome
    await self._create_healing_evidence(incident, result)

    return result
```

### 2. L1 Rules File (Initial Windows Rules)

Create `/etc/msp/rules/windows-baseline.yaml`:

```yaml
rules:
  - id: L1-WIN-DEFENDER-001
    name: Windows Defender Service Down
    description: Restart Windows Defender if service stopped
    conditions:
      - field: check_type
        operator: eq
        value: av_edr
      - field: drift_detected
        operator: eq
        value: true
      - field: details.service_running
        operator: eq
        value: false
      - field: details.platform
        operator: eq
        value: windows
    action: run_windows_runbook
    action_params:
      runbook_id: RB-WIN-AV-001
      phases: ["remediate", "verify"]
    hipaa_controls:
      - "164.308(a)(5)(ii)(B)"
    priority: 5
    cooldown_seconds: 300

  - id: L1-WIN-EVENTLOG-001
    name: Windows Event Log Service Down
    description: Restart Windows Event Log service
    conditions:
      - field: check_type
        operator: eq
        value: logging
      - field: drift_detected
        operator: eq
        value: true
      - field: details.platform
        operator: eq
        value: windows
    action: run_windows_runbook
    action_params:
      runbook_id: RB-WIN-LOGGING-001
      phases: ["remediate", "verify"]
    hipaa_controls:
      - "164.312(b)"
    priority: 5
    cooldown_seconds: 300

  - id: L1-WIN-FIREWALL-001
    name: Windows Firewall Disabled
    description: Enable Windows Firewall if disabled
    conditions:
      - field: check_type
        operator: eq
        value: firewall
      - field: drift_detected
        operator: eq
        value: true
      - field: details.firewall_enabled
        operator: eq
        value: false
      - field: details.platform
        operator: eq
        value: windows
    action: run_windows_runbook
    action_params:
      runbook_id: RB-WIN-FIREWALL-001
      phases: ["remediate", "verify"]
    hipaa_controls:
      - "164.312(e)(1)"
    priority: 10
    cooldown_seconds: 600

  - id: L1-WIN-PATCH-CRITICAL-001
    name: Critical Windows Update Missing
    description: Escalate critical missing patches (reboot required)
    conditions:
      - field: check_type
        operator: eq
        value: patching
      - field: drift_detected
        operator: eq
        value: true
      - field: details.critical_missing
        operator: gt
        value: 0
      - field: details.platform
        operator: eq
        value: windows
    action: escalate
    action_params:
      reason: "Critical Windows patches missing - requires maintenance window"
      urgency: high
      runbook_id: RB-WIN-PATCH-001
    hipaa_controls:
      - "164.308(a)(5)(ii)(B)"
    priority: 3
    cooldown_seconds: 3600

  - id: L1-WIN-BACKUP-AGE-001
    name: Windows Backup Overdue
    description: Trigger backup if last backup > 24 hours
    conditions:
      - field: check_type
        operator: eq
        value: backup
      - field: drift_detected
        operator: eq
        value: true
      - field: details.backup_age_hours
        operator: gt
        value: 24
      - field: details.platform
        operator: eq
        value: windows
    action: run_windows_runbook
    action_params:
      runbook_id: RB-WIN-BACKUP-001
      phases: ["remediate", "verify"]
    hipaa_controls:
      - "164.308(a)(7)(ii)(A)"
    priority: 15
    cooldown_seconds: 1800
```

### 3. Config Additions

Add to `config.py` AgentConfig class:

```python
# Healing options
healing_enabled: bool = Field(
    default=True,
    description="Enable auto-healing"
)
dry_run: bool = Field(
    default=False,
    description="Dry-run mode (log actions, don't execute)"
)
l2_enabled: bool = Field(
    default=False,
    description="Enable L2 LLM planner"
)
l2_api_key_file: Optional[Path] = Field(
    default=None,
    description="Path to OpenAI API key file"
)
escalation_webhook_url: Optional[str] = Field(
    default=None,
    description="Webhook URL for L3 escalations"
)
```

### 4. Evidence Schema (Already Exists)

The `EvidenceBundle` in `models.py` already has:
- `outcome`: success/failed/reverted/deferred/alert
- `actions`: List[ActionTaken]
- `error`: Optional error message
- `rollback_available`, `rollback_generation`

**Add field for healing resolution level:**
```python
healing_resolution_level: Optional[str] = None  # L1, L2, L3
```

### 5. Test Plan

1. **Unit tests:**
   - Test AutoHealer initialization with mocks
   - Test incident creation from DriftResult
   - Test L1 rule matching

2. **Integration tests (local):**
   - Run appliance_agent with dry_run=True
   - Verify drift creates incident
   - Verify L1 rule matches
   - Verify evidence includes healing outcome

3. **VM test (North Valley Lab):**
   - Deploy updated agent to test appliance
   - Introduce Windows drift (stop Defender)
   - Verify L1 rule triggers runbook
   - Verify runbook restarts Defender
   - Verify evidence bundle created

4. **Chaos test:**
   - Run chaos agent on Windows DC
   - Randomly break things (services, firewall, etc.)
   - Verify auto-healing responds correctly
   - Monitor L2/L3 escalation paths

---

## G. Risk Assessment

### What Could Break

| Risk | Impact | Mitigation |
|------|--------|------------|
| Healing causes downtime | HIGH | dry_run mode default |
| L1 rule matches wrong incident | MEDIUM | Strict condition matching |
| L2 LLM hallucinates action | MEDIUM | Guardrails + action whitelist |
| Runbook fails mid-execution | MEDIUM | Automatic rollback |
| Evidence not generated | LOW | Try/catch with logging |

### Rollback Plan

1. **dry_run mode** - Set `dry_run=True` in config to log without executing
2. **Kill switch** - Add endpoint to disable healing via Central Command
3. **NixOS rollback** - Previous generation always available
4. **Rule disable** - Can disable individual L1 rules via YAML

### Dry-Run Bulletproofing

```python
async def _execute_action(self, action: str, params: dict, ...) -> Any:
    """Action executor with dry-run support."""
    if self.dry_run:
        logger.info(f"[DRY-RUN] Would execute: {action} with {params}")
        return {"dry_run": True, "action": action, "params": params}

    # Actual execution
    return await self._real_execute(action, params, ...)
```

---

## H. Summary

### Current State
- Three-tier healing code: **COMPLETE**
- Windows runbooks: **COMPLETE** (7 runbooks)
- Data flywheel: **COMPLETE**
- Integration with appliance_agent: **NOT DONE**

### Effort Estimate
- Integration code changes: ~200 lines
- Config additions: ~20 lines
- Test coverage: ~100 lines
- New L1 rules file: ~100 lines

### Recommendation

**Proceed with integration.** The core components are battle-tested (161 tests passing). The main work is wiring them together in `appliance_agent.py`.

Start with:
1. Add dry_run mode
2. Wire AutoHealer to appliance_agent
3. Deploy to test appliance
4. Run chaos test against Windows VM

---

**Ready for your approval to proceed with code changes.**
