"""
Linux Runbook Definitions.

HIPAA-compliant runbooks for Ubuntu and RHEL servers.
Each runbook follows detect → remediate → verify pattern.

Categories:
- SSH Configuration (LIN-SSH-*)
- Firewall (LIN-FW-*)
- Services (LIN-SVC-*)
- Audit (LIN-AUDIT-*)
- Patching (LIN-PATCH-*)
- Encryption (LIN-CRYPT-*)
- Accounts (LIN-ACCT-*)
- Permissions (LIN-PERM-*)
- MAC (LIN-MAC-*) - SELinux/AppArmor
- Kernel Hardening (LIN-KERN-*)
- Logging (LIN-LOG-*)
- Network (LIN-NET-*)
- Boot Security (LIN-BOOT-*)
- Cron (LIN-CRON-*)
- Banner (LIN-BANNER-*)
- Cryptographic Policy (LIN-CRYPTO-*)

Version: 1.0
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any


@dataclass
class LinuxRunbook:
    """Linux runbook definition with distro-specific support."""
    id: str
    name: str
    description: str
    hipaa_controls: List[str]
    check_type: str
    severity: str  # critical, high, medium, low

    # Detection script (bash)
    detect_script: str

    # Remediation scripts
    remediate_script: Optional[str] = None  # Generic
    remediate_ubuntu: Optional[str] = None  # Ubuntu/Debian specific
    remediate_rhel: Optional[str] = None    # RHEL/CentOS specific

    # Verification script (optional, defaults to detect_script)
    verify_script: Optional[str] = None

    # Behavior
    requires_sudo: bool = True
    timeout_seconds: int = 60
    l1_auto_heal: bool = False
    l2_llm_eligible: bool = True

    # Evidence
    capture_pre_state: bool = True
    capture_post_state: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "hipaa_controls": self.hipaa_controls,
            "check_type": self.check_type,
            "severity": self.severity,
            "requires_sudo": self.requires_sudo,
            "l1_auto_heal": self.l1_auto_heal,
        }


# =============================================================================
# SSH CONFIGURATION RUNBOOKS
# =============================================================================

LIN_SSH_001 = LinuxRunbook(
    id="LIN-SSH-001",
    name="SSH Root Login Disabled",
    description="Ensure PermitRootLogin is set to 'no' in sshd_config",
    hipaa_controls=["164.312(d)", "164.312(a)(1)"],
    check_type="ssh_config",
    severity="high",
    detect_script='''
        VALUE=$(grep -E "^PermitRootLogin" /etc/ssh/sshd_config 2>/dev/null | awk '{print $2}')
        if [ "$VALUE" = "no" ]; then
            echo "COMPLIANT"
            exit 0
        else
            echo "DRIFT:PermitRootLogin=$VALUE"
            exit 1
        fi
    ''',
    remediate_script='''
        sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
        if ! grep -q "^PermitRootLogin" /etc/ssh/sshd_config; then
            echo "PermitRootLogin no" >> /etc/ssh/sshd_config
        fi
        systemctl reload sshd
        echo "REMEDIATED"
    ''',
    verify_script='''
        VALUE=$(grep -E "^PermitRootLogin" /etc/ssh/sshd_config | awk '{print $2}')
        if [ "$VALUE" = "no" ]; then
            echo "VERIFIED"
            exit 0
        else
            echo "VERIFY_FAILED"
            exit 1
        fi
    ''',
    l1_auto_heal=True,
    timeout_seconds=30
)

LIN_SSH_002 = LinuxRunbook(
    id="LIN-SSH-002",
    name="SSH Password Authentication Disabled",
    description="Ensure PasswordAuthentication is set to 'no' (use keys only)",
    hipaa_controls=["164.312(d)", "164.312(a)(1)"],
    check_type="ssh_config",
    severity="high",
    detect_script='''
        VALUE=$(grep -E "^PasswordAuthentication" /etc/ssh/sshd_config 2>/dev/null | awk '{print $2}')
        if [ "$VALUE" = "no" ]; then
            echo "COMPLIANT"
            exit 0
        else
            echo "DRIFT:PasswordAuthentication=$VALUE"
            exit 1
        fi
    ''',
    remediate_script='''
        sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
        if ! grep -q "^PasswordAuthentication" /etc/ssh/sshd_config; then
            echo "PasswordAuthentication no" >> /etc/ssh/sshd_config
        fi
        systemctl reload sshd
        echo "REMEDIATED"
    ''',
    l1_auto_heal=True,
    timeout_seconds=30
)

LIN_SSH_003 = LinuxRunbook(
    id="LIN-SSH-003",
    name="SSH Max Auth Tries",
    description="Limit SSH authentication attempts to 3",
    hipaa_controls=["164.312(a)(1)"],
    check_type="ssh_config",
    severity="medium",
    detect_script='''
        VALUE=$(grep -E "^MaxAuthTries" /etc/ssh/sshd_config 2>/dev/null | awk '{print $2}')
        if [ -n "$VALUE" ] && [ "$VALUE" -le 3 ]; then
            echo "COMPLIANT:MaxAuthTries=$VALUE"
            exit 0
        else
            echo "DRIFT:MaxAuthTries=${VALUE:-not_set}"
            exit 1
        fi
    ''',
    remediate_script='''
        sed -i 's/^#*MaxAuthTries.*/MaxAuthTries 3/' /etc/ssh/sshd_config
        if ! grep -q "^MaxAuthTries" /etc/ssh/sshd_config; then
            echo "MaxAuthTries 3" >> /etc/ssh/sshd_config
        fi
        systemctl reload sshd
        echo "REMEDIATED"
    ''',
    l1_auto_heal=True,
    timeout_seconds=30
)


# =============================================================================
# FIREWALL RUNBOOKS
# =============================================================================

LIN_FW_001 = LinuxRunbook(
    id="LIN-FW-001",
    name="Firewall Active",
    description="Ensure firewall (ufw or firewalld) is active",
    hipaa_controls=["164.312(e)(1)"],
    check_type="firewall",
    severity="critical",
    detect_script='''
        # Check ufw (Ubuntu/Debian)
        if command -v ufw &>/dev/null; then
            STATUS=$(ufw status 2>/dev/null | head -1)
            if echo "$STATUS" | grep -q "Status: active"; then
                echo "COMPLIANT:ufw_active"
                exit 0
            fi
        fi
        # Check firewalld (RHEL/CentOS)
        if command -v firewall-cmd &>/dev/null; then
            if firewall-cmd --state 2>/dev/null | grep -q "running"; then
                echo "COMPLIANT:firewalld_active"
                exit 0
            fi
        fi
        # Check iptables as fallback
        if iptables -L -n 2>/dev/null | grep -q "Chain INPUT"; then
            RULES=$(iptables -L -n | wc -l)
            if [ "$RULES" -gt 10 ]; then
                echo "COMPLIANT:iptables_configured"
                exit 0
            fi
        fi
        echo "DRIFT:no_active_firewall"
        exit 1
    ''',
    remediate_ubuntu='''
        apt-get update -qq
        apt-get install -y ufw
        ufw --force enable
        ufw default deny incoming
        ufw default allow outgoing
        ufw allow ssh
        echo "REMEDIATED:ufw_enabled"
    ''',
    remediate_rhel='''
        yum install -y firewalld
        systemctl enable firewalld
        systemctl start firewalld
        firewall-cmd --permanent --add-service=ssh
        firewall-cmd --reload
        echo "REMEDIATED:firewalld_enabled"
    ''',
    l1_auto_heal=True,
    timeout_seconds=120
)


# =============================================================================
# SERVICE RUNBOOKS
# =============================================================================

LIN_SVC_001 = LinuxRunbook(
    id="LIN-SVC-001",
    name="SSH Service Running",
    description="Ensure SSH daemon is running",
    hipaa_controls=["164.312(a)(1)"],
    check_type="services",
    severity="critical",
    detect_script='''
        if systemctl is-active sshd &>/dev/null || systemctl is-active ssh &>/dev/null; then
            echo "COMPLIANT:sshd_running"
            exit 0
        else
            echo "DRIFT:sshd_not_running"
            exit 1
        fi
    ''',
    remediate_ubuntu="systemctl enable ssh && systemctl start ssh && echo REMEDIATED",
    remediate_rhel="systemctl enable sshd && systemctl start sshd && echo REMEDIATED",
    l1_auto_heal=True,
    timeout_seconds=30
)

LIN_SVC_002 = LinuxRunbook(
    id="LIN-SVC-002",
    name="Audit Daemon Running",
    description="Ensure auditd is running for HIPAA audit logs",
    hipaa_controls=["164.312(b)"],
    check_type="services",
    severity="high",
    detect_script='''
        if systemctl is-active auditd &>/dev/null; then
            echo "COMPLIANT:auditd_running"
            exit 0
        else
            echo "DRIFT:auditd_not_running"
            exit 1
        fi
    ''',
    remediate_ubuntu='''
        apt-get update -qq
        apt-get install -y auditd
        systemctl enable auditd
        systemctl start auditd
        echo "REMEDIATED"
    ''',
    remediate_rhel='''
        yum install -y audit
        systemctl enable auditd
        systemctl start auditd
        echo "REMEDIATED"
    ''',
    l1_auto_heal=True,
    timeout_seconds=60
)

LIN_SVC_003 = LinuxRunbook(
    id="LIN-SVC-003",
    name="Rsyslog Running",
    description="Ensure rsyslog is running for system logging",
    hipaa_controls=["164.312(b)"],
    check_type="services",
    severity="high",
    detect_script='''
        if systemctl is-active rsyslog &>/dev/null; then
            echo "COMPLIANT:rsyslog_running"
            exit 0
        else
            echo "DRIFT:rsyslog_not_running"
            exit 1
        fi
    ''',
    remediate_script='''
        systemctl enable rsyslog
        systemctl start rsyslog
        echo "REMEDIATED"
    ''',
    l1_auto_heal=True,
    timeout_seconds=30
)

LIN_SVC_004 = LinuxRunbook(
    id="LIN-SVC-004",
    name="No Telnet Service",
    description="Ensure telnet service is not running (insecure)",
    hipaa_controls=["164.312(e)(1)"],
    check_type="services",
    severity="critical",
    detect_script='''
        if systemctl is-active telnet &>/dev/null || \
           systemctl is-active xinetd &>/dev/null && grep -q "telnet" /etc/xinetd.d/* 2>/dev/null; then
            echo "DRIFT:telnet_running"
            exit 1
        else
            echo "COMPLIANT:no_telnet"
            exit 0
        fi
    ''',
    remediate_script='''
        systemctl stop telnet 2>/dev/null || true
        systemctl disable telnet 2>/dev/null || true
        apt-get remove -y telnetd 2>/dev/null || yum remove -y telnet-server 2>/dev/null || true
        echo "REMEDIATED"
    ''',
    l1_auto_heal=True,
    timeout_seconds=30
)


# =============================================================================
# AUDIT RUNBOOKS
# =============================================================================

LIN_AUDIT_001 = LinuxRunbook(
    id="LIN-AUDIT-001",
    name="Audit Rules for Identity Files",
    description="Audit access to /etc/passwd and /etc/shadow",
    hipaa_controls=["164.312(b)", "164.308(a)(1)(ii)(D)"],
    check_type="audit",
    severity="high",
    detect_script='''
        if auditctl -l 2>/dev/null | grep -q "/etc/passwd" && \
           auditctl -l 2>/dev/null | grep -q "/etc/shadow"; then
            echo "COMPLIANT:identity_files_audited"
            exit 0
        else
            echo "DRIFT:identity_files_not_audited"
            exit 1
        fi
    ''',
    remediate_script='''
        # Add audit rules
        auditctl -w /etc/passwd -p wa -k identity
        auditctl -w /etc/shadow -p wa -k identity
        auditctl -w /etc/group -p wa -k identity
        # Make persistent
        cat >> /etc/audit/rules.d/identity.rules << 'EOF'
-w /etc/passwd -p wa -k identity
-w /etc/shadow -p wa -k identity
-w /etc/group -p wa -k identity
EOF
        echo "REMEDIATED"
    ''',
    l1_auto_heal=True,
    timeout_seconds=30
)

LIN_AUDIT_002 = LinuxRunbook(
    id="LIN-AUDIT-002",
    name="Audit Rules for Auth Logs",
    description="Audit access to authentication logs",
    hipaa_controls=["164.312(b)"],
    check_type="audit",
    severity="high",
    detect_script='''
        if auditctl -l 2>/dev/null | grep -q "auth.log\\|secure"; then
            echo "COMPLIANT:auth_logs_audited"
            exit 0
        else
            echo "DRIFT:auth_logs_not_audited"
            exit 1
        fi
    ''',
    remediate_script='''
        # Ubuntu uses auth.log, RHEL uses secure
        if [ -f /var/log/auth.log ]; then
            auditctl -w /var/log/auth.log -p wa -k auth
            echo "-w /var/log/auth.log -p wa -k auth" >> /etc/audit/rules.d/auth.rules
        fi
        if [ -f /var/log/secure ]; then
            auditctl -w /var/log/secure -p wa -k auth
            echo "-w /var/log/secure -p wa -k auth" >> /etc/audit/rules.d/auth.rules
        fi
        echo "REMEDIATED"
    ''',
    l1_auto_heal=True,
    timeout_seconds=30
)


# =============================================================================
# PATCHING RUNBOOKS
# =============================================================================

LIN_PATCH_001 = LinuxRunbook(
    id="LIN-PATCH-001",
    name="Security Updates Available",
    description="Check for available security updates",
    hipaa_controls=["164.308(a)(5)(ii)(B)"],
    check_type="patching",
    severity="high",
    detect_script='''
        if command -v apt-get &>/dev/null; then
            apt-get update -qq 2>/dev/null
            SECURITY=$(apt-get -s upgrade 2>/dev/null | grep -i security | wc -l)
            if [ "$SECURITY" -eq 0 ]; then
                echo "COMPLIANT:no_security_updates"
                exit 0
            else
                echo "DRIFT:security_updates_available=$SECURITY"
                exit 1
            fi
        elif command -v yum &>/dev/null; then
            SECURITY=$(yum check-update --security 2>/dev/null | grep -v "^$" | wc -l)
            if [ "$SECURITY" -le 1 ]; then
                echo "COMPLIANT:no_security_updates"
                exit 0
            else
                echo "DRIFT:security_updates_available=$SECURITY"
                exit 1
            fi
        fi
        echo "UNKNOWN:no_package_manager"
        exit 1
    ''',
    remediate_ubuntu='''
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold"
        echo "REMEDIATED"
    ''',
    remediate_rhel='''
        yum update -y --security
        echo "REMEDIATED"
    ''',
    l1_auto_heal=False,  # L2 - patching requires human review
    l2_llm_eligible=True,
    timeout_seconds=600
)


# =============================================================================
# PERMISSION RUNBOOKS
# =============================================================================

LIN_PERM_001 = LinuxRunbook(
    id="LIN-PERM-001",
    name="Shadow File Permissions",
    description="Ensure /etc/shadow has correct permissions (0640)",
    hipaa_controls=["164.312(a)(1)", "164.312(c)(1)"],
    check_type="permissions",
    severity="critical",
    detect_script='''
        PERMS=$(stat -c "%a" /etc/shadow 2>/dev/null)
        if [ "$PERMS" = "640" ] || [ "$PERMS" = "600" ] || [ "$PERMS" = "000" ]; then
            echo "COMPLIANT:shadow_perms=$PERMS"
            exit 0
        else
            echo "DRIFT:shadow_perms=$PERMS"
            exit 1
        fi
    ''',
    remediate_script='''
        chmod 640 /etc/shadow
        chown root:shadow /etc/shadow 2>/dev/null || chown root:root /etc/shadow
        echo "REMEDIATED"
    ''',
    l1_auto_heal=True,
    timeout_seconds=15
)

LIN_PERM_002 = LinuxRunbook(
    id="LIN-PERM-002",
    name="SSH Config Permissions",
    description="Ensure /etc/ssh/sshd_config has correct permissions (0600)",
    hipaa_controls=["164.312(a)(1)"],
    check_type="permissions",
    severity="high",
    detect_script='''
        PERMS=$(stat -c "%a" /etc/ssh/sshd_config 2>/dev/null)
        if [ "$PERMS" = "600" ] || [ "$PERMS" = "644" ]; then
            echo "COMPLIANT:sshd_config_perms=$PERMS"
            exit 0
        else
            echo "DRIFT:sshd_config_perms=$PERMS"
            exit 1
        fi
    ''',
    remediate_script='''
        chmod 600 /etc/ssh/sshd_config
        chown root:root /etc/ssh/sshd_config
        echo "REMEDIATED"
    ''',
    l1_auto_heal=True,
    timeout_seconds=15
)

LIN_PERM_003 = LinuxRunbook(
    id="LIN-PERM-003",
    name="No World-Writable Files in /etc",
    description="Ensure no world-writable files exist in /etc",
    hipaa_controls=["164.312(c)(1)"],
    check_type="permissions",
    severity="high",
    detect_script='''
        WW_FILES=$(find /etc -type f -perm -0002 2>/dev/null | head -10)
        if [ -z "$WW_FILES" ]; then
            echo "COMPLIANT:no_world_writable"
            exit 0
        else
            echo "DRIFT:world_writable_found"
            echo "$WW_FILES"
            exit 1
        fi
    ''',
    remediate_script='''
        find /etc -type f -perm -0002 -exec chmod o-w {} \;
        echo "REMEDIATED"
    ''',
    l1_auto_heal=True,
    timeout_seconds=60
)


# =============================================================================
# ACCOUNT RUNBOOKS
# =============================================================================

LIN_ACCT_001 = LinuxRunbook(
    id="LIN-ACCT-001",
    name="No UID 0 Except Root",
    description="Ensure only root has UID 0",
    hipaa_controls=["164.312(a)(1)"],
    check_type="accounts",
    severity="critical",
    detect_script='''
        UID0_USERS=$(awk -F: '$3 == 0 && $1 != "root" {print $1}' /etc/passwd)
        if [ -z "$UID0_USERS" ]; then
            echo "COMPLIANT:only_root_uid0"
            exit 0
        else
            echo "DRIFT:other_uid0_users=$UID0_USERS"
            exit 1
        fi
    ''',
    # No auto-remediation - requires human review
    l1_auto_heal=False,
    l2_llm_eligible=True,
    timeout_seconds=15
)

LIN_ACCT_002 = LinuxRunbook(
    id="LIN-ACCT-002",
    name="Password Expiry Policy",
    description="Ensure password maximum age is set to 90 days",
    hipaa_controls=["164.312(a)(1)", "164.308(a)(5)(ii)(D)"],
    check_type="accounts",
    severity="medium",
    detect_script='''
        MAX_DAYS=$(grep "^PASS_MAX_DAYS" /etc/login.defs | awk '{print $2}')
        if [ -n "$MAX_DAYS" ] && [ "$MAX_DAYS" -le 90 ]; then
            echo "COMPLIANT:pass_max_days=$MAX_DAYS"
            exit 0
        else
            echo "DRIFT:pass_max_days=${MAX_DAYS:-not_set}"
            exit 1
        fi
    ''',
    remediate_script='''
        sed -i 's/^PASS_MAX_DAYS.*/PASS_MAX_DAYS 90/' /etc/login.defs
        if ! grep -q "^PASS_MAX_DAYS" /etc/login.defs; then
            echo "PASS_MAX_DAYS 90" >> /etc/login.defs
        fi
        echo "REMEDIATED"
    ''',
    l1_auto_heal=False,  # Policy change - L2
    l2_llm_eligible=True,
    timeout_seconds=15
)


# =============================================================================
# MAC (Mandatory Access Control) RUNBOOKS
# =============================================================================

LIN_MAC_001 = LinuxRunbook(
    id="LIN-MAC-001",
    name="SELinux/AppArmor Active",
    description="Ensure mandatory access control is enforcing",
    hipaa_controls=["164.312(a)(1)", "164.312(c)(1)"],
    check_type="mac",
    severity="high",
    detect_script='''
        # Check SELinux (RHEL)
        if command -v getenforce &>/dev/null; then
            STATUS=$(getenforce 2>/dev/null)
            if [ "$STATUS" = "Enforcing" ]; then
                echo "COMPLIANT:selinux_enforcing"
                exit 0
            elif [ "$STATUS" = "Permissive" ]; then
                echo "DRIFT:selinux_permissive"
                exit 1
            fi
        fi
        # Check AppArmor (Ubuntu)
        if command -v aa-status &>/dev/null; then
            PROFILES=$(aa-status 2>/dev/null | grep "profiles are loaded" | awk '{print $1}')
            if [ -n "$PROFILES" ] && [ "$PROFILES" -gt 0 ]; then
                echo "COMPLIANT:apparmor_active"
                exit 0
            fi
        fi
        echo "DRIFT:no_mac_active"
        exit 1
    ''',
    remediate_ubuntu='''
        apt-get update -qq
        apt-get install -y apparmor apparmor-utils
        systemctl enable apparmor
        systemctl start apparmor
        echo "REMEDIATED"
    ''',
    remediate_rhel='''
        # Set SELinux to enforcing
        setenforce 1 2>/dev/null || true
        sed -i 's/^SELINUX=.*/SELINUX=enforcing/' /etc/selinux/config
        echo "REMEDIATED:selinux_set_enforcing"
    ''',
    l1_auto_heal=False,  # MAC changes can break apps - L2
    l2_llm_eligible=True,
    timeout_seconds=120
)


# =============================================================================
# TIME SYNCHRONIZATION RUNBOOKS
# =============================================================================

LIN_NTP_001 = LinuxRunbook(
    id="LIN-NTP-001",
    name="NTP Time Synchronization",
    description="Ensure time synchronization is configured and active",
    hipaa_controls=["164.312(b)", "164.308(a)(1)(ii)(D)"],
    check_type="time_sync",
    severity="high",
    detect_script='''
        # Check for chrony or ntpd
        SYNC_STATUS="NOT_CONFIGURED"
        TIME_DRIFT=0

        if command -v chronyc &>/dev/null; then
            if systemctl is-active chronyd &>/dev/null; then
                SYNC_STATUS=$(chronyc tracking 2>/dev/null | grep "Leap status" | awk '{print $4}')
                TIME_DRIFT=$(chronyc tracking 2>/dev/null | grep "System time" | awk '{print $4}')
                if [ "$SYNC_STATUS" = "Normal" ]; then
                    echo "COMPLIANT:chrony_synced,drift=${TIME_DRIFT}s"
                    exit 0
                fi
            fi
        fi

        if command -v ntpq &>/dev/null; then
            if systemctl is-active ntpd &>/dev/null || systemctl is-active ntp &>/dev/null; then
                PEERS=$(ntpq -p 2>/dev/null | grep -c "^\*")
                if [ "$PEERS" -gt 0 ]; then
                    echo "COMPLIANT:ntp_synced,peers=$PEERS"
                    exit 0
                fi
            fi
        fi

        # Check systemd-timesyncd
        if systemctl is-active systemd-timesyncd &>/dev/null; then
            SYNC=$(timedatectl show --property=NTPSynchronized --value 2>/dev/null)
            if [ "$SYNC" = "yes" ]; then
                echo "COMPLIANT:timesyncd_synced"
                exit 0
            fi
        fi

        echo "DRIFT:time_not_synchronized"
        exit 1
    ''',
    remediate_ubuntu='''
        # Install and configure chrony
        apt-get update -qq
        apt-get install -y chrony

        # Configure NTP servers
        cat > /etc/chrony/chrony.conf << 'NTPEOF'
# Use public NTP servers
server 0.pool.ntp.org iburst
server 1.pool.ntp.org iburst
server 2.pool.ntp.org iburst
server 3.pool.ntp.org iburst

# Record the rate at which the system clock gains/losses time.
driftfile /var/lib/chrony/chrony.drift

# Allow system clock to be stepped in first 3 updates
makestep 1.0 3

# Enable kernel synchronization of real-time clock
rtcsync

# Log measurements
logdir /var/log/chrony
NTPEOF

        systemctl enable chronyd
        systemctl restart chronyd

        # Force immediate sync
        chronyc makestep 2>/dev/null || true

        echo "REMEDIATED:chrony_configured"
    ''',
    remediate_rhel='''
        # Install and configure chrony
        yum install -y chrony

        # Configure NTP servers
        cat > /etc/chrony.conf << 'NTPEOF'
server 0.pool.ntp.org iburst
server 1.pool.ntp.org iburst
server 2.pool.ntp.org iburst
server 3.pool.ntp.org iburst
driftfile /var/lib/chrony/drift
makestep 1.0 3
rtcsync
logdir /var/log/chrony
NTPEOF

        systemctl enable chronyd
        systemctl restart chronyd
        chronyc makestep 2>/dev/null || true

        echo "REMEDIATED:chrony_configured"
    ''',
    verify_script='''
        if command -v chronyc &>/dev/null && systemctl is-active chronyd &>/dev/null; then
            SYNC=$(chronyc tracking 2>/dev/null | grep "Leap status" | awk '{print $4}')
            if [ "$SYNC" = "Normal" ]; then
                echo "VERIFIED:chrony_synced"
                exit 0
            fi
        fi
        echo "VERIFY_FAILED"
        exit 1
    ''',
    l1_auto_heal=True,
    timeout_seconds=120
)


# =============================================================================
# INTEGRITY MONITORING RUNBOOKS
# =============================================================================

LIN_INTEGRITY_001 = LinuxRunbook(
    id="LIN-INTEGRITY-001",
    name="File Integrity Monitoring",
    description="Ensure file integrity monitoring (AIDE) is installed and configured",
    hipaa_controls=["164.312(c)(1)", "164.312(c)(2)", "164.312(b)"],
    check_type="integrity",
    severity="high",
    detect_script='''
        # Check for AIDE
        if command -v aide &>/dev/null; then
            if [ -f /var/lib/aide/aide.db ]; then
                # Check if database is recent (within 30 days)
                DB_AGE=$(find /var/lib/aide/aide.db -mtime -30 2>/dev/null | wc -l)
                if [ "$DB_AGE" -gt 0 ]; then
                    echo "COMPLIANT:aide_configured"
                    exit 0
                else
                    echo "DRIFT:aide_db_stale"
                    exit 1
                fi
            else
                echo "DRIFT:aide_db_missing"
                exit 1
            fi
        fi

        # Check for Samhain
        if command -v samhain &>/dev/null; then
            if systemctl is-active samhain &>/dev/null; then
                echo "COMPLIANT:samhain_active"
                exit 0
            fi
        fi

        # Check for OSSEC
        if [ -d /var/ossec ]; then
            if systemctl is-active ossec-hids &>/dev/null; then
                echo "COMPLIANT:ossec_active"
                exit 0
            fi
        fi

        echo "DRIFT:no_integrity_monitoring"
        exit 1
    ''',
    remediate_ubuntu='''
        # Install AIDE
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y aide aide-common

        # Configure AIDE
        cat > /etc/aide/aide.conf.d/99_local.conf << 'AIDEEOF'
# Monitor critical directories
/etc NORMAL
/bin NORMAL
/sbin NORMAL
/usr/bin NORMAL
/usr/sbin NORMAL
/lib NORMAL
/lib64 NORMAL

# Monitor boot files
/boot NORMAL

# Monitor configuration files
!/etc/mtab
!/etc/adjtime

# Log directories (monitor existence, not content changes)
/var/log DIR

# Exclude noisy directories
!/proc
!/sys
!/dev
!/run
!/tmp
!/var/cache
!/var/tmp
AIDEEOF

        # Initialize AIDE database
        aideinit --yes 2>/dev/null || aide --init

        # Move new database to active
        if [ -f /var/lib/aide/aide.db.new ]; then
            mv /var/lib/aide/aide.db.new /var/lib/aide/aide.db
        fi

        # Setup daily cron
        cat > /etc/cron.daily/aide << 'CRONEOF'
#!/bin/bash
/usr/bin/aide --check --config /etc/aide/aide.conf | mail -s "AIDE Report: $(hostname)" root 2>/dev/null || true
CRONEOF
        chmod +x /etc/cron.daily/aide

        echo "REMEDIATED:aide_installed"
    ''',
    remediate_rhel='''
        # Install AIDE
        yum install -y aide

        # Configure AIDE
        cat >> /etc/aide.conf << 'AIDEEOF'
# Additional local monitoring
/etc NORMAL
/bin NORMAL
/sbin NORMAL
/usr/bin NORMAL
/usr/sbin NORMAL
AIDEEOF

        # Initialize AIDE database
        aide --init

        # Move new database to active
        mv /var/lib/aide/aide.db.new.gz /var/lib/aide/aide.db.gz 2>/dev/null || true

        # Setup daily cron
        echo "0 5 * * * root /usr/sbin/aide --check" >> /etc/crontab

        echo "REMEDIATED:aide_installed"
    ''',
    verify_script='''
        if command -v aide &>/dev/null && [ -f /var/lib/aide/aide.db ]; then
            echo "VERIFIED:aide_configured"
            exit 0
        fi
        echo "VERIFY_FAILED"
        exit 1
    ''',
    l1_auto_heal=False,  # FIM setup can be disruptive - L2
    l2_llm_eligible=True,
    timeout_seconds=300
)


# =============================================================================
# INCIDENT RESPONSE RUNBOOKS
# =============================================================================

LIN_IR_001 = LinuxRunbook(
    id="LIN-IR-001",
    name="Incident Response Readiness",
    description="Verify incident response tools and logging are properly configured",
    hipaa_controls=["164.308(a)(6)", "164.312(b)"],
    check_type="incident_response",
    severity="high",
    detect_script='''
        RESULT="COMPLIANT"
        ISSUES=""

        # Check if critical logs exist and are being written
        if [ -f /var/log/auth.log ] || [ -f /var/log/secure ]; then
            LOG_FILE=$(ls /var/log/auth.log /var/log/secure 2>/dev/null | head -1)
            LOG_AGE=$(find "$LOG_FILE" -mmin -60 2>/dev/null | wc -l)
            if [ "$LOG_AGE" -eq 0 ]; then
                RESULT="DRIFT"
                ISSUES="$ISSUES auth_log_stale"
            fi
        else
            RESULT="DRIFT"
            ISSUES="$ISSUES auth_log_missing"
        fi

        # Check if auditd is running
        if ! systemctl is-active auditd &>/dev/null; then
            RESULT="DRIFT"
            ISSUES="$ISSUES auditd_not_running"
        fi

        # Check for log rotation
        if [ ! -f /etc/logrotate.d/rsyslog ] && [ ! -f /etc/logrotate.d/syslog ]; then
            ISSUES="$ISSUES logrotate_not_configured"
        fi

        # Check for remote syslog (optional but recommended)
        if grep -q "^[^#]*@" /etc/rsyslog.conf /etc/rsyslog.d/*.conf 2>/dev/null; then
            echo "INFO: Remote syslog configured"
        fi

        # Check for forensic tools
        TOOLS_INSTALLED=0
        for tool in tcpdump strace lsof netstat ss; do
            if command -v $tool &>/dev/null; then
                TOOLS_INSTALLED=$((TOOLS_INSTALLED + 1))
            fi
        done

        if [ "$TOOLS_INSTALLED" -lt 3 ]; then
            ISSUES="$ISSUES forensic_tools_missing"
        fi

        if [ "$RESULT" = "COMPLIANT" ]; then
            echo "COMPLIANT:ir_ready"
            exit 0
        else
            echo "DRIFT:$ISSUES"
            exit 1
        fi
    ''',
    remediate_ubuntu='''
        apt-get update -qq

        # Install forensic and IR tools
        apt-get install -y \
            tcpdump \
            strace \
            lsof \
            net-tools \
            iproute2 \
            auditd \
            rsyslog

        # Ensure auditd is running
        systemctl enable auditd
        systemctl start auditd

        # Ensure rsyslog is running
        systemctl enable rsyslog
        systemctl start rsyslog

        # Configure log retention (90 days minimum for HIPAA)
        cat > /etc/logrotate.d/hipaa-retention << 'LOGEOF'
/var/log/auth.log
/var/log/syslog
/var/log/messages
{
    rotate 90
    daily
    missingok
    notifempty
    compress
    delaycompress
    sharedscripts
    postrotate
        /usr/lib/rsyslog/rsyslog-rotate 2>/dev/null || true
    endscript
}
LOGEOF

        echo "REMEDIATED:ir_tools_installed"
    ''',
    remediate_rhel='''
        yum install -y \
            tcpdump \
            strace \
            lsof \
            net-tools \
            iproute \
            audit \
            rsyslog

        systemctl enable auditd
        systemctl start auditd
        systemctl enable rsyslog
        systemctl start rsyslog

        # Configure 90-day retention
        sed -i 's/^rotate.*/rotate 90/' /etc/logrotate.conf

        echo "REMEDIATED:ir_tools_installed"
    ''',
    verify_script='''
        OK=true

        if ! systemctl is-active auditd &>/dev/null; then
            OK=false
        fi

        if ! systemctl is-active rsyslog &>/dev/null; then
            OK=false
        fi

        if ! command -v tcpdump &>/dev/null; then
            OK=false
        fi

        if $OK; then
            echo "VERIFIED:ir_ready"
            exit 0
        else
            echo "VERIFY_FAILED"
            exit 1
        fi
    ''',
    l1_auto_heal=True,
    timeout_seconds=180
)


# =============================================================================
# SSH IDLE TIMEOUT RUNBOOKS
# =============================================================================

LIN_SSH_004 = LinuxRunbook(
    id="LIN-SSH-004",
    name="SSH Idle Timeout",
    description="Ensure SSH idle timeout is configured (ClientAliveInterval 300, ClientAliveCountMax 0)",
    hipaa_controls=["164.312(a)(2)(iii)"],
    check_type="ssh_config",
    severity="high",
    detect_script='''
        INTERVAL=$(grep -E "^ClientAliveInterval" /etc/ssh/sshd_config 2>/dev/null | awk '{print $2}')
        COUNTMAX=$(grep -E "^ClientAliveCountMax" /etc/ssh/sshd_config 2>/dev/null | awk '{print $2}')
        if [ -n "$INTERVAL" ] && [ "$INTERVAL" -le 300 ] && [ -n "$COUNTMAX" ] && [ "$COUNTMAX" -eq 0 ]; then
            echo "COMPLIANT:ClientAliveInterval=$INTERVAL,ClientAliveCountMax=$COUNTMAX"
            exit 0
        else
            echo "DRIFT:ClientAliveInterval=${INTERVAL:-not_set},ClientAliveCountMax=${COUNTMAX:-not_set}"
            exit 1
        fi
    ''',
    remediate_script='''
        # Set ClientAliveInterval
        sed -i 's/^#*ClientAliveInterval.*/ClientAliveInterval 300/' /etc/ssh/sshd_config
        if ! grep -q "^ClientAliveInterval" /etc/ssh/sshd_config; then
            echo "ClientAliveInterval 300" >> /etc/ssh/sshd_config
        fi
        # Set ClientAliveCountMax
        sed -i 's/^#*ClientAliveCountMax.*/ClientAliveCountMax 0/' /etc/ssh/sshd_config
        if ! grep -q "^ClientAliveCountMax" /etc/ssh/sshd_config; then
            echo "ClientAliveCountMax 0" >> /etc/ssh/sshd_config
        fi
        systemctl reload sshd
        echo "REMEDIATED"
    ''',
    verify_script='''
        INTERVAL=$(grep -E "^ClientAliveInterval" /etc/ssh/sshd_config | awk '{print $2}')
        COUNTMAX=$(grep -E "^ClientAliveCountMax" /etc/ssh/sshd_config | awk '{print $2}')
        if [ "$INTERVAL" = "300" ] && [ "$COUNTMAX" = "0" ]; then
            echo "VERIFIED"
            exit 0
        else
            echo "VERIFY_FAILED:ClientAliveInterval=$INTERVAL,ClientAliveCountMax=$COUNTMAX"
            exit 1
        fi
    ''',
    l1_auto_heal=True,
    timeout_seconds=30
)


# =============================================================================
# KERNEL HARDENING RUNBOOKS
# =============================================================================

LIN_KERN_001 = LinuxRunbook(
    id="LIN-KERN-001",
    name="Kernel Hardening (sysctl)",
    description="Ensure critical sysctl kernel hardening parameters are set for HIPAA compliance",
    hipaa_controls=["164.312(a)(1)"],
    check_type="kernel",
    severity="high",
    detect_script='''
        DRIFT_PARAMS=""
        COMPLIANT=true

        check_param() {
            PARAM="$1"
            EXPECTED="$2"
            ACTUAL=$(sysctl -n "$PARAM" 2>/dev/null)
            if [ "$ACTUAL" != "$EXPECTED" ]; then
                DRIFT_PARAMS="$DRIFT_PARAMS $PARAM=$ACTUAL(expected=$EXPECTED)"
                COMPLIANT=false
            fi
        }

        check_param "net.ipv4.ip_forward" "0"
        check_param "net.ipv4.conf.all.send_redirects" "0"
        check_param "net.ipv4.conf.all.accept_redirects" "0"
        check_param "kernel.randomize_va_space" "2"
        check_param "fs.suid_dumpable" "0"

        if $COMPLIANT; then
            echo "COMPLIANT:all_sysctl_params_set"
            exit 0
        else
            echo "DRIFT:$DRIFT_PARAMS"
            exit 1
        fi
    ''',
    remediate_script='''
        # Apply immediately
        sysctl -w net.ipv4.ip_forward=0
        sysctl -w net.ipv4.conf.all.send_redirects=0
        sysctl -w net.ipv4.conf.all.accept_redirects=0
        sysctl -w kernel.randomize_va_space=2
        sysctl -w fs.suid_dumpable=0

        # Persist to config file
        cat > /etc/sysctl.d/99-hipaa-hardening.conf << 'SYSCTLEOF'
# HIPAA Kernel Hardening - managed by compliance-agent
# Do not edit manually - changes will be overwritten

# Disable IP forwarding
net.ipv4.ip_forward = 0

# Disable ICMP redirects
net.ipv4.conf.all.send_redirects = 0

# Disable acceptance of ICMP redirects
net.ipv4.conf.all.accept_redirects = 0

# Enable ASLR (full randomization)
kernel.randomize_va_space = 2

# Disable core dumps for SUID programs
fs.suid_dumpable = 0
SYSCTLEOF

        # Reload sysctl
        sysctl --system >/dev/null 2>&1
        echo "REMEDIATED"
    ''',
    verify_script='''
        FAIL=false
        for PARAM in "net.ipv4.ip_forward=0" "net.ipv4.conf.all.send_redirects=0" \
                     "net.ipv4.conf.all.accept_redirects=0" "kernel.randomize_va_space=2" \
                     "fs.suid_dumpable=0"; do
            KEY=$(echo "$PARAM" | cut -d= -f1)
            EXPECTED=$(echo "$PARAM" | cut -d= -f2)
            ACTUAL=$(sysctl -n "$KEY" 2>/dev/null)
            if [ "$ACTUAL" != "$EXPECTED" ]; then
                FAIL=true
            fi
        done
        if $FAIL; then
            echo "VERIFY_FAILED"
            exit 1
        else
            echo "VERIFIED"
            exit 0
        fi
    ''',
    l1_auto_heal=True,
    timeout_seconds=30
)

LIN_KERN_002 = LinuxRunbook(
    id="LIN-KERN-002",
    name="Kernel Module Restrictions",
    description="Ensure dangerous kernel modules (usb-storage, firewire-core, bluetooth) are blacklisted",
    hipaa_controls=["164.310(d)(1)"],
    check_type="kernel",
    severity="medium",
    detect_script='''
        BLACKLIST_FILE="/etc/modprobe.d/hipaa-blacklist.conf"
        MISSING_MODULES=""
        COMPLIANT=true

        for MODULE in usb-storage firewire-core bluetooth; do
            # Check if blocked in any modprobe.d config
            if ! grep -rq "install $MODULE /bin/true" /etc/modprobe.d/ 2>/dev/null && \
               ! grep -rq "install $MODULE /bin/false" /etc/modprobe.d/ 2>/dev/null && \
               ! grep -rq "blacklist $MODULE" /etc/modprobe.d/ 2>/dev/null; then
                MISSING_MODULES="$MISSING_MODULES $MODULE"
                COMPLIANT=false
            fi
        done

        if $COMPLIANT; then
            echo "COMPLIANT:all_modules_blacklisted"
            exit 0
        else
            echo "DRIFT:not_blacklisted=$MISSING_MODULES"
            exit 1
        fi
    ''',
    remediate_script='''
        BLACKLIST_FILE="/etc/modprobe.d/hipaa-blacklist.conf"

        cat > "$BLACKLIST_FILE" << 'MODEOF'
# HIPAA Module Blacklist - managed by compliance-agent
# Do not edit manually - changes will be overwritten

# Disable USB storage (prevent unauthorized data exfiltration)
install usb-storage /bin/true
blacklist usb-storage

# Disable FireWire (prevent DMA attacks)
install firewire-core /bin/true
blacklist firewire-core

# Disable Bluetooth (reduce attack surface in healthcare environments)
install bluetooth /bin/true
blacklist bluetooth
MODEOF

        chmod 644 "$BLACKLIST_FILE"
        echo "REMEDIATED"
    ''',
    verify_script='''
        BLACKLIST_FILE="/etc/modprobe.d/hipaa-blacklist.conf"
        if [ -f "$BLACKLIST_FILE" ]; then
            FOUND=0
            for MODULE in usb-storage firewire-core bluetooth; do
                if grep -q "install $MODULE /bin/true" "$BLACKLIST_FILE"; then
                    FOUND=$((FOUND + 1))
                fi
            done
            if [ "$FOUND" -eq 3 ]; then
                echo "VERIFIED"
                exit 0
            fi
        fi
        echo "VERIFY_FAILED"
        exit 1
    ''',
    l1_auto_heal=True,
    timeout_seconds=30
)


# =============================================================================
# SUDO CONFIGURATION RUNBOOKS
# =============================================================================

LIN_PERM_004 = LinuxRunbook(
    id="LIN-PERM-004",
    name="Sudo Configuration",
    description="Check for NOPASSWD entries in sudoers and verify sudoers file permissions (0440)",
    hipaa_controls=["164.312(a)(1)"],
    check_type="permissions",
    severity="high",
    detect_script='''
        DRIFT_ISSUES=""
        COMPLIANT=true

        # Check for NOPASSWD entries in /etc/sudoers
        NOPASSWD_MAIN=$(grep -c "NOPASSWD" /etc/sudoers 2>/dev/null || echo "0")

        # Check for NOPASSWD entries in /etc/sudoers.d/
        NOPASSWD_DIR=0
        if [ -d /etc/sudoers.d ]; then
            NOPASSWD_DIR=$(grep -rc "NOPASSWD" /etc/sudoers.d/ 2>/dev/null | awk -F: '{sum+=$2} END {print sum+0}')
        fi

        NOPASSWD_TOTAL=$((NOPASSWD_MAIN + NOPASSWD_DIR))
        if [ "$NOPASSWD_TOTAL" -gt 0 ]; then
            DRIFT_ISSUES="$DRIFT_ISSUES nopasswd_entries=$NOPASSWD_TOTAL"
            COMPLIANT=false
        fi

        # Check sudoers file permissions (should be 0440)
        SUDOERS_PERMS=$(stat -c "%a" /etc/sudoers 2>/dev/null)
        if [ "$SUDOERS_PERMS" != "440" ] && [ "$SUDOERS_PERMS" != "400" ]; then
            DRIFT_ISSUES="$DRIFT_ISSUES sudoers_perms=$SUDOERS_PERMS"
            COMPLIANT=false
        fi

        if $COMPLIANT; then
            echo "COMPLIANT:sudoers_secure"
            exit 0
        else
            echo "DRIFT:$DRIFT_ISSUES"
            exit 1
        fi
    ''',
    remediate_script='''
        FIXED=""

        # Fix sudoers file permissions (safe to auto-fix)
        SUDOERS_PERMS=$(stat -c "%a" /etc/sudoers 2>/dev/null)
        if [ "$SUDOERS_PERMS" != "440" ] && [ "$SUDOERS_PERMS" != "400" ]; then
            chmod 440 /etc/sudoers
            chown root:root /etc/sudoers
            FIXED="$FIXED permissions_fixed"
        fi

        # Fix sudoers.d directory permissions
        if [ -d /etc/sudoers.d ]; then
            chmod 750 /etc/sudoers.d
            find /etc/sudoers.d -type f -exec chmod 440 {} \\;
        fi

        # NOPASSWD entries: ALERT ONLY - too risky to auto-remove
        NOPASSWD_MAIN=$(grep -c "NOPASSWD" /etc/sudoers 2>/dev/null || echo "0")
        NOPASSWD_DIR=0
        if [ -d /etc/sudoers.d ]; then
            NOPASSWD_DIR=$(grep -rc "NOPASSWD" /etc/sudoers.d/ 2>/dev/null | awk -F: '{sum+=$2} END {print sum+0}')
        fi
        NOPASSWD_TOTAL=$((NOPASSWD_MAIN + NOPASSWD_DIR))

        if [ "$NOPASSWD_TOTAL" -gt 0 ]; then
            echo "ALERT:nopasswd_entries=$NOPASSWD_TOTAL requires manual review"
            echo "REMEDIATED:partial - permissions fixed, NOPASSWD requires L2 review"
        else
            echo "REMEDIATED"
        fi
    ''',
    verify_script='''
        SUDOERS_PERMS=$(stat -c "%a" /etc/sudoers 2>/dev/null)
        NOPASSWD_MAIN=$(grep -c "NOPASSWD" /etc/sudoers 2>/dev/null || echo "0")
        NOPASSWD_DIR=0
        if [ -d /etc/sudoers.d ]; then
            NOPASSWD_DIR=$(grep -rc "NOPASSWD" /etc/sudoers.d/ 2>/dev/null | awk -F: '{sum+=$2} END {print sum+0}')
        fi
        NOPASSWD_TOTAL=$((NOPASSWD_MAIN + NOPASSWD_DIR))

        echo "sudoers_perms=$SUDOERS_PERMS nopasswd_count=$NOPASSWD_TOTAL"
        if [ "$SUDOERS_PERMS" = "440" ] || [ "$SUDOERS_PERMS" = "400" ]; then
            if [ "$NOPASSWD_TOTAL" -eq 0 ]; then
                echo "VERIFIED"
                exit 0
            else
                echo "VERIFIED:partial - permissions ok, nopasswd=$NOPASSWD_TOTAL requires manual review"
                exit 1
            fi
        fi
        echo "VERIFY_FAILED"
        exit 1
    ''',
    l1_auto_heal=False,  # L2 only - NOPASSWD changes require human review
    l2_llm_eligible=True,
    timeout_seconds=30
)


# =============================================================================
# LOG RETENTION RUNBOOKS
# =============================================================================

LIN_LOG_001 = LinuxRunbook(
    id="LIN-LOG-001",
    name="Log Retention & Forwarding",
    description="Ensure log retention is configured for 90+ days (HIPAA audit trail requirement)",
    hipaa_controls=["164.312(b)"],
    check_type="logging",
    severity="high",
    detect_script='''
        DRIFT_ISSUES=""
        COMPLIANT=true

        # Check journald MaxRetentionSec
        if [ -f /etc/systemd/journald.conf ]; then
            RETENTION=$(grep -E "^MaxRetentionSec" /etc/systemd/journald.conf 2>/dev/null | cut -d= -f2 | tr -d ' ')
            if [ -z "$RETENTION" ]; then
                DRIFT_ISSUES="$DRIFT_ISSUES journald_retention=not_set"
                COMPLIANT=false
            else
                # Convert to seconds for comparison (90 days = 7776000)
                # Handle common suffixes
                case "$RETENTION" in
                    *d|*day|*days)
                        DAYS=$(echo "$RETENTION" | sed 's/[^0-9]//g')
                        if [ "$DAYS" -lt 90 ]; then
                            DRIFT_ISSUES="$DRIFT_ISSUES journald_retention=${DAYS}d"
                            COMPLIANT=false
                        fi
                        ;;
                    *)
                        # Assume seconds
                        SECS=$(echo "$RETENTION" | sed 's/[^0-9]//g')
                        if [ -n "$SECS" ] && [ "$SECS" -lt 7776000 ]; then
                            DRIFT_ISSUES="$DRIFT_ISSUES journald_retention=${SECS}s"
                            COMPLIANT=false
                        fi
                        ;;
                esac
            fi
        else
            DRIFT_ISSUES="$DRIFT_ISSUES journald_conf_missing"
            COMPLIANT=false
        fi

        # Check rsyslog remote forwarding (informational)
        if command -v rsyslogd &>/dev/null; then
            if ! grep -rq "^[^#]*@@\\?" /etc/rsyslog.conf /etc/rsyslog.d/*.conf 2>/dev/null; then
                echo "INFO:no_remote_syslog_forwarding"
            fi
        fi

        # Check logrotate retention
        if [ -f /etc/logrotate.conf ]; then
            ROTATE_COUNT=$(grep -E "^rotate " /etc/logrotate.conf 2>/dev/null | awk '{print $2}')
            if [ -n "$ROTATE_COUNT" ] && [ "$ROTATE_COUNT" -lt 90 ]; then
                DRIFT_ISSUES="$DRIFT_ISSUES logrotate_count=$ROTATE_COUNT"
                COMPLIANT=false
            fi
        fi

        if $COMPLIANT; then
            echo "COMPLIANT:log_retention_adequate"
            exit 0
        else
            echo "DRIFT:$DRIFT_ISSUES"
            exit 1
        fi
    ''',
    remediate_script='''
        # Configure journald retention to 90 days
        JOURNALD_CONF="/etc/systemd/journald.conf"
        if [ -f "$JOURNALD_CONF" ]; then
            sed -i 's/^#*MaxRetentionSec=.*/MaxRetentionSec=7776000/' "$JOURNALD_CONF"
            if ! grep -q "^MaxRetentionSec" "$JOURNALD_CONF"; then
                echo "MaxRetentionSec=7776000" >> "$JOURNALD_CONF"
            fi
            # Also ensure persistent storage
            sed -i 's/^#*Storage=.*/Storage=persistent/' "$JOURNALD_CONF"
            if ! grep -q "^Storage" "$JOURNALD_CONF"; then
                echo "Storage=persistent" >> "$JOURNALD_CONF"
            fi
            systemctl restart systemd-journald
        fi

        # Configure logrotate for 90-day retention
        if [ -f /etc/logrotate.conf ]; then
            sed -i 's/^rotate .*/rotate 90/' /etc/logrotate.conf
        fi

        # Create HIPAA-specific logrotate config
        cat > /etc/logrotate.d/hipaa-retention << 'LOGEOF'
# HIPAA Log Retention - managed by compliance-agent
/var/log/auth.log
/var/log/syslog
/var/log/messages
/var/log/secure
{
    daily
    rotate 90
    missingok
    notifempty
    compress
    delaycompress
    sharedscripts
    postrotate
        /usr/lib/rsyslog/rsyslog-rotate 2>/dev/null || true
        /bin/kill -HUP $(cat /var/run/rsyslogd.pid 2>/dev/null) 2>/dev/null || true
    endscript
}
LOGEOF

        echo "REMEDIATED"
    ''',
    verify_script='''
        JOURNALD_CONF="/etc/systemd/journald.conf"
        RETENTION=$(grep -E "^MaxRetentionSec" "$JOURNALD_CONF" 2>/dev/null | cut -d= -f2 | tr -d ' ')
        STORAGE=$(grep -E "^Storage" "$JOURNALD_CONF" 2>/dev/null | cut -d= -f2 | tr -d ' ')

        if [ "$RETENTION" = "7776000" ] && [ "$STORAGE" = "persistent" ]; then
            echo "VERIFIED:journald_retention=90d,storage=persistent"
            exit 0
        else
            echo "VERIFY_FAILED:retention=$RETENTION,storage=$STORAGE"
            exit 1
        fi
    ''',
    l1_auto_heal=True,
    timeout_seconds=60
)


# =============================================================================
# NETWORK HARDENING RUNBOOKS
# =============================================================================

LIN_NET_001 = LinuxRunbook(
    id="LIN-NET-001",
    name="Network Hardening",
    description="Ensure network sysctl hardening params: SYN cookies, broadcast ICMP, RP filter, martian logging",
    hipaa_controls=["164.312(e)(1)"],
    check_type="network",
    severity="medium",
    detect_script='''
        DRIFT_PARAMS=""
        COMPLIANT=true

        check_param() {
            PARAM="$1"
            EXPECTED="$2"
            ACTUAL=$(sysctl -n "$PARAM" 2>/dev/null)
            if [ "$ACTUAL" != "$EXPECTED" ]; then
                DRIFT_PARAMS="$DRIFT_PARAMS $PARAM=$ACTUAL(expected=$EXPECTED)"
                COMPLIANT=false
            fi
        }

        check_param "net.ipv4.ip_forward" "0"
        check_param "net.ipv4.tcp_syncookies" "1"
        check_param "net.ipv4.icmp_echo_ignore_broadcasts" "1"
        check_param "net.ipv4.conf.all.rp_filter" "1"
        check_param "net.ipv4.conf.all.log_martians" "1"

        if $COMPLIANT; then
            echo "COMPLIANT:all_network_params_set"
            exit 0
        else
            echo "DRIFT:$DRIFT_PARAMS"
            exit 1
        fi
    ''',
    remediate_script='''
        # Apply immediately
        sysctl -w net.ipv4.ip_forward=0
        sysctl -w net.ipv4.tcp_syncookies=1
        sysctl -w net.ipv4.icmp_echo_ignore_broadcasts=1
        sysctl -w net.ipv4.conf.all.rp_filter=1
        sysctl -w net.ipv4.conf.all.log_martians=1

        # Persist to config file
        cat > /etc/sysctl.d/99-hipaa-network.conf << 'SYSCTLEOF'
# HIPAA Network Hardening - managed by compliance-agent
# Do not edit manually - changes will be overwritten

# Disable IP forwarding (not a router)
net.ipv4.ip_forward = 0

# Enable SYN cookies (SYN flood protection)
net.ipv4.tcp_syncookies = 1

# Ignore ICMP broadcast requests
net.ipv4.icmp_echo_ignore_broadcasts = 1

# Enable reverse path filtering (anti-spoofing)
net.ipv4.conf.all.rp_filter = 1

# Log martian packets (impossible source addresses)
net.ipv4.conf.all.log_martians = 1
SYSCTLEOF

        # Reload sysctl
        sysctl --system >/dev/null 2>&1
        echo "REMEDIATED"
    ''',
    verify_script='''
        FAIL=false
        for PARAM in "net.ipv4.ip_forward=0" "net.ipv4.tcp_syncookies=1" \
                     "net.ipv4.icmp_echo_ignore_broadcasts=1" \
                     "net.ipv4.conf.all.rp_filter=1" "net.ipv4.conf.all.log_martians=1"; do
            KEY=$(echo "$PARAM" | cut -d= -f1)
            EXPECTED=$(echo "$PARAM" | cut -d= -f2)
            ACTUAL=$(sysctl -n "$KEY" 2>/dev/null)
            if [ "$ACTUAL" != "$EXPECTED" ]; then
                FAIL=true
            fi
        done
        if $FAIL; then
            echo "VERIFY_FAILED"
            exit 1
        else
            echo "VERIFIED"
            exit 0
        fi
    ''',
    l1_auto_heal=True,
    timeout_seconds=30
)


# =============================================================================
# BOOTLOADER SECURITY RUNBOOKS
# =============================================================================

LIN_BOOT_001 = LinuxRunbook(
    id="LIN-BOOT-001",
    name="Bootloader Security",
    description="Check GRUB bootloader password and grub.cfg file permissions (0600)",
    hipaa_controls=["164.310(a)(1)"],
    check_type="boot",
    severity="medium",
    detect_script='''
        DRIFT_ISSUES=""
        COMPLIANT=true

        # Check GRUB password configuration
        GRUB_PW_SET=false
        if [ -f /etc/grub.d/40_custom ] && grep -q "password_pbkdf2\\|password " /etc/grub.d/40_custom 2>/dev/null; then
            GRUB_PW_SET=true
        fi
        if [ -f /boot/grub2/user.cfg ] && grep -q "GRUB2_PASSWORD" /boot/grub2/user.cfg 2>/dev/null; then
            GRUB_PW_SET=true
        fi
        if [ -f /boot/grub/grub.cfg ] && grep -q "password_pbkdf2\\|password " /boot/grub/grub.cfg 2>/dev/null; then
            GRUB_PW_SET=true
        fi

        if ! $GRUB_PW_SET; then
            DRIFT_ISSUES="$DRIFT_ISSUES grub_password_not_set"
            COMPLIANT=false
        fi

        # Check grub.cfg permissions (should be 0600 or 0400)
        GRUB_CFG=""
        if [ -f /boot/grub/grub.cfg ]; then
            GRUB_CFG="/boot/grub/grub.cfg"
        elif [ -f /boot/grub2/grub.cfg ]; then
            GRUB_CFG="/boot/grub2/grub.cfg"
        fi

        if [ -n "$GRUB_CFG" ]; then
            PERMS=$(stat -c "%a" "$GRUB_CFG" 2>/dev/null)
            if [ "$PERMS" != "600" ] && [ "$PERMS" != "400" ]; then
                DRIFT_ISSUES="$DRIFT_ISSUES grub_cfg_perms=$PERMS"
                COMPLIANT=false
            fi
        fi

        if $COMPLIANT; then
            echo "COMPLIANT:bootloader_secured"
            exit 0
        else
            echo "DRIFT:$DRIFT_ISSUES"
            exit 1
        fi
    ''',
    remediate_script='''
        FIXED=""

        # Fix grub.cfg permissions (safe to auto-fix)
        GRUB_CFG=""
        if [ -f /boot/grub/grub.cfg ]; then
            GRUB_CFG="/boot/grub/grub.cfg"
        elif [ -f /boot/grub2/grub.cfg ]; then
            GRUB_CFG="/boot/grub2/grub.cfg"
        fi

        if [ -n "$GRUB_CFG" ]; then
            chmod 600 "$GRUB_CFG"
            chown root:root "$GRUB_CFG"
            FIXED="$FIXED permissions_fixed"
        fi

        # GRUB password: ALERT ONLY - too risky to auto-configure
        GRUB_PW_SET=false
        if [ -f /etc/grub.d/40_custom ] && grep -q "password_pbkdf2\\|password " /etc/grub.d/40_custom 2>/dev/null; then
            GRUB_PW_SET=true
        fi
        if [ -f /boot/grub2/user.cfg ] && grep -q "GRUB2_PASSWORD" /boot/grub2/user.cfg 2>/dev/null; then
            GRUB_PW_SET=true
        fi

        if ! $GRUB_PW_SET; then
            echo "ALERT:grub_password_not_set requires manual configuration"
            echo "REMEDIATED:partial - permissions fixed, password requires L2 review"
        else
            echo "REMEDIATED"
        fi
    ''',
    verify_script='''
        GRUB_CFG=""
        if [ -f /boot/grub/grub.cfg ]; then
            GRUB_CFG="/boot/grub/grub.cfg"
        elif [ -f /boot/grub2/grub.cfg ]; then
            GRUB_CFG="/boot/grub2/grub.cfg"
        fi

        if [ -n "$GRUB_CFG" ]; then
            PERMS=$(stat -c "%a" "$GRUB_CFG" 2>/dev/null)
            if [ "$PERMS" = "600" ] || [ "$PERMS" = "400" ]; then
                echo "VERIFIED:grub_cfg_perms=$PERMS"
                exit 0
            fi
        fi
        echo "VERIFY_FAILED"
        exit 1
    ''',
    l1_auto_heal=False,  # L2 only - bootloader changes are risky
    l2_llm_eligible=True,
    timeout_seconds=30
)


# =============================================================================
# CRON JOB AUDITING RUNBOOKS
# =============================================================================

LIN_CRON_001 = LinuxRunbook(
    id="LIN-CRON-001",
    name="Cron Job Auditing",
    description="Ensure cron file permissions are secure and cron.allow exists",
    hipaa_controls=["164.308(a)(1)(ii)(D)"],
    check_type="cron",
    severity="medium",
    detect_script='''
        DRIFT_ISSUES=""
        COMPLIANT=true

        # Check /etc/crontab permissions (should be 0600)
        if [ -f /etc/crontab ]; then
            PERMS=$(stat -c "%a" /etc/crontab 2>/dev/null)
            if [ "$PERMS" != "600" ] && [ "$PERMS" != "400" ]; then
                DRIFT_ISSUES="$DRIFT_ISSUES crontab_perms=$PERMS"
                COMPLIANT=false
            fi
        fi

        # Check /etc/cron.d/ permissions
        if [ -d /etc/cron.d ]; then
            PERMS=$(stat -c "%a" /etc/cron.d 2>/dev/null)
            if [ "$PERMS" != "700" ] && [ "$PERMS" != "750" ]; then
                DRIFT_ISSUES="$DRIFT_ISSUES cron_d_perms=$PERMS"
                COMPLIANT=false
            fi
        fi

        # Check cron.allow exists
        if [ ! -f /etc/cron.allow ]; then
            DRIFT_ISSUES="$DRIFT_ISSUES cron_allow_missing"
            COMPLIANT=false
        fi

        # Check for world-writable cron files
        WW_CRON=$(find /etc/cron* -type f -perm -0002 2>/dev/null | head -5)
        if [ -n "$WW_CRON" ]; then
            DRIFT_ISSUES="$DRIFT_ISSUES world_writable_cron_files"
            COMPLIANT=false
        fi

        if $COMPLIANT; then
            echo "COMPLIANT:cron_secured"
            exit 0
        else
            echo "DRIFT:$DRIFT_ISSUES"
            exit 1
        fi
    ''',
    remediate_script='''
        # Fix /etc/crontab permissions
        if [ -f /etc/crontab ]; then
            chmod 600 /etc/crontab
            chown root:root /etc/crontab
        fi

        # Fix /etc/cron.d/ permissions
        if [ -d /etc/cron.d ]; then
            chmod 700 /etc/cron.d
            chown root:root /etc/cron.d
        fi

        # Fix cron.hourly/daily/weekly/monthly permissions
        for DIR in /etc/cron.hourly /etc/cron.daily /etc/cron.weekly /etc/cron.monthly; do
            if [ -d "$DIR" ]; then
                chmod 700 "$DIR"
                chown root:root "$DIR"
            fi
        done

        # Create cron.allow if missing (only root by default)
        if [ ! -f /etc/cron.allow ]; then
            echo "root" > /etc/cron.allow
            chmod 600 /etc/cron.allow
            chown root:root /etc/cron.allow
        fi

        # Remove world-writable permissions from cron files
        find /etc/cron* -type f -perm -0002 -exec chmod o-w {} \\; 2>/dev/null

        echo "REMEDIATED"
    ''',
    verify_script='''
        FAIL=false

        # Check crontab permissions
        if [ -f /etc/crontab ]; then
            PERMS=$(stat -c "%a" /etc/crontab 2>/dev/null)
            if [ "$PERMS" != "600" ] && [ "$PERMS" != "400" ]; then
                FAIL=true
            fi
        fi

        # Check cron.allow
        if [ ! -f /etc/cron.allow ]; then
            FAIL=true
        fi

        # Check no world-writable cron files
        WW_CRON=$(find /etc/cron* -type f -perm -0002 2>/dev/null | head -1)
        if [ -n "$WW_CRON" ]; then
            FAIL=true
        fi

        if $FAIL; then
            echo "VERIFY_FAILED"
            exit 1
        else
            echo "VERIFIED"
            exit 0
        fi
    ''',
    l1_auto_heal=True,
    timeout_seconds=30
)


# =============================================================================
# LOGIN BANNER RUNBOOKS
# =============================================================================

LIN_BANNER_001 = LinuxRunbook(
    id="LIN-BANNER-001",
    name="Login Banner",
    description="Ensure HIPAA authorized-use warning banners are configured in /etc/issue and /etc/issue.net",
    hipaa_controls=["164.310(b)"],
    check_type="banner",
    severity="low",
    detect_script='''
        COMPLIANT=true
        DRIFT_ISSUES=""

        # Check /etc/issue for authorized-use warning
        if [ -f /etc/issue ]; then
            if ! grep -qi "authorized" /etc/issue 2>/dev/null; then
                DRIFT_ISSUES="$DRIFT_ISSUES issue_no_warning"
                COMPLIANT=false
            fi
        else
            DRIFT_ISSUES="$DRIFT_ISSUES issue_missing"
            COMPLIANT=false
        fi

        # Check /etc/issue.net for authorized-use warning
        if [ -f /etc/issue.net ]; then
            if ! grep -qi "authorized" /etc/issue.net 2>/dev/null; then
                DRIFT_ISSUES="$DRIFT_ISSUES issue_net_no_warning"
                COMPLIANT=false
            fi
        else
            DRIFT_ISSUES="$DRIFT_ISSUES issue_net_missing"
            COMPLIANT=false
        fi

        if $COMPLIANT; then
            echo "COMPLIANT:banners_configured"
            exit 0
        else
            echo "DRIFT:$DRIFT_ISSUES"
            exit 1
        fi
    ''',
    remediate_script='''
        BANNER_TEXT="WARNING: This system is for authorized use only.

Access to this system is restricted to authorized users. All activities
on this system are monitored and recorded. Unauthorized access or use
of this system is prohibited and may result in disciplinary action,
civil liability, and/or criminal prosecution.

By continuing to use this system, you indicate your awareness of and
consent to these terms. If you are not an authorized user, disconnect
immediately.

This system processes Protected Health Information (PHI) under HIPAA.
All access is logged for compliance auditing."

        # Set /etc/issue (local console login)
        echo "$BANNER_TEXT" > /etc/issue
        chmod 644 /etc/issue
        chown root:root /etc/issue

        # Set /etc/issue.net (remote/SSH login)
        echo "$BANNER_TEXT" > /etc/issue.net
        chmod 644 /etc/issue.net
        chown root:root /etc/issue.net

        # Ensure SSH uses the banner
        if [ -f /etc/ssh/sshd_config ]; then
            sed -i 's|^#*Banner.*|Banner /etc/issue.net|' /etc/ssh/sshd_config
            if ! grep -q "^Banner" /etc/ssh/sshd_config; then
                echo "Banner /etc/issue.net" >> /etc/ssh/sshd_config
            fi
            systemctl reload sshd 2>/dev/null || true
        fi

        echo "REMEDIATED"
    ''',
    verify_script='''
        FAIL=false

        if [ -f /etc/issue ]; then
            if ! grep -qi "authorized" /etc/issue; then
                FAIL=true
            fi
        else
            FAIL=true
        fi

        if [ -f /etc/issue.net ]; then
            if ! grep -qi "authorized" /etc/issue.net; then
                FAIL=true
            fi
        else
            FAIL=true
        fi

        if $FAIL; then
            echo "VERIFY_FAILED"
            exit 1
        else
            echo "VERIFIED"
            exit 0
        fi
    ''',
    l1_auto_heal=True,
    timeout_seconds=30
)


# =============================================================================
# CRYPTOGRAPHIC POLICY RUNBOOKS
# =============================================================================

LIN_CRYPTO_001 = LinuxRunbook(
    id="LIN-CRYPTO-001",
    name="Cryptographic Policy",
    description="Ensure SSH uses only strong ciphers, MACs, and key exchange algorithms (no weak crypto)",
    hipaa_controls=["164.312(e)(2)(ii)"],
    check_type="crypto",
    severity="high",
    detect_script='''
        SSHD_CONFIG="/etc/ssh/sshd_config"
        DRIFT_ISSUES=""
        COMPLIANT=true

        # Weak cipher patterns to reject
        WEAK_CIPHERS="3des|arcfour|blowfish|cast128|rc4"
        # Weak MAC patterns to reject
        WEAK_MACS="md5|sha1-96|umac-64"
        # Weak KexAlgorithm patterns to reject
        WEAK_KEX="diffie-hellman-group1-sha1|diffie-hellman-group14-sha1|diffie-hellman-group-exchange-sha1"

        # Check configured ciphers
        CIPHERS_LINE=$(grep -E "^Ciphers " "$SSHD_CONFIG" 2>/dev/null | awk '{print $2}')
        if [ -n "$CIPHERS_LINE" ]; then
            if echo "$CIPHERS_LINE" | grep -qiE "$WEAK_CIPHERS"; then
                DRIFT_ISSUES="$DRIFT_ISSUES weak_ciphers_found"
                COMPLIANT=false
            fi
        else
            # No explicit cipher config - check ssh defaults for weak ciphers
            ACTIVE_CIPHERS=$(sshd -T 2>/dev/null | grep "^ciphers " | awk '{print $2}')
            if echo "$ACTIVE_CIPHERS" | grep -qiE "$WEAK_CIPHERS"; then
                DRIFT_ISSUES="$DRIFT_ISSUES weak_default_ciphers"
                COMPLIANT=false
            fi
        fi

        # Check configured MACs
        MACS_LINE=$(grep -E "^MACs " "$SSHD_CONFIG" 2>/dev/null | awk '{print $2}')
        if [ -n "$MACS_LINE" ]; then
            if echo "$MACS_LINE" | grep -qiE "$WEAK_MACS"; then
                DRIFT_ISSUES="$DRIFT_ISSUES weak_macs_found"
                COMPLIANT=false
            fi
        else
            ACTIVE_MACS=$(sshd -T 2>/dev/null | grep "^macs " | awk '{print $2}')
            if echo "$ACTIVE_MACS" | grep -qiE "$WEAK_MACS"; then
                DRIFT_ISSUES="$DRIFT_ISSUES weak_default_macs"
                COMPLIANT=false
            fi
        fi

        # Check configured KexAlgorithms
        KEX_LINE=$(grep -E "^KexAlgorithms " "$SSHD_CONFIG" 2>/dev/null | awk '{print $2}')
        if [ -n "$KEX_LINE" ]; then
            if echo "$KEX_LINE" | grep -qiE "$WEAK_KEX"; then
                DRIFT_ISSUES="$DRIFT_ISSUES weak_kex_found"
                COMPLIANT=false
            fi
        else
            ACTIVE_KEX=$(sshd -T 2>/dev/null | grep "^kexalgorithms " | awk '{print $2}')
            if echo "$ACTIVE_KEX" | grep -qiE "$WEAK_KEX"; then
                DRIFT_ISSUES="$DRIFT_ISSUES weak_default_kex"
                COMPLIANT=false
            fi
        fi

        if $COMPLIANT; then
            echo "COMPLIANT:strong_crypto_only"
            exit 0
        else
            echo "DRIFT:$DRIFT_ISSUES"
            exit 1
        fi
    ''',
    remediate_script='''
        SSHD_CONFIG="/etc/ssh/sshd_config"

        # Strong ciphers only (FIPS 140-2 compatible)
        STRONG_CIPHERS="aes256-gcm@openssh.com,aes128-gcm@openssh.com,aes256-ctr,aes192-ctr,aes128-ctr,chacha20-poly1305@openssh.com"

        # Strong MACs only
        STRONG_MACS="hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com,hmac-sha2-512,hmac-sha2-256"

        # Strong key exchange only
        STRONG_KEX="curve25519-sha256,curve25519-sha256@libssh.org,diffie-hellman-group16-sha512,diffie-hellman-group18-sha512,diffie-hellman-group-exchange-sha256"

        # Set Ciphers
        sed -i 's/^#*Ciphers .*/Ciphers '"$STRONG_CIPHERS"'/' "$SSHD_CONFIG"
        if ! grep -q "^Ciphers " "$SSHD_CONFIG"; then
            echo "Ciphers $STRONG_CIPHERS" >> "$SSHD_CONFIG"
        fi

        # Set MACs
        sed -i 's/^#*MACs .*/MACs '"$STRONG_MACS"'/' "$SSHD_CONFIG"
        if ! grep -q "^MACs " "$SSHD_CONFIG"; then
            echo "MACs $STRONG_MACS" >> "$SSHD_CONFIG"
        fi

        # Set KexAlgorithms
        sed -i 's/^#*KexAlgorithms .*/KexAlgorithms '"$STRONG_KEX"'/' "$SSHD_CONFIG"
        if ! grep -q "^KexAlgorithms " "$SSHD_CONFIG"; then
            echo "KexAlgorithms $STRONG_KEX" >> "$SSHD_CONFIG"
        fi

        # Validate config before reloading
        if sshd -t 2>/dev/null; then
            systemctl reload sshd
            echo "REMEDIATED"
        else
            echo "ERROR:sshd_config_validation_failed"
            exit 1
        fi
    ''',
    verify_script='''
        # Brief pause to ensure sshd has reloaded after remediate
        sleep 2
        WEAK_CIPHERS="3des|arcfour|blowfish|cast128|rc4"
        WEAK_MACS="md5|sha1-96|umac-64"
        WEAK_KEX="diffie-hellman-group1-sha1|diffie-hellman-group14-sha1|diffie-hellman-group-exchange-sha1"
        FAIL=false

        # Use sshd -T to check ACTIVE config (handles Include directives, defaults)
        # Fall back to grepping the config file if sshd -T not available
        ACTIVE_CONFIG=$(sshd -T 2>/dev/null)

        if [ -n "$ACTIVE_CONFIG" ]; then
            CIPHERS=$(echo "$ACTIVE_CONFIG" | grep "^ciphers " | awk '{print $2}')
            if echo "$CIPHERS" | grep -qiE "$WEAK_CIPHERS"; then FAIL=true; fi
            MACS=$(echo "$ACTIVE_CONFIG" | grep "^macs " | awk '{print $2}')
            if echo "$MACS" | grep -qiE "$WEAK_MACS"; then FAIL=true; fi
            KEX=$(echo "$ACTIVE_CONFIG" | grep "^kexalgorithms " | awk '{print $2}')
            if echo "$KEX" | grep -qiE "$WEAK_KEX"; then FAIL=true; fi
        else
            # Fallback: check the config file directly
            SSHD_CONFIG="/etc/ssh/sshd_config"
            CIPHERS=$(grep -E "^Ciphers " "$SSHD_CONFIG" 2>/dev/null | awk '{print $2}')
            if [ -n "$CIPHERS" ] && echo "$CIPHERS" | grep -qiE "$WEAK_CIPHERS"; then FAIL=true; fi
            MACS=$(grep -E "^MACs " "$SSHD_CONFIG" 2>/dev/null | awk '{print $2}')
            if [ -n "$MACS" ] && echo "$MACS" | grep -qiE "$WEAK_MACS"; then FAIL=true; fi
            KEX=$(grep -E "^KexAlgorithms " "$SSHD_CONFIG" 2>/dev/null | awk '{print $2}')
            if [ -n "$KEX" ] && echo "$KEX" | grep -qiE "$WEAK_KEX"; then FAIL=true; fi
        fi

        if $FAIL; then
            echo "VERIFY_FAILED"
            exit 1
        else
            echo "VERIFIED:strong_crypto_configured"
            exit 0
        fi
    ''',
    l1_auto_heal=True,
    timeout_seconds=30
)


# =============================================================================
# SUID BINARY DETECTION RUNBOOKS
# =============================================================================

LIN_SUID_001 = LinuxRunbook(
    id="LIN-SUID-001",
    name="Unauthorized SUID Binary Cleanup",
    description="Detect and remove unauthorized SUID binaries in /tmp and world-writable directories",
    hipaa_controls=["164.312(a)(1)", "164.308(a)(5)(ii)(C)"],
    check_type="permissions",
    severity="critical",
    detect_script='''
        SUID_FOUND=$(find /tmp /var/tmp /dev/shm -perm -4000 -type f 2>/dev/null)
        if [ -z "$SUID_FOUND" ]; then
            echo "COMPLIANT"
            exit 0
        else
            COUNT=$(echo "$SUID_FOUND" | wc -l)
            echo "DRIFT:suid_binaries_found=$COUNT"
            echo "$SUID_FOUND"
            exit 1
        fi
    ''',
    remediate_script='''
        REMOVED=0
        for f in $(find /tmp /var/tmp /dev/shm -perm -4000 -type f 2>/dev/null); do
            rm -f "$f" && REMOVED=$((REMOVED+1))
        done
        echo "REMEDIATED:removed=$REMOVED"
    ''',
    verify_script='''
        SUID_FOUND=$(find /tmp /var/tmp /dev/shm -perm -4000 -type f 2>/dev/null)
        if [ -z "$SUID_FOUND" ]; then
            echo "VERIFIED"
            exit 0
        else
            echo "VERIFY_FAILED"
            exit 1
        fi
    ''',
    l1_auto_heal=True,
    timeout_seconds=30
)


# =============================================================================
# RUNBOOK REGISTRY
# =============================================================================

RUNBOOKS: Dict[str, LinuxRunbook] = {
    # SSH
    "LIN-SSH-001": LIN_SSH_001,
    "LIN-SSH-002": LIN_SSH_002,
    "LIN-SSH-003": LIN_SSH_003,
    "LIN-SSH-004": LIN_SSH_004,
    # Firewall
    "LIN-FW-001": LIN_FW_001,
    # Services
    "LIN-SVC-001": LIN_SVC_001,
    "LIN-SVC-002": LIN_SVC_002,
    "LIN-SVC-003": LIN_SVC_003,
    "LIN-SVC-004": LIN_SVC_004,
    # Audit
    "LIN-AUDIT-001": LIN_AUDIT_001,
    "LIN-AUDIT-002": LIN_AUDIT_002,
    # Patching
    "LIN-PATCH-001": LIN_PATCH_001,
    # Permissions
    "LIN-PERM-001": LIN_PERM_001,
    "LIN-PERM-002": LIN_PERM_002,
    "LIN-PERM-003": LIN_PERM_003,
    "LIN-PERM-004": LIN_PERM_004,
    # Accounts
    "LIN-ACCT-001": LIN_ACCT_001,
    "LIN-ACCT-002": LIN_ACCT_002,
    # MAC
    "LIN-MAC-001": LIN_MAC_001,
    # Time Sync
    "LIN-NTP-001": LIN_NTP_001,
    # Integrity
    "LIN-INTEGRITY-001": LIN_INTEGRITY_001,
    # Incident Response
    "LIN-IR-001": LIN_IR_001,
    # Kernel Hardening
    "LIN-KERN-001": LIN_KERN_001,
    "LIN-KERN-002": LIN_KERN_002,
    # Logging
    "LIN-LOG-001": LIN_LOG_001,
    # Network Hardening
    "LIN-NET-001": LIN_NET_001,
    # Boot Security
    "LIN-BOOT-001": LIN_BOOT_001,
    # Cron
    "LIN-CRON-001": LIN_CRON_001,
    # SUID
    "LIN-SUID-001": LIN_SUID_001,
    # Banner
    "LIN-BANNER-001": LIN_BANNER_001,
    # Cryptographic Policy
    "LIN-CRYPTO-001": LIN_CRYPTO_001,
}


def get_runbook(runbook_id: str) -> Optional[LinuxRunbook]:
    """Get runbook by ID."""
    return RUNBOOKS.get(runbook_id)


def get_runbooks_by_type(check_type: str) -> List[LinuxRunbook]:
    """Get all runbooks for a check type."""
    return [rb for rb in RUNBOOKS.values() if rb.check_type == check_type]


def get_l1_runbooks() -> List[LinuxRunbook]:
    """Get all L1 (auto-heal) runbooks."""
    return [rb for rb in RUNBOOKS.values() if rb.l1_auto_heal]


def get_l2_runbooks() -> List[LinuxRunbook]:
    """Get all L2 (LLM-eligible) runbooks."""
    return [rb for rb in RUNBOOKS.values() if rb.l2_llm_eligible and not rb.l1_auto_heal]
