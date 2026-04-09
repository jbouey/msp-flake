package daemon

import (
	"encoding/json"
	"testing"
)

func newMacOSTestScanner() *driftScanner {
	return &driftScanner{
		svc: &Services{
			Checks: &stubCheckConfig{},
			Config: &Config{},
		},
	}
}

func mustMacOSJSON(state macosScanState) string {
	b, _ := json.Marshal(state)
	return string(b)
}

func TestParseMacOSFindings_AllPassing(t *testing.T) {
	ds := newMacOSTestScanner()

	state := macosScanState{
		FileVault:        "on",
		Gatekeeper:       "enabled",
		SIP:              "enabled",
		Firewall:         "enabled",
		AutoUpdate:       "enabled",
		ScreenLock:       "enabled",
		ScreenLockDelay:  3,
		RemoteLogin:      "on",
		FileSharing:      "off",
		TimeMachine:      "current",
		TMDiskAccessible: "yes",
		TMIntegrity:      "passed",
		NTP:              "synced",
		OpenPorts:        "none",
		OpenPortCount:    0,
		AdminUsers:       "admin jrelly",
		AdminCount:       2,
		CertExpiry:       "ok",
	}
	state.Disk.Warning = "ok"
	state.Disk.MaxPct = 55

	output := mustMacOSJSON(state)
	findings := ds.parseMacOSFindings(output, "imac.local")

	if len(findings) != 0 {
		for _, f := range findings {
			t.Errorf("unexpected finding: %s expected=%s actual=%s", f.CheckType, f.Expected, f.Actual)
		}
	}
}

func TestParseMacOSFindings_AllFailing(t *testing.T) {
	ds := newMacOSTestScanner()

	state := macosScanState{
		FileVault:        "off",
		Gatekeeper:       "disabled",
		SIP:              "disabled",
		Firewall:         "disabled",
		AutoUpdate:       "disabled",
		ScreenLock:       "disabled",
		ScreenLockDelay:  0,
		RemoteLogin:      "on",
		FileSharing:      "on",
		TimeMachine:      "no_backup",
		TMDiskAccessible: "no_destination",
		TMIntegrity:      "failed",
		NTP:              "not_synced",
		OpenPorts:        "80,443",
		OpenPortCount:    2,
		AdminUsers:       "admin user1 user2 user3 user4",
		AdminCount:       5,
		CertExpiry:       "/etc/ssl/cert.pem:Jan 1 2020",
	}
	state.Disk.Warning = "/:97%"
	state.Disk.MaxPct = 97

	output := mustMacOSJSON(state)
	findings := ds.parseMacOSFindings(output, "imac.local")

	foundTypes := make(map[string]bool)
	for _, f := range findings {
		foundTypes[f.CheckType] = true
	}

	expectedChecks := []string{
		"macos_filevault",
		"macos_gatekeeper",
		"macos_sip",
		"macos_firewall",
		"macos_auto_update",
		"macos_screen_lock",
		"macos_file_sharing",
		"macos_time_machine", // no_backup + disk_no_destination + integrity_failed = multiple
		"macos_ntp_sync",
		"macos_admin_users",
		"macos_disk_space",
		"macos_cert_expiry",
	}

	for _, ct := range expectedChecks {
		if !foundTypes[ct] {
			t.Errorf("missing expected check: %s", ct)
		}
	}
}

func TestParseMacOSFindings_MixedState(t *testing.T) {
	ds := newMacOSTestScanner()

	state := macosScanState{
		// Passing
		FileVault:        "on",
		Gatekeeper:       "enabled",
		SIP:              "enabled",
		Firewall:         "enabled",
		NTP:              "synced",
		AdminCount:       2,
		AdminUsers:       "admin jrelly",
		CertExpiry:       "ok",
		TimeMachine:      "current",
		TMDiskAccessible: "yes",
		TMIntegrity:      "passed",
		// Failing
		AutoUpdate:      "disabled",
		ScreenLock:      "enabled",
		ScreenLockDelay: 10, // > 5s
		FileSharing:     "on",
	}
	state.Disk.Warning = "ok"
	state.Disk.MaxPct = 50

	output := mustMacOSJSON(state)
	findings := ds.parseMacOSFindings(output, "imac.local")

	foundTypes := make(map[string]bool)
	for _, f := range findings {
		foundTypes[f.CheckType] = true
	}

	expectedPresent := []string{
		"macos_auto_update",
		"macos_screen_lock", // delay > 5
		"macos_file_sharing",
	}
	expectedAbsent := []string{
		"macos_filevault",
		"macos_gatekeeper",
		"macos_sip",
		"macos_firewall",
		"macos_ntp_sync",
		"macos_admin_users",
		"macos_disk_space",
		"macos_cert_expiry",
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

func TestParseMacOSFindings_EmptyJSON(t *testing.T) {
	ds := newMacOSTestScanner()

	// Empty JSON: zero values. Most should not trigger (zero strings are not "off"/"disabled").
	findings := ds.parseMacOSFindings("{}", "imac.local")

	// Should not panic, and most zero-value strings should not match failing conditions.
	foundTypes := make(map[string]bool)
	for _, f := range findings {
		foundTypes[f.CheckType] = true
	}

	// FileVault="" != "off", Gatekeeper="" != "disabled", etc.
	// So mostly no findings from empty state.
	if foundTypes["macos_filevault"] {
		t.Error("empty FileVault should not trigger (not 'off')")
	}
}

func TestParseMacOSFindings_NilInput(t *testing.T) {
	ds := newMacOSTestScanner()

	findings := ds.parseMacOSFindings("", "host")
	if findings != nil {
		t.Error("empty input should return nil")
	}
}

func TestParseMacOSFindings_InvalidJSON(t *testing.T) {
	ds := newMacOSTestScanner()

	findings := ds.parseMacOSFindings("not json at all", "host")
	if findings != nil {
		t.Error("invalid JSON should return nil")
	}
}

func TestParseMacOSFindings_JSONWithPrefix(t *testing.T) {
	ds := newMacOSTestScanner()

	state := macosScanState{
		FileVault:        "on",
		Gatekeeper:       "enabled",
		SIP:              "enabled",
		Firewall:         "enabled",
		AutoUpdate:       "enabled",
		ScreenLock:       "enabled",
		ScreenLockDelay:  0,
		FileSharing:      "off",
		TimeMachine:      "current",
		TMDiskAccessible: "yes",
		TMIntegrity:      "passed",
		NTP:              "synced",
		OpenPorts:        "none",
		AdminCount:       2,
		CertExpiry:       "ok",
	}
	state.Disk.Warning = "ok"

	jsonBytes, _ := json.Marshal(state)
	prefixed := "bash: warning: setlocale: LC_ALL: cannot change locale\n" + string(jsonBytes)

	findings := ds.parseMacOSFindings(prefixed, "imac.local")
	if len(findings) != 0 {
		for _, f := range findings {
			t.Errorf("unexpected finding after prefix stripping: %s", f.CheckType)
		}
	}
}

func TestParseMacOSFindings_ScreenLockDelay(t *testing.T) {
	tests := []struct {
		name      string
		status    string
		delay     int
		wantDrift bool
		wantSev   string
	}{
		{"enabled, delay 0", "enabled", 0, false, ""},
		{"enabled, delay 5", "enabled", 5, false, ""},
		{"enabled, delay 6", "enabled", 6, true, "low"},
		{"enabled, delay 30", "enabled", 30, true, "low"},
		{"disabled", "disabled", 0, true, "medium"},
		{"unknown", "unknown", 0, false, ""},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ds := newMacOSTestScanner()
			state := macosScanState{
				FileVault:       "on",
				Gatekeeper:      "enabled",
				SIP:             "enabled",
				Firewall:        "enabled",
				AutoUpdate:      "enabled",
				ScreenLock:      tt.status,
				ScreenLockDelay: tt.delay,
				FileSharing:     "off",
				TimeMachine:     "current",
				TMDiskAccessible: "yes",
				TMIntegrity:     "passed",
				NTP:             "synced",
				AdminCount:      2,
				CertExpiry:      "ok",
			}
			state.Disk.Warning = "ok"

			output := mustMacOSJSON(state)
			findings := ds.parseMacOSFindings(output, "host")
			found := false
			var foundSev string
			for _, f := range findings {
				if f.CheckType == "macos_screen_lock" {
					found = true
					foundSev = f.Severity
				}
			}
			if found != tt.wantDrift {
				t.Errorf("ScreenLock(%s, delay=%d): wantDrift=%v got=%v",
					tt.status, tt.delay, tt.wantDrift, found)
			}
			if found && foundSev != tt.wantSev {
				t.Errorf("ScreenLock(%s, delay=%d): wantSev=%s got=%s",
					tt.status, tt.delay, tt.wantSev, foundSev)
			}
		})
	}
}

func TestParseMacOSFindings_TimeMachineStates(t *testing.T) {
	tests := []struct {
		name           string
		tmStatus       string
		diskAccessible string
		integrity      string
		wantCount      int
		desc           string
	}{
		{"current, accessible, passed", "current", "yes", "passed", 0, "all good"},
		{"no_backup, no_destination, not_tested", "no_backup", "no_destination", "not_tested", 2, "no_backup + disk_no_destination"},
		{"stale_14d, yes, passed", "stale_14d", "yes", "passed", 1, "stale backup only"},
		{"current, unmounted, passed", "current", "unmounted", "passed", 1, "disk unmounted"},
		{"current, yes, failed", "current", "yes", "failed", 1, "integrity failed"},
		{"stale_30d, unmounted, failed", "stale_30d", "unmounted", "failed", 3, "stale + unmounted + integrity_failed"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ds := newMacOSTestScanner()
			state := macosScanState{
				FileVault:        "on",
				Gatekeeper:       "enabled",
				SIP:              "enabled",
				Firewall:         "enabled",
				AutoUpdate:       "enabled",
				ScreenLock:       "enabled",
				ScreenLockDelay:  0,
				FileSharing:      "off",
				TimeMachine:      tt.tmStatus,
				TMDiskAccessible: tt.diskAccessible,
				TMIntegrity:      tt.integrity,
				NTP:              "synced",
				AdminCount:       2,
				CertExpiry:       "ok",
			}
			state.Disk.Warning = "ok"

			output := mustMacOSJSON(state)
			findings := ds.parseMacOSFindings(output, "host")

			tmCount := 0
			for _, f := range findings {
				if f.CheckType == "macos_time_machine" {
					tmCount++
				}
			}
			if tmCount != tt.wantCount {
				t.Errorf("TM(%s): want %d time_machine findings, got %d (%s)",
					tt.name, tt.wantCount, tmCount, tt.desc)
			}
		})
	}
}

func TestParseMacOSFindings_AdminUsers(t *testing.T) {
	tests := []struct {
		name      string
		count     int
		users     string
		wantDrift bool
	}{
		{"1 admin", 1, "admin", false},
		{"3 admins", 3, "admin user1 user2", false},
		{"4 admins (>3)", 4, "admin user1 user2 user3", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ds := newMacOSTestScanner()
			state := macosScanState{
				FileVault:  "on",
				Gatekeeper: "enabled",
				SIP:        "enabled",
				Firewall:   "enabled",
				AutoUpdate: "enabled",
				ScreenLock: "enabled",
				FileSharing: "off",
				TimeMachine: "current",
				TMDiskAccessible: "yes",
				TMIntegrity: "passed",
				NTP:        "synced",
				AdminCount: tt.count,
				AdminUsers: tt.users,
				CertExpiry: "ok",
			}
			state.Disk.Warning = "ok"

			output := mustMacOSJSON(state)
			findings := ds.parseMacOSFindings(output, "host")
			found := false
			for _, f := range findings {
				if f.CheckType == "macos_admin_users" {
					found = true
				}
			}
			if found != tt.wantDrift {
				t.Errorf("AdminCount=%d: wantDrift=%v got=%v", tt.count, tt.wantDrift, found)
			}
		})
	}
}

func TestParseMacOSFindings_DiskSeverity(t *testing.T) {
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
			ds := newMacOSTestScanner()
			state := macosScanState{
				FileVault:  "on",
				Gatekeeper: "enabled",
				SIP:        "enabled",
				Firewall:   "enabled",
				AutoUpdate: "enabled",
				ScreenLock: "enabled",
				FileSharing: "off",
				TimeMachine: "current",
				TMDiskAccessible: "yes",
				TMIntegrity: "passed",
				NTP:        "synced",
				AdminCount: 2,
				CertExpiry: "ok",
			}
			state.Disk.Warning = tt.warning
			state.Disk.MaxPct = tt.maxPct

			output := mustMacOSJSON(state)
			findings := ds.parseMacOSFindings(output, "host")
			found := false
			for _, f := range findings {
				if f.CheckType == "macos_disk_space" {
					found = true
					if f.Severity != tt.wantSev {
						t.Errorf("disk %d%%: want severity=%s got=%s", tt.maxPct, tt.wantSev, f.Severity)
					}
				}
			}
			if found != tt.wantFind {
				t.Errorf("disk %d%%: wantFind=%v got=%v", tt.maxPct, tt.wantFind, found)
			}
		})
	}
}

func TestParseMacOSFindings_DisabledChecks(t *testing.T) {
	ds := &driftScanner{
		svc: &Services{
			Checks: &stubCheckConfig{
				disabled: map[string]bool{
					"macos_filevault": true,
					"macos_sip":      true,
				},
			},
			Config: &Config{},
		},
	}

	state := macosScanState{
		FileVault:  "off",
		Gatekeeper: "disabled",
		SIP:        "disabled",
		Firewall:   "enabled",
		AutoUpdate: "enabled",
		ScreenLock: "enabled",
		FileSharing: "off",
		TimeMachine: "current",
		TMDiskAccessible: "yes",
		TMIntegrity: "passed",
		NTP:        "synced",
		AdminCount: 2,
		CertExpiry: "ok",
	}
	state.Disk.Warning = "ok"

	output := mustMacOSJSON(state)
	findings := ds.parseMacOSFindings(output, "host")

	for _, f := range findings {
		if f.CheckType == "macos_filevault" || f.CheckType == "macos_sip" {
			t.Errorf("disabled check %s should have been filtered", f.CheckType)
		}
	}

	// Gatekeeper should still be reported
	found := false
	for _, f := range findings {
		if f.CheckType == "macos_gatekeeper" {
			found = true
		}
	}
	if !found {
		t.Error("macos_gatekeeper should still be reported (not disabled)")
	}
}

func TestParseMacOSFindings_SeverityMapping(t *testing.T) {
	// Verify critical/high/medium/low severity assignments are correct
	ds := newMacOSTestScanner()

	state := macosScanState{
		FileVault:        "off",       // critical
		Gatekeeper:       "disabled",  // high
		SIP:              "disabled",  // critical
		Firewall:         "disabled",  // high
		AutoUpdate:       "disabled",  // medium
		ScreenLock:       "disabled",  // medium
		FileSharing:      "on",        // medium
		TimeMachine:      "no_backup", // medium
		TMDiskAccessible: "no_destination", // high
		TMIntegrity:      "failed",    // high
		NTP:              "not_synced", // low
		AdminCount:       5,           // high
		CertExpiry:       "/cert:exp", // high
	}
	state.Disk.Warning = "/:97%"
	state.Disk.MaxPct = 97 // high (>95)

	output := mustMacOSJSON(state)
	findings := ds.parseMacOSFindings(output, "host")

	sevMap := make(map[string]string) // checkType -> severity
	for _, f := range findings {
		// time_machine may appear multiple times; keep the first
		if _, exists := sevMap[f.CheckType+":"+f.Expected]; !exists {
			sevMap[f.CheckType+":"+f.Expected] = f.Severity
		}
	}

	criticalChecks := map[string]bool{
		"macos_filevault": true,
		"macos_sip":       true,
	}
	for _, f := range findings {
		if criticalChecks[f.CheckType] && f.Severity != "critical" {
			t.Errorf("%s should be critical, got %s", f.CheckType, f.Severity)
		}
	}
}
