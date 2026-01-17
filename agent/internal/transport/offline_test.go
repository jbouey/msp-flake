// Package transport handles communication with the appliance.
package transport

import (
	"os"
	"testing"
	"time"

	pb "github.com/osiriscare/agent/proto"
)

func TestNewOfflineQueue(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "queue-test-*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tmpDir)

	q, err := NewOfflineQueue(tmpDir)
	if err != nil {
		t.Fatalf("failed to create queue: %v", err)
	}
	defer q.Close()

	if q.maxSize != DefaultMaxQueueSize {
		t.Errorf("expected maxSize %d, got %d", DefaultMaxQueueSize, q.maxSize)
	}

	if q.maxAge != DefaultMaxQueueAge {
		t.Errorf("expected maxAge %v, got %v", DefaultMaxQueueAge, q.maxAge)
	}
}

func TestNewOfflineQueueWithOptions(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "queue-test-*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tmpDir)

	opts := QueueOptions{
		MaxSize: 100,
		MaxAge:  time.Hour,
	}

	q, err := NewOfflineQueueWithOptions(tmpDir, opts)
	if err != nil {
		t.Fatalf("failed to create queue: %v", err)
	}
	defer q.Close()

	if q.maxSize != 100 {
		t.Errorf("expected maxSize 100, got %d", q.maxSize)
	}

	if q.maxAge != time.Hour {
		t.Errorf("expected maxAge 1h, got %v", q.maxAge)
	}
}

func TestEnqueueDequeue(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "queue-test-*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tmpDir)

	q, err := NewOfflineQueue(tmpDir)
	if err != nil {
		t.Fatalf("failed to create queue: %v", err)
	}
	defer q.Close()

	// Initially empty
	if q.Count() != 0 {
		t.Errorf("expected count 0, got %d", q.Count())
	}

	// Enqueue an event
	event := &pb.DriftEvent{
		AgentId: "test-machine",
		CheckType: "firewall",
		Passed:    false,
	}

	err = q.Enqueue(event)
	if err != nil {
		t.Fatalf("failed to enqueue: %v", err)
	}

	// Count should be 1
	if q.Count() != 1 {
		t.Errorf("expected count 1, got %d", q.Count())
	}

	// Dequeue
	dequeued, ok := q.Dequeue()
	if !ok {
		t.Fatal("expected to dequeue an event")
	}

	if dequeued.AgentId != "test-machine" {
		t.Errorf("expected machine_id 'test-machine', got '%s'", dequeued.AgentId)
	}

	// Now empty
	if q.Count() != 0 {
		t.Errorf("expected count 0 after dequeue, got %d", q.Count())
	}
}

func TestEnqueueRaw(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "queue-test-*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tmpDir)

	q, err := NewOfflineQueue(tmpDir)
	if err != nil {
		t.Fatalf("failed to create queue: %v", err)
	}
	defer q.Close()

	err = q.EnqueueRaw("heartbeat", []byte(`{"status":"ok"}`))
	if err != nil {
		t.Fatalf("failed to enqueue raw: %v", err)
	}

	if q.Count() != 1 {
		t.Errorf("expected count 1, got %d", q.Count())
	}
}

func TestDequeueAll(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "queue-test-*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tmpDir)

	q, err := NewOfflineQueue(tmpDir)
	if err != nil {
		t.Fatalf("failed to create queue: %v", err)
	}
	defer q.Close()

	// Enqueue multiple events
	for i := 0; i < 5; i++ {
		event := &pb.DriftEvent{
			AgentId: "test-machine",
			CheckType: "test",
		}
		if err := q.Enqueue(event); err != nil {
			t.Fatalf("failed to enqueue: %v", err)
		}
	}

	if q.Count() != 5 {
		t.Errorf("expected count 5, got %d", q.Count())
	}

	// Dequeue 3
	events, err := q.DequeueAll(3)
	if err != nil {
		t.Fatalf("failed to dequeue all: %v", err)
	}

	if len(events) != 3 {
		t.Errorf("expected 3 events, got %d", len(events))
	}

	// 2 remaining
	if q.Count() != 2 {
		t.Errorf("expected count 2, got %d", q.Count())
	}
}

func TestQueueSizeLimit(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "queue-test-*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tmpDir)

	opts := QueueOptions{
		MaxSize: 10,
		MaxAge:  time.Hour,
	}

	q, err := NewOfflineQueueWithOptions(tmpDir, opts)
	if err != nil {
		t.Fatalf("failed to create queue: %v", err)
	}
	defer q.Close()

	// Fill the queue
	for i := 0; i < 15; i++ {
		event := &pb.DriftEvent{
			AgentId: "test-machine",
			CheckType: "test",
		}
		if err := q.Enqueue(event); err != nil {
			t.Fatalf("failed to enqueue event %d: %v", i, err)
		}
	}

	// Queue should have pruned old events
	count := q.Count()
	if count > 10 {
		t.Errorf("expected count <= 10 after enforcing limit, got %d", count)
	}
}

func TestQueueStats(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "queue-test-*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tmpDir)

	opts := QueueOptions{
		MaxSize: 100,
		MaxAge:  time.Hour,
	}

	q, err := NewOfflineQueueWithOptions(tmpDir, opts)
	if err != nil {
		t.Fatalf("failed to create queue: %v", err)
	}
	defer q.Close()

	// Initial stats
	stats := q.Stats()
	if stats.Count != 0 {
		t.Errorf("expected count 0, got %d", stats.Count)
	}
	if stats.MaxSize != 100 {
		t.Errorf("expected maxSize 100, got %d", stats.MaxSize)
	}
	if stats.UsageRatio != 0 {
		t.Errorf("expected usage ratio 0, got %f", stats.UsageRatio)
	}

	// Add some events
	for i := 0; i < 50; i++ {
		event := &pb.DriftEvent{
			AgentId: "test-machine",
			CheckType: "test",
		}
		if err := q.Enqueue(event); err != nil {
			t.Fatalf("failed to enqueue: %v", err)
		}
	}

	stats = q.Stats()
	if stats.Count != 50 {
		t.Errorf("expected count 50, got %d", stats.Count)
	}
	if stats.UsageRatio != 0.5 {
		t.Errorf("expected usage ratio 0.5, got %f", stats.UsageRatio)
	}
}

func TestPrune(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "queue-test-*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tmpDir)

	q, err := NewOfflineQueue(tmpDir)
	if err != nil {
		t.Fatalf("failed to create queue: %v", err)
	}
	defer q.Close()

	// Enqueue events
	for i := 0; i < 5; i++ {
		event := &pb.DriftEvent{
			AgentId: "test-machine",
			CheckType: "test",
		}
		if err := q.Enqueue(event); err != nil {
			t.Fatalf("failed to enqueue: %v", err)
		}
	}

	// Prune with very short duration should remove nothing
	pruned, err := q.Prune(time.Second)
	if err != nil {
		t.Fatalf("prune failed: %v", err)
	}
	if pruned != 0 {
		t.Errorf("expected 0 pruned (events too new), got %d", pruned)
	}

	// Count should still be 5
	if q.Count() != 5 {
		t.Errorf("expected count 5, got %d", q.Count())
	}
}

func TestEmptyDequeue(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "queue-test-*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tmpDir)

	q, err := NewOfflineQueue(tmpDir)
	if err != nil {
		t.Fatalf("failed to create queue: %v", err)
	}
	defer q.Close()

	// Dequeue from empty queue
	event, ok := q.Dequeue()
	if ok {
		t.Error("expected ok=false for empty queue")
	}
	if event != nil {
		t.Error("expected nil event for empty queue")
	}
}
