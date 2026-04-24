package daemon

import (
	"strings"
	"testing"
	"time"
)

// TestApplyServerDirectivesL2ConfidenceThreshold verifies that
// ApplyServerDirectives stores the L2 confidence threshold and that
// GetL2ConfidenceThreshold returns it faithfully. Zero-value (server
// not directing a specific value) must also round-trip as zero so
// callers can fall back to their local default.
func TestApplyServerDirectivesL2ConfidenceThreshold(t *testing.T) {
	sm := NewStateManager()

	// Initial state: zero.
	if got := sm.GetL2ConfidenceThreshold(); got != 0 {
		t.Errorf("initial threshold = %v, want 0", got)
	}

	sm.ApplyServerDirectives(0.85, "", false)
	if got := sm.GetL2ConfidenceThreshold(); got != 0.85 {
		t.Errorf("after set 0.85, GetL2ConfidenceThreshold() = %v, want 0.85", got)
	}

	// Server stops directing → zero flows back.
	sm.ApplyServerDirectives(0, "", false)
	if got := sm.GetL2ConfidenceThreshold(); got != 0 {
		t.Errorf("after clear, threshold = %v, want 0 (fallback signal)", got)
	}
}

// TestIsHealingSuppressedMaintenance exercises the maintenance_until
// gate. A future RFC3339 timestamp must suppress healing; a past one
// must not; an unparseable string must be rejected (silent pass).
func TestIsHealingSuppressedMaintenance(t *testing.T) {
	sm := NewStateManager()

	// Baseline: nothing set → not suppressed.
	if suppressed, _ := sm.IsHealingSuppressed(); suppressed {
		t.Fatalf("baseline state should not suppress healing")
	}

	// Future window → suppressed with reason=maintenance.
	future := time.Now().Add(1 * time.Hour).UTC().Format(time.RFC3339)
	sm.ApplyServerDirectives(0, future, false)
	suppressed, reason := sm.IsHealingSuppressed()
	if !suppressed {
		t.Fatalf("future MaintenanceUntil should suppress healing; got suppressed=%v", suppressed)
	}
	if reason != "maintenance" {
		t.Errorf("expected reason='maintenance', got %q", reason)
	}

	// Past window → not suppressed.
	past := time.Now().Add(-1 * time.Hour).UTC().Format(time.RFC3339)
	sm.ApplyServerDirectives(0, past, false)
	if suppressed, _ := sm.IsHealingSuppressed(); suppressed {
		t.Errorf("past MaintenanceUntil must not suppress healing")
	}

	// Empty string → clears the window.
	sm.ApplyServerDirectives(0, "", false)
	if suppressed, _ := sm.IsHealingSuppressed(); suppressed {
		t.Errorf("empty MaintenanceUntil must clear the suppression")
	}

	// Malformed string → parse fails, stored as zero-value, not suppressed.
	sm.ApplyServerDirectives(0, "not-a-date", false)
	if suppressed, _ := sm.IsHealingSuppressed(); suppressed {
		t.Errorf("unparseable MaintenanceUntil must not fake-suppress healing")
	}
}

// TestIsHealingSuppressedBillingHold verifies the billing_hold gate
// independently of the maintenance window.
func TestIsHealingSuppressedBillingHold(t *testing.T) {
	sm := NewStateManager()

	sm.ApplyServerDirectives(0, "", true)
	suppressed, reason := sm.IsHealingSuppressed()
	if !suppressed {
		t.Fatalf("billing_hold=true must suppress healing")
	}
	if reason != "billing_hold" {
		t.Errorf("expected reason='billing_hold', got %q", reason)
	}

	sm.ApplyServerDirectives(0, "", false)
	if suppressed, _ := sm.IsHealingSuppressed(); suppressed {
		t.Errorf("billing_hold=false must clear suppression")
	}
}

// TestIsHealingSuppressedBothActive verifies precedence when BOTH
// maintenance and billing_hold are active. Maintenance wins the
// reason string — operators care more about "why" than strict order.
func TestIsHealingSuppressedBothActive(t *testing.T) {
	sm := NewStateManager()
	future := time.Now().Add(30 * time.Minute).UTC().Format(time.RFC3339)
	sm.ApplyServerDirectives(0, future, true)

	suppressed, reason := sm.IsHealingSuppressed()
	if !suppressed {
		t.Fatalf("both directives active must suppress healing")
	}
	if reason != "maintenance" {
		t.Errorf("maintenance should win the reason; got %q", reason)
	}
}

// TestApplyServerDirectivesIdempotent exercises the "no change" path
// — repeated identical applies should not spam logs (tested indirectly
// by ensuring no state corruption after N applies).
func TestApplyServerDirectivesIdempotent(t *testing.T) {
	sm := NewStateManager()
	future := time.Now().Add(2 * time.Hour).UTC().Format(time.RFC3339)

	for i := 0; i < 5; i++ {
		sm.ApplyServerDirectives(0.75, future, true)
	}

	if got := sm.GetL2ConfidenceThreshold(); got != 0.75 {
		t.Errorf("after 5 identical applies, threshold drifted: %v", got)
	}
	suppressed, reason := sm.IsHealingSuppressed()
	if !suppressed || reason != "maintenance" {
		t.Errorf("after 5 identical applies, suppression state drifted: %v / %q", suppressed, reason)
	}
}

// TestRFC3339ParsingSilentOnBadInput asserts malformed maintenance_until
// strings (the most plausible foot-gun) fall through as "no window"
// rather than corrupting state. Includes a few common bad forms.
func TestRFC3339ParsingSilentOnBadInput(t *testing.T) {
	sm := NewStateManager()

	badForms := []string{
		"not-a-date",
		"2026-04-24",                 // no time component
		"2026-04-24 15:00:00",        // missing T and Z
		strings.Repeat("x", 100),     // garbage
	}
	for _, bad := range badForms {
		sm.ApplyServerDirectives(0, bad, false)
		if suppressed, _ := sm.IsHealingSuppressed(); suppressed {
			t.Errorf("malformed input %q caused false suppression", bad)
		}
	}
}
