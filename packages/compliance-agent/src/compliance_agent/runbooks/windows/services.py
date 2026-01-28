"""
Windows Service Runbooks for HIPAA Compliance.

Runbooks for monitoring and recovering critical Windows services.
"""

from typing import Dict, List
from dataclasses import dataclass, field
from .runbooks import WindowsRunbook, ExecutionConstraints


# =============================================================================
# RB-WIN-SVC-001: DNS Server Service Recovery
# =============================================================================

RUNBOOK_DNS_SERVICE = WindowsRunbook(
    id="RB-WIN-SVC-001",
    name="DNS Server Service Recovery",
    description="Monitor and restart Windows DNS Server service if stopped",
    version="1.0",
    hipaa_controls=["164.312(b)"],
    severity="critical",
    constraints=ExecutionConstraints(
        max_retries=3,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check DNS Server service status
$ServiceName = "DNS"
$Result = @{
    ServiceName = $ServiceName
    Drifted = $false
}

try {
    $Service = Get-Service -Name $ServiceName -ErrorAction Stop
    $Result.Status = $Service.Status.ToString()
    $Result.StartType = $Service.StartType.ToString()
    $Result.Drifted = ($Service.Status -ne "Running")

    # Check if it's a DNS server role
    $DNSFeature = Get-WindowsFeature -Name DNS -ErrorAction SilentlyContinue
    $Result.DNSRoleInstalled = ($DNSFeature -and $DNSFeature.Installed)

    if (-not $Result.DNSRoleInstalled) {
        $Result.Drifted = $false
        $Result.Note = "DNS role not installed - skipping"
    }
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Drifted = $false
    $Result.Note = "DNS service not found"
}

$Result | ConvertTo-Json
''',

    remediate_script=r'''
# Restart DNS Server service
$ServiceName = "DNS"
$Result = @{ Success = $false }

try {
    $Service = Get-Service -Name $ServiceName -ErrorAction Stop

    if ($Service.Status -ne "Running") {
        Start-Service -Name $ServiceName -ErrorAction Stop
        Start-Sleep -Seconds 5

        $Service = Get-Service -Name $ServiceName
        $Result.Success = ($Service.Status -eq "Running")
        $Result.Status = $Service.Status.ToString()
        $Result.Message = "DNS service started successfully"
    } else {
        $Result.Success = $true
        $Result.Message = "DNS service already running"
    }
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
# Verify DNS service is running
$Service = Get-Service -Name "DNS" -ErrorAction SilentlyContinue
@{
    Status = if ($Service) { $Service.Status.ToString() } else { "NotFound" }
    Verified = ($Service -and $Service.Status -eq "Running")
} | ConvertTo-Json
''',

    timeout_seconds=120,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["Status", "StartType", "DNSRoleInstalled"]
)


# =============================================================================
# RB-WIN-SVC-002: DHCP Server Service Recovery
# =============================================================================

RUNBOOK_DHCP_SERVICE = WindowsRunbook(
    id="RB-WIN-SVC-002",
    name="DHCP Server Service Recovery",
    description="Monitor and restart Windows DHCP Server service if stopped",
    version="1.0",
    hipaa_controls=["164.312(b)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=3,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check DHCP Server service status
$ServiceName = "DHCPServer"
$Result = @{
    ServiceName = $ServiceName
    Drifted = $false
}

try {
    $Service = Get-Service -Name $ServiceName -ErrorAction Stop
    $Result.Status = $Service.Status.ToString()
    $Result.StartType = $Service.StartType.ToString()
    $Result.Drifted = ($Service.Status -ne "Running")

    # Check if DHCP role is installed
    $DHCPFeature = Get-WindowsFeature -Name DHCP -ErrorAction SilentlyContinue
    $Result.DHCPRoleInstalled = ($DHCPFeature -and $DHCPFeature.Installed)

    if (-not $Result.DHCPRoleInstalled) {
        $Result.Drifted = $false
        $Result.Note = "DHCP role not installed - skipping"
    }

    # Get scope info if running
    if ($Service.Status -eq "Running") {
        $Scopes = Get-DhcpServerv4Scope -ErrorAction SilentlyContinue
        $Result.ScopeCount = @($Scopes).Count
    }
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Drifted = $false
    $Result.Note = "DHCP service not found"
}

$Result | ConvertTo-Json
''',

    remediate_script=r'''
# Restart DHCP Server service
$ServiceName = "DHCPServer"
$Result = @{ Success = $false }

try {
    $Service = Get-Service -Name $ServiceName -ErrorAction Stop

    if ($Service.Status -ne "Running") {
        Start-Service -Name $ServiceName -ErrorAction Stop
        Start-Sleep -Seconds 5

        $Service = Get-Service -Name $ServiceName
        $Result.Success = ($Service.Status -eq "Running")
        $Result.Status = $Service.Status.ToString()
        $Result.Message = "DHCP service started successfully"
    } else {
        $Result.Success = $true
        $Result.Message = "DHCP service already running"
    }
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
# Verify DHCP service is running
$Service = Get-Service -Name "DHCPServer" -ErrorAction SilentlyContinue
@{
    Status = if ($Service) { $Service.Status.ToString() } else { "NotFound" }
    Verified = ($Service -and $Service.Status -eq "Running")
} | ConvertTo-Json
''',

    timeout_seconds=120,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["Status", "StartType", "DHCPRoleInstalled", "ScopeCount"]
)


# =============================================================================
# RB-WIN-SVC-003: Print Spooler Service Recovery
# =============================================================================

RUNBOOK_PRINT_SPOOLER = WindowsRunbook(
    id="RB-WIN-SVC-003",
    name="Print Spooler Service Recovery",
    description="Monitor Print Spooler service - restart only if needed and secure",
    version="1.0",
    hipaa_controls=["164.308(a)(5)(ii)(B)"],
    severity="medium",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check Print Spooler status (security-conscious)
$ServiceName = "Spooler"
$Result = @{
    ServiceName = $ServiceName
    Drifted = $false
}

try {
    $Service = Get-Service -Name $ServiceName -ErrorAction Stop
    $Result.Status = $Service.Status.ToString()
    $Result.StartType = $Service.StartType.ToString()

    # Check if this is a print server or DC
    $IsDC = (Get-WmiObject Win32_ComputerSystem).DomainRole -ge 4
    $HasPrintRole = (Get-WindowsFeature -Name Print-Services -ErrorAction SilentlyContinue).Installed

    $Result.IsDomainController = $IsDC
    $Result.PrintServerRole = $HasPrintRole

    # Print Spooler should be DISABLED on DCs (security)
    if ($IsDC -and $Service.Status -eq "Running") {
        $Result.SecurityWarning = "Print Spooler running on DC - security risk"
        $Result.Drifted = $true
        $Result.RecommendedAction = "Disable"
    } elseif ($HasPrintRole -and $Service.Status -ne "Running") {
        # Print server needs spooler running
        $Result.Drifted = $true
        $Result.RecommendedAction = "Start"
    }
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    remediate_script=r'''
# Handle Print Spooler based on role
$ServiceName = "Spooler"
$Result = @{ Success = $false }

try {
    $IsDC = (Get-WmiObject Win32_ComputerSystem).DomainRole -ge 4
    $Service = Get-Service -Name $ServiceName -ErrorAction Stop

    if ($IsDC) {
        # Stop and disable on DCs for security
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        Set-Service -Name $ServiceName -StartupType Disabled
        $Result.Success = $true
        $Result.Action = "Disabled on DC for security"
    } else {
        # Start on non-DC if stopped
        if ($Service.Status -ne "Running") {
            Start-Service -Name $ServiceName -ErrorAction Stop
            Start-Sleep -Seconds 3
        }
        $Service = Get-Service -Name $ServiceName
        $Result.Success = ($Service.Status -eq "Running")
        $Result.Action = "Started"
    }

    $Result.Status = (Get-Service -Name $ServiceName).Status.ToString()
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
$IsDC = (Get-WmiObject Win32_ComputerSystem).DomainRole -ge 4
$Service = Get-Service -Name "Spooler" -ErrorAction SilentlyContinue
$Expected = if ($IsDC) { "Stopped" } else { "Running" }
$ActualStatus = if ($Service) { $Service.Status.ToString() } else { "NotFound" }
@{
    Status = $ActualStatus
    Expected = $Expected
    Verified = ($ActualStatus -eq $Expected)
} | ConvertTo-Json
''',

    timeout_seconds=60,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["Status", "IsDomainController", "PrintServerRole", "SecurityWarning"]
)


# =============================================================================
# RB-WIN-SVC-004: Windows Time Service Recovery
# =============================================================================

RUNBOOK_TIME_SERVICE = WindowsRunbook(
    id="RB-WIN-SVC-004",
    name="Windows Time Service Recovery",
    description="Ensure W32Time service is running for proper NTP synchronization",
    version="1.0",
    hipaa_controls=["164.312(b)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=3,
        retry_delay_seconds=15,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check Windows Time service and sync status
$ServiceName = "W32Time"
$Result = @{
    ServiceName = $ServiceName
    Drifted = $false
}

try {
    $Service = Get-Service -Name $ServiceName -ErrorAction Stop
    $Result.Status = $Service.Status.ToString()
    $Result.StartType = $Service.StartType.ToString()

    # Check time sync status
    $TimeStatus = w32tm /query /status 2>&1
    if ($LASTEXITCODE -eq 0) {
        $Result.TimeSource = ($TimeStatus | Select-String "Source:").ToString().Split(":")[1].Trim()
        $Result.LastSync = ($TimeStatus | Select-String "Last Successful Sync Time:").ToString().Split(":",2)[1].Trim()
        $Result.Stratum = [int]($TimeStatus | Select-String "Stratum:").ToString().Split(":")[1].Trim()
    }

    # Drift detection
    if ($Service.Status -ne "Running") {
        $Result.Drifted = $true
        $Result.DriftReason = "Service not running"
    } elseif ($Result.Stratum -gt 10) {
        $Result.Drifted = $true
        $Result.DriftReason = "High stratum - poor time source"
    }
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Drifted = $true
}

$Result | ConvertTo-Json
''',

    remediate_script=r'''
# Fix Windows Time service
$Result = @{ Success = $false }

try {
    # Ensure service is running
    $Service = Get-Service -Name "W32Time"
    if ($Service.Status -ne "Running") {
        Set-Service -Name "W32Time" -StartupType Automatic
        Start-Service -Name "W32Time" -ErrorAction Stop
        Start-Sleep -Seconds 3
    }

    # Force resync
    w32tm /resync /force | Out-Null

    # Check if domain-joined
    $Domain = (Get-WmiObject Win32_ComputerSystem).Domain
    $IsDomainJoined = -not [string]::IsNullOrEmpty($Domain) -and $Domain -ne "WORKGROUP"

    if (-not $IsDomainJoined) {
        # Configure NTP for standalone
        w32tm /config /manualpeerlist:"time.nist.gov,0x1 time.windows.com,0x1" /syncfromflags:manual /reliable:YES /update
        Restart-Service -Name "W32Time"
        w32tm /resync
    }

    $Service = Get-Service -Name "W32Time"
    $Result.Success = ($Service.Status -eq "Running")
    $Result.Status = $Service.Status.ToString()
    $Result.Message = "Time service configured and synced"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
$Service = Get-Service -Name "W32Time" -ErrorAction SilentlyContinue
$TimeStatus = w32tm /query /status 2>&1
$Stratum = 99
if ($LASTEXITCODE -eq 0) {
    $Stratum = [int]($TimeStatus | Select-String "Stratum:").ToString().Split(":")[1].Trim()
}
@{
    Status = if ($Service) { $Service.Status.ToString() } else { "NotFound" }
    Stratum = $Stratum
    Verified = ($Service.Status -eq "Running" -and $Stratum -lt 10)
} | ConvertTo-Json
''',

    timeout_seconds=120,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["Status", "TimeSource", "LastSync", "Stratum"]
)


# =============================================================================
# Service Runbooks Registry
# =============================================================================

SERVICE_RUNBOOKS: Dict[str, WindowsRunbook] = {
    "RB-WIN-SVC-001": RUNBOOK_DNS_SERVICE,
    "RB-WIN-SVC-002": RUNBOOK_DHCP_SERVICE,
    "RB-WIN-SVC-003": RUNBOOK_PRINT_SPOOLER,
    "RB-WIN-SVC-004": RUNBOOK_TIME_SERVICE,
}
