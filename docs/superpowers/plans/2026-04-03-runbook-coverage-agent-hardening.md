# Runbook Coverage + Agent Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close runbook coverage gaps across all 3 platforms and harden the agent updater against platform mismatches.

**Architecture:** Three sub-projects executed sequentially: (A) Linux L1 executor for the workstation agent, (B) agent updater platform validation + chaos lab cron hardening, (C) macOS/Windows healing additions. All follow existing executor patterns (build-tagged files, `Execute()` interface, `runShell()`/`runPS()` helpers, structured logging, idempotent scripts).

**Tech Stack:** Go 1.26 (agent), bash/PowerShell (healing scripts), `go test` (unit tests)

---

## File Structure

### Sub-project A: Linux L1 Executor
| Action | File | Responsibility |
|--------|------|---------------|
| Create | `agent/internal/healing/executor_linux.go` | Linux healing dispatch + 7 heal functions |
| Create | `agent/internal/healing/executor_linux_test.go` | Unit tests for all Linux heal functions |
| Modify | `agent/internal/healing/executor_other.go` | Change build tag from `!windows && !darwin` to explicit unsupported list |

### Sub-project B: Agent Hardening
| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `agent/internal/updater/updater.go` | Platform validation before binary swap |
| Modify | `agent/internal/updater/updater_test.go` | Tests for platform validation |

### Sub-project C: macOS/Windows Healing Additions
| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `agent/internal/healing/executor_darwin.go` | Add FileVault + Time Machine healing |
| Modify | `agent/internal/healing/executor.go` | Add Windows patching healing |
| Create | `agent/internal/healing/executor_darwin_test.go` | Tests for new macOS healers |
| Create | `agent/internal/healing/executor_windows_test.go` | Tests for new Windows healer |

---

## Sub-project A: Linux L1 Executor

### Task 1: Replace the stub with a Linux executor

**Files:**
- Modify: `agent/internal/healing/executor_other.go` — narrow build tag
- Create: `agent/internal/healing/executor_linux.go` — full Linux executor
- Create: `agent/internal/healing/executor_linux_test.go` — unit tests

- [ ] **Step 1: Update executor_other.go build tag**

Change from catch-all to explicit unsupported platforms so Linux gets its own file:

```go
//go:build !windows && !darwin && !linux

package healing

import (
	"context"

	pb "github.com/osiriscare/agent/proto"
)

// Execute on unsupported platforms always returns an error.
func Execute(ctx context.Context, cmd *pb.HealCommand) *Result {
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   false,
		Error:     "healing not supported on this platform",
	}
}
```

- [ ] **Step 2: Write executor_linux.go with all 7 heal functions**

```go
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
# Harden SSH config (idempotent — only changes non-compliant lines)
SSHD_CONF="/etc/ssh/sshd_config"
if [ ! -f "$SSHD_CONF" ]; then echo "sshd_config not found"; exit 1; fi

# Backup before modifying
cp "$SSHD_CONF" "${SSHD_CONF}.bak.$(date +%s)"

# Set secure values (sed is idempotent if value already matches)
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
// Supports apt (Debian/Ubuntu) and dnf (RHEL/Fedora).
func healLinuxUnattendedUpgrades(ctx context.Context, cmd *pb.HealCommand) *Result {
	script := `set -e
if command -v apt-get >/dev/null 2>&1; then
    # Debian/Ubuntu: install and enable unattended-upgrades
    DEBIAN_FRONTEND=noninteractive apt-get install -y unattended-upgrades >/dev/null 2>&1 || true
    dpkg-reconfigure -f noninteractive unattended-upgrades 2>/dev/null || true
    # Enable auto-update timers
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
// Only touches known-safe targets. Never removes SUID from su/sudo/passwd.
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
if ! command -v auditd >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y auditd >/dev/null 2>&1
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y audit >/dev/null 2>&1
    elif command -v yum >/dev/null 2>&1; then
        yum install -y audit >/dev/null 2>&1
    fi
fi

# Enable and start
systemctl enable --now auditd 2>/dev/null || true

# Add basic HIPAA audit rules if not present
RULES_FILE="/etc/audit/rules.d/hipaa.rules"
if [ ! -f "$RULES_FILE" ]; then
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
// This is intentionally a no-op heal that reports the issue.
func healLinuxUsers(ctx context.Context, cmd *pb.HealCommand) *Result {
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
```

- [ ] **Step 3: Write executor_linux_test.go**

```go
//go:build linux

package healing

import (
	"context"
	"testing"

	pb "github.com/osiriscare/agent/proto"
)

func TestExecute_DispatchesKnownCheckTypes(t *testing.T) {
	knownTypes := []string{
		"linux_ssh_config",
		"linux_firewall",
		"linux_unattended_upgrades",
		"linux_suid_binaries",
		"linux_audit_logging",
		"linux_user_accounts",
		"linux_ntp_sync",
	}
	for _, ct := range knownTypes {
		t.Run(ct, func(t *testing.T) {
			cmd := &pb.HealCommand{
				CommandId:      "test-" + ct,
				CheckType:      ct,
				Action:         "heal",
				TimeoutSeconds: 5,
			}
			// On a non-root test environment, these will fail at execution
			// but should NOT return "requires manual remediation"
			res := Execute(context.Background(), cmd)
			if res.CommandID != cmd.CommandId {
				t.Errorf("CommandID: got %s, want %s", res.CommandID, cmd.CommandId)
			}
			if res.CheckType != ct {
				t.Errorf("CheckType: got %s, want %s", res.CheckType, ct)
			}
			// Should NOT be the "unsupported" fallback message
			if res.Error == "check type "+ct+" requires manual remediation on Linux" {
				t.Errorf("check type %s fell through to default case", ct)
			}
		})
	}
}

func TestExecute_UnknownCheckType(t *testing.T) {
	cmd := &pb.HealCommand{
		CommandId:      "test-unknown",
		CheckType:      "nonexistent_check",
		Action:         "heal",
		TimeoutSeconds: 5,
	}
	res := Execute(context.Background(), cmd)
	if res.Success {
		t.Error("expected failure for unknown check type")
	}
	if res.Error == "" {
		t.Error("expected error message for unknown check type")
	}
}

func TestExecute_DefaultTimeout(t *testing.T) {
	cmd := &pb.HealCommand{
		CommandId:      "test-timeout",
		CheckType:      "linux_ntp_sync",
		Action:         "heal",
		TimeoutSeconds: 0, // should default to 60
	}
	// Just verifying it doesn't panic with 0 timeout
	res := Execute(context.Background(), cmd)
	if res.CommandID != "test-timeout" {
		t.Errorf("CommandID: got %s, want test-timeout", res.CommandID)
	}
}

func TestExecute_NegativeTimeout(t *testing.T) {
	cmd := &pb.HealCommand{
		CommandId:      "test-neg-timeout",
		CheckType:      "linux_ntp_sync",
		Action:         "heal",
		TimeoutSeconds: -1, // should default to 60
	}
	res := Execute(context.Background(), cmd)
	if res.CommandID != "test-neg-timeout" {
		t.Errorf("CommandID: got %s, want test-neg-timeout", res.CommandID)
	}
}

func TestExecute_ExcessiveTimeout(t *testing.T) {
	cmd := &pb.HealCommand{
		CommandId:      "test-excess-timeout",
		CheckType:      "linux_ntp_sync",
		Action:         "heal",
		TimeoutSeconds: 9999, // should clamp to 60
	}
	res := Execute(context.Background(), cmd)
	if res.CommandID != "test-excess-timeout" {
		t.Errorf("CommandID: got %s, want test-excess-timeout", res.CommandID)
	}
}

func TestHealLinuxUsers_AlwaysEscalates(t *testing.T) {
	cmd := &pb.HealCommand{
		CommandId:      "test-users",
		CheckType:      "linux_user_accounts",
		Action:         "heal",
		TimeoutSeconds: 5,
	}
	res := Execute(context.Background(), cmd)
	if res.Success {
		t.Error("user account healing should always escalate (fail)")
	}
	if res.Error == "" {
		t.Error("expected escalation error message")
	}
}

func TestRunShell_BasicExecution(t *testing.T) {
	out, err := runShell(context.Background(), "echo hello")
	if err != nil {
		t.Fatalf("runShell failed: %v", err)
	}
	if out != "hello" {
		t.Errorf("got %q, want %q", out, "hello")
	}
}

func TestRunShell_FailingCommand(t *testing.T) {
	_, err := runShell(context.Background(), "exit 1")
	if err == nil {
		t.Error("expected error from failing command")
	}
}

func TestRunShell_ContextCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel immediately
	_, err := runShell(ctx, "sleep 10")
	if err == nil {
		t.Error("expected error from cancelled context")
	}
}
```

- [ ] **Step 4: Verify tests compile and run**

Run: `cd /Users/dad/Documents/Msp_Flakes/agent && go test ./internal/healing/ -v -count=1`
Expected: Tests pass (linux_user_accounts always fails intentionally; shell tests pass on any Linux/macOS)

Note: On macOS dev machine, the `executor_linux_test.go` won't compile due to build tag. Run: `GOOS=linux go vet ./internal/healing/` to verify it compiles for Linux.

- [ ] **Step 5: Commit**

```bash
git add agent/internal/healing/executor_linux.go agent/internal/healing/executor_linux_test.go agent/internal/healing/executor_other.go
git commit -m "feat: Linux L1 healing executor — 7 check types (SSH, firewall, upgrades, SUID, audit, users, NTP)"
```

---

## Sub-project B: Agent Updater Hardening

### Task 2: Platform validation before binary swap

**Files:**
- Modify: `agent/internal/updater/updater.go:127-136` — add platform check
- Modify: `agent/internal/updater/updater_test.go` — add platform validation tests

- [ ] **Step 1: Add platform validation function to updater.go**

Add after the `fileSHA256` function (around line 300):

```go
// validateBinaryPlatform checks that a downloaded binary matches the current OS.
// Returns nil if the binary is compatible, an error describing the mismatch otherwise.
// This prevents cross-platform update errors (e.g., Windows PE on macOS).
func validateBinaryPlatform(path string) error {
	f, err := os.Open(path)
	if err != nil {
		return fmt.Errorf("open binary: %w", err)
	}
	defer f.Close()

	// Read first 4 bytes (magic number)
	magic := make([]byte, 4)
	if _, err := io.ReadFull(f, magic); err != nil {
		return fmt.Errorf("read magic: %w", err)
	}

	currentOS := runtime.GOOS
	switch {
	case magic[0] == 'M' && magic[1] == 'Z':
		// PE (Windows) executable
		if currentOS != "windows" {
			return fmt.Errorf("binary is Windows PE but running on %s", currentOS)
		}
	case magic[0] == 0xCF && magic[1] == 0xFA && magic[2] == 0xED && magic[3] == 0xFE:
		// Mach-O 64-bit (macOS, little-endian)
		if currentOS != "darwin" {
			return fmt.Errorf("binary is macOS Mach-O but running on %s", currentOS)
		}
	case magic[0] == 0xFE && magic[1] == 0xED && magic[2] == 0xFA && magic[3] == 0xCF:
		// Mach-O 64-bit (macOS, big-endian)
		if currentOS != "darwin" {
			return fmt.Errorf("binary is macOS Mach-O but running on %s", currentOS)
		}
	case magic[0] == 0xCA && magic[1] == 0xFE && magic[2] == 0xBA && magic[3] == 0xBE:
		// Mach-O universal/fat binary (macOS)
		if currentOS != "darwin" {
			return fmt.Errorf("binary is macOS universal but running on %s", currentOS)
		}
	case magic[0] == 0x7F && magic[1] == 'E' && magic[2] == 'L' && magic[3] == 'F':
		// ELF (Linux)
		if currentOS != "linux" {
			return fmt.Errorf("binary is Linux ELF but running on %s", currentOS)
		}
	default:
		return fmt.Errorf("unrecognized binary format (magic: %x)", magic)
	}

	return nil
}
```

- [ ] **Step 2: Wire platform validation into CheckAndUpdate**

In `CheckAndUpdate`, add after SHA256 verification (line 133) and before `applyUpdate` (line 136):

```go
	// Validate binary matches current platform (prevents cross-platform update errors)
	if err := validateBinaryPlatform(newPath); err != nil {
		os.Remove(newPath)
		u.recordFailure()
		return fmt.Errorf("platform mismatch: %w", err)
	}
```

Also add `"io"` to the imports if not already present.

- [ ] **Step 3: Add platform validation tests to updater_test.go**

Append these tests:

```go
func TestValidateBinaryPlatform_MatchesCurrentOS(t *testing.T) {
	tmpDir := t.TempDir()
	path := filepath.Join(tmpDir, "testbin")

	var magic []byte
	switch runtime.GOOS {
	case "darwin":
		magic = []byte{0xCF, 0xFA, 0xED, 0xFE, 0, 0, 0, 0} // Mach-O 64 LE
	case "linux":
		magic = []byte{0x7F, 'E', 'L', 'F', 0, 0, 0, 0} // ELF
	case "windows":
		magic = []byte{'M', 'Z', 0, 0, 0, 0, 0, 0} // PE
	default:
		t.Skipf("unsupported OS: %s", runtime.GOOS)
	}
	os.WriteFile(path, magic, 0644)

	if err := validateBinaryPlatform(path); err != nil {
		t.Errorf("expected no error for matching platform, got: %v", err)
	}
}

func TestValidateBinaryPlatform_RejectsWrongPlatform(t *testing.T) {
	tmpDir := t.TempDir()

	tests := []struct {
		name  string
		magic []byte
		skip  string // skip on this OS (binary matches)
	}{
		{"PE_on_non_windows", []byte{'M', 'Z', 0, 0, 0, 0, 0, 0}, "windows"},
		{"MachO_on_non_darwin", []byte{0xCF, 0xFA, 0xED, 0xFE, 0, 0, 0, 0}, "darwin"},
		{"ELF_on_non_linux", []byte{0x7F, 'E', 'L', 'F', 0, 0, 0, 0}, "linux"},
		{"MachO_universal_on_non_darwin", []byte{0xCA, 0xFE, 0xBA, 0xBE, 0, 0, 0, 0}, "darwin"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if runtime.GOOS == tt.skip {
				t.Skipf("binary matches current OS %s", tt.skip)
			}
			path := filepath.Join(tmpDir, tt.name)
			os.WriteFile(path, tt.magic, 0644)

			err := validateBinaryPlatform(path)
			if err == nil {
				t.Error("expected platform mismatch error")
			}
		})
	}
}

func TestValidateBinaryPlatform_UnrecognizedFormat(t *testing.T) {
	tmpDir := t.TempDir()
	path := filepath.Join(tmpDir, "garbage")
	os.WriteFile(path, []byte{0xFF, 0xFF, 0xFF, 0xFF}, 0644)

	err := validateBinaryPlatform(path)
	if err == nil {
		t.Error("expected error for unrecognized format")
	}
}

func TestValidateBinaryPlatform_EmptyFile(t *testing.T) {
	tmpDir := t.TempDir()
	path := filepath.Join(tmpDir, "empty")
	os.WriteFile(path, []byte{}, 0644)

	err := validateBinaryPlatform(path)
	if err == nil {
		t.Error("expected error for empty file")
	}
}
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/dad/Documents/Msp_Flakes/agent && go test ./internal/updater/ -v -count=1`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add agent/internal/updater/updater.go agent/internal/updater/updater_test.go
git commit -m "feat: updater platform validation — rejects cross-platform binaries before swap"
```

---

## Sub-project C: macOS + Windows Healing Additions

### Task 3: macOS FileVault deferred enablement

**Files:**
- Modify: `agent/internal/healing/executor_darwin.go` — add FileVault + Time Machine cases

- [ ] **Step 1: Add FileVault and Time Machine heal functions to executor_darwin.go**

Add new cases in the `Execute` switch (after `macos_gatekeeper` case):

```go
	case "macos_filevault":
		res = healMacFileVault(execCtx, cmd)
	case "macos_time_machine":
		res = healMacTimeMachine(execCtx, cmd)
```

Add the functions:

```go
// healMacFileVault enables FileVault using deferred enablement.
// Deferred mode queues encryption for the next user login — no interactive password needed.
// If already enabled, this is a no-op.
func healMacFileVault(ctx context.Context, cmd *pb.HealCommand) *Result {
	script := `# Check if already enabled
FV_STATUS=$(fdesetup status 2>&1)
if echo "$FV_STATUS" | grep -q "FileVault is On"; then
    echo "FileVault already enabled"
    exit 0
fi

# Attempt deferred enablement (queues for next login)
# This requires an institutional recovery key or escrow to be pre-configured.
# If no institutional key exists, this will fail gracefully.
if fdesetup enable -defer /var/db/FileVaultDeferred.plist -forcerestart 0 2>&1; then
    echo "FileVault deferred enablement configured — will activate at next user login"
else
    # Deferred requires Setup Assistant or MDM profile. Report status for L3.
    echo "FileVault deferred enablement failed — requires MDM profile or manual setup"
    exit 1
fi`

	out, err := runShell(ctx, script)
	if err != nil {
		return &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("filevault enable failed: %v — %s", err, out),
		}
	}
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
		Artifacts: map[string]string{"output": out},
	}
}

// healMacTimeMachine enables Time Machine if a backup destination is already configured.
// Does NOT create or configure a backup destination (requires user interaction).
func healMacTimeMachine(ctx context.Context, cmd *pb.HealCommand) *Result {
	script := `# Check if a backup destination exists
DEST=$(tmutil destinationinfo 2>/dev/null)
if [ -z "$DEST" ] || echo "$DEST" | grep -q "No destinations configured"; then
    echo "No backup destination configured — cannot enable Time Machine without a target disk"
    exit 1
fi

# Enable Time Machine auto-backup
tmutil enable
defaults write /Library/Preferences/com.apple.TimeMachine AutoBackup -bool true
echo "Time Machine enabled with auto-backup"`

	out, err := runShell(ctx, script)
	if err != nil {
		return &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("time machine enable failed: %v — %s", err, out),
		}
	}
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
		Artifacts: map[string]string{"output": out},
	}
}
```

- [ ] **Step 2: Create executor_darwin_test.go**

```go
//go:build darwin

package healing

import (
	"context"
	"testing"

	pb "github.com/osiriscare/agent/proto"
)

func TestMacExecute_DispatchesKnownCheckTypes(t *testing.T) {
	knownTypes := []string{
		"macos_firewall",
		"macos_auto_update",
		"macos_screen_lock",
		"macos_ntp_sync",
		"macos_file_sharing",
		"macos_gatekeeper",
		"macos_filevault",
		"macos_time_machine",
	}
	for _, ct := range knownTypes {
		t.Run(ct, func(t *testing.T) {
			cmd := &pb.HealCommand{
				CommandId:      "test-" + ct,
				CheckType:      ct,
				Action:         "heal",
				TimeoutSeconds: 5,
			}
			res := Execute(context.Background(), cmd)
			if res.CommandID != cmd.CommandId {
				t.Errorf("CommandID: got %s, want %s", res.CommandID, cmd.CommandId)
			}
			if res.CheckType != ct {
				t.Errorf("CheckType: got %s, want %s", res.CheckType, ct)
			}
			// Should NOT be the generic "manual remediation" fallback
			if res.Error == "check type "+ct+" requires manual remediation" {
				t.Errorf("check type %s fell through to default case", ct)
			}
		})
	}
}

func TestMacExecute_UnknownCheckType(t *testing.T) {
	cmd := &pb.HealCommand{
		CommandId:      "test-unknown",
		CheckType:      "nonexistent_check",
		Action:         "heal",
		TimeoutSeconds: 5,
	}
	res := Execute(context.Background(), cmd)
	if res.Success {
		t.Error("expected failure for unknown check type")
	}
}

func TestMacRunShell_BasicExecution(t *testing.T) {
	out, err := runShell(context.Background(), "echo hello")
	if err != nil {
		t.Fatalf("runShell failed: %v", err)
	}
	if out != "hello" {
		t.Errorf("got %q, want %q", out, "hello")
	}
}

func TestMacRunShell_FailingCommand(t *testing.T) {
	_, err := runShell(context.Background(), "exit 1")
	if err == nil {
		t.Error("expected error from failing command")
	}
}
```

- [ ] **Step 3: Run macOS tests**

Run: `cd /Users/dad/Documents/Msp_Flakes/agent && go test ./internal/healing/ -v -count=1`
Expected: All tests PASS (heal functions will fail without root but dispatch correctly)

- [ ] **Step 4: Commit**

```bash
git add agent/internal/healing/executor_darwin.go agent/internal/healing/executor_darwin_test.go
git commit -m "feat: macOS FileVault deferred enablement + Time Machine auto-enable"
```

### Task 4: Windows patching L1 rule

**Files:**
- Modify: `agent/internal/healing/executor.go` — add `windows_update` check type handler
- Create: `agent/internal/healing/executor_windows_test.go` — tests

- [ ] **Step 1: Add windows_update healing to executor.go**

Add a new case in the `Execute` switch (Windows file):

```go
	case "windows_update":
		res = healWindowsUpdate(execCtx, cmd)
```

Add the function:

```go
// healWindowsUpdate installs pending critical/security updates via Windows Update.
// Uses the PSWindowsUpdate module if available, falls back to wuauclt.
// Idempotent: if no updates pending, reports success.
func healWindowsUpdate(ctx context.Context, cmd *pb.HealCommand) *Result {
	script := `
# Check for pending updates and install critical ones
try {
    # Try PSWindowsUpdate module first (more control)
    if (Get-Module -ListAvailable PSWindowsUpdate -EA SilentlyContinue) {
        Import-Module PSWindowsUpdate
        $updates = Get-WindowsUpdate -Category 'Security Updates','Critical Updates' -AcceptAll -IgnoreReboot -EA Stop
        if ($updates.Count -eq 0) {
            Write-Output "NO_UPDATES_PENDING"
        } else {
            Install-WindowsUpdate -Category 'Security Updates','Critical Updates' -AcceptAll -IgnoreReboot -EA Stop | Out-Null
            Write-Output "INSTALLED:$($updates.Count)"
        }
    } else {
        # Fallback: trigger Windows Update scan + install
        $Session = New-Object -ComObject Microsoft.Update.Session
        $Searcher = $Session.CreateUpdateSearcher()
        $SearchResult = $Searcher.Search("IsInstalled=0 and Type='Software' and IsHidden=0")
        
        $Critical = @($SearchResult.Updates | Where-Object { $_.MsrcSeverity -eq 'Critical' -or $_.MsrcSeverity -eq 'Important' })
        
        if ($Critical.Count -eq 0) {
            Write-Output "NO_UPDATES_PENDING"
        } else {
            $Downloader = $Session.CreateUpdateDownloader()
            $ToInstall = New-Object -ComObject Microsoft.Update.UpdateColl
            foreach ($u in $Critical) { $ToInstall.Add($u) | Out-Null }
            $Downloader.Updates = $ToInstall
            $Downloader.Download() | Out-Null
            
            $Installer = $Session.CreateUpdateInstaller()
            $Installer.Updates = $ToInstall
            $Result = $Installer.Install()
            Write-Output "INSTALLED:$($Critical.Count) REBOOT:$($Result.RebootRequired)"
        }
    }
} catch {
    Write-Output "ERROR:$($_.Exception.Message)"
    exit 1
}
`
	out, err := runPS(ctx, script)
	if err != nil {
		return &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("windows update failed: %v — %s", err, out),
		}
	}
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
		Artifacts: map[string]string{
			"output": out,
		},
	}
}
```

- [ ] **Step 2: Create executor_windows_test.go**

```go
//go:build windows

package healing

import (
	"context"
	"testing"

	pb "github.com/osiriscare/agent/proto"
)

func TestWinExecute_DispatchesKnownCheckTypes(t *testing.T) {
	knownTypes := []string{
		"firewall",
		"defender",
		"screenlock",
		"bitlocker",
		"winrm",
		"dns_service",
		"windows_update",
	}
	for _, ct := range knownTypes {
		t.Run(ct, func(t *testing.T) {
			cmd := &pb.HealCommand{
				CommandId:      "test-" + ct,
				CheckType:      ct,
				Action:         "heal",
				TimeoutSeconds: 5,
			}
			res := Execute(context.Background(), cmd)
			if res.CommandID != cmd.CommandId {
				t.Errorf("CommandID: got %s, want %s", res.CommandID, cmd.CommandId)
			}
			if res.CheckType != ct {
				t.Errorf("CheckType: got %s, want %s", res.CheckType, ct)
			}
		})
	}
}

func TestWinExecute_UnknownCheckType(t *testing.T) {
	cmd := &pb.HealCommand{
		CommandId:      "test-unknown",
		CheckType:      "nonexistent_check",
		Action:         "heal",
		TimeoutSeconds: 5,
	}
	res := Execute(context.Background(), cmd)
	if res.Success {
		t.Error("expected failure for unknown check type")
	}
}
```

- [ ] **Step 3: Verify compilation for Windows target**

Run: `cd /Users/dad/Documents/Msp_Flakes/agent && GOOS=windows go vet ./internal/healing/`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add agent/internal/healing/executor.go agent/internal/healing/executor_windows_test.go
git commit -m "feat: Windows Update L1 healing — installs critical/security patches"
```

### Task 5: Run full test suite and verify

- [ ] **Step 1: Run all agent tests**

Run: `cd /Users/dad/Documents/Msp_Flakes/agent && go test ./... -count=1`
Expected: All packages PASS

- [ ] **Step 2: Cross-compile verification**

Run:
```bash
GOOS=linux go vet ./internal/healing/
GOOS=windows go vet ./internal/healing/
GOOS=darwin go vet ./internal/healing/
```
Expected: No errors on any platform

- [ ] **Step 3: Build all binaries**

Run:
```bash
export PATH=$PATH:$(go env GOPATH)/bin
CGO_ENABLED=0 GOOS=darwin GOARCH=amd64 go1.24.4 build -ldflags "-s -w -X main.Version=0.4.3" -o bin/osiris-agent-darwin-amd64 ./cmd/osiris-agent
CGO_ENABLED=0 GOOS=darwin GOARCH=arm64 go build -ldflags "-s -w -X main.Version=0.4.3" -o bin/osiris-agent-darwin-arm64 ./cmd/osiris-agent
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags "-s -w -X main.Version=0.4.3" -o bin/osiris-agent-linux-amd64 ./cmd/osiris-agent
```
Expected: All three binaries produced

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: cross-platform build verification for v0.4.3"
```
