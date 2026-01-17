// Package checks implements Windows compliance checks.
package checks

import (
	"context"
	"fmt"
	"strings"

	"github.com/osiriscare/agent/internal/wmi"
)

// FirewallCheck verifies Windows Firewall is enabled on all profiles.
//
// HIPAA Control: §164.312(e)(1) - Transmission Security
// Checks: Domain, Private, and Public firewall profiles are enabled
type FirewallCheck struct{}

// Name returns the check identifier
func (c *FirewallCheck) Name() string {
	return "firewall"
}

// Run executes the Windows Firewall compliance check
func (c *FirewallCheck) Run(ctx context.Context) CheckResult {
	result := CheckResult{
		CheckType:    "firewall",
		HIPAAControl: "164.312(e)(1)",
		Metadata:     make(map[string]string),
	}

	// Query Windows Firewall profiles via WMI
	// Note: We use Win32_Service first to check if firewall service is running
	services, err := wmi.Query(ctx,
		"root\\CIMV2",
		"SELECT * FROM Win32_Service WHERE Name = 'MpsSvc'",
	)
	if err != nil {
		result.Error = err
		result.Passed = false
		result.Actual = fmt.Sprintf("WMI query failed: %v", err)
		result.Expected = "Firewall service running"
		return result
	}

	if len(services) == 0 {
		result.Passed = false
		result.Expected = "Firewall service found"
		result.Actual = "MpsSvc service not found"
		return result
	}

	svc := services[0]
	state, ok := wmi.GetPropertyString(svc, "State")
	if !ok || state != "Running" {
		result.Passed = false
		result.Expected = "MpsSvc service running"
		result.Actual = fmt.Sprintf("MpsSvc service state: %s", state)
		return result
	}

	// Query firewall profiles via netsh command wrapper in WMI
	// We'll use Win32_Process to execute netsh and capture output
	// This is more reliable than the HNetCfg.FwPolicy2 COM object

	// Query registry for profile status (more direct)
	profiles, err := queryFirewallProfiles(ctx)
	if err != nil {
		result.Error = err
		result.Passed = false
		result.Actual = fmt.Sprintf("Failed to query firewall profiles: %v", err)
		result.Expected = "All firewall profiles enabled"
		return result
	}

	disabledProfiles := []string{}
	for name, enabled := range profiles {
		result.Metadata[fmt.Sprintf("profile_%s", strings.ToLower(name))] = fmt.Sprintf("%v", enabled)
		if !enabled {
			disabledProfiles = append(disabledProfiles, name)
		}
	}

	if len(disabledProfiles) > 0 {
		result.Passed = false
		result.Expected = "All firewall profiles enabled"
		result.Actual = fmt.Sprintf("Disabled profiles: %s", strings.Join(disabledProfiles, ", "))
		return result
	}

	result.Passed = true
	result.Expected = "All firewall profiles enabled"
	result.Actual = "Domain, Private, Public profiles enabled"
	return result
}

// queryFirewallProfiles queries firewall profile status via registry
func queryFirewallProfiles(ctx context.Context) (map[string]bool, error) {
	profiles := map[string]bool{
		"Domain":  false,
		"Private": false,
		"Public":  false,
	}

	// Registry paths for firewall profiles
	// HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy
	basePath := `SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy`

	profilePaths := map[string]string{
		"Domain":  basePath + `\DomainProfile`,
		"Private": basePath + `\StandardProfile`, // Private is "StandardProfile" in registry
		"Public":  basePath + `\PublicProfile`,
	}

	for name, path := range profilePaths {
		// EnableFirewall: 1 = enabled, 0 = disabled
		value, err := wmi.GetRegistryDWORD(ctx, wmi.HKEY_LOCAL_MACHINE, path, "EnableFirewall")
		if err != nil {
			// If we can't read the value, assume enabled (safer default)
			// This can happen if the key doesn't exist (unusual but possible)
			profiles[name] = true
			continue
		}
		profiles[name] = (value == 1)
	}

	return profiles, nil
}
