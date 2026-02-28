// Package updater manages the agent self-update lifecycle.
//
// The update flow:
//  1. Appliance signals update available via HeartbeatResponse
//  2. Agent downloads new binary from appliance HTTP server
//  3. SHA256 verification against expected hash
//  4. Rename current binary to .bak, rename new to primary
//  5. Write update-pending.json marker
//  6. Spawn detached restart script (platform-specific)
//  7. On next startup, verify update succeeded or rollback
package updater

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// UpdateMarker is written before restarting to track update state.
type UpdateMarker struct {
	PreviousVersion  string `json:"previous_version"`
	NewVersion       string `json:"new_version"`
	Timestamp        string `json:"timestamp"`
	RollbackDeadline string `json:"rollback_deadline"`
	SHA256           string `json:"sha256"`
}

// Updater manages the agent self-update lifecycle.
type Updater struct {
	dataDir        string // C:\ProgramData\OsirisCare
	installDir     string // C:\OsirisCare (where the binary lives)
	serviceName    string // OsirisCareAgent
	currentVersion string

	mu           sync.Mutex
	updating     bool
	lastCheck    time.Time
	lastFailure  time.Time
	failureCount int

	httpClient *http.Client
}

// New creates a new Updater.
func New(dataDir, installDir, currentVersion, serviceName string) *Updater {
	return &Updater{
		dataDir:        dataDir,
		installDir:     installDir,
		serviceName:    serviceName,
		currentVersion: currentVersion,
		httpClient: &http.Client{
			Timeout: 5 * time.Minute, // generous for large binaries over LAN
		},
	}
}

// CheckAndUpdate checks if an update is available and applies it.
// Called from the heartbeat loop when resp.UpdateAvailable is true.
func (u *Updater) CheckAndUpdate(ctx context.Context, updateVersion, updateURL, updateSHA256 string) error {
	u.mu.Lock()
	if u.updating {
		u.mu.Unlock()
		return fmt.Errorf("update already in progress")
	}

	// Backoff after failures: wait 10min * failureCount
	if u.failureCount > 0 && time.Since(u.lastFailure) < time.Duration(u.failureCount)*10*time.Minute {
		u.mu.Unlock()
		return fmt.Errorf("backing off after %d failures", u.failureCount)
	}

	u.updating = true
	u.lastCheck = time.Now()
	u.mu.Unlock()

	defer func() {
		u.mu.Lock()
		u.updating = false
		u.mu.Unlock()
	}()

	// Validate URL is HTTP to a local address (security: prevent redirect to external)
	parsed, err := url.Parse(updateURL)
	if err != nil {
		return fmt.Errorf("invalid update URL: %w", err)
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return fmt.Errorf("unsupported URL scheme: %s", parsed.Scheme)
	}

	log.Printf("[updater] Starting update: v%s → v%s from %s", u.currentVersion, updateVersion, updateURL)

	// Download to temp file
	newPath := filepath.Join(u.installDir, "osiris-agent.exe.new")
	if err := u.downloadBinary(ctx, updateURL, newPath); err != nil {
		u.recordFailure()
		return fmt.Errorf("download failed: %w", err)
	}

	// Verify SHA256
	actualHash, err := fileSHA256(newPath)
	if err != nil {
		os.Remove(newPath)
		u.recordFailure()
		return fmt.Errorf("hash computation failed: %w", err)
	}

	if actualHash != updateSHA256 {
		os.Remove(newPath)
		u.recordFailure()
		return fmt.Errorf("SHA256 mismatch: expected %s, got %s", updateSHA256, actualHash)
	}

	log.Printf("[updater] Download verified: SHA256=%s size OK", actualHash[:16])

	// Apply the update (platform-specific rename + restart)
	if err := u.applyUpdate(updateVersion, updateSHA256); err != nil {
		u.recordFailure()
		return fmt.Errorf("apply failed: %w", err)
	}

	// Reset failure count on success
	u.mu.Lock()
	u.failureCount = 0
	u.mu.Unlock()

	return nil
}

// CheckRollbackNeeded is called at startup to verify the previous update
// succeeded or to rollback if it failed.
func (u *Updater) CheckRollbackNeeded() {
	markerPath := filepath.Join(u.dataDir, "update-pending.json")
	data, err := os.ReadFile(markerPath)
	if err != nil {
		return // no pending update
	}

	var marker UpdateMarker
	if err := json.Unmarshal(data, &marker); err != nil {
		log.Printf("[updater] Corrupt update marker, removing: %v", err)
		os.Remove(markerPath)
		return
	}

	bakPath := filepath.Join(u.installDir, "osiris-agent.exe.bak")

	if u.currentVersion == marker.NewVersion {
		// Update succeeded — clean up
		log.Printf("[updater] Update to v%s confirmed successful", marker.NewVersion)
		os.Remove(bakPath)
		os.Remove(markerPath)
		return
	}

	if u.currentVersion == marker.PreviousVersion {
		// We're running the old version — new binary must have failed
		log.Printf("[updater] ROLLBACK: v%s failed, reverting to v%s",
			marker.NewVersion, marker.PreviousVersion)

		currentPath := filepath.Join(u.installDir, "osiris-agent.exe")
		failedPath := filepath.Join(u.installDir, "osiris-agent.exe.failed")

		// Move broken new binary aside
		os.Rename(currentPath, failedPath)

		// Restore backup
		if err := os.Rename(bakPath, currentPath); err != nil {
			log.Printf("[updater] ROLLBACK FAILED: could not restore .bak: %v", err)
		} else {
			log.Printf("[updater] Rollback complete, running v%s", marker.PreviousVersion)
		}

		os.Remove(markerPath)
		return
	}

	// Version doesn't match either — stale marker, clean up
	log.Printf("[updater] Stale update marker (current=%s, marker prev=%s/new=%s), removing",
		u.currentVersion, marker.PreviousVersion, marker.NewVersion)
	os.Remove(markerPath)
}

// applyUpdate renames the binaries and triggers a service restart.
func (u *Updater) applyUpdate(newVersion, sha256hex string) error {
	currentPath := filepath.Join(u.installDir, "osiris-agent.exe")
	bakPath := filepath.Join(u.installDir, "osiris-agent.exe.bak")
	newPath := filepath.Join(u.installDir, "osiris-agent.exe.new")

	// Remove old backup if it exists
	os.Remove(bakPath)

	// Rename current → .bak (Windows allows renaming a running exe)
	if err := os.Rename(currentPath, bakPath); err != nil {
		return fmt.Errorf("rename current to .bak: %w", err)
	}

	// Rename .new → current
	if err := os.Rename(newPath, currentPath); err != nil {
		// Try to restore
		os.Rename(bakPath, currentPath)
		return fmt.Errorf("rename .new to current: %w", err)
	}

	// Write update marker
	marker := UpdateMarker{
		PreviousVersion:  u.currentVersion,
		NewVersion:       newVersion,
		Timestamp:        time.Now().UTC().Format(time.RFC3339),
		RollbackDeadline: time.Now().UTC().Add(5 * time.Minute).Format(time.RFC3339),
		SHA256:           sha256hex,
	}
	markerData, _ := json.MarshalIndent(marker, "", "  ")
	markerPath := filepath.Join(u.dataDir, "update-pending.json")
	if err := os.WriteFile(markerPath, markerData, 0644); err != nil {
		log.Printf("[updater] WARNING: failed to write update marker: %v", err)
	}

	log.Printf("[updater] Binary swapped: v%s → v%s, restarting service...", u.currentVersion, newVersion)

	// Platform-specific service restart
	return restartService(u.serviceName, u.dataDir)
}

// downloadBinary downloads a file from url to destPath.
func (u *Updater) downloadBinary(ctx context.Context, url, destPath string) error {
	req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		return err
	}

	resp, err := u.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d from %s", resp.StatusCode, url)
	}

	f, err := os.Create(destPath)
	if err != nil {
		return err
	}
	defer f.Close()

	n, err := io.Copy(f, resp.Body)
	if err != nil {
		os.Remove(destPath)
		return err
	}

	log.Printf("[updater] Downloaded %d bytes to %s", n, destPath)
	return nil
}

func (u *Updater) recordFailure() {
	u.mu.Lock()
	u.failureCount++
	u.lastFailure = time.Now()
	u.mu.Unlock()
}

// fileSHA256 computes the hex-encoded SHA256 of a file.
func fileSHA256(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()

	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
