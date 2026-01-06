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
        # Get DC as DNS server
        $DC = ([System.DirectoryServices.ActiveDirectory.Domain]::GetCurrentDomain()).DomainControllers[0].IPAddress

        $Adapters = Get-NetAdapter | Where-Object { $_.Status -eq "Up" }
        foreach ($Adapter in $Adapters) {
            # Set DC as primary DNS
            Set-DnsClientServerAddress -InterfaceIndex $Adapter.ifIndex -ServerAddresses $DC
            $Result.Actions += "Set DNS to DC ($DC) on $($Adapter.Name)"
        }
    } else {
        # Standalone - use DHCP or set reliable DNS
        $Adapters = Get-NetAdapter | Where-Object { $_.Status -eq "Up" }
        foreach ($Adapter in $Adapters) {
            # Check if DHCP
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
foreach ($Adapter in $Adapters) {
    $DNS = Get-DnsClientServerAddress -InterfaceIndex $Adapter.ifIndex -AddressFamily IPv4
    if ($DNS.ServerAddresses.Count -gt 0) {
        $HasDNS = $true
        break
    }
}
@{
    HasDNSServers = $HasDNS
    Verified = $HasDNS
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
# Network Runbooks Registry
# =============================================================================

NETWORK_RUNBOOKS: Dict[str, WindowsRunbook] = {
    "RB-WIN-NET-001": RUNBOOK_DNS_CLIENT,
    "RB-WIN-NET-002": RUNBOOK_NIC_RESET,
    "RB-WIN-NET-003": RUNBOOK_NETWORK_PROFILE,
    "RB-WIN-NET-004": RUNBOOK_NETBIOS,
}
