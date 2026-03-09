//go:build darwin

package checks

import (
	"context"
	"strings"
)

// MacScreenLockCheck verifies screen lock is configured to require a password.
//
// HIPAA Control: §164.312(a)(1) - Access Control
type MacScreenLockCheck struct{}

func (c *MacScreenLockCheck) Name() string { return "macos_screen_lock" }

func (c *MacScreenLockCheck) Run(ctx context.Context) CheckResult {
	result := CheckResult{
		CheckType:    "macos_screen_lock",
		HIPAAControl: "164.312(a)(1)",
		Expected:     "askForPassword=1",
		Metadata:     make(map[string]string),
	}

	// Check if password is required after screensaver
	askPw, err := runCmd(ctx, "defaults", "read", "com.apple.screensaver", "askForPassword")
	if err != nil {
		result.Actual = "askForPassword not set"
		result.Metadata["ask_for_password"] = "not_set"
		return result
	}

	result.Metadata["ask_for_password"] = strings.TrimSpace(askPw)

	// Check password delay
	delay, _ := runCmd(ctx, "defaults", "read", "com.apple.screensaver", "askForPasswordDelay")
	result.Metadata["ask_for_password_delay"] = strings.TrimSpace(delay)

	// Check screensaver idle time
	idleTime, _ := runCmd(ctx, "defaults", "-currentHost", "read", "com.apple.screensaver", "idleTime")
	result.Metadata["idle_time"] = strings.TrimSpace(idleTime)

	if strings.TrimSpace(askPw) == "1" {
		result.Passed = true
		result.Actual = "askForPassword=1"
	} else {
		result.Actual = "askForPassword=" + strings.TrimSpace(askPw)
	}

	return result
}
