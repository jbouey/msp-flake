#!/bin/bash
#
# Linux Chaos Lab - Create drift conditions for compliance testing
#
# Usage:
#   ./linux_chaos_lab.sh <target_ip> <action>
#
# Actions:
#   create-ssh-drift     - Enable PermitRootLogin, PasswordAuthentication
#   create-firewall-drift - Disable ufw/firewalld
#   create-service-drift  - Stop auditd, rsyslog
#   create-audit-drift    - Remove audit rules
#   create-permission-drift - Make /etc/shadow world-readable
#   create-all           - Create all drift conditions
#   status               - Check current drift state
#   reset                - Reset to compliant state
#
# Example:
#   ./linux_chaos_lab.sh 192.168.88.242 create-all
#

set -e

TARGET="${1:-192.168.88.242}"
ACTION="${2:-status}"
SSH_USER="${SSH_USER:-osiris}"
SSH_KEY="${SSH_KEY:-}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# SSH command helper
ssh_cmd() {
    if [ -n "$SSH_KEY" ]; then
        ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$TARGET" "$1"
    else
        ssh -o StrictHostKeyChecking=no "$SSH_USER@$TARGET" "$1"
    fi
}

# Check if we can connect
check_connection() {
    log_info "Testing connection to $TARGET as $SSH_USER..."
    if ssh_cmd "echo connected" > /dev/null 2>&1; then
        log_info "Connection successful"
        return 0
    else
        log_error "Cannot connect to $TARGET"
        exit 1
    fi
}

# Create SSH configuration drift
create_ssh_drift() {
    log_warn "Creating SSH drift on $TARGET..."

    ssh_cmd "sudo sed -i 's/^PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config || \
             sudo bash -c 'echo \"PermitRootLogin yes\" >> /etc/ssh/sshd_config'"

    ssh_cmd "sudo sed -i 's/^PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config || \
             sudo bash -c 'echo \"PasswordAuthentication yes\" >> /etc/ssh/sshd_config'"

    ssh_cmd "sudo sed -i 's/^MaxAuthTries.*/MaxAuthTries 10/' /etc/ssh/sshd_config || \
             sudo bash -c 'echo \"MaxAuthTries 10\" >> /etc/ssh/sshd_config'"

    ssh_cmd "sudo systemctl reload sshd || sudo systemctl reload ssh" 2>/dev/null || true

    log_warn "SSH drift created: PermitRootLogin=yes, PasswordAuthentication=yes, MaxAuthTries=10"
}

# Create firewall drift
create_firewall_drift() {
    log_warn "Creating firewall drift on $TARGET..."

    # Try ufw first (Ubuntu)
    ssh_cmd "sudo ufw disable" 2>/dev/null || true

    # Try firewalld (RHEL)
    ssh_cmd "sudo systemctl stop firewalld" 2>/dev/null || true

    log_warn "Firewall drift created: firewall disabled"
}

# Create service drift
create_service_drift() {
    log_warn "Creating service drift on $TARGET..."

    # Stop auditd if running
    ssh_cmd "sudo systemctl stop auditd" 2>/dev/null || true

    # Stop rsyslog (careful - this affects logging)
    # ssh_cmd "sudo systemctl stop rsyslog" 2>/dev/null || true

    log_warn "Service drift created: auditd stopped"
}

# Create audit drift
create_audit_drift() {
    log_warn "Creating audit drift on $TARGET..."

    # Remove audit rules
    ssh_cmd "sudo auditctl -D" 2>/dev/null || true

    # Remove audit rule files
    ssh_cmd "sudo rm -f /etc/audit/rules.d/identity.rules /etc/audit/rules.d/auth.rules" 2>/dev/null || true

    log_warn "Audit drift created: audit rules removed"
}

# Create permission drift
create_permission_drift() {
    log_warn "Creating permission drift on $TARGET..."

    # Make shadow file world-readable (DANGEROUS - only for testing!)
    ssh_cmd "sudo chmod 644 /etc/shadow"

    # Make sshd_config world-writable
    ssh_cmd "sudo chmod 666 /etc/ssh/sshd_config"

    log_warn "Permission drift created: /etc/shadow=644, /etc/ssh/sshd_config=666"
}

# Create all drift conditions
create_all_drift() {
    log_warn "Creating ALL drift conditions on $TARGET..."
    create_ssh_drift
    create_firewall_drift
    create_service_drift
    create_audit_drift
    create_permission_drift
    log_warn "All drift conditions created!"
}

# Check current status
check_status() {
    log_info "Checking drift status on $TARGET..."
    echo ""

    echo "=== SSH Configuration ==="
    ssh_cmd "grep -E '^PermitRootLogin|^PasswordAuthentication|^MaxAuthTries' /etc/ssh/sshd_config" || echo "Not set"
    echo ""

    echo "=== Firewall Status ==="
    ssh_cmd "sudo ufw status 2>/dev/null || sudo firewall-cmd --state 2>/dev/null || echo 'No firewall detected'"
    echo ""

    echo "=== Service Status ==="
    ssh_cmd "systemctl is-active auditd 2>/dev/null || echo 'auditd: not active'"
    ssh_cmd "systemctl is-active rsyslog 2>/dev/null || echo 'rsyslog: not active'"
    echo ""

    echo "=== Audit Rules ==="
    ssh_cmd "sudo auditctl -l 2>/dev/null | head -5 || echo 'No audit rules'"
    echo ""

    echo "=== File Permissions ==="
    ssh_cmd "stat -c '%a %n' /etc/shadow /etc/ssh/sshd_config 2>/dev/null"
    echo ""
}

# Reset to compliant state
reset_to_compliant() {
    log_info "Resetting $TARGET to compliant state..."

    # Fix SSH config
    ssh_cmd "sudo sed -i 's/^PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config"
    ssh_cmd "sudo sed -i 's/^PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config"
    ssh_cmd "sudo sed -i 's/^MaxAuthTries.*/MaxAuthTries 3/' /etc/ssh/sshd_config"
    ssh_cmd "sudo systemctl reload sshd || sudo systemctl reload ssh" 2>/dev/null || true

    # Enable firewall
    ssh_cmd "sudo ufw --force enable" 2>/dev/null || \
    ssh_cmd "sudo systemctl start firewalld" 2>/dev/null || true

    # Start services
    ssh_cmd "sudo systemctl start auditd" 2>/dev/null || true
    ssh_cmd "sudo systemctl start rsyslog" 2>/dev/null || true

    # Fix permissions
    ssh_cmd "sudo chmod 640 /etc/shadow"
    ssh_cmd "sudo chmod 600 /etc/ssh/sshd_config"

    log_info "Reset complete!"
}

# Main
case "$ACTION" in
    create-ssh-drift)
        check_connection
        create_ssh_drift
        ;;
    create-firewall-drift)
        check_connection
        create_firewall_drift
        ;;
    create-service-drift)
        check_connection
        create_service_drift
        ;;
    create-audit-drift)
        check_connection
        create_audit_drift
        ;;
    create-permission-drift)
        check_connection
        create_permission_drift
        ;;
    create-all)
        check_connection
        create_all_drift
        ;;
    status)
        check_connection
        check_status
        ;;
    reset)
        check_connection
        reset_to_compliant
        ;;
    *)
        echo "Usage: $0 <target_ip> <action>"
        echo ""
        echo "Actions:"
        echo "  create-ssh-drift      - Enable PermitRootLogin, PasswordAuthentication"
        echo "  create-firewall-drift - Disable ufw/firewalld"
        echo "  create-service-drift  - Stop auditd"
        echo "  create-audit-drift    - Remove audit rules"
        echo "  create-permission-drift - Make files world-readable"
        echo "  create-all            - Create all drift conditions"
        echo "  status                - Check current drift state"
        echo "  reset                 - Reset to compliant state"
        exit 1
        ;;
esac
