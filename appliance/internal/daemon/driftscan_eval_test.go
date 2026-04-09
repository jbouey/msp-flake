package daemon

import (
	"testing"
)

// stubCheckConfig implements CheckConfig for tests — nothing disabled.
type stubCheckConfig struct {
	disabled map[string]bool
}

func (s *stubCheckConfig) IsDisabled(checkType string) bool {
	if s.disabled == nil {
		return false
	}
	return s.disabled[checkType]
}

func (s *stubCheckConfig) GetDisabledChecks() map[string]bool {
	return s.disabled
}

// newTestDriftScanner creates a minimal driftScanner for testing evaluation
// functions that only need svc.Checks and svc.Config.
func newTestDriftScanner() *driftScanner {
	dcIP := "192.168.88.250"
	return &driftScanner{
		svc: &Services{
			Checks: &stubCheckConfig{},
			Config: &Config{
				DomainController: &dcIP,
			},
		},
	}
}

// --- evaluateWindowsFindings ---

func TestEvaluateWindowsFindings_AllPassing(t *testing.T) {
	ds := newTestDriftScanner()
	target := scanTarget{hostname: "dc01.local", label: "DC"}

	state := &windowsScanState{
		Firewall:      map[string]string{"Domain": "True", "Private": "True", "Public": "True"},
		Defender:      "Running",
		WindowsUpdate: "Running",
		EventLog:      "Running",
		RogueAdmins:   nil,
		RogueTasks:    nil,
		AgentStatus:   "Running",
		BitLocker:     "On",
		BitLockerVolumes: []struct {
			MountPoint           string `json:"MountPoint"`
			ProtectionStatus     string `json:"ProtectionStatus"`
			EncryptionPercentage int    `json:"EncryptionPercentage"`
		}{
			{MountPoint: "C:", ProtectionStatus: "On", EncryptionPercentage: 100},
		},
		SMBSigning:           "Required",
		SMB1:                 "Disabled",
		ScreenLock:           "600",
		DefenderExclusions:   nil,
		DNSServers:           flexStringSlice{"192.168.88.250", "8.8.8.8"},
		NetworkProfiles:      map[string]string{"Ethernet": "DomainAuthenticated"},
		PasswordPolicy: struct {
			MinLength        int `json:"MinLength"`
			MaxAgeDays       int `json:"MaxAgeDays"`
			LockoutThreshold int `json:"LockoutThreshold"`
		}{MinLength: 12, MaxAgeDays: 60, LockoutThreshold: 5},
		RDPNLA:               "Enabled",
		GuestAccount:         "Disabled",
		ADServices:           map[string]string{"DNS": "Running", "Netlogon": "Running"},
		WMIPersistence:       nil,
		RegistryRunKeys:      nil,
		AuditPolicy:          map[string]string{"Logon/Logoff": "Success and Failure"},
		DefenderAdvanced:     map[string]string{"RealTimeProtection": "True", "MAPSReporting": "2", "SubmitSamplesConsent": "1"},
		SpoolerService:       "Stopped",
		DangerousInboundRules: nil,
		BackupVerification: struct {
			BackupTool    string  `json:"backup_tool"`
			LastBackup    string  `json:"last_backup"`
			BackupAgeDays float64 `json:"backup_age_days"`
			BackupStatus  string  `json:"backup_status"`
			RestoreTest   string  `json:"restore_test"`
			Details       string  `json:"details"`
		}{
			BackupTool:    "wbadmin",
			BackupStatus:  "current",
			BackupAgeDays: 1.0,
		},
	}

	findings := ds.evaluateWindowsFindings(state, target)
	if len(findings) != 0 {
		for _, f := range findings {
			t.Errorf("unexpected finding: %s expected=%s actual=%s", f.CheckType, f.Expected, f.Actual)
		}
	}
}

func TestEvaluateWindowsFindings_AllFailing(t *testing.T) {
	ds := newTestDriftScanner()
	target := scanTarget{hostname: "dc01.local", label: "DC"}

	state := &windowsScanState{
		Firewall:      map[string]string{"Domain": "false", "Private": "false", "Public": "false"},
		Defender:      "Stopped",
		WindowsUpdate: "Stopped",
		EventLog:      "Stopped",
		RogueAdmins:   flexStringSlice{"hacker1"},
		RogueTasks: []struct {
			Name  string `json:"Name"`
			Path  string `json:"Path"`
			State string `json:"State"`
		}{
			{Name: "EvilTask", Path: "\\", State: "Ready"},
		},
		AgentStatus: "NotInstalled",
		BitLocker:   "Off",
		BitLockerVolumes: []struct {
			MountPoint           string `json:"MountPoint"`
			ProtectionStatus     string `json:"ProtectionStatus"`
			EncryptionPercentage int    `json:"EncryptionPercentage"`
		}{
			{MountPoint: "C:", ProtectionStatus: "Off", EncryptionPercentage: 0},
		},
		SMBSigning:         "NotRequired",
		SMB1:               "Enabled",
		ScreenLock:         "NotConfigured",
		DefenderExclusions: flexStringSlice{"C:\\Temp"},
		DNSServers:         flexStringSlice{"4.3.2.1"}, // suspicious, not in known list
		NetworkProfiles:    map[string]string{"Ethernet": "Public"},
		PasswordPolicy: struct {
			MinLength        int `json:"MinLength"`
			MaxAgeDays       int `json:"MaxAgeDays"`
			LockoutThreshold int `json:"LockoutThreshold"`
		}{MinLength: 4, MaxAgeDays: 999, LockoutThreshold: 0},
		RDPNLA:       "Disabled",
		GuestAccount: "Enabled",
		ADServices:   map[string]string{"DNS": "Stopped", "Netlogon": "Stopped"},
		WMIPersistence: []struct {
			Name  string `json:"Name"`
			Query string `json:"Query"`
		}{
			{Name: "EvilSub", Query: "SELECT *"},
		},
		RegistryRunKeys: []struct {
			Name  string `json:"Name"`
			Value string `json:"Value"`
			Path  string `json:"Path"`
		}{
			{Name: "MalwareLoader", Value: "C:\\bad.exe", Path: "HKLM\\..\\Run"},
		},
		AuditPolicy:          map[string]string{"Logon/Logoff": "No Auditing", "Object Access": "No Auditing"},
		DefenderAdvanced:     map[string]string{"RealTimeProtection": "False", "MAPSReporting": "0", "SubmitSamplesConsent": "0"},
		SpoolerService:       "Running",
		DangerousInboundRules: []struct {
			Name     string `json:"Name"`
			Port     string `json:"Port"`
			Protocol string `json:"Protocol"`
		}{
			{Name: "AllowAll", Port: "0-65535", Protocol: "TCP"},
		},
		BackupVerification: struct {
			BackupTool    string  `json:"backup_tool"`
			LastBackup    string  `json:"last_backup"`
			BackupAgeDays float64 `json:"backup_age_days"`
			BackupStatus  string  `json:"backup_status"`
			RestoreTest   string  `json:"restore_test"`
			Details       string  `json:"details"`
		}{
			BackupTool:    "none",
			BackupStatus:  "missing",
			BackupAgeDays: -1,
		},
	}

	findings := ds.evaluateWindowsFindings(state, target)

	// Collect all check types found
	foundTypes := make(map[string]bool)
	for _, f := range findings {
		foundTypes[f.CheckType] = true
	}

	// DC label triggers: agent_status is WS-only, but network_profile is WS-only too.
	// DC triggers: AD services, spooler.
	expectedChecks := []string{
		"firewall_status",
		"windows_defender",
		"windows_update",
		"audit_logging",
		"rogue_admin_users",
		"rogue_scheduled_tasks",
		"bitlocker_status",
		"smb_signing",
		"smb1_protocol",
		"screen_lock_policy",
		"defender_exclusions",
		"dns_config",
		"password_policy",
		"rdp_nla",
		"guest_account",
		"service_dns",
		"service_netlogon",
		"wmi_event_persistence",
		"registry_run_persistence",
		"audit_policy",
		"defender_cloud_protection",
		"spooler_service",
		"firewall_dangerous_rules",
		"backup_not_configured",
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

func TestEvaluateWindowsFindings_MixedState(t *testing.T) {
	ds := newTestDriftScanner()
	target := scanTarget{hostname: "ws01.local", label: "WS"}

	state := &windowsScanState{
		// Firewall: 1 profile disabled
		Firewall:      map[string]string{"Domain": "True", "Private": "True", "Public": "false"},
		Defender:      "Running",
		WindowsUpdate: "Running",
		EventLog:      "Running",
		AgentStatus:   "NotInstalled", // WS-specific check
		BitLocker:     "On",
		BitLockerVolumes: []struct {
			MountPoint           string `json:"MountPoint"`
			ProtectionStatus     string `json:"ProtectionStatus"`
			EncryptionPercentage int    `json:"EncryptionPercentage"`
		}{
			{MountPoint: "C:", ProtectionStatus: "On", EncryptionPercentage: 100},
			{MountPoint: "D:", ProtectionStatus: "Off", EncryptionPercentage: 0},
		},
		SMBSigning:      "Required",
		SMB1:            "Disabled",
		ScreenLock:      "1200", // exceeds 900s limit
		NetworkProfiles: map[string]string{"Ethernet": "Public"}, // WS on public = drift
		PasswordPolicy: struct {
			MinLength        int `json:"MinLength"`
			MaxAgeDays       int `json:"MaxAgeDays"`
			LockoutThreshold int `json:"LockoutThreshold"`
		}{MinLength: 12, MaxAgeDays: 60, LockoutThreshold: 5},
		RDPNLA:       "Enabled",
		GuestAccount: "Disabled",
		BackupVerification: struct {
			BackupTool    string  `json:"backup_tool"`
			LastBackup    string  `json:"last_backup"`
			BackupAgeDays float64 `json:"backup_age_days"`
			BackupStatus  string  `json:"backup_status"`
			RestoreTest   string  `json:"restore_test"`
			Details       string  `json:"details"`
		}{
			BackupTool:    "wbadmin",
			BackupStatus:  "stale",
			BackupAgeDays: 14.0,
		},
	}

	findings := ds.evaluateWindowsFindings(state, target)
	foundTypes := make(map[string]bool)
	for _, f := range findings {
		foundTypes[f.CheckType] = true
	}

	expected := []string{
		"firewall_status",     // Public profile disabled
		"agent_status",        // WS label, NotInstalled
		"bitlocker_status",    // D: unprotected
		"screen_lock_policy",  // 1200 > 900
		"network_profile",     // Public on WS
		"backup_verification", // stale
	}
	for _, ct := range expected {
		if !foundTypes[ct] {
			t.Errorf("missing expected check: %s", ct)
		}
	}
}

func TestEvaluateWindowsFindings_NilState(t *testing.T) {
	ds := newTestDriftScanner()
	target := scanTarget{hostname: "test.local", label: "WS"}

	// nil state will panic because the function dereferences state.Firewall (map range).
	// Verify the caller never passes nil — the production code always checks before calling.
	defer func() {
		if r := recover(); r == nil {
			t.Error("expected panic on nil state, but none occurred")
		}
	}()
	_ = ds.evaluateWindowsFindings(nil, target)
}

func TestEvaluateWindowsFindings_EmptyState(t *testing.T) {
	ds := newTestDriftScanner()
	target := scanTarget{hostname: "test.local", label: "WS"}

	state := &windowsScanState{}
	findings := ds.evaluateWindowsFindings(state, target)

	// Empty state: most checks should pass since zero values are generally benign.
	// Agent status on WS with empty string should flag.
	foundTypes := make(map[string]bool)
	for _, f := range findings {
		foundTypes[f.CheckType] = true
	}
	if !foundTypes["agent_status"] {
		t.Error("empty AgentStatus on WS should flag agent_status drift")
	}
}

func TestEvaluateWindowsFindings_ScreenLockTimeout(t *testing.T) {
	tests := []struct {
		name      string
		value     string
		wantDrift bool
	}{
		{"within limit (600s)", "600", false},
		{"at limit (900s)", "900", false},
		{"over limit (901s)", "901", true},
		{"zero timeout (disabled)", "0", true},
		{"not configured", "NotConfigured", true},
		{"unknown", "Unknown", false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ds := newTestDriftScanner()
			target := scanTarget{hostname: "srv01", label: "DC"}
			state := &windowsScanState{
				ScreenLock: tt.value,
				Firewall:   map[string]string{"Domain": "True"},
				Defender:   "Running",
				BitLocker:  "On",
			}
			findings := ds.evaluateWindowsFindings(state, target)
			found := false
			for _, f := range findings {
				if f.CheckType == "screen_lock_policy" {
					found = true
					break
				}
			}
			if found != tt.wantDrift {
				t.Errorf("ScreenLock=%q: wantDrift=%v, got=%v", tt.value, tt.wantDrift, found)
			}
		})
	}
}

func TestEvaluateWindowsFindings_DNSKnownServers(t *testing.T) {
	tests := []struct {
		name      string
		dns       []string
		dc        *string
		wantDrift bool
	}{
		{"well-known public DNS", []string{"8.8.8.8", "1.1.1.1"}, nil, false},
		{"DC IP as DNS", []string{"192.168.88.250"}, strPtr("192.168.88.250"), false},
		{"private range 192.168", []string{"192.168.1.1"}, nil, false},
		{"private range 10.x", []string{"10.0.0.1"}, nil, false},
		{"private range 172.x", []string{"172.16.0.1"}, nil, false},
		{"suspicious external", []string{"4.3.2.1"}, nil, true},
		{"mixed good and bad", []string{"8.8.8.8", "6.6.6.6"}, nil, true},
		{"empty", nil, nil, false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ds := &driftScanner{
				svc: &Services{
					Checks: &stubCheckConfig{},
					Config: &Config{
						DomainController: tt.dc,
					},
				},
			}
			target := scanTarget{hostname: "srv01", label: "DC"}
			state := &windowsScanState{
				DNSServers: flexStringSlice(tt.dns),
				Firewall:   map[string]string{"Domain": "True"},
				Defender:   "Running",
				BitLocker:  "On",
			}
			findings := ds.evaluateWindowsFindings(state, target)
			found := false
			for _, f := range findings {
				if f.CheckType == "dns_config" {
					found = true
					break
				}
			}
			if found != tt.wantDrift {
				t.Errorf("DNS=%v: wantDrift=%v, got=%v", tt.dns, tt.wantDrift, found)
			}
		})
	}
}

func TestEvaluateWindowsFindings_BitLockerVolumes(t *testing.T) {
	tests := []struct {
		name      string
		volumes   []struct{ MountPoint, ProtectionStatus string }
		fallback  string
		wantDrift bool
	}{
		{
			name: "all protected",
			volumes: []struct{ MountPoint, ProtectionStatus string }{
				{"C:", "On"}, {"D:", "1"},
			},
			wantDrift: false,
		},
		{
			name: "one unprotected",
			volumes: []struct{ MountPoint, ProtectionStatus string }{
				{"C:", "On"}, {"D:", "Off"},
			},
			wantDrift: true,
		},
		{
			name:      "no volumes, fallback Off",
			volumes:   nil,
			fallback:  "Off",
			wantDrift: true,
		},
		{
			name:      "no volumes, fallback NotAvailable",
			volumes:   nil,
			fallback:  "NotAvailable",
			wantDrift: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ds := newTestDriftScanner()
			target := scanTarget{hostname: "srv01", label: "DC"}
			state := &windowsScanState{
				Firewall:  map[string]string{"Domain": "True"},
				Defender:  "Running",
				BitLocker: tt.fallback,
			}
			for _, v := range tt.volumes {
				state.BitLockerVolumes = append(state.BitLockerVolumes, struct {
					MountPoint           string `json:"MountPoint"`
					ProtectionStatus     string `json:"ProtectionStatus"`
					EncryptionPercentage int    `json:"EncryptionPercentage"`
				}{MountPoint: v.MountPoint, ProtectionStatus: v.ProtectionStatus})
			}
			findings := ds.evaluateWindowsFindings(state, target)
			found := false
			for _, f := range findings {
				if f.CheckType == "bitlocker_status" {
					found = true
					break
				}
			}
			if found != tt.wantDrift {
				t.Errorf("wantDrift=%v, got=%v", tt.wantDrift, found)
			}
		})
	}
}

func TestEvaluateWindowsFindings_BackupStates(t *testing.T) {
	tests := []struct {
		name       string
		tool       string
		status     string
		ageDays    float64
		wantType   string
		wantDrift  bool
	}{
		{"current backup", "wbadmin", "current", 1.0, "", false},
		{"missing backup", "none", "missing", -1, "backup_not_configured", true},
		{"stale backup", "wbadmin", "stale", 14.0, "backup_verification", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ds := newTestDriftScanner()
			target := scanTarget{hostname: "srv01", label: "DC"}
			state := &windowsScanState{
				Firewall: map[string]string{"Domain": "True"},
				Defender: "Running",
				BitLocker: "On",
				BackupVerification: struct {
					BackupTool    string  `json:"backup_tool"`
					LastBackup    string  `json:"last_backup"`
					BackupAgeDays float64 `json:"backup_age_days"`
					BackupStatus  string  `json:"backup_status"`
					RestoreTest   string  `json:"restore_test"`
					Details       string  `json:"details"`
				}{
					BackupTool:    tt.tool,
					BackupStatus:  tt.status,
					BackupAgeDays: tt.ageDays,
				},
			}
			findings := ds.evaluateWindowsFindings(state, target)
			found := false
			for _, f := range findings {
				if tt.wantType != "" && f.CheckType == tt.wantType {
					found = true
				}
			}
			if tt.wantDrift && !found {
				t.Errorf("expected drift with checkType=%s", tt.wantType)
			}
			if !tt.wantDrift && len(findings) != 0 {
				for _, f := range findings {
					if f.CheckType == "backup_not_configured" || f.CheckType == "backup_verification" {
						t.Errorf("unexpected backup finding: %s", f.CheckType)
					}
				}
			}
		})
	}
}

func TestEvaluateWindowsFindings_DisabledChecks(t *testing.T) {
	ds := &driftScanner{
		svc: &Services{
			Checks: &stubCheckConfig{
				disabled: map[string]bool{
					"firewall_status":  true,
					"windows_defender": true,
				},
			},
			Config: &Config{},
		},
	}
	target := scanTarget{hostname: "srv01", label: "DC"}
	state := &windowsScanState{
		Firewall: map[string]string{"Domain": "false"},
		Defender: "Stopped",
	}

	findings := ds.evaluateWindowsFindings(state, target)
	for _, f := range findings {
		if f.CheckType == "firewall_status" || f.CheckType == "windows_defender" {
			t.Errorf("disabled check %s should have been filtered", f.CheckType)
		}
	}
}

func TestEvaluateWindowsFindings_WSvsLabel(t *testing.T) {
	// Agent status only fires on WS, not DC
	ds := newTestDriftScanner()

	dcTarget := scanTarget{hostname: "dc01", label: "DC"}
	wsTarget := scanTarget{hostname: "ws01", label: "WS"}

	state := &windowsScanState{
		AgentStatus: "NotInstalled",
		Firewall:    map[string]string{},
		Defender:    "Running",
		BitLocker:   "On",
	}

	dcFindings := ds.evaluateWindowsFindings(state, dcTarget)
	for _, f := range dcFindings {
		if f.CheckType == "agent_status" {
			t.Error("agent_status should not fire for DC label")
		}
	}

	wsFindings := ds.evaluateWindowsFindings(state, wsTarget)
	found := false
	for _, f := range wsFindings {
		if f.CheckType == "agent_status" {
			found = true
		}
	}
	if !found {
		t.Error("agent_status should fire for WS label with NotInstalled")
	}
}

func TestEvaluateWindowsFindings_PasswordPolicy(t *testing.T) {
	tests := []struct {
		name      string
		minLen    int
		maxAge    int
		lockout   int
		wantDrift bool
	}{
		{"compliant", 12, 60, 5, false},
		{"short password", 4, 60, 5, true},
		{"no lockout", 12, 60, 0, true},
		{"both bad", 4, 60, 0, true},
		{"zero min length (not >0)", 0, 60, 5, false}, // minLength check requires >0
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ds := newTestDriftScanner()
			target := scanTarget{hostname: "dc01", label: "DC"}
			state := &windowsScanState{
				Firewall: map[string]string{},
				Defender: "Running",
				BitLocker: "On",
				PasswordPolicy: struct {
					MinLength        int `json:"MinLength"`
					MaxAgeDays       int `json:"MaxAgeDays"`
					LockoutThreshold int `json:"LockoutThreshold"`
				}{MinLength: tt.minLen, MaxAgeDays: tt.maxAge, LockoutThreshold: tt.lockout},
			}
			findings := ds.evaluateWindowsFindings(state, target)
			found := false
			for _, f := range findings {
				if f.CheckType == "password_policy" {
					found = true
					break
				}
			}
			if found != tt.wantDrift {
				t.Errorf("PasswordPolicy(min=%d,lockout=%d): wantDrift=%v, got=%v",
					tt.minLen, tt.lockout, tt.wantDrift, found)
			}
		})
	}
}

// helper
func strPtr(s string) *string { return &s }
