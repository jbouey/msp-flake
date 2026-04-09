package daemon

import (
	"log/slog"
	"math"
	"sync"
	"time"
)

// healOutcome records a single healing attempt result with a timestamp.
type healOutcome struct {
	at      time.Time
	success bool
}

// healingRateTracker tracks healing attempt outcomes over a rolling 24-hour window.
// Used to log the healing rate after each scan cycle.
type healingRateTracker struct {
	mu       sync.Mutex
	outcomes []healOutcome
}

func newHealingRateTracker() *healingRateTracker {
	return &healingRateTracker{}
}

// Record adds a healing outcome (success or failure) to the tracker.
func (t *healingRateTracker) Record(success bool) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.outcomes = append(t.outcomes, healOutcome{at: time.Now(), success: success})
}

// Rate returns the healing success rate and counts over the last 24 hours.
// Evicts stale entries older than 24h.
func (t *healingRateTracker) Rate() (percent float64, successes, total int) {
	t.mu.Lock()
	defer t.mu.Unlock()

	cutoff := time.Now().Add(-24 * time.Hour)

	// Evict old entries
	fresh := t.outcomes[:0]
	for _, o := range t.outcomes {
		if o.at.After(cutoff) {
			fresh = append(fresh, o)
		}
	}
	t.outcomes = fresh

	total = len(fresh)
	if total == 0 {
		return 0, 0, 0
	}

	for _, o := range fresh {
		if o.success {
			successes++
		}
	}
	percent = float64(successes) / float64(total) * 100
	return percent, successes, total
}

// logHealingRate logs the 24h healing rate. Called after each scan cycle.
func (d *Daemon) logHealingRate() {
	if d.healTracker == nil {
		return
	}
	pct, ok, total := d.healTracker.Rate()
	if total == 0 {
		return // No healing attempts yet — nothing to log
	}
	slog.Info("healing rate", "component", "daemon", "rate_pct", math.Round(pct), "healed", ok, "total", total)
}
