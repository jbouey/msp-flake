package orders

import (
	"context"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

func TestPendingUpdate_RoundTrip(t *testing.T) {
	dir := t.TempDir()
	now := time.Now().UTC().Truncate(time.Second)

	in := PendingUpdate{
		OrderID:         "order-abc",
		ExpectedVersion: "0.4.3",
		ScheduledAt:     now,
		TimeoutAt:       now.Add(10 * time.Minute),
	}
	if err := WritePendingUpdate(dir, in); err != nil {
		t.Fatalf("write: %v", err)
	}

	out := LoadPendingUpdate(dir)
	if out == nil {
		t.Fatal("load returned nil after write")
	}
	if out.OrderID != in.OrderID || out.ExpectedVersion != in.ExpectedVersion {
		t.Errorf("round-trip mismatch: got %+v want %+v", out, in)
	}

	if err := ClearPendingUpdate(dir); err != nil {
		t.Errorf("clear: %v", err)
	}
	if got := LoadPendingUpdate(dir); got != nil {
		t.Errorf("expected nil after clear, got %+v", got)
	}
}

func TestPendingUpdate_LoadAbsentReturnsNil(t *testing.T) {
	dir := t.TempDir()
	if got := LoadPendingUpdate(dir); got != nil {
		t.Fatalf("expected nil for absent marker, got %+v", got)
	}
}

func TestPendingUpdate_LoadMalformedReturnsNil(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, pendingUpdateFile), []byte("not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	if got := LoadPendingUpdate(dir); got != nil {
		t.Errorf("expected nil for malformed marker, got %+v", got)
	}
}

func TestPendingUpdate_WriteRequiresFields(t *testing.T) {
	dir := t.TempDir()
	if err := WritePendingUpdate(dir, PendingUpdate{}); err == nil {
		t.Error("expected error for empty PendingUpdate")
	}
}

// captureCompletion records the args passed to onComplete so tests can
// assert on success, error message, and result content.
type capturedCompletion struct {
	mu      sync.Mutex
	called  bool
	orderID string
	success bool
	result  map[string]interface{}
	errMsg  string
}

func (c *capturedCompletion) cb(_ context.Context, orderID string, success bool, result map[string]interface{}, errMsg string) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.called = true
	c.orderID = orderID
	c.success = success
	c.result = result
	c.errMsg = errMsg
	return nil
}

func (c *capturedCompletion) wait(t *testing.T, timeout time.Duration) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		c.mu.Lock()
		called := c.called
		c.mu.Unlock()
		if called {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatalf("completion callback was not invoked within %s", timeout)
}

func TestCompletePendingUpdate_VersionMatch_PostsSuccess(t *testing.T) {
	dir := t.TempDir()
	cap := &capturedCompletion{}
	p := NewProcessor(dir, cap.cb)

	// ScheduledAt 5 minutes ago so the 90s decision window has long passed.
	pending := PendingUpdate{
		OrderID:         "order-xyz",
		ExpectedVersion: "0.4.3",
		ScheduledAt:     time.Now().Add(-5 * time.Minute).UTC(),
		TimeoutAt:       time.Now().Add(5 * time.Minute).UTC(),
	}
	if err := WritePendingUpdate(dir, pending); err != nil {
		t.Fatal(err)
	}

	p.CompletePendingUpdate(context.Background(), "0.4.3")
	cap.wait(t, 2*time.Second)

	if !cap.success {
		t.Errorf("expected success=true, got false (errMsg=%q)", cap.errMsg)
	}
	if cap.orderID != "order-xyz" {
		t.Errorf("expected orderID=order-xyz, got %q", cap.orderID)
	}
	if got := LoadPendingUpdate(dir); got != nil {
		t.Errorf("expected marker cleared after success, got %+v", got)
	}
}

func TestCompletePendingUpdate_VersionMismatch_PostsRollbackFailure(t *testing.T) {
	dir := t.TempDir()
	cap := &capturedCompletion{}
	p := NewProcessor(dir, cap.cb)

	pending := PendingUpdate{
		OrderID:         "order-rb",
		ExpectedVersion: "0.4.3",
		ScheduledAt:     time.Now().Add(-5 * time.Minute).UTC(),
		TimeoutAt:       time.Now().Add(5 * time.Minute).UTC(),
	}
	if err := WritePendingUpdate(dir, pending); err != nil {
		t.Fatal(err)
	}

	p.CompletePendingUpdate(context.Background(), "0.4.0")
	cap.wait(t, 2*time.Second)

	if cap.success {
		t.Error("expected success=false on version mismatch")
	}
	if cap.errMsg == "" {
		t.Error("expected non-empty errMsg on version mismatch")
	}
	if got := LoadPendingUpdate(dir); got != nil {
		t.Errorf("expected marker cleared after failure, got %+v", got)
	}
}

func TestCompletePendingUpdate_NoMarker_NoOp(t *testing.T) {
	dir := t.TempDir()
	cap := &capturedCompletion{}
	p := NewProcessor(dir, cap.cb)

	p.CompletePendingUpdate(context.Background(), "0.4.3")
	// Give the goroutine a moment to fire if it were going to.
	time.Sleep(200 * time.Millisecond)

	cap.mu.Lock()
	called := cap.called
	cap.mu.Unlock()
	if called {
		t.Error("expected no completion call when marker is absent")
	}
}
