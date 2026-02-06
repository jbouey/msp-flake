"""
Windows Remote Desktop Runbooks for HIPAA Compliance.

Runbooks for RDP hardening and secure remote access configuration.
"""

from typing import Dict, List
from dataclasses import dataclass, field
from .runbooks import WindowsRunbook, ExecutionConstraints


# =============================================================================
# RB-WIN-RDP-001: Remote Desktop Hardening
# =============================================================================

RUNBOOK_RDP_HARDENING = WindowsRunbook(
    id="RB-WIN-RDP-001",
    name="Remote Desktop Hardening",
    description="Harden RDP settings including NLA, encryption level, idle timeout, and restricted admin mode",
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
# Comprehensive RDP hardening check
$Result = @{
    Drifted = $false
    Issues = @()
}

try {
    # Check if RDP is enabled
    $TSKey = "HKLM:\System\CurrentControlSet\Control\Terminal Server"
    $RDPDisabled = (Get-ItemProperty -Path $TSKey -Name "fDenyTSConnections" -ErrorAction SilentlyContinue).fDenyTSConnections
    $Result.RDPEnabled = ($RDPDisabled -eq 0)

    if ($RDPDisabled -ne 0) {
        $Result.Note = "RDP is disabled - hardening check informational only"
        $Result | ConvertTo-Json -Depth 2
        return
    }

    $RDPTcpKey = "HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"

    # 1. Check NLA (Network Level Authentication)
    $NLA = (Get-ItemProperty -Path $RDPTcpKey -Name "UserAuthentication" -ErrorAction SilentlyContinue).UserAuthentication
    $Result.NLAEnabled = ($NLA -eq 1)
    if ($NLA -ne 1) {
        $Result.Drifted = $true
        $Result.Issues += "Network Level Authentication (NLA) is not enabled"
    }

    # 2. Check Security Layer
    # 0 = RDP Security Layer, 1 = Negotiate, 2 = SSL/TLS
    $SecurityLayer = (Get-ItemProperty -Path $RDPTcpKey -Name "SecurityLayer" -ErrorAction SilentlyContinue).SecurityLayer
    $Result.SecurityLayer = $SecurityLayer
    $Result.SecurityLayerName = switch ($SecurityLayer) {
        0 { "RDP (insecure)" }
        1 { "Negotiate" }
        2 { "SSL/TLS" }
        default { "Unknown" }
    }
    if ($SecurityLayer -lt 2) {
        $Result.Drifted = $true
        $Result.Issues += "RDP security layer should be SSL/TLS (current: $($Result.SecurityLayerName))"
    }

    # 3. Check Encryption Level
    # 1 = Low, 2 = Client Compatible, 3 = High, 4 = FIPS Compliant
    $EncryptionLevel = (Get-ItemProperty -Path $RDPTcpKey -Name "MinEncryptionLevel" -ErrorAction SilentlyContinue).MinEncryptionLevel
    $Result.MinEncryptionLevel = $EncryptionLevel
    $Result.EncryptionLevelName = switch ($EncryptionLevel) {
        1 { "Low" }
        2 { "Client Compatible" }
        3 { "High" }
        4 { "FIPS Compliant" }
        default { "Unknown" }
    }
    if ($null -eq $EncryptionLevel -or $EncryptionLevel -lt 3) {
        $Result.Drifted = $true
        $Result.Issues += "RDP encryption level should be High or FIPS (current: $($Result.EncryptionLevelName))"
    }

    # 4. Check Idle Timeout
    $TSPolicyKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services"
    $IdleTimeout = (Get-ItemProperty -Path $TSPolicyKey -Name "MaxIdleTime" -ErrorAction SilentlyContinue).MaxIdleTime
    $Result.IdleTimeoutMs = $IdleTimeout
    $Result.IdleTimeoutMinutes = if ($IdleTimeout) { [math]::Round($IdleTimeout / 60000, 0) } else { "Not configured" }

    # Should be 15 minutes (900000 ms) or less
    if ($null -eq $IdleTimeout -or $IdleTimeout -gt 900000 -or $IdleTimeout -eq 0) {
        $Result.Drifted = $true
        $Result.Issues += "RDP idle timeout not configured or exceeds 15 minutes"
    }

    # 5. Check Disconnected Session Timeout
    $DisconnectTimeout = (Get-ItemProperty -Path $TSPolicyKey -Name "MaxDisconnectionTime" -ErrorAction SilentlyContinue).MaxDisconnectionTime
    $Result.DisconnectTimeoutMs = $DisconnectTimeout
    if ($null -eq $DisconnectTimeout -or $DisconnectTimeout -gt 300000 -or $DisconnectTimeout -eq 0) {
        $Result.Issues += "RDP disconnected session timeout not configured or exceeds 5 minutes"
    }

    # 6. Check Restricted Admin Mode
    $RestrictedAdmin = (Get-ItemProperty -Path "HKLM:\System\CurrentControlSet\Control\Lsa" -Name "DisableRestrictedAdmin" -ErrorAction SilentlyContinue).DisableRestrictedAdmin
    $Result.RestrictedAdminEnabled = ($RestrictedAdmin -eq 0)
    if ($RestrictedAdmin -ne 0) {
        $Result.Issues += "Restricted Admin mode is not enabled (credential theft mitigation)"
    }

    # 7. Check RDP Port (default 3389 - note if changed)
    $RDPPort = (Get-ItemProperty -Path $RDPTcpKey -Name "PortNumber" -ErrorAction SilentlyContinue).PortNumber
    $Result.RDPPort = $RDPPort

    # 8. Check if RDP is restricted to specific users
    $RDPUsersGroup = [ADSI]"WinNT://./Remote Desktop Users,group"
    $RDPMembers = @($RDPUsersGroup.Invoke("Members")) | ForEach-Object {
        ([ADSI]$_).Path.Split("/")[-1]
    }
    $Result.RDPAllowedUsers = $RDPMembers
    $Result.RDPUserCount = $RDPMembers.Count

    # 9. Check RDP listening on firewall
    $RDPFirewallRules = Get-NetFirewallRule -DisplayName "*Remote Desktop*" -ErrorAction SilentlyContinue |
        Where-Object { $_.Enabled -eq $true }
    $Result.RDPFirewallRulesEnabled = @($RDPFirewallRules).Count

    # 10. Check for RemoteFX compression (should be disabled for security)
    $RemoteFX = (Get-ItemProperty -Path $TSPolicyKey -Name "fDisableVirtualChannelFiltering" -ErrorAction SilentlyContinue).fDisableVirtualChannelFiltering
    if ($RemoteFX -eq 1) {
        $Result.Issues += "Virtual channel filtering is disabled (security risk)"
    }
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Drifted = $true
}

$Result | ConvertTo-Json -Depth 3
''',

    remediate_script=r'''
# Harden RDP configuration
$Result = @{ Success = $false; Actions = @() }

try {
    $RDPTcpKey = "HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"
    $TSPolicyKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services"

    # Check if RDP is even enabled
    $TSKey = "HKLM:\System\CurrentControlSet\Control\Terminal Server"
    $RDPDisabled = (Get-ItemProperty -Path $TSKey -Name "fDenyTSConnections" -ErrorAction SilentlyContinue).fDenyTSConnections
    if ($RDPDisabled -ne 0) {
        $Result.Success = $true
        $Result.Message = "RDP is disabled - no hardening needed"
        $Result | ConvertTo-Json
        return
    }

    # 1. Enable NLA (Network Level Authentication)
    Set-ItemProperty -Path $RDPTcpKey -Name "UserAuthentication" -Value 1 -Type DWord
    $Result.Actions += "Enabled Network Level Authentication (NLA)"

    # 2. Set Security Layer to SSL/TLS
    Set-ItemProperty -Path $RDPTcpKey -Name "SecurityLayer" -Value 2 -Type DWord
    $Result.Actions += "Set RDP security layer to SSL/TLS"

    # 3. Set Encryption Level to High
    Set-ItemProperty -Path $RDPTcpKey -Name "MinEncryptionLevel" -Value 3 -Type DWord
    $Result.Actions += "Set RDP encryption level to High"

    # 4. Configure Idle Timeout (15 minutes = 900000 ms)
    if (-not (Test-Path $TSPolicyKey)) {
        New-Item -Path $TSPolicyKey -Force | Out-Null
    }
    Set-ItemProperty -Path $TSPolicyKey -Name "MaxIdleTime" -Value 900000 -Type DWord
    $Result.Actions += "Set RDP idle timeout to 15 minutes"

    # 5. Configure Disconnected Session Timeout (5 minutes = 300000 ms)
    Set-ItemProperty -Path $TSPolicyKey -Name "MaxDisconnectionTime" -Value 300000 -Type DWord
    $Result.Actions += "Set disconnected session timeout to 5 minutes"

    # 6. Enable Restricted Admin Mode
    Set-ItemProperty -Path "HKLM:\System\CurrentControlSet\Control\Lsa" -Name "DisableRestrictedAdmin" -Value 0 -Type DWord
    $Result.Actions += "Enabled Restricted Admin mode"

    # 7. Enable virtual channel filtering
    Set-ItemProperty -Path $TSPolicyKey -Name "fDisableVirtualChannelFiltering" -Value 0 -Type DWord
    $Result.Actions += "Enabled virtual channel filtering"

    # 8. Disable clipboard redirection for security
    Set-ItemProperty -Path $TSPolicyKey -Name "fDisableClip" -Value 1 -Type DWord
    $Result.Actions += "Disabled clipboard redirection"

    # 9. Disable drive redirection for data loss prevention
    Set-ItemProperty -Path $TSPolicyKey -Name "fDisableCdm" -Value 1 -Type DWord
    $Result.Actions += "Disabled drive redirection"

    # 10. Set maximum connection limit
    Set-ItemProperty -Path $TSPolicyKey -Name "MaxInstanceCount" -Value 2 -Type DWord
    $Result.Actions += "Set maximum concurrent RDP connections to 2"

    $Result.Success = $true
    $Result.Message = "RDP hardening complete"
    $Result.Warning = "Clipboard and drive redirection have been disabled for security. Re-enable if required for operations."
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
# Verify RDP hardening settings
try {
    $TSKey = "HKLM:\System\CurrentControlSet\Control\Terminal Server"
    $RDPDisabled = (Get-ItemProperty -Path $TSKey -Name "fDenyTSConnections" -ErrorAction SilentlyContinue).fDenyTSConnections

    if ($RDPDisabled -ne 0) {
        @{ Verified = $true; Note = "RDP is disabled" } | ConvertTo-Json
        return
    }

    $RDPTcpKey = "HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"
    $TSPolicyKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services"

    $NLA = (Get-ItemProperty -Path $RDPTcpKey -Name "UserAuthentication" -ErrorAction SilentlyContinue).UserAuthentication
    $SecurityLayer = (Get-ItemProperty -Path $RDPTcpKey -Name "SecurityLayer" -ErrorAction SilentlyContinue).SecurityLayer
    $EncryptionLevel = (Get-ItemProperty -Path $RDPTcpKey -Name "MinEncryptionLevel" -ErrorAction SilentlyContinue).MinEncryptionLevel
    $IdleTimeout = (Get-ItemProperty -Path $TSPolicyKey -Name "MaxIdleTime" -ErrorAction SilentlyContinue).MaxIdleTime

    @{
        NLAEnabled = ($NLA -eq 1)
        SecurityLayerTLS = ($SecurityLayer -eq 2)
        EncryptionHigh = ($EncryptionLevel -ge 3)
        IdleTimeoutConfigured = ($null -ne $IdleTimeout -and $IdleTimeout -le 900000 -and $IdleTimeout -gt 0)
        Verified = (
            $NLA -eq 1 -and
            $SecurityLayer -eq 2 -and
            $EncryptionLevel -ge 3 -and
            $null -ne $IdleTimeout -and $IdleTimeout -le 900000 -and $IdleTimeout -gt 0
        )
    } | ConvertTo-Json
} catch {
    @{ Verified = $false; Error = $_.Exception.Message } | ConvertTo-Json
}
''',

    timeout_seconds=120,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["NLAEnabled", "SecurityLayer", "MinEncryptionLevel", "IdleTimeoutMs", "RestrictedAdminEnabled", "RDPAllowedUsers", "Issues"]
)


# =============================================================================
# RDP Runbooks Registry
# =============================================================================

RDP_RUNBOOKS: Dict[str, WindowsRunbook] = {
    "RB-WIN-RDP-001": RUNBOOK_RDP_HARDENING,
}
