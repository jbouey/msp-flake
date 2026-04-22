package orders

import (
	"context"
	"testing"
)

// TestNixGCOrderTypeRegistered guards that the handler is wired into the
// dispatcher. If someone deletes the p.handlers["nix_gc"] line in
// NewProcessor, this fails — saves us from a silently-unprocessable order
// sitting in admin_orders forever.
func TestNixGCOrderTypeRegistered(t *testing.T) {
	p := NewProcessor(t.TempDir(), nil)
	if _, ok := p.handlers["nix_gc"]; !ok {
		t.Fatal("expected 'nix_gc' handler registered in NewProcessor()")
	}
}

// TestNixGCParameterValidation guards the input validator. The handler
// must reject bad inputs BEFORE invoking systemd-run, otherwise the
// nix-collect-garbage subprocess runs with unvalidated args and we lose
// the ability to audit what was actually requested.
func TestNixGCParameterValidation(t *testing.T) {
	p := NewProcessor(t.TempDir(), nil)

	cases := []struct {
		name   string
		params map[string]interface{}
		wantOK bool
	}{
		{"days_zero", map[string]interface{}{"older_than_days": 0}, false},
		{"days_negative", map[string]interface{}{"older_than_days": -5}, false},
		{"days_too_large", map[string]interface{}{"older_than_days": 9999}, false},
		{"days_non_numeric_string", map[string]interface{}{"older_than_days": "abc"}, false},
		{"days_non_numeric_type", map[string]interface{}{"older_than_days": []int{14}}, false},
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := p.handleNixGC(ctx, tc.params)
			if (err == nil) != tc.wantOK {
				t.Fatalf("handleNixGC(%v) err=%v wantOK=%v", tc.params, err, tc.wantOK)
			}
		})
	}
}

// TestNixGCTimeoutMapped guards that the 30-minute timeout is in place.
// Default (2 min) would kill a legitimate GC on a large /nix/store and
// every operator would see a spurious failure.
func TestNixGCTimeoutMapped(t *testing.T) {
	got := orderTimeout("nix_gc")
	const want = 30 * 60 * 1_000_000_000 // 30 min in ns
	if int64(got) != want {
		t.Fatalf("expected nix_gc timeout=30m (%d ns), got %v (%d ns)", want, got, int64(got))
	}
}
