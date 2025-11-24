# Data Flywheel - Self-Learning System

**Last Updated:** 2025-11-23
**Module:** `learning_loop.py`, `incident_db.py`
**Status:** Production Ready

---

## Overview

The Data Flywheel is a self-learning system that continuously improves incident resolution by:

1. **Tracking** all L2 LLM decisions and their outcomes
2. **Identifying** patterns with consistent successful resolutions
3. **Promoting** successful patterns from L2 (LLM) to L1 (deterministic rules)
4. **Reducing** latency and cost over time

```
┌─────────────────────────────────────────────────────────────────┐
│                    Data Flywheel Cycle                          │
│                                                                  │
│    ┌──────────┐     ┌──────────┐     ┌──────────┐              │
│    │ Incident │────▶│   L2     │────▶│ Outcome  │              │
│    │  Occurs  │     │   LLM    │     │ Tracked  │              │
│    └──────────┘     └──────────┘     └────┬─────┘              │
│                                           │                     │
│    ┌──────────┐     ┌──────────┐     ┌────▼─────┐              │
│    │   L1     │◀────│ Promote  │◀────│ Pattern  │              │
│    │  Rules   │     │  Rule    │     │ Analysis │              │
│    └──────────┘     └──────────┘     └──────────┘              │
│                                                                  │
│    Result: L1 handles more incidents → faster, cheaper          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Components

### 1. Incident Database (`incident_db.py`)

SQLite database tracking all incidents and their resolutions.

#### Schema

```sql
-- Incidents table
CREATE TABLE incidents (
    id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL,
    host_id TEXT NOT NULL,
    incident_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    pattern_signature TEXT,
    resolution_level TEXT,  -- LEVEL1_DETERMINISTIC, LEVEL2_LLM, LEVEL3_HUMAN
    resolution_action TEXT,
    outcome TEXT,           -- SUCCESS, FAILURE, PARTIAL, ESCALATED
    resolution_time_ms INTEGER,
    created_at TEXT,
    resolved_at TEXT,
    raw_data TEXT
);

-- Pattern statistics (auto-updated)
CREATE TABLE pattern_stats (
    pattern_signature TEXT PRIMARY KEY,
    total_occurrences INTEGER DEFAULT 0,
    l1_resolutions INTEGER DEFAULT 0,
    l2_resolutions INTEGER DEFAULT 0,
    l3_resolutions INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    avg_resolution_time_ms REAL DEFAULT 0,
    last_seen TEXT,
    promotion_eligible BOOLEAN DEFAULT 0
);
```

#### Pattern Signatures

Each incident generates a unique pattern signature based on:
- Incident type (e.g., `patching`, `backup`, `logging`)
- Key attributes from the incident data
- Hash of normalized incident structure

```python
def _generate_pattern_signature(self, incident: Incident) -> str:
    """Generate unique pattern signature for incident grouping."""
    key_parts = [
        incident.incident_type,
        incident.severity,
        str(sorted(incident.data.get("check_type", ""))),
    ]
    signature_input = "|".join(key_parts)
    return hashlib.sha256(signature_input.encode()).hexdigest()[:16]
```

### 2. Self-Learning System (`learning_loop.py`)

Analyzes incident patterns and promotes successful L2 resolutions to L1 rules.

#### Promotion Criteria

| Criterion | Default | Description |
|-----------|---------|-------------|
| `min_occurrences` | 5 | Minimum times pattern must occur |
| `min_l2_resolutions` | 3 | Minimum L2 (LLM) resolutions |
| `min_success_rate` | 90% | Minimum success rate |
| `max_avg_resolution_time_ms` | 30,000 | Max average resolution time |

```python
@dataclass
class PromotionConfig:
    min_occurrences: int = 5
    min_l2_resolutions: int = 3
    min_success_rate: float = 0.9
    max_avg_resolution_time_ms: int = 30000
    check_interval_hours: int = 24
    auto_promote: bool = False  # Require human approval by default
```

#### Promotion Workflow

```
1. Find Candidates
   └── Query patterns meeting all criteria

2. Calculate Confidence
   └── Base: success_rate
   └── Bonus: occurrence count (up to 10%)
   └── Bonus: action consistency (up to 10%)
   └── Penalty: staleness (up to -20%)

3. Generate Rule
   └── Build L1 rule conditions from sample incidents
   └── Extract HIPAA control mappings
   └── Set priority (50 = medium, below built-in rules)

4. Promote
   └── Save rule to YAML file
   └── Record promotion in database
   └── Update pattern statistics
```

---

## API Reference

### IncidentDatabase

```python
from compliance_agent.incident_db import IncidentDatabase, Incident

# Initialize
db = IncidentDatabase("/var/lib/msp/incidents.db")

# Record incident
incident = Incident(
    id="INC-001",
    site_id="clinic-001",
    host_id="server-01",
    incident_type="patching",
    severity="high",
    data={"check_type": "critical_patches", "drift_detected": True}
)
db.record_incident(incident)

# Update resolution
db.update_resolution(
    incident_id="INC-001",
    resolution_level=ResolutionLevel.LEVEL2_LLM,
    resolution_action="apply_patches",
    outcome=IncidentOutcome.SUCCESS,
    resolution_time_ms=2500
)

# Get pattern context (for L2 LLM)
context = db.get_pattern_context("abc123def456")
# Returns: recent_incidents, successful_actions, failure_patterns

# Get promotion candidates
candidates = db.get_promotion_candidates()
# Returns patterns meeting promotion criteria

# Get statistics
stats = db.get_stats_summary(days=30)
# Returns: total_incidents, l1/l2/l3 percentages, success_rate, avg_time
```

### SelfLearningSystem

```python
from compliance_agent.learning_loop import SelfLearningSystem, PromotionConfig

# Initialize with custom config
config = PromotionConfig(
    min_occurrences=10,
    min_success_rate=0.95,
    auto_promote=False
)
learning = SelfLearningSystem(incident_db, config)

# Find promotion candidates
candidates = learning.find_promotion_candidates()
for candidate in candidates:
    print(f"Pattern: {candidate.pattern_signature}")
    print(f"Confidence: {candidate.confidence_score:.2f}")
    print(f"Action: {candidate.recommended_action}")
    print(f"Reason: {candidate.promotion_reason}")

# Generate rule (preview)
rule = learning.generate_rule(candidate)
print(rule.to_yaml())

# Promote pattern to L1 (creates YAML file)
rule = learning.promote_pattern(candidate, approved_by="admin@clinic.com")

# Get promotion report (for review)
report = learning.get_promotion_report()

# Get learning metrics (flywheel health)
metrics = learning.get_learning_metrics(days=30)
print(f"Flywheel status: {metrics['flywheel_status']}")
print(f"L1 percentage: {metrics['resolution_breakdown']['l1_percentage']}%")
```

---

## Promotion Candidates

### PromotionCandidate Structure

```python
@dataclass
class PromotionCandidate:
    pattern_signature: str      # Unique pattern identifier
    stats: PatternStats         # Aggregated statistics
    sample_incidents: List[Dict]  # Recent incident examples
    recommended_action: str     # Most common successful action
    action_params: Dict         # Parameters for the action
    confidence_score: float     # 0.0 - 1.0 confidence
    promotion_reason: str       # Human-readable explanation
```

### Confidence Score Calculation

```python
confidence = (
    base_confidence           # success_rate (e.g., 0.92)
    + occurrence_bonus        # min(occurrences/50, 0.10)
    + consistency_bonus       # action_consistency * 0.10
    - recency_penalty         # min(days_since_last/30, 0.20)
)
# Clamped to [0.0, 1.0]
```

### Example Candidate

```json
{
  "pattern_signature": "a1b2c3d4e5f6",
  "stats": {
    "total_occurrences": 15,
    "l1_resolutions": 0,
    "l2_resolutions": 12,
    "l3_resolutions": 3,
    "success_count": 14,
    "failure_count": 1,
    "success_rate": 0.933,
    "avg_resolution_time_ms": 2450
  },
  "recommended_action": "restart_logging_service",
  "confidence_score": 0.95,
  "promotion_reason": "Pattern seen 15 times with 93.3% success rate. 12 L2 resolutions with consistent action. Confidence: 0.95"
}
```

---

## Generated Rules

When a pattern is promoted, it generates a Level 1 deterministic rule:

### Rule Structure

```yaml
id: L1-PROMOTED-A1B2C3D4
name: "Promoted: restart_logging_service"
description: "Auto-promoted from L2. Pattern seen 15 times with 93.3% success rate."
enabled: true
priority: 50
source: promoted

conditions:
  - field: incident_type
    operator: equals
    value: logging
  - field: check_type
    operator: equals
    value: audit_logging
  - field: drift_detected
    operator: equals
    value: true

action: restart_logging_service
action_params: {}

hipaa_controls:
  - "164.312(b)"

cooldown_seconds: 300
max_retries: 1

_promotion_metadata:
  promoted_at: "2025-11-23T14:32:01Z"
  promoted_by: "admin@clinic.com"
  confidence_score: 0.95
  promotion_reason: "Pattern seen 15 times with 93.3% success rate..."
  sample_incident_count: 10
  stats:
    total_occurrences: 15
    success_rate: 0.933
    l2_resolutions: 12
```

### Rule Storage

- **Location:** `/etc/msp/rules/promoted/`
- **Format:** YAML files named `{rule_id}.yaml`
- **Loading:** Automatically loaded by L1 deterministic engine on startup

---

## Learning Metrics

### Flywheel Health Assessment

```python
metrics = learning.get_learning_metrics(days=30)
```

Returns:

```json
{
  "period_days": 30,
  "total_incidents": 450,
  "resolution_breakdown": {
    "l1_percentage": 72.5,
    "l2_percentage": 22.0,
    "l3_percentage": 5.5
  },
  "success_rate": 96.2,
  "avg_resolution_time_ms": 1250,
  "promoted_rules_count": 8,
  "promotion_candidates": 3,
  "flywheel_status": "good"
}
```

### Flywheel Status Levels

| Status | L1 % | Success Rate | Description |
|--------|------|--------------|-------------|
| `excellent` | ≥70% | ≥95% | Flywheel is highly effective |
| `good` | ≥50% | ≥85% | Flywheel is working well |
| `developing` | ≥30% | ≥70% | Flywheel is learning |
| `needs_attention` | <30% | <70% | Review L2 decisions and rules |

---

## Configuration

### Environment Variables

```bash
# Database location
INCIDENT_DB_PATH="/var/lib/msp/incidents.db"

# Promotion output directory
PROMOTION_OUTPUT_DIR="/etc/msp/rules/promoted"

# Auto-promotion (default: false, requires human approval)
AUTO_PROMOTE_RULES="false"
```

### NixOS Configuration

```nix
services.msp-compliance-agent = {
  enable = true;

  learningLoop = {
    enable = true;
    minOccurrences = 5;
    minSuccessRate = 0.9;
    checkIntervalHours = 24;
    autoPromote = false;  # Require human approval
  };

  incidentDb = {
    path = "/var/lib/msp/incidents.db";
    retentionDays = 365;
  };
};
```

---

## Integration with Auto-Healer

### Flow

```
Incident
    │
    ▼
┌──────────────────┐
│   Auto-Healer    │
│                  │
│  1. Try L1 rules │◀─────────────────────┐
│  2. Fall to L2   │                      │
│  3. Fall to L3   │                      │
└────────┬─────────┘                      │
         │                                │
         ▼                                │
┌──────────────────┐              ┌───────┴────────┐
│  Incident DB     │              │ Learning Loop  │
│                  │──────────────▶                │
│  Record outcome  │              │ Analyze & Promote
└──────────────────┘              └────────────────┘
```

### Code Integration

```python
# In auto_healer.py
async def handle_incident(self, incident: Incident) -> HealingResult:
    # Record incident
    self.incident_db.record_incident(incident)

    # Try L1
    l1_match = self.l1_engine.match(incident)
    if l1_match:
        result = await self._execute_l1(l1_match)
        self.incident_db.update_resolution(
            incident.id,
            ResolutionLevel.LEVEL1_DETERMINISTIC,
            l1_match.rule.action,
            result.outcome
        )
        return result

    # Try L2 (with context from incident DB)
    context = self.incident_db.get_pattern_context(
        incident.pattern_signature
    )
    l2_decision = await self.l2_planner.plan(incident, context)
    # ... execute and record
```

---

## Testing

### Unit Tests

```bash
# Test auto-healer (includes learning loop)
python -m pytest tests/test_auto_healer.py -v -k "learning"

# Test incident database
python -m pytest tests/test_auto_healer.py -v -k "incident_db"

# Test promotion
python -m pytest tests/test_auto_healer.py -v -k "promotion"
```

### Integration Tests

```bash
# Test data flywheel scenarios
python -m pytest tests/test_auto_healer_integration.py -v -k "flywheel"

# Test pattern tracking
python -m pytest tests/test_auto_healer_integration.py -v -k "pattern"
```

### Key Test Cases

| Test | Description |
|------|-------------|
| `test_pattern_tracking` | Verifies pattern signatures are generated and tracked |
| `test_promotion_eligibility` | Tests promotion criteria evaluation |
| `test_rule_generation` | Validates L1 rule generation from L2 patterns |
| `test_data_flywheel_improvement` | End-to-end flywheel cycle test |
| `test_confidence_scoring` | Tests confidence calculation logic |

---

## Best Practices

### 1. Start with Human Approval

```python
config = PromotionConfig(auto_promote=False)
```

Review all promotion candidates before enabling auto-promotion.

### 2. Monitor Flywheel Health

```python
metrics = learning.get_learning_metrics(days=30)
if metrics['flywheel_status'] == 'needs_attention':
    # Review L2 decisions and rule coverage
    report = learning.get_promotion_report()
```

### 3. Review Promoted Rules

Check `/etc/msp/rules/promoted/` periodically to ensure promoted rules are appropriate.

### 4. Set Appropriate Thresholds

- **High-risk environments:** Higher `min_occurrences`, `min_success_rate`
- **New deployments:** Lower thresholds to accelerate learning
- **Stable environments:** Higher thresholds for quality

### 5. Track Metrics Over Time

```python
# Weekly metrics comparison
week1 = learning.get_learning_metrics(days=7)
week2 = learning.get_learning_metrics(days=14)  # Compare trend
```

---

## Troubleshooting

### No Promotion Candidates

**Causes:**
- Not enough incidents (< `min_occurrences`)
- Low success rate (< `min_success_rate`)
- No L2 resolutions (all handled by L1 or L3)

**Solutions:**
- Lower thresholds temporarily
- Review L2 planner effectiveness
- Check incident recording

### Flywheel Not Improving

**Causes:**
- L2 decisions are inconsistent
- Patterns are too specific (not grouping well)
- Promoted rules have low priority

**Solutions:**
- Review L2 decision quality
- Adjust pattern signature generation
- Increase promoted rule priority

### High L3 Escalation Rate

**Causes:**
- L1 rules don't cover common patterns
- L2 planner can't resolve incidents
- New incident types not seen before

**Solutions:**
- Review L3 escalations for patterns
- Manually create L1 rules for common L3 cases
- Improve L2 planner prompts/context

---

## Related Documentation

- [AUTO_HEALING.md](./AUTO_HEALING.md) - Three-tier architecture overview
- [TECH_STACK.md](./TECH_STACK.md) - Full technology stack
- [TESTING.md](./TESTING.md) - Test guide

---

**Maintained by:** MSP Automation Team
