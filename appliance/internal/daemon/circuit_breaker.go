package daemon

import (
	"log"
	"sync"
	"time"
)

// CircuitState represents the state of a circuit breaker.
type CircuitState string

const (
	CircuitClosed   CircuitState = "closed"    // Normal operation
	CircuitOpen     CircuitState = "open"       // Failing, skip requests
	CircuitHalfOpen CircuitState = "half-open"  // Testing with single request
)

// CircuitBreaker prevents repeated calls to a failing service.
// After `threshold` consecutive failures, it opens the circuit for `resetTimeout`.
// After the timeout, it transitions to half-open and allows one probe request.
// If the probe succeeds, the circuit closes. If it fails, it reopens.
type CircuitBreaker struct {
	mu           sync.Mutex
	state        CircuitState
	failures     int
	threshold    int
	lastFailure  time.Time
	resetTimeout time.Duration
}

// NewCircuitBreaker creates a breaker with the given threshold and reset timeout.
func NewCircuitBreaker(threshold int, resetTimeout time.Duration) *CircuitBreaker {
	return &CircuitBreaker{
		state:        CircuitClosed,
		threshold:    threshold,
		resetTimeout: resetTimeout,
	}
}

// Allow checks if a request should be allowed through.
// Returns true if the circuit is closed or if enough time has passed (half-open probe).
func (cb *CircuitBreaker) Allow() bool {
	cb.mu.Lock()
	defer cb.mu.Unlock()

	switch cb.state {
	case CircuitClosed:
		return true
	case CircuitOpen:
		if time.Since(cb.lastFailure) > cb.resetTimeout {
			cb.state = CircuitHalfOpen
			log.Printf("[circuit-breaker] transitioning to half-open after %v timeout", cb.resetTimeout)
			return true // Allow one probe request
		}
		return false
	case CircuitHalfOpen:
		return false // Only one request allowed in half-open; already in flight
	}
	return true
}

// RecordSuccess resets the failure count and closes the circuit.
func (cb *CircuitBreaker) RecordSuccess() {
	cb.mu.Lock()
	defer cb.mu.Unlock()

	if cb.state == CircuitHalfOpen {
		log.Printf("[circuit-breaker] probe succeeded, closing circuit")
	}
	cb.failures = 0
	cb.state = CircuitClosed
}

// RecordFailure increments the failure count. If threshold is reached, opens the circuit.
func (cb *CircuitBreaker) RecordFailure() {
	cb.mu.Lock()
	defer cb.mu.Unlock()

	cb.failures++
	cb.lastFailure = time.Now()

	if cb.state == CircuitHalfOpen {
		cb.state = CircuitOpen
		log.Printf("[circuit-breaker] half-open probe failed, reopening circuit for %v", cb.resetTimeout)
		return
	}

	if cb.failures >= cb.threshold {
		cb.state = CircuitOpen
		log.Printf("[circuit-breaker] %d consecutive failures, opening circuit for %v", cb.failures, cb.resetTimeout)
	}
}

// State returns the current circuit state.
func (cb *CircuitBreaker) State() CircuitState {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	return cb.state
}

// Failures returns the current consecutive failure count.
func (cb *CircuitBreaker) Failures() int {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	return cb.failures
}
