package daemon

// healing_journal.go — Persistent healing journal for crash-safe execution tracking.
//
// Every healing operation (L1, L2, healing orders) is checkpointed to disk before
// execution begins. If the daemon crashes mid-heal, the journal records which
// operations were in-flight and marks them as "crashed" on next startup.
//
// Uses the same atomic write pattern as state.go (tmp + rename).

import (
	"encoding/json"
	"log"
	"os"
	"path/filepath"
	"sync"
	"time"
)

const healingJournalFile = "healing_journal.json"

// HealingStatus represents the state of a healing operation.
type HealingStatus string

const (
	HealingStarted   HealingStatus = "started"
	HealingPhase     HealingStatus = "phase_complete"
	HealingSucceeded HealingStatus = "succeeded"
	HealingFailed    HealingStatus = "failed"
	HealingCrashed   HealingStatus = "crashed"
)

// HealingEntry records a single healing operation's lifecycle.
// Serves as the investigation audit trail — captures the full chain of
// reasoning from detection through resolution for auditor inspection.
type HealingEntry struct {
	ID          string        `json:"id"`
	RunbookID   string        `json:"runbook_id"`
	Hostname    string        `json:"hostname"`
	Platform    string        `json:"platform"`
	CheckType   string        `json:"check_type,omitempty"`
	Level       string        `json:"level"` // L1, L2, order
	Status      HealingStatus `json:"status"`
	Phase       string        `json:"phase,omitempty"`        // Current/last completed phase
	StartedAt   time.Time     `json:"started_at"`
	CompletedAt *time.Time    `json:"completed_at,omitempty"`
	Error       string        `json:"error,omitempty"`
	// Investigation audit trail fields (Blazytko pattern)
	Hypothesis  string  `json:"hypothesis,omitempty"`   // Root cause hypothesis that was validated
	Confidence  float64 `json:"confidence,omitempty"`   // 0-1, from L2 LLM or 1.0 for L1 deterministic
	Reasoning   string  `json:"reasoning,omitempty"`    // Why this action was chosen (L2 LLM reasoning)
}

// HealingJournal persists healing operation state to disk for crash recovery.
type HealingJournal struct {
	mu       sync.Mutex
	entries  map[string]*HealingEntry // key: entry ID
	stateDir string
}

// journalData is the on-disk format.
type journalData struct {
	Entries  []*HealingEntry `json:"entries"`
	SavedAt  time.Time       `json:"saved_at"`
}

// newHealingJournal creates a journal and recovers any interrupted entries from disk.
func newHealingJournal(stateDir string) *HealingJournal {
	j := &HealingJournal{
		entries:  make(map[string]*HealingEntry),
		stateDir: stateDir,
	}
	j.recover()
	return j
}

// StartHealing records that a healing operation has begun.
// Must be called before any execution starts.
func (j *HealingJournal) StartHealing(id, runbookID, hostname, platform, checkType, level string) {
	j.mu.Lock()
	defer j.mu.Unlock()

	j.entries[id] = &HealingEntry{
		ID:        id,
		RunbookID: runbookID,
		Hostname:  hostname,
		Platform:  platform,
		CheckType: checkType,
		Level:     level,
		Status:    HealingStarted,
		StartedAt: time.Now(),
	}
	j.persistLocked()
}

// SetAuditTrail enriches a healing entry with investigation context.
// Called after L2 planning to record the hypothesis, confidence, and reasoning
// that led to the chosen action. L1 entries get confidence=1.0 implicitly.
func (j *HealingJournal) SetAuditTrail(id, hypothesis string, confidence float64, reasoning string) {
	j.mu.Lock()
	defer j.mu.Unlock()

	if e, ok := j.entries[id]; ok {
		e.Hypothesis = hypothesis
		e.Confidence = confidence
		e.Reasoning = reasoning
		j.persistLocked()
	}
}

// CompletePhase records that a phase (detect/remediate/verify) completed.
func (j *HealingJournal) CompletePhase(id, phase string) {
	j.mu.Lock()
	defer j.mu.Unlock()

	if entry, ok := j.entries[id]; ok {
		entry.Phase = phase
		entry.Status = HealingPhase
		j.persistLocked()
	}
}

// FinishHealing records that a healing operation completed (success or failure).
// Removes the entry from active tracking after persisting final state.
func (j *HealingJournal) FinishHealing(id string, success bool, errMsg string) {
	j.mu.Lock()
	defer j.mu.Unlock()

	entry, ok := j.entries[id]
	if !ok {
		return
	}

	now := time.Now()
	entry.CompletedAt = &now
	if success {
		entry.Status = HealingSucceeded
	} else {
		entry.Status = HealingFailed
		entry.Error = errMsg
	}

	j.persistLocked()

	// Remove completed entries after persisting — they've been recorded
	delete(j.entries, id)
	j.persistLocked()
}

// ActiveCount returns the number of in-flight healing operations.
func (j *HealingJournal) ActiveCount() int {
	j.mu.Lock()
	defer j.mu.Unlock()
	return len(j.entries)
}

// recover loads the journal from disk and marks any in-flight entries as crashed.
func (j *HealingJournal) recover() {
	path := filepath.Join(j.stateDir, healingJournalFile)
	data, err := os.ReadFile(path)
	if err != nil {
		if !os.IsNotExist(err) {
			log.Printf("[healing-journal] Failed to read journal: %v", err)
		}
		return
	}

	var jd journalData
	if err := json.Unmarshal(data, &jd); err != nil {
		log.Printf("[healing-journal] Failed to parse journal: %v", err)
		return
	}

	crashed := 0
	for _, entry := range jd.Entries {
		if entry.Status == HealingStarted || entry.Status == HealingPhase {
			// Was in-flight when daemon stopped — mark as crashed
			now := time.Now()
			entry.Status = HealingCrashed
			entry.CompletedAt = &now
			entry.Error = "daemon crashed or restarted during execution"
			crashed++
		}
		// Keep all entries briefly for the final persist, then clear completed
		j.entries[entry.ID] = entry
	}

	if crashed > 0 {
		log.Printf("[healing-journal] Recovered %d crashed healing operations from prior session (saved %s ago)",
			crashed, time.Since(jd.SavedAt).Round(time.Second))
		j.persistLocked()
	}

	// Clear completed/crashed entries — they've been recorded
	for id, entry := range j.entries {
		if entry.Status == HealingCrashed || entry.Status == HealingSucceeded || entry.Status == HealingFailed {
			delete(j.entries, id)
		}
	}
}

// persistLocked writes the journal to disk. Caller must hold j.mu.
func (j *HealingJournal) persistLocked() {
	entries := make([]*HealingEntry, 0, len(j.entries))
	for _, entry := range j.entries {
		entries = append(entries, entry)
	}

	jd := journalData{
		Entries: entries,
		SavedAt: time.Now(),
	}

	data, err := json.MarshalIndent(jd, "", "  ")
	if err != nil {
		log.Printf("[healing-journal] Marshal error: %v", err)
		return
	}

	path := filepath.Join(j.stateDir, healingJournalFile)
	tmpPath := path + ".tmp"
	if err := os.WriteFile(tmpPath, data, 0600); err != nil {
		log.Printf("[healing-journal] Write error: %v", err)
		return
	}
	if err := os.Rename(tmpPath, path); err != nil {
		log.Printf("[healing-journal] Rename error: %v", err)
	}
}
