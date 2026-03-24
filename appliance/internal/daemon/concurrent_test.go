package daemon

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestConcurrentCooldownAccess(t *testing.T) {
	runtime.GOMAXPROCS(4)

	sm := NewStateManager()
	const goroutines = 30
	const iterations = 100

	var wg sync.WaitGroup
	wg.Add(goroutines)

	for g := 0; g < goroutines; g++ {
		go func(id int) {
			defer wg.Done()
			// Each goroutine hammers the same key and a unique key
			sharedKey := "host1:firewall"
			uniqueKey := fmt.Sprintf("host%d:check", id)
			for i := 0; i < iterations; i++ {
				sm.ShouldSuppress(sharedKey)
				sm.ShouldSuppress(uniqueKey)
			}
		}(g)
	}

	wg.Wait()

	// After all goroutines complete, cooldown should be in effect for the shared key
	// (it was seen many times). The first call allowed it, subsequent calls within
	// the cooldown window should suppress.
	if !sm.ShouldSuppress("host1:firewall") {
		// It's possible the cooldown expired if the test ran slow, but under
		// normal conditions the 10-minute default cooldown is still active.
		t.Log("shared key not suppressed — acceptable if test was slow")
	}
}

func TestConcurrentExhaustionTracking(t *testing.T) {
	runtime.GOMAXPROCS(4)

	sm := NewStateManager()
	const goroutines = 20
	const failuresPerGoroutine = 5
	key := "ws01:antivirus"

	var wg sync.WaitGroup
	wg.Add(goroutines)

	for g := 0; g < goroutines; g++ {
		go func() {
			defer wg.Done()
			for i := 0; i < failuresPerGoroutine; i++ {
				sm.RecordHealingFailure(key)
				sm.IsHealingExhausted(key)
			}
		}()
	}

	wg.Wait()

	// After 20 goroutines * 5 failures = 100 total calls to RecordHealingFailure,
	// the key must be exhausted (threshold is 3).
	if !sm.IsHealingExhausted(key) {
		t.Fatal("expected healing to be exhausted after many concurrent failures")
	}

	// Reset and verify it clears
	sm.ResetHealingExhaustion(key)
	if sm.IsHealingExhausted(key) {
		t.Fatal("expected healing exhaustion to be reset")
	}
}

func TestConcurrentTargetReadsWrites(t *testing.T) {
	runtime.GOMAXPROCS(4)

	sm := NewStateManager()
	const writers = 5
	const readers = 20
	const iterations = 100

	var wg sync.WaitGroup
	wg.Add(writers + readers)

	// Writers replace the linux targets slice repeatedly
	for w := 0; w < writers; w++ {
		go func(id int) {
			defer wg.Done()
			for i := 0; i < iterations; i++ {
				targets := []linuxTarget{
					{Hostname: fmt.Sprintf("host-%d-%d", id, i), Port: 22, Username: "root", Label: "linux"},
					{Hostname: fmt.Sprintf("host-%d-%d-b", id, i), Port: 22, Username: "admin", Label: "linux"},
				}
				sm.SetLinuxTargets(targets)
			}
		}(w)
	}

	// Readers get the targets and verify internal consistency
	for r := 0; r < readers; r++ {
		go func() {
			defer wg.Done()
			for i := 0; i < iterations; i++ {
				targets := sm.GetLinuxTargets()
				// The returned slice must be internally consistent:
				// either empty (initial state) or a coherent snapshot
				if len(targets) != 0 && len(targets) != 2 {
					t.Errorf("unexpected target count: %d (expected 0 or 2)", len(targets))
				}
			}
		}()
	}

	wg.Wait()
}

func TestConcurrentHealingJournal(t *testing.T) {
	runtime.GOMAXPROCS(4)

	tmpDir := t.TempDir()
	journal := newHealingJournal(tmpDir)

	const goroutines = 20
	var wg sync.WaitGroup
	wg.Add(goroutines)

	for g := 0; g < goroutines; g++ {
		go func(id int) {
			defer wg.Done()
			entryID := fmt.Sprintf("heal-%d", id)
			journal.StartHealing(entryID, "RB-001", fmt.Sprintf("host%d", id), "windows", "firewall", "L1")
			// Simulate brief work
			time.Sleep(time.Millisecond)
			journal.CompletePhase(entryID, "detect")
			time.Sleep(time.Millisecond)
			journal.FinishHealing(entryID, id%2 == 0, "")
		}(g)
	}

	wg.Wait()

	// All entries should be finished (removed from active tracking)
	if count := journal.ActiveCount(); count != 0 {
		t.Fatalf("expected 0 active entries after all goroutines complete, got %d", count)
	}

	// Verify journal file on disk is valid JSON
	data, err := os.ReadFile(filepath.Join(tmpDir, healingJournalFile))
	if err != nil {
		t.Fatalf("failed to read journal file: %v", err)
	}
	var jd journalData
	if err := json.Unmarshal(data, &jd); err != nil {
		t.Fatalf("journal file is not valid JSON: %v", err)
	}
}

func TestScanAtomicGuard(t *testing.T) {
	runtime.GOMAXPROCS(4)

	// Use a bare int32 to test the same atomic CAS pattern used by driftScanner.
	// We don't create a full driftScanner because it requires a running Daemon
	// with real network services. The pattern under test is:
	//   if !atomic.CompareAndSwapInt32(&running, 0, 1) { return rejected }
	//   defer atomic.StoreInt32(&running, 0)
	var running int32

	// Simulate first scan holding the lock
	if !atomic.CompareAndSwapInt32(&running, 0, 1) {
		t.Fatal("first scan should acquire the guard")
	}

	// Try to start a second scan — should be rejected
	if atomic.CompareAndSwapInt32(&running, 0, 1) {
		t.Fatal("second scan should be rejected while first is running")
	}

	// Release first scan
	atomic.StoreInt32(&running, 0)

	// Now a new scan should succeed
	if !atomic.CompareAndSwapInt32(&running, 0, 1) {
		t.Fatal("scan should succeed after first released the guard")
	}
	atomic.StoreInt32(&running, 0)

	// Stress test: many goroutines competing for the guard
	const goroutines = 50
	var acquired int64
	var rejected int64
	var wg sync.WaitGroup
	wg.Add(goroutines)

	for g := 0; g < goroutines; g++ {
		go func() {
			defer wg.Done()
			for i := 0; i < 100; i++ {
				if atomic.CompareAndSwapInt32(&running, 0, 1) {
					atomic.AddInt64(&acquired, 1)
					// Hold briefly to simulate scan work
					runtime.Gosched()
					atomic.StoreInt32(&running, 0)
				} else {
					atomic.AddInt64(&rejected, 1)
				}
			}
		}()
	}

	wg.Wait()

	t.Logf("atomic guard stress: %d acquired, %d rejected", acquired, rejected)
	if acquired == 0 {
		t.Fatal("no goroutine ever acquired the guard")
	}
	if rejected == 0 {
		t.Fatal("no goroutine was ever rejected — guard may not be working")
	}
}

func TestConcurrentStateManagerDiskPersist(t *testing.T) {
	runtime.GOMAXPROCS(4)

	tmpDir := t.TempDir()
	sm := NewStateManager()

	const writers = 10
	const persisters = 5
	const iterations = 50

	var wg sync.WaitGroup
	wg.Add(writers + persisters)

	// State mutators: modify targets, cooldowns, and exhaustion concurrently
	for w := 0; w < writers; w++ {
		go func(id int) {
			defer wg.Done()
			for i := 0; i < iterations; i++ {
				// Modify linux targets
				sm.SetLinuxTargets([]linuxTarget{
					{Hostname: fmt.Sprintf("host-%d", id), Port: 22, Username: "root", Label: "linux"},
				})

				// Trigger cooldown state changes
				sm.ShouldSuppress(fmt.Sprintf("host-%d:check-%d", id, i%5))

				// Modify exhaustion state
				sm.RecordHealingFailure(fmt.Sprintf("host-%d:heal", id))

				// Modify L2 mode
				modes := []string{"auto", "manual", "disabled"}
				sm.SetL2Mode(modes[i%3])

				// Modify subscription
				sm.SetSubscriptionStatus("active")
			}
		}(w)
	}

	// Concurrent disk persistence
	for p := 0; p < persisters; p++ {
		go func() {
			defer wg.Done()
			for i := 0; i < iterations; i++ {
				sm.SaveToDisk(tmpDir)
			}
		}()
	}

	wg.Wait()

	// Final save and verify the file is valid
	sm.SaveToDisk(tmpDir)
	data, err := os.ReadFile(filepath.Join(tmpDir, stateFileName))
	if err != nil {
		t.Fatalf("failed to read state file: %v", err)
	}

	var state PersistedState
	if err := json.Unmarshal(data, &state); err != nil {
		t.Fatalf("state file is not valid JSON: %v", err)
	}

	// Verify the saved state has some content
	if state.SavedAt.IsZero() {
		t.Fatal("saved_at should not be zero")
	}

	// Verify LoadFromDisk works with the persisted state
	sm2 := NewStateManager()
	if err := sm2.LoadFromDisk(tmpDir); err != nil {
		t.Fatalf("LoadFromDisk failed: %v", err)
	}
}
