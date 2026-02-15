"""
Windows Network Runbooks for HIPAA Compliance.

Runbooks for network configuration and recovery.
"""

from typing import Dict, List
from dataclasses import dataclass, field
from .runbooks import WindowsRunbook, ExecutionConstraints


# =============================================================================
# RB-WIN-NET-001: DNS Client Configuration Reset
# =============================================================================

RUNBOOK_DNS_CLIENT = WindowsRunbook(
    id="RB-WIN-NET-001",
    name="DNS Client Configuration Reset",
    description="Reset DNS client settings to proper DNS servers",
    version="1.0",
    hipaa_controls=["164.312(b)"],
    severity="medium",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=15,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check DNS client configuration
$Result = @{
    Drifted = $false
    Adapters = @()
}

$Adapters = Get-NetAdapter | Where-Object { $_.Status -eq "Up" }

foreach ($Adapter in $Adapters) {
    $DNS = Get-DnsClientServerAddress -InterfaceIndex $Adapter.ifIndex -AddressFamily IPv4
    $AdapterInfo = @{
        Name = $Adapter.Name
        InterfaceIndex = $Adapter.ifIndex
        DNSServers = $DNS.ServerAddresses
    }
    $Result.Adapters += $AdapterInfo

    # Check for public DNS on domain-joined machines
    $IsDomainJoined = (Get-WmiObject Win32_ComputerSystem).PartOfDomain
    if ($IsDomainJoined) {
        $PublicDNS = @("8.8.8.8", "8.8.4.4", "1.1.1.1", "1.0.0.1")
        foreach ($Server in $DNS.ServerAddresses) {
            if ($Server -in $PublicDNS) {
                $Result.Drifted = $true
                $Result.DriftReason = "Public DNS configured on domain-joined machine"
                break
            }
        }
    }

    # Check for empty DNS
    if (-not $DNS.ServerAddresses -or $DNS.ServerAddresses.Count -eq 0) {
        $Result.Drifted = $true
        $Result.DriftReason = "No DNS servers configured"
    }
}

$Result | ConvertTo-Json -Depth 3
''',

    remediate_script=r'''
# Reset DNS configuration based on domain membership
$Result = @{ Success = $false; Actions = @() }

try {
    $IsDomainJoined = (Get-WmiObject Win32_ComputerSystem).PartOfDomain

    if ($IsDomainJoined) {
        $DC = $null

        # Step 1: Check if THIS machine is a Domain Controller
        # If so, DNS should point to itself (loopback or own IP)
        $isDC = (Get-WmiObject Win32_ComputerSystem).DomainRole -ge 4
        if ($isDC) {
            # DC should use its own IP as DNS — get the primary adapter IP
            $myIP = (Get-NetIPAddress -AddressFamily IPv4 |
                Where-Object { $_.IPAddress -ne '127.0.0.1' -and $_.PrefixOrigin -ne 'WellKnown' } |
                Select-Object -First 1).IPAddress
            if ($myIP) {
                $DC = $myIP
                $Result.Actions += "Machine is a DC, using own IP ($myIP)"
            }
        }

        # Step 2: Try AD lookup (works if DNS is not hijacked)
        if (-not $DC) {
            try {
                $DC = ([System.DirectoryServices.ActiveDirectory.Domain]::GetCurrentDomain()).DomainControllers[0].IPAddress
            } catch {}
        }

        # Step 3: Try nltest (also DNS-dependent but different path)
        if (-not $DC) {
            try {
                $nltest = nltest /dsgetdc: 2>&1
                if ($nltest -match 'DC:\\\\(\S+)') {
                    $DC = [System.Net.Dns]::GetHostAddresses($Matches[1])[0].IPAddressToString
                }
            } catch {}
        }

        # Step 4: Try reading cached DC from registry (survives DNS hijack)
        if (-not $DC) {
            try {
                $cached = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\Netlogon\Parameters' -Name 'DynamicSiteName' -ErrorAction SilentlyContinue)
                $dcList = nltest /dsgetdc: /force 2>&1
                if ($dcList -match '(\d+\.\d+\.\d+\.\d+)') { $DC = $Matches[1] }
            } catch {}
        }

        # Step 5: Last resort — gateway (likely DC on small networks)
        if (-not $DC) {
            $gw = (Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Select-Object -First 1).NextHop
            if ($gw) { $DC = $gw }
        }

        if ($DC) {
            $Adapters = Get-NetAdapter | Where-Object { $_.Status -eq "Up" }
            foreach ($Adapter in $Adapters) {
                Set-DnsClientServerAddress -InterfaceIndex $Adapter.ifIndex -ServerAddresses $DC
                $Result.Actions += "Set DNS to DC ($DC) on $($Adapter.Name)"
            }
        } else {
            $Result.Error = "Could not determine DC IP for DNS restoration"
        }
    } else {
        # Standalone - use DHCP or set reliable DNS
        $Adapters = Get-NetAdapter | Where-Object { $_.Status -eq "Up" }
        foreach ($Adapter in $Adapters) {
            $IPConfig = Get-NetIPConfiguration -InterfaceIndex $Adapter.ifIndex
            if ($IPConfig.NetIPv4Interface.Dhcp -eq "Enabled") {
                Set-DnsClientServerAddress -InterfaceIndex $Adapter.ifIndex -ResetServerAddresses
                $Result.Actions += "Reset to DHCP DNS on $($Adapter.Name)"
            }
        }
    }

    # Flush DNS cache
    Clear-DnsClientCache
    $Result.Actions += "Flushed DNS cache"

    $Result.Success = $true
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
$Adapters = Get-NetAdapter | Where-Object { $_.Status -eq "Up" }
$HasDNS = $false
$StillHijacked = $false
$PublicDNS = @("8.8.8.8", "8.8.4.4", "1.1.1.1", "1.0.0.1")
$IsDomainJoined = (Get-WmiObject Win32_ComputerSystem).PartOfDomain
foreach ($Adapter in $Adapters) {
    $DNS = Get-DnsClientServerAddress -InterfaceIndex $Adapter.ifIndex -AddressFamily IPv4
    if ($DNS.ServerAddresses.Count -gt 0) {
        $HasDNS = $true
        # On domain-joined machines, public DNS means still hijacked
        if ($IsDomainJoined) {
            foreach ($Server in $DNS.ServerAddresses) {
                if ($Server -in $PublicDNS) { $StillHijacked = $true }
            }
        }
    }
}
@{
    HasDNSServers = $HasDNS
    Verified = ($HasDNS -and -not $StillHijacked)
} | ConvertTo-Json
''',

    timeout_seconds=60,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["Adapters", "DriftReason"]
)


# =============================================================================
# RB-WIN-NET-002: NIC Reset and Recovery
# =============================================================================

RUNBOOK_NIC_RESET = WindowsRunbook(
    id="RB-WIN-NET-002",
    name="NIC Reset and Recovery",
    description="Reset network adapter if connectivity issues detected",
    version="1.0",
    hipaa_controls=["164.312(a)(1)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=False  # Only one NIC reset at a time
    ),

    detect_script=r'''
# Check network adapter health
$Result = @{
    Drifted = $false
    Adapters = @()
}

$Adapters = Get-NetAdapter

foreach ($Adapter in $Adapters) {
    $AdapterInfo = @{
        Name = $Adapter.Name
        Status = $Adapter.Status.ToString()
        LinkSpeed = $Adapter.LinkSpeed
        MediaConnectionState = $Adapter.MediaConnectionState.ToString()
        DriverVersion = $Adapter.DriverVersion
    }

    # Get statistics
    $Stats = Get-NetAdapterStatistics -Name $Adapter.Name -ErrorAction SilentlyContinue
    if ($Stats) {
        $AdapterInfo.ReceivedBytes = $Stats.ReceivedBytes
        $AdapterInfo.SentBytes = $Stats.SentBytes
        $AdapterInfo.InboundErrors = $Stats.ReceivedPacketsWithErrors
        $AdapterInfo.OutboundErrors = $Stats.OutboundPacketErrors

        # High error rate indicates problems
        if ($Stats.ReceivedPacketsWithErrors -gt 1000 -or $Stats.OutboundPacketErrors -gt 1000) {
            $AdapterInfo.HighErrorRate = $true
        }
    }

    $Result.Adapters += $AdapterInfo

    # Drift if adapter is down but should be up
    if ($Adapter.Status -eq "Disabled" -and $Adapter.AdminStatus -eq "Up") {
        $Result.Drifted = $true
        $Result.DriftReason = "Adapter disabled unexpectedly"
    }
}

# Check overall connectivity
$Gateway = (Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue).NextHop | Select-Object -First 1
if ($Gateway) {
    $PingResult = Test-Connection -ComputerName $Gateway -Count 2 -Quiet -ErrorAction SilentlyContinue
    $Result.GatewayReachable = $PingResult
    if (-not $PingResult) {
        $Result.Drifted = $true
        $Result.DriftReason = "Cannot reach gateway"
    }
}

$Result | ConvertTo-Json -Depth 3
''',

    remediate_script=r'''
# Reset network adapter
$Result = @{ Success = $false; Actions = @() }

try {
    # Get primary network adapter
    $Adapter = Get-NetAdapter | Where-Object { $_.Status -eq "Up" } | Select-Object -First 1

    if (-not $Adapter) {
        # Try to enable disabled adapters
        $DisabledAdapter = Get-NetAdapter | Where-Object { $_.Status -eq "Disabled" } | Select-Object -First 1
        if ($DisabledAdapter) {
            Enable-NetAdapter -Name $DisabledAdapter.Name -Confirm:$false
            $Result.Actions += "Enabled adapter: $($DisabledAdapter.Name)"
            Start-Sleep -Seconds 5
        }
    } else {
        # Disable and re-enable to reset
        $AdapterName = $Adapter.Name
        Disable-NetAdapter -Name $AdapterName -Confirm:$false
        Start-Sleep -Seconds 3
        Enable-NetAdapter -Name $AdapterName -Confirm:$false
        $Result.Actions += "Reset adapter: $AdapterName"
        Start-Sleep -Seconds 5
    }

    # Release and renew DHCP if applicable
    $IPConfig = Get-NetIPConfiguration | Where-Object { $_.NetIPv4Interface.Dhcp -eq "Enabled" }
    if ($IPConfig) {
        ipconfig /release | Out-Null
        Start-Sleep -Seconds 2
        ipconfig /renew | Out-Null
        $Result.Actions += "Renewed DHCP lease"
    }

    # Verify connectivity
    Start-Sleep -Seconds 3
    $Gateway = (Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue).NextHop | Select-Object -First 1
    if ($Gateway) {
        $Result.GatewayReachable = Test-Connection -ComputerName $Gateway -Count 2 -Quiet
    }

    $Result.Success = $true
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
$Adapter = Get-NetAdapter | Where-Object { $_.Status -eq "Up" } | Select-Object -First 1
$Gateway = (Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue).NextHop | Select-Object -First 1
$GatewayOk = if ($Gateway) { Test-Connection -ComputerName $Gateway -Count 2 -Quiet } else { $false }
@{
    AdapterUp = ($null -ne $Adapter)
    GatewayReachable = $GatewayOk
    Verified = ($null -ne $Adapter -and $GatewayOk)
} | ConvertTo-Json
''',

    timeout_seconds=180,
    requires_reboot=False,
    disruptive=True,  # Brief network interruption
    evidence_fields=["Adapters", "GatewayReachable"]
)


# =============================================================================
# RB-WIN-NET-003: Network Profile Remediation
# =============================================================================

RUNBOOK_NETWORK_PROFILE = WindowsRunbook(
    id="RB-WIN-NET-003",
    name="Network Profile Remediation",
    description="Ensure proper network profile (Domain/Private) for security",
    version="1.0",
    hipaa_controls=["164.312(e)(1)"],
    severity="medium",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=15,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check network profile settings
$Result = @{
    Drifted = $false
    Profiles = @()
}

$Profiles = Get-NetConnectionProfile

foreach ($Profile in $Profiles) {
    $ProfileInfo = @{
        Name = $Profile.Name
        InterfaceAlias = $Profile.InterfaceAlias
        NetworkCategory = $Profile.NetworkCategory.ToString()
        IPv4Connectivity = $Profile.IPv4Connectivity.ToString()
    }
    $Result.Profiles += $ProfileInfo

    # Domain-joined machines should have Domain profile
    $IsDomainJoined = (Get-WmiObject Win32_ComputerSystem).PartOfDomain

    if ($IsDomainJoined -and $Profile.NetworkCategory -eq "Public") {
        $Result.Drifted = $true
        $Result.DriftReason = "Domain machine on Public network profile"
    }

    # Servers should not be on Public profile
    $IsServer = (Get-WmiObject Win32_OperatingSystem).ProductType -eq 3
    if ($IsServer -and $Profile.NetworkCategory -eq "Public") {
        $Result.Drifted = $true
        $Result.DriftReason = "Server on Public network profile"
    }
}

$Result | ConvertTo-Json -Depth 2
''',

    remediate_script=r'''
# Set appropriate network profile
$Result = @{ Success = $false; Actions = @() }

try {
    $IsDomainJoined = (Get-WmiObject Win32_ComputerSystem).PartOfDomain
    $Profiles = Get-NetConnectionProfile

    foreach ($Profile in $Profiles) {
        if ($Profile.NetworkCategory -eq "Public") {
            if ($IsDomainJoined) {
                # Can't set to Domain directly, set to Private
                Set-NetConnectionProfile -InterfaceIndex $Profile.InterfaceIndex -NetworkCategory Private
                $Result.Actions += "Changed $($Profile.InterfaceAlias) from Public to Private"
            } else {
                Set-NetConnectionProfile -InterfaceIndex $Profile.InterfaceIndex -NetworkCategory Private
                $Result.Actions += "Changed $($Profile.InterfaceAlias) from Public to Private"
            }
        }
    }

    $Result.Success = $true
    $Result.Message = "Network profiles updated"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
$Profiles = Get-NetConnectionProfile
$PublicCount = ($Profiles | Where-Object { $_.NetworkCategory -eq "Public" }).Count
@{
    PublicProfileCount = $PublicCount
    Verified = ($PublicCount -eq 0)
} | ConvertTo-Json
''',

    timeout_seconds=60,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["Profiles", "DriftReason"]
)


# =============================================================================
# RB-WIN-NET-004: WINS/NetBIOS Configuration
# =============================================================================

RUNBOOK_NETBIOS = WindowsRunbook(
    id="RB-WIN-NET-004",
    name="WINS/NetBIOS Configuration",
    description="Configure WINS and NetBIOS settings for domain environment",
    version="1.0",
    hipaa_controls=["164.312(b)"],
    severity="low",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=15,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check NetBIOS configuration
$Result = @{
    Drifted = $false
    Adapters = @()
}

$Adapters = Get-NetAdapter | Where-Object { $_.Status -eq "Up" }

foreach ($Adapter in $Adapters) {
    $NetbiosOption = (Get-WmiObject Win32_NetworkAdapterConfiguration |
        Where-Object { $_.InterfaceIndex -eq $Adapter.ifIndex }).TcpipNetbiosOptions

    $OptionName = switch ($NetbiosOption) {
        0 { "Default" }
        1 { "Enabled" }
        2 { "Disabled" }
        default { "Unknown" }
    }

    $AdapterInfo = @{
        Name = $Adapter.Name
        NetBIOSOption = $OptionName
        NetBIOSCode = $NetbiosOption
    }

    # Get WINS servers
    $Config = Get-WmiObject Win32_NetworkAdapterConfiguration |
        Where-Object { $_.InterfaceIndex -eq $Adapter.ifIndex }
    $AdapterInfo.WINSPrimary = $Config.WINSPrimaryServer
    $AdapterInfo.WINSSecondary = $Config.WINSSecondaryServer

    $Result.Adapters += $AdapterInfo
}

# On domain networks, NetBIOS should typically be Default (DHCP-controlled)
$IsDomainJoined = (Get-WmiObject Win32_ComputerSystem).PartOfDomain
$Result.IsDomainJoined = $IsDomainJoined

# Note: This is informational - not all environments need WINS
$Result.Drifted = $false

$Result | ConvertTo-Json -Depth 2
''',

    remediate_script=r'''
# Configure NetBIOS settings
$Result = @{ Success = $false; Actions = @() }

try {
    $IsDomainJoined = (Get-WmiObject Win32_ComputerSystem).PartOfDomain

    $Adapters = Get-WmiObject Win32_NetworkAdapterConfiguration |
        Where-Object { $_.IPEnabled -eq $true }

    foreach ($Adapter in $Adapters) {
        if ($IsDomainJoined) {
            # Set to Default (use DHCP setting)
            $Adapter.SetTcpipNetbios(0) | Out-Null
            $Result.Actions += "Set NetBIOS to Default on $($Adapter.Description)"
        } else {
            # Standalone - disable NetBIOS for security
            $Adapter.SetTcpipNetbios(2) | Out-Null
            $Result.Actions += "Disabled NetBIOS on $($Adapter.Description)"
        }
    }

    $Result.Success = $true
    $Result.Message = "NetBIOS configuration updated"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
$Adapters = Get-WmiObject Win32_NetworkAdapterConfiguration | Where-Object { $_.IPEnabled -eq $true }
$Settings = $Adapters | Select-Object Description, TcpipNetbiosOptions
@{
    AdapterCount = @($Settings).Count
    Settings = $Settings
    Verified = $true  # Informational runbook
} | ConvertTo-Json -Depth 2
''',

    timeout_seconds=60,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["Adapters", "IsDomainJoined"]
)


# =============================================================================
# RB-WIN-NET-005: LLMNR/mDNS Disable
# =============================================================================

RUNBOOK_LLMNR_DISABLE = WindowsRunbook(
    id="RB-WIN-NET-005",
    name="LLMNR/mDNS Disable",
    description="Disable LLMNR and mDNS to prevent name resolution poisoning attacks",
    version="1.0",
    hipaa_controls=["164.312(e)(1)"],
    severity="medium",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=15,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check LLMNR and mDNS configuration
$Result = @{
    Drifted = $false
    Issues = @()
}

try {
    # Check LLMNR status via registry (GPO)
    $LLMNRKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient"
    $EnableMulticast = (Get-ItemProperty -Path $LLMNRKey -Name "EnableMulticast" -ErrorAction SilentlyContinue).EnableMulticast
    $Result.LLMNREnabled = if ($null -eq $EnableMulticast) { "NotConfigured (default: enabled)" } else { $EnableMulticast }

    # LLMNR should be disabled (EnableMulticast = 0)
    if ($EnableMulticast -ne 0) {
        $Result.Drifted = $true
        $Result.Issues += "LLMNR is not disabled (EnableMulticast should be 0)"
    }

    # Check mDNS status
    $mDNSKey = "HKLM:\SYSTEM\CurrentControlSet\Services\Dnscache\Parameters"
    $EnableMDNS = (Get-ItemProperty -Path $mDNSKey -Name "EnableMDNS" -ErrorAction SilentlyContinue).EnableMDNS
    $Result.mDNSEnabled = if ($null -eq $EnableMDNS) { "NotConfigured (default: enabled)" } else { $EnableMDNS }

    # mDNS should be disabled (EnableMDNS = 0)
    if ($EnableMDNS -ne 0) {
        $Result.Drifted = $true
        $Result.Issues += "mDNS is not disabled (EnableMDNS should be 0)"
    }

    # Check NetBIOS over TCP/IP (related attack surface)
    $Adapters = Get-WmiObject Win32_NetworkAdapterConfiguration | Where-Object { $_.IPEnabled -eq $true }
    $NetBIOSIssues = @()
    foreach ($Adapter in $Adapters) {
        # TcpipNetbiosOptions: 0=Default, 1=Enabled, 2=Disabled
        if ($Adapter.TcpipNetbiosOptions -ne 2) {
            $NetBIOSIssues += "$($Adapter.Description): NetBIOS not disabled"
        }
    }
    $Result.NetBIOSIssues = $NetBIOSIssues

    # Check if WPAD is disabled
    $WPADKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings\Wpad"
    $WpadOverride = (Get-ItemProperty -Path $WPADKey -Name "WpadOverride" -ErrorAction SilentlyContinue).WpadOverride
    $Result.WPADDisabled = ($WpadOverride -eq 1)
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Drifted = $true
}

$Result | ConvertTo-Json -Depth 2
''',

    remediate_script=r'''
# Disable LLMNR and mDNS
$Result = @{ Success = $false; Actions = @() }

try {
    # Disable LLMNR via registry
    $LLMNRKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient"
    if (-not (Test-Path $LLMNRKey)) {
        New-Item -Path $LLMNRKey -Force | Out-Null
    }
    Set-ItemProperty -Path $LLMNRKey -Name "EnableMulticast" -Value 0 -Type DWord
    $Result.Actions += "Disabled LLMNR (EnableMulticast = 0)"

    # Disable mDNS
    $mDNSKey = "HKLM:\SYSTEM\CurrentControlSet\Services\Dnscache\Parameters"
    if (-not (Test-Path $mDNSKey)) {
        New-Item -Path $mDNSKey -Force | Out-Null
    }
    Set-ItemProperty -Path $mDNSKey -Name "EnableMDNS" -Value 0 -Type DWord
    $Result.Actions += "Disabled mDNS (EnableMDNS = 0)"

    # Disable WPAD auto-discovery
    $WPADKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings\Wpad"
    if (-not (Test-Path $WPADKey)) {
        New-Item -Path $WPADKey -Force | Out-Null
    }
    Set-ItemProperty -Path $WPADKey -Name "WpadOverride" -Value 1 -Type DWord
    $Result.Actions += "Disabled WPAD auto-discovery"

    # Restart DNS Client service to apply changes
    Restart-Service -Name "Dnscache" -Force -ErrorAction SilentlyContinue
    $Result.Actions += "Restarted DNS Client service"

    $Result.Success = $true
    $Result.Message = "LLMNR and mDNS disabled to prevent name resolution attacks"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
# Verify LLMNR and mDNS are disabled
try {
    $LLMNRKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient"
    $EnableMulticast = (Get-ItemProperty -Path $LLMNRKey -Name "EnableMulticast" -ErrorAction SilentlyContinue).EnableMulticast
    $LLMNRDisabled = ($EnableMulticast -eq 0)

    $mDNSKey = "HKLM:\SYSTEM\CurrentControlSet\Services\Dnscache\Parameters"
    $EnableMDNS = (Get-ItemProperty -Path $mDNSKey -Name "EnableMDNS" -ErrorAction SilentlyContinue).EnableMDNS
    $mDNSDisabled = ($EnableMDNS -eq 0)

    @{
        LLMNRDisabled = $LLMNRDisabled
        mDNSDisabled = $mDNSDisabled
        Verified = ($LLMNRDisabled -and $mDNSDisabled)
    } | ConvertTo-Json
} catch {
    @{ Verified = $false; Error = $_.Exception.Message } | ConvertTo-Json
}
''',

    timeout_seconds=60,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["LLMNREnabled", "mDNSEnabled", "NetBIOSIssues", "Issues"]
)


# =============================================================================
# RB-NET-SECURITY-001: Network Security Posture Verification
# =============================================================================

RUNBOOK_NET_SECURITY = WindowsRunbook(
    id="RB-NET-SECURITY-001",
    name="Network Security Posture Verification",
    description="Verify network security controls including segmentation, encryption, and monitoring",
    version="1.0",
    hipaa_controls=["164.312(e)(1)", "164.312(e)(2)(ii)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Comprehensive network security posture check
$Result = @{
    Drifted = $false
    Issues = @()
    FirewallProfiles = @()
    NetworkConnections = @()
    TLSSettings = @{}
    ListeningPorts = @()
}

# Check firewall on all profiles
$Profiles = Get-NetFirewallProfile
foreach ($Profile in $Profiles) {
    $ProfileInfo = @{
        Name = $Profile.Name
        Enabled = $Profile.Enabled
        DefaultInbound = $Profile.DefaultInboundAction.ToString()
        DefaultOutbound = $Profile.DefaultOutboundAction.ToString()
        LogAllowed = $Profile.LogAllowed
        LogBlocked = $Profile.LogBlocked
    }
    $Result.FirewallProfiles += $ProfileInfo

    if (-not $Profile.Enabled) {
        $Result.Drifted = $true
        $Result.Issues += "Firewall disabled on $($Profile.Name) profile"
    }

    # Check for permissive inbound default
    if ($Profile.DefaultInboundAction -eq "Allow") {
        $Result.Issues += "Warning: Default inbound action is ALLOW on $($Profile.Name)"
    }
}

# Check TLS settings
$TLSPath = "HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols"
$TLSVersions = @("SSL 2.0", "SSL 3.0", "TLS 1.0", "TLS 1.1", "TLS 1.2", "TLS 1.3")
$WeakProtocols = @("SSL 2.0", "SSL 3.0", "TLS 1.0", "TLS 1.1")

foreach ($Version in $TLSVersions) {
    $ServerPath = "$TLSPath\$Version\Server"
    $Enabled = (Get-ItemProperty -Path $ServerPath -Name "Enabled" -ErrorAction SilentlyContinue).Enabled
    $Result.TLSSettings[$Version] = @{
        Enabled = if ($null -eq $Enabled) { "Default" } else { $Enabled }
    }

    # Weak protocols should be disabled
    if ($Version -in $WeakProtocols -and $Enabled -ne 0) {
        $Result.Drifted = $true
        $Result.Issues += "$Version should be disabled"
    }
}

# Check for suspicious listening ports
$Listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalPort, OwningProcess
$SuspiciousPorts = @(21, 23, 69, 137, 138, 139, 445, 3389)  # FTP, Telnet, TFTP, NetBIOS, SMB, RDP

foreach ($Listener in $Listeners) {
    $Process = Get-Process -Id $Listener.OwningProcess -ErrorAction SilentlyContinue
    $PortInfo = @{
        Port = $Listener.LocalPort
        ProcessName = $Process.ProcessName
        ProcessId = $Listener.OwningProcess
    }
    $Result.ListeningPorts += $PortInfo

    if ($Listener.LocalPort -in $SuspiciousPorts) {
        $Result.Issues += "Warning: Port $($Listener.LocalPort) is listening ($($Process.ProcessName))"
    }
}

# Check SMB encryption
try {
    $SMBConfig = Get-SmbServerConfiguration
    $Result.SMBEncryption = @{
        EncryptData = $SMBConfig.EncryptData
        RejectUnencryptedAccess = $SMBConfig.RejectUnencryptedAccess
    }
    if (-not $SMBConfig.EncryptData) {
        $Result.Issues += "SMB encryption not enabled"
    }
} catch {
    $Result.SMBEncryption = @{ Error = $_.Exception.Message }
}

# Check IPsec policies
$IPsecRules = Get-NetIPsecRule -ErrorAction SilentlyContinue
$Result.IPsecRuleCount = @($IPsecRules).Count

$Result.IssueCount = $Result.Issues.Count
$Result | ConvertTo-Json -Depth 4
''',

    remediate_script=r'''
# Remediate network security issues
$Result = @{ Success = $false; Actions = @() }

try {
    # Enable firewall on all profiles
    $Profiles = Get-NetFirewallProfile
    foreach ($Profile in $Profiles) {
        if (-not $Profile.Enabled) {
            Set-NetFirewallProfile -Name $Profile.Name -Enabled True
            $Result.Actions += "Enabled firewall on $($Profile.Name)"
        }
    }

    # Set default inbound to Block
    Set-NetFirewallProfile -All -DefaultInboundAction Block -DefaultOutboundAction Allow
    $Result.Actions += "Set default inbound action to Block"

    # Enable firewall logging
    Set-NetFirewallProfile -All -LogBlocked True -LogAllowed False
    $Result.Actions += "Enabled blocked connection logging"

    # Disable weak TLS/SSL protocols
    $TLSPath = "HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols"
    $WeakProtocols = @("SSL 2.0", "SSL 3.0", "TLS 1.0", "TLS 1.1")

    foreach ($Protocol in $WeakProtocols) {
        $ServerPath = "$TLSPath\$Protocol\Server"
        $ClientPath = "$TLSPath\$Protocol\Client"

        foreach ($Path in @($ServerPath, $ClientPath)) {
            if (-not (Test-Path $Path)) {
                New-Item -Path $Path -Force | Out-Null
            }
            Set-ItemProperty -Path $Path -Name "Enabled" -Value 0 -Type DWord
            Set-ItemProperty -Path $Path -Name "DisabledByDefault" -Value 1 -Type DWord
        }
        $Result.Actions += "Disabled $Protocol"
    }

    # Enable TLS 1.2 and 1.3
    foreach ($Protocol in @("TLS 1.2", "TLS 1.3")) {
        $ServerPath = "$TLSPath\$Protocol\Server"
        $ClientPath = "$TLSPath\$Protocol\Client"

        foreach ($Path in @($ServerPath, $ClientPath)) {
            if (-not (Test-Path $Path)) {
                New-Item -Path $Path -Force | Out-Null
            }
            Set-ItemProperty -Path $Path -Name "Enabled" -Value 1 -Type DWord
            Set-ItemProperty -Path $Path -Name "DisabledByDefault" -Value 0 -Type DWord
        }
        $Result.Actions += "Enabled $Protocol"
    }

    # Enable SMB encryption
    try {
        Set-SmbServerConfiguration -EncryptData $true -Force
        $Result.Actions += "Enabled SMB encryption"
    } catch {
        $Result.Actions += "SMB encryption: $($_.Exception.Message)"
    }

    $Result.Success = $true
    $Result.Message = "Network security hardened"
    $Result.Warning = "Some changes require reboot to take full effect"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
$FirewallOk = (Get-NetFirewallProfile | Where-Object { -not $_.Enabled }).Count -eq 0

# Check TLS 1.0 is disabled
$TLS10Path = "HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\TLS 1.0\Server"
$TLS10Enabled = (Get-ItemProperty -Path $TLS10Path -Name "Enabled" -ErrorAction SilentlyContinue).Enabled
$TLSOk = ($TLS10Enabled -eq 0)

@{
    FirewallEnabled = $FirewallOk
    WeakTLSDisabled = $TLSOk
    Verified = ($FirewallOk -and $TLSOk)
} | ConvertTo-Json
''',

    timeout_seconds=180,
    requires_reboot=True,
    disruptive=False,
    evidence_fields=["FirewallProfiles", "TLSSettings", "ListeningPorts", "SMBEncryption", "Issues"]
)


# =============================================================================
# Network Runbooks Registry
# =============================================================================

NETWORK_RUNBOOKS: Dict[str, WindowsRunbook] = {
    "RB-WIN-NET-001": RUNBOOK_DNS_CLIENT,
    "RB-WIN-NET-002": RUNBOOK_NIC_RESET,
    "RB-WIN-NET-003": RUNBOOK_NETWORK_PROFILE,
    "RB-WIN-NET-004": RUNBOOK_NETBIOS,
    "RB-WIN-NET-005": RUNBOOK_LLMNR_DISABLE,
    "RB-NET-SECURITY-001": RUNBOOK_NET_SECURITY,
}
