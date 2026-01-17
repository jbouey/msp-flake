// Package checks implements Windows compliance checks.
package checks

import (
	"testing"
)

func TestCheckResultDefaults(t *testing.T) {
	result := CheckResult{}

	if result.Passed {
		t.Error("new CheckResult should not be passed by default")
	}

	if result.CheckType != "" {
		t.Error("new CheckResult should have empty CheckType")
	}
}

func TestCheckResultWithMetadata(t *testing.T) {
	result := CheckResult{
		CheckType:    "test",
		Passed:       true,
		Expected:     "expected",
		Actual:       "actual",
		HIPAAControl: "164.312(a)(1)",
		Metadata:     make(map[string]string),
	}

	result.Metadata["key"] = "value"

	if result.Metadata["key"] != "value" {
		t.Error("metadata should store values")
	}
}

func TestDefenderCheckName(t *testing.T) {
	check := &DefenderCheck{}
	if check.Name() != "defender" {
		t.Errorf("expected 'defender', got '%s'", check.Name())
	}
}

func TestBitLockerCheckName(t *testing.T) {
	check := &BitLockerCheck{}
	if check.Name() != "bitlocker" {
		t.Errorf("expected 'bitlocker', got '%s'", check.Name())
	}
}

func TestFirewallCheckName(t *testing.T) {
	check := &FirewallCheck{}
	if check.Name() != "firewall" {
		t.Errorf("expected 'firewall', got '%s'", check.Name())
	}
}

func TestPatchesCheckName(t *testing.T) {
	check := &PatchesCheck{}
	if check.Name() != "patches" {
		t.Errorf("expected 'patches', got '%s'", check.Name())
	}
}

func TestScreenLockCheckName(t *testing.T) {
	check := &ScreenLockCheck{}
	if check.Name() != "screenlock" {
		t.Errorf("expected 'screenlock', got '%s'", check.Name())
	}
}

func TestRMMCheckName(t *testing.T) {
	check := &RMMCheck{}
	if check.Name() != "rmm_detection" {
		t.Errorf("expected 'rmm_detection', got '%s'", check.Name())
	}
}

func TestParseUpdateDate(t *testing.T) {
	tests := []struct {
		input    string
		wantYear int
		wantErr  bool
	}{
		{"1/2/2024", 2024, false},
		{"01/02/2024", 2024, false},
		{"2024-01-02", 2024, false},
		{"invalid", 0, true},
	}

	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			parsed, err := parseUpdateDate(tt.input)
			if tt.wantErr {
				if err == nil {
					t.Error("expected error, got nil")
				}
				return
			}
			if err != nil {
				t.Errorf("unexpected error: %v", err)
				return
			}
			if parsed.Year() != tt.wantYear {
				t.Errorf("expected year %d, got %d", tt.wantYear, parsed.Year())
			}
		})
	}
}

func TestParseTimeoutString(t *testing.T) {
	tests := []struct {
		input   string
		want    int
		wantErr bool
	}{
		{"300", 300, false},
		{"900", 900, false},
		{"0", 0, false},
		{"abc", 0, true},
	}

	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got, err := parseTimeoutString(tt.input)
			if tt.wantErr {
				if err == nil {
					t.Error("expected error, got nil")
				}
				return
			}
			if err != nil {
				t.Errorf("unexpected error: %v", err)
				return
			}
			if got != tt.want {
				t.Errorf("expected %d, got %d", tt.want, got)
			}
		})
	}
}
