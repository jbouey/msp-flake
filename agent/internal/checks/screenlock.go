// Package checks implements Windows compliance checks.
package checks

import (
	"context"
	"fmt"

	"github.com/osiriscare/agent/internal/wmi"
)

// ScreenLockCheck verifies screen lock policy is configured.
//
// HIPAA Control: §164.312(a)(2)(i) - Unique User Identification
// Checks: Screen saver timeout enabled, password on resume required
type ScreenLockCheck struct{}

// Name returns the check identifier
func (c *ScreenLockCheck) Name() string {
	return "screenlock"
}

// Run executes the screen lock compliance check
func (c *ScreenLockCheck) Run(ctx context.Context) CheckResult {
	result := CheckResult{
		CheckType:    "screenlock",
		HIPAAControl: "164.312(a)(2)(i)",
		Metadata:     make(map[string]string),
	}

	// Check power settings for display timeout
	// We'll query Win32_PowerPlan for active power scheme
	// Then check the associated power settings

	// Query active power scheme
	powerPlans, err := wmi.Query(ctx,
		"root\\CIMV2\\power",
		"SELECT ElementName, IsActive FROM Win32_PowerPlan WHERE IsActive = TRUE",
	)
	if err != nil {
		// Power WMI namespace might not be available, fall back to basic check
		result.Metadata["power_wmi_error"] = err.Error()
	} else if len(powerPlans) > 0 {
		if planName, ok := wmi.GetPropertyString(powerPlans[0], "ElementName"); ok {
			result.Metadata["active_power_plan"] = planName
		}
	}

	// Query desktop settings for screen saver
	// We'll check via registry-based approach
	screenSaverActive, screenSaverTimeout, passwordProtected, err := checkScreenSaverSettings(ctx)

	if err != nil {
		result.Error = err
		result.Passed = false
		result.Actual = fmt.Sprintf("Failed to check screen saver settings: %v", err)
		result.Expected = "Screen saver with password protection"
		return result
	}

	result.Metadata["screensaver_active"] = fmt.Sprintf("%v", screenSaverActive)
	result.Metadata["screensaver_timeout_seconds"] = fmt.Sprintf("%d", screenSaverTimeout)
	result.Metadata["password_protected"] = fmt.Sprintf("%v", passwordProtected)

	// HIPAA typically requires:
	// - Screen lock after 15 minutes of inactivity
	// - Password required to unlock

	maxTimeout := 15 * 60 // 15 minutes in seconds

	if !screenSaverActive {
		result.Passed = false
		result.Expected = "Screen saver enabled"
		result.Actual = "Screen saver disabled"
		return result
	}

	if screenSaverTimeout > maxTimeout {
		result.Passed = false
		result.Expected = fmt.Sprintf("Screen timeout <= %d minutes", maxTimeout/60)
		result.Actual = fmt.Sprintf("Screen timeout: %d minutes", screenSaverTimeout/60)
		return result
	}

	if !passwordProtected {
		result.Passed = false
		result.Expected = "Password required on resume"
		result.Actual = "Password not required on resume"
		return result
	}

	result.Passed = true
	result.Expected = "Screen lock with password after <= 15 min"
	result.Actual = fmt.Sprintf("Screen lock after %d min, password required", screenSaverTimeout/60)
	return result
}

// checkScreenSaverSettings checks screen saver configuration.
// Checks both HKLM policy path (where Group Policy / healing writes) and HKCU
// user settings. HKLM policy takes precedence when present.
func checkScreenSaverSettings(ctx context.Context) (active bool, timeoutSeconds int, passwordProtected bool, err error) {
	// First check HKLM machine policy (set by GPO and healing scripts)
	policyPath := `SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System`
	policyTimeout, policyErr := wmi.GetRegistryDWORD(ctx, wmi.HKEY_LOCAL_MACHINE, policyPath, "InactivityTimeoutSecs")
	if policyErr == nil && policyTimeout > 0 {
		// Machine policy is enforced — this is the authoritative source
		active = true
		timeoutSeconds = int(policyTimeout)
		passwordProtected = true // Machine policy implies password on resume
		return active, timeoutSeconds, passwordProtected, nil
	}

	// Fall back to HKCU user settings
	desktopPath := `Control Panel\Desktop`

	// ScreenSaveActive: "1" = enabled, "0" = disabled
	activeStr, readErr := wmi.GetRegistryString(ctx, wmi.HKEY_CURRENT_USER, desktopPath, "ScreenSaveActive")
	if readErr != nil {
		activeStr = "0"
	}
	active = (activeStr == "1")

	// ScreenSaveTimeOut: timeout in seconds (stored as string)
	timeoutStr, readErr := wmi.GetRegistryString(ctx, wmi.HKEY_CURRENT_USER, desktopPath, "ScreenSaveTimeOut")
	if readErr != nil {
		timeoutSeconds = 0
	} else {
		if t, parseErr := parseTimeoutString(timeoutStr); parseErr == nil {
			timeoutSeconds = t
		} else {
			timeoutSeconds = 0
		}
	}

	// ScreenSaverIsSecure: "1" = password required, "0" = no password
	secureStr, readErr := wmi.GetRegistryString(ctx, wmi.HKEY_CURRENT_USER, desktopPath, "ScreenSaverIsSecure")
	if readErr != nil {
		passwordProtected = false
	} else {
		passwordProtected = (secureStr == "1")
	}

	return active, timeoutSeconds, passwordProtected, nil
}

// parseTimeoutString parses a screen saver timeout string to seconds
func parseTimeoutString(s string) (int, error) {
	var timeout int
	_, err := fmt.Sscanf(s, "%d", &timeout)
	return timeout, err
}
