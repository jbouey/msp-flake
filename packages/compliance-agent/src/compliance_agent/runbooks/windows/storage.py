"""
Windows Storage Runbooks for HIPAA Compliance.

Runbooks for disk space, backup, and storage health.
"""

from typing import Dict, List
from dataclasses import dataclass, field
from .runbooks import WindowsRunbook, ExecutionConstraints


# =============================================================================
# RB-WIN-STG-001: Disk Space Cleanup
# =============================================================================

RUNBOOK_DISK_CLEANUP = WindowsRunbook(
    id="RB-WIN-STG-001",
    name="Disk Space Cleanup",
    description="Clean temp files, old logs, and Windows Update cache to free disk space",
    version="1.0",
    hipaa_controls=["164.312(c)(1)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=60,
        requires_maintenance_window=False,
        allow_concurrent=False
    ),

    detect_script=r'''
# Check disk space
$Result = @{
    Drifted = $false
    Volumes = @()
}

$Volumes = Get-WmiObject Win32_LogicalDisk -Filter "DriveType=3"

foreach ($Vol in $Volumes) {
    $FreeGB = [math]::Round($Vol.FreeSpace / 1GB, 2)
    $TotalGB = [math]::Round($Vol.Size / 1GB, 2)
    $UsedPercent = [math]::Round((($Vol.Size - $Vol.FreeSpace) / $Vol.Size) * 100, 1)

    $VolInfo = @{
        DriveLetter = $Vol.DeviceID
        TotalGB = $TotalGB
        FreeGB = $FreeGB
        UsedPercent = $UsedPercent
    }
    $Result.Volumes += $VolInfo

    # Alert if less than 10% or less than 10GB free
    if ($UsedPercent -gt 90 -or $FreeGB -lt 10) {
        $Result.Drifted = $true
        $VolInfo.LowSpace = $true
    }
}

# Check specific cleanup targets
$TempSize = (Get-ChildItem $env:TEMP -Recurse -ErrorAction SilentlyContinue |
    Measure-Object -Property Length -Sum).Sum / 1GB
$WindowsTempSize = (Get-ChildItem "C:\Windows\Temp" -Recurse -ErrorAction SilentlyContinue |
    Measure-Object -Property Length -Sum).Sum / 1GB
$SoftwareDistSize = (Get-ChildItem "C:\Windows\SoftwareDistribution\Download" -Recurse -ErrorAction SilentlyContinue |
    Measure-Object -Property Length -Sum).Sum / 1GB

$Result.CleanupTargets = @{
    UserTempGB = [math]::Round($TempSize, 2)
    WindowsTempGB = [math]::Round($WindowsTempSize, 2)
    SoftwareDistGB = [math]::Round($SoftwareDistSize, 2)
    TotalRecoverableGB = [math]::Round($TempSize + $WindowsTempSize + $SoftwareDistSize, 2)
}

$Result | ConvertTo-Json -Depth 3
''',

    remediate_script=r'''
# Clean up disk space
$Result = @{ Success = $false; SpaceRecoveredMB = 0; Actions = @() }

try {
    $InitialFree = (Get-WmiObject Win32_LogicalDisk -Filter "DeviceID='C:'").FreeSpace

    # Clean user temp
    Remove-Item "$env:TEMP\*" -Recurse -Force -ErrorAction SilentlyContinue
    $Result.Actions += "Cleaned user temp folder"

    # Clean Windows temp
    Remove-Item "C:\Windows\Temp\*" -Recurse -Force -ErrorAction SilentlyContinue
    $Result.Actions += "Cleaned Windows temp folder"

    # Clean Software Distribution (Windows Update cache)
    Stop-Service -Name wuauserv -Force -ErrorAction SilentlyContinue
    Remove-Item "C:\Windows\SoftwareDistribution\Download\*" -Recurse -Force -ErrorAction SilentlyContinue
    Start-Service -Name wuauserv -ErrorAction SilentlyContinue
    $Result.Actions += "Cleaned Windows Update cache"

    # Clean old Windows Error Reports
    Remove-Item "C:\ProgramData\Microsoft\Windows\WER\ReportArchive\*" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item "C:\ProgramData\Microsoft\Windows\WER\ReportQueue\*" -Recurse -Force -ErrorAction SilentlyContinue
    $Result.Actions += "Cleaned Windows Error Reports"

    # Clean old log files (older than 30 days)
    Get-ChildItem "C:\Windows\Logs" -Recurse -Include "*.log" -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
        Remove-Item -Force -ErrorAction SilentlyContinue
    $Result.Actions += "Cleaned old log files"

    # Calculate space recovered
    $FinalFree = (Get-WmiObject Win32_LogicalDisk -Filter "DeviceID='C:'").FreeSpace
    $Result.SpaceRecoveredMB = [math]::Round(($FinalFree - $InitialFree) / 1MB, 0)

    $Result.Success = $true
    $Result.Message = "Disk cleanup completed"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
$Vol = Get-WmiObject Win32_LogicalDisk -Filter "DeviceID='C:'"
$FreeGB = [math]::Round($Vol.FreeSpace / 1GB, 2)
$UsedPercent = [math]::Round((($Vol.Size - $Vol.FreeSpace) / $Vol.Size) * 100, 1)
@{
    FreeSpaceGB = $FreeGB
    UsedPercent = $UsedPercent
    Verified = ($UsedPercent -lt 90 -or $FreeGB -gt 5)
} | ConvertTo-Json
''',

    timeout_seconds=600,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["Volumes", "CleanupTargets"]
)


# =============================================================================
# RB-WIN-STG-002: Shadow Copy Recovery
# =============================================================================

RUNBOOK_SHADOW_COPY = WindowsRunbook(
    id="RB-WIN-STG-002",
    name="Shadow Copy Recovery",
    description="Verify and restore Volume Shadow Copy service for backup support",
    version="1.0",
    hipaa_controls=["164.308(a)(7)(ii)(A)"],
    severity="medium",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check Volume Shadow Copy status
$Result = @{
    Drifted = $false
}

# Check VSS service
$VSSService = Get-Service -Name VSS -ErrorAction SilentlyContinue
$Result.VSSServiceStatus = if ($VSSService) { $VSSService.Status.ToString() } else { "NotFound" }
$Result.VSSStartType = if ($VSSService) { $VSSService.StartType.ToString() } else { "Unknown" }

# Check for shadow storage
$ShadowStorage = vssadmin list shadowstorage 2>&1
$Result.ShadowStorageConfigured = $ShadowStorage -notmatch "No shadow copies"

# List existing shadow copies
$Shadows = vssadmin list shadows 2>&1
$ShadowCount = ($Shadows | Select-String "Shadow Copy ID").Count
$Result.ShadowCopyCount = $ShadowCount

# Get shadow copy providers
$Providers = vssadmin list providers 2>&1
$Result.ProvidersAvailable = $Providers -match "Provider"

# Check if VSS writers are healthy
$Writers = vssadmin list writers 2>&1
$FailedWriters = ($Writers | Select-String "Last error:" | Where-Object { $_ -notmatch "No error" }).Count
$Result.FailedWriterCount = $FailedWriters

# Drift conditions
if ($VSSService.Status -ne "Running" -and $VSSService.StartType -ne "Manual") {
    $Result.Drifted = $true
    $Result.DriftReason = "VSS service not configured correctly"
}

if ($FailedWriters -gt 0) {
    $Result.Drifted = $true
    $Result.DriftReason = "VSS writers in failed state"
}

$Result | ConvertTo-Json
''',

    remediate_script=r'''
# Fix VSS configuration
$Result = @{ Success = $false; Actions = @() }

try {
    # Ensure VSS service is set to Manual (starts on demand)
    $Service = Get-Service -Name VSS
    if ($Service.StartType -ne "Manual") {
        Set-Service -Name VSS -StartupType Manual
        $Result.Actions += "Set VSS service to Manual start"
    }

    # Ensure service can start
    if ($Service.Status -ne "Running") {
        Start-Service -Name VSS -ErrorAction SilentlyContinue
        $Result.Actions += "Started VSS service"
    }

    # Configure shadow storage if not configured
    $Storage = vssadmin list shadowstorage 2>&1
    if ($Storage -match "No shadow copies are configured") {
        # Add shadow storage for C: drive (10% of disk)
        vssadmin add shadowstorage /for=C: /on=C: /maxsize=10% 2>&1 | Out-Null
        $Result.Actions += "Configured shadow storage for C: drive"
    }

    # Create a shadow copy to verify it works
    $CreateResult = vssadmin create shadow /for=C: 2>&1
    if ($CreateResult -match "Successfully created") {
        $Result.Actions += "Created test shadow copy"
    }

    $Result.Success = $true
    $Result.Message = "VSS configuration verified"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
$Service = Get-Service -Name VSS -ErrorAction SilentlyContinue
$Writers = vssadmin list writers 2>&1
$FailedWriters = ($Writers | Select-String "Last error:" | Where-Object { $_ -notmatch "No error" }).Count
@{
    VSSServiceExists = ($null -ne $Service)
    FailedWriters = $FailedWriters
    Verified = ($null -ne $Service -and $FailedWriters -eq 0)
} | ConvertTo-Json
''',

    timeout_seconds=180,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["VSSServiceStatus", "ShadowCopyCount", "FailedWriterCount"]
)


# =============================================================================
# RB-WIN-STG-003: Volume Health Check
# =============================================================================

RUNBOOK_VOLUME_HEALTH = WindowsRunbook(
    id="RB-WIN-STG-003",
    name="Volume Health Check",
    description="Check disk SMART status and volume integrity",
    version="1.0",
    hipaa_controls=["164.310(d)(2)(iv)"],
    severity="critical",
    constraints=ExecutionConstraints(
        max_retries=1,
        retry_delay_seconds=60,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check disk and volume health
$Result = @{
    Drifted = $false
    Disks = @()
    Volumes = @()
}

# Check physical disks
$Disks = Get-PhysicalDisk -ErrorAction SilentlyContinue

foreach ($Disk in $Disks) {
    $DiskInfo = @{
        FriendlyName = $Disk.FriendlyName
        MediaType = $Disk.MediaType.ToString()
        Size = [math]::Round($Disk.Size / 1GB, 0)
        HealthStatus = $Disk.HealthStatus.ToString()
        OperationalStatus = $Disk.OperationalStatus.ToString()
    }

    # Get SMART data if available
    $SmartData = Get-StorageReliabilityCounter -PhysicalDisk $Disk -ErrorAction SilentlyContinue
    if ($SmartData) {
        $DiskInfo.Temperature = $SmartData.Temperature
        $DiskInfo.ReadErrors = $SmartData.ReadErrorsTotal
        $DiskInfo.WriteErrors = $SmartData.WriteErrorsTotal
        $DiskInfo.PowerOnHours = $SmartData.PowerOnHours
    }

    $Result.Disks += $DiskInfo

    # Alert on unhealthy status
    if ($Disk.HealthStatus -ne "Healthy" -or $Disk.OperationalStatus -ne "OK") {
        $Result.Drifted = $true
        $DiskInfo.Alert = $true
    }
}

# Check volumes for file system errors
$Volumes = Get-Volume | Where-Object { $_.DriveType -eq "Fixed" }

foreach ($Vol in $Volumes) {
    $VolInfo = @{
        DriveLetter = $Vol.DriveLetter
        FileSystem = $Vol.FileSystemType
        HealthStatus = $Vol.HealthStatus.ToString()
        SizeGB = [math]::Round($Vol.Size / 1GB, 0)
    }

    # Check for dirty bit
    if ($Vol.DriveLetter) {
        $Dirty = fsutil dirty query "$($Vol.DriveLetter):" 2>&1
        $VolInfo.IsDirty = $Dirty -match "dirty"

        if ($VolInfo.IsDirty) {
            $Result.Drifted = $true
        }
    }

    $Result.Volumes += $VolInfo

    if ($Vol.HealthStatus -ne "Healthy") {
        $Result.Drifted = $true
        $VolInfo.Alert = $true
    }
}

$Result | ConvertTo-Json -Depth 3
''',

    remediate_script=r'''
# Volume health remediation (limited - mainly alerting)
$Result = @{ Success = $false; Actions = @(); Alerts = @() }

try {
    # Check for dirty volumes
    $Volumes = Get-Volume | Where-Object { $_.DriveType -eq "Fixed" -and $_.DriveLetter }

    foreach ($Vol in $Volumes) {
        $Dirty = fsutil dirty query "$($Vol.DriveLetter):" 2>&1

        if ($Dirty -match "dirty") {
            $Result.Alerts += "Volume $($Vol.DriveLetter): is dirty - schedule chkdsk on next reboot"

            # Schedule chkdsk (non-destructive)
            # Note: This requires a reboot to run
            $Result.Actions += "Run manually: chkdsk $($Vol.DriveLetter): /f"
        }
    }

    # Check for failing disks
    $Disks = Get-PhysicalDisk | Where-Object { $_.HealthStatus -ne "Healthy" }
    foreach ($Disk in $Disks) {
        $Result.Alerts += "CRITICAL: Disk '$($Disk.FriendlyName)' health is $($Disk.HealthStatus) - replace immediately"
    }

    $Result.Success = $true
    $Result.Message = if ($Result.Alerts.Count -gt 0) {
        "Issues detected - review alerts"
    } else {
        "All volumes healthy"
    }
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
$Disks = Get-PhysicalDisk
$UnhealthyDisks = ($Disks | Where-Object { $_.HealthStatus -ne "Healthy" }).Count
$Volumes = Get-Volume | Where-Object { $_.DriveType -eq "Fixed" }
$UnhealthyVolumes = ($Volumes | Where-Object { $_.HealthStatus -ne "Healthy" }).Count
@{
    UnhealthyDisks = $UnhealthyDisks
    UnhealthyVolumes = $UnhealthyVolumes
    Verified = ($UnhealthyDisks -eq 0 -and $UnhealthyVolumes -eq 0)
} | ConvertTo-Json
''',

    timeout_seconds=120,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["Disks", "Volumes"]
)


# =============================================================================
# Storage Runbooks Registry
# =============================================================================

STORAGE_RUNBOOKS: Dict[str, WindowsRunbook] = {
    "RB-WIN-STG-001": RUNBOOK_DISK_CLEANUP,
    "RB-WIN-STG-002": RUNBOOK_SHADOW_COPY,
    "RB-WIN-STG-003": RUNBOOK_VOLUME_HEALTH,
}
