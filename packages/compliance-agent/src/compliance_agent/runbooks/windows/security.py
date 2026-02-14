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
# Using direct auditpol calls instead of Invoke-Expression for security
$Policies = @(
    @{ Subcategory = "Logon"; Success = $true; Failure = $true },
    @{ Subcategory = "Logoff"; Success = $true; Failure = $false },
    @{ Subcategory = "Account Lockout"; Success = $true; Failure = $true },
    @{ Subcategory = "User Account Management"; Success = $true; Failure = $true },
    @{ Subcategory = "Security Group Management"; Success = $true; Failure = $true },
    @{ Subcategory = "Audit Policy Change"; Success = $true; Failure = $true },
    @{ Subcategory = "Authentication Policy Change"; Success = $true; Failure = $false },
    @{ Subcategory = "Sensitive Privilege Use"; Success = $true; Failure = $true }
)

$Results = @()
foreach ($Policy in $Policies) {
    $SuccessArg = if ($Policy.Success) { "/success:enable" } else { "/success:disable" }
    $FailureArg = if ($Policy.Failure) { "/failure:enable" } else { "/failure:disable" }
    $Args = @("/set", "/subcategory:`"$($Policy.Subcategory)`"", $SuccessArg, $FailureArg)

    $Proc = Start-Process -FilePath "auditpol.exe" -ArgumentList $Args -NoNewWindow -Wait -PassThru
    $Results += @{ Subcategory = $Policy.Subcategory; ExitCode = $Proc.ExitCode }
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
# RB-WIN-SEC-007: SMB Signing Enforcement
# =============================================================================

RUNBOOK_SMB_SIGNING = WindowsRunbook(
    id="RB-WIN-SEC-007",
    name="SMB Signing Enforcement",
    description="Enforce SMB signing to prevent MITM attacks on file shares",
    version="1.0",
    hipaa_controls=["164.312(e)(1)", "164.312(e)(2)(i)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check SMB signing settings
$Result = @{
    Drifted = $false
    Issues = @()
}

# Check server-side SMB signing
$SmbServerConfig = Get-SmbServerConfiguration
$Result.RequireSecuritySignature = $SmbServerConfig.RequireSecuritySignature
$Result.EnableSecuritySignature = $SmbServerConfig.EnableSecuritySignature

# Check client-side SMB signing via registry
$ClientSigning = Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters" -ErrorAction SilentlyContinue
$Result.ClientRequireSigning = $ClientSigning.RequireSecuritySignature
$Result.ClientEnableSigning = $ClientSigning.EnableSecuritySignature

# HIPAA requires SMB signing
if (-not $SmbServerConfig.RequireSecuritySignature) {
    $Result.Drifted = $true
    $Result.Issues += "Server SMB signing not required"
}
if (-not $SmbServerConfig.EnableSecuritySignature) {
    $Result.Drifted = $true
    $Result.Issues += "Server SMB signing not enabled"
}
if ($ClientSigning.RequireSecuritySignature -ne 1) {
    $Result.Drifted = $true
    $Result.Issues += "Client SMB signing not required"
}

$Result | ConvertTo-Json -Depth 2
''',

    remediate_script=r'''
# Enable SMB signing on server and client
$Result = @{ Success = $false; Actions = @() }

try {
    # Enable server-side SMB signing
    Set-SmbServerConfiguration -RequireSecuritySignature $true -EnableSecuritySignature $true -Confirm:$false
    $Result.Actions += "Enabled server SMB signing"

    # Enable client-side SMB signing via registry
    Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters" -Name "RequireSecuritySignature" -Value 1 -Type DWord
    Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters" -Name "EnableSecuritySignature" -Value 1 -Type DWord
    $Result.Actions += "Enabled client SMB signing"

    $Result.Success = $true
    $Result.Message = "SMB signing enforced"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
$SmbConfig = Get-SmbServerConfiguration
@{
    RequireSecuritySignature = $SmbConfig.RequireSecuritySignature
    EnableSecuritySignature = $SmbConfig.EnableSecuritySignature
    Verified = ($SmbConfig.RequireSecuritySignature -and $SmbConfig.EnableSecuritySignature)
} | ConvertTo-Json
''',

    timeout_seconds=60,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["RequireSecuritySignature", "EnableSecuritySignature", "Issues"]
)


# =============================================================================
# RB-WIN-SEC-008: NTLM Security Settings
# =============================================================================

RUNBOOK_NTLM_SECURITY = WindowsRunbook(
    id="RB-WIN-SEC-008",
    name="NTLM Security Settings",
    description="Configure NTLM security to prevent credential relay attacks",
    version="1.0",
    hipaa_controls=["164.312(d)", "164.312(e)(2)(ii)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=True,  # May affect authentication
        allow_concurrent=True
    ),

    detect_script=r'''
# Check NTLM security settings
$Result = @{
    Drifted = $false
    Issues = @()
}

# Check LAN Manager authentication level
$LMKey = "HKLM:\SYSTEM\CurrentControlSet\Control\Lsa"
$LMLevel = (Get-ItemProperty -Path $LMKey -Name "LmCompatibilityLevel" -ErrorAction SilentlyContinue).LmCompatibilityLevel
$Result.LmCompatibilityLevel = $LMLevel

# Level 5 = Send NTLMv2 response only, refuse LM & NTLM
# Minimum for HIPAA should be 3 or higher
if ($null -eq $LMLevel -or $LMLevel -lt 3) {
    $Result.Drifted = $true
    $Result.Issues += "LM compatibility level too low (should be >= 3)"
}

# Check NTLMv2 session security
$SessionSecurity = (Get-ItemProperty -Path $LMKey -Name "NtlmMinClientSec" -ErrorAction SilentlyContinue).NtlmMinClientSec
$Result.NtlmMinClientSec = $SessionSecurity

# Check if NTLM is restricted
$RestrictNTLM = (Get-ItemProperty -Path "$LMKey\MSV1_0" -Name "RestrictSendingNTLMTraffic" -ErrorAction SilentlyContinue).RestrictSendingNTLMTraffic
$Result.RestrictNTLMTraffic = $RestrictNTLM

# Check NoLMHash
$NoLMHash = (Get-ItemProperty -Path $LMKey -Name "NoLMHash" -ErrorAction SilentlyContinue).NoLMHash
$Result.NoLMHash = $NoLMHash

if ($NoLMHash -ne 1) {
    $Result.Drifted = $true
    $Result.Issues += "LM hash storage not disabled"
}

$Result | ConvertTo-Json -Depth 2
''',

    remediate_script=r'''
# Configure NTLM security settings
$Result = @{ Success = $false; Actions = @() }

try {
    $LMKey = "HKLM:\SYSTEM\CurrentControlSet\Control\Lsa"

    # Set LM compatibility level to 5 (NTLMv2 only)
    Set-ItemProperty -Path $LMKey -Name "LmCompatibilityLevel" -Value 5 -Type DWord
    $Result.Actions += "Set LM compatibility level to 5 (NTLMv2 only)"

    # Disable LM hash storage
    Set-ItemProperty -Path $LMKey -Name "NoLMHash" -Value 1 -Type DWord
    $Result.Actions += "Disabled LM hash storage"

    # Set minimum session security
    Set-ItemProperty -Path $LMKey -Name "NtlmMinClientSec" -Value 537395200 -Type DWord
    Set-ItemProperty -Path $LMKey -Name "NtlmMinServerSec" -Value 537395200 -Type DWord
    $Result.Actions += "Enabled NTLM session security"

    $Result.Success = $true
    $Result.Message = "NTLM security hardened"
    $Result.Warning = "Some legacy applications may require testing"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
$LMKey = "HKLM:\SYSTEM\CurrentControlSet\Control\Lsa"
$LMLevel = (Get-ItemProperty -Path $LMKey -Name "LmCompatibilityLevel" -ErrorAction SilentlyContinue).LmCompatibilityLevel
$NoLMHash = (Get-ItemProperty -Path $LMKey -Name "NoLMHash" -ErrorAction SilentlyContinue).NoLMHash
@{
    LmCompatibilityLevel = $LMLevel
    NoLMHash = $NoLMHash
    Verified = ($LMLevel -ge 3 -and $NoLMHash -eq 1)
} | ConvertTo-Json
''',

    timeout_seconds=60,
    requires_reboot=True,  # Registry changes may need reboot
    disruptive=True,
    evidence_fields=["LmCompatibilityLevel", "NoLMHash", "Issues"]
)


# =============================================================================
# RB-WIN-SEC-009: Unauthorized User Detection
# =============================================================================

RUNBOOK_UNAUTHORIZED_USERS = WindowsRunbook(
    id="RB-WIN-SEC-009",
    name="Unauthorized User Detection",
    description="Detect and disable unauthorized local admin accounts (backdoor users)",
    version="1.0",
    hipaa_controls=["164.312(a)(2)(i)", "164.308(a)(3)(ii)(A)"],
    severity="critical",
    constraints=ExecutionConstraints(
        max_retries=1,
        retry_delay_seconds=60,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Detect unauthorized local admin accounts
$Result = @{
    Drifted = $false
    LocalAdmins = @()
    SuspiciousAccounts = @()
}

# Known legitimate admin accounts (customize per environment)
$KnownAdmins = @("Administrator", "Domain Admins", "Enterprise Admins", "svc.monitoring", "svc.backup")

# Get local administrators group members
$AdminGroup = [ADSI]"WinNT://./Administrators,group"
$Members = @($AdminGroup.Invoke("Members")) | ForEach-Object {
    $Path = ([ADSI]$_).Path
    $Name = $Path.Split("/")[-1]
    @{
        Name = $Name
        Path = $Path
        Type = if ($Path -match "WinNT://[^/]+/[^/]+$") { "Local" } else { "Domain" }
    }
}

$Result.LocalAdmins = $Members

# Check for suspicious accounts
foreach ($Member in $Members) {
    $IsKnown = $false
    foreach ($KnownAdmin in $KnownAdmins) {
        if ($Member.Name -like "*$KnownAdmin*") {
            $IsKnown = $true
            break
        }
    }

    if (-not $IsKnown -and $Member.Type -eq "Local") {
        # Check account creation date
        $User = Get-LocalUser -Name $Member.Name -ErrorAction SilentlyContinue
        if ($User) {
            $SuspiciousInfo = @{
                Name = $Member.Name
                Created = $User.PasswordLastSet
                Enabled = $User.Enabled
                Description = $User.Description
            }

            # Check if recently created (within 24 hours)
            if ($User.PasswordLastSet -and (Get-Date) - $User.PasswordLastSet -lt (New-TimeSpan -Hours 24)) {
                $SuspiciousInfo.RecentlyCreated = $true
            }

            $Result.SuspiciousAccounts += $SuspiciousInfo
            $Result.Drifted = $true
        }
    }
}

$Result.SuspiciousCount = $Result.SuspiciousAccounts.Count
$Result | ConvertTo-Json -Depth 3
''',

    remediate_script=r'''
# Disable suspicious local admin accounts
$Result = @{ Success = $false; Actions = @() }

$KnownAdmins = @("Administrator", "Domain Admins", "Enterprise Admins", "svc.monitoring", "svc.backup")

try {
    # Get local administrators
    $AdminGroup = [ADSI]"WinNT://./Administrators,group"
    $Members = @($AdminGroup.Invoke("Members")) | ForEach-Object {
        $Path = ([ADSI]$_).Path
        $Name = $Path.Split("/")[-1]
        @{ Name = $Name; Path = $Path; Type = if ($Path -match "WinNT://[^/]+/[^/]+$") { "Local" } else { "Domain" } }
    }

    foreach ($Member in $Members) {
        $IsKnown = $false
        foreach ($KnownAdmin in $KnownAdmins) {
            if ($Member.Name -like "*$KnownAdmin*") { $IsKnown = $true; break }
        }

        if (-not $IsKnown -and $Member.Type -eq "Local") {
            $User = Get-LocalUser -Name $Member.Name -ErrorAction SilentlyContinue
            if ($User -and $User.Enabled) {
                # Disable the suspicious account (don't delete - preserve for forensics)
                Disable-LocalUser -Name $Member.Name
                $Result.Actions += "Disabled suspicious account: $($Member.Name)"

                # Remove from Administrators group
                Remove-LocalGroupMember -Group "Administrators" -Member $Member.Name -ErrorAction SilentlyContinue
                $Result.Actions += "Removed from Administrators: $($Member.Name)"
            }
        }
    }

    $Result.Success = $true
    $Result.Message = "Suspicious accounts disabled"
    $Result.Warning = "Review disabled accounts and delete if confirmed malicious"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
$KnownAdmins = @("Administrator", "Domain Admins", "Enterprise Admins", "svc.monitoring", "svc.backup")
$AdminGroup = [ADSI]"WinNT://./Administrators,group"
$Members = @($AdminGroup.Invoke("Members")) | ForEach-Object { ([ADSI]$_).Path.Split("/")[-1] }
$Unknown = $Members | Where-Object { $Name = $_; -not ($KnownAdmins | Where-Object { $Name -like "*$_*" }) }
@{
    AdminCount = $Members.Count
    UnknownAdmins = @($Unknown)
    Verified = ($Unknown.Count -eq 0)
} | ConvertTo-Json
''',

    timeout_seconds=120,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["LocalAdmins", "SuspiciousAccounts", "SuspiciousCount"]
)


# =============================================================================
# RB-WIN-SEC-010: NLA Enforcement (Network Level Authentication)
# =============================================================================

RUNBOOK_NLA_ENFORCEMENT = WindowsRunbook(
    id="RB-WIN-SEC-010",
    name="NLA Enforcement",
    description="Enforce Network Level Authentication for RDP connections",
    version="1.0",
    hipaa_controls=["164.312(d)", "164.312(e)(1)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=15,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check NLA settings for RDP
$Result = @{
    Drifted = $false
    Issues = @()
}

# Check if RDP is enabled
$RDPEnabled = (Get-ItemProperty -Path "HKLM:\System\CurrentControlSet\Control\Terminal Server" -Name "fDenyTSConnections" -ErrorAction SilentlyContinue).fDenyTSConnections -eq 0
$Result.RDPEnabled = $RDPEnabled

if ($RDPEnabled) {
    # Check NLA setting
    $NLAEnabled = (Get-ItemProperty -Path "HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" -Name "UserAuthentication" -ErrorAction SilentlyContinue).UserAuthentication
    $Result.NLAEnabled = ($NLAEnabled -eq 1)

    # Check security layer
    $SecurityLayer = (Get-ItemProperty -Path "HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" -Name "SecurityLayer" -ErrorAction SilentlyContinue).SecurityLayer
    $Result.SecurityLayer = $SecurityLayer
    # 0 = RDP, 1 = Negotiate, 2 = TLS

    # Check encryption level
    $EncryptionLevel = (Get-ItemProperty -Path "HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" -Name "MinEncryptionLevel" -ErrorAction SilentlyContinue).MinEncryptionLevel
    $Result.MinEncryptionLevel = $EncryptionLevel

    if ($NLAEnabled -ne 1) {
        $Result.Drifted = $true
        $Result.Issues += "NLA not enabled for RDP"
    }
    if ($SecurityLayer -lt 2) {
        $Result.Drifted = $true
        $Result.Issues += "RDP security layer not set to TLS"
    }
}

$Result | ConvertTo-Json -Depth 2
''',

    remediate_script=r'''
# Enable NLA for RDP
$Result = @{ Success = $false; Actions = @() }

try {
    $RDPPath = "HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"

    # Enable NLA
    Set-ItemProperty -Path $RDPPath -Name "UserAuthentication" -Value 1 -Type DWord
    $Result.Actions += "Enabled NLA for RDP"

    # Set security layer to TLS
    Set-ItemProperty -Path $RDPPath -Name "SecurityLayer" -Value 2 -Type DWord
    $Result.Actions += "Set RDP security layer to TLS"

    # Set minimum encryption level to High
    Set-ItemProperty -Path $RDPPath -Name "MinEncryptionLevel" -Value 3 -Type DWord
    $Result.Actions += "Set RDP encryption to High"

    $Result.Success = $true
    $Result.Message = "NLA and RDP security enforced"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
$RDPPath = "HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"
$NLA = (Get-ItemProperty -Path $RDPPath -Name "UserAuthentication" -ErrorAction SilentlyContinue).UserAuthentication
$Security = (Get-ItemProperty -Path $RDPPath -Name "SecurityLayer" -ErrorAction SilentlyContinue).SecurityLayer
@{
    NLAEnabled = ($NLA -eq 1)
    SecurityLayer = $Security
    Verified = ($NLA -eq 1 -and $Security -ge 2)
} | ConvertTo-Json
''',

    timeout_seconds=60,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["NLAEnabled", "SecurityLayer", "MinEncryptionLevel", "Issues"]
)


# =============================================================================
# RB-WIN-SEC-011: UAC Enforcement
# =============================================================================

RUNBOOK_UAC_ENFORCEMENT = WindowsRunbook(
    id="RB-WIN-SEC-011",
    name="UAC Enforcement",
    description="Ensure User Account Control is enabled and properly configured",
    version="1.0",
    hipaa_controls=["164.312(a)(1)", "164.312(a)(2)(i)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=15,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check UAC settings
$Result = @{
    Drifted = $false
    Issues = @()
}

$UACPath = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"

# EnableLUA - UAC enabled/disabled
$EnableLUA = (Get-ItemProperty -Path $UACPath -Name "EnableLUA" -ErrorAction SilentlyContinue).EnableLUA
$Result.UACEnabled = ($EnableLUA -eq 1)

# ConsentPromptBehaviorAdmin - Admin prompt behavior
$AdminPrompt = (Get-ItemProperty -Path $UACPath -Name "ConsentPromptBehaviorAdmin" -ErrorAction SilentlyContinue).ConsentPromptBehaviorAdmin
$Result.AdminPromptBehavior = $AdminPrompt
# 0 = Elevate without prompting, 1 = Prompt for credentials on secure desktop
# 2 = Prompt for consent on secure desktop, 3 = Prompt for credentials
# 4 = Prompt for consent, 5 = Prompt for consent for non-Windows binaries

# PromptOnSecureDesktop - Secure desktop for prompts
$SecureDesktop = (Get-ItemProperty -Path $UACPath -Name "PromptOnSecureDesktop" -ErrorAction SilentlyContinue).PromptOnSecureDesktop
$Result.SecureDesktopEnabled = ($SecureDesktop -eq 1)

# EnableVirtualization - File/registry virtualization
$Virtualization = (Get-ItemProperty -Path $UACPath -Name "EnableVirtualization" -ErrorAction SilentlyContinue).EnableVirtualization
$Result.VirtualizationEnabled = ($Virtualization -eq 1)

# Check for drift
if ($EnableLUA -ne 1) {
    $Result.Drifted = $true
    $Result.Issues += "UAC is disabled"
}
if ($AdminPrompt -eq 0) {
    $Result.Drifted = $true
    $Result.Issues += "Admin elevation without prompting"
}
if ($SecureDesktop -ne 1) {
    $Result.Drifted = $true
    $Result.Issues += "Secure desktop disabled for UAC prompts"
}

$Result | ConvertTo-Json -Depth 2
''',

    remediate_script=r'''
# Enable and configure UAC
$Result = @{ Success = $false; Actions = @() }

try {
    $UACPath = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"

    # Enable UAC
    Set-ItemProperty -Path $UACPath -Name "EnableLUA" -Value 1 -Type DWord
    $Result.Actions += "Enabled UAC"

    # Set admin prompt to secure desktop with consent
    Set-ItemProperty -Path $UACPath -Name "ConsentPromptBehaviorAdmin" -Value 2 -Type DWord
    $Result.Actions += "Set admin prompt to secure desktop with consent"

    # Enable secure desktop
    Set-ItemProperty -Path $UACPath -Name "PromptOnSecureDesktop" -Value 1 -Type DWord
    $Result.Actions += "Enabled secure desktop"

    # Enable virtualization
    Set-ItemProperty -Path $UACPath -Name "EnableVirtualization" -Value 1 -Type DWord
    $Result.Actions += "Enabled file/registry virtualization"

    $Result.Success = $true
    $Result.Message = "UAC enforced"
    $Result.Warning = "Reboot may be required for full effect"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
$UACPath = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
$EnableLUA = (Get-ItemProperty -Path $UACPath -Name "EnableLUA" -ErrorAction SilentlyContinue).EnableLUA
$AdminPrompt = (Get-ItemProperty -Path $UACPath -Name "ConsentPromptBehaviorAdmin" -ErrorAction SilentlyContinue).ConsentPromptBehaviorAdmin
@{
    UACEnabled = ($EnableLUA -eq 1)
    AdminPrompt = $AdminPrompt
    Verified = ($EnableLUA -eq 1 -and $AdminPrompt -gt 0)
} | ConvertTo-Json
''',

    timeout_seconds=60,
    requires_reboot=True,
    disruptive=False,
    evidence_fields=["UACEnabled", "AdminPromptBehavior", "SecureDesktopEnabled", "Issues"]
)


# =============================================================================
# RB-WIN-SEC-012: Event Log Protection
# =============================================================================

RUNBOOK_EVENT_LOG_PROTECTION = WindowsRunbook(
    id="RB-WIN-SEC-012",
    name="Event Log Protection",
    description="Protect Windows event logs from clearing and ensure proper retention",
    version="1.0",
    hipaa_controls=["164.312(b)", "164.308(a)(1)(ii)(D)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=15,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check event log protection and retention
$Result = @{
    Drifted = $false
    Issues = @()
    Logs = @()
}

$ImportantLogs = @("Security", "System", "Application")

foreach ($LogName in $ImportantLogs) {
    $Log = Get-WinEvent -ListLog $LogName -ErrorAction SilentlyContinue
    if ($Log) {
        $LogInfo = @{
            Name = $LogName
            Enabled = $Log.IsEnabled
            MaxSizeMB = [math]::Round($Log.MaximumSizeInBytes / 1MB, 0)
            LogMode = $Log.LogMode.ToString()
            RecordCount = $Log.RecordCount
        }
        $Result.Logs += $LogInfo

        # Check if log is enabled
        if (-not $Log.IsEnabled) {
            $Result.Drifted = $true
            $Result.Issues += "$LogName log is disabled"
        }

        # Check minimum size (Security should be at least 1GB for HIPAA)
        if ($LogName -eq "Security" -and $Log.MaximumSizeInBytes -lt 1GB) {
            $Result.Drifted = $true
            $Result.Issues += "Security log size too small (< 1GB)"
        }

        # Check if overwrite mode (should be Archive/DoNotOverwrite for compliance)
        if ($LogName -eq "Security" -and $Log.LogMode -eq "Circular") {
            $Result.Issues += "Security log may overwrite old events"
        }
    }
}

# Check if event log service is running
$EventLogService = Get-Service -Name "EventLog" -ErrorAction SilentlyContinue
$Result.EventLogServiceRunning = ($EventLogService.Status -eq "Running")
if ($EventLogService.Status -ne "Running") {
    $Result.Drifted = $true
    $Result.Issues += "Event Log service not running"
}

# Check audit for log clearing (Event ID 1102)
$RecentClears = Get-WinEvent -FilterHashtable @{LogName='Security';Id=1102} -MaxEvents 5 -ErrorAction SilentlyContinue
$Result.RecentLogClears = @($RecentClears).Count
if ($Result.RecentLogClears -gt 0) {
    $Result.Drifted = $true
    $Result.Issues += "Security log was recently cleared ($($Result.RecentLogClears) times)"
}

$Result | ConvertTo-Json -Depth 3
''',

    remediate_script=r'''
# Configure event log protection
$Result = @{ Success = $false; Actions = @() }

try {
    # Ensure Event Log service is running
    $Service = Get-Service -Name "EventLog"
    if ($Service.Status -ne "Running") {
        Start-Service -Name "EventLog"
        $Result.Actions += "Started Event Log service"
    }

    # Configure Security log
    $SecurityLog = Get-WinEvent -ListLog Security
    if ($SecurityLog.MaximumSizeInBytes -lt 1GB) {
        Limit-EventLog -LogName Security -MaximumSize 1GB
        $Result.Actions += "Set Security log to 1GB"
    }

    # Enable all important logs
    $Logs = @("Security", "System", "Application")
    foreach ($LogName in $Logs) {
        $Log = Get-WinEvent -ListLog $LogName -ErrorAction SilentlyContinue
        if ($Log -and -not $Log.IsEnabled) {
            wevtutil sl $LogName /e:true
            $Result.Actions += "Enabled $LogName log"
        }
    }

    # Configure audit policy to log log clearing
    auditpol /set /subcategory:"Audit Policy Change" /success:enable /failure:enable 2>&1 | Out-Null
    $Result.Actions += "Enabled audit for policy changes"

    $Result.Success = $true
    $Result.Message = "Event log protection configured"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
$SecurityLog = Get-WinEvent -ListLog Security -ErrorAction SilentlyContinue
$Service = Get-Service -Name "EventLog" -ErrorAction SilentlyContinue
@{
    SecurityLogEnabled = $SecurityLog.IsEnabled
    SecurityLogSizeMB = [math]::Round($SecurityLog.MaximumSizeInBytes / 1MB, 0)
    EventLogServiceRunning = ($Service.Status -eq "Running")
    Verified = ($SecurityLog.IsEnabled -and $Service.Status -eq "Running" -and $SecurityLog.MaximumSizeInBytes -ge 1GB)
} | ConvertTo-Json
''',

    timeout_seconds=120,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["Logs", "EventLogServiceRunning", "RecentLogClears", "Issues"]
)


# =============================================================================
# RB-WIN-SEC-013: Credential Guard Status
# =============================================================================

RUNBOOK_CREDENTIAL_GUARD = WindowsRunbook(
    id="RB-WIN-SEC-013",
    name="Credential Guard Status",
    description="Check and alert on Windows Credential Guard status",
    version="1.0",
    hipaa_controls=["164.312(a)(2)(iv)", "164.312(d)"],
    severity="medium",
    constraints=ExecutionConstraints(
        max_retries=1,
        retry_delay_seconds=30,
        requires_maintenance_window=True,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check Credential Guard status
$Result = @{
    Drifted = $false
    Issues = @()
}

try {
    # Check Device Guard status
    $DeviceGuard = Get-CimInstance -ClassName Win32_DeviceGuard -Namespace root\Microsoft\Windows\DeviceGuard -ErrorAction Stop

    $Result.VirtualizationBasedSecurityStatus = $DeviceGuard.VirtualizationBasedSecurityStatus
    $Result.SecurityServicesConfigured = $DeviceGuard.SecurityServicesConfigured
    $Result.SecurityServicesRunning = $DeviceGuard.SecurityServicesRunning

    # 0 = Not configured, 1 = Credential Guard configured
    $Result.CredentialGuardConfigured = $DeviceGuard.SecurityServicesConfigured -contains 1
    $Result.CredentialGuardRunning = $DeviceGuard.SecurityServicesRunning -contains 1

    # Check if VBS is running
    if ($DeviceGuard.VirtualizationBasedSecurityStatus -ne 2) {
        $Result.Issues += "Virtualization Based Security not running"
    }

    # Credential Guard not running is informational (hardware dependent)
    if (-not $Result.CredentialGuardRunning) {
        $Result.Issues += "Credential Guard not running (may require hardware support)"
    }

} catch {
    $Result.Error = $_.Exception.Message
    $Result.Issues += "Unable to query Credential Guard status"
}

# Check for LSA protection
$LSAProtection = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Lsa" -Name "RunAsPPL" -ErrorAction SilentlyContinue).RunAsPPL
$Result.LSAProtectionEnabled = ($LSAProtection -eq 1)

if ($LSAProtection -ne 1) {
    $Result.Drifted = $true
    $Result.Issues += "LSA Protection (RunAsPPL) not enabled"
}

$Result | ConvertTo-Json -Depth 2
''',

    remediate_script=r'''
# Enable LSA Protection (Credential Guard requires hardware support)
$Result = @{ Success = $false; Actions = @() }

try {
    # Enable LSA Protection
    Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Lsa" -Name "RunAsPPL" -Value 1 -Type DWord
    $Result.Actions += "Enabled LSA Protection (RunAsPPL)"

    # Note: Full Credential Guard requires:
    # - Compatible hardware (VT-x, TPM 2.0)
    # - UEFI with Secure Boot
    # - Windows 10 Enterprise or Windows Server 2016+

    $Result.Success = $true
    $Result.Message = "LSA Protection enabled"
    $Result.Warning = "Reboot required. Full Credential Guard may require additional configuration."
    $Result.Note = "Credential Guard requires compatible hardware and Enterprise SKU"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
$LSAProtection = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Lsa" -Name "RunAsPPL" -ErrorAction SilentlyContinue).RunAsPPL
@{
    LSAProtectionEnabled = ($LSAProtection -eq 1)
    Verified = ($LSAProtection -eq 1)
} | ConvertTo-Json
''',

    timeout_seconds=60,
    requires_reboot=True,
    disruptive=False,
    evidence_fields=["CredentialGuardConfigured", "CredentialGuardRunning", "LSAProtectionEnabled", "Issues"]
)


# =============================================================================
# RB-WIN-ACCESS-001: Comprehensive Access Control Verification
# =============================================================================

RUNBOOK_ACCESS_CONTROL = WindowsRunbook(
    id="RB-WIN-ACCESS-001",
    name="Access Control Verification",
    description="Verify access controls including MFA, password policies, and account management",
    version="1.0",
    hipaa_controls=["164.312(d)", "164.312(a)(1)", "164.312(a)(2)(i)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Comprehensive access control verification
$Result = @{
    Drifted = $false
    Issues = @()
    PasswordPolicy = @{}
    AccountLockout = @{}
    MFAStatus = @{}
    PrivilegedAccounts = @()
}

# Check password policy
$NetAccounts = net accounts 2>&1
foreach ($Line in $NetAccounts) {
    if ($Line -match "Minimum password length:\s*(\d+)") {
        $Result.PasswordPolicy.MinLength = [int]$Matches[1]
        if ([int]$Matches[1] -lt 12) {
            $Result.Drifted = $true
            $Result.Issues += "Password min length < 12 characters"
        }
    }
    if ($Line -match "Maximum password age.*:\s*(\d+|Unlimited)") {
        $Result.PasswordPolicy.MaxAge = $Matches[1]
        if ($Matches[1] -eq "Unlimited" -or [int]$Matches[1] -gt 90) {
            $Result.Drifted = $true
            $Result.Issues += "Password max age > 90 days or unlimited"
        }
    }
    if ($Line -match "Lockout threshold:\s*(\d+|Never)") {
        $Result.AccountLockout.Threshold = $Matches[1]
        if ($Matches[1] -eq "Never" -or [int]$Matches[1] -eq 0 -or [int]$Matches[1] -gt 5) {
            $Result.Drifted = $true
            $Result.Issues += "Account lockout threshold not configured (should be 3-5)"
        }
    }
}

# Check for local admin accounts
$AdminGroup = [ADSI]"WinNT://./Administrators,group"
$Admins = @($AdminGroup.Invoke("Members")) | ForEach-Object {
    $Path = ([ADSI]$_).Path
    $Name = $Path.Split("/")[-1]
    @{ Name = $Name; Type = if ($Path -match "WinNT://[^/]+/[^/]+$") { "Local" } else { "Domain" } }
}
$Result.PrivilegedAccounts = $Admins
$LocalAdmins = @($Admins | Where-Object { $_.Type -eq "Local" })
if ($LocalAdmins.Count -gt 2) {
    $Result.Drifted = $true
    $Result.Issues += "More than 2 local admin accounts found ($($LocalAdmins.Count))"
}

# Check for disabled accounts that should be removed
$DisabledLocalUsers = Get-LocalUser | Where-Object { -not $_.Enabled -and $_.LastLogon -lt (Get-Date).AddDays(-90) }
if ($DisabledLocalUsers.Count -gt 0) {
    $Result.Issues += "$($DisabledLocalUsers.Count) stale disabled accounts found"
}

# Check password complexity via secedit
$TempCfg = "$env:TEMP\secpol_check.cfg"
secedit /export /cfg $TempCfg /areas SECURITYPOLICY 2>&1 | Out-Null
if (Test-Path $TempCfg) {
    $SecPol = Get-Content $TempCfg
    $Complexity = ($SecPol | Select-String "PasswordComplexity\s*=\s*1") -ne $null
    $Result.PasswordPolicy.ComplexityEnabled = $Complexity
    if (-not $Complexity) {
        $Result.Drifted = $true
        $Result.Issues += "Password complexity not enabled"
    }
    Remove-Item $TempCfg -Force -ErrorAction SilentlyContinue
}

# Check for Windows Hello / MFA indicators
try {
    $CredProvider = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\*" -ErrorAction SilentlyContinue
    $Result.MFAStatus.WindowsHelloConfigured = (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{D6886603-9D2F-4EB2-B667-1971041FA96B}")
} catch {
    $Result.MFAStatus.WindowsHelloConfigured = $false
}

$Result | ConvertTo-Json -Depth 4
''',

    remediate_script=r'''
# Remediate access control issues
$Result = @{ Success = $false; Actions = @() }

try {
    # Set minimum password length to 12
    net accounts /minpwlen:12 2>&1 | Out-Null
    $Result.Actions += "Set minimum password length to 12"

    # Set maximum password age to 90 days
    net accounts /maxpwage:90 2>&1 | Out-Null
    $Result.Actions += "Set maximum password age to 90 days"

    # Set account lockout threshold to 5
    net accounts /lockoutthreshold:5 2>&1 | Out-Null
    $Result.Actions += "Set account lockout threshold to 5"

    # Set lockout duration to 30 minutes
    net accounts /lockoutduration:30 2>&1 | Out-Null
    $Result.Actions += "Set account lockout duration to 30 minutes"

    # Enable password complexity via secedit
    $CfgFile = "$env:TEMP\secpol_fix.cfg"
    $DbFile = "$env:TEMP\secpol_fix.sdb"
    @"
[Unicode]
Unicode=yes
[System Access]
PasswordComplexity = 1
MinimumPasswordLength = 12
MaximumPasswordAge = 90
MinimumPasswordAge = 1
PasswordHistorySize = 12
"@ | Set-Content $CfgFile

    secedit /configure /db $DbFile /cfg $CfgFile /areas SECURITYPOLICY 2>&1 | Out-Null
    $Result.Actions += "Configured password complexity and policy via secedit"

    Remove-Item $CfgFile, $DbFile -Force -ErrorAction SilentlyContinue

    $Result.Success = $true
    $Result.Message = "Access control policies configured"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
$NetAccounts = net accounts 2>&1
$MinLength = 0
$LockoutThreshold = 0
foreach ($Line in $NetAccounts) {
    if ($Line -match "Minimum password length:\s*(\d+)") { $MinLength = [int]$Matches[1] }
    if ($Line -match "Lockout threshold:\s*(\d+)") { $LockoutThreshold = [int]$Matches[1] }
}
@{
    MinPasswordLength = $MinLength
    LockoutThreshold = $LockoutThreshold
    Verified = ($MinLength -ge 12 -and $LockoutThreshold -ge 3 -and $LockoutThreshold -le 5)
} | ConvertTo-Json
''',

    timeout_seconds=120,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["PasswordPolicy", "AccountLockout", "MFAStatus", "PrivilegedAccounts", "Issues"]
)


# =============================================================================
# RB-WIN-SEC-014: TLS/SSL Configuration
# =============================================================================

RUNBOOK_TLS_CONFIG = WindowsRunbook(
    id="RB-WIN-SEC-014",
    name="TLS/SSL Configuration",
    description="Ensure legacy TLS/SSL protocols are disabled and TLS 1.2+ is enforced",
    version="1.0",
    hipaa_controls=["164.312(e)(1)", "164.312(e)(2)(ii)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=True,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check TLS/SSL protocol configuration
$Result = @{
    Drifted = $false
    Issues = @()
    Protocols = @{}
}

$BasePath = "HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols"

# Protocols that must be DISABLED for HIPAA
$LegacyProtocols = @("SSL 2.0", "SSL 3.0", "TLS 1.0", "TLS 1.1")

# Protocols that must be ENABLED
$ModernProtocols = @("TLS 1.2")

try {
    # Check legacy protocols (should be disabled)
    foreach ($Protocol in $LegacyProtocols) {
        $ServerPath = "$BasePath\$Protocol\Server"
        $ClientPath = "$BasePath\$Protocol\Client"

        $ServerEnabled = (Get-ItemProperty -Path $ServerPath -Name "Enabled" -ErrorAction SilentlyContinue).Enabled
        $ServerDisabledByDefault = (Get-ItemProperty -Path $ServerPath -Name "DisabledByDefault" -ErrorAction SilentlyContinue).DisabledByDefault
        $ClientEnabled = (Get-ItemProperty -Path $ClientPath -Name "Enabled" -ErrorAction SilentlyContinue).Enabled

        $ProtocolInfo = @{
            ServerEnabled = if ($null -eq $ServerEnabled) { "NotConfigured" } else { $ServerEnabled }
            ServerDisabledByDefault = if ($null -eq $ServerDisabledByDefault) { "NotConfigured" } else { $ServerDisabledByDefault }
            ClientEnabled = if ($null -eq $ClientEnabled) { "NotConfigured" } else { $ClientEnabled }
        }
        $Result.Protocols[$Protocol] = $ProtocolInfo

        # Legacy protocol is drifted if not explicitly disabled
        if ($ServerEnabled -ne 0 -or $ServerDisabledByDefault -ne 1) {
            $Result.Drifted = $true
            $Result.Issues += "$Protocol server not explicitly disabled"
        }
        if ($ClientEnabled -ne 0) {
            $Result.Drifted = $true
            $Result.Issues += "$Protocol client not explicitly disabled"
        }
    }

    # Check modern protocols (should be enabled)
    foreach ($Protocol in $ModernProtocols) {
        $ServerPath = "$BasePath\$Protocol\Server"
        $ClientPath = "$BasePath\$Protocol\Client"

        $ServerEnabled = (Get-ItemProperty -Path $ServerPath -Name "Enabled" -ErrorAction SilentlyContinue).Enabled
        $ClientEnabled = (Get-ItemProperty -Path $ClientPath -Name "Enabled" -ErrorAction SilentlyContinue).Enabled

        $ProtocolInfo = @{
            ServerEnabled = if ($null -eq $ServerEnabled) { "NotConfigured" } else { $ServerEnabled }
            ClientEnabled = if ($null -eq $ClientEnabled) { "NotConfigured" } else { $ClientEnabled }
        }
        $Result.Protocols[$Protocol] = $ProtocolInfo

        # TLS 1.2 should be explicitly enabled
        if ($ServerEnabled -eq 0) {
            $Result.Drifted = $true
            $Result.Issues += "$Protocol server is disabled"
        }
    }

    # Check for weak cipher suites
    $WeakCiphers = Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Ciphers\RC4 128/128" -Name "Enabled" -ErrorAction SilentlyContinue
    if ($null -ne $WeakCiphers -and $WeakCiphers.Enabled -ne 0) {
        $Result.Issues += "RC4 cipher is still enabled"
    }
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Drifted = $true
}

$Result | ConvertTo-Json -Depth 3
''',

    remediate_script=r'''
# Disable legacy TLS/SSL protocols and enable TLS 1.2+
$Result = @{ Success = $false; Actions = @() }

try {
    $BasePath = "HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols"

    # Disable legacy protocols
    $LegacyProtocols = @("SSL 2.0", "SSL 3.0", "TLS 1.0", "TLS 1.1")

    foreach ($Protocol in $LegacyProtocols) {
        foreach ($Side in @("Server", "Client")) {
            $Path = "$BasePath\$Protocol\$Side"
            if (-not (Test-Path $Path)) {
                New-Item -Path $Path -Force | Out-Null
            }
            Set-ItemProperty -Path $Path -Name "Enabled" -Value 0 -Type DWord
            Set-ItemProperty -Path $Path -Name "DisabledByDefault" -Value 1 -Type DWord
        }
        $Result.Actions += "Disabled $Protocol (Server and Client)"
    }

    # Enable TLS 1.2
    foreach ($Side in @("Server", "Client")) {
        $Path = "$BasePath\TLS 1.2\$Side"
        if (-not (Test-Path $Path)) {
            New-Item -Path $Path -Force | Out-Null
        }
        Set-ItemProperty -Path $Path -Name "Enabled" -Value 1 -Type DWord
        Set-ItemProperty -Path $Path -Name "DisabledByDefault" -Value 0 -Type DWord
    }
    $Result.Actions += "Enabled TLS 1.2 (Server and Client)"

    # Enable TLS 1.3 if registry path is available
    foreach ($Side in @("Server", "Client")) {
        $Path = "$BasePath\TLS 1.3\$Side"
        if (-not (Test-Path $Path)) {
            New-Item -Path $Path -Force | Out-Null
        }
        Set-ItemProperty -Path $Path -Name "Enabled" -Value 1 -Type DWord
        Set-ItemProperty -Path $Path -Name "DisabledByDefault" -Value 0 -Type DWord
    }
    $Result.Actions += "Enabled TLS 1.3 (Server and Client)"

    # Disable RC4 cipher
    $RC4Path = "HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Ciphers\RC4 128/128"
    if (-not (Test-Path $RC4Path)) {
        New-Item -Path $RC4Path -Force | Out-Null
    }
    Set-ItemProperty -Path $RC4Path -Name "Enabled" -Value 0 -Type DWord
    $Result.Actions += "Disabled RC4 cipher"

    # Ensure .NET Framework uses strong crypto
    $NetFx64 = "HKLM:\SOFTWARE\Microsoft\.NETFramework\v4.0.30319"
    $NetFx32 = "HKLM:\SOFTWARE\Wow6432Node\Microsoft\.NETFramework\v4.0.30319"
    foreach ($Path in @($NetFx64, $NetFx32)) {
        if (Test-Path $Path) {
            Set-ItemProperty -Path $Path -Name "SchUseStrongCrypto" -Value 1 -Type DWord
        }
    }
    $Result.Actions += "Enabled .NET strong crypto"

    $Result.Success = $true
    $Result.Message = "TLS/SSL configuration hardened"
    $Result.Warning = "Reboot required for changes to take effect"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
# Verify TLS configuration
$BasePath = "HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols"

try {
    # Check TLS 1.0 is disabled
    $TLS10Enabled = (Get-ItemProperty -Path "$BasePath\TLS 1.0\Server" -Name "Enabled" -ErrorAction SilentlyContinue).Enabled
    $TLS10Disabled = ($TLS10Enabled -eq 0)

    # Check TLS 1.1 is disabled
    $TLS11Enabled = (Get-ItemProperty -Path "$BasePath\TLS 1.1\Server" -Name "Enabled" -ErrorAction SilentlyContinue).Enabled
    $TLS11Disabled = ($TLS11Enabled -eq 0)

    # Check SSL 3.0 is disabled
    $SSL3Enabled = (Get-ItemProperty -Path "$BasePath\SSL 3.0\Server" -Name "Enabled" -ErrorAction SilentlyContinue).Enabled
    $SSL3Disabled = ($SSL3Enabled -eq 0)

    # Check TLS 1.2 is enabled
    $TLS12Enabled = (Get-ItemProperty -Path "$BasePath\TLS 1.2\Server" -Name "Enabled" -ErrorAction SilentlyContinue).Enabled
    $TLS12Ok = ($TLS12Enabled -ne 0)

    @{
        TLS10Disabled = $TLS10Disabled
        TLS11Disabled = $TLS11Disabled
        SSL3Disabled = $SSL3Disabled
        TLS12Enabled = $TLS12Ok
        Verified = ($TLS10Disabled -and $TLS11Disabled -and $SSL3Disabled -and $TLS12Ok)
    } | ConvertTo-Json
} catch {
    @{ Verified = $false; Error = $_.Exception.Message } | ConvertTo-Json
}
''',

    timeout_seconds=120,
    requires_reboot=True,
    disruptive=False,
    evidence_fields=["Protocols", "Issues"]
)


# =============================================================================
# RB-WIN-SEC-015: USB/Removable Media Control
# =============================================================================

RUNBOOK_USB_CONTROL = WindowsRunbook(
    id="RB-WIN-SEC-015",
    name="USB/Removable Media Control",
    description="Restrict USB storage devices and disable autorun to prevent data exfiltration",
    version="1.0",
    hipaa_controls=["164.310(d)(1)", "164.312(a)(1)"],
    severity="medium",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=15,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check USB storage and autorun configuration
$Result = @{
    Drifted = $false
    Issues = @()
}

try {
    # Check USBSTOR service start type
    # 3 = Manual (default), 4 = Disabled
    $USBSTORKey = "HKLM:\SYSTEM\CurrentControlSet\Services\USBSTOR"
    $USBSTORStart = (Get-ItemProperty -Path $USBSTORKey -Name "Start" -ErrorAction SilentlyContinue).Start
    $Result.USBSTORStartType = $USBSTORStart

    $StartTypeName = switch ($USBSTORStart) {
        0 { "Boot" }
        1 { "System" }
        2 { "Automatic" }
        3 { "Manual" }
        4 { "Disabled" }
        default { "Unknown" }
    }
    $Result.USBSTORStartTypeName = $StartTypeName

    if ($USBSTORStart -ne 4) {
        $Result.Drifted = $true
        $Result.Issues += "USB storage service is not disabled (current: $StartTypeName)"
    }

    # Check AutoRun/AutoPlay settings
    $AutoRunKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer"
    $NoDriveTypeAutoRun = (Get-ItemProperty -Path $AutoRunKey -Name "NoDriveTypeAutoRun" -ErrorAction SilentlyContinue).NoDriveTypeAutoRun
    $Result.NoDriveTypeAutoRun = $NoDriveTypeAutoRun

    # 0xFF = Disable autorun for all drive types
    if ($NoDriveTypeAutoRun -ne 255) {
        $Result.Drifted = $true
        $Result.Issues += "AutoRun not fully disabled (should be 0xFF/255)"
    }

    # Check removable storage access policy (GPO)
    $RemovableKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\RemovableStorageDevices"
    $DenyAll = (Get-ItemProperty -Path "$RemovableKey\{53f5630d-b6bf-11d0-94f2-00a0c91efb8b}" -Name "Deny_All" -ErrorAction SilentlyContinue).Deny_All
    $Result.RemovableStorageDenied = ($DenyAll -eq 1)

    # Check for currently connected USB storage devices
    $USBDevices = Get-WmiObject Win32_DiskDrive | Where-Object { $_.InterfaceType -eq "USB" }
    $Result.ConnectedUSBDrives = @($USBDevices).Count
    if ($USBDevices) {
        $Result.Issues += "$(@($USBDevices).Count) USB storage device(s) currently connected"
    }
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Drifted = $true
}

$Result | ConvertTo-Json -Depth 2
''',

    remediate_script=r'''
# Disable USB storage and autorun
$Result = @{ Success = $false; Actions = @() }

try {
    # Disable USBSTOR service (set start type to 4 = Disabled)
    $USBSTORKey = "HKLM:\SYSTEM\CurrentControlSet\Services\USBSTOR"
    Set-ItemProperty -Path $USBSTORKey -Name "Start" -Value 4 -Type DWord
    $Result.Actions += "Disabled USBSTOR service (Start = 4)"

    # Disable AutoRun for all drive types
    $ExplorerKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer"
    if (-not (Test-Path $ExplorerKey)) {
        New-Item -Path $ExplorerKey -Force | Out-Null
    }
    Set-ItemProperty -Path $ExplorerKey -Name "NoDriveTypeAutoRun" -Value 255 -Type DWord
    $Result.Actions += "Disabled AutoRun for all drive types (0xFF)"

    # Disable AutoPlay
    Set-ItemProperty -Path $ExplorerKey -Name "NoAutorun" -Value 1 -Type DWord
    $Result.Actions += "Disabled AutoPlay"

    # Set removable storage deny policy
    $RemovableKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\RemovableStorageDevices\{53f5630d-b6bf-11d0-94f2-00a0c91efb8b}"
    if (-not (Test-Path $RemovableKey)) {
        New-Item -Path $RemovableKey -Force | Out-Null
    }
    Set-ItemProperty -Path $RemovableKey -Name "Deny_All" -Value 1 -Type DWord
    $Result.Actions += "Set removable storage deny policy"

    # Also set for WPD devices (phones, cameras)
    $WPDKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\RemovableStorageDevices\{6AC27878-A6FA-4155-BA85-F98F491D4F33}"
    if (-not (Test-Path $WPDKey)) {
        New-Item -Path $WPDKey -Force | Out-Null
    }
    Set-ItemProperty -Path $WPDKey -Name "Deny_All" -Value 1 -Type DWord
    $Result.Actions += "Set WPD device deny policy"

    $Result.Success = $true
    $Result.Message = "USB storage and autorun disabled"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
# Verify USB storage restrictions
try {
    $USBSTORStart = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\USBSTOR" -Name "Start" -ErrorAction SilentlyContinue).Start
    $AutoRun = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer" -Name "NoDriveTypeAutoRun" -ErrorAction SilentlyContinue).NoDriveTypeAutoRun

    @{
        USBSTORDisabled = ($USBSTORStart -eq 4)
        AutoRunDisabled = ($AutoRun -eq 255)
        Verified = ($USBSTORStart -eq 4 -and $AutoRun -eq 255)
    } | ConvertTo-Json
} catch {
    @{ Verified = $false; Error = $_.Exception.Message } | ConvertTo-Json
}
''',

    timeout_seconds=60,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["USBSTORStartType", "NoDriveTypeAutoRun", "ConnectedUSBDrives", "Issues"]
)


# =============================================================================
# RB-WIN-SEC-016: Screen Lock / Auto Logoff
# =============================================================================

RUNBOOK_SCREEN_LOCK = WindowsRunbook(
    id="RB-WIN-SEC-016",
    name="Screen Lock / Auto Logoff",
    description="Enforce screen saver timeout, password protection, and idle disconnect for HIPAA",
    version="1.0",
    hipaa_controls=["164.312(a)(2)(iii)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=15,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check screen lock and auto logoff settings
$Result = @{
    Drifted = $false
    Issues = @()
}

try {
    # Check screen saver timeout (machine-level GPO)
    $SSKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Control Panel\Desktop"
    $SSTimeout = (Get-ItemProperty -Path $SSKey -Name "ScreenSaveTimeOut" -ErrorAction SilentlyContinue).ScreenSaveTimeOut
    $SSActive = (Get-ItemProperty -Path $SSKey -Name "ScreenSaveActive" -ErrorAction SilentlyContinue).ScreenSaveActive
    $SSSecure = (Get-ItemProperty -Path $SSKey -Name "ScreenSaverIsSecure" -ErrorAction SilentlyContinue).ScreenSaverIsSecure

    # Also check user-level (fallback)
    if ($null -eq $SSTimeout) {
        $UserSSKey = "HKCU:\Control Panel\Desktop"
        $SSTimeout = (Get-ItemProperty -Path $UserSSKey -Name "ScreenSaveTimeOut" -ErrorAction SilentlyContinue).ScreenSaveTimeOut
        $SSActive = (Get-ItemProperty -Path $UserSSKey -Name "ScreenSaveActive" -ErrorAction SilentlyContinue).ScreenSaveActive
        $SSSecure = (Get-ItemProperty -Path $UserSSKey -Name "ScreenSaverIsSecure" -ErrorAction SilentlyContinue).ScreenSaverIsSecure
    }

    $Result.ScreenSaverTimeout = $SSTimeout
    $Result.ScreenSaverActive = $SSActive
    $Result.ScreenSaverSecure = $SSSecure

    # HIPAA requires 15 minutes or less
    if ($null -eq $SSTimeout -or [int]$SSTimeout -gt 900) {
        $Result.Drifted = $true
        $Result.Issues += "Screen saver timeout not set or exceeds 900 seconds (15 minutes)"
    }

    if ($SSActive -ne "1") {
        $Result.Drifted = $true
        $Result.Issues += "Screen saver is not active"
    }

    if ($SSSecure -ne "1") {
        $Result.Drifted = $true
        $Result.Issues += "Screen saver password protection not enabled"
    }

    # Check idle disconnect for RDP sessions
    $TSKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services"
    $IdleTimeout = (Get-ItemProperty -Path $TSKey -Name "MaxIdleTime" -ErrorAction SilentlyContinue).MaxIdleTime
    $DisconnectTimeout = (Get-ItemProperty -Path $TSKey -Name "MaxDisconnectionTime" -ErrorAction SilentlyContinue).MaxDisconnectionTime

    $Result.RDPIdleTimeoutMs = $IdleTimeout
    $Result.RDPDisconnectTimeoutMs = $DisconnectTimeout

    # Idle timeout should be 15 minutes (900000 ms) or less
    if ($null -eq $IdleTimeout -or $IdleTimeout -gt 900000 -or $IdleTimeout -eq 0) {
        $Result.Drifted = $true
        $Result.Issues += "RDP idle timeout not configured or exceeds 15 minutes"
    }

    # Check machine inactivity limit (local security policy)
    $InactivityKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
    $InactivityLimit = (Get-ItemProperty -Path $InactivityKey -Name "InactivityTimeoutSecs" -ErrorAction SilentlyContinue).InactivityTimeoutSecs
    $Result.InactivityTimeoutSecs = $InactivityLimit

    if ($null -eq $InactivityLimit -or $InactivityLimit -gt 900 -or $InactivityLimit -eq 0) {
        $Result.Drifted = $true
        $Result.Issues += "Machine inactivity timeout not configured or exceeds 900 seconds"
    }
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Drifted = $true
}

$Result | ConvertTo-Json -Depth 2
''',

    remediate_script=r'''
# Configure screen lock and idle disconnect settings
$Result = @{ Success = $false; Actions = @() }

try {
    # Set screen saver timeout to 900 seconds (15 minutes) via GPO registry
    $SSKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Control Panel\Desktop"
    if (-not (Test-Path $SSKey)) {
        New-Item -Path $SSKey -Force | Out-Null
    }
    Set-ItemProperty -Path $SSKey -Name "ScreenSaveTimeOut" -Value "900" -Type String
    $Result.Actions += "Set screen saver timeout to 900 seconds (15 minutes)"

    # Enable screen saver
    Set-ItemProperty -Path $SSKey -Name "ScreenSaveActive" -Value "1" -Type String
    $Result.Actions += "Enabled screen saver"

    # Require password on resume
    Set-ItemProperty -Path $SSKey -Name "ScreenSaverIsSecure" -Value "1" -Type String
    $Result.Actions += "Enabled screen saver password protection"

    # Configure RDP idle timeout (15 minutes = 900000 ms)
    $TSKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services"
    if (-not (Test-Path $TSKey)) {
        New-Item -Path $TSKey -Force | Out-Null
    }
    Set-ItemProperty -Path $TSKey -Name "MaxIdleTime" -Value 900000 -Type DWord
    $Result.Actions += "Set RDP idle timeout to 15 minutes"

    # Set disconnected session timeout to 5 minutes (300000 ms)
    Set-ItemProperty -Path $TSKey -Name "MaxDisconnectionTime" -Value 300000 -Type DWord
    $Result.Actions += "Set RDP disconnected session timeout to 5 minutes"

    # Set machine inactivity limit
    $InactivityKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
    Set-ItemProperty -Path $InactivityKey -Name "InactivityTimeoutSecs" -Value 900 -Type DWord
    $Result.Actions += "Set machine inactivity timeout to 900 seconds"

    $Result.Success = $true
    $Result.Message = "Screen lock and idle disconnect configured for HIPAA compliance"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
# Verify screen lock settings
try {
    $SSKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Control Panel\Desktop"
    $SSTimeout = (Get-ItemProperty -Path $SSKey -Name "ScreenSaveTimeOut" -ErrorAction SilentlyContinue).ScreenSaveTimeOut
    $SSSecure = (Get-ItemProperty -Path $SSKey -Name "ScreenSaverIsSecure" -ErrorAction SilentlyContinue).ScreenSaverIsSecure

    $TSKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services"
    $IdleTimeout = (Get-ItemProperty -Path $TSKey -Name "MaxIdleTime" -ErrorAction SilentlyContinue).MaxIdleTime

    $InactivityKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
    $InactivityLimit = (Get-ItemProperty -Path $InactivityKey -Name "InactivityTimeoutSecs" -ErrorAction SilentlyContinue).InactivityTimeoutSecs

    @{
        ScreenSaverTimeout = $SSTimeout
        ScreenSaverSecure = $SSSecure
        RDPIdleTimeoutMs = $IdleTimeout
        InactivityTimeoutSecs = $InactivityLimit
        Verified = (
            $SSTimeout -le 900 -and
            $SSSecure -eq "1" -and
            $IdleTimeout -le 900000 -and $IdleTimeout -gt 0 -and
            $InactivityLimit -le 900 -and $InactivityLimit -gt 0
        )
    } | ConvertTo-Json
} catch {
    @{ Verified = $false; Error = $_.Exception.Message } | ConvertTo-Json
}
''',

    timeout_seconds=60,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["ScreenSaverTimeout", "ScreenSaverSecure", "RDPIdleTimeoutMs", "InactivityTimeoutSecs", "Issues"]
)


# =============================================================================
# RB-WIN-SEC-017: Windows Defender Exclusion Audit
# =============================================================================

RUNBOOK_DEFENDER_EXCLUSIONS = WindowsRunbook(
    id="RB-WIN-SEC-017",
    name="Windows Defender Exclusion Audit",
    description="Detect and remove unauthorized Windows Defender exclusions that could hide malware",
    version="1.0",
    hipaa_controls=["164.308(a)(5)(ii)(B)", "164.312(b)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=1,
        retry_delay_seconds=10,
        requires_maintenance_window=False,
        allow_concurrent=False
    ),

    detect_script=r'''
try {
    $Prefs = Get-MpPreference

    # Collect all exclusion types
    $PathExclusions = @($Prefs.ExclusionPath | Where-Object { $_ })
    $ExtExclusions = @($Prefs.ExclusionExtension | Where-Object { $_ })
    $ProcessExclusions = @($Prefs.ExclusionProcess | Where-Object { $_ })

    # Known-suspicious patterns
    $SuspiciousPaths = @(
        'C:\Windows\Temp',
        'C:\Temp',
        'C:\Users\Public',
        'C:\ProgramData',
        'C:\'
    )
    $SuspiciousExtensions = @('exe', 'dll', 'bat', 'cmd', 'ps1', 'vbs', 'js', 'wsf', 'scr', 'com')

    $Unauthorized = @()
    $Details = @()

    # Check path exclusions
    foreach ($Path in $PathExclusions) {
        $IsSuspicious = $false
        foreach ($Pattern in $SuspiciousPaths) {
            if ($Path -like "$Pattern*") {
                $IsSuspicious = $true
                break
            }
        }
        if ($IsSuspicious) {
            $Unauthorized += $Path
            $Details += "Suspicious path exclusion: $Path"
        }
    }

    # Check extension exclusions
    foreach ($Ext in $ExtExclusions) {
        $NormExt = $Ext.TrimStart('.')
        if ($SuspiciousExtensions -contains $NormExt) {
            $Unauthorized += "ext:$NormExt"
            $Details += "Dangerous extension exclusion: .$NormExt"
        }
    }

    # Check process exclusions for suspicious patterns
    foreach ($Proc in $ProcessExclusions) {
        if ($Proc -match '\\Temp\\|\\Downloads\\|\\Public\\|\\AppData\\Local\\Temp') {
            $Unauthorized += "proc:$Proc"
            $Details += "Suspicious process exclusion: $Proc"
        }
    }

    $Drifted = $Unauthorized.Count -gt 0

    @{
        Drifted = $Drifted
        PathExclusions = $PathExclusions
        ExtensionExclusions = $ExtExclusions
        ProcessExclusions = $ProcessExclusions
        UnauthorizedCount = $Unauthorized.Count
        UnauthorizedExclusions = $Unauthorized
        Details = $Details
    } | ConvertTo-Json -Depth 3
} catch {
    @{ Drifted = $false; Error = $_.Exception.Message } | ConvertTo-Json
}
''',

    remediate_script=r'''
try {
    $Prefs = Get-MpPreference
    $Removed = @()

    # Suspicious paths to remove
    $SuspiciousPaths = @(
        'C:\Windows\Temp',
        'C:\Temp',
        'C:\Users\Public',
        'C:\ProgramData',
        'C:\'
    )
    $SuspiciousExtensions = @('exe', 'dll', 'bat', 'cmd', 'ps1', 'vbs', 'js', 'wsf', 'scr', 'com')

    # Remove suspicious path exclusions
    foreach ($Path in @($Prefs.ExclusionPath | Where-Object { $_ })) {
        foreach ($Pattern in $SuspiciousPaths) {
            if ($Path -like "$Pattern*") {
                Remove-MpPreference -ExclusionPath $Path -ErrorAction Stop
                $Removed += "path:$Path"
                break
            }
        }
    }

    # Remove suspicious extension exclusions
    foreach ($Ext in @($Prefs.ExclusionExtension | Where-Object { $_ })) {
        $NormExt = $Ext.TrimStart('.')
        if ($SuspiciousExtensions -contains $NormExt) {
            Remove-MpPreference -ExclusionExtension $Ext -ErrorAction Stop
            $Removed += "ext:$Ext"
        }
    }

    # Remove suspicious process exclusions
    foreach ($Proc in @($Prefs.ExclusionProcess | Where-Object { $_ })) {
        if ($Proc -match '\\Temp\\|\\Downloads\\|\\Public\\|\\AppData\\Local\\Temp') {
            Remove-MpPreference -ExclusionProcess $Proc -ErrorAction Stop
            $Removed += "proc:$Proc"
        }
    }

    @{
        Success = $true
        RemovedCount = $Removed.Count
        RemovedExclusions = $Removed
    } | ConvertTo-Json -Depth 3
} catch {
    @{ Success = $false; Error = $_.Exception.Message } | ConvertTo-Json
}
''',

    verify_script=r'''
try {
    $Prefs = Get-MpPreference

    $SuspiciousPaths = @(
        'C:\Windows\Temp',
        'C:\Temp',
        'C:\Users\Public',
        'C:\ProgramData',
        'C:\'
    )
    $SuspiciousExtensions = @('exe', 'dll', 'bat', 'cmd', 'ps1', 'vbs', 'js', 'wsf', 'scr', 'com')

    $Remaining = @()

    foreach ($Path in @($Prefs.ExclusionPath | Where-Object { $_ })) {
        foreach ($Pattern in $SuspiciousPaths) {
            if ($Path -like "$Pattern*") {
                $Remaining += "path:$Path"
                break
            }
        }
    }

    foreach ($Ext in @($Prefs.ExclusionExtension | Where-Object { $_ })) {
        $NormExt = $Ext.TrimStart('.')
        if ($SuspiciousExtensions -contains $NormExt) {
            $Remaining += "ext:$Ext"
        }
    }

    @{
        Verified = ($Remaining.Count -eq 0)
        RemainingCount = $Remaining.Count
        RemainingExclusions = $Remaining
        TotalPathExclusions = @($Prefs.ExclusionPath | Where-Object { $_ }).Count
        TotalExtExclusions = @($Prefs.ExclusionExtension | Where-Object { $_ }).Count
        TotalProcessExclusions = @($Prefs.ExclusionProcess | Where-Object { $_ }).Count
    } | ConvertTo-Json -Depth 3
} catch {
    @{ Verified = $false; Error = $_.Exception.Message } | ConvertTo-Json
}
''',

    timeout_seconds=120,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["UnauthorizedExclusions", "RemovedExclusions", "RemainingExclusions"]
)


# =============================================================================
# Security Runbooks Registry
# =============================================================================

# =============================================================================
# RB-WIN-SEC-018: Suspicious Scheduled Task Removal
# =============================================================================

RUNBOOK_SCHED_TASK_PERSIST = WindowsRunbook(
    id="RB-WIN-SEC-018",
    name="Suspicious Scheduled Task Removal",
    description="Detect and remove suspicious scheduled tasks used for persistence",
    version="1.0",
    hipaa_controls=["164.308(a)(1)(ii)(D)", "164.312(b)"],
    severity="critical",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=10,
        requires_maintenance_window=False,
        allow_concurrent=False
    ),

    detect_script=r'''
$suspicious = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
    $_.TaskName -notmatch '^(Microsoft|Google|Adobe|Mozilla|OneDrive|MicrosoftEdge|Optimize|Scheduled|User_Feed|CreateExplorerShellUnelevatedTask)' -and
    $_.TaskPath -eq '\' -and
    $_.State -ne 'Disabled'
} | ForEach-Object {
    $action = ($_.Actions | Select-Object -First 1).Execute
    if ($action -and $action -notmatch '(svchost|taskhost|consent|SystemSettings|WindowsUpdate|defrag|SilentCleanup)') {
        @{TaskName=$_.TaskName; Execute=$action; State=$_.State.ToString()}
    }
}
if ($suspicious) {
    @{Status='FAIL'; Details="Suspicious scheduled tasks found"; Tasks=$suspicious} | ConvertTo-Json -Compress
} else {
    @{Status='PASS'; Details="No suspicious scheduled tasks"} | ConvertTo-Json -Compress
}
''',

    remediate_script=r'''
$removed = @()
$failed = @()
Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
    $_.TaskName -notmatch '^(Microsoft|Google|Adobe|Mozilla|OneDrive|MicrosoftEdge|Optimize|Scheduled|User_Feed|CreateExplorerShellUnelevatedTask)' -and
    $_.TaskPath -eq '\' -and
    $_.State -ne 'Disabled'
} | ForEach-Object {
    $action = ($_.Actions | Select-Object -First 1).Execute
    if ($action -and $action -notmatch '(svchost|taskhost|consent|SystemSettings|WindowsUpdate|defrag|SilentCleanup)') {
        try {
            Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false -ErrorAction Stop
            $removed += $_.TaskName
        } catch {
            $failed += @{TaskName=$_.TaskName; Error=$_.Exception.Message}
        }
    }
}
@{Removed=$removed; Failed=$failed} | ConvertTo-Json -Compress
''',

    verify_script=r'''
$remaining = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
    $_.TaskName -notmatch '^(Microsoft|Google|Adobe|Mozilla|OneDrive|MicrosoftEdge|Optimize|Scheduled|User_Feed|CreateExplorerShellUnelevatedTask)' -and
    $_.TaskPath -eq '\' -and
    $_.State -ne 'Disabled'
} | ForEach-Object {
    $action = ($_.Actions | Select-Object -First 1).Execute
    if ($action -and $action -notmatch '(svchost|taskhost|consent|SystemSettings|WindowsUpdate|defrag|SilentCleanup)') {
        @{TaskName=$_.TaskName; Execute=$action}
    }
}
if ($remaining) {
    @{Status='FAIL'; Details="Suspicious tasks still present"; Tasks=$remaining} | ConvertTo-Json -Compress
} else {
    @{Status='PASS'; Details="All suspicious scheduled tasks removed"} | ConvertTo-Json -Compress
}
'''
)


# =============================================================================
# RB-WIN-SEC-019: Suspicious Registry Run Key Removal
# =============================================================================

RUNBOOK_REGISTRY_PERSIST = WindowsRunbook(
    id="RB-WIN-SEC-019",
    name="Suspicious Registry Run Key Removal",
    description="Detect and remove suspicious Run/RunOnce registry entries used for persistence",
    version="1.0",
    hipaa_controls=["164.308(a)(1)(ii)(D)", "164.312(b)"],
    severity="critical",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=10,
        requires_maintenance_window=False,
        allow_concurrent=False
    ),

    detect_script=r'''
$found = @()
$paths = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce'
)
foreach ($p in $paths) {
    $props = Get-ItemProperty -Path $p -ErrorAction SilentlyContinue
    if ($props) {
        $props.PSObject.Properties | Where-Object {
            $_.Name -notmatch '^(PS|VMware|SecurityHealth|RealTimeProtection|Windows)' -and
            $_.Value -match '\.(exe|bat|cmd|ps1|vbs|js)' -and
            $_.Value -notmatch '(Program Files|Windows|Microsoft|VMware)'
        } | ForEach-Object {
            $found += @{Name=$_.Name; Value=$_.Value; Path=$p}
        }
    }
}
if ($found.Count -gt 0) {
    @{Status='FAIL'; Details="Suspicious Run entries found"; Entries=$found} | ConvertTo-Json -Compress
} else {
    @{Status='PASS'; Details="No suspicious Run entries"} | ConvertTo-Json -Compress
}
''',

    remediate_script=r'''
$removed = @()
$failed = @()
$paths = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce'
)
foreach ($p in $paths) {
    $props = Get-ItemProperty -Path $p -ErrorAction SilentlyContinue
    if ($props) {
        $props.PSObject.Properties | Where-Object {
            $_.Name -notmatch '^(PS|VMware|SecurityHealth|RealTimeProtection|Windows)' -and
            $_.Value -match '\.(exe|bat|cmd|ps1|vbs|js)' -and
            $_.Value -notmatch '(Program Files|Windows|Microsoft|VMware)'
        } | ForEach-Object {
            try {
                Remove-ItemProperty -Path $p -Name $_.Name -Force -ErrorAction Stop
                $removed += @{Name=$_.Name; Path=$p}
            } catch {
                $failed += @{Name=$_.Name; Path=$p; Error=$_.Exception.Message}
            }
        }
    }
}
@{Removed=$removed; Failed=$failed} | ConvertTo-Json -Compress
''',

    verify_script=r'''
$remaining = @()
$paths = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce'
)
foreach ($p in $paths) {
    $props = Get-ItemProperty -Path $p -ErrorAction SilentlyContinue
    if ($props) {
        $props.PSObject.Properties | Where-Object {
            $_.Name -notmatch '^(PS|VMware|SecurityHealth|RealTimeProtection|Windows)' -and
            $_.Value -match '\.(exe|bat|cmd|ps1|vbs|js)' -and
            $_.Value -notmatch '(Program Files|Windows|Microsoft|VMware)'
        } | ForEach-Object {
            $remaining += @{Name=$_.Name; Value=$_.Value; Path=$p}
        }
    }
}
if ($remaining.Count -gt 0) {
    @{Status='FAIL'; Details="Suspicious entries still present"; Entries=$remaining} | ConvertTo-Json -Compress
} else {
    @{Status='PASS'; Details="All suspicious Run entries removed"} | ConvertTo-Json -Compress
}
'''
)


# =============================================================================
# RB-WIN-SEC-020: SMBv1 Protocol Disabling
# =============================================================================

RUNBOOK_SMB1_DISABLE = WindowsRunbook(
    id="RB-WIN-SEC-020",
    name="SMBv1 Protocol Disabling",
    description="Disable insecure SMBv1 protocol to prevent EternalBlue-class attacks",
    version="1.0",
    hipaa_controls=["164.312(e)(1)", "164.312(e)(2)(i)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check if SMBv1 protocol is enabled
$Result = @{
    Drifted = $false
    Issues = @()
}

try {
    # Check via SMB Server Configuration
    $SmbConfig = Get-SmbServerConfiguration -ErrorAction Stop
    $Result.EnableSMB1Protocol = $SmbConfig.EnableSMB1Protocol

    if ($SmbConfig.EnableSMB1Protocol) {
        $Result.Drifted = $true
        $Result.Issues += "SMBv1 protocol is enabled on server"
    }

    # Also check Windows Optional Feature (may differ from config)
    $Feature = Get-WindowsOptionalFeature -Online -FeatureName SMB1Protocol -ErrorAction SilentlyContinue
    if ($Feature) {
        $Result.SMB1FeatureState = $Feature.State.ToString()
        if ($Feature.State -eq "Enabled") {
            $Result.Drifted = $true
            $Result.Issues += "SMB1Protocol Windows feature is enabled"
        }
    }
} catch {
    $Result.Error = $_.Exception.Message
    # If Get-SmbServerConfiguration fails, check registry directly
    $regPath = "HKLM:\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters"
    $smb1Val = (Get-ItemProperty -Path $regPath -Name SMB1 -ErrorAction SilentlyContinue).SMB1
    if ($null -eq $smb1Val -or $smb1Val -ne 0) {
        $Result.Drifted = $true
        $Result.Issues += "SMBv1 not explicitly disabled in registry"
    }
    $Result.SMB1RegistryValue = $smb1Val
}

$Result | ConvertTo-Json -Depth 2
''',

    remediate_script=r'''
# Disable SMBv1 protocol
$Result = @{ Success = $false; Actions = @() }

try {
    # Disable via SMB Server Configuration (immediate effect)
    Set-SmbServerConfiguration -EnableSMB1Protocol $false -Confirm:$false -ErrorAction Stop
    $Result.Actions += "Disabled SMBv1 via Set-SmbServerConfiguration"

    # Also set registry value for persistence across reboots
    $regPath = "HKLM:\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters"
    Set-ItemProperty -Path $regPath -Name SMB1 -Value 0 -Type DWord -ErrorAction SilentlyContinue
    $Result.Actions += "Set SMB1=0 in registry"

    # Disable Windows Optional Feature (prevents re-enablement)
    $Feature = Get-WindowsOptionalFeature -Online -FeatureName SMB1Protocol -ErrorAction SilentlyContinue
    if ($Feature -and $Feature.State -eq "Enabled") {
        Disable-WindowsOptionalFeature -Online -FeatureName SMB1Protocol -NoRestart -ErrorAction SilentlyContinue
        $Result.Actions += "Disabled SMB1Protocol Windows feature (reboot may be needed)"
    }

    $Result.Success = $true
    $Result.Message = "SMBv1 protocol disabled"
} catch {
    $Result.Error = $_.Exception.Message
    # Fallback: try registry-only approach
    try {
        $regPath = "HKLM:\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters"
        Set-ItemProperty -Path $regPath -Name SMB1 -Value 0 -Type DWord
        $Result.Actions += "Fallback: Set SMB1=0 in registry"
        $Result.Success = $true
    } catch {
        $Result.FallbackError = $_.Exception.Message
    }
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
$SmbConfig = Get-SmbServerConfiguration -ErrorAction SilentlyContinue
$enabled = if ($SmbConfig) { $SmbConfig.EnableSMB1Protocol } else { $null }
$regPath = "HKLM:\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters"
$regVal = (Get-ItemProperty -Path $regPath -Name SMB1 -ErrorAction SilentlyContinue).SMB1
@{
    EnableSMB1Protocol = $enabled
    SMB1RegistryValue = $regVal
    Verified = ($enabled -eq $false -or $regVal -eq 0)
} | ConvertTo-Json
''',

    timeout_seconds=120,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["EnableSMB1Protocol", "SMB1FeatureState", "SMB1RegistryValue", "Issues"]
)


# =============================================================================
# RB-WIN-SEC-021: WMI Event Subscription Persistence Detection
# =============================================================================

RUNBOOK_WMI_PERSIST = WindowsRunbook(
    id="RB-WIN-SEC-021",
    name="WMI Event Subscription Persistence Removal",
    description="Detect and remove malicious WMI event subscriptions used for persistence",
    version="1.0",
    hipaa_controls=["164.308(a)(5)(ii)(C)", "164.312(a)(1)"],
    severity="critical",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check for suspicious WMI event subscriptions (persistence mechanism)
$Result = @{
    Drifted = $false
    Issues = @()
    Filters = @()
    Consumers = @()
    Bindings = @()
}

# Known safe system WMI filter/consumer names
$SafeNames = @(
    'BVTFilter',
    'SCM Event Log Filter',
    '__InstanceOperationEvent',
    'Microsoft-Windows-*',
    'WMI Self-Instrumentation*'
)

function Test-IsSafe($name) {
    foreach ($safe in $SafeNames) {
        if ($name -like $safe) { return $true }
    }
    return $false
}

try {
    # Check EventFilters
    $filters = Get-WmiObject -Namespace root\subscription -Class __EventFilter -ErrorAction SilentlyContinue
    foreach ($f in $filters) {
        if (-not (Test-IsSafe $f.Name)) {
            $Result.Drifted = $true
            $Result.Filters += @{
                Name = $f.Name
                Query = $f.QueryLanguage + ": " + $f.Query
            }
            $Result.Issues += "Suspicious EventFilter: $($f.Name)"
        }
    }

    # Check EventConsumers (multiple types)
    $consumerClasses = @(
        'CommandLineEventConsumer',
        'ActiveScriptEventConsumer',
        'LogFileEventConsumer'
    )
    foreach ($cls in $consumerClasses) {
        $consumers = Get-WmiObject -Namespace root\subscription -Class $cls -ErrorAction SilentlyContinue
        foreach ($c in $consumers) {
            if (-not (Test-IsSafe $c.Name)) {
                $Result.Drifted = $true
                $consumerInfo = @{ Name = $c.Name; Type = $cls }
                if ($c.CommandLineTemplate) { $consumerInfo.Command = $c.CommandLineTemplate }
                if ($c.ScriptText) { $consumerInfo.Script = $c.ScriptText.Substring(0, [Math]::Min(200, $c.ScriptText.Length)) }
                $Result.Consumers += $consumerInfo
                $Result.Issues += "Suspicious $cls`: $($c.Name)"
            }
        }
    }

    # Check FilterToConsumerBindings
    $bindings = Get-WmiObject -Namespace root\subscription -Class __FilterToConsumerBinding -ErrorAction SilentlyContinue
    foreach ($b in $bindings) {
        $filterName = ($b.Filter -split '"')[1]
        $consumerName = ($b.Consumer -split '"')[1]
        if (-not (Test-IsSafe $filterName) -or -not (Test-IsSafe $consumerName)) {
            $Result.Drifted = $true
            $Result.Bindings += @{
                Filter = $filterName
                Consumer = $consumerName
            }
        }
    }
} catch {
    $Result.Error = $_.Exception.Message
}

$Result.TotalSuspicious = $Result.Filters.Count + $Result.Consumers.Count
$Result | ConvertTo-Json -Depth 3
''',

    remediate_script=r'''
# Remove suspicious WMI event subscriptions
$Result = @{ Success = $false; Actions = @(); Errors = @() }

$SafeNames = @(
    'BVTFilter',
    'SCM Event Log Filter',
    '__InstanceOperationEvent',
    'Microsoft-Windows-*',
    'WMI Self-Instrumentation*'
)

function Test-IsSafe($name) {
    foreach ($safe in $SafeNames) {
        if ($name -like $safe) { return $true }
    }
    return $false
}

try {
    # Remove bindings first (must be removed before filters/consumers)
    $bindings = Get-WmiObject -Namespace root\subscription -Class __FilterToConsumerBinding -ErrorAction SilentlyContinue
    foreach ($b in $bindings) {
        $filterName = ($b.Filter -split '"')[1]
        $consumerName = ($b.Consumer -split '"')[1]
        if (-not (Test-IsSafe $filterName) -or -not (Test-IsSafe $consumerName)) {
            try {
                $b | Remove-WmiObject
                $Result.Actions += "Removed binding: $filterName -> $consumerName"
            } catch {
                $Result.Errors += "Failed to remove binding: $($_.Exception.Message)"
            }
        }
    }

    # Remove suspicious EventFilters
    $filters = Get-WmiObject -Namespace root\subscription -Class __EventFilter -ErrorAction SilentlyContinue
    foreach ($f in $filters) {
        if (-not (Test-IsSafe $f.Name)) {
            try {
                $f | Remove-WmiObject
                $Result.Actions += "Removed EventFilter: $($f.Name)"
            } catch {
                $Result.Errors += "Failed to remove filter $($f.Name): $($_.Exception.Message)"
            }
        }
    }

    # Remove suspicious EventConsumers
    $consumerClasses = @(
        'CommandLineEventConsumer',
        'ActiveScriptEventConsumer',
        'LogFileEventConsumer'
    )
    foreach ($cls in $consumerClasses) {
        $consumers = Get-WmiObject -Namespace root\subscription -Class $cls -ErrorAction SilentlyContinue
        foreach ($c in $consumers) {
            if (-not (Test-IsSafe $c.Name)) {
                try {
                    $c | Remove-WmiObject
                    $Result.Actions += "Removed $cls`: $($c.Name)"
                } catch {
                    $Result.Errors += "Failed to remove consumer $($c.Name): $($_.Exception.Message)"
                }
            }
        }
    }

    $Result.Success = ($Result.Errors.Count -eq 0)
    $Result.Message = "Removed $($Result.Actions.Count) WMI persistence objects"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
# Verify no suspicious WMI subscriptions remain
$suspicious = 0
$SafeNames = @('BVTFilter','SCM Event Log Filter','__InstanceOperationEvent','Microsoft-Windows-*','WMI Self-Instrumentation*')

function Test-IsSafe($name) {
    foreach ($safe in $SafeNames) { if ($name -like $safe) { return $true } }
    return $false
}

$filters = Get-WmiObject -Namespace root\subscription -Class __EventFilter -ErrorAction SilentlyContinue
foreach ($f in $filters) { if (-not (Test-IsSafe $f.Name)) { $suspicious++ } }

$consumerClasses = @('CommandLineEventConsumer','ActiveScriptEventConsumer','LogFileEventConsumer')
foreach ($cls in $consumerClasses) {
    $consumers = Get-WmiObject -Namespace root\subscription -Class $cls -ErrorAction SilentlyContinue
    foreach ($c in $consumers) { if (-not (Test-IsSafe $c.Name)) { $suspicious++ } }
}

@{
    SuspiciousRemaining = $suspicious
    Verified = ($suspicious -eq 0)
} | ConvertTo-Json
''',

    timeout_seconds=120,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["TotalSuspicious", "Filters", "Consumers", "Bindings", "Issues"]
)


# =============================================================================
# Combined Security Runbook Registry
# =============================================================================

SECURITY_RUNBOOKS: Dict[str, WindowsRunbook] = {
    "RB-WIN-SEC-001": RUNBOOK_FIREWALL_ENABLE,
    "RB-WIN-SEC-002": RUNBOOK_AUDIT_POLICY,
    "RB-WIN-SEC-003": RUNBOOK_LOCKOUT_POLICY,
    "RB-WIN-SEC-004": RUNBOOK_PASSWORD_POLICY,
    "RB-WIN-SEC-005": RUNBOOK_BITLOCKER_STATUS,
    "RB-WIN-SEC-006": RUNBOOK_DEFENDER_REALTIME,
    "RB-WIN-SEC-007": RUNBOOK_SMB_SIGNING,
    "RB-WIN-SEC-008": RUNBOOK_NTLM_SECURITY,
    "RB-WIN-SEC-009": RUNBOOK_UNAUTHORIZED_USERS,
    "RB-WIN-SEC-010": RUNBOOK_NLA_ENFORCEMENT,
    "RB-WIN-SEC-011": RUNBOOK_UAC_ENFORCEMENT,
    "RB-WIN-SEC-012": RUNBOOK_EVENT_LOG_PROTECTION,
    "RB-WIN-SEC-013": RUNBOOK_CREDENTIAL_GUARD,
    "RB-WIN-SEC-014": RUNBOOK_TLS_CONFIG,
    "RB-WIN-SEC-015": RUNBOOK_USB_CONTROL,
    "RB-WIN-SEC-016": RUNBOOK_SCREEN_LOCK,
    "RB-WIN-SEC-017": RUNBOOK_DEFENDER_EXCLUSIONS,
    "RB-WIN-SEC-018": RUNBOOK_SCHED_TASK_PERSIST,
    "RB-WIN-SEC-019": RUNBOOK_REGISTRY_PERSIST,
    "RB-WIN-SEC-020": RUNBOOK_SMB1_DISABLE,
    "RB-WIN-SEC-021": RUNBOOK_WMI_PERSIST,
    "RB-WIN-ACCESS-001": RUNBOOK_ACCESS_CONTROL,
}
