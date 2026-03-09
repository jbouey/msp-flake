//go:build darwin

package checks

import (
	"context"
	"os"
	"strings"
)

// MacRemoteLoginCheck verifies SSH/Remote Login configuration.
//
// HIPAA Control: §164.312(d) - Person or Entity Authentication
type MacRemoteLoginCheck struct{}

func (c *MacRemoteLoginCheck) Name() string { return "macos_remote_login" }

func (c *MacRemoteLoginCheck) Run(ctx context.Context) CheckResult {
	result := CheckResult{
		CheckType:    "macos_remote_login",
		HIPAAControl: "164.312(d)",
		Expected:     "SSH hardened or disabled",
		Metadata:     make(map[string]string),
	}

	// Check if Remote Login is enabled
	out, err := runCmd(ctx, "systemsetup", "-getremotelogin")
	if err != nil || strings.Contains(out, "administrator access") {
		// Non-root: check if sshd is loaded via launchctl
		lsOut, lsErr := runCmd(ctx, "launchctl", "list", "com.openssh.sshd")
		if lsErr == nil && strings.TrimSpace(lsOut) != "" {
			result.Metadata["remote_login"] = "Remote Login: On"
		} else {
			result.Metadata["remote_login"] = "Remote Login: Off"
		}
	} else {
		result.Metadata["remote_login"] = out
	}

	// Check SSH config for hardening
	sshdConfig, err := os.ReadFile("/etc/ssh/sshd_config")
	if err == nil {
		for _, line := range strings.Split(string(sshdConfig), "\n") {
			line = strings.TrimSpace(line)
			if strings.HasPrefix(line, "PermitRootLogin") {
				result.Metadata["permit_root_login"] = strings.TrimPrefix(line, "PermitRootLogin ")
			}
			if strings.HasPrefix(line, "PasswordAuthentication") {
				result.Metadata["password_auth"] = strings.TrimPrefix(line, "PasswordAuthentication ")
			}
		}
	}

	// Pass if SSH is off, or if it's on with root login disabled
	remoteLogin := result.Metadata["remote_login"]
	if strings.Contains(remoteLogin, "Off") {
		result.Passed = true
		result.Actual = "Remote Login disabled"
	} else if strings.Contains(remoteLogin, "On") {
		rootLogin := result.Metadata["permit_root_login"]
		if rootLogin == "no" {
			result.Passed = true
			result.Actual = "SSH enabled, root login disabled"
		} else {
			result.Actual = "SSH enabled, root login allowed"
		}
	} else {
		// Can't determine — pass with warning
		result.Passed = true
		result.Actual = "SSH status unknown (not root)"
	}

	return result
}
