//go:build darwin

package checks

import (
	"context"
	"strings"
)

// MacFileSharingCheck verifies SMB file sharing is disabled.
//
// HIPAA Control: §164.312(e)(1) - Transmission Security
type MacFileSharingCheck struct{}

func (c *MacFileSharingCheck) Name() string { return "macos_file_sharing" }

func (c *MacFileSharingCheck) Run(ctx context.Context) CheckResult {
	result := CheckResult{
		CheckType:    "macos_file_sharing",
		HIPAAControl: "164.312(e)(1)",
		Expected:     "SMB file sharing disabled",
		Metadata:     make(map[string]string),
	}

	// Check if smbd is loaded
	out, err := runCmd(ctx, "launchctl", "list")
	if err != nil {
		result.Error = err
		result.Actual = "launchctl command failed"
		return result
	}

	smbRunning := false
	for _, line := range strings.Split(out, "\n") {
		if strings.Contains(line, "smbd") {
			smbRunning = true
			result.Metadata["smbd_status"] = "running"
			break
		}
	}

	if !smbRunning {
		result.Passed = true
		result.Actual = "SMB file sharing disabled"
		result.Metadata["smbd_status"] = "not_running"
	} else {
		result.Actual = "SMB file sharing enabled"
	}

	return result
}
