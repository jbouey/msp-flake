package l2planner

// telemetry_queue.go — File-backed retry queue for telemetry reports.
//
// When a telemetry POST fails (network error, server 5xx), the payload is
// persisted to a queue directory. On the next drain cycle (triggered by the
// daemon after each successful checkin), queued entries are retried.
//
// Queue entries are individual JSON files in {StateDir}/telemetry_queue/.
// Files older than 24h are discarded (stale telemetry isn't worth retrying).

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const (
	telemetryQueueDir = "telemetry_queue"
	maxQueueAge       = 24 * time.Hour
	maxQueueEntries   = 200
)

// EnableQueue sets the state directory for file-backed retry queue.
// Must be called before any telemetry is reported.
func (r *TelemetryReporter) EnableQueue(stateDir string) {
	r.queueDir = filepath.Join(stateDir, telemetryQueueDir)
	if err := os.MkdirAll(r.queueDir, 0700); err != nil {
		log.Printf("[telemetry] Failed to create queue dir: %v", err)
		r.queueDir = ""
	}
}

// enqueue persists a failed telemetry payload for later retry.
func (r *TelemetryReporter) enqueue(body []byte) {
	if r.queueDir == "" {
		return
	}

	name := fmt.Sprintf("telem_%d.json", time.Now().UnixNano())
	path := filepath.Join(r.queueDir, name)
	if err := os.WriteFile(path, body, 0600); err != nil {
		log.Printf("[telemetry] Queue write failed: %v", err)
	}
}

// DrainQueue retries all queued telemetry entries. Called by the daemon
// after each successful checkin (network is known-good at that point).
func (r *TelemetryReporter) DrainQueue() {
	if r.queueDir == "" {
		return
	}

	entries, err := os.ReadDir(r.queueDir)
	if err != nil {
		if !os.IsNotExist(err) {
			log.Printf("[telemetry] Queue read failed: %v", err)
		}
		return
	}

	if len(entries) == 0 {
		return
	}

	now := time.Now()
	sent, dropped, failed := 0, 0, 0

	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}

		path := filepath.Join(r.queueDir, entry.Name())

		// Drop stale entries
		info, err := entry.Info()
		if err != nil || now.Sub(info.ModTime()) > maxQueueAge {
			os.Remove(path)
			dropped++
			continue
		}

		body, err := os.ReadFile(path)
		if err != nil {
			os.Remove(path)
			dropped++
			continue
		}

		// Attempt to send
		if r.postTelemetry(body) {
			os.Remove(path)
			sent++
		} else {
			failed++
			// Stop retrying on first failure — network probably down
			break
		}
	}

	if sent > 0 || dropped > 0 {
		log.Printf("[telemetry] Queue drain: sent=%d dropped=%d remaining=%d", sent, dropped, failed)
	}

	// Enforce max queue size — drop oldest if over limit
	r.pruneQueue()
}

// postTelemetry sends a raw JSON body to the executions endpoint. Returns true on success.
func (r *TelemetryReporter) postTelemetry(body []byte) bool {
	url := fmt.Sprintf("%s/api/agent/executions", r.endpoint)
	req, err := http.NewRequestWithContext(context.Background(), http.MethodPost, url, strings.NewReader(string(body)))
	if err != nil {
		return false
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+r.creds.APIKey())

	resp, err := r.client.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()

	return resp.StatusCode == http.StatusOK || resp.StatusCode == http.StatusCreated
}

// pruneQueue removes excess queue files, keeping only the newest maxQueueEntries.
func (r *TelemetryReporter) pruneQueue() {
	entries, err := os.ReadDir(r.queueDir)
	if err != nil {
		return
	}

	// Filter to .json files
	var jsonFiles []os.DirEntry
	for _, e := range entries {
		if !e.IsDir() && strings.HasSuffix(e.Name(), ".json") {
			jsonFiles = append(jsonFiles, e)
		}
	}

	if len(jsonFiles) <= maxQueueEntries {
		return
	}

	// DirEntry names are timestamp-based, so sorted alphabetically = oldest first
	excess := len(jsonFiles) - maxQueueEntries
	for i := 0; i < excess; i++ {
		os.Remove(filepath.Join(r.queueDir, jsonFiles[i].Name()))
	}
	log.Printf("[telemetry] Pruned %d stale queue entries", excess)
}

// queuedPayload wraps the marshal+enqueue for the common case.
// Called when a POST fails so the payload can be retried later.
func (r *TelemetryReporter) queuePayload(payload interface{}) {
	body, err := json.Marshal(payload)
	if err != nil {
		return
	}
	r.enqueue(body)
}
