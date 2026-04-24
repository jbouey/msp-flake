// Package daemon — Credentials type.
//
// v40.6 (2026-04-24, Principal SWE round-table on source-of-truth
// hygiene) closes the in-memory split-brain. Pre-0.4.8 the daemon's
// sub-components (incident_reporter, l2planner.TelemetryReporter,
// logshipper.Shipper) each captured cfg.APIKey by value at
// construction — a rekey updated d.config.APIKey but N stale copies
// in N mutexes kept serving 401s forever. 0.4.8's SetAPIKey pattern
// was the emergency fix: N mutex-protected mirrors with explicit
// propagation on rekey.
//
// This file replaces that with the correct primitive: a single
// Credentials holder the Daemon owns, passed to sub-components as
// a `CredentialProvider` interface. Sub-components call APIKey()
// per-request. Updates are instantaneous, no propagation step
// required, test seam is clean (sub-components mock with a fake
// CredentialProvider instead of juggling 3 SetAPIKey calls).
//
// atomic.Pointer[string] is used so reads are lock-free and writes
// are atomic — strings in Go are (pointer, length) pairs, not
// atomic-word-sized, so naïve `apiKey = new` is a torn-read race.
// The atomic.Pointer indirection gives us correct memory semantics
// for free; the extra pointer dereference is measured in
// nanoseconds per HTTP request, nothing in a fleet context.
package daemon

import "sync/atomic"

// Credentials holds the daemon's current bearer token in an
// atomically-swappable cell. Construct via NewCredentials, mutate
// via Set, read via APIKey (which implements the CredentialProvider
// interface used across sub-component packages).
type Credentials struct {
	apiKey atomic.Pointer[string]
}

// NewCredentials returns a Credentials pre-loaded with the initial
// bearer token. Nil-safe callers (e.g. sub-components that received
// a nil provider) should still guard their own call sites.
func NewCredentials(initial string) *Credentials {
	c := &Credentials{}
	c.Set(initial)
	return c
}

// APIKey returns the current bearer token. Safe to call concurrently
// with Set from any goroutine. Returns "" on an uninitialized
// Credentials (should not happen in production — NewCredentials is
// the only construction path).
func (c *Credentials) APIKey() string {
	if c == nil {
		return ""
	}
	p := c.apiKey.Load()
	if p == nil {
		return ""
	}
	return *p
}

// Set atomically replaces the bearer token. Any in-flight HTTP
// requests that already captured the Authorization header see the
// old value; requests that build their header AFTER this call see
// the new value. There's no in-between torn-read window.
func (c *Credentials) Set(key string) {
	if c == nil {
		return
	}
	c.apiKey.Store(&key)
}
