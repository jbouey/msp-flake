"""Public check definitions catalog.

Publishes what OsirisCare checks and which HIPAA controls each maps to.
Does NOT expose remediation logic, scripts, or implementation details.
"""
from fastapi import APIRouter, Query
from typing import Optional

router = APIRouter(prefix="/api/check-catalog", tags=["check-catalog"])

# Check definitions: name, description, platform, hipaa_controls, pass_criteria
# This is the public "what" -- the "how" stays in the Go daemon.
CHECK_CATALOG = [
    # =========================================================================
    # Windows checks (25 checks from driftscan.go evaluateWindowsFindings)
    # =========================================================================
    {
        "check_type": "firewall_status",
        "name": "Windows Firewall",
        "description": "Verifies Windows Firewall is enabled on all network profiles (Domain, Private, Public)",
        "platform": "windows",
        "hipaa_controls": ["164.312(a)(1)"],
        "pass_criteria": "All three firewall profiles enabled",
        "severity": "high",
    },
    {
        "check_type": "windows_defender",
        "name": "Windows Defender",
        "description": "Verifies the Windows Defender antimalware service is running",
        "platform": "windows",
        "hipaa_controls": ["164.308(a)(5)(ii)(B)"],
        "pass_criteria": "WinDefend service status is Running",
        "severity": "high",
    },
    {
        "check_type": "windows_update",
        "name": "Windows Update Service",
        "description": "Verifies the Windows Update service (wuauserv) is running to ensure patches can be applied",
        "platform": "windows",
        "hipaa_controls": ["164.308(a)(5)(ii)(A)"],
        "pass_criteria": "wuauserv service status is Running",
        "severity": "medium",
    },
    {
        "check_type": "audit_logging",
        "name": "Windows Event Log Service",
        "description": "Verifies the Windows Event Log service is running for HIPAA-required audit trail integrity",
        "platform": "windows",
        "hipaa_controls": ["164.312(b)"],
        "pass_criteria": "EventLog service status is Running",
        "severity": "critical",
    },
    {
        "check_type": "rogue_admin_users",
        "name": "Rogue Local Administrators",
        "description": "Detects unexpected user accounts in the local Administrators group beyond standard defaults (Administrator, Domain Admins, Enterprise Admins)",
        "platform": "windows",
        "hipaa_controls": ["164.312(a)(1)"],
        "pass_criteria": "No unexpected users in local Administrators group",
        "severity": "critical",
    },
    {
        "check_type": "rogue_scheduled_tasks",
        "name": "Rogue Scheduled Tasks",
        "description": "Detects non-standard scheduled tasks outside the Microsoft\\Windows\\ path that may indicate unauthorized software or persistence mechanisms",
        "platform": "windows",
        "hipaa_controls": ["164.308(a)(1)(ii)(D)"],
        "pass_criteria": "No unexpected scheduled tasks outside standard Microsoft paths",
        "severity": "high",
    },
    {
        "check_type": "agent_status",
        "name": "OsirisCare Agent Status",
        "description": "Verifies the OsirisCare compliance agent service is running on managed workstations",
        "platform": "windows",
        "hipaa_controls": [],
        "pass_criteria": "OsirisCareAgent service status is Running",
        "severity": "medium",
    },
    {
        "check_type": "bitlocker_status",
        "name": "BitLocker Drive Encryption",
        "description": "Verifies BitLocker full-disk encryption is enabled and protecting all mounted volumes for HIPAA encryption-at-rest requirements",
        "platform": "windows",
        "hipaa_controls": ["164.312(a)(2)(iv)"],
        "pass_criteria": "All mounted volumes report ProtectionStatus = On",
        "severity": "critical",
    },
    {
        "check_type": "smb_signing",
        "name": "SMB Signing",
        "description": "Verifies SMB packet signing is required to prevent man-in-the-middle attacks on file shares",
        "platform": "windows",
        "hipaa_controls": ["164.312(e)(2)(ii)"],
        "pass_criteria": "RequireSecuritySignature is True on SMB server configuration",
        "severity": "high",
    },
    {
        "check_type": "smb1_protocol",
        "name": "SMB1 Protocol",
        "description": "Verifies the legacy SMBv1 protocol is disabled to prevent exploitation via known vulnerabilities (EternalBlue, WannaCry)",
        "platform": "windows",
        "hipaa_controls": ["164.312(e)(1)"],
        "pass_criteria": "SMB1 protocol disabled",
        "severity": "high",
    },
    {
        "check_type": "screen_lock_policy",
        "name": "Screen Lock / Inactivity Timeout",
        "description": "Verifies a screen lock inactivity timeout is configured at 15 minutes or less per HIPAA workstation security requirements",
        "platform": "windows",
        "hipaa_controls": ["164.312(a)(2)(iii)"],
        "pass_criteria": "Inactivity timeout configured and <= 900 seconds",
        "severity": "medium",
    },
    {
        "check_type": "defender_exclusions",
        "name": "Defender Exclusions Review",
        "description": "Flags any configured Windows Defender exclusions (paths, processes, extensions) that reduce antimalware coverage and require periodic review",
        "platform": "windows",
        "hipaa_controls": ["164.308(a)(5)(ii)(B)"],
        "pass_criteria": "No Defender exclusions configured",
        "severity": "medium",
    },
    {
        "check_type": "dns_config",
        "name": "DNS Configuration",
        "description": "Detects suspicious or unknown DNS server configurations that could indicate DNS hijacking or unauthorized resolver changes",
        "platform": "windows",
        "hipaa_controls": ["164.312(e)(1)"],
        "pass_criteria": "All DNS servers are known trusted resolvers or internal domain controllers",
        "severity": "critical",
    },
    {
        "check_type": "network_profile",
        "name": "Network Profile",
        "description": "Verifies domain-joined workstations are on the DomainAuthenticated network profile, not Public, which applies weaker firewall rules",
        "platform": "windows",
        "hipaa_controls": ["164.312(e)(1)"],
        "pass_criteria": "Network profile is DomainAuthenticated (not Public)",
        "severity": "medium",
    },
    {
        "check_type": "password_policy",
        "name": "Password Policy",
        "description": "Verifies domain password policy meets minimum requirements: at least 8 characters minimum length and a non-zero account lockout threshold",
        "platform": "windows",
        "hipaa_controls": ["164.312(d)"],
        "pass_criteria": "Minimum password length >= 8 and lockout threshold > 0",
        "severity": "high",
    },
    {
        "check_type": "rdp_nla",
        "name": "RDP Network Level Authentication",
        "description": "Verifies Network Level Authentication is enabled for Remote Desktop connections, requiring authentication before session establishment",
        "platform": "windows",
        "hipaa_controls": ["164.312(d)"],
        "pass_criteria": "UserAuthentication registry value is 1 (NLA enabled)",
        "severity": "high",
    },
    {
        "check_type": "guest_account",
        "name": "Guest Account",
        "description": "Verifies the built-in Guest account is disabled to prevent unauthorized anonymous access",
        "platform": "windows",
        "hipaa_controls": ["164.312(a)(1)"],
        "pass_criteria": "Guest account is Disabled",
        "severity": "high",
    },
    {
        "check_type": "service_dns",
        "name": "AD DNS Service",
        "description": "Verifies the Active Directory DNS service is running on domain controllers for name resolution and domain functionality",
        "platform": "windows",
        "hipaa_controls": ["164.312(a)(1)"],
        "pass_criteria": "DNS service status is Running (DC only)",
        "severity": "critical",
    },
    {
        "check_type": "service_netlogon",
        "name": "AD Netlogon Service",
        "description": "Verifies the Netlogon service is running on domain controllers for secure channel authentication between domain members",
        "platform": "windows",
        "hipaa_controls": ["164.312(a)(1)"],
        "pass_criteria": "Netlogon service status is Running (DC only)",
        "severity": "critical",
    },
    {
        "check_type": "wmi_event_persistence",
        "name": "WMI Event Subscription Persistence",
        "description": "Detects unauthorized WMI event subscriptions that can serve as a fileless persistence mechanism for malware",
        "platform": "windows",
        "hipaa_controls": ["164.308(a)(5)(ii)(C)"],
        "pass_criteria": "No unexpected WMI event filter subscriptions",
        "severity": "critical",
    },
    {
        "check_type": "registry_run_persistence",
        "name": "Registry Run Key Persistence",
        "description": "Detects unexpected entries in HKLM Run/RunOnce registry keys that execute programs at logon, a common malware persistence technique",
        "platform": "windows",
        "hipaa_controls": ["164.308(a)(5)(ii)(C)"],
        "pass_criteria": "No unexpected registry Run key entries",
        "severity": "high",
    },
    {
        "check_type": "audit_policy",
        "name": "Windows Audit Policy",
        "description": "Verifies HIPAA-critical audit policy subcategories (Logon, Account Lockout, Process Creation, Security Group Management, etc.) are set to audit both Success and Failure events",
        "platform": "windows",
        "hipaa_controls": ["164.312(b)"],
        "pass_criteria": "All 12 critical audit subcategories have auditing enabled (not No Auditing)",
        "severity": "critical",
    },
    {
        "check_type": "defender_cloud_protection",
        "name": "Defender Cloud Protection",
        "description": "Verifies Windows Defender cloud-delivered protection features are enabled: real-time protection, MAPS reporting, and sample submission",
        "platform": "windows",
        "hipaa_controls": ["164.308(a)(5)(ii)(B)"],
        "pass_criteria": "Real-time protection, cloud protection (MAPS), and sample submission all enabled",
        "severity": "high",
    },
    {
        "check_type": "spooler_service",
        "name": "Print Spooler Service",
        "description": "Verifies the Print Spooler service is stopped on domain controllers to mitigate PrintNightmare (CVE-2021-34527) attack surface",
        "platform": "windows",
        "hipaa_controls": ["164.312(e)(1)"],
        "pass_criteria": "Spooler service is Stopped on domain controllers",
        "severity": "medium",
    },
    {
        "check_type": "firewall_dangerous_rules",
        "name": "Dangerous Inbound Firewall Rules",
        "description": "Detects overly permissive inbound firewall rules such as Allow All, open Telnet/FTP, or rules exposing risky ports (21, 23, 69, 445, 4444) to any source",
        "platform": "windows",
        "hipaa_controls": ["164.312(e)(1)"],
        "pass_criteria": "No dangerous inbound firewall rules matching known risky patterns",
        "severity": "high",
    },
    {
        "check_type": "backup_verification",
        "name": "Windows Backup Verification",
        "description": "Verifies that backup systems (VSS shadow copies, Windows Server Backup, or System Restore) have a recent backup within 7 days",
        "platform": "windows",
        "hipaa_controls": ["164.308(a)(7)(ii)(A)"],
        "pass_criteria": "Most recent backup is less than 7 days old",
        "severity": "medium",
    },
    {
        "check_type": "backup_not_configured",
        "name": "Backup Not Configured",
        "description": "Detects systems with no backup software installed or configured (VSS, Windows Server Backup, or System Restore)",
        "platform": "windows",
        "hipaa_controls": ["164.308(a)(7)(ii)(A)"],
        "pass_criteria": "At least one backup mechanism is installed and configured",
        "severity": "low",
    },
    {
        "check_type": "credential_ip_mismatch",
        "name": "Credential IP Mismatch",
        "description": "Detects when a device's stored credential IP address differs from the IP discovered via ARP/network scan, indicating a possible DHCP change or stale credential",
        "platform": "windows",
        "hipaa_controls": ["164.312(a)(1)"],
        "pass_criteria": "Stored credential IP matches the discovered network IP",
        "severity": "medium",
    },
    {
        "check_type": "device_unreachable",
        "name": "Device Unreachable",
        "description": "Reports when a managed device cannot be reached via WinRM for compliance scanning, indicating a network, firewall, or configuration issue",
        "platform": "windows",
        "hipaa_controls": ["164.312(a)(1)"],
        "pass_criteria": "Device responds to WinRM scan within timeout",
        "severity": "high",
    },
    # =========================================================================
    # Linux checks (17 checks from linuxscan.go parseLinuxFindings)
    # =========================================================================
    {
        "check_type": "linux_firewall",
        "name": "Linux Firewall",
        "description": "Verifies that a host-based firewall (nftables, iptables, or ufw) is active with at least one rule configured",
        "platform": "linux",
        "hipaa_controls": ["164.312(e)(1)"],
        "pass_criteria": "Firewall active with > 0 rules",
        "severity": "high",
    },
    {
        "check_type": "linux_ssh_config",
        "name": "SSH Hardening",
        "description": "Verifies SSH daemon is hardened: root login disabled (PermitRootLogin no/prohibit-password) and password authentication disabled in favor of key-based auth",
        "platform": "linux",
        "hipaa_controls": ["164.312(a)(2)(i)"],
        "pass_criteria": "PermitRootLogin is not 'yes' and PasswordAuthentication is not 'yes'",
        "severity": "high",
    },
    {
        "check_type": "linux_failed_services",
        "name": "Failed Systemd Services",
        "description": "Detects systemd services in a failed state that may indicate broken system components or security service failures",
        "platform": "linux",
        "hipaa_controls": ["164.308(a)(5)(ii)(B)"],
        "pass_criteria": "Zero systemd units in failed state",
        "severity": "medium",
    },
    {
        "check_type": "linux_disk_space",
        "name": "Linux Disk Space",
        "description": "Monitors mounted filesystem usage and flags any partition exceeding 90% capacity, which can cause service failures and log loss",
        "platform": "linux",
        "hipaa_controls": ["164.308(a)(7)(ii)(A)"],
        "pass_criteria": "All mounted filesystems below 90% usage",
        "severity": "medium",
    },
    {
        "check_type": "linux_suid_binaries",
        "name": "Unexpected SUID Binaries",
        "description": "Detects setuid binaries outside the known safe list (sudo, passwd, mount, etc.) that could be used for privilege escalation",
        "platform": "linux",
        "hipaa_controls": ["164.312(a)(1)"],
        "pass_criteria": "No unexpected SUID binaries found outside known safe list",
        "severity": "high",
    },
    {
        "check_type": "linux_audit_logging",
        "name": "Linux Audit Logging",
        "description": "Verifies persistent audit logging is configured via auditd or persistent journald storage for HIPAA audit trail requirements",
        "platform": "linux",
        "hipaa_controls": ["164.312(b)"],
        "pass_criteria": "auditd active or journald configured with persistent storage",
        "severity": "critical",
    },
    {
        "check_type": "linux_ntp_sync",
        "name": "Linux NTP Synchronization",
        "description": "Verifies system clock is synchronized via NTP (timedatectl or chrony) for accurate audit log timestamps",
        "platform": "linux",
        "hipaa_controls": ["164.312(b)"],
        "pass_criteria": "NTP synchronized = yes",
        "severity": "medium",
    },
    {
        "check_type": "linux_kernel_params",
        "name": "Kernel Security Parameters",
        "description": "Verifies security-critical sysctl settings: IP forwarding disabled, SYN cookies enabled, ICMP redirects rejected, and reverse path filtering active",
        "platform": "linux",
        "hipaa_controls": ["164.312(e)(1)"],
        "pass_criteria": "ip_forward=0, tcp_syncookies=1, accept_redirects=0",
        "severity": "medium",
    },
    {
        "check_type": "linux_open_ports",
        "name": "Linux Open Ports",
        "description": "Detects externally listening TCP ports (excluding localhost) and flags when more than 5 are open, indicating excessive network exposure",
        "platform": "linux",
        "hipaa_controls": ["164.312(e)(1)"],
        "pass_criteria": "5 or fewer externally listening TCP ports",
        "severity": "medium",
    },
    {
        "check_type": "linux_user_accounts",
        "name": "Linux User Accounts",
        "description": "Detects unexpected local user accounts with login shells (UID >= 1000) that are not part of the expected user baseline",
        "platform": "linux",
        "hipaa_controls": ["164.312(a)(1)"],
        "pass_criteria": "No unexpected user accounts with interactive login shells",
        "severity": "high",
    },
    {
        "check_type": "linux_file_permissions",
        "name": "Critical File Permissions",
        "description": "Verifies correct permissions on sensitive system files: /etc/shadow and /etc/gshadow (600/640), /etc/passwd and /etc/group (644)",
        "platform": "linux",
        "hipaa_controls": ["164.312(a)(1)"],
        "pass_criteria": "shadow files mode 600/640, passwd/group files mode 644",
        "severity": "high",
    },
    {
        "check_type": "linux_unattended_upgrades",
        "name": "Linux Automatic Updates",
        "description": "Verifies automatic security updates are configured via NixOS upgrade timer, unattended-upgrades, or dnf-automatic",
        "platform": "linux",
        "hipaa_controls": ["164.308(a)(5)(ii)(A)"],
        "pass_criteria": "An automatic update mechanism is active",
        "severity": "medium",
    },
    {
        "check_type": "linux_log_forwarding",
        "name": "Linux Log Forwarding",
        "description": "Verifies centralized log forwarding is configured via rsyslog, systemd-journal-upload, or persistent journald for off-host log retention",
        "platform": "linux",
        "hipaa_controls": ["164.312(b)"],
        "pass_criteria": "At least one log forwarding mechanism configured",
        "severity": "low",
    },
    {
        "check_type": "linux_cron_review",
        "name": "Linux Cron Job Review",
        "description": "Inventories non-system cron jobs for periodic review to detect unauthorized scheduled tasks or persistence mechanisms",
        "platform": "linux",
        "hipaa_controls": ["164.308(a)(1)(ii)(D)"],
        "pass_criteria": "All cron jobs reviewed and accounted for",
        "severity": "low",
    },
    {
        "check_type": "linux_cert_expiry",
        "name": "Linux Certificate Expiry",
        "description": "Checks TLS certificates in standard locations (/etc/ssl/certs/, /var/lib/msp/ca/) for upcoming expiration within 30 days",
        "platform": "linux",
        "hipaa_controls": ["164.312(e)(2)(ii)"],
        "pass_criteria": "All TLS certificates valid for at least 30 more days",
        "severity": "high",
    },
    {
        "check_type": "linux_backup_status",
        "name": "Linux Backup Status",
        "description": "Verifies backup recency by checking restic, borg, rsnapshot, or generic backup tools for a successful backup within the last 7 days",
        "platform": "linux",
        "hipaa_controls": ["164.308(a)(7)(ii)(A)"],
        "pass_criteria": "Most recent backup is less than 7 days old",
        "severity": "medium",
    },
    {
        "check_type": "linux_encryption",
        "name": "Linux Disk Encryption",
        "description": "Verifies all mounted data volumes are on LUKS/dm-crypt encrypted partitions for HIPAA encryption-at-rest compliance (boot partitions excluded)",
        "platform": "linux",
        "hipaa_controls": ["164.312(a)(2)(iv)"],
        "pass_criteria": "All data volumes sit on LUKS-encrypted block devices",
        "severity": "critical",
    },
    # =========================================================================
    # macOS checks (12 checks from macosscan.go parseMacOSFindings)
    # =========================================================================
    {
        "check_type": "macos_filevault",
        "name": "FileVault Disk Encryption",
        "description": "Verifies FileVault full-disk encryption is enabled for HIPAA encryption-at-rest requirements on macOS",
        "platform": "macos",
        "hipaa_controls": ["164.312(a)(2)(iv)"],
        "pass_criteria": "FileVault status is On",
        "severity": "critical",
    },
    {
        "check_type": "macos_gatekeeper",
        "name": "Gatekeeper",
        "description": "Verifies macOS Gatekeeper is enabled to enforce code signing and notarization requirements for downloaded applications",
        "platform": "macos",
        "hipaa_controls": ["164.308(a)(5)(ii)(A)"],
        "pass_criteria": "Gatekeeper assessments enabled",
        "severity": "high",
    },
    {
        "check_type": "macos_sip",
        "name": "System Integrity Protection",
        "description": "Verifies macOS System Integrity Protection (SIP) is enabled to prevent unauthorized modification of protected system files and processes",
        "platform": "macos",
        "hipaa_controls": ["164.312(a)(1)"],
        "pass_criteria": "SIP status is enabled",
        "severity": "critical",
    },
    {
        "check_type": "macos_firewall",
        "name": "macOS Application Firewall",
        "description": "Verifies the macOS Application Firewall (socketfilterfw) is enabled to control inbound network connections",
        "platform": "macos",
        "hipaa_controls": ["164.312(e)(1)"],
        "pass_criteria": "Application Firewall global state is enabled",
        "severity": "high",
    },
    {
        "check_type": "macos_auto_update",
        "name": "macOS Automatic Updates",
        "description": "Verifies macOS automatic software update checking is enabled via the AutomaticCheckEnabled preference",
        "platform": "macos",
        "hipaa_controls": ["164.308(a)(5)(ii)(A)"],
        "pass_criteria": "AutomaticCheckEnabled is 1",
        "severity": "medium",
    },
    {
        "check_type": "macos_screen_lock",
        "name": "macOS Screen Lock",
        "description": "Verifies screen lock is enabled with password required on wake and a delay of 5 seconds or less per HIPAA workstation security requirements",
        "platform": "macos",
        "hipaa_controls": ["164.310(b)"],
        "pass_criteria": "askForPassword=1 and askForPasswordDelay <= 5 seconds",
        "severity": "medium",
    },
    {
        "check_type": "macos_file_sharing",
        "name": "macOS File Sharing (SMB)",
        "description": "Verifies macOS SMB file sharing (smbd) is disabled to reduce network attack surface on endpoints",
        "platform": "macos",
        "hipaa_controls": [],
        "pass_criteria": "SMB file sharing service is not running",
        "severity": "medium",
    },
    {
        "check_type": "macos_time_machine",
        "name": "Time Machine Backup",
        "description": "Verifies Time Machine has a recent backup within 7 days, the backup disk is accessible, and backup integrity checks pass",
        "platform": "macos",
        "hipaa_controls": ["164.308(a)(7)(ii)(A)"],
        "pass_criteria": "Backup within 7 days, disk accessible, integrity verified",
        "severity": "medium",
    },
    {
        "check_type": "macos_ntp_sync",
        "name": "macOS NTP Synchronization",
        "description": "Verifies the system clock is synchronized via network time for accurate audit log timestamps",
        "platform": "macos",
        "hipaa_controls": [],
        "pass_criteria": "Network time is On",
        "severity": "low",
    },
    {
        "check_type": "macos_admin_users",
        "name": "macOS Admin Users",
        "description": "Flags systems with more than 3 users in the admin group, indicating excessive administrative privilege",
        "platform": "macos",
        "hipaa_controls": ["164.312(a)(1)"],
        "pass_criteria": "3 or fewer admin group members",
        "severity": "high",
    },
    {
        "check_type": "macos_disk_space",
        "name": "macOS Disk Space",
        "description": "Monitors root filesystem usage and flags when capacity exceeds 90%, which can cause system instability and log loss",
        "platform": "macos",
        "hipaa_controls": [],
        "pass_criteria": "Root filesystem below 90% usage",
        "severity": "medium",
    },
    {
        "check_type": "macos_cert_expiry",
        "name": "macOS Certificate Expiry",
        "description": "Checks TLS certificates in standard locations for upcoming expiration within 30 days",
        "platform": "macos",
        "hipaa_controls": ["164.312(e)(2)(ii)"],
        "pass_criteria": "All TLS certificates valid for at least 30 more days",
        "severity": "high",
    },
]


@router.get("/")
async def get_check_catalog(platform: Optional[str] = Query(None, description="Filter by platform: windows, linux, macos")):
    """Public check definitions catalog. No auth required.

    Returns the complete list of compliance checks OsirisCare performs,
    including HIPAA control mappings and pass criteria. Remediation
    methodology is proprietary and not included.
    """
    checks = CHECK_CATALOG
    if platform:
        checks = [c for c in checks if c["platform"] == platform]
    return {
        "checks": checks,
        "count": len(checks),
        "platforms": ["windows", "linux", "macos"],
        "note": "This catalog describes what OsirisCare monitors. Remediation methodology is proprietary.",
    }


@router.get("/hipaa-mapping")
async def get_hipaa_mapping():
    """HIPAA control to check mapping. No auth required.

    Returns a mapping from each HIPAA control ID to the checks that
    verify compliance with that control across all platforms.
    """
    mapping: dict = {}
    for check in CHECK_CATALOG:
        for control in check.get("hipaa_controls", []):
            if control not in mapping:
                mapping[control] = []
            mapping[control].append({
                "check_type": check["check_type"],
                "name": check["name"],
                "platform": check["platform"],
                "severity": check["severity"],
            })
    return {
        "controls": mapping,
        "total_checks": len(CHECK_CATALOG),
        "total_controls": len(mapping),
    }


@router.get("/summary")
async def get_catalog_summary():
    """Summary statistics for the check catalog. No auth required."""
    platform_counts: dict = {}
    severity_counts: dict = {}
    for check in CHECK_CATALOG:
        p = check["platform"]
        s = check["severity"]
        platform_counts[p] = platform_counts.get(p, 0) + 1
        severity_counts[s] = severity_counts.get(s, 0) + 1

    controls = set()
    for check in CHECK_CATALOG:
        for c in check.get("hipaa_controls", []):
            controls.add(c)

    return {
        "total_checks": len(CHECK_CATALOG),
        "by_platform": platform_counts,
        "by_severity": severity_counts,
        "unique_hipaa_controls": len(controls),
        "platforms": ["windows", "linux", "macos"],
    }
