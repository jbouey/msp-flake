package daemon

import (
	"encoding/json"
	"testing"
	"time"
)

func TestThreatDetector_Init(t *testing.T) {
	d := New(testConfig())
	if d.threatDet == nil {
		t.Fatal("expected threat detector to be initialized")
	}
	if d.threatDet.failedLogins == nil {
		t.Fatal("expected failedLogins map to be initialized")
	}
	if d.threatDet.vssShadowBaseline == nil {
		t.Fatal("expected vssShadowBaseline map to be initialized")
	}
}

func TestThreatDetector_ParseEvents(t *testing.T) {
	d := New(testConfig())
	td := d.threatDet

	entries := []deviceLogEntry{
		{TS: "2026-03-23T10:00:00Z", Unit: "Security/4625", Pri: 4, Msg: "An account failed to log on. Account Name: admin Source Network Address: 10.0.0.5", Hostname: "dc01"},
		{TS: "2026-03-23T10:00:01Z", Unit: "Security/4625", Pri: 4, Msg: "An account failed to log on. Account Name: user1 Source Network Address: 10.0.0.5", Hostname: "dc01"},
		{TS: "2026-03-23T10:00:02Z", Unit: "Security/4624", Pri: 6, Msg: "An account was successfully logged on.", Hostname: "dc01"},
		{TS: "2026-03-23T10:00:03Z", Unit: "Security/1102", Pri: 1, Msg: "The audit log was cleared.", Hostname: "ws01"},
	}

	events := td.parseEvents(entries)
	if len(events) != 4 {
		t.Fatalf("expected 4 events, got %d", len(events))
	}

	// Check 4625 event parsing
	if events[0].EventID != 4625 {
		t.Errorf("expected EventID 4625, got %d", events[0].EventID)
	}
	if events[0].SourceIP != "10.0.0.5" {
		t.Errorf("expected SourceIP '10.0.0.5', got %q", events[0].SourceIP)
	}
	if events[0].Username != "admin" {
		t.Errorf("expected Username 'admin', got %q", events[0].Username)
	}

	// Check 1102 event
	if events[3].EventID != 1102 {
		t.Errorf("expected EventID 1102, got %d", events[3].EventID)
	}
}

func TestThreatDetector_IngestFailedLogin(t *testing.T) {
	d := New(testConfig())
	td := d.threatDet

	now := time.Now()

	ev := threatEvent{
		EventID:  4625,
		Hostname: "dc01",
		SourceIP: "10.0.0.5",
		Username: "admin",
	}

	td.ingestFailedLogin(ev, now)

	tracker, ok := td.failedLogins["10.0.0.5"]
	if !ok {
		t.Fatal("expected tracker for source IP 10.0.0.5")
	}
	if tracker.Count != 1 {
		t.Errorf("expected count 1, got %d", tracker.Count)
	}
	if !tracker.Hosts["dc01"] {
		t.Error("expected dc01 in hosts")
	}
	if !tracker.Usernames["admin"] {
		t.Error("expected admin in usernames")
	}

	// Second event from same source, different host
	ev2 := threatEvent{
		EventID:  4625,
		Hostname: "ws01",
		SourceIP: "10.0.0.5",
		Username: "user1",
	}
	td.ingestFailedLogin(ev2, now)

	if tracker.Count != 2 {
		t.Errorf("expected count 2, got %d", tracker.Count)
	}
	if len(tracker.Hosts) != 2 {
		t.Errorf("expected 2 hosts, got %d", len(tracker.Hosts))
	}
	if !tracker.Hosts["ws01"] {
		t.Error("expected ws01 in hosts")
	}
}

func TestThreatDetector_CleanupTrackers(t *testing.T) {
	d := New(testConfig())
	td := d.threatDet

	staleTime := time.Now().Add(-20 * time.Minute)
	td.failedLogins["old-source"] = &loginTracker{
		Count:     5,
		FirstSeen: staleTime,
		LastSeen:  staleTime,
		Hosts:     map[string]bool{"dc01": true},
		Usernames: map[string]bool{"admin": true},
	}

	recentTime := time.Now()
	td.failedLogins["new-source"] = &loginTracker{
		Count:     3,
		FirstSeen: recentTime,
		LastSeen:  recentTime,
		Hosts:     map[string]bool{"dc01": true},
		Usernames: map[string]bool{"admin": true},
	}

	td.cleanupTrackers(time.Now())

	if _, ok := td.failedLogins["old-source"]; ok {
		t.Error("expected old-source to be cleaned up")
	}
	if _, ok := td.failedLogins["new-source"]; !ok {
		t.Error("expected new-source to remain")
	}
}

func TestThreatDetector_AlertCooldown(t *testing.T) {
	d := New(testConfig())
	td := d.threatDet

	key := "brute_force_cross:10.0.0.5"

	// Initially not cooling down
	if td.isAlertCoolingDown(key) {
		t.Error("expected no cooldown initially")
	}

	// Set cooldown
	td.alertCooldowns[key] = time.Now()

	// Should be cooling down
	if !td.isAlertCoolingDown(key) {
		t.Error("expected cooldown to be active")
	}

	// Set expired cooldown
	td.alertCooldowns[key] = time.Now().Add(-35 * time.Minute)

	// Should no longer be cooling down
	if td.isAlertCoolingDown(key) {
		t.Error("expected cooldown to have expired")
	}
}

func TestThreatDetector_ExtractField(t *testing.T) {
	tests := []struct {
		name     string
		msg      string
		label    string
		expected string
	}{
		{
			name:     "source network address",
			msg:      "An account failed to log on. Account Name: admin Source Network Address: 10.0.0.5 Other",
			label:    "Source Network Address:",
			expected: "10.0.0.5",
		},
		{
			name:     "account name",
			msg:      "An account failed to log on. Account Name: administrator Source Network Address: 10.0.0.5",
			label:    "Account Name:",
			expected: "administrator",
		},
		{
			name:     "label not found",
			msg:      "Some random message",
			label:    "Account Name:",
			expected: "",
		},
		{
			name:     "label at end",
			msg:      "Source Network Address: 192.168.1.1",
			label:    "Source Network Address:",
			expected: "192.168.1.1",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := extractField(tt.msg, tt.label)
			if result != tt.expected {
				t.Errorf("extractField(%q, %q) = %q, want %q", tt.msg, tt.label, result, tt.expected)
			}
		})
	}
}

func TestThreatDetector_MapKeys(t *testing.T) {
	m := map[string]bool{
		"host1": true,
		"host2": true,
		"host3": true,
	}

	keys := mapKeys(m)
	if len(keys) != 3 {
		t.Errorf("expected 3 keys, got %d", len(keys))
	}

	// All keys should be present (order doesn't matter)
	found := map[string]bool{}
	for _, k := range keys {
		found[k] = true
	}
	for k := range m {
		if !found[k] {
			t.Errorf("missing key %q", k)
		}
	}
}

func TestThreatDetector_AnalyzeEmpty(t *testing.T) {
	d := New(testConfig())
	td := d.threatDet

	// Should not panic on empty entries
	td.analyze(nil, nil)
	td.analyze(nil, []deviceLogEntry{})
}

func TestThreatDetector_SingleHostBelowThreshold(t *testing.T) {
	d := New(testConfig())
	td := d.threatDet

	// Generate 5 failed logins from same source on one host (below threshold of 20)
	now := time.Now()
	td.mu.Lock()
	td.failedLogins["10.0.0.5"] = &loginTracker{
		Count:     5,
		FirstSeen: now,
		LastSeen:  now,
		Hosts:     map[string]bool{"dc01": true},
		Usernames: map[string]bool{"admin": true},
	}
	td.mu.Unlock()

	// This should NOT trigger an alert
	td.mu.Lock()
	td.detectBruteForce(nil, now)
	// Check the tracker still exists (not cleaned up by alert)
	if _, ok := td.failedLogins["10.0.0.5"]; !ok {
		t.Error("tracker should still exist below threshold")
	}
	td.mu.Unlock()
}

func TestThreatDetector_CrossHostDetection(t *testing.T) {
	d := New(testConfig())
	td := d.threatDet

	// Simulate cross-host attack: 12 failed logins across 3 hosts
	now := time.Now()
	td.mu.Lock()
	td.failedLogins["10.0.0.99"] = &loginTracker{
		Count:     12,
		FirstSeen: now.Add(-5 * time.Minute),
		LastSeen:  now,
		Hosts:     map[string]bool{"dc01": true, "ws01": true, "ws02": true},
		Usernames: map[string]bool{"admin": true, "user1": true},
	}

	// This should trigger a cross-host brute force alert and delete the tracker
	td.detectBruteForce(nil, now)

	if _, ok := td.failedLogins["10.0.0.99"]; ok {
		t.Error("expected tracker to be deleted after cross-host alert")
	}

	// Check cooldown was set
	if _, ok := td.alertCooldowns["brute_force_cross:10.0.0.99"]; !ok {
		t.Error("expected cooldown to be set after alert")
	}
	td.mu.Unlock()
}

func TestThreatDetector_SingleHostDetection(t *testing.T) {
	d := New(testConfig())
	td := d.threatDet

	// Simulate single-host attack: 25 failed logins on one host
	now := time.Now()
	td.mu.Lock()
	td.failedLogins["10.0.0.88"] = &loginTracker{
		Count:     25,
		FirstSeen: now.Add(-3 * time.Minute),
		LastSeen:  now,
		Hosts:     map[string]bool{"dc01": true},
		Usernames: map[string]bool{"admin": true, "user1": true, "user2": true},
	}

	td.detectBruteForce(nil, now)

	if _, ok := td.failedLogins["10.0.0.88"]; ok {
		t.Error("expected tracker to be deleted after single-host alert")
	}

	if _, ok := td.alertCooldowns["brute_force_single:10.0.0.88"]; !ok {
		t.Error("expected cooldown to be set after alert")
	}
	td.mu.Unlock()
}

func TestThreatDetector_CooldownPreventsRefire(t *testing.T) {
	d := New(testConfig())
	td := d.threatDet

	now := time.Now()

	// Set a cooldown as if we already fired
	td.mu.Lock()
	td.alertCooldowns["brute_force_cross:10.0.0.99"] = now

	// Even with high count, cooldown should prevent refiring
	td.failedLogins["10.0.0.99"] = &loginTracker{
		Count:     50,
		FirstSeen: now.Add(-5 * time.Minute),
		LastSeen:  now,
		Hosts:     map[string]bool{"dc01": true, "ws01": true, "ws02": true},
		Usernames: map[string]bool{"admin": true},
	}

	td.detectBruteForce(nil, now)

	// Tracker should still exist because cooldown prevented the alert
	if _, ok := td.failedLogins["10.0.0.99"]; !ok {
		t.Error("tracker should still exist when cooldown prevents alert")
	}
	td.mu.Unlock()
}

func TestThreatDetector_VSSBaseline(t *testing.T) {
	d := New(testConfig())
	td := d.threatDet

	// Set initial baseline with shadows
	td.vssShadowBaseline["dc01"] = 5

	// Verify baseline is stored
	if td.vssShadowBaseline["dc01"] != 5 {
		t.Errorf("expected baseline 5, got %d", td.vssShadowBaseline["dc01"])
	}
}

// TestWindowsBackupVerificationStruct verifies the new BackupVerification field
// parses correctly from JSON.
func TestWindowsBackupVerificationStruct(t *testing.T) {
	jsonStr := `{
		"Firewall": {},
		"Defender": "Running",
		"WindowsUpdate": "Running",
		"EventLog": "Running",
		"RogueAdmins": [],
		"RogueTasks": [],
		"AgentStatus": "Running",
		"BitLocker": "On",
		"SMBSigning": "Required",
		"SMB1": "Disabled",
		"ScreenLock": "300",
		"DefenderExclusions": [],
		"DNSServers": [],
		"NetworkProfiles": {},
		"PasswordPolicy": {"MinLength": 12, "MaxAgeDays": 90, "LockoutThreshold": 5},
		"RDPNLA": "Enabled",
		"GuestAccount": "Disabled",
		"ADServices": {},
		"WMIPersistence": [],
		"RegistryRunKeys": [],
		"AuditPolicy": {},
		"DefenderAdvanced": {},
		"SpoolerService": "Stopped",
		"DangerousInboundRules": [],
		"BackupVerification": {
			"backup_tool": "vss",
			"last_backup": "2026-03-23T10:00:00Z",
			"backup_age_days": 0.5,
			"backup_status": "current",
			"restore_test": "not_tested",
			"details": "3 shadow copies"
		}
	}`

	var state windowsScanState
	if err := json.Unmarshal([]byte(jsonStr), &state); err != nil {
		t.Fatalf("Failed to parse JSON: %v", err)
	}

	bv := state.BackupVerification
	if bv.BackupTool != "vss" {
		t.Errorf("expected backup_tool 'vss', got %q", bv.BackupTool)
	}
	if bv.BackupStatus != "current" {
		t.Errorf("expected backup_status 'current', got %q", bv.BackupStatus)
	}
	if bv.BackupAgeDays != 0.5 {
		t.Errorf("expected backup_age_days 0.5, got %f", bv.BackupAgeDays)
	}
	if bv.Details != "3 shadow copies" {
		t.Errorf("expected details '3 shadow copies', got %q", bv.Details)
	}
}

// TestWindowsBackupVerificationMissing verifies drift is reported when no backup exists.
func TestWindowsBackupVerificationMissing(t *testing.T) {
	d := New(testConfig())
	ds := d.scanner

	state := &windowsScanState{}
	state.BackupVerification.BackupTool = "none"
	state.BackupVerification.BackupStatus = "missing"
	state.BackupVerification.RestoreTest = "not_tested"

	target := scanTarget{hostname: "ws01.test.local", label: "WS"}
	findings := ds.evaluateWindowsFindings(state, target)

	found := false
	for _, f := range findings {
		if f.CheckType == "backup_not_configured" {
			found = true
			if f.Severity != "low" {
				t.Errorf("expected severity 'low' for setup requirement, got %q", f.Severity)
			}
			if f.HIPAAControl != "164.308(a)(7)(ii)(A)" {
				t.Errorf("expected HIPAA control '164.308(a)(7)(ii)(A)', got %q", f.HIPAAControl)
			}
			break
		}
	}
	if !found {
		t.Error("expected backup_not_configured finding for missing backup")
	}
}

// TestWindowsBackupVerificationStale verifies drift is reported for stale backups.
func TestWindowsBackupVerificationStale(t *testing.T) {
	d := New(testConfig())
	ds := d.scanner

	state := &windowsScanState{}
	state.BackupVerification.BackupTool = "vss"
	state.BackupVerification.BackupStatus = "stale"
	state.BackupVerification.BackupAgeDays = 14.5
	state.BackupVerification.LastBackup = "2026-03-09T10:00:00Z"
	state.BackupVerification.RestoreTest = "not_tested"

	target := scanTarget{hostname: "ws01.test.local", label: "WS"}
	findings := ds.evaluateWindowsFindings(state, target)

	found := false
	for _, f := range findings {
		if f.CheckType == "backup_verification" {
			found = true
			if f.Severity != "medium" {
				t.Errorf("expected severity 'medium' for stale, got %q", f.Severity)
			}
			break
		}
	}
	if !found {
		t.Error("expected backup_verification finding for stale backup")
	}
}

// TestWindowsBackupVerificationCurrent verifies no drift when backup is current.
func TestWindowsBackupVerificationCurrent(t *testing.T) {
	d := New(testConfig())
	ds := d.scanner

	state := &windowsScanState{}
	state.BackupVerification.BackupTool = "vss"
	state.BackupVerification.BackupStatus = "current"
	state.BackupVerification.BackupAgeDays = 0.5

	target := scanTarget{hostname: "ws01.test.local", label: "WS"}
	findings := ds.evaluateWindowsFindings(state, target)

	for _, f := range findings {
		if f.CheckType == "backup_verification" {
			t.Error("should not report backup_verification drift for current backup")
		}
	}
}

// TestLinuxBackupStatusParsing verifies the Linux backup field parses correctly.
func TestLinuxBackupStatusParsing(t *testing.T) {
	d := New(testConfig())
	ds := d.scanner

	output := `{"firewall":{"status":"active","rules":5},"ssh":{"root_login":"prohibit-password","password_auth":"no","port":"22"},"failed_services":{"count":0,"services":"none"},"disk":{"warning":"ok","max_pct":0},"suid":"none","audit":"journald_persistent","ntp_synced":"yes","kernel":{"ip_forward":"0","syncookies":"1","rp_filter":"1","accept_redirects":"0"},"open_ports":"22","users":"none","permissions":"ok","auto_update":"nixos_upgrade_timer","log_forwarding":"journald_persistent","cron":"none","cert_expiry":"ok","backup":{"tool":"none","last_backup":"","age_days":-1,"status":"missing","details":""}}`

	findings := ds.parseLinuxFindings(output, "linux01")

	found := false
	for _, f := range findings {
		if f.CheckType == "linux_backup_status" {
			found = true
			if f.HIPAAControl != "164.308(a)(7)(ii)(A)" {
				t.Errorf("expected HIPAA control '164.308(a)(7)(ii)(A)', got %q", f.HIPAAControl)
			}
			break
		}
	}
	if !found {
		t.Error("expected linux_backup_status finding for missing backup")
	}
}

// TestLinuxBackupStatusCurrent verifies no finding when backup is current.
func TestLinuxBackupStatusCurrent(t *testing.T) {
	d := New(testConfig())
	ds := d.scanner

	output := `{"firewall":{"status":"active","rules":5},"ssh":{"root_login":"prohibit-password","password_auth":"no","port":"22"},"failed_services":{"count":0,"services":"none"},"disk":{"warning":"ok","max_pct":0},"suid":"none","audit":"journald_persistent","ntp_synced":"yes","kernel":{"ip_forward":"0","syncookies":"1","rp_filter":"1","accept_redirects":"0"},"open_ports":"22","users":"none","permissions":"ok","auto_update":"nixos_upgrade_timer","log_forwarding":"journald_persistent","cron":"none","cert_expiry":"ok","backup":{"tool":"restic","last_backup":"2026-03-23T10:00:00Z","age_days":0,"status":"current","details":"repo:/var/backups/restic"}}`

	findings := ds.parseLinuxFindings(output, "linux01")

	for _, f := range findings {
		if f.CheckType == "linux_backup_status" {
			t.Error("should not report linux_backup_status for current backup")
		}
	}
}

// TestMacOSScanState verifies the new TM fields parse correctly.
func TestMacOSScanStateWithTMFields(t *testing.T) {
	jsonStr := `{
		"filevault": "on",
		"gatekeeper": "enabled",
		"sip": "enabled",
		"firewall": "enabled",
		"auto_update": "enabled",
		"screen_lock": "enabled",
		"screen_lock_delay": 0,
		"remote_login": "on",
		"file_sharing": "off",
		"time_machine": "current",
		"tm_disk_accessible": "yes",
		"tm_integrity": "passed",
		"ntp": "synced",
		"open_ports": "none",
		"open_port_count": 0,
		"admin_users": "admin",
		"admin_count": 1,
		"disk": {"warning": "ok", "max_pct": 45},
		"cert_expiry": "ok"
	}`

	var state macosScanState
	if err := json.Unmarshal([]byte(jsonStr), &state); err != nil {
		t.Fatalf("Failed to parse macOS scan state: %v", err)
	}

	if state.TMDiskAccessible != "yes" {
		t.Errorf("expected tm_disk_accessible 'yes', got %q", state.TMDiskAccessible)
	}
	if state.TMIntegrity != "passed" {
		t.Errorf("expected tm_integrity 'passed', got %q", state.TMIntegrity)
	}
}

// TestMacOSTMIntegrityFailed verifies finding when TM integrity check fails.
func TestMacOSTMIntegrityFailed(t *testing.T) {
	d := New(testConfig())
	ds := d.scanner

	output := `{"filevault":"on","gatekeeper":"enabled","sip":"enabled","firewall":"enabled","auto_update":"enabled","screen_lock":"enabled","screen_lock_delay":0,"remote_login":"on","file_sharing":"off","time_machine":"current","tm_disk_accessible":"yes","tm_integrity":"failed","ntp":"synced","open_ports":"none","open_port_count":0,"admin_users":"admin","admin_count":1,"disk":{"warning":"ok","max_pct":45},"cert_expiry":"ok"}`

	findings := ds.parseMacOSFindings(output, "imac01")

	found := false
	for _, f := range findings {
		if f.CheckType == "macos_time_machine" && f.Expected == "integrity_passed" {
			found = true
			if f.Severity != "high" {
				t.Errorf("expected severity 'high' for integrity failure, got %q", f.Severity)
			}
			break
		}
	}
	if !found {
		t.Error("expected macos_time_machine finding for integrity failure")
	}
}

// TestMacOSTMDiskUnmounted verifies finding when TM disk is unmounted.
func TestMacOSTMDiskUnmounted(t *testing.T) {
	d := New(testConfig())
	ds := d.scanner

	output := `{"filevault":"on","gatekeeper":"enabled","sip":"enabled","firewall":"enabled","auto_update":"enabled","screen_lock":"enabled","screen_lock_delay":0,"remote_login":"on","file_sharing":"off","time_machine":"no_backup","tm_disk_accessible":"unmounted","tm_integrity":"not_tested","ntp":"synced","open_ports":"none","open_port_count":0,"admin_users":"admin","admin_count":1,"disk":{"warning":"ok","max_pct":45},"cert_expiry":"ok"}`

	findings := ds.parseMacOSFindings(output, "imac01")

	foundTMBackup := false
	foundTMDisk := false
	for _, f := range findings {
		if f.CheckType == "macos_time_machine" {
			if f.Expected == "recent_backup" {
				foundTMBackup = true
			}
			if f.Expected == "backup_disk_accessible" {
				foundTMDisk = true
				if f.Severity != "high" {
					t.Errorf("expected severity 'high' for unmounted disk, got %q", f.Severity)
				}
			}
		}
	}
	if !foundTMBackup {
		t.Error("expected macos_time_machine finding for no_backup")
	}
	if !foundTMDisk {
		t.Error("expected macos_time_machine finding for unmounted disk")
	}
}
