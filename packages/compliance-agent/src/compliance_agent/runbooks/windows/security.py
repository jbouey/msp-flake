"""
Windows Security Runbooks for HIPAA Compliance.

Runbooks for security policy enforcement and protection.
"""

from typing import Dict, List
from dataclasses import dataclass, field
from .runbooks import WindowsRunbook, ExecutionConstraints


# =============================================================================
# RB-WIN-SEC-001: Windows Firewall Re-enable
# =============================================================================

RUNBOOK_FIREWALL_ENABLE = WindowsRunbook(
    id="RB-WIN-SEC-001",
    name="Windows Firewall Re-enable",
    description="Re-enable Windows Firewall if disabled on any profile",
    version="1.0",
    hipaa_controls=["164.312(a)(1)", "164.312(e)(1)"],
    severity="critical",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=15,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check Windows Firewall status on all profiles
$Profiles = Get-NetFirewallProfile
$Result = @{
    Profiles = @{}
    Drifted = $false
    DisabledProfiles = @()
}

foreach ($Profile in $Profiles) {
    $Result.Profiles[$Profile.Name] = @{
        Enabled = $Profile.Enabled
        DefaultInboundAction = $Profile.DefaultInboundAction.ToString()
        DefaultOutboundAction = $Profile.DefaultOutboundAction.ToString()
    }

    if (-not $Profile.Enabled) {
        $Result.Drifted = $true
        $Result.DisabledProfiles += $Profile.Name
    }
}

$Result | ConvertTo-Json -Depth 3
''',

    remediate_script=r'''
# Enable Windows Firewall on all profiles
$Result = @{ Success = $false; Actions = @() }

try {
    $Profiles = Get-NetFirewallProfile

    foreach ($Profile in $Profiles) {
        if (-not $Profile.Enabled) {
            Set-NetFirewallProfile -Name $Profile.Name -Enabled True
            $Result.Actions += "Enabled $($Profile.Name) profile"
        }
    }

    # Verify all enabled
    $Profiles = Get-NetFirewallProfile
    $AllEnabled = ($Profiles | Where-Object { -not $_.Enabled }).Count -eq 0

    $Result.Success = $AllEnabled
    $Result.Message = if ($AllEnabled) { "All profiles enabled" } else { "Some profiles failed to enable" }
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
$Profiles = Get-NetFirewallProfile
$AllEnabled = ($Profiles | Where-Object { -not $_.Enabled }).Count -eq 0
@{
    AllEnabled = $AllEnabled
    Verified = $AllEnabled
} | ConvertTo-Json
''',

    timeout_seconds=60,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["Profiles", "DisabledProfiles"]
)


# =============================================================================
# RB-WIN-SEC-002: Audit Policy Remediation
# =============================================================================

RUNBOOK_AUDIT_POLICY = WindowsRunbook(
    id="RB-WIN-SEC-002",
    name="Audit Policy Remediation",
    description="Ensure HIPAA-required Windows audit policies are configured",
    version="1.0",
    hipaa_controls=["164.312(b)", "164.308(a)(1)(ii)(D)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check HIPAA-required audit policies
$RequiredPolicies = @{
    "Logon" = "Success,Failure"
    "Logoff" = "Success"
    "Account Lockout" = "Success,Failure"
    "User Account Management" = "Success,Failure"
    "Security Group Management" = "Success,Failure"
    "Audit Policy Change" = "Success,Failure"
    "Authentication Policy Change" = "Success"
    "Sensitive Privilege Use" = "Success,Failure"
}

$AuditPolicy = auditpol /get /category:* /r 2>$null | ConvertFrom-Csv
$Result = @{
    Drifted = $false
    MissingPolicies = @()
    ConfiguredPolicies = @()
}

foreach ($Policy in $RequiredPolicies.Keys) {
    $Current = $AuditPolicy | Where-Object { $_.Subcategory -like "*$Policy*" }
    if ($Current) {
        $Setting = $Current."Inclusion Setting"
        if ($Setting -eq "No Auditing") {
            $Result.Drifted = $true
            $Result.MissingPolicies += $Policy
        } else {
            $Result.ConfiguredPolicies += @{ Name = $Policy; Setting = $Setting }
        }
    }
}

$Result | ConvertTo-Json -Depth 2
''',

    remediate_script=r'''
# Configure HIPAA-required audit policies
$Commands = @(
    "auditpol /set /subcategory:`"Logon`" /success:enable /failure:enable",
    "auditpol /set /subcategory:`"Logoff`" /success:enable",
    "auditpol /set /subcategory:`"Account Lockout`" /success:enable /failure:enable",
    "auditpol /set /subcategory:`"User Account Management`" /success:enable /failure:enable",
    "auditpol /set /subcategory:`"Security Group Management`" /success:enable /failure:enable",
    "auditpol /set /subcategory:`"Audit Policy Change`" /success:enable /failure:enable",
    "auditpol /set /subcategory:`"Authentication Policy Change`" /success:enable",
    "auditpol /set /subcategory:`"Sensitive Privilege Use`" /success:enable /failure:enable"
)

$Results = @()
foreach ($Cmd in $Commands) {
    $Output = Invoke-Expression $Cmd 2>&1
    $Results += @{ Command = $Cmd.Split('"')[1]; ExitCode = $LASTEXITCODE }
}

$FailedCount = ($Results | Where-Object { $_.ExitCode -ne 0 }).Count

@{
    Success = ($FailedCount -eq 0)
    CommandsExecuted = $Results.Count
    FailedCommands = $FailedCount
    Results = $Results
} | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
$AuditPolicy = auditpol /get /category:* /r | ConvertFrom-Csv
$LogonAudit = $AuditPolicy | Where-Object { $_.Subcategory -like "*Logon*" }
@{
    LogonAuditEnabled = ($LogonAudit."Inclusion Setting" -ne "No Auditing")
    Verified = ($LogonAudit."Inclusion Setting" -ne "No Auditing")
} | ConvertTo-Json
''',

    timeout_seconds=120,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["MissingPolicies", "ConfiguredPolicies"]
)


# =============================================================================
# RB-WIN-SEC-003: Account Lockout Policy Reset
# =============================================================================

RUNBOOK_LOCKOUT_POLICY = WindowsRunbook(
    id="RB-WIN-SEC-003",
    name="Account Lockout Policy Reset",
    description="Configure account lockout thresholds per HIPAA requirements",
    version="1.0",
    hipaa_controls=["164.312(a)(2)(i)"],
    severity="medium",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check account lockout policy
$Result = @{
    Drifted = $false
}

# Get current policy from net accounts
$NetAccounts = net accounts 2>&1
$LockoutThreshold = 0
$LockoutDuration = 0
$LockoutWindow = 0

foreach ($Line in $NetAccounts) {
    if ($Line -match "Lockout threshold:\s*(\d+|Never)") {
        $LockoutThreshold = if ($Matches[1] -eq "Never") { 0 } else { [int]$Matches[1] }
    }
    if ($Line -match "Lockout duration.*:\s*(\d+)") {
        $LockoutDuration = [int]$Matches[1]
    }
    if ($Line -match "Lockout observation window.*:\s*(\d+)") {
        $LockoutWindow = [int]$Matches[1]
    }
}

$Result.LockoutThreshold = $LockoutThreshold
$Result.LockoutDuration = $LockoutDuration
$Result.LockoutWindow = $LockoutWindow

# HIPAA requires lockout after 3-5 failed attempts
if ($LockoutThreshold -eq 0 -or $LockoutThreshold -gt 5) {
    $Result.Drifted = $true
    $Result.DriftReason = "Lockout threshold should be 3-5 attempts"
}

# Lockout duration should be at least 15 minutes
if ($LockoutDuration -lt 15 -and $LockoutThreshold -gt 0) {
    $Result.Drifted = $true
    $Result.DriftReason = "Lockout duration should be at least 15 minutes"
}

$Result | ConvertTo-Json
''',

    remediate_script=r'''
# Configure account lockout policy
$Result = @{ Success = $false }

try {
    # Set lockout threshold to 5 attempts
    net accounts /lockoutthreshold:5 | Out-Null

    # Set lockout duration to 30 minutes
    net accounts /lockoutduration:30 | Out-Null

    # Set lockout observation window to 30 minutes
    net accounts /lockoutwindow:30 | Out-Null

    $Result.Success = $true
    $Result.Message = "Account lockout policy configured"
    $Result.Settings = @{
        LockoutThreshold = 5
        LockoutDuration = 30
        LockoutWindow = 30
    }
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
$NetAccounts = net accounts 2>&1
$LockoutThreshold = 0
foreach ($Line in $NetAccounts) {
    if ($Line -match "Lockout threshold:\s*(\d+)") {
        $LockoutThreshold = [int]$Matches[1]
    }
}
@{
    LockoutThreshold = $LockoutThreshold
    Verified = ($LockoutThreshold -ge 3 -and $LockoutThreshold -le 5)
} | ConvertTo-Json
''',

    timeout_seconds=60,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["LockoutThreshold", "LockoutDuration", "LockoutWindow"]
)


# =============================================================================
# RB-WIN-SEC-004: Password Policy Enforcement
# =============================================================================

RUNBOOK_PASSWORD_POLICY = WindowsRunbook(
    id="RB-WIN-SEC-004",
    name="Password Policy Enforcement",
    description="Verify password complexity and expiration policies meet HIPAA requirements",
    version="1.0",
    hipaa_controls=["164.312(d)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check password policy
$Result = @{
    Drifted = $false
    Issues = @()
}

$NetAccounts = net accounts 2>&1
$MinLength = 0
$MaxAge = 0
$MinAge = 0
$History = 0

foreach ($Line in $NetAccounts) {
    if ($Line -match "Minimum password length:\s*(\d+)") {
        $MinLength = [int]$Matches[1]
    }
    if ($Line -match "Maximum password age.*:\s*(\d+|Unlimited)") {
        $MaxAge = if ($Matches[1] -eq "Unlimited") { 999 } else { [int]$Matches[1] }
    }
    if ($Line -match "Minimum password age.*:\s*(\d+)") {
        $MinAge = [int]$Matches[1]
    }
    if ($Line -match "Length of password history.*:\s*(\d+|None)") {
        $History = if ($Matches[1] -eq "None") { 0 } else { [int]$Matches[1] }
    }
}

$Result.MinimumLength = $MinLength
$Result.MaximumAge = $MaxAge
$Result.MinimumAge = $MinAge
$Result.PasswordHistory = $History

# Check compliance
if ($MinLength -lt 8) {
    $Result.Drifted = $true
    $Result.Issues += "Minimum length should be at least 8 characters"
}
if ($MaxAge -gt 90 -or $MaxAge -eq 999) {
    $Result.Drifted = $true
    $Result.Issues += "Maximum age should be 90 days or less"
}
if ($History -lt 6) {
    $Result.Drifted = $true
    $Result.Issues += "Password history should remember at least 6 passwords"
}

# Check complexity requirement
$SecEdit = secedit /export /cfg "$env:TEMP\secpol.cfg" /areas SECURITYPOLICY 2>&1
$SecPol = Get-Content "$env:TEMP\secpol.cfg" -ErrorAction SilentlyContinue
$Complexity = ($SecPol | Select-String "PasswordComplexity\s*=\s*1") -ne $null
Remove-Item "$env:TEMP\secpol.cfg" -Force -ErrorAction SilentlyContinue

$Result.ComplexityEnabled = $Complexity
if (-not $Complexity) {
    $Result.Drifted = $true
    $Result.Issues += "Password complexity should be enabled"
}

$Result | ConvertTo-Json -Depth 2
''',

    remediate_script=r'''
# Configure password policy (domain GPO or local)
$Result = @{ Success = $false; Actions = @() }

try {
    # These work for local policy; domain requires GPO
    net accounts /minpwlen:12 2>&1 | Out-Null
    $Result.Actions += "Set minimum password length to 12"

    net accounts /maxpwage:90 2>&1 | Out-Null
    $Result.Actions += "Set maximum password age to 90 days"

    net accounts /minpwage:1 2>&1 | Out-Null
    $Result.Actions += "Set minimum password age to 1 day"

    net accounts /uniquepw:12 2>&1 | Out-Null
    $Result.Actions += "Set password history to 12"

    # Enable complexity via secedit
    $CfgFile = "$env:TEMP\secpol_fix.cfg"
    $DbFile = "$env:TEMP\secpol_fix.sdb"

    @"
[Unicode]
Unicode=yes
[System Access]
PasswordComplexity = 1
"@ | Set-Content $CfgFile

    secedit /configure /db $DbFile /cfg $CfgFile /areas SECURITYPOLICY 2>&1 | Out-Null
    $Result.Actions += "Enabled password complexity"

    Remove-Item $CfgFile, $DbFile -Force -ErrorAction SilentlyContinue

    $Result.Success = $true
    $Result.Message = "Password policy configured"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
$NetAccounts = net accounts 2>&1
$MinLength = 0
foreach ($Line in $NetAccounts) {
    if ($Line -match "Minimum password length:\s*(\d+)") {
        $MinLength = [int]$Matches[1]
    }
}
@{
    MinimumLength = $MinLength
    Verified = ($MinLength -ge 8)
} | ConvertTo-Json
''',

    timeout_seconds=120,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["MinimumLength", "MaximumAge", "PasswordHistory", "ComplexityEnabled"]
)


# =============================================================================
# RB-WIN-SEC-005: BitLocker Status Recovery
# =============================================================================

RUNBOOK_BITLOCKER_STATUS = WindowsRunbook(
    id="RB-WIN-SEC-005",
    name="BitLocker Status Recovery",
    description="Check and resume BitLocker protection if suspended",
    version="1.0",
    hipaa_controls=["164.312(a)(2)(iv)", "164.312(e)(2)(ii)"],
    severity="critical",
    constraints=ExecutionConstraints(
        max_retries=1,
        retry_delay_seconds=60,
        requires_maintenance_window=True,
        allow_concurrent=False
    ),

    detect_script=r'''
# Check BitLocker status
$Result = @{
    Drifted = $false
    Volumes = @()
}

try {
    $Volumes = Get-BitLockerVolume -ErrorAction Stop

    foreach ($Vol in $Volumes) {
        $VolInfo = @{
            MountPoint = $Vol.MountPoint
            VolumeStatus = $Vol.VolumeStatus.ToString()
            ProtectionStatus = $Vol.ProtectionStatus.ToString()
            EncryptionPercentage = $Vol.EncryptionPercentage
        }
        $Result.Volumes += $VolInfo

        # Check for issues on system drive
        if ($Vol.MountPoint -eq "C:") {
            if ($Vol.ProtectionStatus -eq "Off" -and $Vol.VolumeStatus -eq "FullyEncrypted") {
                $Result.Drifted = $true
                $Result.DriftReason = "BitLocker suspended on system drive"
            } elseif ($Vol.VolumeStatus -ne "FullyEncrypted") {
                $Result.Drifted = $true
                $Result.DriftReason = "System drive not fully encrypted"
            }
        }
    }
} catch {
    $Result.Error = $_.Exception.Message
    $Result.BitLockerNotAvailable = $true
}

$Result | ConvertTo-Json -Depth 3
''',

    remediate_script=r'''
# Resume BitLocker protection if suspended
$Result = @{ Success = $false }

try {
    $Vol = Get-BitLockerVolume -MountPoint "C:" -ErrorAction Stop

    if ($Vol.ProtectionStatus -eq "Off" -and $Vol.VolumeStatus -eq "FullyEncrypted") {
        # Resume protection
        Resume-BitLocker -MountPoint "C:" -ErrorAction Stop
        $Result.Success = $true
        $Result.Action = "Resumed BitLocker protection"
    } elseif ($Vol.VolumeStatus -eq "FullyEncrypted") {
        $Result.Success = $true
        $Result.Message = "BitLocker already active"
    } else {
        # Not encrypted - this requires planning
        $Result.Action = "ALERT"
        $Result.Message = "Drive not encrypted - manual intervention required"
        $Result.Warning = "Enable BitLocker: Enable-BitLocker -MountPoint C: -EncryptionMethod XtsAes256"
    }

    $Result.CurrentStatus = (Get-BitLockerVolume -MountPoint "C:").ProtectionStatus.ToString()
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
try {
    $Vol = Get-BitLockerVolume -MountPoint "C:" -ErrorAction Stop
    @{
        ProtectionStatus = $Vol.ProtectionStatus.ToString()
        Verified = ($Vol.ProtectionStatus -eq "On")
    } | ConvertTo-Json
} catch {
    @{ Verified = $false; Error = $_.Exception.Message } | ConvertTo-Json
}
''',

    timeout_seconds=120,
    requires_reboot=False,
    disruptive=True,
    evidence_fields=["Volumes", "DriftReason"]
)


# =============================================================================
# RB-WIN-SEC-006: Windows Defender Real-time Protection
# =============================================================================

RUNBOOK_DEFENDER_REALTIME = WindowsRunbook(
    id="RB-WIN-SEC-006",
    name="Windows Defender Real-time Protection",
    description="Enable Windows Defender real-time protection and update signatures",
    version="1.0",
    hipaa_controls=["164.308(a)(5)(ii)(B)"],
    severity="critical",
    constraints=ExecutionConstraints(
        max_retries=3,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check Windows Defender status
$Result = @{
    Drifted = $false
}

try {
    $Status = Get-MpComputerStatus -ErrorAction Stop
    $SignatureAge = (Get-Date) - $Status.AntivirusSignatureLastUpdated

    $Result.RealTimeEnabled = $Status.RealTimeProtectionEnabled
    $Result.AntivirusEnabled = $Status.AntivirusEnabled
    $Result.BehaviorMonitorEnabled = $Status.BehaviorMonitorEnabled
    $Result.SignatureVersion = $Status.AntivirusSignatureVersion
    $Result.SignatureAgeDays = [math]::Round($SignatureAge.TotalDays, 1)
    $Result.EngineVersion = $Status.AMEngineVersion
    $Result.QuickScanAgeDays = $Status.QuickScanAge

    # Drift conditions
    if (-not $Status.RealTimeProtectionEnabled) {
        $Result.Drifted = $true
        $Result.DriftReason = "Real-time protection disabled"
    } elseif ($SignatureAge.TotalDays -gt 3) {
        $Result.Drifted = $true
        $Result.DriftReason = "Signatures older than 3 days"
    } elseif (-not $Status.AntivirusEnabled) {
        $Result.Drifted = $true
        $Result.DriftReason = "Antivirus disabled"
    }
} catch {
    $Result.Error = $_.Exception.Message
    $Result.DefenderNotAvailable = $true
    $Result.Drifted = $true
}

$Result | ConvertTo-Json
''',

    remediate_script=r'''
# Enable Defender and update signatures
$Result = @{ Success = $false; Actions = @() }

try {
    # Enable real-time protection
    Set-MpPreference -DisableRealtimeMonitoring $false -ErrorAction Stop
    $Result.Actions += "Enabled real-time monitoring"

    # Enable behavior monitoring
    Set-MpPreference -DisableBehaviorMonitoring $false -ErrorAction SilentlyContinue
    $Result.Actions += "Enabled behavior monitoring"

    # Update signatures
    Update-MpSignature -ErrorAction Stop
    $Result.Actions += "Updated virus signatures"

    # Quick scan in background
    Start-MpScan -ScanType QuickScan -AsJob | Out-Null
    $Result.Actions += "Started background quick scan"

    $Result.Success = $true
    $Result.Message = "Windows Defender protection enabled and updated"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
try {
    $Status = Get-MpComputerStatus
    $SignatureAge = (Get-Date) - $Status.AntivirusSignatureLastUpdated
    @{
        RealTimeEnabled = $Status.RealTimeProtectionEnabled
        SignatureAgeDays = [math]::Round($SignatureAge.TotalDays, 1)
        Verified = ($Status.RealTimeProtectionEnabled -and $SignatureAge.TotalDays -le 3)
    } | ConvertTo-Json
} catch {
    @{ Verified = $false; Error = $_.Exception.Message } | ConvertTo-Json
}
''',

    timeout_seconds=300,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["RealTimeEnabled", "SignatureVersion", "SignatureAgeDays", "QuickScanAgeDays"]
)


# =============================================================================
# Security Runbooks Registry
# =============================================================================

SECURITY_RUNBOOKS: Dict[str, WindowsRunbook] = {
    "RB-WIN-SEC-001": RUNBOOK_FIREWALL_ENABLE,
    "RB-WIN-SEC-002": RUNBOOK_AUDIT_POLICY,
    "RB-WIN-SEC-003": RUNBOOK_LOCKOUT_POLICY,
    "RB-WIN-SEC-004": RUNBOOK_PASSWORD_POLICY,
    "RB-WIN-SEC-005": RUNBOOK_BITLOCKER_STATUS,
    "RB-WIN-SEC-006": RUNBOOK_DEFENDER_REALTIME,
}
