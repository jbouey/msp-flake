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
# RUNBOOK REGISTRY
# =============================================================================

RUNBOOKS: Dict[str, LinuxRunbook] = {
    # SSH
    "LIN-SSH-001": LIN_SSH_001,
    "LIN-SSH-002": LIN_SSH_002,
    "LIN-SSH-003": LIN_SSH_003,
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
    # Accounts
    "LIN-ACCT-001": LIN_ACCT_001,
    "LIN-ACCT-002": LIN_ACCT_002,
    # MAC
    "LIN-MAC-001": LIN_MAC_001,
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
