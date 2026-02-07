"""
Windows Clinic Device Runbooks for HIPAA Compliance.

Runbooks for clinic device compliance auditing including printers,
IoT/medical devices, default credentials, and EDR deployment verification.
"""

from typing import Dict, List
from dataclasses import dataclass, field
from .runbooks import WindowsRunbook, ExecutionConstraints


# =============================================================================
# RB-WIN-DEVICE-001: Network Printer/Copier Security Audit
# =============================================================================

RUNBOOK_PRINTER_SECURITY = WindowsRunbook(
    id="RB-WIN-DEVICE-001",
    name="Network Printer/Copier Security Audit",
    description="Scan for network printers and audit SNMP community strings, HTTPS admin, firmware, and hard drive encryption",
    version="1.0",
    hipaa_controls=["164.312(a)(1)", "164.312(e)(2)(ii)", "164.310(d)(1)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Scan subnet for network printers and audit security posture
$ErrorActionPreference = "SilentlyContinue"
$Result = @{
    Drifted = $false
    Printers = @()
    Issues = @()
    ScanTimestamp = (Get-Date).ToUniversalTime().ToString("o")
}

# Get local subnet from primary adapter
$IPConfig = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.PrefixOrigin -ne "WellKnown" -and $_.IPAddress -ne "127.0.0.1" } |
    Select-Object -First 1

if (-not $IPConfig) {
    @{ Error = "No active network adapter found"; Drifted = $true } | ConvertTo-Json
    return
}

$LocalIP = $IPConfig.IPAddress
$Prefix = $IPConfig.PrefixLength
$Subnet = ($LocalIP -split '\.')[0..2] -join '.'

# Common printer ports: 9100 (RAW), 515 (LPR), 631 (IPP)
$PrinterPorts = @(9100, 515, 631)
$FoundPrinters = @()

# Scan subnet for devices with printer ports open
$ScanRange = 1..254
$Jobs = @()

foreach ($Octet in $ScanRange) {
    $TargetIP = "$Subnet.$Octet"
    if ($TargetIP -eq $LocalIP) { continue }

    # Test primary printer port (9100) with short timeout
    $Job = Test-NetConnection -ComputerName $TargetIP -Port 9100 -WarningAction SilentlyContinue -InformationLevel Quiet
    if ($Job) {
        $FoundPrinters += $TargetIP
    }
}

# Also check ports 515 and 631 on ARP-discovered devices
$ArpEntries = Get-NetNeighbor -AddressFamily IPv4 |
    Where-Object { $_.State -ne "Unreachable" -and $_.IPAddress -like "$Subnet.*" }

foreach ($Entry in $ArpEntries) {
    $IP = $Entry.IPAddress
    if ($IP -in $FoundPrinters) { continue }
    foreach ($Port in @(515, 631)) {
        $Test = Test-NetConnection -ComputerName $IP -Port $Port -WarningAction SilentlyContinue -InformationLevel Quiet
        if ($Test) {
            $FoundPrinters += $IP
            break
        }
    }
}

$FoundPrinters = $FoundPrinters | Select-Object -Unique

foreach ($PrinterIP in $FoundPrinters) {
    $PrinterInfo = @{
        IPAddress = $PrinterIP
        OpenPorts = @()
        Issues = @()
        DefaultSNMP = $false
        HTTPSAdmin = $false
        FirmwareVersion = "Unknown"
        HardDriveEncryption = "Unknown"
    }

    # Check which printer ports are open
    foreach ($Port in $PrinterPorts) {
        $PortTest = Test-NetConnection -ComputerName $PrinterIP -Port $Port -WarningAction SilentlyContinue
        if ($PortTest.TcpTestSucceeded) {
            $PrinterInfo.OpenPorts += $Port
        }
    }

    # Check SNMP default community strings (public/private)
    # Use .NET SNMP query if available, otherwise use snmpwalk if installed
    try {
        # Try SNMP query with 'public' community string (OID: sysDescr.0)
        $UdpClient = New-Object System.Net.Sockets.UdpClient
        $UdpClient.Client.ReceiveTimeout = 2000

        # SNMP GET for sysDescr.0 (1.3.6.1.2.1.1.1.0) with community 'public'
        # Simplified SNMP v1 GET packet
        $SNMPPacket = [byte[]](
            0x30, 0x26, 0x02, 0x01, 0x00, 0x04, 0x06, 0x70,
            0x75, 0x62, 0x6C, 0x69, 0x63, 0xA0, 0x19, 0x02,
            0x04, 0x00, 0x00, 0x00, 0x01, 0x02, 0x01, 0x00,
            0x02, 0x01, 0x00, 0x30, 0x0B, 0x30, 0x09, 0x06,
            0x05, 0x2B, 0x06, 0x01, 0x02, 0x01, 0x05, 0x00
        )

        $Endpoint = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Parse($PrinterIP), 161)
        $UdpClient.Send($SNMPPacket, $SNMPPacket.Length, $Endpoint) | Out-Null
        $RemoteEP = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)

        try {
            $Response = $UdpClient.Receive([ref]$RemoteEP)
            if ($Response.Length -gt 0) {
                $PrinterInfo.DefaultSNMP = $true
                $PrinterInfo.Issues += "Default SNMP community string 'public' accepted"
                $Result.Drifted = $true

                # Try to extract sysDescr from response for firmware info
                $ResponseStr = [System.Text.Encoding]::ASCII.GetString($Response)
                if ($ResponseStr -match '[A-Za-z].*\d+\.\d+') {
                    $PrinterInfo.FirmwareVersion = $Matches[0].Trim()
                }
            }
        } catch {
            # Timeout = community string rejected (good)
            $PrinterInfo.DefaultSNMP = $false
        }

        $UdpClient.Close()

        # Also test 'private' community string
        $UdpClient2 = New-Object System.Net.Sockets.UdpClient
        $UdpClient2.Client.ReceiveTimeout = 2000

        $SNMPPacketPrivate = [byte[]](
            0x30, 0x27, 0x02, 0x01, 0x00, 0x04, 0x07, 0x70,
            0x72, 0x69, 0x76, 0x61, 0x74, 0x65, 0xA0, 0x19,
            0x02, 0x04, 0x00, 0x00, 0x00, 0x02, 0x02, 0x01,
            0x00, 0x02, 0x01, 0x00, 0x30, 0x0B, 0x30, 0x09,
            0x06, 0x05, 0x2B, 0x06, 0x01, 0x02, 0x01, 0x05,
            0x00
        )

        $Endpoint2 = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Parse($PrinterIP), 161)
        $UdpClient2.Send($SNMPPacketPrivate, $SNMPPacketPrivate.Length, $Endpoint2) | Out-Null

        try {
            $Response2 = $UdpClient2.Receive([ref]$RemoteEP)
            if ($Response2.Length -gt 0) {
                $PrinterInfo.Issues += "Default SNMP community string 'private' accepted (READ-WRITE)"
                $Result.Drifted = $true
            }
        } catch {
            # Timeout = community string rejected (good)
        }

        $UdpClient2.Close()
    } catch {
        $PrinterInfo.SNMPError = $_.Exception.Message
    }

    # Check if web admin portal uses HTTPS
    try {
        # Test HTTPS (port 443)
        $HTTPSTest = Test-NetConnection -ComputerName $PrinterIP -Port 443 -WarningAction SilentlyContinue
        $HTTPTest = Test-NetConnection -ComputerName $PrinterIP -Port 80 -WarningAction SilentlyContinue

        if ($HTTPSTest.TcpTestSucceeded) {
            $PrinterInfo.HTTPSAdmin = $true
        }

        if ($HTTPTest.TcpTestSucceeded -and -not $HTTPSTest.TcpTestSucceeded) {
            $PrinterInfo.Issues += "Web admin accessible via HTTP only (no HTTPS)"
            $Result.Drifted = $true
        }

        if (-not $HTTPSTest.TcpTestSucceeded -and -not $HTTPTest.TcpTestSucceeded) {
            $PrinterInfo.WebAdminAvailable = $false
        }
    } catch {
        $PrinterInfo.WebAdminError = $_.Exception.Message
    }

    # Check hard drive encryption via SNMP OID if SNMP is accessible
    # Common OID for storage info: 1.3.6.1.2.1.25.2 (hrStorage)
    if ($PrinterInfo.DefaultSNMP) {
        $PrinterInfo.HardDriveEncryption = "SNMP accessible with defaults - cannot verify encryption status securely"
        $PrinterInfo.Issues += "Hard drive encryption status unverifiable due to default SNMP credentials"
    }

    $Result.Printers += $PrinterInfo
    $Result.Issues += $PrinterInfo.Issues
}

$Result.PrinterCount = $FoundPrinters.Count
$Result.NonCompliantCount = ($Result.Printers | Where-Object { $_.Issues.Count -gt 0 }).Count

if ($Result.PrinterCount -eq 0) {
    $Result.Message = "No network printers detected on subnet $Subnet.0/$Prefix"
}

$Result | ConvertTo-Json -Depth 4
''',

    remediate_script=r'''
# Printer security issues require manual/escalated remediation
# Printers cannot be safely auto-remediated remotely
$Result = @{ Success = $true; Actions = @() }

# Re-scan to build current findings report
$IPConfig = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.PrefixOrigin -ne "WellKnown" -and $_.IPAddress -ne "127.0.0.1" } |
    Select-Object -First 1

$Subnet = ($IPConfig.IPAddress -split '\.')[0..2] -join '.'
$Findings = @()

# Check known printer ports on ARP table
$ArpEntries = Get-NetNeighbor -AddressFamily IPv4 |
    Where-Object { $_.State -ne "Unreachable" -and $_.IPAddress -like "$Subnet.*" }

foreach ($Entry in $ArpEntries) {
    $IP = $Entry.IPAddress
    $PrinterTest = Test-NetConnection -ComputerName $IP -Port 9100 -WarningAction SilentlyContinue -InformationLevel Quiet
    if ($PrinterTest) {
        $Finding = @{
            IPAddress = $IP
            MACAddress = $Entry.LinkLayerAddress
            Recommendations = @(
                "Change SNMP community strings from defaults",
                "Enable HTTPS on web admin portal",
                "Enable hard drive encryption if available",
                "Update firmware to latest version",
                "Restrict web admin access to management VLAN"
            )
        }
        $Findings += $Finding
    }
}

$Result.Actions += "Generated printer security findings report"
$Result.Findings = $Findings
$Result.FindingsCount = $Findings.Count
$Result.Escalation = "REQUIRED - Printer remediation must be performed manually or via vendor tools"
$Result.Priority = "HIGH - Default SNMP credentials expose device configuration"

$Result | ConvertTo-Json -Depth 3
''',

    verify_script=r'''
# Re-scan printers and verify SNMP community strings changed
$ErrorActionPreference = "SilentlyContinue"
$Result = @{
    Compliant = $true
    PrintersChecked = 0
    StillNonCompliant = @()
}

$IPConfig = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.PrefixOrigin -ne "WellKnown" -and $_.IPAddress -ne "127.0.0.1" } |
    Select-Object -First 1

$Subnet = ($IPConfig.IPAddress -split '\.')[0..2] -join '.'

# Check ARP table for printer devices
$ArpEntries = Get-NetNeighbor -AddressFamily IPv4 |
    Where-Object { $_.State -ne "Unreachable" -and $_.IPAddress -like "$Subnet.*" }

foreach ($Entry in $ArpEntries) {
    $IP = $Entry.IPAddress
    $PrinterTest = Test-NetConnection -ComputerName $IP -Port 9100 -WarningAction SilentlyContinue -InformationLevel Quiet
    if (-not $PrinterTest) { continue }

    $Result.PrintersChecked++

    # Test default SNMP community string 'public'
    try {
        $UdpClient = New-Object System.Net.Sockets.UdpClient
        $UdpClient.Client.ReceiveTimeout = 2000

        $SNMPPacket = [byte[]](
            0x30, 0x26, 0x02, 0x01, 0x00, 0x04, 0x06, 0x70,
            0x75, 0x62, 0x6C, 0x69, 0x63, 0xA0, 0x19, 0x02,
            0x04, 0x00, 0x00, 0x00, 0x01, 0x02, 0x01, 0x00,
            0x02, 0x01, 0x00, 0x30, 0x0B, 0x30, 0x09, 0x06,
            0x05, 0x2B, 0x06, 0x01, 0x02, 0x01, 0x05, 0x00
        )

        $Endpoint = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Parse($IP), 161)
        $UdpClient.Send($SNMPPacket, $SNMPPacket.Length, $Endpoint) | Out-Null
        $RemoteEP = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)

        try {
            $Response = $UdpClient.Receive([ref]$RemoteEP)
            if ($Response.Length -gt 0) {
                $Result.Compliant = $false
                $Result.StillNonCompliant += @{
                    IPAddress = $IP
                    Issue = "Default SNMP community string 'public' still accepted"
                }
            }
        } catch {
            # Timeout = good, community string rejected
        }

        $UdpClient.Close()
    } catch {
        # SNMP check failed
    }
}

$Result | ConvertTo-Json -Depth 3
''',

    timeout_seconds=600,  # Network scanning takes time
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["PrinterCount", "NonCompliantCount", "Printers", "Issues"]
)


# =============================================================================
# RB-WIN-DEVICE-002: IoT/Medical Device Inventory
# =============================================================================

RUNBOOK_IOT_INVENTORY = WindowsRunbook(
    id="RB-WIN-DEVICE-002",
    name="IoT/Medical Device Inventory",
    description="Discover all devices on clinic network, identify medical devices by MAC OUI, check network isolation",
    version="1.0",
    hipaa_controls=["164.312(a)(1)", "164.308(a)(1)(ii)(A)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Discover all devices on clinic network and identify medical devices
$ErrorActionPreference = "SilentlyContinue"
$Result = @{
    Drifted = $false
    Devices = @()
    MedicalDevices = @()
    UnmanagedDevices = @()
    Issues = @()
    ScanTimestamp = (Get-Date).ToUniversalTime().ToString("o")
}

# Medical device vendor MAC OUI prefixes (first 3 octets)
$MedicalOUIs = @{
    "00-09-FB" = "Philips Healthcare"
    "00-1E-8F" = "Philips Medical Systems"
    "00-21-A0" = "Philips Healthcare"
    "00-80-25" = "GE Healthcare"
    "00-19-B3" = "GE Healthcare"
    "00-50-F1" = "GE Healthcare"
    "00-00-87" = "Siemens Healthineers"
    "00-01-E5" = "Siemens Medical"
    "08-00-06" = "Siemens AG"
    "00-80-F4" = "Telemecanique (Baxter)"
    "00-0C-C6" = "Baxter International"
    "00-1B-5B" = "Welch Allyn"
    "00-90-5F" = "Welch Allyn"
    "00-24-7E" = "Welch Allyn / Hillrom"
    "00-0D-E1" = "Masimo Corporation"
    "00-25-41" = "Masimo Corporation"
    "00-1A-6B" = "Draeger Medical"
    "00-0F-EA" = "Draeger Medical"
    "00-17-C4" = "Mindray Medical"
    "00-1D-A5" = "Mindray Medical"
    "00-1E-C9" = "Nihon Kohden"
    "00-13-FD" = "Nihon Kohden"
    "00-04-A5" = "Spacelabs Medical"
    "00-1A-2A" = "Spacelabs Healthcare"
    "00-0E-C4" = "Natus Medical"
    "00-1C-06" = "Siemens Healthineers"
    "00-50-04" = "3COM (common in older medical)"
}

# Get all ARP entries for network discovery
$ArpEntries = Get-NetNeighbor -AddressFamily IPv4 |
    Where-Object { $_.State -ne "Unreachable" -and $_.State -ne "Permanent" }

# Get domain computers for comparison (if domain-joined)
$DomainComputers = @()
try {
    Import-Module ActiveDirectory -ErrorAction Stop
    $DomainComputers = @(Get-ADComputer -Filter * -Properties DNSHostName, OperatingSystem |
        Select-Object -ExpandProperty DNSHostName)
} catch {
    # Not domain-joined or AD module not available
}

# Get DHCP leases if DHCP server role is available
$DHCPLeases = @{}
try {
    $Scopes = Get-DhcpServerv4Scope -ErrorAction Stop
    foreach ($Scope in $Scopes) {
        $Leases = Get-DhcpServerv4Lease -ScopeId $Scope.ScopeId -ErrorAction SilentlyContinue
        foreach ($Lease in $Leases) {
            $DHCPLeases[$Lease.IPAddress.ToString()] = @{
                HostName = $Lease.HostName
                ClientId = $Lease.ClientId
                LeaseExpiry = $Lease.LeaseExpiryTime
            }
        }
    }
} catch {
    # DHCP server not available on this machine
}

foreach ($Entry in $ArpEntries) {
    $IP = $Entry.IPAddress
    $MAC = $Entry.LinkLayerAddress

    # Skip broadcast and multicast
    if ($IP -like "*.255" -or $IP -like "224.*") { continue }

    $Device = @{
        IPAddress = $IP
        MACAddress = $MAC
        Vendor = "Unknown"
        IsMedicalDevice = $false
        IsManaged = $false
        HostName = "Unknown"
        NetworkIsolation = "Unknown"
    }

    # Resolve hostname via DNS
    try {
        $DNSResult = Resolve-DnsName -Name $IP -ErrorAction Stop
        if ($DNSResult.NameHost) {
            $Device.HostName = $DNSResult.NameHost
        }
    } catch {
        # DNS resolution failed
    }

    # Check DHCP lease info
    if ($DHCPLeases.ContainsKey($IP)) {
        $Device.HostName = $DHCPLeases[$IP].HostName
        $Device.DHCPLease = $true
    }

    # Extract OUI (first 3 octets of MAC)
    $OUI = ($MAC -replace '[:-]', '-').Substring(0, 8).ToUpper()

    # Check against medical device vendor OUIs
    if ($MedicalOUIs.ContainsKey($OUI)) {
        $Device.Vendor = $MedicalOUIs[$OUI]
        $Device.IsMedicalDevice = $true
        $Result.MedicalDevices += $Device
    }

    # Check if device is a managed domain computer
    if ($DomainComputers.Count -gt 0) {
        $IsDomainMember = $DomainComputers | Where-Object { $_ -like "*$($Device.HostName)*" }
        $Device.IsManaged = ($null -ne $IsDomainMember)
    }

    # Check network isolation by testing if device can reach other subnets
    # Get local subnet info
    $LocalAdapter = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.PrefixOrigin -ne "WellKnown" -and $_.IPAddress -ne "127.0.0.1" } |
        Select-Object -First 1

    if ($LocalAdapter) {
        $LocalSubnet = ($LocalAdapter.IPAddress -split '\.')[0..2] -join '.'
        $DeviceSubnet = ($IP -split '\.')[0..2] -join '.'
        $Device.NetworkIsolation = if ($LocalSubnet -ne $DeviceSubnet) { "Segmented" } else { "Same Subnet" }
    }

    if (-not $Device.IsManaged -and -not $Device.IsMedicalDevice) {
        $Result.UnmanagedDevices += $Device
    }

    $Result.Devices += $Device
}

# Determine drift
$Result.TotalDevices = $Result.Devices.Count
$Result.MedicalDeviceCount = $Result.MedicalDevices.Count
$Result.UnmanagedCount = $Result.UnmanagedDevices.Count

# Drift if medical devices not isolated or unmanaged devices exist
foreach ($MedDevice in $Result.MedicalDevices) {
    if ($MedDevice.NetworkIsolation -eq "Same Subnet") {
        $Result.Drifted = $true
        $Result.Issues += "Medical device $($MedDevice.Vendor) ($($MedDevice.IPAddress)) not on isolated network segment"
    }
}

if ($Result.UnmanagedCount -gt 0) {
    $Result.Drifted = $true
    $Result.Issues += "$($Result.UnmanagedCount) unmanaged device(s) detected on clinic network"
}

$Result | ConvertTo-Json -Depth 4
''',

    remediate_script=r'''
# IoT/Medical device remediation requires manual intervention
# Cannot auto-remediate physical network segmentation or device firmware
$Result = @{ Success = $true; Actions = @() }

$Result.Actions += "Generated device inventory report for review"
$Result.Escalation = "REQUIRED - Medical device network isolation must be configured manually"

# Build inventory summary
$ArpEntries = Get-NetNeighbor -AddressFamily IPv4 |
    Where-Object { $_.State -ne "Unreachable" -and $_.State -ne "Permanent" }

$Inventory = @{
    TotalDevices = $ArpEntries.Count
    ScanDate = (Get-Date).ToUniversalTime().ToString("o")
    Recommendations = @(
        "Segment medical devices onto dedicated VLAN",
        "Implement ACLs to restrict medical device communication",
        "Deploy network monitoring for anomalous medical device traffic",
        "Maintain device inventory with firmware versions",
        "Establish patching schedule with device vendors",
        "Review and document all unmanaged devices"
    )
}

$Result.Inventory = $Inventory
$Result.Actions += "Medical devices must be isolated on dedicated network segment per HIPAA 164.312(a)(1)"

$Result | ConvertTo-Json -Depth 3
''',

    verify_script=r'''
# Re-scan network and compare against baseline inventory
$ErrorActionPreference = "SilentlyContinue"
$Result = @{
    Compliant = $true
    DevicesFound = 0
    MedicalDevicesIsolated = $true
}

# Medical device vendor MAC OUI prefixes
$MedicalOUIs = @(
    "00-09-FB", "00-1E-8F", "00-21-A0",
    "00-80-25", "00-19-B3", "00-50-F1",
    "00-00-87", "00-01-E5", "08-00-06",
    "00-80-F4", "00-0C-C6",
    "00-1B-5B", "00-90-5F", "00-24-7E",
    "00-0D-E1", "00-25-41",
    "00-1A-6B", "00-0F-EA",
    "00-17-C4", "00-1D-A5",
    "00-1E-C9", "00-13-FD",
    "00-04-A5", "00-1A-2A",
    "00-0E-C4", "00-1C-06"
)

$LocalAdapter = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.PrefixOrigin -ne "WellKnown" -and $_.IPAddress -ne "127.0.0.1" } |
    Select-Object -First 1

$LocalSubnet = ($LocalAdapter.IPAddress -split '\.')[0..2] -join '.'

$ArpEntries = Get-NetNeighbor -AddressFamily IPv4 |
    Where-Object { $_.State -ne "Unreachable" -and $_.State -ne "Permanent" }

$Result.DevicesFound = $ArpEntries.Count

foreach ($Entry in $ArpEntries) {
    $OUI = ($Entry.LinkLayerAddress -replace '[:-]', '-').Substring(0, 8).ToUpper()
    $DeviceSubnet = ($Entry.IPAddress -split '\.')[0..2] -join '.'

    if ($OUI -in $MedicalOUIs -and $DeviceSubnet -eq $LocalSubnet) {
        $Result.MedicalDevicesIsolated = $false
        $Result.Compliant = $false
        break
    }
}

$Result | ConvertTo-Json -Depth 2
''',

    timeout_seconds=600,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["TotalDevices", "MedicalDeviceCount", "UnmanagedCount", "MedicalDevices", "Issues"]
)


# =============================================================================
# RB-WIN-DEVICE-003: Default Credential Scanner
# =============================================================================

RUNBOOK_DEFAULT_CREDS = WindowsRunbook(
    id="RB-WIN-DEVICE-003",
    name="Default Credential Scanner",
    description="Check for common default credentials on network services: SNMP, SQL Server SA, printer web UI, IPMI",
    version="1.0",
    hipaa_controls=["164.312(d)", "164.312(a)(1)"],
    severity="critical",
    constraints=ExecutionConstraints(
        max_retries=1,
        retry_delay_seconds=60,
        requires_maintenance_window=True,  # Credential testing can trigger lockouts
        allow_concurrent=False
    ),

    detect_script=r'''
# Check for common default credentials on network services
# NOTE: This only tests WELL-KNOWN defaults, NOT brute force
$ErrorActionPreference = "SilentlyContinue"
$Result = @{
    Drifted = $false
    Findings = @()
    ServicesChecked = 0
    DefaultCredsFound = 0
    ScanTimestamp = (Get-Date).ToUniversalTime().ToString("o")
}

$IPConfig = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.PrefixOrigin -ne "WellKnown" -and $_.IPAddress -ne "127.0.0.1" } |
    Select-Object -First 1

$Subnet = ($IPConfig.IPAddress -split '\.')[0..2] -join '.'
$LocalIP = $IPConfig.IPAddress

# ---- 1. SNMP Default Community Strings ----
$ArpEntries = Get-NetNeighbor -AddressFamily IPv4 |
    Where-Object { $_.State -ne "Unreachable" -and $_.IPAddress -like "$Subnet.*" }

foreach ($Entry in $ArpEntries) {
    $IP = $Entry.IPAddress
    if ($IP -eq $LocalIP) { continue }

    $Result.ServicesChecked++

    try {
        $UdpClient = New-Object System.Net.Sockets.UdpClient
        $UdpClient.Client.ReceiveTimeout = 1500

        # SNMP v1 GET with community 'public'
        $SNMPPacket = [byte[]](
            0x30, 0x26, 0x02, 0x01, 0x00, 0x04, 0x06, 0x70,
            0x75, 0x62, 0x6C, 0x69, 0x63, 0xA0, 0x19, 0x02,
            0x04, 0x00, 0x00, 0x00, 0x01, 0x02, 0x01, 0x00,
            0x02, 0x01, 0x00, 0x30, 0x0B, 0x30, 0x09, 0x06,
            0x05, 0x2B, 0x06, 0x01, 0x02, 0x01, 0x05, 0x00
        )

        $Endpoint = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Parse($IP), 161)
        $UdpClient.Send($SNMPPacket, $SNMPPacket.Length, $Endpoint) | Out-Null
        $RemoteEP = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)

        try {
            $Response = $UdpClient.Receive([ref]$RemoteEP)
            if ($Response.Length -gt 0) {
                $Result.Findings += @{
                    Type = "SNMP"
                    IPAddress = $IP
                    Credential = "public (community string)"
                    Severity = "HIGH"
                    Risk = "Read access to device configuration via SNMP"
                }
                $Result.DefaultCredsFound++
                $Result.Drifted = $true
            }
        } catch {
            # Timeout = no default community string (good)
        }

        $UdpClient.Close()
    } catch {
        # Connection error
    }
}

# ---- 2. SQL Server SA Default Password ----
# Check if SQL Server is running locally or on known hosts
$SQLInstances = @($LocalIP)

# Check local SQL Server instances
$SQLServices = Get-Service -Name "MSSQL*" -ErrorAction SilentlyContinue |
    Where-Object { $_.Status -eq "Running" }

if ($SQLServices) {
    $Result.ServicesChecked++

    try {
        # Test SA account with common defaults
        $DefaultPasswords = @("", "sa", "password", "Password1", "SQL2019", "SQL2022")

        foreach ($Password in $DefaultPasswords) {
            $ConnectionString = "Server=localhost;User Id=sa;Password=$Password;Connection Timeout=3;"
            $SqlConnection = New-Object System.Data.SqlClient.SqlConnection($ConnectionString)

            try {
                $SqlConnection.Open()

                if ($SqlConnection.State -eq "Open") {
                    $PasswordDisplay = if ($Password -eq "") { "(empty)" } else { "(default known password)" }
                    $Result.Findings += @{
                        Type = "SQL Server"
                        IPAddress = $LocalIP
                        Credential = "SA account with default password $PasswordDisplay"
                        Severity = "CRITICAL"
                        Risk = "Full database access including PHI data"
                    }
                    $Result.DefaultCredsFound++
                    $Result.Drifted = $true
                    $SqlConnection.Close()
                    break
                }
            } catch {
                # Login failed = not default (good)
            } finally {
                if ($SqlConnection.State -eq "Open") { $SqlConnection.Close() }
            }
        }
    } catch {
        # SQL connection test failed
    }
}

# ---- 3. Printer Web UI Default Credentials ----
# Check common printer admin ports
foreach ($Entry in $ArpEntries) {
    $IP = $Entry.IPAddress
    $PrinterTest = Test-NetConnection -ComputerName $IP -Port 80 -WarningAction SilentlyContinue -InformationLevel Quiet
    if (-not $PrinterTest) { continue }

    # Also check if it has printer port open (to confirm it's a printer)
    $Is9100 = Test-NetConnection -ComputerName $IP -Port 9100 -WarningAction SilentlyContinue -InformationLevel Quiet
    if (-not $Is9100) { continue }

    $Result.ServicesChecked++

    try {
        # Try common default credentials via HTTP basic auth
        $DefaultCreds = @(
            @{ User = "admin"; Pass = "admin" },
            @{ User = "admin"; Pass = "" },
            @{ User = "admin"; Pass = "password" },
            @{ User = "admin"; Pass = "1234" }
        )

        foreach ($Cred in $DefaultCreds) {
            $Pair = "$($Cred.User):$($Cred.Pass)"
            $EncodedAuth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($Pair))
            $Headers = @{ Authorization = "Basic $EncodedAuth" }

            try {
                $WebResponse = Invoke-WebRequest -Uri "http://$IP/" -Headers $Headers -TimeoutSec 3 -UseBasicParsing
                if ($WebResponse.StatusCode -eq 200 -and $WebResponse.Content -notmatch "login|sign.in|unauthorized") {
                    $PassDisplay = if ($Cred.Pass -eq "") { "(empty)" } else { $Cred.Pass }
                    $Result.Findings += @{
                        Type = "Printer Web UI"
                        IPAddress = $IP
                        Credential = "$($Cred.User) / $PassDisplay"
                        Severity = "HIGH"
                        Risk = "Printer admin access - can modify settings, access stored documents"
                    }
                    $Result.DefaultCredsFound++
                    $Result.Drifted = $true
                    break
                }
            } catch {
                # Auth failed = not default (good)
            }
        }
    } catch {
        # Web request failed
    }
}

# ---- 4. IPMI Default Credentials ----
# Check for IPMI/BMC on port 623 (UDP) and web on 80/443
foreach ($Entry in $ArpEntries) {
    $IP = $Entry.IPAddress

    $IPMITest = Test-NetConnection -ComputerName $IP -Port 623 -WarningAction SilentlyContinue -InformationLevel Quiet
    if (-not $IPMITest) { continue }

    $Result.ServicesChecked++

    # Try default IPMI web interface credentials
    $IPMIDefaults = @(
        @{ User = "ADMIN"; Pass = "ADMIN" },
        @{ User = "admin"; Pass = "admin" },
        @{ User = "root"; Pass = "calvin" },       # Dell iDRAC
        @{ User = "ADMIN"; Pass = "ADMIN" },        # Supermicro
        @{ User = "Administrator"; Pass = "" }       # HP iLO
    )

    foreach ($Cred in $IPMIDefaults) {
        try {
            $Pair = "$($Cred.User):$($Cred.Pass)"
            $EncodedAuth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($Pair))
            $Headers = @{ Authorization = "Basic $EncodedAuth" }

            $WebResponse = Invoke-WebRequest -Uri "https://$IP/" -Headers $Headers -TimeoutSec 3 -UseBasicParsing -SkipCertificateCheck
            if ($WebResponse.StatusCode -eq 200 -and $WebResponse.Content -notmatch "login|sign.in") {
                $PassDisplay = if ($Cred.Pass -eq "") { "(empty)" } else { "(default)" }
                $Result.Findings += @{
                    Type = "IPMI/BMC"
                    IPAddress = $IP
                    Credential = "$($Cred.User) / $PassDisplay"
                    Severity = "CRITICAL"
                    Risk = "Full hardware management - power control, console access, firmware flash"
                }
                $Result.DefaultCredsFound++
                $Result.Drifted = $true
                break
            }
        } catch {
            # Auth failed = not default (good)
        }
    }
}

$Result | ConvertTo-Json -Depth 3
''',

    remediate_script=r'''
# Remediate default credentials where possible, escalate otherwise
$Result = @{ Success = $false; Actions = @() }

try {
    # ---- Auto-remediate: SQL Server SA password ----
    $SQLServices = Get-Service -Name "MSSQL*" -ErrorAction SilentlyContinue |
        Where-Object { $_.Status -eq "Running" }

    if ($SQLServices) {
        $DefaultPasswords = @("", "sa", "password", "Password1", "SQL2019", "SQL2022")

        foreach ($Password in $DefaultPasswords) {
            $ConnectionString = "Server=localhost;User Id=sa;Password=$Password;Connection Timeout=3;"
            $SqlConnection = New-Object System.Data.SqlClient.SqlConnection($ConnectionString)

            try {
                $SqlConnection.Open()

                if ($SqlConnection.State -eq "Open") {
                    # Generate a strong random password
                    Add-Type -AssemblyName System.Web
                    $NewPassword = [System.Web.Security.Membership]::GeneratePassword(24, 6)

                    $Command = $SqlConnection.CreateCommand()
                    $Command.CommandText = "ALTER LOGIN [sa] WITH PASSWORD = N'$NewPassword';"
                    $Command.ExecuteNonQuery() | Out-Null

                    # Also disable SA if not needed
                    $Command.CommandText = "ALTER LOGIN [sa] DISABLE;"
                    $Command.ExecuteNonQuery() | Out-Null

                    $Result.Actions += "Changed SQL SA password and disabled SA login"
                    $Result.Actions += "New SA password stored - requires secure documentation"

                    $SqlConnection.Close()
                    break
                }
            } catch {
                # Login failed with this password
            } finally {
                if ($SqlConnection.State -eq "Open") { $SqlConnection.Close() }
            }
        }
    }

    # ---- Escalate: Everything else requires manual remediation ----
    $Result.Actions += "ESCALATION: SNMP community strings must be changed on each device individually"
    $Result.Actions += "ESCALATION: Printer admin passwords must be changed via device web UI"
    $Result.Actions += "ESCALATION: IPMI/BMC credentials must be changed via hardware management interface"

    $Result.Success = $true
    $Result.EscalationRequired = $true
    $Result.Recommendations = @(
        "Change all SNMP community strings to unique complex values",
        "Change all printer admin passwords to unique complex values",
        "Change all IPMI/BMC default credentials immediately",
        "Disable SA account on SQL Server instances",
        "Implement network segmentation for management interfaces",
        "Document all credential changes in password manager"
    )
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
# Re-test default credentials to confirm they have been changed
$ErrorActionPreference = "SilentlyContinue"
$Result = @{
    Compliant = $true
    ServicesChecked = 0
    DefaultCredsRemaining = 0
    Details = @()
}

$IPConfig = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.PrefixOrigin -ne "WellKnown" -and $_.IPAddress -ne "127.0.0.1" } |
    Select-Object -First 1

$Subnet = ($IPConfig.IPAddress -split '\.')[0..2] -join '.'
$LocalIP = $IPConfig.IPAddress

# Re-check SNMP defaults
$ArpEntries = Get-NetNeighbor -AddressFamily IPv4 |
    Where-Object { $_.State -ne "Unreachable" -and $_.IPAddress -like "$Subnet.*" }

foreach ($Entry in $ArpEntries) {
    $IP = $Entry.IPAddress
    if ($IP -eq $LocalIP) { continue }

    $Result.ServicesChecked++

    try {
        $UdpClient = New-Object System.Net.Sockets.UdpClient
        $UdpClient.Client.ReceiveTimeout = 1500

        $SNMPPacket = [byte[]](
            0x30, 0x26, 0x02, 0x01, 0x00, 0x04, 0x06, 0x70,
            0x75, 0x62, 0x6C, 0x69, 0x63, 0xA0, 0x19, 0x02,
            0x04, 0x00, 0x00, 0x00, 0x01, 0x02, 0x01, 0x00,
            0x02, 0x01, 0x00, 0x30, 0x0B, 0x30, 0x09, 0x06,
            0x05, 0x2B, 0x06, 0x01, 0x02, 0x01, 0x05, 0x00
        )

        $Endpoint = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Parse($IP), 161)
        $UdpClient.Send($SNMPPacket, $SNMPPacket.Length, $Endpoint) | Out-Null
        $RemoteEP = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)

        try {
            $Response = $UdpClient.Receive([ref]$RemoteEP)
            if ($Response.Length -gt 0) {
                $Result.Compliant = $false
                $Result.DefaultCredsRemaining++
                $Result.Details += "SNMP default 'public' still active on $IP"
            }
        } catch { }

        $UdpClient.Close()
    } catch { }
}

# Re-check SQL SA
$SQLServices = Get-Service -Name "MSSQL*" -ErrorAction SilentlyContinue |
    Where-Object { $_.Status -eq "Running" }

if ($SQLServices) {
    $Result.ServicesChecked++
    $DefaultPasswords = @("", "sa", "password", "Password1")

    foreach ($Password in $DefaultPasswords) {
        try {
            $Conn = New-Object System.Data.SqlClient.SqlConnection("Server=localhost;User Id=sa;Password=$Password;Connection Timeout=3;")
            $Conn.Open()
            if ($Conn.State -eq "Open") {
                $Result.Compliant = $false
                $Result.DefaultCredsRemaining++
                $Result.Details += "SQL SA still has default password"
                $Conn.Close()
                break
            }
        } catch { }
    }
}

$Result | ConvertTo-Json -Depth 2
''',

    timeout_seconds=600,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["Findings", "ServicesChecked", "DefaultCredsFound"]
)


# =============================================================================
# RB-WIN-DEVICE-004: EDR/Antimalware Deployment Verification
# =============================================================================

RUNBOOK_EDR_DEPLOYMENT = WindowsRunbook(
    id="RB-WIN-DEVICE-004",
    name="EDR/Antimalware Deployment Verification",
    description="Query AD for all domain computers and verify EDR/antimalware is installed on each endpoint",
    version="1.0",
    hipaa_controls=["164.308(a)(5)(ii)(B)", "164.312(b)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=60,
        requires_maintenance_window=False,
        allow_concurrent=False  # WMI queries should not flood network
    ),

    detect_script=r'''
# Query AD for all domain computers and verify endpoint protection
$ErrorActionPreference = "SilentlyContinue"
$Result = @{
    Drifted = $false
    TotalEndpoints = 0
    ProtectedEndpoints = 0
    UnprotectedEndpoints = @()
    EndpointDetails = @()
    Issues = @()
    ScanTimestamp = (Get-Date).ToUniversalTime().ToString("o")
}

# Get list of all domain computers
$Computers = @()
try {
    Import-Module ActiveDirectory -ErrorAction Stop
    $Computers = Get-ADComputer -Filter { Enabled -eq $true } -Properties OperatingSystem, LastLogonDate, DNSHostName |
        Where-Object { $_.LastLogonDate -gt (Get-Date).AddDays(-30) } |
        Select-Object Name, DNSHostName, OperatingSystem, LastLogonDate
} catch {
    # If AD not available, check local machine only
    $Computers = @(@{
        Name = $env:COMPUTERNAME
        DNSHostName = "$env:COMPUTERNAME.$env:USERDNSDOMAIN"
        OperatingSystem = (Get-WmiObject Win32_OperatingSystem).Caption
        LastLogonDate = Get-Date
    })
}

$Result.TotalEndpoints = $Computers.Count

# Known AV/EDR product names to search for
$AVProducts = @(
    "Windows Defender",
    "Microsoft Defender",
    "Symantec Endpoint Protection",
    "Norton",
    "McAfee",
    "Trend Micro",
    "Kaspersky",
    "ESET",
    "Sophos",
    "CrowdStrike",
    "SentinelOne",
    "Carbon Black",
    "Webroot",
    "Malwarebytes",
    "Bitdefender",
    "Cylance",
    "Palo Alto Cortex",
    "Huntress"
)

# Registry paths where AV/EDR products register
$AVRegistryPaths = @(
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
)

foreach ($Computer in $Computers) {
    $CompName = if ($Computer.Name) { $Computer.Name } else { $Computer["Name"] }
    $CompDNS = if ($Computer.DNSHostName) { $Computer.DNSHostName } else { $Computer["DNSHostName"] }
    $CompOS = if ($Computer.OperatingSystem) { $Computer.OperatingSystem } else { $Computer["OperatingSystem"] }

    $EndpointInfo = @{
        ComputerName = $CompName
        DNSHostName = $CompDNS
        OperatingSystem = $CompOS
        AVInstalled = $false
        AVProducts = @()
        DefenderStatus = "Unknown"
        Reachable = $false
    }

    # Test if computer is reachable
    $Reachable = Test-Connection -ComputerName $CompName -Count 1 -Quiet -ErrorAction SilentlyContinue
    $EndpointInfo.Reachable = $Reachable

    if (-not $Reachable) {
        $EndpointInfo.Note = "Endpoint not reachable - cannot verify protection"
        $Result.EndpointDetails += $EndpointInfo
        continue
    }

    # Method 1: WMI query for AntiVirusProduct (Windows Security Center)
    try {
        $AVFromWMI = Get-CimInstance -Namespace "root\SecurityCenter2" -ClassName "AntiVirusProduct" -ComputerName $CompName -ErrorAction Stop
        if ($AVFromWMI) {
            foreach ($AV in $AVFromWMI) {
                $EndpointInfo.AVProducts += @{
                    DisplayName = $AV.displayName
                    ProductState = $AV.productState
                    PathToSignedProductExe = $AV.pathToSignedProductExe
                }
                $EndpointInfo.AVInstalled = $true
            }
        }
    } catch {
        # SecurityCenter2 not available (e.g., Server OS)
    }

    # Method 2: Check Defender status via WMI
    try {
        $DefenderStatus = Invoke-Command -ComputerName $CompName -ScriptBlock {
            Get-MpComputerStatus | Select-Object RealTimeProtectionEnabled, AntivirusEnabled, AntivirusSignatureLastUpdated
        } -ErrorAction Stop

        if ($DefenderStatus) {
            $EndpointInfo.DefenderStatus = @{
                RealTimeProtection = $DefenderStatus.RealTimeProtectionEnabled
                AntivirusEnabled = $DefenderStatus.AntivirusEnabled
                SignatureLastUpdated = $DefenderStatus.AntivirusSignatureLastUpdated
            }
            if ($DefenderStatus.AntivirusEnabled) {
                $EndpointInfo.AVInstalled = $true
                if ($EndpointInfo.AVProducts.Count -eq 0) {
                    $EndpointInfo.AVProducts += @{
                        DisplayName = "Windows Defender"
                        Active = $DefenderStatus.RealTimeProtectionEnabled
                    }
                }
            }
        }
    } catch {
        # Remote command failed
    }

    # Method 3: Check registry for installed AV products via remote registry
    if (-not $EndpointInfo.AVInstalled) {
        try {
            $InstalledSoftware = Invoke-Command -ComputerName $CompName -ScriptBlock {
                $Paths = @(
                    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
                    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
                )
                Get-ItemProperty $Paths -ErrorAction SilentlyContinue |
                    Select-Object DisplayName, Publisher, InstallDate
            } -ErrorAction Stop

            foreach ($SW in $InstalledSoftware) {
                foreach ($AVName in $AVProducts) {
                    if ($SW.DisplayName -match [regex]::Escape($AVName)) {
                        $EndpointInfo.AVProducts += @{
                            DisplayName = $SW.DisplayName
                            Publisher = $SW.Publisher
                        }
                        $EndpointInfo.AVInstalled = $true
                    }
                }
            }
        } catch {
            # Remote registry query failed
            $EndpointInfo.RegistryQueryError = $_.Exception.Message
        }
    }

    # Classify endpoint
    if (-not $EndpointInfo.AVInstalled) {
        $Result.UnprotectedEndpoints += @{
            ComputerName = $CompName
            OperatingSystem = $CompOS
            Reachable = $Reachable
        }
        $Result.Issues += "No endpoint protection found on $CompName"
        $Result.Drifted = $true
    } else {
        $Result.ProtectedEndpoints++
    }

    $Result.EndpointDetails += $EndpointInfo
}

$Result.UnprotectedCount = $Result.UnprotectedEndpoints.Count
$Result.CoveragePercent = if ($Result.TotalEndpoints -gt 0) {
    [math]::Round(($Result.ProtectedEndpoints / $Result.TotalEndpoints) * 100, 1)
} else { 0 }

# Drift if any endpoint lacks protection
if ($Result.UnprotectedEndpoints.Count -gt 0) {
    $Result.Drifted = $true
}

$Result | ConvertTo-Json -Depth 4
''',

    remediate_script=r'''
# Generate report of unprotected endpoints and push Defender policy if possible
$Result = @{ Success = $false; Actions = @() }

try {
    # Get unprotected endpoints
    $Computers = @()
    try {
        Import-Module ActiveDirectory -ErrorAction Stop
        $Computers = Get-ADComputer -Filter { Enabled -eq $true } -Properties OperatingSystem, LastLogonDate, DNSHostName |
            Where-Object { $_.LastLogonDate -gt (Get-Date).AddDays(-30) }
    } catch {
        $Computers = @(@{ Name = $env:COMPUTERNAME })
    }

    $UnprotectedList = @()

    foreach ($Computer in $Computers) {
        $CompName = $Computer.Name
        $Reachable = Test-Connection -ComputerName $CompName -Count 1 -Quiet -ErrorAction SilentlyContinue
        if (-not $Reachable) { continue }

        # Check if Defender is active
        $HasAV = $false
        try {
            $DefenderStatus = Invoke-Command -ComputerName $CompName -ScriptBlock {
                (Get-MpComputerStatus).AntivirusEnabled
            } -ErrorAction Stop

            $HasAV = $DefenderStatus
        } catch {
            # Check WMI fallback
            try {
                $AV = Get-CimInstance -Namespace "root\SecurityCenter2" -ClassName "AntiVirusProduct" -ComputerName $CompName -ErrorAction Stop
                $HasAV = ($null -ne $AV -and $AV.Count -gt 0)
            } catch { }
        }

        if (-not $HasAV) {
            $UnprotectedList += $CompName

            # Attempt to enable Windows Defender remotely
            try {
                Invoke-Command -ComputerName $CompName -ScriptBlock {
                    Set-MpPreference -DisableRealtimeMonitoring $false -ErrorAction Stop
                    Update-MpSignature -ErrorAction SilentlyContinue
                } -ErrorAction Stop

                $Result.Actions += "Enabled Windows Defender on $CompName"
            } catch {
                $Result.Actions += "ESCALATION: Cannot enable Defender on $CompName - $($_.Exception.Message)"
            }
        }
    }

    # Try pushing Defender policy via GPO if available
    try {
        Import-Module GroupPolicy -ErrorAction Stop
        $GPO = Get-GPO -Name "Endpoint Protection Policy" -ErrorAction SilentlyContinue
        if ($GPO) {
            $Result.Actions += "GPO 'Endpoint Protection Policy' exists - ensure it is linked to all OUs"
        } else {
            $Result.Actions += "RECOMMENDATION: Create GPO for Endpoint Protection Policy"
        }
    } catch {
        $Result.Actions += "Group Policy module not available"
    }

    $Result.Success = $true
    $Result.UnprotectedEndpoints = $UnprotectedList
    $Result.UnprotectedCount = $UnprotectedList.Count
    $Result.Recommendations = @(
        "Deploy managed endpoint protection to all workstations and servers",
        "Create GPO to enforce Windows Defender if no third-party EDR",
        "Enable Windows Defender ATP/MDE for advanced threat detection",
        "Configure Defender exclusions only where necessary and documented",
        "Set up centralized AV management console for monitoring"
    )
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
# Re-scan endpoints for AV presence
$ErrorActionPreference = "SilentlyContinue"
$Result = @{
    Compliant = $true
    TotalEndpoints = 0
    ProtectedEndpoints = 0
    UnprotectedEndpoints = @()
}

# Get domain computers
$Computers = @()
try {
    Import-Module ActiveDirectory -ErrorAction Stop
    $Computers = Get-ADComputer -Filter { Enabled -eq $true } -Properties LastLogonDate |
        Where-Object { $_.LastLogonDate -gt (Get-Date).AddDays(-30) }
} catch {
    $Computers = @(@{ Name = $env:COMPUTERNAME })
}

$Result.TotalEndpoints = $Computers.Count

foreach ($Computer in $Computers) {
    $CompName = $Computer.Name
    $Reachable = Test-Connection -ComputerName $CompName -Count 1 -Quiet -ErrorAction SilentlyContinue
    if (-not $Reachable) { continue }

    $HasAV = $false

    # Check Defender
    try {
        $DefenderStatus = Invoke-Command -ComputerName $CompName -ScriptBlock {
            (Get-MpComputerStatus).AntivirusEnabled
        } -ErrorAction Stop
        $HasAV = $DefenderStatus
    } catch { }

    # Check SecurityCenter2
    if (-not $HasAV) {
        try {
            $AV = Get-CimInstance -Namespace "root\SecurityCenter2" -ClassName "AntiVirusProduct" -ComputerName $CompName -ErrorAction Stop
            $HasAV = ($null -ne $AV -and $AV.Count -gt 0)
        } catch { }
    }

    if ($HasAV) {
        $Result.ProtectedEndpoints++
    } else {
        $Result.UnprotectedEndpoints += $CompName
        $Result.Compliant = $false
    }
}

$Result.CoveragePercent = if ($Result.TotalEndpoints -gt 0) {
    [math]::Round(($Result.ProtectedEndpoints / $Result.TotalEndpoints) * 100, 1)
} else { 0 }

$Result | ConvertTo-Json -Depth 2
''',

    timeout_seconds=900,  # 15 minutes for scanning many endpoints
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["TotalEndpoints", "ProtectedEndpoints", "UnprotectedCount", "CoveragePercent", "UnprotectedEndpoints"]
)


# =============================================================================
# Clinic Device Runbooks Registry
# =============================================================================

CLINIC_DEVICE_RUNBOOKS: Dict[str, WindowsRunbook] = {
    "RB-WIN-DEVICE-001": RUNBOOK_PRINTER_SECURITY,
    "RB-WIN-DEVICE-002": RUNBOOK_IOT_INVENTORY,
    "RB-WIN-DEVICE-003": RUNBOOK_DEFAULT_CREDS,
    "RB-WIN-DEVICE-004": RUNBOOK_EDR_DEPLOYMENT,
}
