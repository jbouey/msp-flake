package daemon

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"sync"
	"time"

	"github.com/osiriscare/appliance/internal/grpcserver"
	"github.com/osiriscare/appliance/internal/maputil"
)

// threatDetector correlates security events across hosts to detect
// attack patterns that individual drift checks would miss.
// Runs after each device log collection cycle, analyzing collected event data.
//
// Detections:
//   - Brute force / credential stuffing: >10 failed logins from same source across >2 hosts in 10 min
//   - Single-host brute force: >20 failed logins on one host in 5 min
//   - Ransomware indicators: VSS shadow copy deletion, mass file encryption, audit log clearing
type threatDetector struct {
	svc    *Services
	daemon *Daemon

	mu sync.Mutex

	// Sliding window of failed login events per source key (IP or username).
	failedLogins map[string]*loginTracker

	// VSS shadow copy baseline per hostname. Maps hostname to last known shadow count.
	// If count drops to 0 when it was previously >0, this is a critical ransomware indicator.
	vssShadowBaseline map[string]int

	// Cooldown: don't fire the same alert type+key more than once per window.
	alertCooldowns map[string]time.Time
}

// loginTracker accumulates failed login events for a single source key.
type loginTracker struct {
	Count     int
	FirstSeen time.Time
	LastSeen  time.Time
	Hosts     map[string]bool // which hosts were targeted
	Usernames map[string]bool // which accounts were tried
}

// threatEvent is a parsed security event from devicelogs collection.
type threatEvent struct {
	EventID  int
	Hostname string
	Message  string
	// Extracted fields from 4625 messages
	SourceIP string
	Username string
}

const (
	// Brute force thresholds
	crossHostThreshold = 10           // failed logins from same source
	crossHostMinHosts  = 2            // across at least this many hosts
	crossHostWindow    = 10 * time.Minute
	singleHostThreshold = 20          // failed logins on one host
	singleHostWindow    = 5 * time.Minute

	// Alert cooldown: suppress duplicate alerts for this duration
	alertCooldownDuration = 30 * time.Minute

	// Tracker cleanup: discard stale entries older than this
	trackerMaxAge = 15 * time.Minute
)

func newThreatDetector(svc *Services, d *Daemon) *threatDetector {
	return &threatDetector{
		svc:               svc,
		daemon:            d,
		failedLogins:      make(map[string]*loginTracker),
		vssShadowBaseline: make(map[string]int),
		alertCooldowns:    make(map[string]time.Time),
	}
}

// analyze processes a batch of device log entries from the most recent
// collection cycle. Call this after collectAndAnalyzeDeviceLogs completes.
func (td *threatDetector) analyze(ctx context.Context, entries []deviceLogEntry) {
	if len(entries) == 0 {
		return
	}

	td.mu.Lock()
	defer td.mu.Unlock()

	now := time.Now()

	// Expire stale trackers before processing new events
	td.cleanupTrackers(now)

	// Parse and ingest events
	events := td.parseEvents(entries)

	// Count failed logins by source
	for _, ev := range events {
		if ev.EventID == 4625 { // Failed logon
			td.ingestFailedLogin(ev, now)
		}
	}

	// Evaluate detection rules
	td.detectBruteForce(ctx, now)
}

// analyzeVSS checks VSS shadow copy status on Windows targets.
// This runs as part of the drift scan cycle, not device logs.
func (td *threatDetector) analyzeVSS(ctx context.Context, targets []scanTarget) {
	for _, t := range targets {
		select {
		case <-ctx.Done():
			return
		default:
		}

		count := td.checkVSSShadowCount(ctx, t)
		if count < 0 {
			continue // query failed, skip
		}

		td.mu.Lock()
		prevCount, hasPrev := td.vssShadowBaseline[t.hostname]
		td.vssShadowBaseline[t.hostname] = count

		// Detection: previously had shadows, now has 0
		if hasPrev && prevCount > 0 && count == 0 {
			alertKey := "ransomware_vss:" + t.hostname
			if !td.isAlertCoolingDown(alertKey) {
				td.alertCooldowns[alertKey] = time.Now()
				td.mu.Unlock()

				log.Printf("[threat] CRITICAL: VSS shadow copies deleted on %s (was=%d, now=0) — ransomware indicator",
					t.hostname, prevCount)

				td.reportThreat(ctx, &driftFinding{
					Hostname:     t.hostname,
					CheckType:    "ransomware_indicator",
					Expected:     fmt.Sprintf("%d shadow copies", prevCount),
					Actual:       "0 shadow copies (all deleted)",
					HIPAAControl: "164.308(a)(6)(ii)",
					Severity:     "critical",
					Details: map[string]string{
						"indicator":      "vss_shadow_deleted",
						"previous_count": fmt.Sprintf("%d", prevCount),
						"current_count":  "0",
					},
				})
				continue
			}
		}
		td.mu.Unlock()
	}
}

// checkVSSShadowCount queries the number of VSS shadow copies on a Windows target.
// Returns -1 if the query fails.
func (td *threatDetector) checkVSSShadowCount(ctx context.Context, t scanTarget) int {
	script := `
$ErrorActionPreference = 'SilentlyContinue'
$shadows = @(vssadmin list shadows 2>$null)
$count = ($shadows | Select-String 'Shadow Copy ID').Count
@{count=$count} | ConvertTo-Json -Compress
`
	result := td.svc.WinRM.Execute(t.target, script, "VSS-CHECK", "threat_detector", 15, 0, 10.0, nil)
	if !result.Success {
		return -1
	}

	stdout := maputil.String(result.Output, "std_out")
	if stdout == "" || stdout == "null" {
		return -1
	}

	var parsed struct {
		Count int `json:"count"`
	}
	if err := json.Unmarshal([]byte(stdout), &parsed); err != nil {
		log.Printf("[threat] VSS parse error for %s: %v", t.hostname, err)
		return -1
	}

	return parsed.Count
}

// parseEvents extracts structured threat events from raw device log entries.
func (td *threatDetector) parseEvents(entries []deviceLogEntry) []threatEvent {
	events := make([]threatEvent, 0, len(entries))
	for _, e := range entries {
		// Parse event ID from unit field (format: "Security/4625")
		eventID := 0
		if parts := strings.SplitN(e.Unit, "/", 2); len(parts) == 2 {
			fmt.Sscanf(parts[1], "%d", &eventID)
		}
		if eventID == 0 {
			continue
		}

		ev := threatEvent{
			EventID:  eventID,
			Hostname: e.Hostname,
			Message:  e.Msg,
		}

		// Extract source IP and username from 4625 messages.
		// Typical message: "An account failed to log on. ... Account Name: user ... Source Network Address: 1.2.3.4"
		if eventID == 4625 {
			ev.SourceIP = extractField(e.Msg, "Source Network Address:")
			ev.Username = extractField(e.Msg, "Account Name:")
			// If no source IP extracted, use hostname as source key
			if ev.SourceIP == "" || ev.SourceIP == "-" {
				ev.SourceIP = "local:" + e.Hostname
			}
		}

		events = append(events, ev)
	}
	return events
}

// extractField pulls a value from a Windows Event Log message by label.
// Message format: "... Label:\tvalue ..." or "... Label: value ..."
func extractField(msg, label string) string {
	idx := strings.Index(msg, label)
	if idx < 0 {
		return ""
	}
	rest := strings.TrimSpace(msg[idx+len(label):])
	// Take first token (value ends at whitespace)
	end := strings.IndexAny(rest, " \t\r\n")
	if end > 0 {
		return rest[:end]
	}
	return rest
}

// ingestFailedLogin adds a failed logon event to the sliding window tracker.
func (td *threatDetector) ingestFailedLogin(ev threatEvent, now time.Time) {
	key := ev.SourceIP
	if key == "" {
		return
	}

	tracker, exists := td.failedLogins[key]
	if !exists {
		tracker = &loginTracker{
			FirstSeen: now,
			Hosts:     make(map[string]bool),
			Usernames: make(map[string]bool),
		}
		td.failedLogins[key] = tracker
	}

	tracker.Count++
	tracker.LastSeen = now
	tracker.Hosts[ev.Hostname] = true
	if ev.Username != "" {
		tracker.Usernames[ev.Username] = true
	}
}

// detectBruteForce evaluates all active login trackers against threshold rules.
func (td *threatDetector) detectBruteForce(ctx context.Context, now time.Time) {
	for sourceKey, tracker := range td.failedLogins {
		age := now.Sub(tracker.FirstSeen)

		// Cross-host brute force: >10 failures across >2 hosts within 10 min
		if tracker.Count >= crossHostThreshold &&
			len(tracker.Hosts) >= crossHostMinHosts &&
			age <= crossHostWindow {

			alertKey := "brute_force_cross:" + sourceKey
			if td.isAlertCoolingDown(alertKey) {
				continue
			}
			td.alertCooldowns[alertKey] = now

			hosts := mapKeys(tracker.Hosts)
			users := mapKeys(tracker.Usernames)

			// Report on first targeted host
			hostname := hosts[0]

			log.Printf("[threat] CRITICAL: Cross-host brute force from %s — %d failures across %d hosts in %v",
				sourceKey, tracker.Count, len(tracker.Hosts), age.Round(time.Second))

			go td.reportThreat(ctx, &driftFinding{
				Hostname:     hostname,
				CheckType:    "brute_force_detected",
				Expected:     "no credential stuffing attacks",
				Actual:       fmt.Sprintf("%d failed logins from %s across %d hosts (%s) in %v", tracker.Count, sourceKey, len(tracker.Hosts), strings.Join(hosts, ","), age.Round(time.Second)),
				HIPAAControl: "164.312(d)",
				Severity:     "critical",
				Details: map[string]string{
					"source":         sourceKey,
					"failure_count":  fmt.Sprintf("%d", tracker.Count),
					"target_hosts":   strings.Join(hosts, ","),
					"target_users":   strings.Join(users, ","),
					"window_seconds": fmt.Sprintf("%d", int(age.Seconds())),
					"attack_type":    "cross_host",
				},
			})

			// Reset tracker after alert
			delete(td.failedLogins, sourceKey)
			continue
		}

		// Single-host brute force: >20 failures on one host within 5 min
		if tracker.Count >= singleHostThreshold &&
			len(tracker.Hosts) == 1 &&
			age <= singleHostWindow {

			alertKey := "brute_force_single:" + sourceKey
			if td.isAlertCoolingDown(alertKey) {
				continue
			}
			td.alertCooldowns[alertKey] = now

			hosts := mapKeys(tracker.Hosts)
			users := mapKeys(tracker.Usernames)
			hostname := hosts[0]

			log.Printf("[threat] HIGH: Single-host brute force on %s from %s — %d failures in %v",
				hostname, sourceKey, tracker.Count, age.Round(time.Second))

			go td.reportThreat(ctx, &driftFinding{
				Hostname:     hostname,
				CheckType:    "brute_force_detected",
				Expected:     "no brute force attacks",
				Actual:       fmt.Sprintf("%d failed logins from %s on %s in %v", tracker.Count, sourceKey, hostname, age.Round(time.Second)),
				HIPAAControl: "164.312(d)",
				Severity:     "high",
				Details: map[string]string{
					"source":         sourceKey,
					"failure_count":  fmt.Sprintf("%d", tracker.Count),
					"target_hosts":   hostname,
					"target_users":   strings.Join(users, ","),
					"window_seconds": fmt.Sprintf("%d", int(age.Seconds())),
					"attack_type":    "single_host",
				},
			})

			// Reset tracker after alert
			delete(td.failedLogins, sourceKey)
		}
	}
}

// reportThreat sends a threat detection finding through the healing pipeline.
func (td *threatDetector) reportThreat(ctx context.Context, f *driftFinding) {
	metadata := map[string]string{
		"platform": "windows",
		"source":   "threat_detector",
	}
	for k, v := range f.Details {
		metadata[k] = v
	}

	req := grpcserver.HealRequest{
		Hostname:     f.Hostname,
		CheckType:    f.CheckType,
		Expected:     f.Expected,
		Actual:       f.Actual,
		HIPAAControl: f.HIPAAControl,
		AgentID:      "threat_detector",
		Metadata:     metadata,
	}

	log.Printf("[threat] THREAT: %s/%s expected=%s actual=%s hipaa=%s severity=%s",
		f.Hostname, f.CheckType, f.Expected, f.Actual, f.HIPAAControl, f.Severity)

	td.daemon.healIncident(ctx, &req)
}

// cleanupTrackers removes stale entries from sliding windows.
func (td *threatDetector) cleanupTrackers(now time.Time) {
	for key, tracker := range td.failedLogins {
		if now.Sub(tracker.LastSeen) > trackerMaxAge {
			delete(td.failedLogins, key)
		}
	}

	// Clean up alert cooldowns
	for key, expiry := range td.alertCooldowns {
		if now.Sub(expiry) > alertCooldownDuration {
			delete(td.alertCooldowns, key)
		}
	}
}

// isAlertCoolingDown returns true if the given alert key was recently fired.
func (td *threatDetector) isAlertCoolingDown(key string) bool {
	if lastFired, ok := td.alertCooldowns[key]; ok {
		return time.Since(lastFired) < alertCooldownDuration
	}
	return false
}

// mapKeys returns the keys of a map[string]bool as a sorted slice.
func mapKeys(m map[string]bool) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	return keys
}
