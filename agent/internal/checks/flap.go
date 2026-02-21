// Package checks implements compliance checks for Windows workstations.
package checks

import (
	"fmt"
	"log"
	"sync"
	"time"
)

// FlapDetector tracks recent check results and suppresses drift events
// when a check is rapidly alternating between pass and fail.
//
// A check is considered "flapping" when it has >= flapThreshold state
// transitions within the observation window. When flapping is detected,
// only the first failure event is sent; subsequent failures are suppressed
// until the check stabilizes (stays in the same state for stabilizeCount
// consecutive cycles).
type FlapDetector struct {
	mu              sync.Mutex
	history         map[string]*checkHistory
	windowSize      int // number of recent results to track
	flapThreshold   int // state transitions to trigger flapping
	stabilizeCount  int // consecutive same-state results to clear flapping
}

type checkHistory struct {
	results     []bool    // ring buffer of pass/fail (true=pass)
	timestamps  []time.Time
	pos         int       // current position in ring buffer
	count       int       // total results stored
	flapping    bool      // currently in flap state
	suppressed  int       // events suppressed since flap detected
	lastSent    time.Time // last time we actually sent an event
}

// NewFlapDetector creates a detector with sensible defaults.
// windowSize=6: track last 6 check cycles (~30 min at 5min interval)
// flapThreshold=3: 3+ state transitions = flapping
// stabilizeCount=3: 3 consecutive same results = stable again
func NewFlapDetector() *FlapDetector {
	return &FlapDetector{
		history:        make(map[string]*checkHistory),
		windowSize:     6,
		flapThreshold:  3,
		stabilizeCount: 3,
	}
}

// ShouldSend records a check result and returns whether the drift event
// should be sent. Only called for FAILED checks (passed checks don't
// generate drift events).
//
// Returns:
//   - true: send the drift event normally
//   - false: suppress (check is flapping, event already reported)
func (fd *FlapDetector) ShouldSend(checkType string, passed bool) bool {
	fd.mu.Lock()
	defer fd.mu.Unlock()

	h, ok := fd.history[checkType]
	if !ok {
		h = &checkHistory{
			results:    make([]bool, fd.windowSize),
			timestamps: make([]time.Time, fd.windowSize),
		}
		fd.history[checkType] = h
	}

	// Record this result
	h.results[h.pos] = passed
	h.timestamps[h.pos] = time.Now()
	h.pos = (h.pos + 1) % fd.windowSize
	if h.count < fd.windowSize {
		h.count++
	}

	// Not enough history yet — always send
	if h.count < 3 {
		if !passed {
			h.lastSent = time.Now()
		}
		return !passed // only send if failed
	}

	// Check for stabilization first: last N results all the same
	stabilized := fd.isStabilized(h)

	if h.flapping && stabilized {
		h.flapping = false
		if h.suppressed > 0 {
			log.Printf("[flap] %s stabilized after suppressing %d events", checkType, h.suppressed)
		}
		h.suppressed = 0
	} else if !h.flapping {
		// Only check for new flapping if not just stabilized
		transitions := fd.countTransitions(h)
		if transitions >= fd.flapThreshold {
			h.flapping = true
			h.suppressed = 0
			log.Printf("[flap] %s detected as flapping (%d transitions in %d checks)",
				checkType, transitions, h.count)
		}
	}

	// If the check passed, nothing to send regardless
	if passed {
		return false
	}

	// Check is failing
	if h.flapping {
		// Allow one event per flap episode, then suppress
		if h.lastSent.IsZero() || time.Since(h.lastSent) > 30*time.Minute {
			// Send one event per 30 min even when flapping (keep scorecard fresh)
			h.lastSent = time.Now()
			log.Printf("[flap] %s sending periodic flap report (suppressed %d)", checkType, h.suppressed)
			return true
		}
		h.suppressed++
		return false
	}

	// Not flapping, normal fail — send it
	h.lastSent = time.Now()
	return true
}

// countTransitions counts how many times the check result changed
// in the observation window.
func (fd *FlapDetector) countTransitions(h *checkHistory) int {
	if h.count < 2 {
		return 0
	}

	transitions := 0
	n := h.count
	// Walk the ring buffer from oldest to newest
	start := 0
	if h.count >= fd.windowSize {
		start = h.pos // oldest entry in a full buffer
	}

	prev := h.results[start%fd.windowSize]
	for i := 1; i < n; i++ {
		idx := (start + i) % fd.windowSize
		if h.results[idx] != prev {
			transitions++
		}
		prev = h.results[idx]
	}

	return transitions
}

// isStabilized returns true if the last stabilizeCount results are all the same.
func (fd *FlapDetector) isStabilized(h *checkHistory) bool {
	if h.count < fd.stabilizeCount {
		return false
	}

	// Check last N results (most recent)
	newest := (h.pos - 1 + fd.windowSize) % fd.windowSize
	val := h.results[newest]
	for i := 1; i < fd.stabilizeCount; i++ {
		idx := (newest - i + fd.windowSize) % fd.windowSize
		if h.results[idx] != val {
			return false
		}
	}
	return true
}

// Status returns a human-readable status for a check type.
func (fd *FlapDetector) Status(checkType string) string {
	fd.mu.Lock()
	defer fd.mu.Unlock()

	h, ok := fd.history[checkType]
	if !ok {
		return "no data"
	}
	if h.flapping {
		return fmt.Sprintf("flapping (suppressed %d events)", h.suppressed)
	}
	return "stable"
}
