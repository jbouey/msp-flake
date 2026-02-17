//go:build windows

package discovery

import (
	"os"
	"os/exec"
	"strings"
)

// DiscoverDomain detects the AD domain this machine is joined to.
// Tries USERDNSDOMAIN env var first, then WMI.
func DiscoverDomain() string {
	// Method 1: USERDNSDOMAIN (set for domain-joined machines at user login)
	if d := os.Getenv("USERDNSDOMAIN"); d != "" {
		return strings.ToLower(d)
	}

	// Method 2: USERDOMAIN + USERDNSDOMAIN may not be set for SYSTEM account
	// Use WMI to query the computer's domain
	out, err := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
		"(Get-WmiObject Win32_ComputerSystem).Domain").CombinedOutput()
	if err == nil {
		domain := strings.TrimSpace(string(out))
		if domain != "" && !strings.EqualFold(domain, "WORKGROUP") {
			return strings.ToLower(domain)
		}
	}

	return ""
}
