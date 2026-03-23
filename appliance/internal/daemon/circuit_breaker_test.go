package daemon

import (
	"testing"
	"time"
)

func TestCircuitBreaker_ClosedAllowsRequests(t *testing.T) {
	cb := NewCircuitBreaker(3, 5*time.Second)

	if !cb.Allow() {
		t.Fatal("expected closed circuit to allow requests")
	}
	if cb.State() != CircuitClosed {
		t.Fatalf("expected state closed, got %s", cb.State())
	}
}

func TestCircuitBreaker_OpensAfterThreshold(t *testing.T) {
	cb := NewCircuitBreaker(3, 5*time.Second)

	// Two failures should keep circuit closed
	cb.RecordFailure()
	cb.RecordFailure()
	if cb.State() != CircuitClosed {
		t.Fatalf("expected closed after 2 failures, got %s", cb.State())
	}

	// Third failure should open it
	cb.RecordFailure()
	if cb.State() != CircuitOpen {
		t.Fatalf("expected open after 3 failures, got %s", cb.State())
	}
	if cb.Failures() != 3 {
		t.Fatalf("expected 3 failures, got %d", cb.Failures())
	}
}

func TestCircuitBreaker_OpenBlocksRequests(t *testing.T) {
	cb := NewCircuitBreaker(2, 10*time.Second)

	cb.RecordFailure()
	cb.RecordFailure()

	if cb.State() != CircuitOpen {
		t.Fatalf("expected open, got %s", cb.State())
	}
	if cb.Allow() {
		t.Fatal("expected open circuit to block requests")
	}
}

func TestCircuitBreaker_TransitionsToHalfOpen(t *testing.T) {
	cb := NewCircuitBreaker(2, 50*time.Millisecond)

	cb.RecordFailure()
	cb.RecordFailure()
	if cb.State() != CircuitOpen {
		t.Fatalf("expected open, got %s", cb.State())
	}

	// Wait for reset timeout to expire
	time.Sleep(60 * time.Millisecond)

	// Allow should transition to half-open and return true
	if !cb.Allow() {
		t.Fatal("expected Allow to return true after reset timeout")
	}
	if cb.State() != CircuitHalfOpen {
		t.Fatalf("expected half-open, got %s", cb.State())
	}
}

func TestCircuitBreaker_HalfOpenProbeSuccess(t *testing.T) {
	cb := NewCircuitBreaker(2, 50*time.Millisecond)

	cb.RecordFailure()
	cb.RecordFailure()

	time.Sleep(60 * time.Millisecond)
	cb.Allow() // transition to half-open

	if cb.State() != CircuitHalfOpen {
		t.Fatalf("expected half-open, got %s", cb.State())
	}

	// Successful probe should close the circuit
	cb.RecordSuccess()
	if cb.State() != CircuitClosed {
		t.Fatalf("expected closed after successful probe, got %s", cb.State())
	}
	if cb.Failures() != 0 {
		t.Fatalf("expected 0 failures after success, got %d", cb.Failures())
	}
}

func TestCircuitBreaker_HalfOpenProbeFailure(t *testing.T) {
	cb := NewCircuitBreaker(2, 50*time.Millisecond)

	cb.RecordFailure()
	cb.RecordFailure()

	time.Sleep(60 * time.Millisecond)
	cb.Allow() // transition to half-open

	if cb.State() != CircuitHalfOpen {
		t.Fatalf("expected half-open, got %s", cb.State())
	}

	// Failed probe should reopen the circuit
	cb.RecordFailure()
	if cb.State() != CircuitOpen {
		t.Fatalf("expected open after failed probe, got %s", cb.State())
	}

	// Should block again immediately
	if cb.Allow() {
		t.Fatal("expected reopened circuit to block requests")
	}
}

func TestCircuitBreaker_SuccessResetsCount(t *testing.T) {
	cb := NewCircuitBreaker(3, 5*time.Second)

	cb.RecordFailure()
	cb.RecordFailure()
	if cb.Failures() != 2 {
		t.Fatalf("expected 2 failures, got %d", cb.Failures())
	}

	// Success should reset the counter
	cb.RecordSuccess()
	if cb.Failures() != 0 {
		t.Fatalf("expected 0 failures after success, got %d", cb.Failures())
	}
	if cb.State() != CircuitClosed {
		t.Fatalf("expected closed, got %s", cb.State())
	}

	// Should now need 3 more failures to open
	cb.RecordFailure()
	cb.RecordFailure()
	if cb.State() != CircuitClosed {
		t.Fatalf("expected still closed after 2 failures, got %s", cb.State())
	}
	cb.RecordFailure()
	if cb.State() != CircuitOpen {
		t.Fatalf("expected open after 3 failures, got %s", cb.State())
	}
}
