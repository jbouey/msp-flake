//go:build darwin

package checks

// registerPlatformChecks registers macOS compliance checks.
func registerPlatformChecks(r *Registry) {
	r.Register(&FileVaultCheck{})
	r.Register(&GatekeeperCheck{})
	r.Register(&SIPCheck{})
	r.Register(&MacFirewallCheck{})
	r.Register(&MacAutoUpdateCheck{})
	r.Register(&MacScreenLockCheck{})
	r.Register(&MacRemoteLoginCheck{})
	r.Register(&MacFileSharingCheck{})
	r.Register(&MacTimeMachineCheck{})
	r.Register(&MacNTPCheck{})
	r.Register(&MacAdminUsersCheck{})
	r.Register(&MacDiskSpaceCheck{})
}

// DefaultEnabledChecks returns the default set of enabled checks for macOS.
func DefaultEnabledChecks() []string {
	return []string{
		"macos_filevault",
		"macos_gatekeeper",
		"macos_sip",
		"macos_firewall",
		"macos_auto_update",
		"macos_screen_lock",
		"macos_remote_login",
		"macos_file_sharing",
		"macos_time_machine",
		"macos_ntp_sync",
		"macos_admin_users",
		"macos_disk_space",
	}
}
