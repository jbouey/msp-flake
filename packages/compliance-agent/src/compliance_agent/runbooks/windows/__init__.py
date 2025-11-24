"""
Windows PowerShell Runbooks for HIPAA Compliance.

Executes remediation actions on Windows Server targets via WinRM.
Each runbook maps to a HIPAA Security Rule control.
"""

from .executor import WindowsExecutor
from .runbooks import (
    RUNBOOK_WIN_PATCH,
    RUNBOOK_WIN_AV,
    RUNBOOK_WIN_BACKUP,
    RUNBOOK_WIN_LOGGING,
    RUNBOOK_WIN_FIREWALL,
    RUNBOOK_WIN_ENCRYPTION,
    RUNBOOK_WIN_AD_HEALTH,
    get_runbook,
    list_runbooks,
)

__all__ = [
    'WindowsExecutor',
    'RUNBOOK_WIN_PATCH',
    'RUNBOOK_WIN_AV',
    'RUNBOOK_WIN_BACKUP',
    'RUNBOOK_WIN_LOGGING',
    'RUNBOOK_WIN_FIREWALL',
    'RUNBOOK_WIN_ENCRYPTION',
    'RUNBOOK_WIN_AD_HEALTH',
    'get_runbook',
    'list_runbooks',
]
