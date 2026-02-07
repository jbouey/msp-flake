"""
Windows Clinic/Healthcare Network Runbooks for HIPAA Compliance.

Runbooks for clinic-specific network compliance auditing including
rogue DHCP detection, VLAN segmentation, open port scanning, and
DNS content filtering verification.
"""

from typing import Dict, List
from dataclasses import dataclass, field
from .runbooks import WindowsRunbook, ExecutionConstraints


# =============================================================================
# RB-WIN-NET-006: Rogue DHCP Detection
# =============================================================================

RUNBOOK_ROGUE_DHCP = WindowsRunbook(
    id="RB-WIN-NET-006",
    name="Rogue DHCP Detection",
    description="Detect unauthorized DHCP servers on the clinic network that could redirect ePHI traffic",
    version="1.0",
    hipaa_controls=["164.312(a)(1)", "164.312(e)(1)"],
    severity="critical",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Detect rogue DHCP servers on the network
$ErrorActionPreference = "Stop"
$Result = @{
    Drifted = $false
    ApprovedServers = @()
    DetectedServers = @()
    RogueServers = @()
    Method = "Unknown"
}

# Approved DHCP servers - populated from environment or defaults
# In production, this should be injected from Central Command config
$ApprovedDHCPServers = @()

# Method 1: Check AD-authorized DHCP servers (domain-joined environments)
try {
    Import-Module DhcpServer -ErrorAction Stop
    $ADAuthorized = Get-DhcpServerInDC -ErrorAction Stop
    $Result.ApprovedServers = @($ADAuthorized | ForEach-Object { $_.IPAddress.ToString() })
    $Result.Method = "ActiveDirectory"

    # Get all DHCP servers that responded
    foreach ($Server in $ADAuthorized) {
        try {
            $ServerInfo = Get-DhcpServerSetting -ComputerName $Server.DnsName -ErrorAction Stop
            $Result.DetectedServers += @{
                IPAddress = $Server.IPAddress.ToString()
                DnsName = $Server.DnsName
                Authorized = $true
            }
        } catch {
            $Result.DetectedServers += @{
                IPAddress = $Server.IPAddress.ToString()
                DnsName = $Server.DnsName
                Authorized = $true
                Reachable = $false
            }
        }
    }
} catch {
    # Method 2: Network-based DHCP discovery via broadcast
    $Result.Method = "NetworkDiscovery"

    try {
        # Get current DHCP server from active lease
        $ActiveAdapters = Get-NetAdapter | Where-Object { $_.Status -eq "Up" }
        foreach ($Adapter in $ActiveAdapters) {
            $IPConfig = Get-NetIPConfiguration -InterfaceIndex $Adapter.ifIndex -ErrorAction SilentlyContinue
            if ($IPConfig.NetIPv4Interface.Dhcp -eq "Enabled") {
                $DhcpServer = $IPConfig.IPv4DefaultGateway
                # Get DHCP server from WMI
                $WMIAdapter = Get-WmiObject Win32_NetworkAdapterConfiguration |
                    Where-Object { $_.InterfaceIndex -eq $Adapter.ifIndex -and $_.DHCPEnabled }
                if ($WMIAdapter -and $WMIAdapter.DHCPServer) {
                    $Result.DetectedServers += @{
                        IPAddress = $WMIAdapter.DHCPServer
                        Source = "ActiveLease"
                        Adapter = $Adapter.Name
                        LeaseObtained = $WMIAdapter.DHCPLeaseObtained
                    }
                }
            }
        }

        # Use nbtstat/arp to find potential DHCP servers on the subnet
        $Gateway = (Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue).NextHop |
            Select-Object -First 1
        if ($Gateway) {
            $Subnet = $Gateway -replace '\.\d+$', ''
            # Check common DHCP server addresses
            $CommonDHCPAddresses = @("$Subnet.1", "$Subnet.2", "$Subnet.10", "$Subnet.254")
            foreach ($Addr in $CommonDHCPAddresses) {
                $Ping = Test-Connection -ComputerName $Addr -Count 1 -Quiet -ErrorAction SilentlyContinue
                if ($Ping) {
                    # Check if port 67 (DHCP) is responding
                    $UdpClient = New-Object System.Net.Sockets.UdpClient
                    $UdpClient.Client.ReceiveTimeout = 1000
                    try {
                        $UdpClient.Connect($Addr, 67)
                        $Result.DetectedServers += @{
                            IPAddress = $Addr
                            Source = "PortScan"
                            DHCPPortOpen = $true
                        }
                    } catch {
                        # Port not open, not a DHCP server
                    } finally {
                        $UdpClient.Close()
                    }
                }
            }
        }
    } catch {
        $Result.Error = "Discovery failed: $($_.Exception.Message)"
    }
}

# Check Windows DHCP server event log for DHCP conflicts
try {
    $DHCPConflicts = Get-WinEvent -FilterHashtable @{
        LogName = "System"
        ProviderName = "DHCPClient", "Dhcp"
        Level = 2, 3  # Error and Warning
        StartTime = (Get-Date).AddHours(-24)
    } -MaxEvents 20 -ErrorAction SilentlyContinue

    if ($DHCPConflicts) {
        $Result.RecentDHCPEvents = @($DHCPConflicts | ForEach-Object {
            @{
                TimeCreated = $_.TimeCreated.ToString("o")
                Id = $_.Id
                Message = $_.Message.Substring(0, [Math]::Min(200, $_.Message.Length))
            }
        })
    }
} catch {
    # Event log query failed - non-critical
}

# Determine if there are rogue servers
$ApprovedIPs = $Result.ApprovedServers
$DetectedIPs = @($Result.DetectedServers | ForEach-Object { $_.IPAddress })

foreach ($DetectedIP in $DetectedIPs) {
    if ($DetectedIP -and $ApprovedIPs.Count -gt 0 -and $DetectedIP -notin $ApprovedIPs) {
        $Result.RogueServers += $DetectedIP
        $Result.Drifted = $true
    }
}

# If no approved list and we detected servers, flag for review
if ($ApprovedIPs.Count -eq 0 -and $DetectedIPs.Count -gt 0) {
    $Result.Warning = "No approved DHCP server list configured - manual review required"
    $Result.Drifted = $true
}

$Result | ConvertTo-Json -Depth 3
''',

    remediate_script=r'''
# Rogue DHCP remediation - alert and generate evidence (cannot auto-remediate)
$Result = @{ Success = $false; Actions = @() }

try {
    # Collect evidence for the rogue DHCP detection
    $EvidenceBundle = @{
        Timestamp = (Get-Date).ToUniversalTime().ToString("o")
        Hostname = $env:COMPUTERNAME
        Domain = (Get-WmiObject Win32_ComputerSystem).Domain
        EventType = "RogueDHCPDetected"
    }

    # Capture current network state
    $EvidenceBundle.NetworkConfig = @{
        IPConfiguration = @(Get-NetIPConfiguration | ForEach-Object {
            @{
                InterfaceAlias = $_.InterfaceAlias
                IPv4Address = $_.IPv4Address.IPAddress
                DHCPServer = $_.IPv4DefaultGateway.NextHop
            }
        })
        DHCPLeases = @(Get-WmiObject Win32_NetworkAdapterConfiguration |
            Where-Object { $_.DHCPEnabled } | ForEach-Object {
            @{
                Description = $_.Description
                DHCPServer = $_.DHCPServer
                DHCPLeaseObtained = $_.DHCPLeaseObtained
                DHCPLeaseExpires = $_.DHCPLeaseExpires
            }
        })
    }

    # Log to Windows Event Log for SIEM collection
    $EventMessage = "HIPAA ALERT: Potential rogue DHCP server detected on clinic network. " +
        "Immediate investigation required. Evidence bundle generated at $(Get-Date -Format o)."

    try {
        # Create custom event source if it doesn't exist
        if (-not [System.Diagnostics.EventLog]::SourceExists("MSP-Compliance")) {
            New-EventLog -LogName Application -Source "MSP-Compliance" -ErrorAction SilentlyContinue
        }
        Write-EventLog -LogName Application -Source "MSP-Compliance" `
            -EventId 6001 -EntryType Warning -Message $EventMessage
        $Result.Actions += "Logged HIPAA alert to Application event log (EventId 6001)"
    } catch {
        $Result.Actions += "Could not write event log: $($_.Exception.Message)"
    }

    # Write evidence bundle to compliance directory
    $EvidencePath = "$env:ProgramData\MSP-Compliance\Evidence"
    if (-not (Test-Path $EvidencePath)) {
        New-Item -Path $EvidencePath -ItemType Directory -Force | Out-Null
    }
    $EvidenceFile = Join-Path $EvidencePath "rogue-dhcp-$(Get-Date -Format 'yyyyMMdd-HHmmss').json"
    $EvidenceBundle | ConvertTo-Json -Depth 5 | Set-Content -Path $EvidenceFile -Encoding UTF8
    $Result.Actions += "Evidence bundle saved to $EvidenceFile"

    # Block rogue DHCP at firewall level if detected
    $RogueServers = @()  # Populated from detect phase via Central Command
    foreach ($RogueIP in $RogueServers) {
        try {
            $RuleName = "Block-RogueDHCP-$($RogueIP.Replace('.', '-'))"
            $ExistingRule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
            if (-not $ExistingRule) {
                New-NetFirewallRule -DisplayName $RuleName `
                    -Direction Inbound -Protocol UDP -LocalPort 68 `
                    -RemoteAddress $RogueIP -Action Block `
                    -Description "Auto-block: Rogue DHCP server detected" | Out-Null
                $Result.Actions += "Created firewall rule to block rogue DHCP from $RogueIP"
            }
        } catch {
            $Result.Actions += "Failed to block $RogueIP`: $($_.Exception.Message)"
        }
    }

    $Result.Success = $true
    $Result.Message = "Rogue DHCP alert generated - requires manual investigation and network remediation"
    $Result.Escalation = "L3-REQUIRED: Contact network administrator to locate and disable rogue DHCP server"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 3
''',

    verify_script=r'''
# Re-scan to verify only approved DHCP servers respond
$Result = @{
    Compliant = $false
    DetectedServers = @()
}

try {
    # Re-check active DHCP leases
    $ActiveAdapters = Get-NetAdapter | Where-Object { $_.Status -eq "Up" }
    foreach ($Adapter in $ActiveAdapters) {
        $WMIAdapter = Get-WmiObject Win32_NetworkAdapterConfiguration |
            Where-Object { $_.InterfaceIndex -eq $Adapter.ifIndex -and $_.DHCPEnabled }
        if ($WMIAdapter -and $WMIAdapter.DHCPServer) {
            $Result.DetectedServers += $WMIAdapter.DHCPServer
        }
    }

    # Check AD-authorized list if available
    try {
        Import-Module DhcpServer -ErrorAction Stop
        $ADAuthorized = Get-DhcpServerInDC -ErrorAction Stop
        $ApprovedIPs = @($ADAuthorized | ForEach-Object { $_.IPAddress.ToString() })

        $RogueFound = $false
        foreach ($Server in $Result.DetectedServers) {
            if ($Server -notin $ApprovedIPs) {
                $RogueFound = $true
            }
        }
        $Result.Compliant = (-not $RogueFound)
        $Result.ApprovedCount = $ApprovedIPs.Count
    } catch {
        # Without AD, mark compliant if no new DHCP conflicts in last hour
        $RecentConflicts = Get-WinEvent -FilterHashtable @{
            LogName = "System"
            ProviderName = "DHCPClient", "Dhcp"
            Level = 2, 3
            StartTime = (Get-Date).AddHours(-1)
        } -MaxEvents 5 -ErrorAction SilentlyContinue

        $Result.Compliant = ($null -eq $RecentConflicts -or $RecentConflicts.Count -eq 0)
        $Result.Note = "AD not available - verified via event log (no conflicts in last hour)"
    }
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Compliant = $false
}

$Result | ConvertTo-Json -Depth 2
''',

    timeout_seconds=120,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["ApprovedServers", "DetectedServers", "RogueServers", "Method", "RecentDHCPEvents"]
)


# =============================================================================
# RB-WIN-NET-007: Network Segmentation / VLAN Audit
# =============================================================================

RUNBOOK_VLAN_AUDIT = WindowsRunbook(
    id="RB-WIN-NET-007",
    name="Network Segmentation / VLAN Audit",
    description="Verify ePHI systems are on isolated VLANs with restricted inter-VLAN routing",
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
# Check VLAN assignment and network segmentation for ePHI isolation
$ErrorActionPreference = "Stop"
$Result = @{
    Drifted = $false
    Adapters = @()
    SegmentationIssues = @()
    VLANInfo = @()
}

try {
    # Get physical adapter VLAN information
    $PhysicalAdapters = Get-NetAdapter -Physical | Where-Object { $_.Status -eq "Up" }

    foreach ($Adapter in $PhysicalAdapters) {
        $AdapterInfo = @{
            Name = $Adapter.Name
            InterfaceDescription = $Adapter.InterfaceDescription
            MacAddress = $Adapter.MacAddress
            LinkSpeed = $Adapter.LinkSpeed
            Status = $Adapter.Status.ToString()
        }

        # Get VLAN ID if available
        try {
            $VlanId = (Get-NetAdapterAdvancedProperty -Name $Adapter.Name -RegistryKeyword "VlanID" -ErrorAction SilentlyContinue).RegistryValue
            if ($VlanId) {
                $AdapterInfo.VlanId = [int]$VlanId
            } else {
                $AdapterInfo.VlanId = "NotConfigured"
            }
        } catch {
            $AdapterInfo.VlanId = "NotSupported"
        }

        # Get IP configuration
        $IPConfig = Get-NetIPConfiguration -InterfaceIndex $Adapter.ifIndex -ErrorAction SilentlyContinue
        if ($IPConfig) {
            $AdapterInfo.IPv4Address = ($IPConfig.IPv4Address | Select-Object -First 1).IPAddress
            $AdapterInfo.SubnetPrefix = ($IPConfig.IPv4Address | Select-Object -First 1).PrefixLength
            $AdapterInfo.DefaultGateway = ($IPConfig.IPv4DefaultGateway | Select-Object -First 1).NextHop
            $AdapterInfo.DNSServers = @($IPConfig.DNSServer | Where-Object { $_.AddressFamily -eq 2 } |
                ForEach-Object { $_.ServerAddresses }) | Select-Object -First 3
        }

        $Result.Adapters += $AdapterInfo
    }

    # Check for 802.1Q VLAN sub-interfaces
    $VlanAdapters = Get-NetAdapter | Where-Object { $_.Name -match "VLAN|vlan|\.\d+$" }
    foreach ($VlanAdapter in $VlanAdapters) {
        $Result.VLANInfo += @{
            Name = $VlanAdapter.Name
            Status = $VlanAdapter.Status.ToString()
            InterfaceDescription = $VlanAdapter.InterfaceDescription
        }
    }

    # Determine machine role to check proper VLAN assignment
    $ComputerSystem = Get-WmiObject Win32_ComputerSystem
    $OSInfo = Get-WmiObject Win32_OperatingSystem
    $MachineRole = @{
        DomainJoined = $ComputerSystem.PartOfDomain
        Domain = $ComputerSystem.Domain
        IsServer = ($OSInfo.ProductType -eq 3)
        IsWorkstation = ($OSInfo.ProductType -eq 1)
        ComputerName = $env:COMPUTERNAME
    }
    $Result.MachineRole = $MachineRole

    # Check if ePHI-related services are running (EHR, database, PACS)
    $EPHIServices = @(
        "MSSQLSERVER", "SQLSERVERAGENT",     # SQL Server (EHR database)
        "MySQL", "MySQL80",                   # MySQL
        "postgresql*",                        # PostgreSQL
        "W3SVC",                              # IIS (web-based EHR)
        "OrthanC", "DCM4CHEE",               # PACS/DICOM
        "HL7*",                               # HL7 interfaces
        "nextgen*", "athena*", "epic*"        # Common EHR services
    )

    $RunningEPHIServices = @()
    foreach ($ServicePattern in $EPHIServices) {
        $Found = Get-Service -Name $ServicePattern -ErrorAction SilentlyContinue |
            Where-Object { $_.Status -eq "Running" }
        if ($Found) {
            $RunningEPHIServices += $Found.Name
        }
    }
    $Result.EPHIServicesDetected = $RunningEPHIServices

    # Test segmentation by attempting traceroute to common medical device subnets
    $MedicalDeviceSubnets = @("10.10.10.1", "10.20.20.1", "172.16.100.1", "192.168.100.1")
    $SegmentationTests = @()

    foreach ($Target in $MedicalDeviceSubnets) {
        try {
            $TraceResult = Test-NetConnection -ComputerName $Target -TraceRoute -WarningAction SilentlyContinue -ErrorAction SilentlyContinue
            if ($TraceResult.PingSucceeded) {
                $HopCount = ($TraceResult.TraceRoute | Measure-Object).Count
                $SegmentationTests += @{
                    Target = $Target
                    Reachable = $true
                    HopCount = $HopCount
                    TraceRoute = @($TraceResult.TraceRoute)
                }

                # If a workstation can reach medical device subnets directly, flag it
                if ($MachineRole.IsWorkstation -and $HopCount -le 1) {
                    $Result.SegmentationIssues += "Workstation can directly reach medical subnet $Target (no L3 hop)"
                    $Result.Drifted = $true
                }
            } else {
                $SegmentationTests += @{
                    Target = $Target
                    Reachable = $false
                }
            }
        } catch {
            $SegmentationTests += @{
                Target = $Target
                Reachable = $false
                Error = $_.Exception.Message
            }
        }
    }
    $Result.SegmentationTests = $SegmentationTests

    # If ePHI services found on a machine without VLAN tagging, flag it
    if ($RunningEPHIServices.Count -gt 0) {
        $HasVlanTag = $Result.Adapters | Where-Object { $_.VlanId -ne "NotConfigured" -and $_.VlanId -ne "NotSupported" }
        if (-not $HasVlanTag) {
            $Result.SegmentationIssues += "ePHI services running on machine without VLAN isolation"
            $Result.Drifted = $true
        }
    }

    if ($Result.SegmentationIssues.Count -gt 0) {
        $Result.Drifted = $true
    }
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Drifted = $true
}

$Result | ConvertTo-Json -Depth 4
''',

    remediate_script=r'''
# VLAN remediation - alert and escalate (cannot auto-move VLANs)
$Result = @{ Success = $false; Actions = @() }

try {
    $Timestamp = (Get-Date).ToUniversalTime().ToString("o")

    # Collect evidence for the segmentation violation
    $EvidenceBundle = @{
        Timestamp = $Timestamp
        Hostname = $env:COMPUTERNAME
        Domain = (Get-WmiObject Win32_ComputerSystem).Domain
        EventType = "VLANSegmentationViolation"
        NetworkConfig = @{
            Adapters = @(Get-NetAdapter -Physical | Where-Object { $_.Status -eq "Up" } | ForEach-Object {
                @{
                    Name = $_.Name
                    MacAddress = $_.MacAddress
                    LinkSpeed = $_.LinkSpeed
                }
            })
            IPAddresses = @(Get-NetIPAddress -AddressFamily IPv4 |
                Where-Object { $_.IPAddress -ne "127.0.0.1" } | ForEach-Object {
                @{
                    IPAddress = $_.IPAddress
                    PrefixLength = $_.PrefixLength
                    InterfaceAlias = $_.InterfaceAlias
                }
            })
            Routes = @(Get-NetRoute -AddressFamily IPv4 |
                Where-Object { $_.DestinationPrefix -ne "0.0.0.0/0" -and $_.DestinationPrefix -ne "255.255.255.255/32" } |
                Select-Object -First 20 | ForEach-Object {
                @{
                    Destination = $_.DestinationPrefix
                    NextHop = $_.NextHop
                    InterfaceAlias = $_.InterfaceAlias
                }
            })
        }
    }

    # Log to Windows Event Log
    try {
        if (-not [System.Diagnostics.EventLog]::SourceExists("MSP-Compliance")) {
            New-EventLog -LogName Application -Source "MSP-Compliance" -ErrorAction SilentlyContinue
        }
        $EventMessage = "HIPAA ALERT: Network segmentation violation detected on $env:COMPUTERNAME. " +
            "ePHI systems may not be properly isolated. Immediate network review required."
        Write-EventLog -LogName Application -Source "MSP-Compliance" `
            -EventId 7001 -EntryType Warning -Message $EventMessage
        $Result.Actions += "Logged HIPAA segmentation alert to Application event log (EventId 7001)"
    } catch {
        $Result.Actions += "Could not write event log: $($_.Exception.Message)"
    }

    # Save evidence bundle
    $EvidencePath = "$env:ProgramData\MSP-Compliance\Evidence"
    if (-not (Test-Path $EvidencePath)) {
        New-Item -Path $EvidencePath -ItemType Directory -Force | Out-Null
    }
    $EvidenceFile = Join-Path $EvidencePath "vlan-audit-$(Get-Date -Format 'yyyyMMdd-HHmmss').json"
    $EvidenceBundle | ConvertTo-Json -Depth 5 | Set-Content -Path $EvidenceFile -Encoding UTF8
    $Result.Actions += "Evidence bundle saved to $EvidenceFile"

    $Result.Success = $true
    $Result.Message = "VLAN segmentation alert generated - requires network administrator intervention"
    $Result.Escalation = "L3-REQUIRED: Network administrator must verify VLAN assignments and inter-VLAN ACLs"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
# Re-check VLAN assignment after remediation
$Result = @{
    Compliant = $false
}

try {
    $PhysicalAdapters = Get-NetAdapter -Physical | Where-Object { $_.Status -eq "Up" }
    $VLANAssignments = @()

    foreach ($Adapter in $PhysicalAdapters) {
        $VlanId = $null
        try {
            $VlanId = (Get-NetAdapterAdvancedProperty -Name $Adapter.Name -RegistryKeyword "VlanID" -ErrorAction SilentlyContinue).RegistryValue
        } catch {}

        $VLANAssignments += @{
            Name = $Adapter.Name
            VlanId = if ($VlanId) { [int]$VlanId } else { "NotConfigured" }
            MacAddress = $Adapter.MacAddress
        }
    }

    $Result.VLANAssignments = $VLANAssignments

    # Check if ePHI services are on tagged VLANs
    $EPHIServices = @("MSSQLSERVER", "MySQL", "MySQL80", "W3SVC")
    $HasEPHI = $false
    foreach ($Svc in $EPHIServices) {
        $Running = Get-Service -Name $Svc -ErrorAction SilentlyContinue |
            Where-Object { $_.Status -eq "Running" }
        if ($Running) { $HasEPHI = $true; break }
    }

    if ($HasEPHI) {
        $HasVlanTag = $VLANAssignments | Where-Object { $_.VlanId -ne "NotConfigured" }
        $Result.Compliant = ($null -ne $HasVlanTag)
        $Result.Note = "ePHI services detected - VLAN tagging $(if ($Result.Compliant) { 'confirmed' } else { 'missing' })"
    } else {
        $Result.Compliant = $true
        $Result.Note = "No ePHI services detected on this machine"
    }
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Compliant = $false
}

$Result | ConvertTo-Json -Depth 3
''',

    timeout_seconds=180,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["Adapters", "VLANInfo", "SegmentationIssues", "EPHIServicesDetected", "MachineRole", "SegmentationTests"]
)


# =============================================================================
# RB-WIN-NET-008: Unauthorized Open Ports Detection
# =============================================================================

RUNBOOK_OPEN_PORTS = WindowsRunbook(
    id="RB-WIN-NET-008",
    name="Unauthorized Open Ports Detection",
    description="Detect and remediate unauthorized listening ports that expand the ePHI attack surface",
    version="1.0",
    hipaa_controls=["164.312(a)(1)", "164.312(e)(1)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=15,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Detect unauthorized listening ports
$ErrorActionPreference = "Stop"
$Result = @{
    Drifted = $false
    AllListeningPorts = @()
    AuthorizedPorts = @()
    UnauthorizedPorts = @()
}

# Allowed port whitelist for clinic environments
# These are standard Windows/AD infrastructure ports plus common healthcare
$AllowedPorts = @(
    53,    # DNS
    80,    # HTTP
    88,    # Kerberos
    135,   # RPC Endpoint Mapper
    389,   # LDAP
    443,   # HTTPS
    445,   # SMB
    636,   # LDAPS
    3389,  # RDP
    5985,  # WinRM HTTP
    5986,  # WinRM HTTPS
    9389,  # AD Web Services
    49152  # RPC Dynamic (start range)
)

# RPC dynamic range: 49152-65535 is standard Windows ephemeral
$RPCDynamicStart = 49152
$RPCDynamicEnd = 65535

try {
    # Get all listening TCP connections
    $Listeners = Get-NetTCPConnection -State Listen -ErrorAction Stop

    foreach ($Listener in $Listeners) {
        $Port = $Listener.LocalPort
        $ProcessId = $Listener.OwningProcess
        $LocalAddress = $Listener.LocalAddress

        # Get process info
        $Process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        $ProcessName = if ($Process) { $Process.ProcessName } else { "Unknown (PID: $ProcessId)" }
        $ProcessPath = if ($Process) { $Process.Path } else { "Unknown" }

        $PortInfo = @{
            Port = $Port
            LocalAddress = $LocalAddress
            ProcessName = $ProcessName
            ProcessPath = $ProcessPath
            ProcessId = $ProcessId
        }

        # Check if port is in allowed list or RPC dynamic range
        $IsAuthorized = ($Port -in $AllowedPorts) -or
                        ($Port -ge $RPCDynamicStart -and $Port -le $RPCDynamicEnd)

        # Also allow localhost-only listeners
        $IsLocalOnly = ($LocalAddress -eq "127.0.0.1" -or $LocalAddress -eq "::1")

        if ($IsAuthorized -or $IsLocalOnly) {
            $PortInfo.Authorized = $true
            $PortInfo.Reason = if ($IsLocalOnly) { "LocalhostOnly" }
                              elseif ($Port -ge $RPCDynamicStart) { "RPCDynamicRange" }
                              else { "Whitelisted" }
            $Result.AuthorizedPorts += $PortInfo
        } else {
            $PortInfo.Authorized = $false
            $PortInfo.Risk = switch ($Port) {
                21    { "HIGH - FTP (unencrypted file transfer)" }
                23    { "CRITICAL - Telnet (unencrypted remote access)" }
                25    { "MEDIUM - SMTP (potential relay)" }
                69    { "HIGH - TFTP (trivial file transfer)" }
                110   { "HIGH - POP3 (unencrypted email)" }
                137   { "MEDIUM - NetBIOS Name Service" }
                138   { "MEDIUM - NetBIOS Datagram" }
                139   { "HIGH - NetBIOS Session (legacy SMB)" }
                161   { "MEDIUM - SNMP (potential info leak)" }
                1433  { "HIGH - SQL Server (database)" }
                1521  { "HIGH - Oracle DB" }
                3306  { "HIGH - MySQL (database)" }
                5432  { "HIGH - PostgreSQL (database)" }
                8080  { "MEDIUM - HTTP Alt (potential web service)" }
                8443  { "MEDIUM - HTTPS Alt" }
                default { "MEDIUM - Unknown service on port $Port" }
            }
            $Result.UnauthorizedPorts += $PortInfo
        }

        $Result.AllListeningPorts += $PortInfo
    }

    # Check for listening UDP ports that could be risky
    $UDPListeners = Get-NetUDPEndpoint -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -ne "127.0.0.1" -and $_.LocalAddress -ne "::1" }

    $SuspiciousUDP = @(69, 161, 162, 514, 1900, 5353)  # TFTP, SNMP, Syslog, UPnP, mDNS
    foreach ($UDPEndpoint in $UDPListeners) {
        if ($UDPEndpoint.LocalPort -in $SuspiciousUDP) {
            $UDPProcess = Get-Process -Id $UDPEndpoint.OwningProcess -ErrorAction SilentlyContinue
            $Result.UnauthorizedPorts += @{
                Port = $UDPEndpoint.LocalPort
                Protocol = "UDP"
                LocalAddress = $UDPEndpoint.LocalAddress
                ProcessName = if ($UDPProcess) { $UDPProcess.ProcessName } else { "Unknown" }
                Authorized = $false
                Risk = "MEDIUM - Suspicious UDP service on port $($UDPEndpoint.LocalPort)"
            }
        }
    }

    $Result.TotalListening = $Result.AllListeningPorts.Count
    $Result.UnauthorizedCount = $Result.UnauthorizedPorts.Count
    $Result.Drifted = ($Result.UnauthorizedPorts.Count -gt 0)

} catch {
    $Result.Error = $_.Exception.Message
    $Result.Drifted = $true
}

$Result | ConvertTo-Json -Depth 3
''',

    remediate_script=r'''
# Stop services on unauthorized ports and block via firewall
$Result = @{ Success = $false; Actions = @() }

try {
    # Allowed ports - same whitelist as detect phase
    $AllowedPorts = @(53, 80, 88, 135, 389, 443, 445, 636, 3389, 5985, 5986, 9389)
    $RPCDynamicStart = 49152

    # Get unauthorized listening ports
    $Listeners = Get-NetTCPConnection -State Listen
    $Unauthorized = @()

    foreach ($Listener in $Listeners) {
        $Port = $Listener.LocalPort
        $IsAuthorized = ($Port -in $AllowedPorts) -or ($Port -ge $RPCDynamicStart)
        $IsLocalOnly = ($Listener.LocalAddress -eq "127.0.0.1" -or $Listener.LocalAddress -eq "::1")

        if (-not $IsAuthorized -and -not $IsLocalOnly) {
            $Unauthorized += $Listener
        }
    }

    foreach ($Listener in $Unauthorized) {
        $Port = $Listener.LocalPort
        $ProcessId = $Listener.OwningProcess
        $Process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue

        if ($Process) {
            $ProcessName = $Process.ProcessName

            # Don't stop critical Windows services
            $CriticalProcesses = @("System", "svchost", "lsass", "csrss", "wininit",
                                   "services", "smss", "winlogon")

            if ($ProcessName -notin $CriticalProcesses) {
                # Try to find and stop the associated Windows service
                $Service = Get-WmiObject Win32_Service |
                    Where-Object { $_.ProcessId -eq $ProcessId } |
                    Select-Object -First 1

                if ($Service) {
                    try {
                        Stop-Service -Name $Service.Name -Force -ErrorAction Stop
                        Set-Service -Name $Service.Name -StartupType Disabled
                        $Result.Actions += "Stopped and disabled service '$($Service.Name)' on port $Port"
                    } catch {
                        $Result.Actions += "Failed to stop service '$($Service.Name)' on port $Port`: $($_.Exception.Message)"
                    }
                } else {
                    # Not a service - try to stop the process
                    try {
                        Stop-Process -Id $ProcessId -Force -ErrorAction Stop
                        $Result.Actions += "Stopped process '$ProcessName' (PID $ProcessId) on port $Port"
                    } catch {
                        $Result.Actions += "Failed to stop process '$ProcessName' on port $Port`: $($_.Exception.Message)"
                    }
                }
            } else {
                $Result.Actions += "SKIPPED: Critical process '$ProcessName' on port $Port (manual review required)"
            }
        }

        # Create firewall rule to block the port
        $RuleName = "MSP-Block-UnauthorizedPort-$Port"
        $ExistingRule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
        if (-not $ExistingRule) {
            try {
                New-NetFirewallRule -DisplayName $RuleName `
                    -Direction Inbound -Protocol TCP -LocalPort $Port `
                    -Action Block `
                    -Description "Auto-block: Unauthorized listening port detected by compliance scan" | Out-Null
                $Result.Actions += "Created firewall rule blocking inbound TCP port $Port"
            } catch {
                $Result.Actions += "Failed to create firewall rule for port $Port`: $($_.Exception.Message)"
            }
        }
    }

    if ($Unauthorized.Count -eq 0) {
        $Result.Actions += "No unauthorized ports found requiring remediation"
    }

    $Result.Success = $true
    $Result.PortsRemediated = $Unauthorized.Count
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
# Re-scan listening ports to verify remediation
$AllowedPorts = @(53, 80, 88, 135, 389, 443, 445, 636, 3389, 5985, 5986, 9389)
$RPCDynamicStart = 49152

$Listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue
$StillUnauthorized = @()

foreach ($Listener in $Listeners) {
    $Port = $Listener.LocalPort
    $IsAuthorized = ($Port -in $AllowedPorts) -or ($Port -ge $RPCDynamicStart)
    $IsLocalOnly = ($Listener.LocalAddress -eq "127.0.0.1" -or $Listener.LocalAddress -eq "::1")

    if (-not $IsAuthorized -and -not $IsLocalOnly) {
        $Process = Get-Process -Id $Listener.OwningProcess -ErrorAction SilentlyContinue
        $StillUnauthorized += @{
            Port = $Port
            ProcessName = if ($Process) { $Process.ProcessName } else { "Unknown" }
        }
    }
}

@{
    UnauthorizedRemaining = $StillUnauthorized.Count
    RemainingPorts = $StillUnauthorized
    Compliant = ($StillUnauthorized.Count -eq 0)
} | ConvertTo-Json -Depth 2
''',

    timeout_seconds=120,
    requires_reboot=False,
    disruptive=True,  # Stopping services can be disruptive
    evidence_fields=["AllListeningPorts", "UnauthorizedPorts", "UnauthorizedCount", "TotalListening"]
)


# =============================================================================
# RB-WIN-NET-009: DNS Content Filtering Verification
# =============================================================================

RUNBOOK_DNS_FILTERING = WindowsRunbook(
    id="RB-WIN-NET-009",
    name="DNS Content Filtering Verification",
    description="Verify DNS content filtering is active to protect clinic endpoints from malware and phishing",
    version="1.0",
    hipaa_controls=["164.308(a)(5)(ii)(B)", "164.312(e)(1)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Verify DNS content filtering is properly configured
$ErrorActionPreference = "Stop"
$Result = @{
    Drifted = $false
    DNSServers = @()
    FilteringProvider = "Unknown"
    FilteringActive = $false
    TestResults = @()
}

# Known DNS filtering provider IP ranges
$FilteringProviders = @{
    "CiscoUmbrella" = @("208.67.222.222", "208.67.220.220", "208.67.222.123", "208.67.220.123")
    "CloudflareGateway" = @("172.64.36.1", "172.64.36.2", "1.1.1.2", "1.0.0.2", "1.1.1.3", "1.0.0.3")
    "OpenDNS_FamilyShield" = @("208.67.222.123", "208.67.220.123")
    "CleanBrowsing" = @("185.228.168.9", "185.228.169.9", "185.228.168.10", "185.228.169.11")
    "Quad9" = @("9.9.9.9", "149.112.112.112", "9.9.9.11", "149.112.112.11")
    "FortiGuard" = @("208.91.112.53", "208.91.112.52")
    "NextDNS" = @("45.90.28.0", "45.90.30.0")
}

try {
    # Get DNS servers from all active adapters
    $ActiveAdapters = Get-NetAdapter | Where-Object { $_.Status -eq "Up" }

    foreach ($Adapter in $ActiveAdapters) {
        $DNS = Get-DnsClientServerAddress -InterfaceIndex $Adapter.ifIndex -AddressFamily IPv4
        foreach ($Server in $DNS.ServerAddresses) {
            if ($Server -and $Server -notin $Result.DNSServers) {
                $Result.DNSServers += $Server
            }
        }
    }

    # Check if configured DNS matches a known filtering provider
    $MatchedProvider = $null
    foreach ($Provider in $FilteringProviders.GetEnumerator()) {
        foreach ($DNSServer in $Result.DNSServers) {
            if ($DNSServer -in $Provider.Value) {
                $MatchedProvider = $Provider.Key
                break
            }
        }
        if ($MatchedProvider) { break }
    }

    if ($MatchedProvider) {
        $Result.FilteringProvider = $MatchedProvider
    } else {
        # Check if DNS server is internal (AD DNS that may forward to filtering)
        $InternalDNS = $Result.DNSServers | Where-Object {
            $_ -match "^10\." -or $_ -match "^172\.(1[6-9]|2[0-9]|3[01])\." -or $_ -match "^192\.168\."
        }
        if ($InternalDNS) {
            $Result.FilteringProvider = "InternalDNS"
            $Result.Note = "Internal DNS detected - filtering may be configured upstream"
        } else {
            $Result.FilteringProvider = "None"
            $Result.Drifted = $true
        }
    }

    # Test DNS filtering by resolving known test/malware domains
    # These are safe test domains used by security vendors to verify filtering
    $TestDomains = @(
        @{ Domain = "examplemalwaredomain.com"; Type = "Malware"; Expected = "Blocked" },
        @{ Domain = "internetbadguys.com"; Type = "Malware_Umbrella"; Expected = "Blocked" },
        @{ Domain = "phishing.testcategory.com"; Type = "Phishing"; Expected = "Blocked" },
        @{ Domain = "malware.testcategory.com"; Type = "Malware_Test"; Expected = "Blocked" }
    )

    foreach ($Test in $TestDomains) {
        $TestResult = @{
            Domain = $Test.Domain
            Category = $Test.Type
            Expected = $Test.Expected
        }

        try {
            $Resolution = Resolve-DnsName -Name $Test.Domain -Type A -DnsOnly -ErrorAction Stop
            $ResolvedIP = ($Resolution | Where-Object { $_.QueryType -eq "A" } | Select-Object -First 1).IPAddress

            if ($ResolvedIP) {
                # Check if resolved to a block page IP (common sinkhole IPs)
                $SinkholeIPs = @(
                    "0.0.0.0", "127.0.0.1",
                    "146.112.61.104", "146.112.61.105",  # Cisco Umbrella block
                    "146.112.61.106", "146.112.61.107",
                    "::1"
                )

                if ($ResolvedIP -in $SinkholeIPs) {
                    $TestResult.Result = "Blocked"
                    $TestResult.ResolvedTo = $ResolvedIP
                } else {
                    $TestResult.Result = "Resolved"
                    $TestResult.ResolvedTo = $ResolvedIP
                    $TestResult.Warning = "Domain resolved to real IP - filtering may not be active"
                }
            } else {
                $TestResult.Result = "NoRecord"
            }
        } catch {
            # NXDOMAIN or timeout can indicate blocking
            if ($_.Exception.Message -match "DNS name does not exist|non-existent domain") {
                $TestResult.Result = "NXDOMAIN"
                $TestResult.Note = "Domain blocked at DNS level"
            } else {
                $TestResult.Result = "Error"
                $TestResult.Error = $_.Exception.Message
            }
        }

        $Result.TestResults += $TestResult
    }

    # Determine if filtering is actually working
    $BlockedCount = ($Result.TestResults | Where-Object {
        $_.Result -in @("Blocked", "NXDOMAIN")
    }).Count
    $TotalTests = $Result.TestResults.Count

    $Result.FilteringActive = ($BlockedCount -ge 1)  # At least 1 test domain blocked
    $Result.BlockRate = "$BlockedCount/$TotalTests domains blocked"

    if (-not $Result.FilteringActive -and $Result.FilteringProvider -ne "InternalDNS") {
        $Result.Drifted = $true
    }

} catch {
    $Result.Error = $_.Exception.Message
    $Result.Drifted = $true
}

$Result | ConvertTo-Json -Depth 3
''',

    remediate_script=r'''
# Configure DNS to use approved filtering provider
$Result = @{ Success = $false; Actions = @() }

try {
    # Default to Cisco Umbrella (OpenDNS) - widely used in healthcare
    # These can be overridden by Central Command configuration
    $PrimaryDNS = "208.67.222.222"    # Cisco Umbrella primary
    $SecondaryDNS = "208.67.220.220"  # Cisco Umbrella secondary

    $ActiveAdapters = Get-NetAdapter | Where-Object { $_.Status -eq "Up" }

    # Check if machine is domain-joined (shouldn't change DC DNS directly)
    $IsDomainJoined = (Get-WmiObject Win32_ComputerSystem).PartOfDomain

    if ($IsDomainJoined) {
        # On domain-joined machines, DNS should point to DC
        # Instead, configure DNS forwarders on the DC if this IS a DC
        $IsDC = $false
        try {
            Import-Module ActiveDirectory -ErrorAction Stop
            $DC = Get-ADDomainController -Identity $env:COMPUTERNAME -ErrorAction Stop
            $IsDC = $true
        } catch {
            $IsDC = $false
        }

        if ($IsDC) {
            # Configure DNS server forwarders to use filtering DNS
            try {
                Import-Module DnsServer -ErrorAction Stop
                $CurrentForwarders = (Get-DnsServerForwarder).IPAddress.IPAddressToString

                # Add filtering DNS as forwarders
                $NewForwarders = @($PrimaryDNS, $SecondaryDNS)
                Set-DnsServerForwarder -IPAddress $NewForwarders -ErrorAction Stop
                $Result.Actions += "Configured DC DNS forwarders to Cisco Umbrella ($PrimaryDNS, $SecondaryDNS)"
            } catch {
                $Result.Actions += "Failed to set DNS forwarders: $($_.Exception.Message)"
            }
        } else {
            # Workstation/member server - ensure it points to DC for DNS
            $Result.Actions += "Domain-joined workstation - DNS should point to DC. Forwarders must be configured on DC."
            $Result.Escalation = "Configure DNS filtering forwarders on Domain Controller"
        }
    } else {
        # Standalone machine - set DNS directly to filtering provider
        foreach ($Adapter in $ActiveAdapters) {
            try {
                Set-DnsClientServerAddress -InterfaceIndex $Adapter.ifIndex `
                    -ServerAddresses @($PrimaryDNS, $SecondaryDNS)
                $Result.Actions += "Set DNS on '$($Adapter.Name)' to Cisco Umbrella ($PrimaryDNS, $SecondaryDNS)"
            } catch {
                $Result.Actions += "Failed to set DNS on '$($Adapter.Name)': $($_.Exception.Message)"
            }
        }

        # Flush DNS cache to apply changes immediately
        Clear-DnsClientCache
        $Result.Actions += "Flushed DNS client cache"
    }

    # Verify the change took effect
    Start-Sleep -Seconds 2
    try {
        $TestResolve = Resolve-DnsName -Name "internetbadguys.com" -Type A -DnsOnly -ErrorAction Stop
        $ResolvedIP = ($TestResolve | Where-Object { $_.QueryType -eq "A" } | Select-Object -First 1).IPAddress
        $SinkholeIPs = @("0.0.0.0", "127.0.0.1", "146.112.61.104", "146.112.61.105", "146.112.61.106")
        if ($ResolvedIP -in $SinkholeIPs) {
            $Result.Actions += "DNS filtering verified - test domain blocked successfully"
        }
    } catch {
        if ($_.Exception.Message -match "non-existent domain|DNS name does not exist") {
            $Result.Actions += "DNS filtering verified - test domain returned NXDOMAIN"
        }
    }

    $Result.Success = $true
    $Result.FilteringProvider = "CiscoUmbrella"
    $Result.Message = "DNS content filtering configured"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
# Re-test DNS filtering to verify it is active
$Result = @{
    Compliant = $false
}

try {
    # Check current DNS servers
    $DNSServers = @()
    $ActiveAdapters = Get-NetAdapter | Where-Object { $_.Status -eq "Up" }
    foreach ($Adapter in $ActiveAdapters) {
        $DNS = Get-DnsClientServerAddress -InterfaceIndex $Adapter.ifIndex -AddressFamily IPv4
        $DNSServers += $DNS.ServerAddresses
    }
    $Result.ConfiguredDNS = @($DNSServers | Select-Object -Unique)

    # Test filtering with known test domain
    $FilteringWorks = $false
    try {
        $TestResolve = Resolve-DnsName -Name "internetbadguys.com" -Type A -DnsOnly -ErrorAction Stop
        $ResolvedIP = ($TestResolve | Where-Object { $_.QueryType -eq "A" } | Select-Object -First 1).IPAddress
        $SinkholeIPs = @("0.0.0.0", "127.0.0.1", "146.112.61.104", "146.112.61.105", "146.112.61.106", "146.112.61.107")
        if ($ResolvedIP -in $SinkholeIPs) {
            $FilteringWorks = $true
            $Result.TestResult = "Blocked (sinkholed to $ResolvedIP)"
        } else {
            $Result.TestResult = "NOT BLOCKED (resolved to $ResolvedIP)"
        }
    } catch {
        if ($_.Exception.Message -match "non-existent domain|DNS name does not exist") {
            $FilteringWorks = $true
            $Result.TestResult = "Blocked (NXDOMAIN)"
        } else {
            $Result.TestResult = "Error: $($_.Exception.Message)"
        }
    }

    $Result.FilteringActive = $FilteringWorks
    $Result.Compliant = $FilteringWorks
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Compliant = $false
}

$Result | ConvertTo-Json -Depth 2
''',

    timeout_seconds=120,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["DNSServers", "FilteringProvider", "FilteringActive", "TestResults", "BlockRate"]
)


# =============================================================================
# Clinic Network Runbooks Registry
# =============================================================================

CLINIC_NETWORK_RUNBOOKS: Dict[str, WindowsRunbook] = {
    "RB-WIN-NET-006": RUNBOOK_ROGUE_DHCP,
    "RB-WIN-NET-007": RUNBOOK_VLAN_AUDIT,
    "RB-WIN-NET-008": RUNBOOK_OPEN_PORTS,
    "RB-WIN-NET-009": RUNBOOK_DNS_FILTERING,
}
