//go:build darwin

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

// Execute runs a HealCommand on macOS using shell commands.
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
	case "macos_firewall":
		res = healMacFirewall(execCtx, cmd)
	case "macos_auto_update":
		res = healMacAutoUpdate(execCtx, cmd)
	case "macos_screen_lock":
		res = healMacScreenLock(execCtx, cmd)
	case "macos_ntp_sync":
		res = healMacNTP(execCtx, cmd)
	case "macos_file_sharing":
		res = healMacFileSharing(execCtx, cmd)
	case "macos_gatekeeper":
		res = healMacGatekeeper(execCtx, cmd)
	case "macos_filevault":
		res = healMacFileVault(execCtx, cmd)
	case "macos_time_machine":
		res = healMacTimeMachine(execCtx, cmd)
	default:
		res = &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("check type %s requires manual remediation", cmd.CheckType),
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
	cmd := exec.CommandContext(ctx, "/bin/bash", "-c", script)
	out, err := cmd.CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

func healMacFirewall(ctx context.Context, cmd *pb.HealCommand) *Result {
	fwPath := "/usr/libexec/ApplicationFirewall/socketfilterfw"
	out, err := runShell(ctx, fwPath+" --setglobalstate on && "+fwPath+" --setstealthmode on")
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

func healMacAutoUpdate(ctx context.Context, cmd *pb.HealCommand) *Result {
	script := `defaults write /Library/Preferences/com.apple.SoftwareUpdate AutomaticCheckEnabled -bool true && \
defaults write /Library/Preferences/com.apple.SoftwareUpdate AutomaticDownload -bool true && \
defaults write /Library/Preferences/com.apple.SoftwareUpdate CriticalUpdateInstall -bool true`
	out, err := runShell(ctx, script)
	if err != nil {
		return &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("auto-update enable failed: %v — %s", err, out),
		}
	}
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
	}
}

func healMacScreenLock(ctx context.Context, cmd *pb.HealCommand) *Result {
	script := `defaults write com.apple.screensaver askForPassword -int 1 && \
defaults write com.apple.screensaver askForPasswordDelay -int 5 && \
defaults -currentHost write com.apple.screensaver idleTime -int 900`
	out, err := runShell(ctx, script)
	if err != nil {
		return &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("screen lock config failed: %v — %s", err, out),
		}
	}
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
	}
}

func healMacNTP(ctx context.Context, cmd *pb.HealCommand) *Result {
	out, err := runShell(ctx, "systemsetup -setusingnetworktime on")
	if err != nil {
		return &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("NTP enable failed: %v — %s", err, out),
		}
	}
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
	}
}

func healMacFileSharing(ctx context.Context, cmd *pb.HealCommand) *Result {
	out, err := runShell(ctx, "launchctl unload -w /System/Library/LaunchDaemons/com.apple.smbd.plist 2>/dev/null; echo OK")
	if err != nil {
		return &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("disable file sharing failed: %v — %s", err, out),
		}
	}
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
	}
}

func healMacGatekeeper(ctx context.Context, cmd *pb.HealCommand) *Result {
	out, err := runShell(ctx, "spctl --master-enable")
	if err != nil {
		return &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("gatekeeper enable failed: %v — %s", err, out),
		}
	}
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
	}
}

// healMacFileVault enables FileVault using deferred enablement.
// Deferred mode queues encryption for the next user login — no interactive password needed.
// If already enabled, this is a no-op.
func healMacFileVault(ctx context.Context, cmd *pb.HealCommand) *Result {
	script := `FV_STATUS=$(fdesetup status 2>&1)
if echo "$FV_STATUS" | grep -q "FileVault is On"; then
    echo "FileVault already enabled"
    exit 0
fi
# Attempt deferred enablement (queues for next login).
# Requires institutional recovery key or MDM escrow to be pre-configured.
if fdesetup enable -defer /var/db/FileVaultDeferred.plist -forcerestart 0 2>&1; then
    echo "FileVault deferred enablement configured — will activate at next user login"
else
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
	script := `DEST=$(tmutil destinationinfo 2>/dev/null)
if [ -z "$DEST" ] || echo "$DEST" | grep -q "No destinations configured"; then
    echo "No backup destination configured — cannot enable Time Machine without a target disk"
    exit 1
fi
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
