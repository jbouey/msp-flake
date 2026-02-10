"""
Workstation Compliance Checks via WMI.

Implements 5 critical workstation controls via WMI/PowerShell queries:
1. BitLocker encryption status
2. Windows Defender status
3. Patch status
4. Firewall status
5. Screen lock policy

Runs agentlessly from appliance against discovered workstations.

HIPAA Control Mappings:
- §164.312(a)(2)(iv) - Encryption and Decryption (BitLocker)
- §164.308(a)(5)(ii)(B) - Protection from Malicious Software (Defender, Patches)
- §164.312(a)(1) - Access Control (Firewall)
- §164.312(a)(2)(iii) - Automatic Logoff (Screen Lock)
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ComplianceStatus(str, Enum):
    """Compliance check status values."""
    COMPLIANT = "compliant"
    DRIFTED = "drifted"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass
class CheckResult:
    """Result of a single compliance check."""

    check_type: str
    hostname: str
    status: ComplianceStatus
    compliant: bool
    details: Dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: Optional[str] = None
    hipaa_controls: List[str] = field(default_factory=list)
    duration_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for evidence/API."""
        return {
            "check_type": self.check_type,
            "hostname": self.hostname,
            "status": self.status.value,
            "compliant": self.compliant,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
            "error": self.error,
            "hipaa_controls": self.hipaa_controls,
            "duration_ms": self.duration_ms,
        }


@dataclass
class WorkstationComplianceResult:
    """Aggregate compliance result for a single workstation."""

    hostname: str
    ip_address: Optional[str]
    checks: List[CheckResult]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    overall_status: ComplianceStatus = ComplianceStatus.UNKNOWN

    def __post_init__(self):
        """Calculate overall status from individual checks."""
        if not self.checks:
            self.overall_status = ComplianceStatus.UNKNOWN
        elif all(c.compliant for c in self.checks):
            self.overall_status = ComplianceStatus.COMPLIANT
        elif any(c.status == ComplianceStatus.ERROR for c in self.checks):
            self.overall_status = ComplianceStatus.ERROR
        else:
            self.overall_status = ComplianceStatus.DRIFTED

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for evidence/API."""
        return {
            "hostname": self.hostname,
            "ip_address": self.ip_address,
            "overall_status": self.overall_status.value,
            "timestamp": self.timestamp.isoformat(),
            "checks": {c.check_type: c.to_dict() for c in self.checks},
            "compliant_count": sum(1 for c in self.checks if c.compliant),
            "total_checks": len(self.checks),
        }


class WorkstationComplianceChecker:
    """
    Run compliance checks on Windows workstations via WMI.

    Uses WinRM to execute PowerShell scripts that query WMI classes.
    All checks are read-only (detect only, no remediation).
    """

    # =========================================================================
    # PowerShell Scripts for Each Check
    # =========================================================================

    BITLOCKER_CHECK = '''
$volumes = Get-WmiObject -Namespace "root\\CIMV2\\Security\\MicrosoftVolumeEncryption" `
    -Class Win32_EncryptableVolume -ErrorAction SilentlyContinue

$results = @()
foreach ($vol in $volumes) {
    $status = $vol.GetProtectionStatus()
    $encryptionStatus = $vol.GetConversionStatus()

    $results += @{
        drive_letter = $vol.DriveLetter
        protection_status = switch ($status.ProtectionStatus) {
            0 { "Off" }
            1 { "On" }
            2 { "Unknown" }
            default { "Unknown" }
        }
        encryption_percentage = $encryptionStatus.EncryptionPercentage
        encryption_status = switch ($encryptionStatus.ConversionStatus) {
            0 { "FullyDecrypted" }
            1 { "FullyEncrypted" }
            2 { "EncryptionInProgress" }
            3 { "DecryptionInProgress" }
            4 { "EncryptionPaused" }
            5 { "DecryptionPaused" }
            default { "Unknown" }
        }
        key_protectors = ($vol.GetKeyProtectors(0)).VolumeKeyProtectorID.Count
    }
}

# Check system drive specifically
$systemDrive = $env:SystemDrive
$systemVol = $results | Where-Object { $_.drive_letter -eq $systemDrive }

@{
    volumes = $results
    system_drive = $systemDrive
    system_drive_encrypted = if ($systemVol) { $systemVol.protection_status -eq "On" } else { $false }
    compliant = if ($systemVol) { $systemVol.protection_status -eq "On" -and $systemVol.encryption_percentage -eq 100 } else { $false }
} | ConvertTo-Json -Depth 3
'''

    DEFENDER_CHECK = '''
$mpStatus = Get-MpComputerStatus -ErrorAction SilentlyContinue

if ($mpStatus) {
    $signatureAge = (Get-Date) - $mpStatus.AntivirusSignatureLastUpdated

    @{
        antivirus_enabled = $mpStatus.AntivirusEnabled
        realtime_protection = $mpStatus.RealTimeProtectionEnabled
        antispyware_enabled = $mpStatus.AntispywareEnabled
        behavior_monitor = $mpStatus.BehaviorMonitorEnabled
        ioav_protection = $mpStatus.IoavProtectionEnabled
        on_access_protection = $mpStatus.OnAccessProtectionEnabled
        signature_version = $mpStatus.AntivirusSignatureVersion
        signature_last_updated = $mpStatus.AntivirusSignatureLastUpdated.ToString("o")
        signature_age_days = [math]::Round($signatureAge.TotalDays, 1)
        quick_scan_age_days = if ($mpStatus.QuickScanEndTime) {
            [math]::Round(((Get-Date) - $mpStatus.QuickScanEndTime).TotalDays, 1)
        } else { -1 }
        full_scan_age_days = if ($mpStatus.FullScanEndTime) {
            [math]::Round(((Get-Date) - $mpStatus.FullScanEndTime).TotalDays, 1)
        } else { -1 }
        compliant = $mpStatus.AntivirusEnabled -and $mpStatus.RealTimeProtectionEnabled -and ($signatureAge.TotalDays -lt 7)
    } | ConvertTo-Json
} else {
    @{
        error = "Windows Defender not available"
        compliant = $false
    } | ConvertTo-Json
}
'''

    PATCHES_CHECK = '''
$hotfixes = Get-HotFix | Sort-Object InstalledOn -Descending -ErrorAction SilentlyContinue
$lastPatch = $hotfixes | Select-Object -First 1

$daysSinceLastPatch = if ($lastPatch.InstalledOn) {
    [math]::Round(((Get-Date) - $lastPatch.InstalledOn).TotalDays, 1)
} else { -1 }

# Check for pending updates via Windows Update
$updateSession = New-Object -ComObject Microsoft.Update.Session -ErrorAction SilentlyContinue
$updateSearcher = $updateSession.CreateUpdateSearcher()
$pendingUpdates = @()
try {
    $searchResult = $updateSearcher.Search("IsInstalled=0")
    $pendingUpdates = $searchResult.Updates | ForEach-Object {
        @{
            title = $_.Title
            severity = $_.MsrcSeverity
            kb_article_ids = $_.KBArticleIDs -join ","
        }
    }
} catch {
    # Ignore search errors
}

$criticalPending = ($pendingUpdates | Where-Object { $_.severity -eq "Critical" }).Count
$importantPending = ($pendingUpdates | Where-Object { $_.severity -eq "Important" }).Count

@{
    total_hotfixes = $hotfixes.Count
    last_patch_date = if ($lastPatch.InstalledOn) { $lastPatch.InstalledOn.ToString("o") } else { $null }
    last_patch_kb = $lastPatch.HotFixID
    days_since_last_patch = $daysSinceLastPatch
    pending_updates_count = $pendingUpdates.Count
    critical_pending = $criticalPending
    important_pending = $importantPending
    # Compliant if patched within 30 days and no critical pending
    compliant = ($daysSinceLastPatch -lt 30 -and $daysSinceLastPatch -ge 0) -and ($criticalPending -eq 0)
    recent_patches = ($hotfixes | Select-Object -First 5 | ForEach-Object {
        @{ kb = $_.HotFixID; installed = $_.InstalledOn.ToString("o"); description = $_.Description }
    })
} | ConvertTo-Json -Depth 3
'''

    FIREWALL_CHECK = '''
$profiles = Get-NetFirewallProfile -ErrorAction SilentlyContinue

$result = @{
    profiles = @{}
    all_enabled = $true
}

foreach ($profile in $profiles) {
    $enabled = $profile.Enabled -eq $true
    $result.profiles[$profile.Name] = @{
        enabled = $enabled
        default_inbound_action = $profile.DefaultInboundAction.ToString()
        default_outbound_action = $profile.DefaultOutboundAction.ToString()
        log_allowed = $profile.LogAllowed
        log_blocked = $profile.LogBlocked
    }
    if (-not $enabled) {
        $result.all_enabled = $false
    }
}

$result.compliant = $result.all_enabled
$result | ConvertTo-Json -Depth 3
'''

    # =========================================================================
    # Extended Compliance Checks (audit, SMB, guest, lockout, firewall logging)
    # =========================================================================

    AUDIT_POLICY_CHECK = '''
# Check HIPAA-required audit policies
$categories = @(
    "Account Logon", "Account Management", "Logon/Logoff",
    "Object Access", "Policy Change", "Privilege Use",
    "System", "Detailed Tracking"
)
$results = @{}
$noncompliant = @()

foreach ($cat in $categories) {
    $output = auditpol /get /category:"$cat" 2>$null
    $hasAuditing = $output | Select-String "Success and Failure|Success|Failure" -Quiet
    $noAuditing = $output | Select-String "No Auditing" -Quiet
    $results[$cat] = @{
        configured = [bool]$hasAuditing
        no_auditing = [bool]$noAuditing
    }
    if ($noAuditing -and $cat -in @("Account Logon", "Account Management", "Logon/Logoff", "Object Access", "System")) {
        $noncompliant += $cat
    }
}

@{
    categories = $results
    noncompliant_categories = $noncompliant
    missing_policies = $noncompliant
    compliant = $noncompliant.Count -eq 0
} | ConvertTo-Json -Depth 3
'''

    SMB_SIGNING_CHECK = '''
$clientSigning = (Get-ItemProperty -Path "HKLM:\\SYSTEM\\CurrentControlSet\\Services\\LanmanWorkstation\\Parameters" `
    -Name "RequireSecuritySignature" -ErrorAction SilentlyContinue).RequireSecuritySignature
$serverSigning = (Get-ItemProperty -Path "HKLM:\\SYSTEM\\CurrentControlSet\\Services\\LanmanServer\\Parameters" `
    -Name "RequireSecuritySignature" -ErrorAction SilentlyContinue).RequireSecuritySignature

@{
    client_signing_required = $clientSigning -eq 1
    server_signing_required = $serverSigning -eq 1
    compliant = ($clientSigning -eq 1) -and ($serverSigning -eq 1)
} | ConvertTo-Json
'''

    GUEST_ACCOUNT_CHECK = '''
$guest = Get-LocalUser -Name "Guest" -ErrorAction SilentlyContinue
$guestEnabled = $false
if ($guest) {
    $guestEnabled = $guest.Enabled
}

@{
    guest_exists = [bool]$guest
    guest_enabled = $guestEnabled
    compliant = -not $guestEnabled
} | ConvertTo-Json
'''

    ACCOUNT_LOCKOUT_CHECK = r'''
$lockoutInfo = net accounts 2>$null
$threshold = 0
$duration = 0

foreach ($line in $lockoutInfo) {
    if ($line -match "Lockout threshold:\s+(\d+)") { $threshold = [int]$Matches[1] }
    if ($line -match "Lockout duration.*:\s+(\d+)") { $duration = [int]$Matches[1] }
}

@{
    lockout_threshold = $threshold
    lockout_duration_minutes = $duration
    has_lockout_policy = $threshold -gt 0 -and $threshold -le 10
    compliant = $threshold -gt 0 -and $threshold -le 10 -and $duration -ge 15
} | ConvertTo-Json
'''

    FIREWALL_LOGGING_CHECK = '''
$profiles = Get-NetFirewallProfile -ErrorAction SilentlyContinue
$result = @{
    profiles = @{}
    all_logging = $true
}

foreach ($profile in $profiles) {
    $logEnabled = $profile.LogBlocked -eq "True" -or $profile.LogAllowed -eq "True"
    $result.profiles[$profile.Name] = @{
        log_allowed = [string]$profile.LogAllowed
        log_blocked = [string]$profile.LogBlocked
        log_file_name = $profile.LogFileName
        log_max_size = $profile.LogMaxSizeKilobytes
        logging_enabled = $logEnabled
    }
    if (-not $logEnabled) {
        $result.all_logging = $false
    }
}

$result.compliant = $result.all_logging
$result | ConvertTo-Json -Depth 3
'''

    SCHEDULED_TASK_CHECK = r'''
# Check for suspicious scheduled tasks (persistence mechanism)
$suspiciousTasks = @()
$systemMaintenance = Get-ScheduledTask -TaskPath "\\Microsoft\\Windows\\SystemMaintenance\\" -ErrorAction SilentlyContinue
$customTasks = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
    $_.TaskPath -notlike "\\Microsoft\\*" -and $_.State -ne "Disabled"
}

# Check for known suspicious patterns
$allTasks = @($systemMaintenance) + @($customTasks) | Where-Object { $_ -ne $null }
foreach ($task in $allTasks) {
    $actions = $task.Actions
    foreach ($action in $actions) {
        $exec = $action.Execute
        if ($exec -match "powershell|cmd\.exe|wscript|cscript|mshta|certutil|bitsadmin" -and $exec -notmatch "MpCmdRun") {
            $suspiciousTasks += @{
                name = $task.TaskName
                path = $task.TaskPath
                execute = $exec
                arguments = $action.Arguments
                state = $task.State.ToString()
            }
        }
    }
}

@{
    suspicious_tasks = $suspiciousTasks
    suspicious_count = $suspiciousTasks.Count
    compliant = $suspiciousTasks.Count -eq 0
} | ConvertTo-Json -Depth 3
'''

    REGISTRY_PERSISTENCE_CHECK = r'''
# Check for suspicious registry Run/RunOnce entries
$runKeys = @(
    "HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run",
    "HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\RunOnce",
    "HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run",
    "HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\RunOnce"
)

$allowedEntries = @("SecurityHealth", "VMware User Process", "bginfo")
$suspicious = @()

foreach ($key in $runKeys) {
    $entries = Get-ItemProperty -Path $key -ErrorAction SilentlyContinue
    if ($entries) {
        $props = $entries.PSObject.Properties | Where-Object {
            $_.Name -notlike "PS*" -and $_.Name -notin $allowedEntries
        }
        foreach ($prop in $props) {
            if ($prop.Value -match "powershell|cmd\.exe|wscript|enc |hidden|bypass") {
                $suspicious += @{
                    key = $key
                    name = $prop.Name
                    value = $prop.Value
                }
            }
        }
    }
}

@{
    suspicious_entries = $suspicious
    suspicious_count = $suspicious.Count
    compliant = $suspicious.Count -eq 0
} | ConvertTo-Json -Depth 3
'''

    SCREEN_LOCK_CHECK = '''
# Check Group Policy settings for screen lock
$inactivityTimeout = (Get-ItemProperty -Path "HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System" `
    -Name "InactivityTimeoutSecs" -ErrorAction SilentlyContinue).InactivityTimeoutSecs

# Check screensaver settings
$screensaverActive = (Get-ItemProperty -Path "HKCU:\\Control Panel\\Desktop" `
    -Name "ScreenSaveActive" -ErrorAction SilentlyContinue).ScreenSaveActive
$screensaverTimeout = (Get-ItemProperty -Path "HKCU:\\Control Panel\\Desktop" `
    -Name "ScreenSaveTimeOut" -ErrorAction SilentlyContinue).ScreenSaveTimeOut
$screensaverSecure = (Get-ItemProperty -Path "HKCU:\\Control Panel\\Desktop" `
    -Name "ScreenSaverIsSecure" -ErrorAction SilentlyContinue).ScreenSaverIsSecure

# Check if machine lock is required after sleep
$consoleLock = (Get-ItemProperty -Path "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon" `
    -Name "DisableLockWorkstation" -ErrorAction SilentlyContinue).DisableLockWorkstation

# Compliant if: timeout exists and is <= 15 minutes (900 seconds) OR screensaver with password is on
$hasInactivityTimeout = $inactivityTimeout -and $inactivityTimeout -gt 0 -and $inactivityTimeout -le 900
$hasScreensaverLock = $screensaverActive -eq "1" -and $screensaverSecure -eq "1" -and $screensaverTimeout -and [int]$screensaverTimeout -le 900

@{
    inactivity_timeout_seconds = $inactivityTimeout
    inactivity_timeout_minutes = if ($inactivityTimeout) { [math]::Round($inactivityTimeout / 60, 1) } else { $null }
    screensaver_active = $screensaverActive -eq "1"
    screensaver_timeout_seconds = if ($screensaverTimeout) { [int]$screensaverTimeout } else { $null }
    screensaver_secure = $screensaverSecure -eq "1"
    workstation_lock_disabled = $consoleLock -eq 1
    has_policy_timeout = $hasInactivityTimeout
    has_screensaver_lock = $hasScreensaverLock
    compliant = $hasInactivityTimeout -or $hasScreensaverLock
} | ConvertTo-Json
'''

    def __init__(
        self,
        executor,  # WindowsExecutor instance
        default_credentials: Optional[Dict[str, str]] = None,
        timeout_seconds: int = 30,
    ):
        """
        Initialize workstation compliance checker.

        Args:
            executor: WindowsExecutor for WinRM commands
            default_credentials: Default credentials for workstation access
            timeout_seconds: Timeout for each check
        """
        self.executor = executor
        self.default_credentials = default_credentials or {}
        self.timeout_seconds = timeout_seconds

    async def check_bitlocker(
        self,
        target: str,
        credentials: Optional[Dict[str, str]] = None,
    ) -> CheckResult:
        """
        Check BitLocker encryption status via WMI.

        Query: Win32_EncryptableVolume (root\\CIMV2\\Security\\MicrosoftVolumeEncryption)
        HIPAA: §164.312(a)(2)(iv) - Encryption and Decryption

        Returns:
            CheckResult with encryption status, protection status, key protectors
        """
        start = datetime.now(timezone.utc)
        creds = credentials or self.default_credentials

        try:
            result = await self.executor.run_script(
                target=target,
                script=self.BITLOCKER_CHECK,
                credentials=creds,
                timeout_seconds=self.timeout_seconds,
            )

            duration_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000

            if result.success:
                import json
                data = json.loads(result.output.get("stdout", "{}"))
                compliant = data.get("compliant", False)

                return CheckResult(
                    check_type="bitlocker",
                    hostname=target,
                    status=ComplianceStatus.COMPLIANT if compliant else ComplianceStatus.DRIFTED,
                    compliant=compliant,
                    details=data,
                    hipaa_controls=["§164.312(a)(2)(iv)"],
                    duration_ms=duration_ms,
                )
            else:
                return CheckResult(
                    check_type="bitlocker",
                    hostname=target,
                    status=ComplianceStatus.ERROR,
                    compliant=False,
                    details={},
                    error=result.error,
                    hipaa_controls=["§164.312(a)(2)(iv)"],
                    duration_ms=duration_ms,
                )

        except Exception as e:
            return CheckResult(
                check_type="bitlocker",
                hostname=target,
                status=ComplianceStatus.ERROR,
                compliant=False,
                details={},
                error=str(e),
                hipaa_controls=["§164.312(a)(2)(iv)"],
                duration_ms=(datetime.now(timezone.utc) - start).total_seconds() * 1000,
            )

    async def check_defender(
        self,
        target: str,
        credentials: Optional[Dict[str, str]] = None,
    ) -> CheckResult:
        """
        Check Windows Defender status via WMI.

        Query: MSFT_MpComputerStatus (root/Microsoft/Windows/Defender)
        HIPAA: §164.308(a)(5)(ii)(B) - Protection from Malicious Software

        Returns:
            CheckResult with AV enabled, realtime protection, signature age
        """
        start = datetime.now(timezone.utc)
        creds = credentials or self.default_credentials

        try:
            result = await self.executor.run_script(
                target=target,
                script=self.DEFENDER_CHECK,
                credentials=creds,
                timeout_seconds=self.timeout_seconds,
            )

            duration_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000

            if result.success:
                import json
                data = json.loads(result.output.get("stdout", "{}"))
                compliant = data.get("compliant", False)

                return CheckResult(
                    check_type="defender",
                    hostname=target,
                    status=ComplianceStatus.COMPLIANT if compliant else ComplianceStatus.DRIFTED,
                    compliant=compliant,
                    details=data,
                    hipaa_controls=["§164.308(a)(5)(ii)(B)"],
                    duration_ms=duration_ms,
                )
            else:
                return CheckResult(
                    check_type="defender",
                    hostname=target,
                    status=ComplianceStatus.ERROR,
                    compliant=False,
                    details={},
                    error=result.error,
                    hipaa_controls=["§164.308(a)(5)(ii)(B)"],
                    duration_ms=duration_ms,
                )

        except Exception as e:
            return CheckResult(
                check_type="defender",
                hostname=target,
                status=ComplianceStatus.ERROR,
                compliant=False,
                details={},
                error=str(e),
                hipaa_controls=["§164.308(a)(5)(ii)(B)"],
                duration_ms=(datetime.now(timezone.utc) - start).total_seconds() * 1000,
            )

    async def check_patches(
        self,
        target: str,
        credentials: Optional[Dict[str, str]] = None,
    ) -> CheckResult:
        """
        Check Windows patch status via WMI.

        Query: Win32_QuickFixEngineering
        HIPAA: §164.308(a)(5)(ii)(B) - Protection from Malicious Software

        Returns:
            CheckResult with last patch date, days since update, pending patches
        """
        start = datetime.now(timezone.utc)
        creds = credentials or self.default_credentials

        try:
            result = await self.executor.run_script(
                target=target,
                script=self.PATCHES_CHECK,
                credentials=creds,
                timeout_seconds=self.timeout_seconds,
            )

            duration_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000

            if result.success:
                import json
                data = json.loads(result.output.get("stdout", "{}"))
                compliant = data.get("compliant", False)

                return CheckResult(
                    check_type="patches",
                    hostname=target,
                    status=ComplianceStatus.COMPLIANT if compliant else ComplianceStatus.DRIFTED,
                    compliant=compliant,
                    details=data,
                    hipaa_controls=["§164.308(a)(5)(ii)(B)"],
                    duration_ms=duration_ms,
                )
            else:
                return CheckResult(
                    check_type="patches",
                    hostname=target,
                    status=ComplianceStatus.ERROR,
                    compliant=False,
                    details={},
                    error=result.error,
                    hipaa_controls=["§164.308(a)(5)(ii)(B)"],
                    duration_ms=duration_ms,
                )

        except Exception as e:
            return CheckResult(
                check_type="patches",
                hostname=target,
                status=ComplianceStatus.ERROR,
                compliant=False,
                details={},
                error=str(e),
                hipaa_controls=["§164.308(a)(5)(ii)(B)"],
                duration_ms=(datetime.now(timezone.utc) - start).total_seconds() * 1000,
            )

    async def check_firewall(
        self,
        target: str,
        credentials: Optional[Dict[str, str]] = None,
    ) -> CheckResult:
        """
        Check Windows Firewall status via WMI.

        Query: MSFT_NetFirewallProfile
        HIPAA: §164.312(a)(1) - Access Control

        Returns:
            CheckResult with domain/private/public profile status
        """
        start = datetime.now(timezone.utc)
        creds = credentials or self.default_credentials

        try:
            result = await self.executor.run_script(
                target=target,
                script=self.FIREWALL_CHECK,
                credentials=creds,
                timeout_seconds=self.timeout_seconds,
            )

            duration_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000

            if result.success:
                import json
                data = json.loads(result.output.get("stdout", "{}"))
                compliant = data.get("compliant", False)

                return CheckResult(
                    check_type="firewall",
                    hostname=target,
                    status=ComplianceStatus.COMPLIANT if compliant else ComplianceStatus.DRIFTED,
                    compliant=compliant,
                    details=data,
                    hipaa_controls=["§164.312(a)(1)"],
                    duration_ms=duration_ms,
                )
            else:
                return CheckResult(
                    check_type="firewall",
                    hostname=target,
                    status=ComplianceStatus.ERROR,
                    compliant=False,
                    details={},
                    error=result.error,
                    hipaa_controls=["§164.312(a)(1)"],
                    duration_ms=duration_ms,
                )

        except Exception as e:
            return CheckResult(
                check_type="firewall",
                hostname=target,
                status=ComplianceStatus.ERROR,
                compliant=False,
                details={},
                error=str(e),
                hipaa_controls=["§164.312(a)(1)"],
                duration_ms=(datetime.now(timezone.utc) - start).total_seconds() * 1000,
            )

    async def check_screen_lock(
        self,
        target: str,
        credentials: Optional[Dict[str, str]] = None,
    ) -> CheckResult:
        """
        Check screen lock policy via Registry query.

        Query: Registry HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System
        HIPAA: §164.312(a)(2)(iii) - Automatic Logoff

        Returns:
            CheckResult with InactivityTimeoutSecs, screensaver settings
        """
        start = datetime.now(timezone.utc)
        creds = credentials or self.default_credentials

        try:
            result = await self.executor.run_script(
                target=target,
                script=self.SCREEN_LOCK_CHECK,
                credentials=creds,
                timeout_seconds=self.timeout_seconds,
            )

            duration_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000

            if result.success:
                import json
                data = json.loads(result.output.get("stdout", "{}"))
                compliant = data.get("compliant", False)

                return CheckResult(
                    check_type="screen_lock",
                    hostname=target,
                    status=ComplianceStatus.COMPLIANT if compliant else ComplianceStatus.DRIFTED,
                    compliant=compliant,
                    details=data,
                    hipaa_controls=["§164.312(a)(2)(iii)"],
                    duration_ms=duration_ms,
                )
            else:
                return CheckResult(
                    check_type="screen_lock",
                    hostname=target,
                    status=ComplianceStatus.ERROR,
                    compliant=False,
                    details={},
                    error=result.error,
                    hipaa_controls=["§164.312(a)(2)(iii)"],
                    duration_ms=duration_ms,
                )

        except Exception as e:
            return CheckResult(
                check_type="screen_lock",
                hostname=target,
                status=ComplianceStatus.ERROR,
                compliant=False,
                details={},
                error=str(e),
                hipaa_controls=["§164.312(a)(2)(iii)"],
                duration_ms=(datetime.now(timezone.utc) - start).total_seconds() * 1000,
            )

    async def _run_generic_check(
        self,
        target: str,
        check_type: str,
        script: str,
        hipaa_controls: List[str],
        credentials: Optional[Dict[str, str]] = None,
    ) -> CheckResult:
        """Generic check runner for any PowerShell check script."""
        start = datetime.now(timezone.utc)
        creds = credentials or self.default_credentials

        try:
            result = await self.executor.run_script(
                target=target,
                script=script,
                credentials=creds,
                timeout_seconds=self.timeout_seconds,
            )

            duration_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000

            if result.success:
                import json
                data = json.loads(result.output.get("stdout", "{}"))
                compliant = data.get("compliant", False)

                return CheckResult(
                    check_type=check_type,
                    hostname=target,
                    status=ComplianceStatus.COMPLIANT if compliant else ComplianceStatus.DRIFTED,
                    compliant=compliant,
                    details=data,
                    hipaa_controls=hipaa_controls,
                    duration_ms=duration_ms,
                )
            else:
                return CheckResult(
                    check_type=check_type,
                    hostname=target,
                    status=ComplianceStatus.ERROR,
                    compliant=False,
                    details={},
                    error=result.error,
                    hipaa_controls=hipaa_controls,
                    duration_ms=duration_ms,
                )

        except Exception as e:
            return CheckResult(
                check_type=check_type,
                hostname=target,
                status=ComplianceStatus.ERROR,
                compliant=False,
                details={},
                error=str(e),
                hipaa_controls=hipaa_controls,
                duration_ms=(datetime.now(timezone.utc) - start).total_seconds() * 1000,
            )

    async def check_audit_policy(
        self, target: str, credentials: Optional[Dict[str, str]] = None,
    ) -> CheckResult:
        """Check audit policy compliance. HIPAA: §164.312(b)"""
        return await self._run_generic_check(
            target, "audit_policy", self.AUDIT_POLICY_CHECK,
            ["§164.312(b)", "§164.308(a)(1)(ii)(D)"], credentials,
        )

    async def check_smb_signing(
        self, target: str, credentials: Optional[Dict[str, str]] = None,
    ) -> CheckResult:
        """Check SMB signing. HIPAA: §164.312(e)(1)"""
        return await self._run_generic_check(
            target, "smb_signing", self.SMB_SIGNING_CHECK,
            ["§164.312(e)(1)"], credentials,
        )

    async def check_guest_account(
        self, target: str, credentials: Optional[Dict[str, str]] = None,
    ) -> CheckResult:
        """Check guest account status. HIPAA: §164.312(a)(1)"""
        return await self._run_generic_check(
            target, "guest_account", self.GUEST_ACCOUNT_CHECK,
            ["§164.312(a)(1)"], credentials,
        )

    async def check_account_lockout(
        self, target: str, credentials: Optional[Dict[str, str]] = None,
    ) -> CheckResult:
        """Check account lockout policy. HIPAA: §164.312(a)(2)(i)"""
        return await self._run_generic_check(
            target, "lockout_policy", self.ACCOUNT_LOCKOUT_CHECK,
            ["§164.312(a)(2)(i)"], credentials,
        )

    async def check_firewall_logging(
        self, target: str, credentials: Optional[Dict[str, str]] = None,
    ) -> CheckResult:
        """Check firewall logging. HIPAA: §164.312(b)"""
        return await self._run_generic_check(
            target, "firewall_logging", self.FIREWALL_LOGGING_CHECK,
            ["§164.312(b)"], credentials,
        )

    async def check_scheduled_tasks(
        self, target: str, credentials: Optional[Dict[str, str]] = None,
    ) -> CheckResult:
        """Check for suspicious scheduled tasks. HIPAA: §164.308(a)(1)(ii)(D)"""
        return await self._run_generic_check(
            target, "scheduled_task_persistence", self.SCHEDULED_TASK_CHECK,
            ["§164.308(a)(1)(ii)(D)", "§164.312(b)"], credentials,
        )

    async def check_registry_persistence(
        self, target: str, credentials: Optional[Dict[str, str]] = None,
    ) -> CheckResult:
        """Check for suspicious registry run entries. HIPAA: §164.308(a)(1)(ii)(D)"""
        return await self._run_generic_check(
            target, "registry_run_persistence", self.REGISTRY_PERSISTENCE_CHECK,
            ["§164.308(a)(1)(ii)(D)", "§164.312(b)"], credentials,
        )

    async def run_all_checks(
        self,
        target: str,
        ip_address: Optional[str] = None,
        credentials: Optional[Dict[str, str]] = None,
        full_coverage: bool = True,
    ) -> WorkstationComplianceResult:
        """
        Run compliance checks on a workstation.

        Args:
            target: Hostname or IP of workstation
            ip_address: IP address for reference
            credentials: WinRM credentials
            full_coverage: If True, run all checks including extended.
                          If False, run basic compliance checks only.

        Returns:
            WorkstationComplianceResult with all check results
        """
        logger.info(f"Running {'full' if full_coverage else 'basic'} compliance checks on {target}")

        # Basic compliance checks (always run)
        basic_checks = [
            self.check_bitlocker(target, credentials),
            self.check_defender(target, credentials),
            self.check_patches(target, credentials),
            self.check_firewall(target, credentials),
            self.check_screen_lock(target, credentials),
            self.check_audit_policy(target, credentials),
            self.check_account_lockout(target, credentials),
        ]

        # Extended checks for full coverage
        extended_checks = []
        if full_coverage:
            extended_checks = [
                self.check_smb_signing(target, credentials),
                self.check_guest_account(target, credentials),
                self.check_firewall_logging(target, credentials),
                self.check_scheduled_tasks(target, credentials),
                self.check_registry_persistence(target, credentials),
            ]

        # Run all checks concurrently
        results = await asyncio.gather(
            *basic_checks, *extended_checks,
            return_exceptions=True,
        )

        basic_types = [
            "bitlocker", "defender", "patches", "firewall",
            "screen_lock", "audit_policy", "lockout_policy",
        ]
        extended_types = [
            "smb_signing", "guest_account", "firewall_logging",
            "scheduled_task_persistence", "registry_run_persistence",
        ]
        all_types = basic_types + (extended_types if full_coverage else [])

        # Convert exceptions to error results
        checks = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                checks.append(CheckResult(
                    check_type=all_types[i] if i < len(all_types) else f"check_{i}",
                    hostname=target,
                    status=ComplianceStatus.ERROR,
                    compliant=False,
                    details={},
                    error=str(result),
                ))
            else:
                checks.append(result)

        return WorkstationComplianceResult(
            hostname=target,
            ip_address=ip_address,
            checks=checks,
        )


# Convenience function for appliance agent
async def check_workstation_compliance(
    executor,
    target: str,
    credentials: Dict[str, str],
) -> Dict[str, Any]:
    """
    Convenience function for appliance agent integration.

    Returns dict ready for API/evidence.
    """
    checker = WorkstationComplianceChecker(
        executor=executor,
        default_credentials=credentials,
    )

    result = await checker.run_all_checks(target, credentials=credentials)
    return result.to_dict()
