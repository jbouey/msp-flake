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
# Security Runbooks Registry
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
}
