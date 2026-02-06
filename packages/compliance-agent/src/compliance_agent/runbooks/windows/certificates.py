"""
Windows Certificate Runbooks for HIPAA Compliance.

Runbooks for certificate lifecycle management and expiry monitoring.
"""

from typing import Dict, List
from dataclasses import dataclass, field
from .runbooks import WindowsRunbook, ExecutionConstraints


# =============================================================================
# RB-WIN-CERT-001: Certificate Expiry Check
# =============================================================================

RUNBOOK_CERT_EXPIRY = WindowsRunbook(
    id="RB-WIN-CERT-001",
    name="Certificate Expiry Check",
    description="Monitor LocalMachine certificate store for expiring or expired certificates",
    version="1.0",
    hipaa_controls=["164.312(e)(2)(ii)"],
    severity="high",
    constraints=ExecutionConstraints(
        max_retries=2,
        retry_delay_seconds=30,
        requires_maintenance_window=False,
        allow_concurrent=True
    ),

    detect_script=r'''
# Check for expiring and expired certificates in LocalMachine store
$Result = @{
    Drifted = $false
    Issues = @()
    ExpiringCerts = @()
    ExpiredCerts = @()
    HealthyCerts = @()
}

try {
    $WarningDays = 30
    $Now = Get-Date

    # Get all certificates from LocalMachine stores
    $Stores = @("My", "WebHosting", "Remote Desktop")

    foreach ($StoreName in $Stores) {
        $Certs = Get-ChildItem -Path "Cert:\LocalMachine\$StoreName" -ErrorAction SilentlyContinue

        foreach ($Cert in $Certs) {
            # Skip CA certs and certs without private keys in My store
            $CertInfo = @{
                Subject = $Cert.Subject
                Thumbprint = $Cert.Thumbprint
                Issuer = $Cert.Issuer
                NotAfter = $Cert.NotAfter.ToString("o")
                NotBefore = $Cert.NotBefore.ToString("o")
                Store = $StoreName
                HasPrivateKey = $Cert.HasPrivateKey
                DaysUntilExpiry = [math]::Round(($Cert.NotAfter - $Now).TotalDays, 0)
            }

            if ($Cert.NotAfter -lt $Now) {
                # Already expired
                $CertInfo.Status = "Expired"
                $Result.ExpiredCerts += $CertInfo
                $Result.Drifted = $true
                $Result.Issues += "EXPIRED: $($Cert.Subject) (expired $([math]::Abs($CertInfo.DaysUntilExpiry)) days ago)"
            } elseif ($Cert.NotAfter -lt $Now.AddDays($WarningDays)) {
                # Expiring soon
                $CertInfo.Status = "ExpiringSoon"
                $Result.ExpiringCerts += $CertInfo
                $Result.Drifted = $true
                $Result.Issues += "EXPIRING: $($Cert.Subject) (in $($CertInfo.DaysUntilExpiry) days)"
            } else {
                $CertInfo.Status = "Healthy"
                $Result.HealthyCerts += $CertInfo
            }
        }
    }

    $Result.TotalCertsChecked = $Result.ExpiringCerts.Count + $Result.ExpiredCerts.Count + $Result.HealthyCerts.Count
    $Result.ExpiringCount = $Result.ExpiringCerts.Count
    $Result.ExpiredCount = $Result.ExpiredCerts.Count

    # Also check for self-signed certs in production (potential issue)
    $SelfSigned = Get-ChildItem -Path "Cert:\LocalMachine\My" -ErrorAction SilentlyContinue |
        Where-Object { $_.Subject -eq $_.Issuer -and $_.HasPrivateKey }
    $Result.SelfSignedCount = @($SelfSigned).Count
    if ($SelfSigned) {
        $Result.Issues += "$(@($SelfSigned).Count) self-signed certificate(s) found in Personal store"
    }

    # Check RDP certificate
    $RDPCertKey = "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"
    $RDPThumbprint = (Get-ItemProperty -Path $RDPCertKey -Name "SSLCertificateSHA1Hash" -ErrorAction SilentlyContinue).SSLCertificateSHA1Hash
    if ($RDPThumbprint) {
        $RDPCertHex = ($RDPThumbprint | ForEach-Object { "{0:X2}" -f $_ }) -join ""
        $RDPCert = Get-ChildItem -Path "Cert:\LocalMachine\My\$RDPCertHex" -ErrorAction SilentlyContinue
        if ($RDPCert -and $RDPCert.NotAfter -lt $Now.AddDays($WarningDays)) {
            $Result.Issues += "RDP certificate expiring in $([math]::Round(($RDPCert.NotAfter - $Now).TotalDays, 0)) days"
        }
    }
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Drifted = $true
}

$Result | ConvertTo-Json -Depth 3
''',

    remediate_script=r'''
# Certificate expiry remediation - ALERT ONLY
# Certificate renewal requires CA infrastructure and cannot be auto-remediated
$Result = @{
    Action = "ALERT"
    Success = $false
    Actions = @()
    Report = @()
}

try {
    $Now = Get-Date
    $WarningDays = 30

    # Generate comprehensive report of certificate issues
    $Stores = @("My", "WebHosting", "Remote Desktop")

    foreach ($StoreName in $Stores) {
        $Certs = Get-ChildItem -Path "Cert:\LocalMachine\$StoreName" -ErrorAction SilentlyContinue

        foreach ($Cert in $Certs) {
            $DaysLeft = [math]::Round(($Cert.NotAfter - $Now).TotalDays, 0)

            if ($DaysLeft -lt $WarningDays) {
                $ReportItem = @{
                    Subject = $Cert.Subject
                    Thumbprint = $Cert.Thumbprint
                    Issuer = $Cert.Issuer
                    ExpiryDate = $Cert.NotAfter.ToString("yyyy-MM-dd HH:mm:ss")
                    DaysRemaining = $DaysLeft
                    Store = $StoreName
                    Status = if ($DaysLeft -lt 0) { "EXPIRED" } else { "EXPIRING" }
                }

                # Determine renewal recommendation
                if ($Cert.Subject -eq $Cert.Issuer) {
                    $ReportItem.Recommendation = "Self-signed certificate - regenerate or replace with CA-issued cert"
                } elseif ($Cert.Issuer -like "*Let's Encrypt*") {
                    $ReportItem.Recommendation = "Let's Encrypt certificate - check auto-renewal (certbot renew)"
                } elseif ($Cert.Issuer -like "*DigiCert*" -or $Cert.Issuer -like "*Comodo*" -or $Cert.Issuer -like "*GoDaddy*") {
                    $ReportItem.Recommendation = "Commercial CA certificate - submit renewal request to CA"
                } else {
                    $ReportItem.Recommendation = "Internal CA certificate - request renewal from PKI administrator"
                }

                $Result.Report += $ReportItem
            }
        }
    }

    # Remove any expired self-signed certs that are not in use (cleanup)
    $ExpiredSelfSigned = Get-ChildItem -Path "Cert:\LocalMachine\My" -ErrorAction SilentlyContinue |
        Where-Object { $_.NotAfter -lt $Now -and $_.Subject -eq $_.Issuer }

    foreach ($ExpCert in $ExpiredSelfSigned) {
        # Only log, don't delete - some may still be referenced
        $Result.Actions += "Found expired self-signed cert: $($ExpCert.Subject) (Thumbprint: $($ExpCert.Thumbprint))"
    }

    $Result.CertsRequiringAction = $Result.Report.Count
    $Result.Message = if ($Result.Report.Count -gt 0) {
        "$($Result.Report.Count) certificate(s) require attention - see report"
    } else {
        "No certificates require immediate attention"
    }
    $Result.Success = $true
    $Result.Warning = "Certificate renewal requires manual intervention or PKI administrator action"
} catch {
    $Result.Error = $_.Exception.Message
}

$Result | ConvertTo-Json -Depth 3
''',

    verify_script=r'''
# Verify certificate status
try {
    $Now = Get-Date
    $WarningDays = 30

    $AllCerts = Get-ChildItem -Path "Cert:\LocalMachine\My" -ErrorAction SilentlyContinue
    $ExpiringCerts = @($AllCerts | Where-Object { $_.NotAfter -lt $Now.AddDays($WarningDays) })
    $ExpiredCerts = @($AllCerts | Where-Object { $_.NotAfter -lt $Now })

    $CertDetails = @($ExpiringCerts | ForEach-Object {
        @{
            Subject = $_.Subject
            ExpiryDate = $_.NotAfter.ToString("yyyy-MM-dd")
            DaysRemaining = [math]::Round(($_.NotAfter - $Now).TotalDays, 0)
        }
    })

    @{
        TotalCerts = @($AllCerts).Count
        ExpiringCount = $ExpiringCerts.Count
        ExpiredCount = $ExpiredCerts.Count
        ExpiringCerts = $CertDetails
        Verified = ($ExpiringCerts.Count -eq 0)
    } | ConvertTo-Json -Depth 3
} catch {
    @{ Verified = $false; Error = $_.Exception.Message } | ConvertTo-Json
}
''',

    timeout_seconds=120,
    requires_reboot=False,
    disruptive=False,
    evidence_fields=["TotalCertsChecked", "ExpiringCount", "ExpiredCount", "ExpiringCerts", "ExpiredCerts", "SelfSignedCount", "Issues"]
)


# =============================================================================
# Certificate Runbooks Registry
# =============================================================================

CERT_RUNBOOKS: Dict[str, WindowsRunbook] = {
    "RB-WIN-CERT-001": RUNBOOK_CERT_EXPIRY,
}
