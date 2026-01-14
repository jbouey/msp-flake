"""Health and compliance scoring metrics for Central Command Dashboard.

Implements the scoring model:
- Connectivity Score (40% weight): check-in freshness, healing success, order execution
- Compliance Score (60% weight): patching, AV, backup, logging, firewall, encryption, network
- Overall Health = connectivity * 0.4 + compliance * 0.6

Health Thresholds:
- Critical (Red): 0-39
- Warning (Yellow): 40-79
- Healthy (Green): 80-100
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple

from .models import (
    HealthStatus,
    ConnectivityMetrics,
    ComplianceMetrics,
    HealthMetrics,
)


# =============================================================================
# CONSTANTS
# =============================================================================

HEALTH_THRESHOLDS = {
    "critical": {"max": 39, "color": "#FF3B30", "label": "Critical"},
    "warning": {"min": 40, "max": 79, "color": "#FF9500", "label": "Warning"},
    "healthy": {"min": 80, "color": "#34C759", "label": "Healthy"},
}

# Weight factors for overall health calculation
CONNECTIVITY_WEIGHT = 0.4
COMPLIANCE_WEIGHT = 0.6


# =============================================================================
# CHECK-IN FRESHNESS
# =============================================================================

def calculate_checkin_freshness(last_checkin: Optional[datetime]) -> int:
    """Calculate freshness score based on time since last check-in.

    Args:
        last_checkin: Timestamp of last appliance check-in (timezone-aware)

    Returns:
        Score from 0-100:
        - 100: < 5 minutes ago
        - 75: 5-15 minutes ago
        - 50: 15-60 minutes ago
        - 25: 1-4 hours ago
        - 0: > 4 hours ago or never
    """
    if last_checkin is None:
        return 0

    now = datetime.now(timezone.utc)

    # Ensure last_checkin is timezone-aware
    if last_checkin.tzinfo is None:
        last_checkin = last_checkin.replace(tzinfo=timezone.utc)

    age_minutes = (now - last_checkin).total_seconds() / 60

    if age_minutes < 5:
        return 100
    elif age_minutes < 15:
        return 75
    elif age_minutes < 60:
        return 50
    elif age_minutes < 240:  # 4 hours
        return 25
    else:
        return 0


def get_checkin_age_description(last_checkin: Optional[datetime]) -> str:
    """Get human-readable description of check-in age.

    Args:
        last_checkin: Timestamp of last check-in

    Returns:
        Human-readable string like "2 minutes ago" or "Never"
    """
    if last_checkin is None:
        return "Never"

    now = datetime.now(timezone.utc)

    if last_checkin.tzinfo is None:
        last_checkin = last_checkin.replace(tzinfo=timezone.utc)

    age_seconds = (now - last_checkin).total_seconds()

    if age_seconds < 60:
        return f"{int(age_seconds)} seconds ago"
    elif age_seconds < 3600:
        minutes = int(age_seconds / 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    elif age_seconds < 86400:
        hours = int(age_seconds / 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    else:
        days = int(age_seconds / 86400)
        return f"{days} day{'s' if days != 1 else ''} ago"


# =============================================================================
# CONNECTIVITY SCORE
# =============================================================================

def calculate_healing_success_rate(
    successful_heals: int,
    total_incidents: int
) -> float:
    """Calculate healing success rate.

    Args:
        successful_heals: Number of successfully healed incidents
        total_incidents: Total number of incidents

    Returns:
        Success rate as percentage (0-100)
    """
    if total_incidents == 0:
        return 100.0  # No incidents = perfect
    return (successful_heals / total_incidents) * 100


def calculate_order_execution_rate(
    executed_orders: int,
    total_orders: int
) -> float:
    """Calculate order execution rate.

    Args:
        executed_orders: Number of successfully executed orders
        total_orders: Total number of orders

    Returns:
        Execution rate as percentage (0-100)
    """
    if total_orders == 0:
        return 100.0  # No orders = perfect
    return (executed_orders / total_orders) * 100


def calculate_connectivity_score(
    last_checkin: Optional[datetime],
    successful_heals: int = 0,
    total_incidents: int = 0,
    executed_orders: int = 0,
    total_orders: int = 0,
) -> ConnectivityMetrics:
    """Calculate connectivity health metrics.

    Args:
        last_checkin: Timestamp of last check-in
        successful_heals: Number of successful auto-heals
        total_incidents: Total incident count
        executed_orders: Number of executed orders
        total_orders: Total order count

    Returns:
        ConnectivityMetrics with individual scores and overall score
    """
    checkin_freshness = calculate_checkin_freshness(last_checkin)
    healing_success_rate = calculate_healing_success_rate(successful_heals, total_incidents)
    order_execution_rate = calculate_order_execution_rate(executed_orders, total_orders)

    # Average of the three metrics
    score = (checkin_freshness + healing_success_rate + order_execution_rate) / 3

    return ConnectivityMetrics(
        checkin_freshness=checkin_freshness,
        healing_success_rate=round(healing_success_rate, 1),
        order_execution_rate=round(order_execution_rate, 1),
        score=round(score, 1),
    )


# =============================================================================
# COMPLIANCE SCORE
# =============================================================================

def calculate_compliance_score(
    patching: bool = False,
    antivirus: bool = False,
    backup: bool = False,
    logging: bool = False,
    firewall: bool = False,
    encryption: bool = False,
    network: bool = False,
) -> ComplianceMetrics:
    """Calculate compliance health metrics.

    Each check is binary (pass/fail) and contributes equally to the score.

    Args:
        patching: True if patch compliance is met
        antivirus: True if AV is compliant
        backup: True if backup is compliant
        logging: True if logging is compliant
        firewall: True if firewall is compliant
        encryption: True if encryption is compliant
        network: True if network security posture is compliant

    Returns:
        ComplianceMetrics with individual scores and overall score
    """
    # Convert booleans to 0 or 100
    patching_score = 100 if patching else 0
    antivirus_score = 100 if antivirus else 0
    backup_score = 100 if backup else 0
    logging_score = 100 if logging else 0
    firewall_score = 100 if firewall else 0
    encryption_score = 100 if encryption else 0
    network_score = 100 if network else 0

    # Average of the seven metrics
    score = (
        patching_score +
        antivirus_score +
        backup_score +
        logging_score +
        firewall_score +
        encryption_score +
        network_score
    ) / 7

    return ComplianceMetrics(
        patching=patching_score,
        antivirus=antivirus_score,
        backup=backup_score,
        logging=logging_score,
        firewall=firewall_score,
        encryption=encryption_score,
        network=network_score,
        score=round(score, 1),
    )


def compliance_from_dict(checks: Dict[str, bool]) -> ComplianceMetrics:
    """Create ComplianceMetrics from a dictionary of check results.

    Args:
        checks: Dictionary with keys patching, antivirus, backup, logging, firewall, encryption, network

    Returns:
        ComplianceMetrics instance
    """
    return calculate_compliance_score(
        patching=checks.get("patching", False),
        antivirus=checks.get("antivirus", False),
        backup=checks.get("backup", False),
        logging=checks.get("logging", False),
        firewall=checks.get("firewall", False),
        encryption=checks.get("encryption", False),
        network=checks.get("network", False),
    )


# =============================================================================
# OVERALL HEALTH
# =============================================================================

def get_health_status(score: float) -> HealthStatus:
    """Get health status category based on score.

    Args:
        score: Overall health score (0-100)

    Returns:
        HealthStatus enum value
    """
    if score < 40:
        return HealthStatus.CRITICAL
    elif score < 80:
        return HealthStatus.WARNING
    else:
        return HealthStatus.HEALTHY


def get_health_color(status: HealthStatus) -> str:
    """Get iOS color for health status.

    Args:
        status: HealthStatus enum value

    Returns:
        Hex color string
    """
    colors = {
        HealthStatus.CRITICAL: "#FF3B30",  # iOS red
        HealthStatus.WARNING: "#FF9500",   # iOS orange
        HealthStatus.HEALTHY: "#34C759",   # iOS green
    }
    return colors.get(status, "#8E8E93")  # iOS gray as fallback


def calculate_overall_health(
    connectivity: ConnectivityMetrics,
    compliance: ComplianceMetrics,
) -> HealthMetrics:
    """Calculate overall health score from connectivity and compliance.

    Formula: overall = (connectivity.score * 0.4) + (compliance.score * 0.6)

    Args:
        connectivity: Connectivity metrics
        compliance: Compliance metrics

    Returns:
        Complete HealthMetrics with status
    """
    overall = (connectivity.score * CONNECTIVITY_WEIGHT) + (compliance.score * COMPLIANCE_WEIGHT)
    overall = round(overall, 1)
    status = get_health_status(overall)

    return HealthMetrics(
        connectivity=connectivity,
        compliance=compliance,
        overall=overall,
        status=status,
    )


def calculate_health_from_raw(
    last_checkin: Optional[datetime] = None,
    successful_heals: int = 0,
    total_incidents: int = 0,
    executed_orders: int = 0,
    total_orders: int = 0,
    patching: bool = False,
    antivirus: bool = False,
    backup: bool = False,
    logging: bool = False,
    firewall: bool = False,
    encryption: bool = False,
) -> HealthMetrics:
    """Calculate complete health metrics from raw data.

    Convenience function that combines connectivity and compliance calculation.

    Args:
        last_checkin: Timestamp of last check-in
        successful_heals: Number of successful heals
        total_incidents: Total incident count
        executed_orders: Number of executed orders
        total_orders: Total order count
        patching: Patch compliance status
        antivirus: AV compliance status
        backup: Backup compliance status
        logging: Logging compliance status
        firewall: Firewall compliance status
        encryption: Encryption compliance status

    Returns:
        Complete HealthMetrics
    """
    connectivity = calculate_connectivity_score(
        last_checkin=last_checkin,
        successful_heals=successful_heals,
        total_incidents=total_incidents,
        executed_orders=executed_orders,
        total_orders=total_orders,
    )

    compliance = calculate_compliance_score(
        patching=patching,
        antivirus=antivirus,
        backup=backup,
        logging=logging,
        firewall=firewall,
        encryption=encryption,
    )

    return calculate_overall_health(connectivity, compliance)


# =============================================================================
# AGGREGATION HELPERS
# =============================================================================

def aggregate_health_scores(health_list: list[HealthMetrics]) -> HealthMetrics:
    """Aggregate multiple health scores into a single score.

    Used for calculating client-level health from multiple appliances.

    Args:
        health_list: List of HealthMetrics from individual appliances

    Returns:
        Aggregated HealthMetrics (averages of all scores)
    """
    if not health_list:
        # Return worst-case health if no data
        return calculate_health_from_raw()

    n = len(health_list)

    # Average connectivity metrics
    avg_checkin = sum(h.connectivity.checkin_freshness for h in health_list) / n
    avg_healing = sum(h.connectivity.healing_success_rate for h in health_list) / n
    avg_order = sum(h.connectivity.order_execution_rate for h in health_list) / n
    avg_conn_score = sum(h.connectivity.score for h in health_list) / n

    connectivity = ConnectivityMetrics(
        checkin_freshness=int(avg_checkin),
        healing_success_rate=round(avg_healing, 1),
        order_execution_rate=round(avg_order, 1),
        score=round(avg_conn_score, 1),
    )

    # Average compliance metrics
    avg_patching = sum(h.compliance.patching for h in health_list) / n
    avg_av = sum(h.compliance.antivirus for h in health_list) / n
    avg_backup = sum(h.compliance.backup for h in health_list) / n
    avg_logging = sum(h.compliance.logging for h in health_list) / n
    avg_firewall = sum(h.compliance.firewall for h in health_list) / n
    avg_encryption = sum(h.compliance.encryption for h in health_list) / n
    avg_comp_score = sum(h.compliance.score for h in health_list) / n

    compliance = ComplianceMetrics(
        patching=int(avg_patching),
        antivirus=int(avg_av),
        backup=int(avg_backup),
        logging=int(avg_logging),
        firewall=int(avg_firewall),
        encryption=int(avg_encryption),
        score=round(avg_comp_score, 1),
    )

    return calculate_overall_health(connectivity, compliance)


def get_worst_health(health_list: list[HealthMetrics]) -> Tuple[HealthMetrics, int]:
    """Find the worst health score in a list.

    Args:
        health_list: List of HealthMetrics

    Returns:
        Tuple of (worst HealthMetrics, index in list)
    """
    if not health_list:
        return calculate_health_from_raw(), -1

    worst_idx = 0
    worst_score = health_list[0].overall

    for i, health in enumerate(health_list[1:], 1):
        if health.overall < worst_score:
            worst_score = health.overall
            worst_idx = i

    return health_list[worst_idx], worst_idx
