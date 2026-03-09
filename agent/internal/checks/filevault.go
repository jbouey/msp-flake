//go:build darwin

package checks

import (
	"context"
	"strings"
)

// FileVaultCheck verifies FileVault full-disk encryption is enabled.
//
// HIPAA Control: §164.312(a)(2)(iv) - Encryption and Decryption
type FileVaultCheck struct{}

func (c *FileVaultCheck) Name() string { return "macos_filevault" }

func (c *FileVaultCheck) Run(ctx context.Context) CheckResult {
	result := CheckResult{
		CheckType:    "macos_filevault",
		HIPAAControl: "164.312(a)(2)(iv)",
		Expected:     "FileVault is On",
		Metadata:     make(map[string]string),
	}

	out, err := runCmd(ctx, "fdesetup", "status")
	if err != nil {
		result.Error = err
		result.Actual = "fdesetup command failed"
		return result
	}

	result.Metadata["fdesetup_output"] = out

	if strings.Contains(out, "FileVault is On") {
		result.Passed = true
		result.Actual = "FileVault is On"
	} else if strings.Contains(out, "FileVault is Off") {
		result.Actual = "FileVault is Off"
	} else {
		result.Actual = out
	}

	return result
}
