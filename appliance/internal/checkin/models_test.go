package checkin

import (
	"testing"
	"time"
)

func TestNormalizeMAC(t *testing.T) {
	tests := []struct {
		input, want string
	}{
		{"84:3a:5b:91:b6:61", "84:3A:5B:91:B6:61"},
		{"84-3A-5B-91-B6-61", "84:3A:5B:91:B6:61"},
		{"843a5b91b661", "84:3A:5B:91:B6:61"},
		{"843A5B91B661", "84:3A:5B:91:B6:61"},
		{"84:3A:5B:91:B6:61", "84:3A:5B:91:B6:61"},
		{"invalid", "invalid"}, // Too short, returned as-is
		{"", ""},
	}
	for _, tt := range tests {
		got := NormalizeMAC(tt.input)
		if got != tt.want {
			t.Errorf("NormalizeMAC(%q) = %q, want %q", tt.input, got, tt.want)
		}
	}
}

func TestCleanMAC(t *testing.T) {
	tests := []struct {
		input, want string
	}{
		{"84:3a:5b:91:b6:61", "843A5B91B661"},
		{"84-3A-5B-91-B6-61", "843A5B91B661"},
		{"843a5b91b661", "843A5B91B661"},
	}
	for _, tt := range tests {
		got := CleanMAC(tt.input)
		if got != tt.want {
			t.Errorf("CleanMAC(%q) = %q, want %q", tt.input, got, tt.want)
		}
	}
}

func TestCanonicalApplianceID(t *testing.T) {
	got := CanonicalApplianceID("site-abc", "84:3a:5b:91:b6:61")
	want := "site-abc-84:3A:5B:91:B6:61"
	if got != want {
		t.Errorf("CanonicalApplianceID = %q, want %q", got, want)
	}
}

func TestIsoTime(t *testing.T) {
	ts := time.Date(2026, 2, 17, 15, 30, 0, 0, time.UTC)
	got := isoTime(ts)
	want := "2026-02-17T15:30:00Z"
	if got != want {
		t.Errorf("isoTime = %q, want %q", got, want)
	}
}

func TestIsoTimePtr(t *testing.T) {
	// Nil case
	got := isoTimePtr(nil)
	if got != nil {
		t.Error("isoTimePtr(nil) should be nil")
	}

	// Non-nil case
	ts := time.Date(2026, 2, 17, 15, 30, 0, 0, time.UTC)
	got = isoTimePtr(&ts)
	if got == nil {
		t.Fatal("isoTimePtr should not be nil")
	}
	if *got != "2026-02-17T15:30:00Z" {
		t.Errorf("isoTimePtr = %q, want %q", *got, "2026-02-17T15:30:00Z")
	}
}
