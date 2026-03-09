//go:build darwin

package checks

import (
	"context"
	"strings"
)

// MacFirewallCheck verifies the macOS Application Firewall is enabled.
//
// HIPAA Control: §164.312(e)(1) - Transmission Security
type MacFirewallCheck struct{}

func (c *MacFirewallCheck) Name() string { return "macos_firewall" }

func (c *MacFirewallCheck) Run(ctx context.Context) CheckResult {
	result := CheckResult{
		CheckType:    "macos_firewall",
		HIPAAControl: "164.312(e)(1)",
		Expected:     "Firewall enabled",
		Metadata:     make(map[string]string),
	}

	fwPath := "/usr/libexec/ApplicationFirewall/socketfilterfw"
	out, err := runCmd(ctx, fwPath, "--getglobalstate")
	if err != nil {
		result.Error = err
		result.Actual = "socketfilterfw command failed"
		return result
	}

	result.Metadata["firewall_state"] = out

	if strings.Contains(out, "enabled") {
		result.Passed = true
		result.Actual = "Firewall enabled"
	} else {
		result.Actual = "Firewall disabled"
	}

	// Check stealth mode
	stealth, err := runCmd(ctx, fwPath, "--getstealthmode")
	if err == nil {
		result.Metadata["stealth_mode"] = stealth
	}

	return result
}
