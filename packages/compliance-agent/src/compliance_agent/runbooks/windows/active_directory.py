"""
Windows Active Directory Runbooks for HIPAA Compliance.

Runbooks for AD-specific issues and domain trust.
"""

from typing import Dict, List
from dataclasses import dataclass, field
from .runbooks import WindowsRunbook, ExecutionConstraints


# =============================================================================
# RB-WIN-AD-002: Computer Account Password Reset
# =============================================================================

RUNBOOK_COMPUTER_ACCOUNT = WindowsRunbook(
    id="RB-WIN-AD-002",
    name="Computer Account Password Reset",
    description="Reset machine account password when domain trust relationship is broken",
    version="1.0",
    hipaa_controls=["164.312(a)(1)", "164.308(a)(4)(ii)(B)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=1,
        retry_delay_seconds=60,
        requires_maintenance_window=True,  # Requires domain admin or local admin
        allow_concurrent=False
    ),

    detect_script=r'''
# Check domain trust relationship
$Result = @{
    Drifted = $false
}

# Check if machine is domain-joined
$ComputerSystem = Get-WmiObject Win32_ComputerSystem
$Result.IsDomainJoined = $ComputerSystem.PartOfDomain
$Result.Domain = $ComputerSystem.Domain
$Result.ComputerName = $ComputerSystem.Name

if (-not $ComputerSystem.PartOfDomain) {
    $Result.Note = "Machine is not domain-joined"
    $Result | ConvertTo-Json
    return
}

# Test secure channel
try {
    $SecureChannel = Test-ComputerSecureChannel -ErrorAction Stop
    $Result.SecureChannelValid = $SecureChannel

    if (-not $SecureChannel) {
        $Result.Drifted = $true
        $Result.DriftReason = "Secure channel to domain is broken"
    }
} catch {
    $Result.SecureChannelValid = $false
    $Result.Drifted = $true
    $Result.DriftReason = $_.Exception.Message
}

# Check machine account password age
try {
    $MachineAccount = Get-ADComputer $env:COMPUTERNAME -Properties PasswordLastSet -ErrorAction Stop
    $PasswordAge = (Get-Date) - $MachineAccount.PasswordLastSet
    $Result.PasswordAgeDays = [math]::Round($PasswordAge.TotalDays, 0)
    $Result.PasswordLastSet = $MachineAccount.PasswordLastSet.ToString("o")

    # Machine account passwords should auto-rotate every 30 days
    if ($PasswordAge.TotalDays -gt 45) {
        $Result.Drifted = $true
        $Result.DriftReason = "Machine account password is stale"
    }
} catch {
    # May fail if secure channel is broken
    $Result.ADQueryFailed = $true
}

# Check domain controller connectivity
try {
    $DC = [System.DirectoryServices.ActiveDirectory.Domain]::GetCurrentDomain().DomainControllers[0]
    $Result.DomainController = $DC.Name
    $DCPing = Test-Connection -ComputerName $DC.Name -Count 2 -Quiet
    $Result.DCReachable = $DCPing
} catch {
    $Result.DCReachable = $false
}

$Result | ConvertTo-Json
''',

    remediate_script=r'''
# Reset computer account password
$Result = @{ Success = $false; Actions = @() }

try {
    $ComputerSystem = Get-WmiObject Win32_ComputerSystem

    if (-not $ComputerSystem.PartOfDomain) {
        $Result.Success = $true
        $Result.Message = "Machine not domain-joined - no action needed"
        $Result | ConvertTo-Json
        return
    }

    # First, test if secure channel is actually broken
    $SecureChannel = Test-ComputerSecureChannel -ErrorAction SilentlyContinue

    if ($SecureChannel) {
        $Result.Success = $true
        $Result.Message = "Secure channel is already valid"
        $Result | ConvertTo-Json
        return
    }

    # Attempt to repair secure channel
    # This requires local admin rights but NOT domain connectivity
    $RepairResult = Test-ComputerSecureChannel -Repair -ErrorAction Stop

    if ($RepairResult) {
        $Result.Success = $true
        $Result.Actions += "Repaired secure channel"
        $Result.Message = "Domain trust restored successfully"
    } else {
        # If repair fails, the machine may need to be re-joined
        $Result.Success = $false
        $Result.Actions += "Repair attempt failed"
        $Result.Message = "Manual intervention required - may need domain rejoin"
        $Result.ManualSteps = @(
            "1. Log in with local administrator account",
            "2. Remove computer from domain: Remove-Computer -UnjoinDomainCredential (Get-Credential) -Force",
            "3. Restart computer",
            "4. Rejoin domain: Add-Computer -DomainName $($ComputerSystem.Domain) -Credential (Get-Credential) -Restart"
        )
    }
} catch {
    $Result.Error = $_.Exception.Message

    # Provide guidance for manual fix
    $Result.ManualSteps = @(
        "Trust repair failed. To fix manually:",
        "1. Log in with local admin (.\Administrator)",
        "2. Open PowerShell as Admin",
        "3. Run: Reset-ComputerMachinePassword -Credential (Get-Credential)",
        "4. Enter domain admin credentials when prompted"
    )
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
$ComputerSystem = Get-WmiObject Win32_ComputerSystem
if (-not $ComputerSystem.PartOfDomain) {
    @{ Verified = $true; Note = "Not domain-joined" } | ConvertTo-Json
} else {
    $SecureChannel = Test-ComputerSecureChannel -ErrorAction SilentlyContinue
    @{
        SecureChannelValid = $SecureChannel
        Verified = $SecureChannel
    } | ConvertTo-Json
}
''',

    timeout_seconds=180,
    requires_reboot=False,  # May require reboot if domain rejoin is needed
    disruptive=True,  # Can cause authentication issues during repair
    evidence_fields=["SecureChannelValid", "PasswordAgeDays", "DCReachable", "DriftReason"]
)


# =============================================================================
# RB-WIN-AD-003: Group Policy Compliance
# =============================================================================

RUNBOOK_GPO_COMPLIANCE = WindowsRunbook(
    id="RB-WIN-AD-003",
    name="Group Policy Compliance",
    description="Verify Group Policy application status and force update if GPOs are failing",
    version="1.0",
    hipaa_controls=["164.308(a)(3)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=60,
        requires_maintenance_window=False,
        allow_concurrent=False
    ),

    detect_script=r'''
# Check Group Policy application status
$Result = @{
    Drifted = $false
    Issues = @()
}

try {
    # Check if machine is domain-joined
    $ComputerSystem = Get-WmiObject Win32_ComputerSystem
    $Result.IsDomainJoined = $ComputerSystem.PartOfDomain

    if (-not $ComputerSystem.PartOfDomain) {
        $Result.Note = "Machine is not domain-joined, GPO check not applicable"
        $Result | ConvertTo-Json -Depth 2
        return
    }

    # Get GPO application status via gpresult
    $GPResultXml = gpresult /x "$env:TEMP\gpreport.xml" /f 2>&1
    if (Test-Path "$env:TEMP\gpreport.xml") {
        [xml]$GPReport = Get-Content "$env:TEMP\gpreport.xml"

        # Check computer GPO status
        $ComputerResults = $GPReport.Rsop.ComputerResults
        if ($ComputerResults) {
            $Result.LastGPUpdateComputer = $ComputerResults.EventsDetails.SinglePassEventsDetails.LastPolicyApplicationTime

            # Check for GPO errors
            $GPOErrors = $ComputerResults.ExtensionData | Where-Object { $_.Extension.EventsDetails.EventRecord.EventDescription -like "*failed*" }
            if ($GPOErrors) {
                $Result.Drifted = $true
                $Result.Issues += "Computer GPO processing errors detected"
            }
        }

        # Check applied GPOs
        $AppliedGPOs = @()
        $ComputerGPOs = $ComputerResults.GPO
        foreach ($GPO in $ComputerGPOs) {
            $AppliedGPOs += @{
                Name = $GPO.Name
                Enabled = $GPO.Enabled
                AccessDenied = $GPO.AccessDenied
                Link = $GPO.Link.SOMPath
            }

            if ($GPO.AccessDenied -eq "true") {
                $Result.Issues += "Access denied on GPO: $($GPO.Name)"
            }
        }
        $Result.AppliedGPOCount = $AppliedGPOs.Count
        $Result.AppliedGPOs = $AppliedGPOs

        Remove-Item "$env:TEMP\gpreport.xml" -Force -ErrorAction SilentlyContinue
    }

    # Check last GP update time via registry
    $GPHistoryKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Group Policy\State\Machine\Extension-List\{00000000-0000-0000-0000-000000000000}"
    $LastUpdate = (Get-ItemProperty -Path $GPHistoryKey -Name "startTimeHi" -ErrorAction SilentlyContinue)
    if ($null -ne $LastUpdate) {
        $LastUpdateTime = [DateTime]::FromFileTime(([Int64]$LastUpdate.startTimeHi -shl 32) -bor $LastUpdate.startTimeLo)
        $UpdateAge = (Get-Date) - $LastUpdateTime
        $Result.LastGPUpdateAge = [math]::Round($UpdateAge.TotalHours, 1)

        # GP should update at least every 2 hours (default is 90 minutes)
        if ($UpdateAge.TotalHours -gt 4) {
            $Result.Drifted = $true
            $Result.Issues += "Group Policy not updated in $([math]::Round($UpdateAge.TotalHours, 1)) hours"
        }
    }

    # Check for pending GPO operations
    $EventLog = Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-GroupPolicy';Level=2} -MaxEvents 5 -ErrorAction SilentlyContinue
    if ($EventLog) {
        $Result.Drifted = $true
        $Result.RecentGPOErrors = @($EventLog | Select-Object -First 3 | ForEach-Object {
            @{ TimeCreated = $_.TimeCreated.ToString("o"); Message = $_.Message.Substring(0, [math]::Min(200, $_.Message.Length)) }
        })
        $Result.Issues += "$(@($EventLog).Count) recent Group Policy errors in event log"
    }
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Drifted = $true
}

$Result | ConvertTo-Json -Depth 4
''',

    remediate_script=r'''
# Force Group Policy update and clear cache if needed
$Result = @{ Success = $false; Actions = @() }

try {
    $ComputerSystem = Get-WmiObject Win32_ComputerSystem
    if (-not $ComputerSystem.PartOfDomain) {
        $Result.Success = $true
        $Result.Message = "Machine not domain-joined - no action needed"
        $Result | ConvertTo-Json
        return
    }

    # Force GP update
    $GPUpdate = gpupdate /force /wait:120 2>&1
    $Result.Actions += "Forced Group Policy update"
    $Result.GPUpdateOutput = ($GPUpdate | Out-String).Trim()

    # Check if update was successful
    $GPUpdateSuccess = $GPUpdate -match "completed successfully|successfully"
    if (-not $GPUpdateSuccess) {
        # Clear GP cache and retry
        $GPCachePath = "$env:WINDIR\System32\GroupPolicy\Machine\Registry.pol"
        if (Test-Path $GPCachePath) {
            Remove-Item $GPCachePath -Force -ErrorAction SilentlyContinue
            $Result.Actions += "Cleared local GP cache (Registry.pol)"
        }

        # Clear GP history
        $GPHistoryPath = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Group Policy\History"
        if (Test-Path $GPHistoryPath) {
            Remove-Item -Path $GPHistoryPath -Recurse -Force -ErrorAction SilentlyContinue
            $Result.Actions += "Cleared GP history"
        }

        # Retry GP update
        $GPUpdate2 = gpupdate /force /wait:120 2>&1
        $Result.Actions += "Retried Group Policy update after cache clear"
    }

    $Result.Success = $true
    $Result.Message = "Group Policy update forced"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
# Verify GPO application
try {
    $ComputerSystem = Get-WmiObject Win32_ComputerSystem
    if (-not $ComputerSystem.PartOfDomain) {
        @{ Verified = $true; Note = "Not domain-joined" } | ConvertTo-Json
        return
    }

    # Check for recent GP errors
    $RecentErrors = Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-GroupPolicy';Level=2;StartTime=(Get-Date).AddMinutes(-10)} -MaxEvents 1 -ErrorAction SilentlyContinue
    $NoRecentErrors = ($null -eq $RecentErrors)

    # Check last successful update
    $SuccessEvents = Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-GroupPolicy';Id=1502} -MaxEvents 1 -ErrorAction SilentlyContinue
    $RecentSuccess = ($null -ne $SuccessEvents -and (Get-Date) - $SuccessEvents.TimeCreated -lt (New-TimeSpan -Minutes 10))

    @{
        NoRecentErrors = $NoRecentErrors
        RecentSuccessfulUpdate = $RecentSuccess
        Verified = ($NoRecentErrors -or $RecentSuccess)
    } | ConvertTo-Json
} catch {
    @{ Verified = $false; Error = $_.Exception.Message } | ConvertTo-Json
}
''',

    timeout_seconds=300,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["AppliedGPOCount", "AppliedGPOs", "LastGPUpdateAge", "RecentGPOErrors", "Issues"]
)


# =============================================================================
# RB-WIN-AD-004: LAPS (Local Admin Passwords)
# =============================================================================

RUNBOOK_LAPS = WindowsRunbook(
    id="RB-WIN-AD-004",
    name="LAPS (Local Admin Passwords)",
    description="Check LAPS installation and password rotation status for local admin accounts",
    version="1.0",
    hipaa_controls=["164.312(d)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=1,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check LAPS (Local Administrator Password Solution) status
$Result = @{
    Drifted = $false
    Issues = @()
}

try {
    # Check if machine is domain-joined
    $ComputerSystem = Get-WmiObject Win32_ComputerSystem
    $Result.IsDomainJoined = $ComputerSystem.PartOfDomain

    if (-not $ComputerSystem.PartOfDomain) {
        $Result.Note = "Machine is not domain-joined, LAPS not applicable"
        $Result | ConvertTo-Json -Depth 2
        return
    }

    # Check if LAPS CSE (Client Side Extension) is installed
    $LAPSInstalled = $false

    # Check for legacy LAPS (Microsoft LAPS)
    $LegacyLAPS = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*" -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -like "*Local Administrator Password Solution*" }
    if ($LegacyLAPS) {
        $LAPSInstalled = $true
        $Result.LAPSType = "Legacy (Microsoft LAPS)"
        $Result.LAPSVersion = $LegacyLAPS.DisplayVersion
    }

    # Check for Windows LAPS (built-in, Windows Server 2019+ / Windows 10 21H2+)
    $WindowsLAPSKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\LAPS\Config"
    if (Test-Path $WindowsLAPSKey) {
        $LAPSInstalled = $true
        $Result.LAPSType = "Windows LAPS (built-in)"
    }

    # Check for LAPS CSE DLL
    $LAPSDll = Test-Path "$env:ProgramFiles\LAPS\CSE\AdmPwd.dll"
    if ($LAPSDll) {
        $LAPSInstalled = $true
    }

    $Result.LAPSInstalled = $LAPSInstalled

    if (-not $LAPSInstalled) {
        $Result.Drifted = $true
        $Result.Issues += "LAPS is not installed on this machine"
    }

    # Check if LAPS GPO is configured
    $LAPSPolicyKey = "HKLM:\SOFTWARE\Policies\Microsoft Services\AdmPwd"
    $LAPSEnabled = (Get-ItemProperty -Path $LAPSPolicyKey -Name "AdmPwdEnabled" -ErrorAction SilentlyContinue).AdmPwdEnabled
    $Result.LAPSPolicyEnabled = ($LAPSEnabled -eq 1)

    if ($LAPSInstalled -and $LAPSEnabled -ne 1) {
        $Result.Drifted = $true
        $Result.Issues += "LAPS is installed but not enabled via policy"
    }

    # Check password age settings
    $PasswordAge = (Get-ItemProperty -Path $LAPSPolicyKey -Name "PasswordAgeDays" -ErrorAction SilentlyContinue).PasswordAgeDays
    $Result.PasswordAgeDays = $PasswordAge

    if ($null -ne $PasswordAge -and $PasswordAge -gt 30) {
        $Result.Issues += "LAPS password age exceeds 30 days (current: $PasswordAge)"
    }

    # Check password complexity settings
    $Complexity = (Get-ItemProperty -Path $LAPSPolicyKey -Name "PasswordComplexity" -ErrorAction SilentlyContinue).PasswordComplexity
    $Result.PasswordComplexity = $Complexity
    # 1=Large letters, 2=Large+small, 3=Large+small+numbers, 4=Large+small+numbers+specials

    # Try to check AD attribute for password expiry
    try {
        Import-Module ActiveDirectory -ErrorAction Stop
        $Computer = Get-ADComputer $env:COMPUTERNAME -Properties "ms-Mcs-AdmPwdExpirationTime" -ErrorAction Stop
        $ExpiryTime = $Computer."ms-Mcs-AdmPwdExpirationTime"
        if ($ExpiryTime) {
            $ExpiryDate = [DateTime]::FromFileTime($ExpiryTime)
            $Result.PasswordExpiryDate = $ExpiryDate.ToString("o")
            $DaysUntilExpiry = ($ExpiryDate - (Get-Date)).Days
            $Result.DaysUntilExpiry = $DaysUntilExpiry

            if ($DaysUntilExpiry -lt 0) {
                $Result.Drifted = $true
                $Result.Issues += "LAPS password has expired ($([math]::Abs($DaysUntilExpiry)) days ago)"
            }
        }
    } catch {
        $Result.ADQueryNote = "Could not query AD for LAPS attributes: $($_.Exception.Message)"
    }
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Drifted = $true
}

$Result | ConvertTo-Json -Depth 2
''',

    remediate_script=r'''
# LAPS remediation - ALERT ONLY
# LAPS requires AD schema extension and GPO configuration, cannot auto-fix
$Result = @{
    Action = "ALERT"
    Success = $false
    Actions = @()
}

try {
    $ComputerSystem = Get-WmiObject Win32_ComputerSystem
    if (-not $ComputerSystem.PartOfDomain) {
        $Result.Message = "Machine not domain-joined - LAPS not applicable"
        $Result.Success = $true
        $Result | ConvertTo-Json -Depth 2
        return
    }

    # Check what's missing
    $LAPSInstalled = (Test-Path "$env:ProgramFiles\LAPS\CSE\AdmPwd.dll") -or
                     (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\LAPS\Config")

    if (-not $LAPSInstalled) {
        $Result.Message = "LAPS is not installed. Manual intervention required."
        $Result.Recommendation = @(
            "For Legacy LAPS: Download and install from Microsoft - https://www.microsoft.com/en-us/download/details.aspx?id=46899",
            "For Windows LAPS (Server 2019+): Enable via Windows Features or Group Policy",
            "AD schema extension required: Import-Module AdmPwd.PS; Update-AdmPwdADSchema",
            "Configure GPO: Computer Configuration > Admin Templates > LAPS"
        )
    } else {
        # LAPS is installed but may not be configured
        $LAPSPolicyKey = "HKLM:\SOFTWARE\Policies\Microsoft Services\AdmPwd"
        $LAPSEnabled = (Get-ItemProperty -Path $LAPSPolicyKey -Name "AdmPwdEnabled" -ErrorAction SilentlyContinue).AdmPwdEnabled

        if ($LAPSEnabled -ne 1) {
            $Result.Message = "LAPS is installed but not enabled via Group Policy"
            $Result.Recommendation = @(
                "Enable LAPS via GPO: Computer Configuration > Admin Templates > LAPS > Enable local admin password management",
                "Set password complexity and age requirements",
                "Ensure AD permissions are configured for LAPS attributes"
            )
        } else {
            $Result.Message = "LAPS is installed and enabled. Checking for stale password."
            # Try to trigger password reset
            try {
                Import-Module AdmPwd.PS -ErrorAction Stop
                Reset-AdmPwdPassword -ComputerName $env:COMPUTERNAME -ErrorAction Stop
                $Result.Actions += "Triggered LAPS password reset"
                $Result.Success = $true
            } catch {
                $Result.Actions += "Could not trigger password reset: $($_.Exception.Message)"
            }
        }
    }

    $Result.Warning = "LAPS deployment requires AD admin privileges and schema changes"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
# Verify LAPS status
try {
    $LAPSInstalled = (Test-Path "$env:ProgramFiles\LAPS\CSE\AdmPwd.dll") -or
                     (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\LAPS\Config")

    $LAPSPolicyKey = "HKLM:\SOFTWARE\Policies\Microsoft Services\AdmPwd"
    $LAPSEnabled = (Get-ItemProperty -Path $LAPSPolicyKey -Name "AdmPwdEnabled" -ErrorAction SilentlyContinue).AdmPwdEnabled

    # Try to check password age in AD
    $PasswordAge = $null
    try {
        Import-Module ActiveDirectory -ErrorAction Stop
        $Computer = Get-ADComputer $env:COMPUTERNAME -Properties "ms-Mcs-AdmPwdExpirationTime" -ErrorAction Stop
        $ExpiryTime = $Computer."ms-Mcs-AdmPwdExpirationTime"
        if ($ExpiryTime) {
            $ExpiryDate = [DateTime]::FromFileTime($ExpiryTime)
            $PasswordAge = ((Get-Date) - $ExpiryDate.AddDays(-30)).Days
        }
    } catch { }

    @{
        LAPSInstalled = $LAPSInstalled
        LAPSEnabled = ($LAPSEnabled -eq 1)
        PasswordAgeDays = $PasswordAge
        Verified = ($LAPSInstalled -and $LAPSEnabled -eq 1)
    } | ConvertTo-Json
} catch {
    @{ Verified = $false; Error = $_.Exception.Message } | ConvertTo-Json
}
''',

    timeout_seconds=120,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["LAPSInstalled", "LAPSType", "LAPSPolicyEnabled", "PasswordAgeDays", "DaysUntilExpiry", "Issues"]
)


# =============================================================================
# Active Directory Runbooks Registry
# =============================================================================

AD_RUNBOOKS: Dict[str, WindowsRunbook] = {
    "RB-WIN-AD-002": RUNBOOK_COMPUTER_ACCOUNT,
    "RB-WIN-AD-003": RUNBOOK_GPO_COMPLIANCE,
    "RB-WIN-AD-004": RUNBOOK_LAPS,
}
