"""
Windows Update Runbooks for HIPAA Compliance.

Runbooks for Windows Update and WSUS management.
"""

from typing import Dict, List
from dataclasses import dataclass, field
from .runbooks import WindowsRunbook, ExecutionConstraints


# =============================================================================
# RB-WIN-UPD-001: Windows Update Service Reset
# =============================================================================

RUNBOOK_WUAUSERV_RESET = WindowsRunbook(
    id="RB-WIN-UPD-001",
    name="Windows Update Service Reset",
    description="Reset Windows Update components and clear cache when updates fail",
    version="1.0",
    hipaa_controls=["164.308(a)(5)(ii)(B)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=60,
        requires_maintenance_window=False,
        allow_concurrent=False
    ),

    detect_script=r'''
# Check Windows Update service health
$Result = @{
    Drifted = $false
}

# Check Windows Update service
$WUService = Get-Service -Name wuauserv -ErrorAction SilentlyContinue
$Result.WUServiceStatus = if ($WUService) { $WUService.Status.ToString() } else { "NotFound" }
$Result.WUServiceStartType = if ($WUService) { $WUService.StartType.ToString() } else { "Unknown" }

# Check BITS service
$BITSService = Get-Service -Name BITS -ErrorAction SilentlyContinue
$Result.BITSServiceStatus = if ($BITSService) { $BITSService.Status.ToString() } else { "NotFound" }

# Check Cryptographic Services
$CryptService = Get-Service -Name CryptSvc -ErrorAction SilentlyContinue
$Result.CryptServiceStatus = if ($CryptService) { $CryptService.Status.ToString() } else { "NotFound" }

# Check Windows Update log for errors
$WULog = Get-WindowsUpdateLog -ErrorAction SilentlyContinue
$RecentErrors = Get-WinEvent -LogName "Microsoft-Windows-WindowsUpdateClient/Operational" -MaxEvents 50 -ErrorAction SilentlyContinue |
    Where-Object { $_.LevelDisplayName -eq "Error" -and $_.TimeCreated -gt (Get-Date).AddDays(-7) }
$Result.RecentErrorCount = @($RecentErrors).Count

# Check SoftwareDistribution folder size (large = potential issue)
$SDSize = (Get-ChildItem "C:\Windows\SoftwareDistribution" -Recurse -ErrorAction SilentlyContinue |
    Measure-Object -Property Length -Sum).Sum / 1GB
$Result.SoftwareDistributionSizeGB = [math]::Round($SDSize, 2)

# Check last update check time
$AutoUpdate = (New-Object -ComObject Microsoft.Update.AutoUpdate)
$Result.LastSearchSuccess = $AutoUpdate.Results.LastSearchSuccessDate
$Result.LastInstallSuccess = $AutoUpdate.Results.LastInstallationSuccessDate

# Drift conditions
if ($WUService.Status -eq "Stopped" -and $WUService.StartType -ne "Disabled") {
    $Result.Drifted = $true
    $Result.DriftReason = "Windows Update service stopped unexpectedly"
}

if ($Result.RecentErrorCount -gt 5) {
    $Result.Drifted = $true
    $Result.DriftReason = "Multiple recent Windows Update errors"
}

if ($SDSize -gt 5) {
    $Result.Drifted = $true
    $Result.DriftReason = "Large SoftwareDistribution folder may indicate stuck updates"
}

$Result | ConvertTo-Json
''',

    remediate_script=r'''
# Reset Windows Update components
$Result = @{ Success = $false; Actions = @() }

try {
    # Stop services
    $Services = @("wuauserv", "BITS", "CryptSvc", "msiserver")
    foreach ($Svc in $Services) {
        Stop-Service -Name $Svc -Force -ErrorAction SilentlyContinue
    }
    $Result.Actions += "Stopped Windows Update services"
    Start-Sleep -Seconds 3

    # Rename SoftwareDistribution folder
    if (Test-Path "C:\Windows\SoftwareDistribution") {
        $BackupName = "SoftwareDistribution.old_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
        Rename-Item "C:\Windows\SoftwareDistribution" $BackupName -ErrorAction Stop
        $Result.Actions += "Renamed SoftwareDistribution folder"
    }

    # Rename catroot2 folder
    if (Test-Path "C:\Windows\System32\catroot2") {
        $BackupName = "catroot2.old_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
        Rename-Item "C:\Windows\System32\catroot2" $BackupName -ErrorAction SilentlyContinue
        $Result.Actions += "Renamed catroot2 folder"
    }

    # Re-register BITS and Windows Update DLLs
    $DLLs = @(
        "atl.dll", "urlmon.dll", "mshtml.dll", "shdocvw.dll",
        "browseui.dll", "jscript.dll", "vbscript.dll", "scrrun.dll",
        "msxml.dll", "msxml3.dll", "msxml6.dll", "actxprxy.dll",
        "softpub.dll", "wintrust.dll", "dssenh.dll", "rsaenh.dll",
        "gpkcsp.dll", "sccbase.dll", "slbcsp.dll", "cryptdlg.dll",
        "oleaut32.dll", "ole32.dll", "shell32.dll", "wuaueng.dll",
        "wuapi.dll", "wups.dll", "wups2.dll", "wuwebv.dll", "qmgr.dll"
    )

    foreach ($DLL in $DLLs) {
        regsvr32 /s $DLL 2>&1 | Out-Null
    }
    $Result.Actions += "Re-registered Windows Update DLLs"

    # Reset Winsock
    netsh winsock reset 2>&1 | Out-Null
    $Result.Actions += "Reset Winsock"

    # Reset WinHTTP proxy
    netsh winhttp reset proxy 2>&1 | Out-Null
    $Result.Actions += "Reset WinHTTP proxy"

    # Start services
    foreach ($Svc in @("BITS", "CryptSvc", "wuauserv", "msiserver")) {
        Start-Service -Name $Svc -ErrorAction SilentlyContinue
    }
    $Result.Actions += "Started Windows Update services"

    # Force detection
    $AutoUpdate = (New-Object -ComObject Microsoft.Update.AutoUpdate)
    $AutoUpdate.DetectNow()
    $Result.Actions += "Triggered update detection"

    $Result.Success = $true
    $Result.Message = "Windows Update components reset successfully"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
$WUService = Get-Service -Name wuauserv
$BITSService = Get-Service -Name BITS
@{
    WUServiceRunning = ($WUService.Status -eq "Running")
    BITSServiceRunning = ($BITSService.Status -eq "Running")
    Verified = ($WUService.Status -eq "Running" -or $WUService.StartType -eq "Manual")
} | ConvertTo-Json
''',

    timeout_seconds=300,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["WUServiceStatus", "RecentErrorCount", "SoftwareDistributionSizeGB"]
)


# =============================================================================
# RB-WIN-UPD-002: WSUS Client Registration Fix
# =============================================================================

RUNBOOK_WSUS_CLIENT = WindowsRunbook(
    id="RB-WIN-UPD-002",
    name="WSUS Client Registration Fix",
    description="Re-register with WSUS server if synchronization is broken",
    version="1.0",
    hipaa_controls=["164.308(a)(5)(ii)(B)"],
    severity="medium",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=60,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check WSUS client configuration
$Result = @{
    Drifted = $false
}

# Check if WSUS is configured
$WUServer = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" -ErrorAction SilentlyContinue).WUServer
$WUStatusServer = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" -ErrorAction SilentlyContinue).WUStatusServer
$UseWUServer = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" -ErrorAction SilentlyContinue).UseWUServer

$Result.WSUSConfigured = ($null -ne $WUServer)
$Result.WUServer = $WUServer
$Result.WUStatusServer = $WUStatusServer
$Result.UseWUServer = $UseWUServer

if ($WUServer) {
    # Test WSUS connectivity
    $WSUSUri = [System.Uri]$WUServer
    $TestConnection = Test-NetConnection -ComputerName $WSUSUri.Host -Port $WSUSUri.Port -WarningAction SilentlyContinue
    $Result.WSUSReachable = $TestConnection.TcpTestSucceeded

    if (-not $TestConnection.TcpTestSucceeded) {
        $Result.Drifted = $true
        $Result.DriftReason = "Cannot connect to WSUS server"
    }

    # Check SusClientId
    $SusClientId = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate" -ErrorAction SilentlyContinue).SusClientId
    $Result.SusClientId = $SusClientId

    if (-not $SusClientId) {
        $Result.Drifted = $true
        $Result.DriftReason = "Missing WSUS client ID"
    }

    # Check last report time
    $LastReportTime = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\Results\Detect" -ErrorAction SilentlyContinue).LastSuccessTime
    if ($LastReportTime) {
        $LastReport = [DateTime]::ParseExact($LastReportTime, "yyyy-MM-dd HH:mm:ss", $null)
        $DaysSinceReport = ((Get-Date) - $LastReport).Days
        $Result.DaysSinceLastReport = $DaysSinceReport

        if ($DaysSinceReport -gt 7) {
            $Result.Drifted = $true
            $Result.DriftReason = "No WSUS report in over 7 days"
        }
    }
} else {
    $Result.Note = "WSUS not configured - using Windows Update"
}

$Result | ConvertTo-Json
''',

    remediate_script=r'''
# Re-register with WSUS
$Result = @{ Success = $false; Actions = @() }

try {
    # Check if WSUS is configured
    $WUServer = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" -ErrorAction SilentlyContinue).WUServer

    if (-not $WUServer) {
        $Result.Success = $true
        $Result.Message = "WSUS not configured - no action needed"
        $Result | ConvertTo-Json
        return
    }

    # Stop Windows Update service
    Stop-Service -Name wuauserv -Force -ErrorAction SilentlyContinue
    $Result.Actions += "Stopped Windows Update service"

    # Clear WSUS registration
    $WUPath = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate"
    Remove-ItemProperty -Path $WUPath -Name "AccountDomainSid" -ErrorAction SilentlyContinue
    Remove-ItemProperty -Path $WUPath -Name "PingID" -ErrorAction SilentlyContinue
    Remove-ItemProperty -Path $WUPath -Name "SusClientId" -ErrorAction SilentlyContinue
    Remove-ItemProperty -Path $WUPath -Name "SusClientIdValidation" -ErrorAction SilentlyContinue
    $Result.Actions += "Cleared WSUS client registration"

    # Start Windows Update service
    Start-Service -Name wuauserv
    $Result.Actions += "Started Windows Update service"
    Start-Sleep -Seconds 3

    # Force re-registration
    wuauclt /resetauthorization /detectnow 2>&1 | Out-Null
    $Result.Actions += "Triggered WSUS re-registration"

    # Also use the newer command
    Start-Process -FilePath "usoclient.exe" -ArgumentList "StartScan" -NoNewWindow -Wait -ErrorAction SilentlyContinue
    $Result.Actions += "Triggered update scan"

    # Report to WSUS
    wuauclt /reportnow 2>&1 | Out-Null
    $Result.Actions += "Reported to WSUS"

    $Result.Success = $true
    $Result.Message = "WSUS client re-registered successfully"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json
''',

    verify_script=r'''
$WUServer = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" -ErrorAction SilentlyContinue).WUServer
$SusClientId = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate" -ErrorAction SilentlyContinue).SusClientId

if (-not $WUServer) {
    @{ Verified = $true; Note = "WSUS not configured" } | ConvertTo-Json
} else {
    @{
        WSUSConfigured = $true
        HasClientId = ($null -ne $SusClientId)
        Verified = ($null -ne $SusClientId)
    } | ConvertTo-Json
}
''',

    timeout_seconds=180,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["WUServer", "WSUSReachable", "SusClientId", "DaysSinceLastReport"]
)


# =============================================================================
# Updates Runbooks Registry
# =============================================================================

UPDATES_RUNBOOKS: Dict[str, WindowsRunbook] = {
    "RB-WIN-UPD-001": RUNBOOK_WUAUSERV_RESET,
    "RB-WIN-UPD-002": RUNBOOK_WSUS_CLIENT,
}
