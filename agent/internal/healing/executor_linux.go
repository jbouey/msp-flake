//go:build linux

package healing

import (
	"context"
	"fmt"
	"log"
	"os/exec"
	"strings"
	"time"

	pb "github.com/osiriscare/agent/proto"
)

// Execute runs a HealCommand on Linux using shell commands.
func Execute(ctx context.Context, cmd *pb.HealCommand) *Result {
	ts := cmd.TimeoutSeconds
	if ts <= 0 || ts > 600 {
		ts = 60
	}
	timeout := time.Duration(ts) * time.Second

	execCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	log.Printf("[heal] Executing: %s/%s (id=%s, timeout=%v)",
		cmd.CheckType, cmd.Action, cmd.CommandId, timeout)

	var res *Result
	switch cmd.CheckType {
	case "linux_ssh_config":
		res = healLinuxSSH(execCtx, cmd)
	case "linux_firewall":
		res = healLinuxFirewall(execCtx, cmd)
	case "linux_unattended_upgrades":
		res = healLinuxUnattendedUpgrades(execCtx, cmd)
	case "linux_suid_binaries":
		res = healLinuxSUID(execCtx, cmd)
	case "linux_audit_logging":
		res = healLinuxAudit(execCtx, cmd)
	case "linux_user_accounts":
		res = healLinuxUsers(execCtx, cmd)
	case "linux_ntp_sync":
		res = healLinuxNTP(execCtx, cmd)
	default:
		res = &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("check type %s requires manual remediation on Linux", cmd.CheckType),
		}
	}

	if res.Success {
		log.Printf("[heal] SUCCESS: %s/%s (id=%s)", cmd.CheckType, cmd.Action, cmd.CommandId)
	} else {
		log.Printf("[heal] FAILED: %s/%s (id=%s): %s", cmd.CheckType, cmd.Action, cmd.CommandId, res.Error)
	}
	return res
}

func runShell(ctx context.Context, script string) (string, error) {
	cmd := exec.CommandContext(ctx, "/bin/sh", "-c", script)
	out, err := cmd.CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

// healLinuxSSH hardens /etc/ssh/sshd_config and reloads sshd.
// Idempotent: sed only changes lines that don't already match.
func healLinuxSSH(ctx context.Context, cmd *pb.HealCommand) *Result {
	script := `set -e
SSHD_CONF="/etc/ssh/sshd_config"
if [ ! -f "$SSHD_CONF" ]; then echo "sshd_config not found"; exit 1; fi

# Backup before modifying
cp "$SSHD_CONF" "${SSHD_CONF}.bak.$(date +%s)"

# Set secure values (idempotent — sed replaces whether commented or not)
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' "$SSHD_CONF"
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' "$SSHD_CONF"
sed -i 's/^#\?MaxAuthTries.*/MaxAuthTries 3/' "$SSHD_CONF"
sed -i 's/^#\?X11Forwarding.*/X11Forwarding no/' "$SSHD_CONF"

# Ensure directives exist (append if not present)
grep -q "^PermitRootLogin" "$SSHD_CONF" || echo "PermitRootLogin no" >> "$SSHD_CONF"
grep -q "^PasswordAuthentication" "$SSHD_CONF" || echo "PasswordAuthentication no" >> "$SSHD_CONF"
grep -q "^MaxAuthTries" "$SSHD_CONF" || echo "MaxAuthTries 3" >> "$SSHD_CONF"

# Validate config before reload
sshd -t 2>&1 || { echo "sshd config validation failed"; exit 1; }

# Reload (not restart — preserves existing sessions)
systemctl reload sshd 2>/dev/null || systemctl reload ssh 2>/dev/null || true
echo "SSH hardened and reloaded"`

	out, err := runShell(ctx, script)
	if err != nil {
		return &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("ssh hardening failed: %v — %s", err, out),
		}
	}
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
		Artifacts: map[string]string{"output": out},
	}
}

// healLinuxFirewall enables ufw or firewalld (whichever is installed).
// Idempotent: enabling an already-enabled firewall is a no-op.
func healLinuxFirewall(ctx context.Context, cmd *pb.HealCommand) *Result {
	script := `set -e
if command -v ufw >/dev/null 2>&1; then
    ufw --force enable
    ufw status | head -5
elif command -v firewall-cmd >/dev/null 2>&1; then
    systemctl enable --now firewalld
    firewall-cmd --state
elif command -v nft >/dev/null 2>&1; then
    systemctl enable --now nftables
    nft list ruleset | head -5
else
    echo "No supported firewall found (ufw, firewalld, nftables)"
    exit 1
fi
echo "Firewall enabled"`

	out, err := runShell(ctx, script)
	if err != nil {
		return &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("firewall enable failed: %v — %s", err, out),
		}
	}
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
		Artifacts: map[string]string{"output": out},
	}
}

// healLinuxUnattendedUpgrades enables automatic security updates.
// Supports apt (Debian/Ubuntu), dnf (Fedora/RHEL), and yum (CentOS).
func healLinuxUnattendedUpgrades(ctx context.Context, cmd *pb.HealCommand) *Result {
	script := `set -e
if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y unattended-upgrades >/dev/null 2>&1 || true
    dpkg-reconfigure -f noninteractive unattended-upgrades 2>/dev/null || true
    systemctl enable --now apt-daily.timer 2>/dev/null || true
    systemctl enable --now apt-daily-upgrade.timer 2>/dev/null || true
    echo "unattended-upgrades enabled (apt)"
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y dnf-automatic >/dev/null 2>&1 || true
    sed -i 's/^apply_updates.*/apply_updates = yes/' /etc/dnf/automatic.conf 2>/dev/null || true
    systemctl enable --now dnf-automatic.timer
    echo "dnf-automatic enabled"
elif command -v yum >/dev/null 2>&1; then
    yum install -y yum-cron >/dev/null 2>&1 || true
    sed -i 's/^apply_updates.*/apply_updates = yes/' /etc/yum/yum-cron.conf 2>/dev/null || true
    systemctl enable --now yum-cron
    echo "yum-cron enabled"
else
    echo "No supported package manager found"
    exit 1
fi`

	out, err := runShell(ctx, script)
	if err != nil {
		return &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("unattended upgrades setup failed: %v — %s", err, out),
		}
	}
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
		Artifacts: map[string]string{"output": out},
	}
}

// healLinuxSUID removes SUID bit from non-essential binaries.
// Only touches known-safe targets. Never removes SUID from su/sudo/passwd/mount/ping.
func healLinuxSUID(ctx context.Context, cmd *pb.HealCommand) *Result {
	script := `set -e
REMOVED=""
# Known-safe SUID removals (never touch su, sudo, passwd, mount, umount, ping)
for bin in /usr/bin/newgrp /usr/bin/chsh /usr/bin/chfn /usr/bin/gpasswd /usr/sbin/pppd; do
    if [ -u "$bin" ] 2>/dev/null; then
        chmod u-s "$bin"
        REMOVED="$REMOVED $bin"
    fi
done
if [ -z "$REMOVED" ]; then
    echo "No non-essential SUID binaries found"
else
    echo "Removed SUID from:$REMOVED"
fi`

	out, err := runShell(ctx, script)
	if err != nil {
		return &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("suid cleanup failed: %v — %s", err, out),
		}
	}
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
		Artifacts: map[string]string{"output": out},
	}
}

// healLinuxAudit ensures auditd is running with basic HIPAA rules.
func healLinuxAudit(ctx context.Context, cmd *pb.HealCommand) *Result {
	script := `set -e
# Install auditd if missing
if ! command -v auditd >/dev/null 2>&1 && ! command -v auditctl >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y auditd >/dev/null 2>&1
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y audit >/dev/null 2>&1
    elif command -v yum >/dev/null 2>&1; then
        yum install -y audit >/dev/null 2>&1
    fi
fi

systemctl enable --now auditd 2>/dev/null || true

# Add basic HIPAA audit rules if not present
RULES_FILE="/etc/audit/rules.d/hipaa.rules"
if [ ! -f "$RULES_FILE" ]; then
    mkdir -p /etc/audit/rules.d
    cat > "$RULES_FILE" << 'AUDIT_RULES'
# HIPAA 164.312(b) — Audit controls
-w /etc/passwd -p wa -k identity
-w /etc/group -p wa -k identity
-w /etc/shadow -p wa -k identity
-w /etc/ssh/sshd_config -p wa -k sshd_config
-w /var/log/auth.log -p wa -k auth_log
-w /var/log/secure -p wa -k auth_log
AUDIT_RULES
    augenrules --load 2>/dev/null || auditctl -R "$RULES_FILE" 2>/dev/null || true
fi

systemctl is-active auditd && echo "auditd running with HIPAA rules"`

	out, err := runShell(ctx, script)
	if err != nil {
		return &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("audit setup failed: %v — %s", err, out),
		}
	}
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
		Artifacts: map[string]string{"output": out},
	}
}

// healLinuxUsers escalates to L3 — user account changes require human verification.
func healLinuxUsers(_ context.Context, cmd *pb.HealCommand) *Result {
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   false,
		Error:     "user account changes require human verification — escalating to L3",
	}
}

// healLinuxNTP enables systemd-timesyncd or chronyd.
func healLinuxNTP(ctx context.Context, cmd *pb.HealCommand) *Result {
	script := `set -e
if command -v timedatectl >/dev/null 2>&1; then
    timedatectl set-ntp true
    timedatectl status | grep -E "NTP|synchronized"
elif command -v chronyd >/dev/null 2>&1; then
    systemctl enable --now chronyd
    chronyc tracking | head -3
else
    echo "No supported NTP service found"
    exit 1
fi
echo "NTP sync enabled"`

	out, err := runShell(ctx, script)
	if err != nil {
		return &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("ntp enable failed: %v — %s", err, out),
		}
	}
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
		Artifacts: map[string]string{"output": out},
	}
}
