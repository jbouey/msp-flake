"""
Windows Active Directory Runbooks for HIPAA Compliance.

Runbooks for AD-specific issues and domain trust.
"""

from typing import Dict, List
from dataclasses import dataclass, field
from .runbooks import WindowsRunbook, ExecutionConstraints


# =============================================================================
# RB-WIN-AD-002: Computer Account Password Reset
# =============================================================================

RUNBOOK_COMPUTER_ACCOUNT = WindowsRunbook(
    id="RB-WIN-AD-002",
    name="Computer Account Password Reset",
    description="Reset machine account password when domain trust relationship is broken",
    version="1.0",
    hipaa_controls=["164.312(a)(1)", "164.308(a)(4)(ii)(B)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=1,
        retry_delay_seconds=60,
        requires_maintenance_window=True,  # Requires domain admin or local admin
        allow_concurrent=False
    ),

    detect_script=r'''
# Check domain trust relationship
$Result = @{
    Drifted = $false
}

# Check if machine is domain-joined
$ComputerSystem = Get-WmiObject Win32_ComputerSystem
$Result.IsDomainJoined = $ComputerSystem.PartOfDomain
$Result.Domain = $ComputerSystem.Domain
$Result.ComputerName = $ComputerSystem.Name

if (-not $ComputerSystem.PartOfDomain) {
    $Result.Note = "Machine is not domain-joined"
    $Result | ConvertTo-Json
    return
}

# Test secure channel
try {
    $SecureChannel = Test-ComputerSecureChannel -ErrorAction Stop
    $Result.SecureChannelValid = $SecureChannel

    if (-not $SecureChannel) {
        $Result.Drifted = $true
        $Result.DriftReason = "Secure channel to domain is broken"
    }
} catch {
    $Result.SecureChannelValid = $false
    $Result.Drifted = $true
    $Result.DriftReason = $_.Exception.Message
}

# Check machine account password age
try {
    $MachineAccount = Get-ADComputer $env:COMPUTERNAME -Properties PasswordLastSet -ErrorAction Stop
    $PasswordAge = (Get-Date) - $MachineAccount.PasswordLastSet
    $Result.PasswordAgeDays = [math]::Round($PasswordAge.TotalDays, 0)
    $Result.PasswordLastSet = $MachineAccount.PasswordLastSet.ToString("o")

    # Machine account passwords should auto-rotate every 30 days
    if ($PasswordAge.TotalDays -gt 45) {
        $Result.Drifted = $true
        $Result.DriftReason = "Machine account password is stale"
    }
} catch {
    # May fail if secure channel is broken
    $Result.ADQueryFailed = $true
}

# Check domain controller connectivity
try {
    $DC = [System.DirectoryServices.ActiveDirectory.Domain]::GetCurrentDomain().DomainControllers[0]
    $Result.DomainController = $DC.Name
    $DCPing = Test-Connection -ComputerName $DC.Name -Count 2 -Quiet
    $Result.DCReachable = $DCPing
} catch {
    $Result.DCReachable = $false
}

$Result | ConvertTo-Json
''',

    remediate_script=r'''
# Reset computer account password
$Result = @{ Success = $false; Actions = @() }

try {
    $ComputerSystem = Get-WmiObject Win32_ComputerSystem

    if (-not $ComputerSystem.PartOfDomain) {
        $Result.Success = $true
        $Result.Message = "Machine not domain-joined - no action needed"
        $Result | ConvertTo-Json
        return
    }

    # First, test if secure channel is actually broken
    $SecureChannel = Test-ComputerSecureChannel -ErrorAction SilentlyContinue

    if ($SecureChannel) {
        $Result.Success = $true
        $Result.Message = "Secure channel is already valid"
        $Result | ConvertTo-Json
        return
    }

    # Attempt to repair secure channel
    # This requires local admin rights but NOT domain connectivity
    $RepairResult = Test-ComputerSecureChannel -Repair -ErrorAction Stop

    if ($RepairResult) {
        $Result.Success = $true
        $Result.Actions += "Repaired secure channel"
        $Result.Message = "Domain trust restored successfully"
    } else {
        # If repair fails, the machine may need to be re-joined
        $Result.Success = $false
        $Result.Actions += "Repair attempt failed"
        $Result.Message = "Manual intervention required - may need domain rejoin"
        $Result.ManualSteps = @(
            "1. Log in with local administrator account",
            "2. Remove computer from domain: Remove-Computer -UnjoinDomainCredential (Get-Credential) -Force",
            "3. Restart computer",
            "4. Rejoin domain: Add-Computer -DomainName $($ComputerSystem.Domain) -Credential (Get-Credential) -Restart"
        )
    }
} catch {
    $Result.Error = $_.Exception.Message

    # Provide guidance for manual fix
    $Result.ManualSteps = @(
        "Trust repair failed. To fix manually:",
        "1. Log in with local admin (.\Administrator)",
        "2. Open PowerShell as Admin",
        "3. Run: Reset-ComputerMachinePassword -Credential (Get-Credential)",
        "4. Enter domain admin credentials when prompted"
    )
}

$Result | ConvertTo-Json -Depth 2
''',

    verify_script=r'''
$ComputerSystem = Get-WmiObject Win32_ComputerSystem
if (-not $ComputerSystem.PartOfDomain) {
    @{ Verified = $true; Note = "Not domain-joined" } | ConvertTo-Json
} else {
    $SecureChannel = Test-ComputerSecureChannel -ErrorAction SilentlyContinue
    @{
        SecureChannelValid = $SecureChannel
        Verified = $SecureChannel
    } | ConvertTo-Json
}
''',

    timeout_seconds=180,
    requires_reboot=False,  # May require reboot if domain rejoin is needed
    disruptive=True,  # Can cause authentication issues during repair
    evidence_fields=["SecureChannelValid", "PasswordAgeDays", "DCReachable", "DriftReason"]
)


# =============================================================================
# Active Directory Runbooks Registry
# =============================================================================

AD_RUNBOOKS: Dict[str, WindowsRunbook] = {
    "RB-WIN-AD-002": RUNBOOK_COMPUTER_ACCOUNT,
}
