"""
Windows Runbook Definitions for HIPAA Compliance.

Each runbook contains:
- PowerShell detection script (check for drift)
- PowerShell remediation script (fix drift)
- HIPAA control mappings
- Severity and timeout settings
"""

from typing import Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class WindowsRunbook:
    """Windows runbook definition."""
    id: str
    name: str
    description: str
    hipaa_controls: List[str]
    severity: str  # low, medium, high, critical

    # PowerShell scripts
    detect_script: str
    remediate_script: str
    verify_script: str

    # Execution settings
    timeout_seconds: int = 300
    requires_reboot: bool = False
    disruptive: bool = False

    # Evidence fields to capture
    evidence_fields: List[str] = field(default_factory=list)


# =============================================================================
# RB-WIN-PATCH-001: Windows Update / WSUS Compliance
# =============================================================================

RUNBOOK_WIN_PATCH = WindowsRunbook(
    id="RB-WIN-PATCH-001",
    name="Windows Patch Compliance",
    description="Check and apply missing Windows security updates via WSUS or Windows Update",
    hipaa_controls=["164.308(a)(5)(ii)(B)"],
    severity="high",

    detect_script=r'''
# Check for pending Windows updates
$UpdateSession = New-Object -ComObject Microsoft.Update.Session
$UpdateSearcher = $UpdateSession.CreateUpdateSearcher()

try {
    $SearchResult = $UpdateSearcher.Search("IsInstalled=0 and Type='Software' and IsHidden=0")

    $CriticalUpdates = @($SearchResult.Updates | Where-Object { $_.MsrcSeverity -eq 'Critical' })
    $ImportantUpdates = @($SearchResult.Updates | Where-Object { $_.MsrcSeverity -eq 'Important' })

    $Result = @{
        TotalPending = $SearchResult.Updates.Count
        CriticalPending = $CriticalUpdates.Count
        ImportantPending = $ImportantUpdates.Count
        LastInstallDate = (Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 1).InstalledOn
        Drifted = ($CriticalUpdates.Count -gt 0)
        Updates = @($SearchResult.Updates | Select-Object Title, MsrcSeverity, KBArticleIDs)
    }

    $Result | ConvertTo-Json -Depth 3
} catch {
    @{
        Error = $_.Exception.Message
        Drifted = $true
    } | ConvertTo-Json
}
''',

    remediate_script=r'''
# Install pending critical/important updates
$UpdateSession = New-Object -ComObject Microsoft.Update.Session
$UpdateSearcher = $UpdateSession.CreateUpdateSearcher()
$SearchResult = $UpdateSearcher.Search("IsInstalled=0 and Type='Software' and IsHidden=0")

$UpdatesToInstall = New-Object -ComObject Microsoft.Update.UpdateColl

foreach ($Update in $SearchResult.Updates) {
    if ($Update.MsrcSeverity -in @('Critical', 'Important')) {
        $UpdatesToInstall.Add($Update) | Out-Null
    }
}

if ($UpdatesToInstall.Count -gt 0) {
    $Downloader = $UpdateSession.CreateUpdateDownloader()
    $Downloader.Updates = $UpdatesToInstall
    $DownloadResult = $Downloader.Download()

    $Installer = $UpdateSession.CreateUpdateInstaller()
    $Installer.Updates = $UpdatesToInstall
    $InstallResult = $Installer.Install()

    @{
        UpdatesInstalled = $UpdatesToInstall.Count
        ResultCode = $InstallResult.ResultCode
        RebootRequired = $InstallResult.RebootRequired
    } | ConvertTo-Json
} else {
    @{
        UpdatesInstalled = 0
        Message = "No critical or important updates pending"
    } | ConvertTo-Json
}
''',

    verify_script=r'''
# Verify updates were applied
$UpdateSession = New-Object -ComObject Microsoft.Update.Session
$UpdateSearcher = $UpdateSession.CreateUpdateSearcher()
$SearchResult = $UpdateSearcher.Search("IsInstalled=0 and Type='Software' and IsHidden=0")

$CriticalUpdates = @($SearchResult.Updates | Where-Object { $_.MsrcSeverity -eq 'Critical' })

@{
    CriticalPending = $CriticalUpdates.Count
    Verified = ($CriticalUpdates.Count -eq 0)
} | ConvertTo-Json
''',

    timeout_seconds=1800,  # 30 minutes for updates
    requires_reboot=True,
    disruptive=True,
    evidence_fields=["TotalPending", "CriticalPending", "UpdatesInstalled", "RebootRequired"]
)


# =============================================================================
# RB-WIN-AV-001: Windows Defender / AV Health
# =============================================================================

RUNBOOK_WIN_AV = WindowsRunbook(
    id="RB-WIN-AV-001",
    name="Windows Defender Health",
    description="Check Windows Defender status, signatures, and real-time protection",
    hipaa_controls=["164.308(a)(5)(ii)(B)", "164.312(b)"],
    severity="critical",

    detect_script=r'''
# Check Windows Defender status
try {
    $DefenderStatus = Get-MpComputerStatus
    $SignatureAge = (Get-Date) - $DefenderStatus.AntivirusSignatureLastUpdated

    $Result = @{
        RealTimeProtection = $DefenderStatus.RealTimeProtectionEnabled
        AntivirusEnabled = $DefenderStatus.AntivirusEnabled
        AntispywareEnabled = $DefenderStatus.AntispywareEnabled
        SignatureVersion = $DefenderStatus.AntivirusSignatureVersion
        SignatureLastUpdated = $DefenderStatus.AntivirusSignatureLastUpdated.ToString("o")
        SignatureAgeDays = [math]::Round($SignatureAge.TotalDays, 1)
        QuickScanAge = $DefenderStatus.QuickScanAge
        FullScanAge = $DefenderStatus.FullScanAge
        Drifted = (-not $DefenderStatus.RealTimeProtectionEnabled) -or ($SignatureAge.TotalDays -gt 3)
    }

    $Result | ConvertTo-Json
} catch {
    @{
        Error = $_.Exception.Message
        Drifted = $true
        DefenderNotInstalled = $true
    } | ConvertTo-Json
}
''',

    remediate_script=r'''
# Enable Windows Defender and update signatures
try {
    # Enable real-time protection
    Set-MpPreference -DisableRealtimeMonitoring $false

    # Update signatures
    Update-MpSignature -ErrorAction Stop

    # Run quick scan if signatures were stale
    Start-MpScan -ScanType QuickScan -AsJob

    @{
        RealTimeEnabled = $true
        SignaturesUpdated = $true
        QuickScanStarted = $true
    } | ConvertTo-Json
} catch {
    @{
        Error = $_.Exception.Message
        Success = $false
    } | ConvertTo-Json
}
''',

    verify_script=r'''
# Verify Defender is healthy
$DefenderStatus = Get-MpComputerStatus
$SignatureAge = (Get-Date) - $DefenderStatus.AntivirusSignatureLastUpdated

@{
    RealTimeProtection = $DefenderStatus.RealTimeProtectionEnabled
    SignatureAgeDays = [math]::Round($SignatureAge.TotalDays, 1)
    Verified = $DefenderStatus.RealTimeProtectionEnabled -and ($SignatureAge.TotalDays -le 3)
} | ConvertTo-Json
''',

    timeout_seconds=600,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["RealTimeProtection", "SignatureVersion", "SignatureAgeDays"]
)


# =============================================================================
# RB-WIN-BACKUP-001: Windows Server Backup / Veeam Status
# =============================================================================

RUNBOOK_WIN_BACKUP = WindowsRunbook(
    id="RB-WIN-BACKUP-001",
    name="Backup Verification",
    description="Verify Windows Server Backup or Veeam backup status and age",
    hipaa_controls=["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"],
    severity="critical",

    detect_script=r'''
# Check backup status (Windows Server Backup or Veeam)
$Result = @{
    BackupType = "Unknown"
    LastBackup = $null
    BackupAgeHours = 999
    Drifted = $true
}

# Try Windows Server Backup first
try {
    $WBSummary = Get-WBSummary -ErrorAction Stop
    if ($WBSummary.LastSuccessfulBackupTime) {
        $Age = (Get-Date) - $WBSummary.LastSuccessfulBackupTime
        $Result = @{
            BackupType = "WindowsServerBackup"
            LastBackup = $WBSummary.LastSuccessfulBackupTime.ToString("o")
            BackupAgeHours = [math]::Round($Age.TotalHours, 1)
            NextBackup = $WBSummary.NextBackupTime
            LastResult = $WBSummary.LastBackupResultHR
            Drifted = ($Age.TotalHours -gt 24)
        }
    }
} catch {
    # WSB not available, try Veeam
    try {
        Add-PSSnapin VeeamPSSnapin -ErrorAction Stop
        $VeeamJob = Get-VBRJob | Where-Object { $_.IsScheduleEnabled } | Select-Object -First 1
        if ($VeeamJob) {
            $LastSession = Get-VBRBackupSession -Job $VeeamJob | Sort-Object CreationTime -Descending | Select-Object -First 1
            $Age = (Get-Date) - $LastSession.CreationTime
            $Result = @{
                BackupType = "Veeam"
                JobName = $VeeamJob.Name
                LastBackup = $LastSession.CreationTime.ToString("o")
                BackupAgeHours = [math]::Round($Age.TotalHours, 1)
                LastResult = $LastSession.Result
                Drifted = ($Age.TotalHours -gt 24) -or ($LastSession.Result -ne "Success")
            }
        }
    } catch {
        $Result.Error = "No backup solution detected"
    }
}

$Result | ConvertTo-Json
''',

    remediate_script=r'''
# Trigger backup job
$Result = @{ Success = $false }

# Try Windows Server Backup
try {
    $Policy = Get-WBPolicy -ErrorAction Stop
    if ($Policy) {
        Start-WBBackup -Policy $Policy -Async
        $Result = @{
            Success = $true
            BackupType = "WindowsServerBackup"
            Message = "Backup job started"
        }
    }
} catch {
    # Try Veeam
    try {
        Add-PSSnapin VeeamPSSnapin -ErrorAction Stop
        $VeeamJob = Get-VBRJob | Where-Object { $_.IsScheduleEnabled } | Select-Object -First 1
        if ($VeeamJob) {
            Start-VBRJob -Job $VeeamJob -RunAsync
            $Result = @{
                Success = $true
                BackupType = "Veeam"
                JobName = $VeeamJob.Name
                Message = "Veeam backup job started"
            }
        }
    } catch {
        $Result.Error = $_.Exception.Message
    }
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
# Verify backup completed
Start-Sleep -Seconds 60  # Wait for backup to start

try {
    $WBSummary = Get-WBSummary -ErrorAction Stop
    $Age = (Get-Date) - $WBSummary.LastSuccessfulBackupTime
    @{
        BackupAgeHours = [math]::Round($Age.TotalHours, 1)
        Verified = ($Age.TotalHours -lt 24)
    } | ConvertTo-Json
} catch {
    @{ Verified = $false, Error = $_.Exception.Message } | ConvertTo-Json
}
''',

    timeout_seconds=3600,  # 1 hour for backup
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["BackupType", "LastBackup", "BackupAgeHours", "LastResult"]
)


# =============================================================================
# RB-WIN-LOGGING-001: Windows Event Log / Audit Policy
# =============================================================================

RUNBOOK_WIN_LOGGING = WindowsRunbook(
    id="RB-WIN-LOGGING-001",
    name="Windows Event Logging",
    description="Verify Windows audit policy and event log forwarding",
    hipaa_controls=["164.312(b)", "164.308(a)(1)(ii)(D)"],
    severity="high",

    detect_script=r'''
# Check audit policy and event log health
$AuditPolicy = auditpol /get /category:* /r | ConvertFrom-Csv

$RequiredPolicies = @(
    "Logon",
    "Logoff",
    "Account Lockout",
    "User Account Management",
    "Security Group Management",
    "Process Creation",
    "Object Access"
)

$MissingPolicies = @()
foreach ($Policy in $RequiredPolicies) {
    $Found = $AuditPolicy | Where-Object { $_.Subcategory -like "*$Policy*" -and ($_."Inclusion Setting" -ne "No Auditing") }
    if (-not $Found) {
        $MissingPolicies += $Policy
    }
}

# Check event log sizes
$SecurityLog = Get-WinEvent -ListLog Security
$SystemLog = Get-WinEvent -ListLog System

$Result = @{
    AuditPoliciesConfigured = ($MissingPolicies.Count -eq 0)
    MissingPolicies = $MissingPolicies
    SecurityLogMaxSizeMB = [math]::Round($SecurityLog.MaximumSizeInBytes / 1MB, 0)
    SecurityLogRetentionDays = $SecurityLog.LogRetention
    SecurityLogEnabled = $SecurityLog.IsEnabled
    SystemLogEnabled = $SystemLog.IsEnabled
    Drifted = ($MissingPolicies.Count -gt 0) -or (-not $SecurityLog.IsEnabled)
}

$Result | ConvertTo-Json -Depth 2
''',

    remediate_script=r'''
# Configure audit policy for HIPAA compliance
$Commands = @(
    "auditpol /set /subcategory:`"Logon`" /success:enable /failure:enable",
    "auditpol /set /subcategory:`"Logoff`" /success:enable",
    "auditpol /set /subcategory:`"Account Lockout`" /success:enable /failure:enable",
    "auditpol /set /subcategory:`"User Account Management`" /success:enable /failure:enable",
    "auditpol /set /subcategory:`"Security Group Management`" /success:enable /failure:enable",
    "auditpol /set /subcategory:`"Process Creation`" /success:enable /failure:enable",
    "auditpol /set /subcategory:`"Audit Policy Change`" /success:enable /failure:enable"
)

$Results = @()
foreach ($Cmd in $Commands) {
    $Output = Invoke-Expression $Cmd 2>&1
    $Results += @{ Command = $Cmd; Result = $LASTEXITCODE }
}

# Ensure Security log is properly sized (at least 1GB)
$Log = Get-WinEvent -ListLog Security
if ($Log.MaximumSizeInBytes -lt 1GB) {
    Limit-EventLog -LogName Security -MaximumSize 1GB
}

@{
    CommandsExecuted = $Results.Count
    Success = ($Results | Where-Object { $_.Result -ne 0 }).Count -eq 0
} | ConvertTo-Json
''',

    verify_script=r'''
# Verify audit policy
$AuditPolicy = auditpol /get /category:* /r | ConvertFrom-Csv
$LogonAudit = $AuditPolicy | Where-Object { $_.Subcategory -like "*Logon*" }

@{
    LogonAuditEnabled = ($LogonAudit."Inclusion Setting" -ne "No Auditing")
    Verified = ($LogonAudit."Inclusion Setting" -ne "No Auditing")
} | ConvertTo-Json
''',

    timeout_seconds=300,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["AuditPoliciesConfigured", "MissingPolicies", "SecurityLogMaxSizeMB"]
)


# =============================================================================
# RB-WIN-FIREWALL-001: Windows Firewall Status
# =============================================================================

RUNBOOK_WIN_FIREWALL = WindowsRunbook(
    id="RB-WIN-FIREWALL-001",
    name="Windows Firewall Status",
    description="Verify Windows Firewall is enabled on all profiles",
    hipaa_controls=["164.312(a)(1)", "164.312(e)(1)"],
    severity="critical",

    detect_script=r'''
# Check Windows Firewall status
$Profiles = Get-NetFirewallProfile

$Result = @{
    Domain = @{
        Enabled = ($Profiles | Where-Object { $_.Name -eq "Domain" }).Enabled
        DefaultInboundAction = ($Profiles | Where-Object { $_.Name -eq "Domain" }).DefaultInboundAction
    }
    Private = @{
        Enabled = ($Profiles | Where-Object { $_.Name -eq "Private" }).Enabled
        DefaultInboundAction = ($Profiles | Where-Object { $_.Name -eq "Private" }).DefaultInboundAction
    }
    Public = @{
        Enabled = ($Profiles | Where-Object { $_.Name -eq "Public" }).Enabled
        DefaultInboundAction = ($Profiles | Where-Object { $_.Name -eq "Public" }).DefaultInboundAction
    }
    AllEnabled = ($Profiles | Where-Object { -not $_.Enabled }).Count -eq 0
    Drifted = ($Profiles | Where-Object { -not $_.Enabled }).Count -gt 0
}

$Result | ConvertTo-Json -Depth 2
''',

    remediate_script=r'''
# Enable Windows Firewall on all profiles
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled True

# Set default inbound action to Block
Set-NetFirewallProfile -Profile Domain,Public,Private -DefaultInboundAction Block

@{
    FirewallEnabled = $true
    DefaultInboundAction = "Block"
} | ConvertTo-Json
''',

    verify_script=r'''
$Profiles = Get-NetFirewallProfile
@{
    AllEnabled = ($Profiles | Where-Object { -not $_.Enabled }).Count -eq 0
    Verified = ($Profiles | Where-Object { -not $_.Enabled }).Count -eq 0
} | ConvertTo-Json
''',

    timeout_seconds=120,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["Domain", "Private", "Public", "AllEnabled"]
)


# =============================================================================
# RB-WIN-ENCRYPTION-001: BitLocker Status
# =============================================================================

RUNBOOK_WIN_ENCRYPTION = WindowsRunbook(
    id="RB-WIN-ENCRYPTION-001",
    name="BitLocker Encryption",
    description="Verify BitLocker encryption status on system drives",
    hipaa_controls=["164.312(a)(2)(iv)", "164.312(e)(2)(ii)"],
    severity="critical",

    detect_script=r'''
# Check BitLocker status
$Volumes = Get-BitLockerVolume

$Result = @{
    Volumes = @()
    AllEncrypted = $true
    Drifted = $false
}

foreach ($Vol in $Volumes) {
    $VolInfo = @{
        MountPoint = $Vol.MountPoint
        VolumeStatus = $Vol.VolumeStatus.ToString()
        ProtectionStatus = $Vol.ProtectionStatus.ToString()
        EncryptionPercentage = $Vol.EncryptionPercentage
        KeyProtector = ($Vol.KeyProtector | Select-Object -First 1).KeyProtectorType.ToString()
    }
    $Result.Volumes += $VolInfo

    # Check if system drive is encrypted
    if ($Vol.MountPoint -eq "C:" -and $Vol.VolumeStatus -ne "FullyEncrypted") {
        $Result.AllEncrypted = $false
        $Result.Drifted = $true
    }
}

$Result | ConvertTo-Json -Depth 3
''',

    remediate_script=r'''
# NOTE: BitLocker enablement requires careful planning
# This is an ALERT-ONLY runbook - full encryption requires manual intervention

@{
    Action = "ALERT"
    Message = "BitLocker not enabled on system drive. Manual intervention required."
    Recommendation = "Enable BitLocker via: Enable-BitLocker -MountPoint 'C:' -EncryptionMethod XtsAes256 -UsedSpaceOnly -RecoveryPasswordProtector"
    Warning = "Ensure recovery key is backed up to AD or secure location before enabling"
} | ConvertTo-Json
''',

    verify_script=r'''
$Vol = Get-BitLockerVolume -MountPoint "C:"
@{
    VolumeStatus = $Vol.VolumeStatus.ToString()
    Verified = ($Vol.VolumeStatus -eq "FullyEncrypted")
} | ConvertTo-Json
''',

    timeout_seconds=60,
    requires_reboot=True,
    disruptive=True,  # Encryption is disruptive
    evidence_fields=["Volumes", "AllEncrypted"]
)


# =============================================================================
# RB-WIN-AD-001: Active Directory Health
# =============================================================================

RUNBOOK_WIN_AD_HEALTH = WindowsRunbook(
    id="RB-WIN-AD-001",
    name="Active Directory Health",
    description="Check AD replication, DNS, and account lockout status",
    hipaa_controls=["164.312(a)(1)", "164.308(a)(3)(ii)(C)"],
    severity="high",

    detect_script=r'''
# Check AD health (run on Domain Controller)
$Result = @{
    IsDomainController = $false
    Drifted = $false
}

try {
    Import-Module ActiveDirectory -ErrorAction Stop
    $DC = Get-ADDomainController -Discover -ErrorAction Stop
    $Result.IsDomainController = $true
    $Result.DomainController = $DC.HostName

    # Check replication status
    $ReplStatus = repadmin /showrepl /csv | ConvertFrom-Csv
    $FailedRepl = $ReplStatus | Where-Object { $_."Number of Failures" -gt 0 }
    $Result.ReplicationFailures = $FailedRepl.Count

    # Check locked out accounts
    $LockedAccounts = Search-ADAccount -LockedOut
    $Result.LockedAccountCount = $LockedAccounts.Count
    $Result.LockedAccounts = @($LockedAccounts | Select-Object -First 10 | Select-Object SamAccountName, LockedOut)

    # Check DNS
    $DNSServer = Resolve-DnsName -Name $DC.Domain -Type A -ErrorAction SilentlyContinue
    $Result.DNSHealthy = ($null -ne $DNSServer)

    $Result.Drifted = ($FailedRepl.Count -gt 0) -or ($LockedAccounts.Count -gt 5)

} catch {
    $Result.Error = $_.Exception.Message
    $Result.IsDomainController = $false
}

$Result | ConvertTo-Json -Depth 3
''',

    remediate_script=r'''
# AD remediation actions
Import-Module ActiveDirectory

$Actions = @()

# Unlock accounts that have been locked > 30 minutes
$LockedAccounts = Search-ADAccount -LockedOut
foreach ($Account in $LockedAccounts) {
    $LockoutTime = (Get-ADUser $Account.SamAccountName -Properties AccountLockoutTime).AccountLockoutTime
    if ($LockoutTime -and ((Get-Date) - $LockoutTime).TotalMinutes -gt 30) {
        Unlock-ADAccount -Identity $Account.SamAccountName
        $Actions += "Unlocked: $($Account.SamAccountName)"
    }
}

# Force replication
repadmin /syncall /AdeP

@{
    AccountsUnlocked = $Actions.Count
    Actions = $Actions
    ReplicationForced = $true
} | ConvertTo-Json
''',

    verify_script=r'''
Import-Module ActiveDirectory
$LockedAccounts = Search-ADAccount -LockedOut
@{
    LockedAccountCount = $LockedAccounts.Count
    Verified = ($LockedAccounts.Count -le 5)
} | ConvertTo-Json
''',

    timeout_seconds=300,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["ReplicationFailures", "LockedAccountCount", "DNSHealthy"]
)


# =============================================================================
# Runbook Registry
# =============================================================================

RUNBOOKS: Dict[str, WindowsRunbook] = {
    "RB-WIN-PATCH-001": RUNBOOK_WIN_PATCH,
    "RB-WIN-AV-001": RUNBOOK_WIN_AV,
    "RB-WIN-BACKUP-001": RUNBOOK_WIN_BACKUP,
    "RB-WIN-LOGGING-001": RUNBOOK_WIN_LOGGING,
    "RB-WIN-FIREWALL-001": RUNBOOK_WIN_FIREWALL,
    "RB-WIN-ENCRYPTION-001": RUNBOOK_WIN_ENCRYPTION,
    "RB-WIN-AD-001": RUNBOOK_WIN_AD_HEALTH,
}


def get_runbook(runbook_id: str) -> Optional[WindowsRunbook]:
    """Get runbook by ID."""
    return RUNBOOKS.get(runbook_id)


def list_runbooks() -> List[Dict]:
    """List all available runbooks."""
    return [
        {
            "id": rb.id,
            "name": rb.name,
            "description": rb.description,
            "hipaa_controls": rb.hipaa_controls,
            "severity": rb.severity,
            "disruptive": rb.disruptive,
        }
        for rb in RUNBOOKS.values()
    ]
