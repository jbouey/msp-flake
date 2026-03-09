//go:build darwin

package checks

import (
	"context"
	"strings"
)

// SIPCheck verifies System Integrity Protection is enabled.
// SIP cannot be enabled remotely — requires Recovery Mode boot.
//
// HIPAA Control: §164.312(c)(1) - Integrity Controls
type SIPCheck struct{}

func (c *SIPCheck) Name() string { return "macos_sip" }

func (c *SIPCheck) Run(ctx context.Context) CheckResult {
	result := CheckResult{
		CheckType:    "macos_sip",
		HIPAAControl: "164.312(c)(1)",
		Expected:     "System Integrity Protection status: enabled",
		Metadata:     make(map[string]string),
	}

	out, err := runCmd(ctx, "csrutil", "status")
	if err != nil {
		result.Error = err
		result.Actual = "csrutil command failed"
		return result
	}

	result.Metadata["csrutil_output"] = out

	if strings.Contains(out, "enabled") {
		result.Passed = true
		result.Actual = "SIP enabled"
	} else if strings.Contains(out, "disabled") {
		result.Actual = "SIP disabled (requires Recovery Mode to re-enable)"
		result.Metadata["escalate"] = "true"
	} else {
		result.Actual = out
	}

	return result
}
