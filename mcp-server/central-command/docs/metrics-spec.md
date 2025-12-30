# Health Metrics Specification

This document defines the health scoring model used by Central Command Dashboard.

## Overview

Health scores provide a unified view of appliance and client status across two dimensions:
- **Connectivity** (40% weight): Is the appliance online and functioning?
- **Compliance** (60% weight): Is the appliance meeting HIPAA requirements?

## Overall Health Score

```
overall = (connectivity.score * 0.4) + (compliance.score * 0.6)
```

### Health Thresholds

| Range | Status | Color | Description |
|-------|--------|-------|-------------|
| 0-39 | Critical | #FF3B30 (iOS Red) | Immediate attention required |
| 40-79 | Warning | #FF9500 (iOS Orange) | Issues detected, monitoring |
| 80-100 | Healthy | #34C759 (iOS Green) | Operating normally |

## Connectivity Score (40% Weight)

Measures operational health of the appliance.

### Components

| Metric | Weight | Description |
|--------|--------|-------------|
| Check-in Freshness | 33.3% | Time since last appliance check-in |
| Healing Success Rate | 33.3% | Successful auto-heals / total incidents |
| Order Execution Rate | 33.3% | Executed orders / total orders |

### Check-in Freshness Calculation

```python
def calculate_checkin_freshness(last_checkin: datetime) -> int:
    """Returns 0-100 based on time since last check-in."""
    age_minutes = (now - last_checkin).total_seconds() / 60

    if age_minutes < 5:
        return 100    # Real-time
    elif age_minutes < 15:
        return 75     # Recent
    elif age_minutes < 60:
        return 50     # Stale
    elif age_minutes < 240:  # 4 hours
        return 25     # Very stale
    else:
        return 0      # Offline
```

### Healing Success Rate

```python
def calculate_healing_success_rate(successful_heals: int, total_incidents: int) -> float:
    """Returns 0-100 as percentage."""
    if total_incidents == 0:
        return 100.0  # No incidents = perfect
    return (successful_heals / total_incidents) * 100
```

### Order Execution Rate

```python
def calculate_order_execution_rate(executed_orders: int, total_orders: int) -> float:
    """Returns 0-100 as percentage."""
    if total_orders == 0:
        return 100.0  # No orders = perfect
    return (executed_orders / total_orders) * 100
```

### Connectivity Score Formula

```python
connectivity_score = (checkin_freshness + healing_success_rate + order_execution_rate) / 3
```

## Compliance Score (60% Weight)

Measures HIPAA compliance posture.

### Components

All compliance checks are binary (pass/fail = 100/0).

| Check | HIPAA Citation | Description |
|-------|----------------|-------------|
| Patching | §164.308(a)(5)(ii)(B) | Critical security patches applied |
| Antivirus | §164.308(a)(5)(ii)(B) | AV enabled, signatures current |
| Backup | §164.308(a)(7)(ii)(A) | Backup job successful within SLA |
| Logging | §164.312(b) | Audit logging enabled and collecting |
| Firewall | §164.312(e)(1) | Firewall enabled, rules compliant |
| Encryption | §164.312(a)(2)(iv) | BitLocker/disk encryption enabled |

### Compliance Score Formula

```python
compliance_score = (patching + antivirus + backup + logging + firewall + encryption) / 6
```

Where each check is 0 or 100.

### Example Calculations

**Fully Compliant:**
```
compliance_score = (100 + 100 + 100 + 100 + 100 + 100) / 6 = 100
```

**3 of 6 Checks Passing:**
```
compliance_score = (100 + 100 + 0 + 100 + 0 + 0) / 6 = 50
```

## Aggregation

When calculating client-level health from multiple appliances:

```python
def aggregate_health_scores(health_list: list[HealthMetrics]) -> HealthMetrics:
    """Average all component scores across appliances."""
    n = len(health_list)

    # Average each connectivity metric
    avg_checkin = sum(h.connectivity.checkin_freshness for h in health_list) / n
    avg_healing = sum(h.connectivity.healing_success_rate for h in health_list) / n
    avg_order = sum(h.connectivity.order_execution_rate for h in health_list) / n

    # Average each compliance metric
    avg_patching = sum(h.compliance.patching for h in health_list) / n
    # ... etc for all 6 checks

    # Recalculate overall from averages
    return calculate_overall_health(connectivity, compliance)
```

## Data Sources

### Check-in Freshness
- Source: `appliances.last_checkin` column
- Updated: Every appliance check-in (default 5-minute interval)

### Healing Success Rate
- Source: `incidents` table
- Numerator: COUNT where `resolved_at IS NOT NULL`
- Denominator: COUNT all incidents
- Window: Last 30 days

### Order Execution Rate
- Source: `orders` table
- Numerator: COUNT where `status = 'executed'`
- Denominator: COUNT all orders
- Window: Last 30 days

### Compliance Checks
- Source: `incidents.drift_data` JSONB column
- Keys: `patching_compliant`, `av_compliant`, `backup_compliant`, etc.
- Updated: On each drift check or incident

## API Response Format

```json
{
  "connectivity": {
    "checkin_freshness": 100,
    "healing_success_rate": 90.0,
    "order_execution_rate": 95.0,
    "score": 95.0
  },
  "compliance": {
    "patching": 100,
    "antivirus": 100,
    "backup": 0,
    "logging": 100,
    "firewall": 100,
    "encryption": 100,
    "score": 83.3
  },
  "overall": 88.0,
  "status": "healthy"
}
```

## UI Display

### Health Gauge
- Circular progress indicator
- Color based on status threshold
- Percentage displayed in center

### Compliance Breakdown
- List of 6 checks with pass/fail indicators
- Failed checks shown in red with remediation link

### Connectivity Status
- Last check-in timestamp with relative time
- Color-coded freshness indicator
