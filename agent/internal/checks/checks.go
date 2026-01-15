// Package checks implements compliance checks for Windows workstations.
package checks

import (
	"context"
	"os"
	"sync"
	"time"
)

// CheckResult represents the outcome of a compliance check
type CheckResult struct {
	CheckType    string
	Passed       bool
	Expected     string
	Actual       string
	HIPAAControl string
	Metadata     map[string]string
	Error        error
}

// Check is the interface all compliance checks implement
type Check interface {
	Name() string
	Run(ctx context.Context) CheckResult
}

// Registry manages enabled checks
type Registry struct {
	checks  map[string]Check
	enabled []string
	mu      sync.RWMutex
}

// NewRegistry creates a registry with specified enabled checks
func NewRegistry(enabled []string) *Registry {
	r := &Registry{
		checks:  make(map[string]Check),
		enabled: enabled,
	}

	// Register all available checks
	r.Register(&BitLockerCheck{})
	r.Register(&DefenderCheck{})
	r.Register(&PatchesCheck{})
	r.Register(&FirewallCheck{})
	r.Register(&ScreenLockCheck{})
	r.Register(&RMMCheck{})

	return r
}

// Register adds a check to the registry
func (r *Registry) Register(c Check) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.checks[c.Name()] = c
}

// RunAll executes all enabled checks concurrently
func (r *Registry) RunAll(ctx context.Context) []CheckResult {
	r.mu.RLock()
	defer r.mu.RUnlock()

	var wg sync.WaitGroup
	results := make([]CheckResult, 0, len(r.enabled))
	resultChan := make(chan CheckResult, len(r.enabled))

	for _, name := range r.enabled {
		check, ok := r.checks[name]
		if !ok {
			continue
		}

		wg.Add(1)
		go func(c Check) {
			defer wg.Done()
			resultChan <- c.Run(ctx)
		}(check)
	}

	go func() {
		wg.Wait()
		close(resultChan)
	}()

	for result := range resultChan {
		results = append(results, result)
	}

	return results
}

// GetHostname returns the local hostname
func GetHostname() string {
	hostname, err := os.Hostname()
	if err != nil {
		return "unknown"
	}
	return hostname
}

// GetTimestamp returns current Unix timestamp
func GetTimestamp() int64 {
	return time.Now().Unix()
}
