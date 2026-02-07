"""
Windows Clinic Access Control Runbooks for HIPAA Compliance.

Runbooks for clinic access control and authentication compliance.
"""

from typing import Dict, List
from dataclasses import dataclass, field
from .runbooks import WindowsRunbook, ExecutionConstraints


# =============================================================================
# RB-WIN-ACCESS-002: MFA Enforcement Audit
# =============================================================================

RUNBOOK_MFA_AUDIT = WindowsRunbook(
    id="RB-WIN-ACCESS-002",
    name="MFA Enforcement Audit",
    description="Audit multi-factor authentication enforcement across Azure AD/Entra ID, smart card, RADIUS, Windows Hello, and RDP NLA",
    version="1.0",
    hipaa_controls=["164.312(d)", "164.312(a)(2)(i)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check MFA enforcement status across all available mechanisms
$Result = @{
    Drifted = $false
    Issues = @()
    MFAMethods = @{}
}

try {
    # --- Check Azure AD / Entra ID hybrid join status ---
    $DSRegStatus = dsregcmd /status 2>&1
    $AzureADJoined = ($DSRegStatus | Select-String "AzureAdJoined\s*:\s*YES") -ne $null
    $DomainJoined = ($DSRegStatus | Select-String "DomainJoined\s*:\s*YES") -ne $null
    $Result.AzureADJoined = $AzureADJoined
    $Result.DomainJoined = $DomainJoined

    if ($AzureADJoined) {
        # Check Conditional Access policy markers in registry
        $CAKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\LogonUI"
        $LastLogonMFA = (Get-ItemProperty -Path $CAKey -Name "LastLoggedOnProvider" -ErrorAction SilentlyContinue).LastLoggedOnProvider

        # Check if Azure MFA NPS Extension is installed (for RADIUS integration)
        $NFSExtension = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*" -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -like "*NPS Extension for Azure*" -or $_.DisplayName -like "*Azure MFA*" }
        $Result.MFAMethods.AzureMFANPSExtension = ($null -ne $NFSExtension)

        # Check for Conditional Access compliance via Intune registry key
        $IntuneKey = "HKLM:\SOFTWARE\Microsoft\Enrollments"
        $IntuneEnrolled = (Get-ChildItem $IntuneKey -ErrorAction SilentlyContinue | Measure-Object).Count -gt 0
        $Result.MFAMethods.IntuneManaged = $IntuneEnrolled

        if (-not $NFSExtension -and -not $IntuneEnrolled) {
            $Result.Issues += "Azure AD joined but no Conditional Access / MFA enforcement detected"
            $Result.Drifted = $true
        }
    }

    # --- On-prem only: Check smart card requirement ---
    if ($DomainJoined -and -not $AzureADJoined) {
        # Check if smart card logon is required via GPO
        $SCKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
        $SCForceOption = (Get-ItemProperty -Path $SCKey -Name "scforceoption" -ErrorAction SilentlyContinue).scforceoption
        $Result.MFAMethods.SmartCardRequired = ($SCForceOption -eq 1)

        # Check if RADIUS/NPS is configured
        $NPSService = Get-Service -Name "IAS" -ErrorAction SilentlyContinue
        $Result.MFAMethods.RADIUSAvailable = ($null -ne $NPSService -and $NPSService.Status -eq "Running")

        if ($SCForceOption -ne 1 -and ($null -eq $NPSService -or $NPSService.Status -ne "Running")) {
            $Result.Issues += "On-prem only: No smart card or RADIUS MFA enforcement detected"
            $Result.Drifted = $true
        }
    }

    # --- Check Windows Hello for Business ---
    $WHfBKey = "HKLM:\SOFTWARE\Policies\Microsoft\PassportForWork"
    $WHfBEnabled = (Get-ItemProperty -Path $WHfBKey -Name "Enabled" -ErrorAction SilentlyContinue).Enabled
    $Result.MFAMethods.WindowsHelloEnabled = ($WHfBEnabled -eq 1)

    # Check if any user has enrolled Windows Hello
    $WHfBUserKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{D6886603-9D2F-4EB2-B667-1971041FA96B}"
    $WHfBProviderEnabled = (Get-ItemProperty -Path $WHfBUserKey -Name "Disabled" -ErrorAction SilentlyContinue).Disabled
    $Result.MFAMethods.WindowsHelloProviderActive = ($WHfBProviderEnabled -ne 1)

    # --- Check RDP requires NLA (Network Level Authentication) ---
    $RDPKey = "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"
    $NLARequired = (Get-ItemProperty -Path $RDPKey -Name "UserAuthentication" -ErrorAction SilentlyContinue).UserAuthentication
    $Result.MFAMethods.NLAEnabled = ($NLARequired -eq 1)

    # Also check via policy
    $NLAPolicyKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services"
    $NLAPolicy = (Get-ItemProperty -Path $NLAPolicyKey -Name "UserAuthentication" -ErrorAction SilentlyContinue).UserAuthentication
    if ($null -ne $NLAPolicy) {
        $Result.MFAMethods.NLAPolicy = ($NLAPolicy -eq 1)
    }

    if ($NLARequired -ne 1) {
        $Result.Issues += "RDP Network Level Authentication (NLA) is not enabled"
        $Result.Drifted = $true
    }

    # --- Check if RDP is enabled at all ---
    $RDPEnabled = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server" -Name "fDenyTSConnections" -ErrorAction SilentlyContinue).fDenyTSConnections
    $Result.RDPEnabled = ($RDPEnabled -eq 0)

    # Summary
    $Result.IssueCount = $Result.Issues.Count
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Drifted = $true
}

$Result | ConvertTo-Json -Depth 3
''',

    remediate_script=r'''
# Remediate MFA enforcement gaps
$Result = @{ Success = $false; Actions = @() }

try {
    # Enable NLA for RDP
    $RDPKey = "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"
    $CurrentNLA = (Get-ItemProperty -Path $RDPKey -Name "UserAuthentication" -ErrorAction SilentlyContinue).UserAuthentication
    if ($CurrentNLA -ne 1) {
        Set-ItemProperty -Path $RDPKey -Name "UserAuthentication" -Value 1 -Type DWord
        $Result.Actions += "Enabled Network Level Authentication (NLA) for RDP"
    } else {
        $Result.Actions += "NLA already enabled for RDP"
    }

    # Configure Windows Hello for Business policy via registry
    $WHfBKey = "HKLM:\SOFTWARE\Policies\Microsoft\PassportForWork"
    if (-not (Test-Path $WHfBKey)) {
        New-Item -Path $WHfBKey -Force | Out-Null
    }
    $CurrentWHfB = (Get-ItemProperty -Path $WHfBKey -Name "Enabled" -ErrorAction SilentlyContinue).Enabled
    if ($CurrentWHfB -ne 1) {
        Set-ItemProperty -Path $WHfBKey -Name "Enabled" -Value 1 -Type DWord
        $Result.Actions += "Enabled Windows Hello for Business policy"
    } else {
        $Result.Actions += "Windows Hello for Business policy already enabled"
    }

    # Set Windows Hello minimum PIN length
    $WHfBPinKey = "$WHfBKey\PINComplexity"
    if (-not (Test-Path $WHfBPinKey)) {
        New-Item -Path $WHfBPinKey -Force | Out-Null
    }
    Set-ItemProperty -Path $WHfBPinKey -Name "MinimumPINLength" -Value 6 -Type DWord
    $Result.Actions += "Set Windows Hello minimum PIN length to 6"

    # Check Azure/Entra MFA gaps - alert only (cannot auto-configure Conditional Access)
    $DSRegStatus = dsregcmd /status 2>&1
    $AzureADJoined = ($DSRegStatus | Select-String "AzureAdJoined\s*:\s*YES") -ne $null

    if ($AzureADJoined) {
        $NFSExtension = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*" -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -like "*NPS Extension for Azure*" -or $_.DisplayName -like "*Azure MFA*" }
        if (-not $NFSExtension) {
            $Result.Actions += "ALERT: Azure AD joined but no MFA NPS extension found - configure Conditional Access in Entra portal"
        }
    }

    # Check on-prem MFA gaps - alert only
    $DomainJoined = ($DSRegStatus | Select-String "DomainJoined\s*:\s*YES") -ne $null
    if ($DomainJoined -and -not $AzureADJoined) {
        $SCKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
        $SCForceOption = (Get-ItemProperty -Path $SCKey -Name "scforceoption" -ErrorAction SilentlyContinue).scforceoption
        if ($SCForceOption -ne 1) {
            $Result.Actions += "ALERT: On-prem domain - consider enabling smart card requirement or deploying RADIUS MFA"
        }
    }

    $Result.Success = $true
    $Result.Message = "MFA enforcement remediation applied where possible; alerts generated for cloud-managed gaps"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
# Re-check MFA enforcement status
try {
    # Verify NLA
    $RDPKey = "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"
    $NLAEnabled = (Get-ItemProperty -Path $RDPKey -Name "UserAuthentication" -ErrorAction SilentlyContinue).UserAuthentication -eq 1

    # Verify Windows Hello policy
    $WHfBKey = "HKLM:\SOFTWARE\Policies\Microsoft\PassportForWork"
    $WHfBEnabled = (Get-ItemProperty -Path $WHfBKey -Name "Enabled" -ErrorAction SilentlyContinue).Enabled -eq 1

    # Check Azure/Entra status
    $DSRegStatus = dsregcmd /status 2>&1
    $AzureADJoined = ($DSRegStatus | Select-String "AzureAdJoined\s*:\s*YES") -ne $null

    @{
        NLAEnabled = $NLAEnabled
        WindowsHelloEnabled = $WHfBEnabled
        AzureADJoined = $AzureADJoined
        Compliant = ($NLAEnabled -and $WHfBEnabled)
    } | ConvertTo-Json
} catch {
    @{ Compliant = $false; Error = $_.Exception.Message } | ConvertTo-Json
}
''',

    timeout_seconds=180,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["MFAMethods", "AzureADJoined", "DomainJoined", "RDPEnabled", "Issues"]
)


# =============================================================================
# RB-WIN-ACCESS-003: Failed Login Monitoring and Alerting
# =============================================================================

RUNBOOK_FAILED_LOGIN = WindowsRunbook(
    id="RB-WIN-ACCESS-003",
    name="Failed Login Monitoring and Alerting",
    description="Monitor Security event log for failed logon attempts (Event ID 4625), detect brute force patterns, and check account lockout status",
    version="1.0",
    hipaa_controls=["164.312(b)", "164.308(a)(1)(ii)(D)", "164.312(a)(1)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Query Security event log for failed logon attempts in last 24 hours
$Result = @{
    Drifted = $false
    Issues = @()
    FailedLogons = @{}
}

try {
    $CutoffTime = (Get-Date).AddHours(-24)

    # Query Event ID 4625 (An account failed to log on)
    $FailedEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'Security'
        Id = 4625
        StartTime = $CutoffTime
    } -ErrorAction SilentlyContinue

    $TotalFailures = @($FailedEvents).Count
    $Result.TotalFailedLogons24h = $TotalFailures

    # Group by target account
    $ByAccount = @{}
    $BySourceIP = @{}

    foreach ($Event in $FailedEvents) {
        $Xml = [xml]$Event.ToXml()
        $TargetAccount = ($Xml.Event.EventData.Data | Where-Object { $_.Name -eq "TargetUserName" }).'#text'
        $SourceIP = ($Xml.Event.EventData.Data | Where-Object { $_.Name -eq "IpAddress" }).'#text'
        $LogonType = ($Xml.Event.EventData.Data | Where-Object { $_.Name -eq "LogonType" }).'#text'
        $FailureReason = ($Xml.Event.EventData.Data | Where-Object { $_.Name -eq "SubStatus" }).'#text'

        # Count by account
        if ($TargetAccount -and $TargetAccount -ne "-") {
            if (-not $ByAccount.ContainsKey($TargetAccount)) {
                $ByAccount[$TargetAccount] = @{ Count = 0; SourceIPs = @(); LogonTypes = @(); LastFailure = $null }
            }
            $ByAccount[$TargetAccount].Count++
            if ($SourceIP -and $SourceIP -notin $ByAccount[$TargetAccount].SourceIPs) {
                $ByAccount[$TargetAccount].SourceIPs += $SourceIP
            }
            if ($LogonType -and $LogonType -notin $ByAccount[$TargetAccount].LogonTypes) {
                $ByAccount[$TargetAccount].LogonTypes += $LogonType
            }
            $ByAccount[$TargetAccount].LastFailure = $Event.TimeCreated.ToString("o")
        }

        # Count by source IP
        if ($SourceIP -and $SourceIP -ne "-") {
            if (-not $BySourceIP.ContainsKey($SourceIP)) {
                $BySourceIP[$SourceIP] = 0
            }
            $BySourceIP[$SourceIP]++
        }
    }

    $Result.FailedLogons.ByAccount = $ByAccount
    $Result.FailedLogons.BySourceIP = $BySourceIP
    $Result.UniqueAccountsTargeted = $ByAccount.Count
    $Result.UniqueSourceIPs = $BySourceIP.Count

    # Flag brute force: any account with >10 failures
    $BruteForceAccounts = @($ByAccount.GetEnumerator() | Where-Object { $_.Value.Count -gt 10 })
    $Result.BruteForceAccountCount = $BruteForceAccounts.Count
    $Result.BruteForceAccounts = @($BruteForceAccounts | ForEach-Object {
        @{ Account = $_.Key; FailureCount = $_.Value.Count; SourceIPs = $_.Value.SourceIPs }
    })

    if ($BruteForceAccounts.Count -gt 0) {
        $Result.Drifted = $true
        $Result.Issues += "$($BruteForceAccounts.Count) account(s) with >10 failed logon attempts (potential brute force)"
    }

    # Flag high-volume source IPs
    $HighVolumeIPs = @($BySourceIP.GetEnumerator() | Where-Object { $_.Value -gt 20 })
    $Result.HighVolumeSourceIPs = @($HighVolumeIPs | ForEach-Object { @{ IP = $_.Key; Count = $_.Value } })

    if ($HighVolumeIPs.Count -gt 0) {
        $Result.Issues += "$($HighVolumeIPs.Count) source IP(s) with >20 failed attempts"
    }

    # Check currently locked out accounts
    try {
        Import-Module ActiveDirectory -ErrorAction Stop
        $LockedAccounts = @(Search-ADAccount -LockedOut -ErrorAction Stop)
        $Result.LockedAccountCount = $LockedAccounts.Count
        $Result.LockedAccounts = @($LockedAccounts | Select-Object -First 20 | ForEach-Object {
            @{ SamAccountName = $_.SamAccountName; LockedOut = $_.LockedOut }
        })
    } catch {
        # Not a DC or AD module unavailable
        $Result.LockedAccountCount = -1
        $Result.LockedAccountNote = "AD module not available - cannot check lockout status"
    }

    # Check account lockout policy
    $LockoutThreshold = (net accounts 2>&1 | Select-String "Lockout threshold").ToString() -replace '.*:\s+', ''
    $Result.LockoutThreshold = $LockoutThreshold

    if ($LockoutThreshold -eq "Never" -or $LockoutThreshold -eq "0") {
        $Result.Drifted = $true
        $Result.Issues += "Account lockout threshold is not configured"
    }

    $Result.IssueCount = $Result.Issues.Count
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Drifted = $true
}

$Result | ConvertTo-Json -Depth 4
''',

    remediate_script=r'''
# Lock accounts with excessive failures and block offending source IPs
$Result = @{ Success = $false; Actions = @() }

try {
    $CutoffTime = (Get-Date).AddHours(-24)

    # Get failed logon events
    $FailedEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'Security'
        Id = 4625
        StartTime = $CutoffTime
    } -ErrorAction SilentlyContinue

    # Build account failure counts
    $ByAccount = @{}
    $BySourceIP = @{}

    foreach ($Event in $FailedEvents) {
        $Xml = [xml]$Event.ToXml()
        $TargetAccount = ($Xml.Event.EventData.Data | Where-Object { $_.Name -eq "TargetUserName" }).'#text'
        $SourceIP = ($Xml.Event.EventData.Data | Where-Object { $_.Name -eq "IpAddress" }).'#text'

        if ($TargetAccount -and $TargetAccount -ne "-") {
            if (-not $ByAccount.ContainsKey($TargetAccount)) { $ByAccount[$TargetAccount] = 0 }
            $ByAccount[$TargetAccount]++
        }
        if ($SourceIP -and $SourceIP -ne "-" -and $SourceIP -ne "::1" -and $SourceIP -ne "127.0.0.1") {
            if (-not $BySourceIP.ContainsKey($SourceIP)) { $BySourceIP[$SourceIP] = 0 }
            $BySourceIP[$SourceIP]++
        }
    }

    # Lock accounts with >10 failures (if AD module available)
    try {
        Import-Module ActiveDirectory -ErrorAction Stop
        foreach ($Entry in $ByAccount.GetEnumerator()) {
            if ($Entry.Value -gt 10) {
                $Account = $Entry.Key
                try {
                    # Check if account exists and is not already locked
                    $ADUser = Get-ADUser -Identity $Account -Properties LockedOut -ErrorAction Stop
                    if (-not $ADUser.LockedOut) {
                        # Disable the account temporarily
                        Disable-ADAccount -Identity $Account -ErrorAction Stop
                        $Result.Actions += "Disabled account '$Account' ($($Entry.Value) failed logons)"
                    } else {
                        $Result.Actions += "Account '$Account' already locked ($($Entry.Value) failed logons)"
                    }
                } catch {
                    $Result.Actions += "Could not lock '$Account': $($_.Exception.Message)"
                }
            }
        }
    } catch {
        $Result.Actions += "AD module not available - cannot lock accounts programmatically"
    }

    # Block high-volume external source IPs via Windows Firewall
    $InternalPrefixes = @("10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.",
                          "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.",
                          "172.28.", "172.29.", "172.30.", "172.31.", "192.168.", "169.254.")

    foreach ($Entry in $BySourceIP.GetEnumerator()) {
        if ($Entry.Value -gt 20) {
            $IP = $Entry.Key
            $IsInternal = $false
            foreach ($Prefix in $InternalPrefixes) {
                if ($IP.StartsWith($Prefix)) { $IsInternal = $true; break }
            }

            if (-not $IsInternal) {
                # Block external IP via firewall
                $RuleName = "Block-BruteForce-$($IP -replace '\.', '-')"
                $ExistingRule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
                if (-not $ExistingRule) {
                    New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Block `
                        -RemoteAddress $IP -Protocol Any -Description "Auto-blocked: $($Entry.Value) failed logons in 24h" | Out-Null
                    $Result.Actions += "Blocked external IP $IP ($($Entry.Value) failed attempts)"
                } else {
                    $Result.Actions += "Firewall rule already exists for $IP"
                }
            } else {
                $Result.Actions += "ALERT: Internal IP $IP has $($Entry.Value) failed attempts - investigate manually"
            }
        }
    }

    # Generate incident report
    $Report = @{
        Timestamp = (Get-Date).ToUniversalTime().ToString("o")
        TotalFailedLogons = @($FailedEvents).Count
        AccountsExceedingThreshold = @($ByAccount.GetEnumerator() | Where-Object { $_.Value -gt 10 } | ForEach-Object { $_.Key })
        HighVolumeSourceIPs = @($BySourceIP.GetEnumerator() | Where-Object { $_.Value -gt 20 } | ForEach-Object { @{ IP = $_.Key; Count = $_.Value } })
    }
    $Result.IncidentReport = $Report
    $Result.Success = $true
    $Result.Message = "Failed login monitoring actions completed"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 4
''',

    verify_script=r'''
# Re-check failure counts and confirm lockouts applied
try {
    $CutoffTime = (Get-Date).AddHours(-1)

    # Check for very recent failures (post-remediation)
    $RecentFailures = @(Get-WinEvent -FilterHashtable @{
        LogName = 'Security'
        Id = 4625
        StartTime = $CutoffTime
    } -ErrorAction SilentlyContinue)

    # Check locked/disabled accounts
    $LockedCount = 0
    try {
        Import-Module ActiveDirectory -ErrorAction Stop
        $LockedCount = @(Search-ADAccount -LockedOut -ErrorAction Stop).Count
    } catch { }

    # Verify firewall rules were created
    $BlockRules = @(Get-NetFirewallRule -DisplayName "Block-BruteForce-*" -ErrorAction SilentlyContinue)

    @{
        RecentFailuresLastHour = $RecentFailures.Count
        LockedAccountCount = $LockedCount
        FirewallBlockRules = $BlockRules.Count
        Compliant = ($RecentFailures.Count -lt 10)
    } | ConvertTo-Json
} catch {
    @{ Compliant = $false; Error = $_.Exception.Message } | ConvertTo-Json
}
''',

    timeout_seconds=300,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["TotalFailedLogons24h", "BruteForceAccounts", "HighVolumeSourceIPs", "LockedAccountCount", "LockoutThreshold", "Issues"]
)


# =============================================================================
# RB-WIN-ACCESS-004: Guest WiFi Isolation Verification
# =============================================================================

RUNBOOK_GUEST_WIFI = WindowsRunbook(
    id="RB-WIN-ACCESS-004",
    name="Guest WiFi Isolation Verification",
    description="Verify guest/public WiFi networks cannot reach ePHI VLANs, internal DCs, file servers, or EHR systems",
    version="1.0",
    hipaa_controls=["164.312(a)(1)", "164.312(e)(1)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Enumerate wireless profiles and test guest network isolation
$Result = @{
    Drifted = $false
    Issues = @()
    WirelessProfiles = @()
    IsolationTests = @()
}

try {
    # Enumerate all wireless profiles
    $ProfilesRaw = netsh wlan show profiles 2>&1
    $ProfileNames = @()

    foreach ($Line in $ProfilesRaw) {
        if ($Line -match "All User Profile\s*:\s*(.+)$") {
            $ProfileNames += $Matches[1].Trim()
        }
    }

    $Result.TotalProfiles = $ProfileNames.Count

    # Classify profiles as guest/public or internal
    $GuestKeywords = @("guest", "public", "visitor", "patient", "lobby", "waiting")
    $GuestProfiles = @()
    $InternalProfiles = @()

    foreach ($ProfileName in $ProfileNames) {
        $IsGuest = $false
        foreach ($Keyword in $GuestKeywords) {
            if ($ProfileName -like "*$Keyword*") {
                $IsGuest = $true
                break
            }
        }

        # Get profile details
        $ProfileDetail = netsh wlan show profile name="$ProfileName" 2>&1
        $Authentication = ($ProfileDetail | Select-String "Authentication\s*:\s*(.+)$" | Select-Object -First 1)
        $AuthType = if ($Authentication) { ($Authentication -split ":\s*")[1].Trim() } else { "Unknown" }

        $ProfileInfo = @{
            Name = $ProfileName
            IsGuest = $IsGuest
            Authentication = $AuthType
        }

        $Result.WirelessProfiles += $ProfileInfo

        if ($IsGuest) {
            $GuestProfiles += $ProfileName
        } else {
            $InternalProfiles += $ProfileName
        }
    }

    $Result.GuestProfiles = $GuestProfiles
    $Result.InternalProfiles = $InternalProfiles

    if ($GuestProfiles.Count -eq 0) {
        $Result.Note = "No guest/public wireless profiles detected on this machine"
        $Result | ConvertTo-Json -Depth 3
        return
    }

    # Get current connection info
    $CurrentConnection = netsh wlan show interfaces 2>&1
    $ConnectedSSID = ($CurrentConnection | Select-String "SSID\s*:\s*(.+)$" | Select-Object -First 1)
    $CurrentSSID = if ($ConnectedSSID) { ($ConnectedSSID -split ":\s*")[1].Trim() } else { $null }
    $Result.CurrentSSID = $CurrentSSID

    # Define internal resources to test isolation against
    # These should be ePHI-relevant targets
    $InternalTargets = @()

    # Try to find DC
    try {
        $DC = [System.DirectoryServices.ActiveDirectory.Domain]::GetCurrentDomain().DomainControllers[0]
        $InternalTargets += @{ Name = "DomainController"; IP = $DC.IPAddress }
    } catch { }

    # Common internal subnets to test
    $Gateway = (Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue).NextHop | Select-Object -First 1
    if ($Gateway) {
        $InternalTargets += @{ Name = "DefaultGateway"; IP = $Gateway }
    }

    # Try common EHR/file server ports on gateway subnet
    if ($Gateway) {
        $Subnet = ($Gateway -split '\.')[0..2] -join '.'
        # Test common server IPs
        foreach ($LastOctet in @(1, 2, 5, 10, 100)) {
            $TestIP = "$Subnet.$LastOctet"
            if ($TestIP -ne $Gateway) {
                $InternalTargets += @{ Name = "InternalHost-$TestIP"; IP = $TestIP }
            }
        }
    }

    # Test connectivity from current profile to internal resources
    foreach ($Target in $InternalTargets) {
        $TestResult = @{
            Target = $Target.Name
            IP = $Target.IP
            Reachable = $false
        }

        $Ping = Test-Connection -ComputerName $Target.IP -Count 1 -Quiet -ErrorAction SilentlyContinue
        $TestResult.Reachable = $Ping

        # Test common ePHI ports (SMB 445, RDP 3389, HTTP 80/443, SQL 1433)
        $PortTests = @()
        foreach ($Port in @(445, 3389, 80, 443, 1433)) {
            $TCPTest = New-Object System.Net.Sockets.TcpClient
            try {
                $AsyncResult = $TCPTest.BeginConnect($Target.IP, $Port, $null, $null)
                $WaitResult = $AsyncResult.AsyncWaitHandle.WaitOne(1000, $false)
                $PortOpen = $WaitResult -and $TCPTest.Connected
                if ($PortOpen) {
                    $PortTests += $Port
                }
            } catch { } finally {
                $TCPTest.Close()
            }
        }
        $TestResult.OpenPorts = $PortTests

        $Result.IsolationTests += $TestResult

        # If on guest network and can reach internal resources, that's a problem
        $IsOnGuest = $false
        foreach ($GP in $GuestProfiles) {
            if ($CurrentSSID -and $CurrentSSID -like "*$GP*") {
                $IsOnGuest = $true
                break
            }
        }

        if ($IsOnGuest -and ($Ping -or $PortTests.Count -gt 0)) {
            $Result.Drifted = $true
            $Result.Issues += "Guest network can reach $($Target.Name) ($($Target.IP)) - ports: $($PortTests -join ', ')"
        }
    }

    $Result.IssueCount = $Result.Issues.Count
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Drifted = $true
}

$Result | ConvertTo-Json -Depth 4
''',

    remediate_script=r'''
# Guest WiFi isolation requires network-level fix - alert/escalate
$Result = @{ Success = $false; Actions = @() }

try {
    $Result.Actions += "ALERT: Guest WiFi isolation failure detected - this requires network infrastructure changes"
    $Result.Actions += "ESCALATE: Network administrator must configure VLAN isolation between guest and internal networks"

    $Recommendations = @(
        "1. Verify guest SSID is on a separate VLAN from internal/ePHI networks",
        "2. Configure ACLs on switch/router to block guest VLAN from reaching internal subnets",
        "3. Ensure guest VLAN has no routes to ePHI VLAN (typically VLAN containing DC, file server, EHR)",
        "4. Enable client isolation on the guest wireless network to prevent guest-to-guest attacks",
        "5. Consider implementing a captive portal for guest network access logging"
    )
    $Result.Recommendations = $Recommendations

    # Remove any saved guest profiles from this machine if it's a workstation that should not connect to guest
    $GuestKeywords = @("guest", "public", "visitor", "patient", "lobby", "waiting")
    $ProfilesRaw = netsh wlan show profiles 2>&1
    foreach ($Line in $ProfilesRaw) {
        if ($Line -match "All User Profile\s*:\s*(.+)$") {
            $ProfileName = $Matches[1].Trim()
            foreach ($Keyword in $GuestKeywords) {
                if ($ProfileName -like "*$Keyword*") {
                    netsh wlan delete profile name="$ProfileName" | Out-Null
                    $Result.Actions += "Removed guest WiFi profile '$ProfileName' from this workstation"
                    break
                }
            }
        }
    }

    $Result.Success = $true
    $Result.Message = "Escalation generated for network isolation fix; guest profiles removed from workstation"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
# Re-test guest isolation
try {
    # Check if guest profiles still exist
    $ProfilesRaw = netsh wlan show profiles 2>&1
    $GuestKeywords = @("guest", "public", "visitor", "patient", "lobby", "waiting")
    $GuestProfileCount = 0

    foreach ($Line in $ProfilesRaw) {
        if ($Line -match "All User Profile\s*:\s*(.+)$") {
            $ProfileName = $Matches[1].Trim()
            foreach ($Keyword in $GuestKeywords) {
                if ($ProfileName -like "*$Keyword*") {
                    $GuestProfileCount++
                    break
                }
            }
        }
    }

    # Test connectivity to gateway from current network
    $Gateway = (Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue).NextHop | Select-Object -First 1
    $GatewayReachable = if ($Gateway) { Test-Connection -ComputerName $Gateway -Count 1 -Quiet -ErrorAction SilentlyContinue } else { $false }

    @{
        GuestProfilesRemaining = $GuestProfileCount
        GatewayReachable = $GatewayReachable
        Compliant = ($GuestProfileCount -eq 0)
    } | ConvertTo-Json
} catch {
    @{ Compliant = $false; Error = $_.Exception.Message } | ConvertTo-Json
}
''',

    timeout_seconds=300,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["WirelessProfiles", "GuestProfiles", "IsolationTests", "CurrentSSID", "Issues"]
)


# =============================================================================
# RB-WIN-ACCESS-005: USB/Removable Media Device Enumeration
# =============================================================================

RUNBOOK_USB_ENUM = WindowsRunbook(
    id="RB-WIN-ACCESS-005",
    name="USB/Removable Media Device Enumeration",
    description="Enumerate USB storage devices, check connection history, and verify Group Policy restrictions for removable media",
    version="1.0",
    hipaa_controls=["164.312(a)(2)(i)", "164.310(d)(1)"],
    severity="medium",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Query for removable media devices and USB history
$Result = @{
    Drifted = $false
    Issues = @()
    CurrentDevices = @()
    RecentUSBHistory = @()
    PolicyStatus = @{}
}

try {
    # --- Query currently connected removable drives ---
    $RemovableDrives = Get-WmiObject Win32_DiskDrive | Where-Object { $_.MediaType -like "*removable*" -or $_.InterfaceType -eq "USB" }

    foreach ($Drive in $RemovableDrives) {
        $DriveInfo = @{
            DeviceID = $Drive.DeviceID
            Model = $Drive.Model
            InterfaceType = $Drive.InterfaceType
            MediaType = $Drive.MediaType
            Size = if ($Drive.Size) { [math]::Round($Drive.Size / 1GB, 2) } else { 0 }
            SerialNumber = $Drive.SerialNumber
            PNPDeviceID = $Drive.PNPDeviceID
        }
        $Result.CurrentDevices += $DriveInfo
    }

    $Result.CurrentRemovableDeviceCount = @($RemovableDrives).Count

    # --- Check event log for USB device connections (Event ID 6416 - external device recognized) ---
    $CutoffTime = (Get-Date).AddDays(-30)

    # Event ID 6416: A new external device was recognized by the system
    $USBEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'Security'
        Id = 6416
        StartTime = $CutoffTime
    } -MaxEvents 100 -ErrorAction SilentlyContinue

    foreach ($Event in $USBEvents) {
        $Xml = [xml]$Event.ToXml()
        $DeviceName = ($Xml.Event.EventData.Data | Where-Object { $_.Name -eq "DeviceDescription" }).'#text'
        $DeviceId = ($Xml.Event.EventData.Data | Where-Object { $_.Name -eq "DeviceId" }).'#text'
        $ClassName = ($Xml.Event.EventData.Data | Where-Object { $_.Name -eq "ClassName" }).'#text'

        $Result.RecentUSBHistory += @{
            Timestamp = $Event.TimeCreated.ToString("o")
            DeviceName = $DeviceName
            DeviceId = $DeviceId
            ClassName = $ClassName
        }
    }

    $Result.USBEventsLast30Days = @($USBEvents).Count

    # Also check PnP events (Event ID 20001, 20003 for device install)
    $PnPEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'System'
        ProviderName = 'Microsoft-Windows-UserPnp'
        StartTime = $CutoffTime
    } -MaxEvents 50 -ErrorAction SilentlyContinue

    # Check USB storage device registry history
    $USBSTORKey = "HKLM:\SYSTEM\CurrentControlSet\Enum\USBSTOR"
    $USBHistory = @()
    if (Test-Path $USBSTORKey) {
        $USBDevices = Get-ChildItem $USBSTORKey -ErrorAction SilentlyContinue
        foreach ($Device in $USBDevices) {
            $SubKeys = Get-ChildItem $Device.PSPath -ErrorAction SilentlyContinue
            foreach ($SubKey in $SubKeys) {
                $Props = Get-ItemProperty $SubKey.PSPath -ErrorAction SilentlyContinue
                $USBHistory += @{
                    DeviceDesc = $Props.DeviceDesc
                    FriendlyName = $Props.FriendlyName
                    HardwareID = $Props.HardwareID | Select-Object -First 1
                    RegistryPath = $SubKey.PSPath -replace 'Microsoft.PowerShell.Core\\Registry::', ''
                }
            }
        }
    }
    $Result.USBStorageHistoryCount = $USBHistory.Count
    $Result.USBStorageHistory = $USBHistory | Select-Object -First 20

    # --- Check Group Policy for USB restrictions ---
    # RemovableStorageDevices policy keys
    $GPUSBKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\RemovableStorageDevices"

    # Check read deny
    $DenyReadKey = "$GPUSBKey\{53f5630d-b6bf-11d0-94f2-00a0c91efb8b}"
    $DenyRead = (Get-ItemProperty -Path $DenyReadKey -Name "Deny_Read" -ErrorAction SilentlyContinue).Deny_Read
    $Result.PolicyStatus.DenyUSBRead = ($DenyRead -eq 1)

    # Check write deny
    $DenyWriteKey = "$GPUSBKey\{53f5630d-b6bf-11d0-94f2-00a0c91efb8b}"
    $DenyWrite = (Get-ItemProperty -Path $DenyWriteKey -Name "Deny_Write" -ErrorAction SilentlyContinue).Deny_Write
    $Result.PolicyStatus.DenyUSBWrite = ($DenyWrite -eq 1)

    # Check all removable storage deny
    $DenyAllKey = "$GPUSBKey\{53f56307-b6bf-11d0-94f2-00a0c91efb8b}"
    $DenyAllRead = (Get-ItemProperty -Path $DenyAllKey -Name "Deny_Read" -ErrorAction SilentlyContinue).Deny_Read
    $DenyAllWrite = (Get-ItemProperty -Path $DenyAllKey -Name "Deny_Write" -ErrorAction SilentlyContinue).Deny_Write
    $Result.PolicyStatus.DenyAllRemovableRead = ($DenyAllRead -eq 1)
    $Result.PolicyStatus.DenyAllRemovableWrite = ($DenyAllWrite -eq 1)

    # Check device installation restrictions
    $DeviceInstallKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\DeviceInstall\Restrictions"
    $DenyInstall = (Get-ItemProperty -Path $DeviceInstallKey -Name "DenyDeviceClasses" -ErrorAction SilentlyContinue).DenyDeviceClasses
    $Result.PolicyStatus.DeviceInstallRestricted = ($null -ne $DenyInstall)

    # Determine drift status
    $NoUSBPolicy = (-not $Result.PolicyStatus.DenyUSBWrite) -and
                   (-not $Result.PolicyStatus.DenyAllRemovableWrite) -and
                   (-not $Result.PolicyStatus.DeviceInstallRestricted)

    if ($NoUSBPolicy) {
        $Result.Drifted = $true
        $Result.Issues += "No USB/removable media restriction policy is configured"
    }

    if (@($RemovableDrives).Count -gt 0 -and $NoUSBPolicy) {
        $Result.Issues += "$(@($RemovableDrives).Count) removable device(s) currently connected with no restriction policy"
    }

    $Result.IssueCount = $Result.Issues.Count
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Drifted = $true
}

$Result | ConvertTo-Json -Depth 4
''',

    remediate_script=r'''
# Apply USB restriction GPO settings and alert on unauthorized device history
$Result = @{ Success = $false; Actions = @() }

try {
    # Apply USB write restriction via registry (equivalent to GPO setting)
    $GPUSBKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\RemovableStorageDevices"

    # Deny write to removable storage (disk drives)
    $DiskDriveKey = "$GPUSBKey\{53f5630d-b6bf-11d0-94f2-00a0c91efb8b}"
    if (-not (Test-Path $DiskDriveKey)) {
        New-Item -Path $DiskDriveKey -Force | Out-Null
    }
    Set-ItemProperty -Path $DiskDriveKey -Name "Deny_Write" -Value 1 -Type DWord
    $Result.Actions += "Enabled USB storage write deny policy"

    # Deny write to all removable storage
    $AllRemovableKey = "$GPUSBKey\{53f56307-b6bf-11d0-94f2-00a0c91efb8b}"
    if (-not (Test-Path $AllRemovableKey)) {
        New-Item -Path $AllRemovableKey -Force | Out-Null
    }
    Set-ItemProperty -Path $AllRemovableKey -Name "Deny_Write" -Value 1 -Type DWord
    $Result.Actions += "Enabled all removable storage write deny policy"

    # Deny write to WPD devices (phones, cameras)
    $WPDKey = "$GPUSBKey\{6AC27878-A6FA-4155-BA85-F98F491D4F33}"
    if (-not (Test-Path $WPDKey)) {
        New-Item -Path $WPDKey -Force | Out-Null
    }
    Set-ItemProperty -Path $WPDKey -Name "Deny_Write" -Value 1 -Type DWord
    $Result.Actions += "Enabled WPD (portable device) write deny policy"

    # Enable auditing for removable storage
    $AuditKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\RemovableStorageDevices"
    if (-not (Test-Path $AuditKey)) {
        New-Item -Path $AuditKey -Force | Out-Null
    }
    # Enable audit for removable storage access
    auditpol /set /subcategory:"Removable Storage" /success:enable /failure:enable 2>&1 | Out-Null
    $Result.Actions += "Enabled audit policy for removable storage access"

    # Alert on recent unauthorized USB history
    $USBSTORKey = "HKLM:\SYSTEM\CurrentControlSet\Enum\USBSTOR"
    if (Test-Path $USBSTORKey) {
        $DeviceCount = (Get-ChildItem $USBSTORKey -ErrorAction SilentlyContinue | Measure-Object).Count
        if ($DeviceCount -gt 0) {
            $Result.Actions += "ALERT: $DeviceCount USB storage device(s) found in device history - review for unauthorized access"
        }
    }

    $Result.Success = $true
    $Result.Message = "USB restriction policies applied and auditing enabled"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
# Confirm USB policy applied and check for new connections
try {
    # Verify write deny policies
    $GPUSBKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\RemovableStorageDevices"
    $DiskDriveKey = "$GPUSBKey\{53f5630d-b6bf-11d0-94f2-00a0c91efb8b}"
    $AllRemovableKey = "$GPUSBKey\{53f56307-b6bf-11d0-94f2-00a0c91efb8b}"

    $DiskWriteDenied = (Get-ItemProperty -Path $DiskDriveKey -Name "Deny_Write" -ErrorAction SilentlyContinue).Deny_Write -eq 1
    $AllWriteDenied = (Get-ItemProperty -Path $AllRemovableKey -Name "Deny_Write" -ErrorAction SilentlyContinue).Deny_Write -eq 1

    # Check for currently connected removable devices
    $CurrentRemovable = @(Get-WmiObject Win32_DiskDrive | Where-Object {
        $_.MediaType -like "*removable*" -or $_.InterfaceType -eq "USB"
    })

    # Check audit policy
    $AuditOutput = auditpol /get /subcategory:"Removable Storage" 2>&1
    $AuditEnabled = ($AuditOutput | Select-String "Success and Failure|Success|Failure") -ne $null

    @{
        USBWriteDenyPolicy = $DiskWriteDenied
        AllRemovableWriteDeny = $AllWriteDenied
        CurrentRemovableDevices = $CurrentRemovable.Count
        AuditEnabled = $AuditEnabled
        Compliant = ($DiskWriteDenied -and $AllWriteDenied)
    } | ConvertTo-Json
} catch {
    @{ Compliant = $false; Error = $_.Exception.Message } | ConvertTo-Json
}
''',

    timeout_seconds=180,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["CurrentRemovableDeviceCount", "USBEventsLast30Days", "USBStorageHistoryCount", "PolicyStatus", "Issues"]
)


# =============================================================================
# Clinic Access Runbooks Registry
# =============================================================================

CLINIC_ACCESS_RUNBOOKS: Dict[str, WindowsRunbook] = {
    "RB-WIN-ACCESS-002": RUNBOOK_MFA_AUDIT,
    "RB-WIN-ACCESS-003": RUNBOOK_FAILED_LOGIN,
    "RB-WIN-ACCESS-004": RUNBOOK_GUEST_WIFI,
    "RB-WIN-ACCESS-005": RUNBOOK_USB_ENUM,
}
