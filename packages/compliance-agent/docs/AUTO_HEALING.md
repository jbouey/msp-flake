# Three-Tier Auto-Healing Architecture

**Last Updated:** 2025-11-23
**Version:** 1.0.0
**Tests:** 24 tests covering all levels

---

## Overview

The MSP Compliance Agent implements a three-tier auto-healing architecture designed for the LLM era. This approach optimizes for:

1. **Speed**: 70-80% of incidents resolved in <100ms (Level 1)
2. **Cost**: $0 for deterministic resolutions
3. **Intelligence**: LLM-powered decision making for complex cases
4. **Learning**: Continuous improvement through pattern promotion

## Architecture

```
        Incident
            │
            ▼
    ┌───────────────┐
    │   Level 1     │  70-80% of incidents
    │ Deterministic │  <100ms, $0 cost
    │    Rules      │  YAML pattern matching
    └───────┬───────┘
            │ No match
            ▼
    ┌───────────────┐
    │   Level 2     │  15-20% of incidents
    │  LLM Planner  │  2-5s, context-aware
    │   (Hybrid)    │  Local + API fallback
    └───────┬───────┘
            │ Can't resolve / Low confidence
            ▼
    ┌───────────────┐
    │   Level 3     │  5-10% of incidents
    │    Human      │  Rich tickets
    │  Escalation   │  Slack/PagerDuty/Email
    └───────────────┘
            │
            ▼
    ┌───────────────┐
    │ Learning Loop │  Data Flywheel
    │ L2 → L1       │  Auto-promote patterns
    │  Promotion    │  with 90%+ success
    └───────────────┘
```

---

## Level 1: Deterministic Rules Engine

**File:** `level1_deterministic.py`

### Purpose
Fast, predictable incident resolution using YAML-based pattern matching rules.

### Characteristics
- Response time: <100ms
- Cost: $0 (no LLM calls)
- Predictability: 100% deterministic
- Auditability: Rules are version-controlled YAML

### Built-in Rules

| Rule ID | Trigger | Action | HIPAA Control |
|---------|---------|--------|---------------|
| L1-PATCH-001 | Generation drift | update_to_baseline_generation | 164.308(a)(5)(ii)(B) |
| L1-AV-001 | AV service down | restart_av_service | 164.308(a)(5)(ii)(B) |
| L1-BACKUP-001 | Backup failure | run_backup_job | 164.308(a)(7)(ii)(A) |
| L1-BACKUP-002 | Backup age >24h | run_backup_job | 164.308(a)(7)(ii)(A) |
| L1-LOG-001 | Logging down | restart_logging_services | 164.312(b) |
| L1-FW-001 | Firewall drift | restore_firewall_baseline | 164.312(e)(1) |
| L1-ENCRYPT-001 | Encryption issue | escalate | 164.312(a)(2)(iv) |
| L1-CERT-001 | Cert expiring <30d | renew_certificate | 164.312(e)(1) |
| L1-DISK-001 | Disk >90% | cleanup_disk_space | - |
| L1-SERVICE-001 | Crash loop | escalate | - |

### Custom Rules

Create custom rules in `/etc/msp/rules/`:

```yaml
# /etc/msp/rules/custom.yaml
rules:
  - id: L1-CUSTOM-001
    name: High Memory Usage
    description: Clear caches when memory exceeds 90%
    conditions:
      - field: incident_type
        operator: eq
        value: memory_high
      - field: details.memory_percent
        operator: gt
        value: 90
    action: clear_cache
    action_params:
      targets: ["/var/cache", "/tmp"]
    hipaa_controls: []
    enabled: true
    priority: 25
    cooldown_seconds: 300
```

### Condition Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `eq` | Equals | `field == value` |
| `ne` | Not equals | `field != value` |
| `contains` | String contains | `"error" in field` |
| `regex` | Regex match | `field =~ /pattern/` |
| `gt` | Greater than | `field > value` |
| `lt` | Less than | `field < value` |
| `in` | In list | `field in [a, b, c]` |
| `not_in` | Not in list | `field not in [a, b, c]` |

---

## Level 2: LLM Context-Aware Planner

**File:** `level2_llm.py`

### Purpose
Intelligent incident resolution using LLM with historical context.

### Characteristics
- Response time: 2-5 seconds
- Cost: ~$0.001/incident (varies by model)
- Intelligence: Context-aware decisions
- Modes: Local, API, or Hybrid

### LLM Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| `local` | Ollama/llama.cpp | Privacy-first, air-gapped |
| `api` | OpenAI/Anthropic | Highest quality |
| `hybrid` | Local first, API fallback | Best of both worlds |

### Configuration

```python
from compliance_agent import LLMConfig, LLMMode

config = LLMConfig(
    mode=LLMMode.HYBRID,

    # Local LLM
    local_model="llama3.1:8b",
    local_endpoint="http://localhost:11434",
    local_timeout=30,

    # API LLM (fallback)
    api_provider="openai",  # or "anthropic"
    api_model="gpt-4o-mini",
    api_key="sk-...",
    api_timeout=60,

    # Guardrails
    max_tokens=500,
    temperature=0.1,
    allowed_actions=["restart_service", "run_backup_job", ...]
)
```

### Context Building

The LLM receives rich context:
- Incident details (type, severity, raw data)
- Historical context (pattern statistics)
- Similar incidents (what worked before)
- Successful actions for this pattern

### Guardrails

1. **Action whitelist**: Only allowed actions can be executed
2. **Confidence threshold**: Low confidence (<0.6) requires approval
3. **Dangerous action detection**: Certain actions always require approval
4. **Escalation on uncertainty**: If LLM can't decide, escalate to L3

---

## Level 3: Human Escalation

**File:** `level3_escalation.py`

### Purpose
Human intervention for incidents that can't be auto-resolved.

### Characteristics
- Response time: Minutes to hours (human dependent)
- Rich context: Full incident history in ticket
- Multiple channels: Slack, PagerDuty, Email, Teams
- Feedback loop: Human feedback improves system

### Priority Levels

| Priority | Channels | Response Target |
|----------|----------|-----------------|
| `critical` | PagerDuty + Slack + Email | Immediate |
| `high` | PagerDuty + Slack | 15 minutes |
| `medium` | Slack + Email | 1 hour |
| `low` | Email only | 4 hours |

### Ticket Contents

Escalation tickets include:
- Incident summary and severity
- Full raw data and error details
- Historical context (pattern stats)
- Similar resolved incidents
- Attempted actions and outcomes
- Recommended action (if any)
- HIPAA controls affected

### Configuration

```python
from compliance_agent import EscalationConfig

config = EscalationConfig(
    # Email
    email_enabled=True,
    email_recipients=["oncall@clinic.com"],

    # Slack
    slack_enabled=True,
    slack_webhook_url="https://hooks.slack.com/...",
    slack_channel="#incidents",

    # PagerDuty
    pagerduty_enabled=True,
    pagerduty_routing_key="abc123...",

    # Behavior
    auto_assign=True,
    default_assignee="security-team",
    escalation_timeout_minutes=60
)
```

---

## Learning Loop (Data Flywheel)

**File:** `learning_loop.py`

### Purpose
Continuously improve the system by promoting successful L2 patterns to L1 rules.

### How It Works

1. **Track L2 decisions**: Every LLM decision is recorded with outcome
2. **Identify patterns**: Similar incidents are grouped by pattern signature
3. **Evaluate promotion**: Patterns meeting criteria become candidates
4. **Generate rules**: Successful patterns are converted to L1 rules
5. **Deploy**: New rules are saved and loaded into L1 engine

### Promotion Criteria

| Criterion | Default | Description |
|-----------|---------|-------------|
| `min_occurrences` | 5 | Minimum times pattern seen |
| `min_l2_resolutions` | 3 | Minimum L2 resolutions |
| `min_success_rate` | 90% | Minimum success rate |
| `max_resolution_time` | 30s | Max avg resolution time |

### Pattern Signature

Patterns are identified by normalizing incident data:
- Incident type
- Check type
- Drift type
- Error patterns (with variables removed)

Example signature: `abc123def456` for all "backup/drift_detected/true" incidents.

### Promotion Report

```python
from compliance_agent import SelfLearningSystem

learning = SelfLearningSystem(incident_db, config)
report = learning.get_promotion_report()

# Example output:
{
    "total_candidates": 3,
    "candidates": [
        {
            "pattern_signature": "abc123",
            "recommended_action": "run_backup_job",
            "confidence_score": 0.94,
            "stats": {
                "total_occurrences": 15,
                "success_rate": 0.93,
                "l2_resolutions": 12
            }
        }
    ]
}
```

---

## Incident Database

**File:** `incident_db.py`

### Purpose
SQLite database tracking all incidents for historical context and learning.

### Schema

```sql
-- Main incidents table
CREATE TABLE incidents (
    id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL,
    host_id TEXT NOT NULL,
    incident_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    raw_data TEXT NOT NULL,
    pattern_signature TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution_level TEXT,  -- L1, L2, L3
    resolution_action TEXT,
    outcome TEXT,  -- success, failure, escalated
    resolution_time_ms INTEGER,
    human_feedback TEXT,
    promoted_to_l1 BOOLEAN
);

-- Pattern statistics (materialized view)
CREATE TABLE pattern_stats (
    pattern_signature TEXT PRIMARY KEY,
    total_occurrences INTEGER,
    l1_resolutions INTEGER,
    l2_resolutions INTEGER,
    l3_resolutions INTEGER,
    success_count INTEGER,
    total_resolution_time_ms INTEGER,
    last_seen TEXT,
    recommended_action TEXT,
    promotion_eligible BOOLEAN
);
```

### Querying Context

```python
# Get pattern context for LLM
context = incident_db.get_pattern_context(pattern_signature)

# Get similar incidents
similar = incident_db.get_similar_incidents(incident_type, site_id)

# Get stats summary
stats = incident_db.get_stats_summary(days=30)
```

---

## Usage Example

```python
from compliance_agent import (
    AutoHealer, AutoHealerConfig,
    LLMMode
)

# Configure
config = AutoHealerConfig(
    db_path="/var/lib/msp-compliance-agent/incidents.db",
    rules_dir="/etc/msp/rules",
    enable_level1=True,
    enable_level2=True,
    llm_mode=LLMMode.HYBRID,
    local_model="llama3.1:8b",
    api_key="sk-...",
    enable_level3=True,
    slack_webhook="https://hooks.slack.com/...",
    enable_learning=True,
    dry_run=False
)

# Create healer
healer = AutoHealer(config)

# Process incident
result = await healer.heal(
    site_id="clinic-001",
    host_id="server-01",
    incident_type="backup",
    severity="high",
    raw_data={
        "check_type": "backup",
        "drift_detected": True,
        "details": {"last_backup_success": False}
    }
)

print(f"Resolved at L{result.resolution_level}: {result.action_taken}")
```

---

## Metrics & Monitoring

### Key Metrics

| Metric | Target | Description |
|--------|--------|-------------|
| L1 Resolution Rate | >70% | % handled by deterministic rules |
| L2 Resolution Rate | 15-25% | % handled by LLM |
| L3 Escalation Rate | <10% | % requiring human intervention |
| Overall Success Rate | >95% | % resolved successfully |
| Avg L1 Resolution Time | <100ms | Speed of rule matching |
| Avg L2 Resolution Time | <5s | Speed of LLM decision |
| Promotion Candidates | Growing | Health of learning loop |

### Getting Stats

```python
stats = healer.get_stats(days=30)

# Example output:
{
    "incidents": {
        "total_incidents": 1500,
        "l1_percentage": 73.2,
        "l2_percentage": 18.5,
        "l3_percentage": 8.3,
        "success_rate": 96.8,
        "avg_resolution_time_ms": 450
    },
    "learning": {
        "flywheel_status": "good",
        "promoted_rules_count": 5,
        "promotion_candidates": 3
    }
}
```

---

## Files Reference

| File | Lines | Description |
|------|-------|-------------|
| `auto_healer.py` | ~400 | Main orchestrator |
| `level1_deterministic.py` | ~450 | Rules engine |
| `level2_llm.py` | ~500 | LLM planner |
| `level3_escalation.py` | ~450 | Escalation handler |
| `learning_loop.py` | ~350 | Self-learning system |
| `incident_db.py` | ~500 | Incident database |

---

## Testing

```bash
# Run all auto-healer tests (24 tests)
python -m pytest tests/test_auto_healer.py -v

# Test specific levels
python -m pytest tests/test_auto_healer.py::TestLevel1Deterministic -v
python -m pytest tests/test_auto_healer.py::TestLevel3Escalation -v
python -m pytest tests/test_auto_healer.py::TestLearningLoop -v
```

---

**Maintained by:** MSP Automation Team
