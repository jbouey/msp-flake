# Interface Contracts & Data Types

**Last Updated:** 2025-12-03
**Source:** packages/compliance-agent/src/compliance_agent/models.py

---

## Core Data Models

### ComplianceCheck

Represents a single compliance verification result.

```python
class ComplianceCheck:
    check_id: str           # e.g., "patching", "backup", "encryption"
    status: CheckStatus     # PASS, FAIL, WARNING, ERROR
    message: str            # Human-readable description
    timestamp: datetime     # When check was performed
    hipaa_controls: list[str]  # e.g., ["164.308(a)(5)(ii)(B)"]
    details: dict           # Check-specific metadata
    remediation_available: bool
```

**Valid check_id values:**
- `patching` - OS/software patch status
- `av_edr` - Antivirus/endpoint protection
- `backup` - Backup completion & age
- `logging` - Audit logging status
- `firewall` - Firewall configuration
- `encryption` - Disk/data encryption

---

### Incident

Represents a detected compliance issue requiring action.

```python
class Incident:
    incident_id: str        # UUID
    site_id: str            # Client identifier
    host_id: str            # Target hostname
    check_type: str         # Which compliance check triggered
    severity: Severity      # CRITICAL, HIGH, MEDIUM, LOW
    status: IncidentStatus  # OPEN, IN_PROGRESS, RESOLVED, ESCALATED
    created_at: datetime
    resolved_at: Optional[datetime]
    resolution_level: Optional[int]  # 1=L1, 2=L2, 3=L3
    runbook_id: Optional[str]
    evidence_bundle_id: Optional[str]
```

---

### EvidenceBundle

Cryptographically signed proof of compliance action.

```python
class EvidenceBundle:
    bundle_id: str          # "EB-{timestamp}-{seq}"
    site_id: str
    host_id: str
    timestamp: datetime
    check: str              # Which check triggered
    outcome: str            # "success", "failure", "partial"
    hipaa_controls: list[str]
    pre_state: dict         # State before action
    post_state: dict        # State after action
    actions: list[str]      # What was done
    signature: Optional[str]  # Ed25519 signature (base64)
    phi_scrubbed: bool      # Was PHI scrubbing applied?
```

---

### HealingAction

Represents a remediation action to execute.

```python
class HealingAction:
    action_type: str        # Type of remediation
    runbook_id: str         # Reference to runbook
    parameters: dict        # Action-specific params
    requires_approval: bool # Needs human approval?
    is_disruptive: bool     # Could cause service interruption?
    maintenance_window_only: bool
```

**Valid action_types:**
- `restart_service` - Restart a Windows/Linux service
- `apply_patch` - Install pending patches
- `enable_feature` - Enable Windows feature (Defender, BitLocker)
- `fix_config` - Correct configuration drift
- `run_script` - Execute remediation script
- `escalate` - Send to human (L3)

---

### Order (MCP Command)

Signed instruction from MCP server to agent.

```python
class Order:
    order_id: str           # UUID
    action: str             # What to do
    target: str             # Target host/service
    parameters: dict        # Action parameters
    issued_at: datetime
    expires_at: datetime    # TTL (default 15 min)
    signature: str          # Ed25519 signature
    requires_maintenance_window: bool
```

---

## Three-Tier Auto-Healer Contracts

### L1 Rule (Deterministic)

YAML-based pattern matching rule.

```yaml
# Structure in l1_rules.yaml
rules:
  - id: "L1-BACKUP-001"
    name: "Backup Failure - Restart Service"
    pattern:
      check_type: "backup"
      status: "FAIL"
      message_contains: "service not running"
    action:
      type: "restart_service"
      service: "wbengine"
    hipaa_controls:
      - "164.308(a)(7)(ii)(A)"
    max_retries: 2
    cooldown_seconds: 300
```

---

### L2 Resolution (LLM)

Context passed to LLM planner.

```python
class L2Context:
    incident: Incident
    system_state: dict      # Current compliance state
    recent_incidents: list  # Last 10 incidents on this host
    available_runbooks: list[str]
    constraints: dict       # maintenance_window, approval_required, etc.
```

**L2 Response Contract:**
```python
class L2Decision:
    runbook_id: str         # Selected runbook
    parameters: dict        # Runbook parameters
    reasoning: str          # Why this runbook
    confidence: float       # 0.0-1.0
    escalate: bool          # Should go to L3?
    escalation_reason: Optional[str]
```

---

### L3 Escalation Ticket

Rich ticket for human review.

```python
class EscalationTicket:
    ticket_id: str
    incident: Incident
    attempted_resolutions: list[dict]  # What L1/L2 tried
    l2_reasoning: Optional[str]
    suggested_actions: list[str]
    urgency: str            # "immediate", "next_business_day", "scheduled"
    channels: list[str]     # ["slack", "pagerduty", "email"]
```

---

## Windows Runbook Contract

### RunbookDefinition

```python
class RunbookDefinition:
    runbook_id: str         # "RB-WIN-PATCH-001"
    name: str
    description: str
    hipaa_controls: list[str]
    is_disruptive: bool
    requires_reboot: bool
    estimated_duration: str  # "5m", "30m", etc.
    steps: list[RunbookStep]
    rollback_steps: list[RunbookStep]
    validation_script: str   # PowerShell to verify success
```

### RunbookStep

```python
class RunbookStep:
    step_id: int
    description: str
    script: str             # PowerShell script
    timeout_seconds: int
    retry_count: int
    continue_on_error: bool
```

---

## API Endpoints (Web UI)

| Endpoint | Method | Request | Response |
|----------|--------|---------|----------|
| `/api/status` | GET | - | `{compliance_score, controls_status}` |
| `/api/controls` | GET | - | `list[ComplianceCheck]` |
| `/api/evidence` | GET | `?limit=N` | `list[EvidenceBundle]` |
| `/api/incidents` | GET | `?status=open` | `list[Incident]` |
| `/api/regulatory` | GET | - | `{updates: list, last_check}` |
| `/api/windows/collect` | POST | `{target, checks}` | `{job_id, status}` |

---

## Event Queue Messages

### Incident Created

```json
{
  "event_type": "incident.created",
  "timestamp": "2025-12-03T14:32:01Z",
  "payload": {
    "incident_id": "uuid",
    "site_id": "clinic-001",
    "host_id": "windc-01",
    "check_type": "backup",
    "severity": "HIGH"
  }
}
```

### Healing Completed

```json
{
  "event_type": "healing.completed",
  "timestamp": "2025-12-03T14:35:23Z",
  "payload": {
    "incident_id": "uuid",
    "resolution_level": 1,
    "runbook_id": "RB-WIN-BACKUP-001",
    "outcome": "success",
    "evidence_bundle_id": "EB-20251203-001"
  }
}
```

---

## Validation Rules

### Order Signature Verification

```python
def verify_order(order: Order, public_key: bytes) -> bool:
    """
    1. Check order.expires_at > now()
    2. Reconstruct signing payload: f"{order_id}|{action}|{target}|{issued_at}"
    3. Verify Ed25519 signature against payload
    4. Check order_id not in replay cache
    """
```

### Evidence Bundle Signing

```python
def sign_bundle(bundle: EvidenceBundle, private_key: bytes) -> str:
    """
    1. Serialize bundle to canonical JSON (sorted keys, no whitespace)
    2. Hash with SHA-256
    3. Sign hash with Ed25519 private key
    4. Return base64-encoded signature
    """
```

---

## Error Codes

| Code | Meaning | Recovery |
|------|---------|----------|
| `E001` | Order signature invalid | Reject, log attempt |
| `E002` | Order expired (TTL) | Reject, request fresh order |
| `E003` | Runbook not found | Escalate to L3 |
| `E004` | Target unreachable | Retry with backoff, then escalate |
| `E005` | Maintenance window required | Queue for window |
| `E006` | Rate limit exceeded | Wait cooldown period |
| `E007` | PHI detected in logs | Scrub and flag bundle |
