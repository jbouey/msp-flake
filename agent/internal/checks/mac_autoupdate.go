//go:build darwin

package checks

import (
	"context"
	"strings"
)

// MacAutoUpdateCheck verifies automatic software updates are enabled.
//
// HIPAA Control: §164.308(a)(5)(ii)(B) - Security Updates
type MacAutoUpdateCheck struct{}

func (c *MacAutoUpdateCheck) Name() string { return "macos_auto_update" }

func (c *MacAutoUpdateCheck) Run(ctx context.Context) CheckResult {
	result := CheckResult{
		CheckType:    "macos_auto_update",
		HIPAAControl: "164.308(a)(5)(ii)(B)",
		Expected:     "AutomaticCheckEnabled=1",
		Metadata:     make(map[string]string),
	}

	plist := "/Library/Preferences/com.apple.SoftwareUpdate"

	// Check AutomaticCheckEnabled
	autoCheck, err := runCmd(ctx, "defaults", "read", plist, "AutomaticCheckEnabled")
	if err != nil {
		result.Actual = "AutomaticCheckEnabled not set"
		result.Metadata["auto_check"] = "not_set"
		return result
	}

	result.Metadata["auto_check"] = strings.TrimSpace(autoCheck)

	// Check AutomaticDownload
	autoDL, _ := runCmd(ctx, "defaults", "read", plist, "AutomaticDownload")
	result.Metadata["auto_download"] = strings.TrimSpace(autoDL)

	// Check CriticalUpdateInstall
	critUpdate, _ := runCmd(ctx, "defaults", "read", plist, "CriticalUpdateInstall")
	result.Metadata["critical_update"] = strings.TrimSpace(critUpdate)

	if strings.TrimSpace(autoCheck) == "1" {
		result.Passed = true
		result.Actual = "AutomaticCheckEnabled=1"
	} else {
		result.Actual = "AutomaticCheckEnabled=" + strings.TrimSpace(autoCheck)
	}

	return result
}
