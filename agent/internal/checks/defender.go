// Package checks implements Windows compliance checks.
package checks

import (
	"context"
	"fmt"
	"time"

	"github.com/osiriscare/agent/internal/wmi"
)

// DefenderCheck verifies Windows Defender is enabled with current signatures.
//
// HIPAA Control: §164.308(a)(5)(ii)(B) - Protection from Malicious Software
// Checks: Defender running, real-time protection on, signatures < 7 days old
type DefenderCheck struct{}

// Name returns the check identifier
func (c *DefenderCheck) Name() string {
	return "defender"
}

// Run executes the Windows Defender compliance check
func (c *DefenderCheck) Run(ctx context.Context) CheckResult {
	result := CheckResult{
		CheckType:    "defender",
		HIPAAControl: "164.308(a)(5)(ii)(B)",
		Metadata:     make(map[string]string),
	}

	// Query MSFT_MpComputerStatus
	// Namespace: root\Microsoft\Windows\Defender
	status, err := wmi.Query(ctx,
		"root\\Microsoft\\Windows\\Defender",
		"SELECT AntivirusEnabled, RealTimeProtectionEnabled, AntivirusSignatureLastUpdated, AntivirusSignatureVersion FROM MSFT_MpComputerStatus",
	)
	if err != nil {
		result.Error = err
		result.Passed = false
		result.Actual = fmt.Sprintf("WMI query failed: %v", err)
		result.Expected = "Defender enabled with current signatures"
		return result
	}

	if len(status) == 0 {
		result.Passed = false
		result.Expected = "Windows Defender installed"
		result.Actual = "Defender status not found"
		return result
	}

	s := status[0]

	// Check AV enabled
	avEnabled, ok := wmi.GetPropertyBool(s, "AntivirusEnabled")
	if !ok {
		avEnabled = false
	}

	rtEnabled, ok := wmi.GetPropertyBool(s, "RealTimeProtectionEnabled")
	if !ok {
		rtEnabled = false
	}

	result.Metadata["antivirus_enabled"] = fmt.Sprintf("%v", avEnabled)
	result.Metadata["realtime_enabled"] = fmt.Sprintf("%v", rtEnabled)

	if !avEnabled || !rtEnabled {
		result.Passed = false
		result.Expected = "AntivirusEnabled=true, RealTimeProtectionEnabled=true"
		result.Actual = fmt.Sprintf("AntivirusEnabled=%v, RealTimeProtectionEnabled=%v", avEnabled, rtEnabled)
		return result
	}

	// Check signature version
	if sigVersion, ok := wmi.GetPropertyString(s, "AntivirusSignatureVersion"); ok {
		result.Metadata["signature_version"] = sigVersion
	}

	// Check signature age (warn if > 7 days)
	// Note: The WMI property is a string in DMT format
	if sigDateStr, ok := wmi.GetPropertyString(s, "AntivirusSignatureLastUpdated"); ok {
		result.Metadata["signature_date"] = sigDateStr

		// Parse DMT format: yyyymmddHHMMSS.ffffff+000
		// We'll do a simple check - if it's more than 7 days old
		if sigDate, err := parseDMTDate(sigDateStr); err == nil {
			age := time.Since(sigDate)
			result.Metadata["signature_age_hours"] = fmt.Sprintf("%.1f", age.Hours())

			if age > 7*24*time.Hour {
				result.Passed = false
				result.Expected = "Signatures updated within 7 days"
				result.Actual = fmt.Sprintf("Signatures %d days old", int(age.Hours()/24))
				return result
			}
		}
	}

	result.Passed = true
	result.Expected = "Defender enabled with current signatures"
	result.Actual = "Defender enabled with current signatures"
	return result
}

// parseDMTDate parses WMI DMT format date string
func parseDMTDate(dmt string) (time.Time, error) {
	// DMT format: yyyymmddHHMMSS.ffffff+000 or yyyymmddHHMMSS.ffffff-000
	if len(dmt) < 14 {
		return time.Time{}, fmt.Errorf("invalid DMT format")
	}

	// Extract basic parts
	layout := "20060102150405"
	dateStr := dmt[:14]

	t, err := time.Parse(layout, dateStr)
	if err != nil {
		return time.Time{}, err
	}

	return t, nil
}
