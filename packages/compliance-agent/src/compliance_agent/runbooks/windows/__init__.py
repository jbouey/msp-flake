"""
Windows PowerShell Runbooks for HIPAA Compliance.

Executes remediation actions on Windows Server targets via WinRM.
Each runbook maps to a HIPAA Security Rule control.

Categories:
- Core: Patching, AV, Backup, Logging, Firewall, Encryption, AD Health (7 runbooks)
- Services: DNS, DHCP, Print Spooler, Time Service (4 runbooks)
- Security: Firewall, Audit, Lockout, Password, BitLocker, Defender (6 runbooks)
- Network: DNS Client, NIC, Profile, NetBIOS (4 runbooks)
- Storage: Disk Cleanup, Shadow Copy, Volume Health (3 runbooks)
- Updates: Windows Update, WSUS (2 runbooks)
- Active Directory: Computer Account (1 runbook)

Total: 27 runbooks
"""

from typing import Dict, List, Optional

from .executor import WindowsExecutor
from .runbooks import (
    WindowsRunbook,
    ExecutionConstraints,
    RUNBOOK_WIN_PATCH,
    RUNBOOK_WIN_AV,
    RUNBOOK_WIN_BACKUP,
    RUNBOOK_WIN_LOGGING,
    RUNBOOK_WIN_FIREWALL,
    RUNBOOK_WIN_ENCRYPTION,
    RUNBOOK_WIN_AD_HEALTH,
    RUNBOOKS as CORE_RUNBOOKS,
    get_runbook as get_core_runbook,
    list_runbooks as list_core_runbooks,
)

# Import new runbook categories
from .services import SERVICE_RUNBOOKS
from .security import SECURITY_RUNBOOKS
from .network import NETWORK_RUNBOOKS
from .storage import STORAGE_RUNBOOKS
from .updates import UPDATES_RUNBOOKS
from .active_directory import AD_RUNBOOKS


# =============================================================================
# Combined Runbook Registry
# =============================================================================

ALL_RUNBOOKS: Dict[str, WindowsRunbook] = {
    # Core runbooks (7)
    **CORE_RUNBOOKS,
    # Service runbooks (4)
    **SERVICE_RUNBOOKS,
    # Security runbooks (6)
    **SECURITY_RUNBOOKS,
    # Network runbooks (4)
    **NETWORK_RUNBOOKS,
    # Storage runbooks (3)
    **STORAGE_RUNBOOKS,
    # Updates runbooks (2)
    **UPDATES_RUNBOOKS,
    # AD runbooks (1)
    **AD_RUNBOOKS,
}


# =============================================================================
# Runbook Lookup Functions
# =============================================================================

def get_runbook(runbook_id: str) -> Optional[WindowsRunbook]:
    """Get runbook by ID from all categories."""
    return ALL_RUNBOOKS.get(runbook_id)


def list_runbooks(category: Optional[str] = None) -> List[Dict]:
    """
    List all available runbooks with metadata.

    Args:
        category: Optional filter by category (services, security, network, storage, updates, ad)
    """
    runbooks = []

    for rb in ALL_RUNBOOKS.values():
        info = {
            "id": rb.id,
            "name": rb.name,
            "description": rb.description,
            "version": rb.version,
            "category": _get_category(rb.id),
            "hipaa_controls": rb.hipaa_controls,
            "severity": rb.severity,
            "disruptive": rb.disruptive,
            "requires_reboot": rb.requires_reboot,
            "timeout_seconds": rb.timeout_seconds,
        }

        if category is None or info["category"] == category:
            runbooks.append(info)

    return runbooks


def _get_category(runbook_id: str) -> str:
    """Determine category from runbook ID."""
    if runbook_id.startswith("RB-WIN-SVC-"):
        return "services"
    elif runbook_id.startswith("RB-WIN-SEC-"):
        return "security"
    elif runbook_id.startswith("RB-WIN-NET-"):
        return "network"
    elif runbook_id.startswith("RB-WIN-STG-"):
        return "storage"
    elif runbook_id.startswith("RB-WIN-UPD-"):
        return "updates"
    elif runbook_id.startswith("RB-WIN-AD-"):
        return "ad"
    elif runbook_id.startswith("RB-WIN-PATCH-"):
        return "patching"
    elif runbook_id.startswith("RB-WIN-AV-"):
        return "antivirus"
    elif runbook_id.startswith("RB-WIN-BACKUP-"):
        return "backup"
    elif runbook_id.startswith("RB-WIN-LOGGING-"):
        return "logging"
    elif runbook_id.startswith("RB-WIN-FIREWALL-"):
        return "firewall"
    elif runbook_id.startswith("RB-WIN-ENCRYPTION-"):
        return "encryption"
    else:
        return "other"


def list_categories() -> List[Dict]:
    """List runbook categories with counts."""
    categories = {}

    for rb in ALL_RUNBOOKS.values():
        cat = _get_category(rb.id)
        if cat not in categories:
            categories[cat] = {"name": cat, "count": 0, "runbooks": []}
        categories[cat]["count"] += 1
        categories[cat]["runbooks"].append(rb.id)

    return list(categories.values())


def get_runbooks_by_check_type(check_type: str) -> List[WindowsRunbook]:
    """Get runbooks that handle a specific check type."""
    # Map check types to runbook IDs
    check_type_map = {
        "patching": ["RB-WIN-PATCH-001", "RB-WIN-UPD-001", "RB-WIN-UPD-002"],
        "antivirus": ["RB-WIN-AV-001", "RB-WIN-SEC-006"],
        "backup": ["RB-WIN-BACKUP-001", "RB-WIN-STG-002"],
        "logging": ["RB-WIN-LOGGING-001", "RB-WIN-SEC-002"],
        "firewall": ["RB-WIN-FIREWALL-001", "RB-WIN-SEC-001", "RB-WIN-NET-003"],
        "encryption": ["RB-WIN-ENCRYPTION-001", "RB-WIN-SEC-005"],
        "service_health": ["RB-WIN-SVC-001", "RB-WIN-SVC-002", "RB-WIN-SVC-003",
                          "RB-WIN-AD-001", "RB-WIN-AD-002", "RB-WIN-NET-001",
                          "RB-WIN-NET-002", "RB-WIN-NET-004", "RB-WIN-SEC-003",
                          "RB-WIN-SEC-004"],
        "ntp_sync": ["RB-WIN-SVC-004"],
        "windows_defender": ["RB-WIN-SEC-006"],
        "disk_space": ["RB-WIN-STG-001", "RB-WIN-STG-003"],
    }

    runbook_ids = check_type_map.get(check_type, [])
    return [ALL_RUNBOOKS[rb_id] for rb_id in runbook_ids if rb_id in ALL_RUNBOOKS]


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Executor
    'WindowsExecutor',

    # Base classes
    'WindowsRunbook',
    'ExecutionConstraints',

    # Core runbooks
    'RUNBOOK_WIN_PATCH',
    'RUNBOOK_WIN_AV',
    'RUNBOOK_WIN_BACKUP',
    'RUNBOOK_WIN_LOGGING',
    'RUNBOOK_WIN_FIREWALL',
    'RUNBOOK_WIN_ENCRYPTION',
    'RUNBOOK_WIN_AD_HEALTH',

    # Registries
    'ALL_RUNBOOKS',
    'CORE_RUNBOOKS',
    'SERVICE_RUNBOOKS',
    'SECURITY_RUNBOOKS',
    'NETWORK_RUNBOOKS',
    'STORAGE_RUNBOOKS',
    'UPDATES_RUNBOOKS',
    'AD_RUNBOOKS',

    # Functions
    'get_runbook',
    'list_runbooks',
    'list_categories',
    'get_runbooks_by_check_type',
]
