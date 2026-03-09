//go:build darwin

package checks

import (
	"context"
	"strings"
)

// MacNTPCheck verifies NTP time synchronization is enabled.
//
// HIPAA Control: §164.312(b) - Audit Controls
type MacNTPCheck struct{}

func (c *MacNTPCheck) Name() string { return "macos_ntp_sync" }

func (c *MacNTPCheck) Run(ctx context.Context) CheckResult {
	result := CheckResult{
		CheckType:    "macos_ntp_sync",
		HIPAAControl: "164.312(b)",
		Expected:     "Network time enabled",
		Metadata:     make(map[string]string),
	}

	// Check if network time is enabled
	// systemsetup requires root — try it first, fall back to timed service check
	out, err := runCmd(ctx, "systemsetup", "-getusingnetworktime")
	if err != nil || strings.Contains(out, "administrator access") {
		// Fall back: check if timed (NTP daemon) is loaded
		timedOut, timedErr := runCmd(ctx, "launchctl", "list", "com.apple.timed")
		if timedErr == nil && strings.TrimSpace(timedOut) != "" {
			result.Passed = true
			result.Actual = "timed service running"
			result.Metadata["timed"] = "running"
			return result
		}
		// Also try sntp as a functional test
		sntpOut, sntpErr := runCmd(ctx, "sntp", "time.apple.com")
		if sntpErr == nil && strings.TrimSpace(sntpOut) != "" {
			result.Passed = true
			result.Actual = "NTP functional (sntp test passed)"
			result.Metadata["sntp_result"] = sntpOut
			return result
		}
		result.Actual = "Cannot determine NTP status (not root)"
		return result
	}

	result.Metadata["network_time"] = out

	// Check NTP server
	server, _ := runCmd(ctx, "systemsetup", "-getnetworktimeserver")
	result.Metadata["ntp_server"] = server

	if strings.Contains(out, "On") {
		result.Passed = true
		result.Actual = "Network time enabled"
	} else {
		result.Actual = "Network time disabled"
	}

	return result
}
