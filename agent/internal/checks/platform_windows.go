//go:build windows

package checks

// registerPlatformChecks registers Windows compliance checks.
func registerPlatformChecks(r *Registry) {
	r.Register(&BitLockerCheck{})
	r.Register(&DefenderCheck{})
	r.Register(&PatchesCheck{})
	r.Register(&FirewallCheck{})
	r.Register(&ScreenLockCheck{})
	r.Register(&RMMCheck{})
	r.Register(&WinRMCheck{})
	r.Register(&DNSCheck{})
}

// DefaultEnabledChecks returns the default set of enabled checks for Windows.
func DefaultEnabledChecks() []string {
	return []string{"bitlocker", "defender", "patches", "firewall", "screenlock", "rmm_detection"}
}
