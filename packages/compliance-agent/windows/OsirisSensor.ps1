<#
.SYNOPSIS
    OsirisCare Windows Compliance Sensor
.DESCRIPTION
    Lightweight read-only sensor that detects drift conditions and pushes events
    to the NixOS appliance for remediation. No credentials, no system modifications.
.NOTES
    Version: 1.0.0
    Requires: PowerShell 5.1+
#>

param(
    [string]$ApplianceIP = "192.168.88.246",
    [int]$AppliancePort = 8080,
    [int]$CheckIntervalSeconds = 30,
    [int]$HeartbeatIntervalSeconds = 60,
    [string]$StatusFilePath = "C:\OsirisCare\status.json",
    [string]$LogFilePath = "C:\OsirisCare\sensor.log"
)

# Sensor version
$script:SensorVersion = "1.0.0"

# State tracking for delta detection
$script:PreviousState = @{}
$script:EventQueue = [System.Collections.ArrayList]::new()
$script:MaxQueueSize = 100
$script:LastHeartbeat = [datetime]::MinValue
$script:StartTime = Get-Date

#region Logging

function Write-SensorLog {
    param(
        [string]$Message,
        [ValidateSet("INFO", "WARN", "ERROR", "DEBUG")]
        [string]$Level = "INFO"
    )

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logEntry = "[$timestamp] [$Level] $Message"

    try {
        # Ensure directory exists
        $logDir = Split-Path $LogFilePath -Parent
        if (-not (Test-Path $logDir)) {
            New-Item -Path $logDir -ItemType Directory -Force | Out-Null
        }

        # Rotate log if > 10MB
        if (Test-Path $LogFilePath) {
            $logFile = Get-Item $LogFilePath
            if ($logFile.Length -gt 10MB) {
                # Keep last 3 rotations
                for ($i = 2; $i -ge 0; $i--) {
                    $src = if ($i -eq 0) { $LogFilePath } else { "$LogFilePath.$i" }
                    $dst = "$LogFilePath.$($i + 1)"
                    if (Test-Path $src) {
                        Move-Item $src $dst -Force -ErrorAction SilentlyContinue
                    }
                }
                # Delete oldest
                Remove-Item "$LogFilePath.3" -Force -ErrorAction SilentlyContinue
            }
        }

        Add-Content -Path $LogFilePath -Value $logEntry -ErrorAction SilentlyContinue
    }
    catch {
        # Silent fail - logging shouldn't break sensor
    }
}

#endregion

#region HTTP Client

function Send-ToAppliance {
    param(
        [string]$Endpoint,
        [hashtable]$Body
    )

    $uri = "http://${ApplianceIP}:${AppliancePort}/api/sensor/$Endpoint"
    $json = $Body | ConvertTo-Json -Depth 10 -Compress

    try {
        $response = Invoke-RestMethod -Uri $uri -Method Post -Body $json `
            -ContentType "application/json" -TimeoutSec 10 -ErrorAction Stop
        return @{ Success = $true; Response = $response }
    }
    catch {
        Write-SensorLog "Failed to send to $Endpoint : $_" -Level ERROR
        return @{ Success = $false; Error = $_.Exception.Message }
    }
}

function Send-QueuedEvents {
    if ($script:EventQueue.Count -eq 0) { return }

    $sent = [System.Collections.ArrayList]::new()

    foreach ($event in $script:EventQueue) {
        $result = Send-ToAppliance -Endpoint $event.Endpoint -Body $event.Body
        if ($result.Success) {
            $sent.Add($event) | Out-Null
        }
    }

    # Remove sent events
    foreach ($event in $sent) {
        $script:EventQueue.Remove($event)
    }

    if ($sent.Count -gt 0) {
        Write-SensorLog "Sent $($sent.Count) queued events" -Level INFO
    }
}

function Queue-Event {
    param(
        [string]$Endpoint,
        [hashtable]$Body
    )

    if ($script:EventQueue.Count -ge $script:MaxQueueSize) {
        # Remove oldest
        $script:EventQueue.RemoveAt(0)
    }

    $script:EventQueue.Add(@{
        Endpoint = $Endpoint
        Body = $Body
        QueuedAt = (Get-Date).ToString("o")
    }) | Out-Null
}

#endregion

#region Compliance Checks

function Get-Hostname {
    try {
        $computerSystem = Get-CimInstance Win32_ComputerSystem
        return @{
            Hostname = $env:COMPUTERNAME
            Domain = if ($computerSystem.PartOfDomain) { $computerSystem.Domain } else { $null }
        }
    }
    catch {
        return @{ Hostname = $env:COMPUTERNAME; Domain = $null }
    }
}

function Check-Firewall {
    # CHECK 1: Firewall Status (RB-WIN-FIREWALL-001)
    try {
        $profiles = Get-NetFirewallProfile -ErrorAction Stop
        $disabled = $profiles | Where-Object { -not $_.Enabled }

        if ($disabled) {
            return @{
                DriftType = "firewall_disabled"
                Severity = "critical"
                CheckId = "RB-WIN-FIREWALL-001"
                Drifted = $true
                Details = @{
                    disabled_profiles = @($disabled.Name)
                    all_profiles = @($profiles | ForEach-Object { @{ Name = $_.Name; Enabled = $_.Enabled } })
                }
            }
        }
        return @{ DriftType = "firewall_disabled"; Drifted = $false }
    }
    catch {
        Write-SensorLog "Firewall check failed: $_" -Level WARN
        return @{ DriftType = "firewall_disabled"; Drifted = $false; Error = $_.Exception.Message }
    }
}

function Check-WindowsDefender {
    # CHECK 2: Windows Defender (RB-WIN-AV-001)
    try {
        $service = Get-Service -Name WinDefend -ErrorAction SilentlyContinue

        if (-not $service -or $service.Status -ne 'Running') {
            return @{
                DriftType = "defender_stopped"
                Severity = "critical"
                CheckId = "RB-WIN-AV-001"
                Drifted = $true
                Details = @{
                    service_status = if ($service) { $service.Status.ToString() } else { "NotFound" }
                    service_exists = $null -ne $service
                }
            }
        }
        return @{ DriftType = "defender_stopped"; Drifted = $false }
    }
    catch {
        Write-SensorLog "Defender check failed: $_" -Level WARN
        return @{ DriftType = "defender_stopped"; Drifted = $false; Error = $_.Exception.Message }
    }
}

function Check-PrintSpooler {
    # CHECK 3: Print Spooler (RB-WIN-SVC-001)
    try {
        $service = Get-Service -Name Spooler -ErrorAction SilentlyContinue

        # Check if this is a print server (has print shares)
        $isPrintServer = $false
        try {
            $printShares = Get-Printer -ErrorAction SilentlyContinue | Where-Object { $_.Shared }
            $isPrintServer = $null -ne $printShares -and @($printShares).Count -gt 0
        }
        catch { }

        if ($service -and $service.Status -eq 'Running' -and -not $isPrintServer) {
            return @{
                DriftType = "spooler_running"
                Severity = "high"
                CheckId = "RB-WIN-SVC-001"
                Drifted = $true
                Details = @{
                    service_status = $service.Status.ToString()
                    is_print_server = $isPrintServer
                    reason = "Print Spooler running on non-print server (security risk)"
                }
            }
        }
        return @{ DriftType = "spooler_running"; Drifted = $false }
    }
    catch {
        Write-SensorLog "Spooler check failed: $_" -Level WARN
        return @{ DriftType = "spooler_running"; Drifted = $false; Error = $_.Exception.Message }
    }
}

function Check-CriticalServices {
    # CHECK 4: Critical Services (RB-WIN-SVC-002)
    $criticalServices = @{
        "EventLog" = "critical"
        "W32Time" = "high"
        "Netlogon" = "critical"
        "NTDS" = "critical"      # AD DS
        "DNS" = "critical"       # DNS Server
        "CryptSvc" = "high"
        "WinRM" = "high"
    }

    $stoppedServices = @()
    $highestSeverity = "high"

    foreach ($svcName in $criticalServices.Keys) {
        try {
            $service = Get-Service -Name $svcName -ErrorAction SilentlyContinue
            if ($service -and $service.Status -ne 'Running') {
                $stoppedServices += @{
                    Name = $svcName
                    Status = $service.Status.ToString()
                    Severity = $criticalServices[$svcName]
                }
                if ($criticalServices[$svcName] -eq "critical") {
                    $highestSeverity = "critical"
                }
            }
        }
        catch { }
    }

    if ($stoppedServices.Count -gt 0) {
        return @{
            DriftType = "critical_service_stopped"
            Severity = $highestSeverity
            CheckId = "RB-WIN-SVC-002"
            Drifted = $true
            Details = @{
                stopped_services = $stoppedServices
                count = $stoppedServices.Count
            }
        }
    }
    return @{ DriftType = "critical_service_stopped"; Drifted = $false }
}

function Check-DiskSpace {
    # CHECK 5: Disk Space (RB-WIN-STOR-001)
    try {
        $disks = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" -ErrorAction Stop
        $lowDisks = @()
        $highestSeverity = "high"

        foreach ($disk in $disks) {
            if ($disk.Size -gt 0) {
                $freePercent = [math]::Round(($disk.FreeSpace / $disk.Size) * 100, 1)

                if ($freePercent -lt 5) {
                    $lowDisks += @{
                        Drive = $disk.DeviceID
                        FreePercent = $freePercent
                        FreeGB = [math]::Round($disk.FreeSpace / 1GB, 2)
                        TotalGB = [math]::Round($disk.Size / 1GB, 2)
                        Severity = "critical"
                    }
                    $highestSeverity = "critical"
                }
                elseif ($freePercent -lt 10) {
                    $lowDisks += @{
                        Drive = $disk.DeviceID
                        FreePercent = $freePercent
                        FreeGB = [math]::Round($disk.FreeSpace / 1GB, 2)
                        TotalGB = [math]::Round($disk.Size / 1GB, 2)
                        Severity = "high"
                    }
                }
            }
        }

        if ($lowDisks.Count -gt 0) {
            return @{
                DriftType = "low_disk_space"
                Severity = $highestSeverity
                CheckId = "RB-WIN-STOR-001"
                Drifted = $true
                Details = @{
                    low_disks = $lowDisks
                    count = $lowDisks.Count
                }
            }
        }
        return @{ DriftType = "low_disk_space"; Drifted = $false }
    }
    catch {
        Write-SensorLog "Disk space check failed: $_" -Level WARN
        return @{ DriftType = "low_disk_space"; Drifted = $false; Error = $_.Exception.Message }
    }
}

function Check-GuestAccount {
    # CHECK 6: Guest Account (RB-WIN-SEC-002)
    try {
        $guest = Get-LocalUser -Name "Guest" -ErrorAction SilentlyContinue

        if ($guest -and $guest.Enabled) {
            return @{
                DriftType = "guest_account_enabled"
                Severity = "high"
                CheckId = "RB-WIN-SEC-002"
                Drifted = $true
                Details = @{
                    enabled = $true
                    last_logon = if ($guest.LastLogon) { $guest.LastLogon.ToString("o") } else { $null }
                }
            }
        }
        return @{ DriftType = "guest_account_enabled"; Drifted = $false }
    }
    catch {
        Write-SensorLog "Guest account check failed: $_" -Level WARN
        return @{ DriftType = "guest_account_enabled"; Drifted = $false; Error = $_.Exception.Message }
    }
}

function Check-SMBv1 {
    # CHECK 7: SMBv1 Enabled (RB-WIN-SEC-004)
    try {
        $smbConfig = Get-SmbServerConfiguration -ErrorAction Stop

        if ($smbConfig.EnableSMB1Protocol) {
            return @{
                DriftType = "smbv1_enabled"
                Severity = "critical"
                CheckId = "RB-WIN-SEC-004"
                Drifted = $true
                Details = @{
                    smb1_enabled = $true
                    smb2_enabled = $smbConfig.EnableSMB2Protocol
                    reason = "SMBv1 is vulnerable to EternalBlue/WannaCry exploits"
                }
            }
        }
        return @{ DriftType = "smbv1_enabled"; Drifted = $false }
    }
    catch {
        Write-SensorLog "SMBv1 check failed: $_" -Level WARN
        return @{ DriftType = "smbv1_enabled"; Drifted = $false; Error = $_.Exception.Message }
    }
}

function Check-PendingReboot {
    # CHECK 8: Pending Reboot (RB-WIN-UPD-002)
    try {
        $rebootRequired = $false
        $reasons = @()

        # Windows Update reboot pending
        if (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired") {
            $rebootRequired = $true
            $reasons += "WindowsUpdate"
        }

        # Component-Based Servicing
        if (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending") {
            $rebootRequired = $true
            $reasons += "CBS"
        }

        # Pending file rename operations
        $pendingRename = Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager" -Name PendingFileRenameOperations -ErrorAction SilentlyContinue
        if ($pendingRename.PendingFileRenameOperations) {
            $rebootRequired = $true
            $reasons += "PendingFileRename"
        }

        if ($rebootRequired) {
            return @{
                DriftType = "pending_reboot"
                Severity = "medium"
                CheckId = "RB-WIN-UPD-002"
                Drifted = $true
                Details = @{
                    reasons = $reasons
                }
            }
        }
        return @{ DriftType = "pending_reboot"; Drifted = $false }
    }
    catch {
        Write-SensorLog "Pending reboot check failed: $_" -Level WARN
        return @{ DriftType = "pending_reboot"; Drifted = $false; Error = $_.Exception.Message }
    }
}

function Check-AuditPolicy {
    # CHECK 9: Audit Policy (RB-WIN-SEC-005)
    try {
        $auditOutput = auditpol /get /category:* 2>&1
        $noAuditing = $auditOutput | Select-String "No Auditing"

        # Critical categories that should have auditing
        $criticalMissing = @()
        $auditText = $auditOutput | Out-String

        $criticalCategories = @(
            "Logon",
            "Account Logon",
            "Account Management",
            "Object Access",
            "Policy Change"
        )

        foreach ($cat in $criticalCategories) {
            if ($auditText -match "$cat.*No Auditing") {
                $criticalMissing += $cat
            }
        }

        if ($criticalMissing.Count -gt 0) {
            return @{
                DriftType = "audit_policy_disabled"
                Severity = "high"
                CheckId = "RB-WIN-SEC-005"
                Drifted = $true
                Details = @{
                    disabled_categories = $criticalMissing
                    count = $criticalMissing.Count
                }
            }
        }
        return @{ DriftType = "audit_policy_disabled"; Drifted = $false }
    }
    catch {
        Write-SensorLog "Audit policy check failed: $_" -Level WARN
        return @{ DriftType = "audit_policy_disabled"; Drifted = $false; Error = $_.Exception.Message }
    }
}

function Check-TimeSync {
    # CHECK 10: Time Sync (RB-WIN-NET-002)
    try {
        $w32time = Get-Service -Name w32time -ErrorAction SilentlyContinue

        if (-not $w32time -or $w32time.Status -ne 'Running') {
            return @{
                DriftType = "time_sync_failed"
                Severity = "high"
                CheckId = "RB-WIN-NET-002"
                Drifted = $true
                Details = @{
                    service_status = if ($w32time) { $w32time.Status.ToString() } else { "NotFound" }
                    reason = "W32Time service not running"
                }
            }
        }

        # Check sync status
        $w32tmOutput = w32tm /query /status 2>&1 | Out-String

        if ($w32tmOutput -match "error|not running|0x800706B5") {
            return @{
                DriftType = "time_sync_failed"
                Severity = "high"
                CheckId = "RB-WIN-NET-002"
                Drifted = $true
                Details = @{
                    service_status = "Running"
                    sync_error = $true
                    output = $w32tmOutput.Substring(0, [Math]::Min(500, $w32tmOutput.Length))
                }
            }
        }

        return @{ DriftType = "time_sync_failed"; Drifted = $false }
    }
    catch {
        Write-SensorLog "Time sync check failed: $_" -Level WARN
        return @{ DriftType = "time_sync_failed"; Drifted = $false; Error = $_.Exception.Message }
    }
}

function Check-PasswordPolicy {
    # CHECK 11: Password Policy (Alert Only)
    try {
        $netAccounts = net accounts 2>&1 | Out-String

        $minLength = 0
        if ($netAccounts -match "Minimum password length:\s*(\d+)") {
            $minLength = [int]$matches[1]
        }

        if ($minLength -lt 14) {
            return @{
                DriftType = "weak_password_policy"
                Severity = "medium"
                CheckId = "RB-WIN-SEC-003"
                Drifted = $true
                Details = @{
                    min_length = $minLength
                    required_length = 14
                    alert_only = $true
                    reason = "Password minimum length below HIPAA recommendation"
                }
            }
        }
        return @{ DriftType = "weak_password_policy"; Drifted = $false }
    }
    catch {
        Write-SensorLog "Password policy check failed: $_" -Level WARN
        return @{ DriftType = "weak_password_policy"; Drifted = $false; Error = $_.Exception.Message }
    }
}

function Check-AccountLockout {
    # CHECK 12: Account Lockout (Alert Only)
    try {
        $netAccounts = net accounts 2>&1 | Out-String

        $lockoutThreshold = 0
        if ($netAccounts -match "Lockout threshold:\s*(\d+|Never)") {
            if ($matches[1] -eq "Never") {
                $lockoutThreshold = 0
            }
            else {
                $lockoutThreshold = [int]$matches[1]
            }
        }

        if ($lockoutThreshold -eq 0) {
            return @{
                DriftType = "no_account_lockout"
                Severity = "medium"
                CheckId = "RB-WIN-SEC-006"
                Drifted = $true
                Details = @{
                    lockout_threshold = 0
                    alert_only = $true
                    reason = "Account lockout disabled - vulnerable to brute force"
                }
            }
        }
        return @{ DriftType = "no_account_lockout"; Drifted = $false }
    }
    catch {
        Write-SensorLog "Account lockout check failed: $_" -Level WARN
        return @{ DriftType = "no_account_lockout"; Drifted = $false; Error = $_.Exception.Message }
    }
}

#endregion

#region Main Loop

function Run-AllChecks {
    $checks = @(
        { Check-Firewall },
        { Check-WindowsDefender },
        { Check-PrintSpooler },
        { Check-CriticalServices },
        { Check-DiskSpace },
        { Check-GuestAccount },
        { Check-SMBv1 },
        { Check-PendingReboot },
        { Check-AuditPolicy },
        { Check-TimeSync },
        { Check-PasswordPolicy },
        { Check-AccountLockout }
    )

    $results = @{}

    foreach ($check in $checks) {
        try {
            $result = & $check
            if ($result.DriftType) {
                $results[$result.DriftType] = $result
            }
        }
        catch {
            Write-SensorLog "Check execution failed: $_" -Level ERROR
        }
    }

    return $results
}

function Process-StateChanges {
    param([hashtable]$CurrentState)

    $hostInfo = Get-Hostname
    $timestamp = (Get-Date).ToUniversalTime().ToString("o")

    # Detect NEW drifts
    foreach ($driftType in $CurrentState.Keys) {
        $current = $CurrentState[$driftType]
        $previous = $script:PreviousState[$driftType]

        if ($current.Drifted) {
            # New drift or changed drift
            if (-not $previous -or -not $previous.Drifted) {
                $event = @{
                    hostname = $hostInfo.Hostname
                    domain = $hostInfo.Domain
                    drift_type = $driftType
                    severity = $current.Severity
                    details = $current.Details
                    check_id = $current.CheckId
                    detected_at = $timestamp
                    sensor_version = $script:SensorVersion
                }

                Write-SensorLog "NEW DRIFT: $driftType ($($current.Severity))" -Level WARN

                $result = Send-ToAppliance -Endpoint "drift" -Body $event
                if (-not $result.Success) {
                    Queue-Event -Endpoint "drift" -Body $event
                }
            }
        }
    }

    # Detect RESOLVED drifts
    foreach ($driftType in $script:PreviousState.Keys) {
        $previous = $script:PreviousState[$driftType]
        $current = $CurrentState[$driftType]

        if ($previous.Drifted -and (-not $current -or -not $current.Drifted)) {
            $event = @{
                hostname = $hostInfo.Hostname
                drift_type = $driftType
                resolved_at = $timestamp
                resolved_by = "external"
            }

            Write-SensorLog "RESOLVED: $driftType" -Level INFO

            $result = Send-ToAppliance -Endpoint "resolved" -Body $event
            if (-not $result.Success) {
                Queue-Event -Endpoint "resolved" -Body $event
            }
        }
    }

    # Update previous state
    $script:PreviousState = $CurrentState.Clone()
}

function Send-Heartbeat {
    param([hashtable]$CurrentState)

    $hostInfo = Get-Hostname
    $timestamp = (Get-Date).ToUniversalTime().ToString("o")

    $driftCount = ($CurrentState.Values | Where-Object { $_.Drifted }).Count
    $hasCritical = ($CurrentState.Values | Where-Object { $_.Drifted -and $_.Severity -eq "critical" }).Count -gt 0
    $uptimeSeconds = [int]((Get-Date) - $script:StartTime).TotalSeconds

    $heartbeat = @{
        hostname = $hostInfo.Hostname
        domain = $hostInfo.Domain
        sensor_version = $script:SensorVersion
        timestamp = $timestamp
        drift_count = $driftCount
        has_critical = $hasCritical
        compliant = ($driftCount -eq 0)
        uptime_seconds = $uptimeSeconds
        mode = "sensor"
    }

    $result = Send-ToAppliance -Endpoint "heartbeat" -Body $heartbeat

    if ($result.Success) {
        $script:LastHeartbeat = Get-Date
        Write-SensorLog "Heartbeat sent (drifts: $driftCount, compliant: $($driftCount -eq 0))" -Level DEBUG
    }

    return $result.Success
}

function Write-StatusFile {
    param([hashtable]$CurrentState)

    try {
        $hostInfo = Get-Hostname
        $driftCount = ($CurrentState.Values | Where-Object { $_.Drifted }).Count
        $drifts = @($CurrentState.Values | Where-Object { $_.Drifted } | ForEach-Object {
            @{
                Type = $_.DriftType
                Severity = $_.Severity
                CheckId = $_.CheckId
            }
        })

        $status = @{
            Hostname = $hostInfo.Hostname
            Domain = $hostInfo.Domain
            SensorVersion = $script:SensorVersion
            Timestamp = (Get-Date).ToUniversalTime().ToString("o")
            DriftCount = $driftCount
            Compliant = ($driftCount -eq 0)
            Drifts = $drifts
            ApplianceIP = $ApplianceIP
            AppliancePort = $AppliancePort
            UptimeSeconds = [int]((Get-Date) - $script:StartTime).TotalSeconds
        }

        $statusDir = Split-Path $StatusFilePath -Parent
        if (-not (Test-Path $statusDir)) {
            New-Item -Path $statusDir -ItemType Directory -Force | Out-Null
        }

        $status | ConvertTo-Json -Depth 5 | Out-File -FilePath $StatusFilePath -Encoding UTF8 -Force
    }
    catch {
        Write-SensorLog "Failed to write status file: $_" -Level ERROR
    }
}

function Start-Sensor {
    Write-SensorLog "OsirisCare Sensor v$script:SensorVersion starting" -Level INFO
    Write-SensorLog "Appliance: http://${ApplianceIP}:${AppliancePort}" -Level INFO
    Write-SensorLog "Check interval: ${CheckIntervalSeconds}s, Heartbeat interval: ${HeartbeatIntervalSeconds}s" -Level INFO

    $lastCheck = [datetime]::MinValue

    while ($true) {
        try {
            $now = Get-Date

            # Run compliance checks
            if (($now - $lastCheck).TotalSeconds -ge $CheckIntervalSeconds) {
                $currentState = Run-AllChecks
                Process-StateChanges -CurrentState $currentState
                Write-StatusFile -CurrentState $currentState
                $lastCheck = $now
            }

            # Send heartbeat
            if (($now - $script:LastHeartbeat).TotalSeconds -ge $HeartbeatIntervalSeconds) {
                $currentState = if ($currentState) { $currentState } else { Run-AllChecks }
                Send-Heartbeat -CurrentState $currentState
            }

            # Send queued events
            Send-QueuedEvents

            # Sleep for 5 seconds between iterations
            Start-Sleep -Seconds 5
        }
        catch {
            Write-SensorLog "Main loop error: $_" -Level ERROR
            Start-Sleep -Seconds 10
        }
    }
}

#endregion

# Entry point
Start-Sensor
