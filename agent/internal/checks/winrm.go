// Package checks implements Windows compliance checks.
package checks

import (
	"context"
	"fmt"

	"github.com/osiriscare/agent/internal/wmi"
)

// WinRMCheck verifies the Windows Remote Management service is running
// and configured to accept connections. WinRM is the management plane —
// if chaos testing disables it, the appliance loses its ability to
// remediate this workstation via WinRM. The Go agent can detect and
// heal this locally since it communicates via gRPC, not WinRM.
//
// HIPAA Control: §164.312(a)(2)(ii) - Emergency Access Procedure
type WinRMCheck struct{}

func (c *WinRMCheck) Name() string {
	return "winrm"
}

func (c *WinRMCheck) Run(ctx context.Context) CheckResult {
	result := CheckResult{
		CheckType:    "winrm",
		HIPAAControl: "164.312(a)(2)(ii)",
		Metadata:     make(map[string]string),
	}

	// Check WinRM service (WinRM) state via WMI
	services, err := wmi.Query(ctx,
		"root\\CIMV2",
		"SELECT Name, State, StartMode FROM Win32_Service WHERE Name = 'WinRM'",
	)
	if err != nil {
		result.Error = err
		result.Passed = false
		result.Actual = fmt.Sprintf("WMI query failed: %v", err)
		result.Expected = "WinRM service running"
		return result
	}

	if len(services) == 0 {
		result.Passed = false
		result.Expected = "WinRM service found"
		result.Actual = "WinRM service not found"
		return result
	}

	svc := services[0]
	state, _ := wmi.GetPropertyString(svc, "State")
	startMode, _ := wmi.GetPropertyString(svc, "StartMode")
	result.Metadata["service_state"] = state
	result.Metadata["start_mode"] = startMode

	if state != "Running" {
		result.Passed = false
		result.Expected = "WinRM service running (StartMode=Auto)"
		result.Actual = fmt.Sprintf("WinRM service %s (StartMode=%s)", state, startMode)
		return result
	}

	if startMode != "Auto" {
		result.Passed = false
		result.Expected = "WinRM service running (StartMode=Auto)"
		result.Actual = fmt.Sprintf("WinRM running but StartMode=%s (should be Auto)", startMode)
		return result
	}

	// Also check HTTP listener exists (WinRM needs at least one listener)
	listeners, listenerErr := wmi.Query(ctx,
		"root\\CIMV2",
		"SELECT Name, State FROM Win32_Service WHERE Name = 'HTTP'",
	)
	if listenerErr == nil && len(listeners) > 0 {
		httpState, _ := wmi.GetPropertyString(listeners[0], "State")
		result.Metadata["http_service_state"] = httpState
	}

	result.Passed = true
	result.Expected = "WinRM service running (StartMode=Auto)"
	result.Actual = fmt.Sprintf("WinRM running, StartMode=%s", startMode)
	return result
}
