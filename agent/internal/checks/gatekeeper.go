//go:build darwin

package checks

import (
	"context"
	"strings"
)

// GatekeeperCheck verifies Gatekeeper is enabled to prevent unsigned apps.
//
// HIPAA Control: §164.308(a)(5)(ii)(B) - Protection from Malicious Software
type GatekeeperCheck struct{}

func (c *GatekeeperCheck) Name() string { return "macos_gatekeeper" }

func (c *GatekeeperCheck) Run(ctx context.Context) CheckResult {
	result := CheckResult{
		CheckType:    "macos_gatekeeper",
		HIPAAControl: "164.308(a)(5)(ii)(B)",
		Expected:     "assessments enabled",
		Metadata:     make(map[string]string),
	}

	out, err := runCmd(ctx, "spctl", "--status")
	if err != nil {
		// spctl returns non-zero when disabled
		if strings.Contains(out, "assessments disabled") {
			result.Actual = "assessments disabled"
			result.Metadata["spctl_output"] = out
			return result
		}
		result.Error = err
		result.Actual = "spctl command failed"
		return result
	}

	result.Metadata["spctl_output"] = out

	if strings.Contains(out, "assessments enabled") {
		result.Passed = true
		result.Actual = "assessments enabled"
	} else {
		result.Actual = out
	}

	return result
}
