package l2planner

import (
	"fmt"
	"sync"
	"time"
)

// Haiku 4.5 pricing (per million tokens)
const (
	HaikuInputPricePerMTok  = 0.80 // $0.80 per 1M input tokens
	HaikuOutputPricePerMTok = 4.00 // $4.00 per 1M output tokens
)

// BudgetTracker enforces spending and rate limits for L2 LLM calls.
type BudgetTracker struct {
	mu sync.Mutex

	// Configurable limits
	dailyBudgetUSD     float64
	maxCallsPerHour    int
	maxConcurrentCalls int

	// Current state
	dailySpendUSD float64
	dailyDate     string // YYYY-MM-DD for daily reset
	hourlyCalls   int
	hourlyReset   time.Time

	// Concurrency semaphore
	sem chan struct{}
}

// BudgetConfig holds budget configuration.
type BudgetConfig struct {
	DailyBudgetUSD     float64
	MaxCallsPerHour    int
	MaxConcurrentCalls int
}

// DefaultBudgetConfig returns sane defaults.
func DefaultBudgetConfig() BudgetConfig {
	return BudgetConfig{
		DailyBudgetUSD:     10.00,
		MaxCallsPerHour:    60,
		MaxConcurrentCalls: 3,
	}
}

// NewBudgetTracker creates a new budget tracker.
func NewBudgetTracker(cfg BudgetConfig) *BudgetTracker {
	if cfg.DailyBudgetUSD <= 0 {
		cfg.DailyBudgetUSD = 10.00
	}
	if cfg.MaxCallsPerHour <= 0 {
		cfg.MaxCallsPerHour = 60
	}
	if cfg.MaxConcurrentCalls <= 0 {
		cfg.MaxConcurrentCalls = 3
	}

	return &BudgetTracker{
		dailyBudgetUSD:     cfg.DailyBudgetUSD,
		maxCallsPerHour:    cfg.MaxCallsPerHour,
		maxConcurrentCalls: cfg.MaxConcurrentCalls,
		dailyDate:          time.Now().UTC().Format("2006-01-02"),
		hourlyReset:        time.Now().UTC().Add(time.Hour),
		sem:                make(chan struct{}, cfg.MaxConcurrentCalls),
	}
}

// CheckBudget returns nil if a call is within budget, or an error explaining why not.
func (b *BudgetTracker) CheckBudget() error {
	b.mu.Lock()
	defer b.mu.Unlock()

	b.resetIfNeeded()

	// Daily spend check
	if b.dailySpendUSD >= b.dailyBudgetUSD {
		return fmt.Errorf("daily budget exhausted: $%.4f of $%.2f spent", b.dailySpendUSD, b.dailyBudgetUSD)
	}

	// Hourly rate check
	if b.hourlyCalls >= b.maxCallsPerHour {
		return fmt.Errorf("hourly rate limit: %d of %d calls used", b.hourlyCalls, b.maxCallsPerHour)
	}

	return nil
}

// Acquire acquires a concurrency slot. Blocks until one is available.
// Returns a release function that MUST be called when done.
func (b *BudgetTracker) Acquire() func() {
	b.sem <- struct{}{}
	return func() { <-b.sem }
}

// TryAcquire tries to acquire a concurrency slot without blocking.
// Returns a release function and true if acquired, nil and false otherwise.
func (b *BudgetTracker) TryAcquire() (func(), bool) {
	select {
	case b.sem <- struct{}{}:
		return func() { <-b.sem }, true
	default:
		return nil, false
	}
}

// RecordCost records the cost of a completed API call and increments the hourly counter.
func (b *BudgetTracker) RecordCost(inputTokens, outputTokens int) float64 {
	cost := CalculateCost(inputTokens, outputTokens)

	b.mu.Lock()
	defer b.mu.Unlock()

	b.resetIfNeeded()
	b.dailySpendUSD += cost
	b.hourlyCalls++

	return cost
}

// CalculateCost computes the cost for a given number of tokens.
func CalculateCost(inputTokens, outputTokens int) float64 {
	inputCost := float64(inputTokens) / 1_000_000 * HaikuInputPricePerMTok
	outputCost := float64(outputTokens) / 1_000_000 * HaikuOutputPricePerMTok
	return inputCost + outputCost
}

// Stats returns current budget statistics.
func (b *BudgetTracker) Stats() BudgetStats {
	b.mu.Lock()
	defer b.mu.Unlock()

	b.resetIfNeeded()

	return BudgetStats{
		DailySpendUSD:      b.dailySpendUSD,
		DailyBudgetUSD:     b.dailyBudgetUSD,
		DailyRemaining:     b.dailyBudgetUSD - b.dailySpendUSD,
		HourlyCalls:        b.hourlyCalls,
		MaxCallsPerHour:    b.maxCallsPerHour,
		HourlyRemaining:    b.maxCallsPerHour - b.hourlyCalls,
		ConcurrentCapacity: b.maxConcurrentCalls,
	}
}

// BudgetStats holds budget state for reporting.
type BudgetStats struct {
	DailySpendUSD      float64
	DailyBudgetUSD     float64
	DailyRemaining     float64
	HourlyCalls        int
	MaxCallsPerHour    int
	HourlyRemaining    int
	ConcurrentCapacity int
}

// resetIfNeeded resets daily and hourly counters when their windows expire.
// Must be called with mu held.
func (b *BudgetTracker) resetIfNeeded() {
	now := time.Now().UTC()
	today := now.Format("2006-01-02")

	if today != b.dailyDate {
		b.dailySpendUSD = 0
		b.dailyDate = today
	}

	if now.After(b.hourlyReset) {
		b.hourlyCalls = 0
		b.hourlyReset = now.Add(time.Hour)
	}
}
