//go:build darwin

package checks

import (
	"context"
	"strings"
)

// MacTimeMachineCheck verifies Time Machine backup is configured and running.
//
// HIPAA Control: §164.308(a)(7)(ii)(A) - Data Backup Plan
type MacTimeMachineCheck struct{}

func (c *MacTimeMachineCheck) Name() string { return "macos_time_machine" }

func (c *MacTimeMachineCheck) Run(ctx context.Context) CheckResult {
	result := CheckResult{
		CheckType:    "macos_time_machine",
		HIPAAControl: "164.308(a)(7)(ii)(A)",
		Expected:     "Time Machine configured and backing up",
		Metadata:     make(map[string]string),
	}

	// Check Time Machine status
	status, err := runCmd(ctx, "tmutil", "status")
	if err != nil {
		result.Actual = "Time Machine not configured"
		result.Metadata["tm_status"] = "not_configured"
		return result
	}
	result.Metadata["tm_status"] = status

	// Check latest backup
	latestBackup, err := runCmd(ctx, "tmutil", "latestbackup")
	if err != nil {
		result.Actual = "No backups found"
		result.Metadata["latest_backup"] = "never"
		return result
	}
	result.Metadata["latest_backup"] = latestBackup

	// Check destination
	destInfo, err := runCmd(ctx, "tmutil", "destinationinfo")
	if err != nil || strings.Contains(destInfo, "No destinations") {
		result.Actual = "No backup destination configured"
		result.Metadata["destination"] = "none"
		return result
	}
	result.Metadata["destination"] = destInfo

	// Pass if we have a valid backup path (not an error message)
	if strings.HasPrefix(latestBackup, "/") {
		result.Passed = true
		result.Actual = "Time Machine configured, last backup: " + latestBackup
	} else {
		result.Actual = "Time Machine configured but backup failing: " + latestBackup
	}

	return result
}
