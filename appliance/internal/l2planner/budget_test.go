package l2planner

import (
	"testing"
)

func TestBudgetDefaults(t *testing.T) {
	bt := NewBudgetTracker(DefaultBudgetConfig())
	stats := bt.Stats()

	if stats.DailyBudgetUSD != 10.0 {
		t.Errorf("Expected $10.00 budget, got $%.2f", stats.DailyBudgetUSD)
	}
	if stats.MaxCallsPerHour != 60 {
		t.Errorf("Expected 60 calls/hr, got %d", stats.MaxCallsPerHour)
	}
	if stats.ConcurrentCapacity != 3 {
		t.Errorf("Expected 3 concurrent, got %d", stats.ConcurrentCapacity)
	}
}

func TestCheckBudgetWithinLimits(t *testing.T) {
	bt := NewBudgetTracker(DefaultBudgetConfig())

	err := bt.CheckBudget()
	if err != nil {
		t.Errorf("Fresh budget should be within limits: %v", err)
	}
}

func TestRecordCost(t *testing.T) {
	bt := NewBudgetTracker(DefaultBudgetConfig())

	// Typical Haiku call: ~1000 input, ~500 output tokens
	cost := bt.RecordCost(1000, 500)

	expected := CalculateCost(1000, 500)
	if cost != expected {
		t.Errorf("Cost mismatch: got %.6f, want %.6f", cost, expected)
	}

	stats := bt.Stats()
	if stats.DailySpendUSD != expected {
		t.Errorf("Daily spend: got %.6f, want %.6f", stats.DailySpendUSD, expected)
	}
	if stats.HourlyCalls != 1 {
		t.Errorf("Hourly calls: got %d, want 1", stats.HourlyCalls)
	}
}

func TestDailyBudgetExhaustion(t *testing.T) {
	bt := NewBudgetTracker(BudgetConfig{
		DailyBudgetUSD:     0.01, // $0.01 — tiny budget
		MaxCallsPerHour:    1000,
		MaxConcurrentCalls: 3,
	})

	// First call should work
	if err := bt.CheckBudget(); err != nil {
		t.Errorf("First call should be within budget: %v", err)
	}

	// Record a cost that exceeds the budget
	bt.RecordCost(100000, 10000) // way over $0.01

	// Second call should fail
	if err := bt.CheckBudget(); err == nil {
		t.Error("Should have exhausted daily budget")
	}
}

func TestHourlyRateLimit(t *testing.T) {
	bt := NewBudgetTracker(BudgetConfig{
		DailyBudgetUSD:     1000.00,
		MaxCallsPerHour:    3, // very low
		MaxConcurrentCalls: 3,
	})

	// Use up hourly allowance
	bt.RecordCost(100, 50)
	bt.RecordCost(100, 50)
	bt.RecordCost(100, 50)

	// Should be rate-limited
	if err := bt.CheckBudget(); err == nil {
		t.Error("Should have hit hourly rate limit")
	}
}

func TestConcurrencySemaphore(t *testing.T) {
	bt := NewBudgetTracker(BudgetConfig{
		DailyBudgetUSD:     10.0,
		MaxCallsPerHour:    60,
		MaxConcurrentCalls: 2,
	})

	// Acquire 2 slots
	release1 := bt.Acquire()
	release2 := bt.Acquire()

	// Third should fail (non-blocking)
	_, ok := bt.TryAcquire()
	if ok {
		t.Error("Should not acquire when at capacity")
	}

	// Release one and try again
	release1()

	release3, ok := bt.TryAcquire()
	if !ok {
		t.Error("Should acquire after release")
	}

	release2()
	release3()
}

func TestCalculateCost(t *testing.T) {
	// 1M input tokens at $0.80 + 1M output tokens at $4.00 = $4.80
	cost := CalculateCost(1_000_000, 1_000_000)
	expected := 4.80
	if cost != expected {
		t.Errorf("1M+1M tokens: got $%.2f, want $%.2f", cost, expected)
	}

	// Typical Haiku call: 2000 input, 500 output
	cost = CalculateCost(2000, 500)
	expected = 2000.0/1_000_000*0.80 + 500.0/1_000_000*4.00
	if cost != expected {
		t.Errorf("Typical call: got $%.6f, want $%.6f", cost, expected)
	}
}
