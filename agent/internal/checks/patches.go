// Package checks implements Windows compliance checks.
package checks

import (
	"context"
	"fmt"
	"time"

	"github.com/osiriscare/agent/internal/wmi"
)

// PatchesCheck verifies Windows Update patches are current.
//
// HIPAA Control: §164.308(a)(1)(ii)(B) - Risk Management
// Checks: Critical patches installed, no pending security updates
type PatchesCheck struct{}

// Name returns the check identifier
func (c *PatchesCheck) Name() string {
	return "patches"
}

// Run executes the Windows Update compliance check
func (c *PatchesCheck) Run(ctx context.Context) CheckResult {
	result := CheckResult{
		CheckType:    "patches",
		HIPAAControl: "164.308(a)(1)(ii)(B)",
		Metadata:     make(map[string]string),
	}

	// Check for pending reboot first
	pendingReboot, err := checkPendingReboot(ctx)
	if err == nil {
		result.Metadata["pending_reboot"] = fmt.Sprintf("%v", pendingReboot)
	}

	// Query last Windows Update install date
	// Win32_QuickFixEngineering gives us installed hotfixes
	hotfixes, err := wmi.Query(ctx,
		"root\\CIMV2",
		"SELECT HotFixID, InstalledOn FROM Win32_QuickFixEngineering ORDER BY InstalledOn DESC",
	)
	if err != nil {
		result.Error = err
		result.Passed = false
		result.Actual = fmt.Sprintf("WMI query failed: %v", err)
		result.Expected = "Patches current"
		return result
	}

	result.Metadata["installed_hotfixes"] = fmt.Sprintf("%d", len(hotfixes))

	// Find most recent hotfix
	var latestDate time.Time
	var latestHotfix string

	for _, hf := range hotfixes {
		if id, ok := wmi.GetPropertyString(hf, "HotFixID"); ok {
			if dateStr, ok := wmi.GetPropertyString(hf, "InstalledOn"); ok {
				if t, err := parseUpdateDate(dateStr); err == nil {
					if t.After(latestDate) {
						latestDate = t
						latestHotfix = id
					}
				}
			}
		}
	}

	if !latestDate.IsZero() {
		result.Metadata["latest_hotfix"] = latestHotfix
		result.Metadata["latest_date"] = latestDate.Format("2006-01-02")

		// Warn if no patches in last 45 days
		age := time.Since(latestDate)
		result.Metadata["days_since_patch"] = fmt.Sprintf("%d", int(age.Hours()/24))

		if age > 45*24*time.Hour {
			result.Passed = false
			result.Expected = "Patches within last 45 days"
			result.Actual = fmt.Sprintf("Last patch %d days ago (%s)", int(age.Hours()/24), latestHotfix)
			return result
		}
	}

	// Check if reboot required
	if pendingReboot {
		result.Passed = false
		result.Expected = "No pending reboot required"
		result.Actual = "System requires reboot to complete updates"
		return result
	}

	result.Passed = true
	result.Expected = "Patches current, no pending reboot"
	result.Actual = fmt.Sprintf("Last patch: %s (%s)", latestHotfix, latestDate.Format("2006-01-02"))
	return result
}

// checkPendingReboot checks if a reboot is pending
func checkPendingReboot(ctx context.Context) (bool, error) {
	// Check registry keys that indicate pending reboot
	// Multiple sources can indicate a pending reboot on Windows

	// 1. Windows Update reboot required
	wuRebootRequired := `SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired`
	if exists, err := wmi.RegistryKeyExists(ctx, wmi.HKEY_LOCAL_MACHINE, wuRebootRequired); err == nil && exists {
		return true, nil
	}

	// 2. Component Based Servicing reboot pending
	cbsRebootPending := `SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending`
	if exists, err := wmi.RegistryKeyExists(ctx, wmi.HKEY_LOCAL_MACHINE, cbsRebootPending); err == nil && exists {
		return true, nil
	}

	// 3. Pending file rename operations (can indicate reboot needed)
	pendingFileRename := `SYSTEM\CurrentControlSet\Control\Session Manager`
	if val, err := wmi.GetRegistryString(ctx, wmi.HKEY_LOCAL_MACHINE, pendingFileRename, "PendingFileRenameOperations"); err == nil && val != "" {
		return true, nil
	}

	// 4. Computer rename pending
	computerNamePending := `SYSTEM\CurrentControlSet\Control\ComputerName\ComputerName`
	activeComputerName := `SYSTEM\CurrentControlSet\Control\ComputerName\ActiveComputerName`
	if pendingName, err := wmi.GetRegistryString(ctx, wmi.HKEY_LOCAL_MACHINE, computerNamePending, "ComputerName"); err == nil {
		if activeName, err := wmi.GetRegistryString(ctx, wmi.HKEY_LOCAL_MACHINE, activeComputerName, "ComputerName"); err == nil {
			if pendingName != activeName {
				return true, nil
			}
		}
	}

	return false, nil
}

// parseUpdateDate parses Windows Update date formats
func parseUpdateDate(dateStr string) (time.Time, error) {
	// Try various formats
	formats := []string{
		"1/2/2006",      // M/D/YYYY
		"01/02/2006",    // MM/DD/YYYY
		"2006-01-02",    // YYYY-MM-DD
		"1/2/2006 0:00", // With time
	}

	for _, format := range formats {
		if t, err := time.Parse(format, dateStr); err == nil {
			return t, nil
		}
	}

	return time.Time{}, fmt.Errorf("unable to parse date: %s", dateStr)
}
