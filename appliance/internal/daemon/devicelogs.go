package daemon

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/osiriscare/appliance/internal/maputil"
	"github.com/osiriscare/appliance/internal/phiscrub"
)

// deviceLogEntry is a security event from a Windows workstation or DC.
type deviceLogEntry struct {
	TS       string `json:"ts"`
	Unit     string `json:"unit"`
	Pri      int    `json:"pri"`
	Msg      string `json:"msg"`
	Hostname string `json:"-"` // used for grouping, not serialized
}

// HIPAA-relevant Windows Security Event IDs.
// These cover: authentication, account management, policy changes, privilege use.
// Intentionally excludes noisy events (e.g., 4688 process creation, 5156 WFP).
const windowsSecurityEventIDs = "" +
	"4624," + // Successful logon
	"4625," + // Failed logon
	"4634," + // Logoff
	"4648," + // Logon with explicit credentials
	"4720," + // User account created
	"4722," + // User account enabled
	"4724," + // Password reset attempt
	"4725," + // User account disabled
	"4726," + // User account deleted
	"4728," + // Member added to security group
	"4732," + // Member added to local group
	"4735," + // Local group changed
	"4740," + // Account lockout
	"4756," + // Member added to universal group
	"4767," + // Account unlocked
	"4768," + // Kerberos TGT requested
	"4771," + // Kerberos pre-auth failed
	"4776," + // NTLM auth attempted
	"4946," + // Firewall rule added
	"4947," + // Firewall rule modified
	"4950," + // Firewall setting changed
	"1102"    // Audit log cleared (critical!)

// maxDeviceLogsPerHost caps events per scan cycle to prevent log floods.
const maxDeviceLogsPerHost = 200

// collectWindowsEventLogs pulls HIPAA-relevant Security events from a Windows
// target via WinRM. Returns structured log entries ready for shipping.
// Only collects events from the last 20 minutes to bound the query.
func (ds *driftScanner) collectWindowsEventLogs(ctx context.Context, t scanTarget) []deviceLogEntry {
	// PowerShell script to pull filtered Security events.
	// Uses Get-WinEvent with a hash table filter for efficiency (server-side filtering).
	// Output is compact JSON array — strips unnecessary fields for bandwidth.
	script := fmt.Sprintf(`
$ErrorActionPreference = 'SilentlyContinue'
$cutoff = (Get-Date).AddMinutes(-20)
$ids = @(%s)
$events = @()
try {
    $raw = Get-WinEvent -FilterHashtable @{
        LogName='Security'
        ID=$ids
        StartTime=$cutoff
    } -MaxEvents %d -EA Stop
    foreach ($e in $raw) {
        $events += @{
            id = $e.Id
            ts = $e.TimeCreated.ToUniversalTime().ToString('o')
            msg = $e.Message.Split([Environment]::NewLine)[0]
        }
    }
} catch {
    if ($_.Exception.Message -notmatch 'No events were found') {
        $events += @{ id=0; ts=(Get-Date).ToUniversalTime().ToString('o'); msg="EventLog query error: $($_.Exception.Message)" }
    }
}
$events | ConvertTo-Json -Compress -Depth 2
`, windowsSecurityEventIDs, maxDeviceLogsPerHost)

	result := ds.svc.WinRM.Execute(t.target, script, "EVENTLOG", "devicelogs", 30, 0, 10.0, nil)
	if !result.Success {
		return nil
	}

	stdout := maputil.String(result.Output, "std_out")
	if stdout == "" || stdout == "null" {
		return nil
	}

	// Parse events — PowerShell returns single object for 1 event, array for multiple
	var rawEvents []struct {
		ID  int    `json:"id"`
		TS  string `json:"ts"`
		Msg string `json:"msg"`
	}
	if err := json.Unmarshal([]byte(stdout), &rawEvents); err != nil {
		// Try as single object
		var single struct {
			ID  int    `json:"id"`
			TS  string `json:"ts"`
			Msg string `json:"msg"`
		}
		if err2 := json.Unmarshal([]byte(stdout), &single); err2 != nil {
			log.Printf("[devicelogs] Parse error for %s: %v", t.hostname, err)
			return nil
		}
		rawEvents = append(rawEvents, single)
	}

	entries := make([]deviceLogEntry, 0, len(rawEvents))
	for _, e := range rawEvents {
		if e.TS == "" {
			continue
		}
		// Map event ID to syslog priority
		pri := eventPriority(e.ID)
		// Truncate message to 512 chars
		msg := e.Msg
		if len(msg) > 512 {
			msg = msg[:512]
		}
		// Strip any embedded newlines/carriage returns
		msg = strings.ReplaceAll(msg, "\r", "")
		msg = strings.ReplaceAll(msg, "\n", " ")

		entries = append(entries, deviceLogEntry{
			TS:       e.TS,
			Unit:     fmt.Sprintf("Security/%d", e.ID),
			Pri:      pri,
			Msg:      msg,
			Hostname: t.hostname,
		})
	}

	if len(entries) > 0 {
		log.Printf("[devicelogs] Collected %d security events from %s", len(entries), t.hostname)
	}
	return entries
}

// eventPriority maps Windows Security Event IDs to syslog priority levels.
// 0=emergency, 1=alert, 2=critical, 3=error, 4=warning, 5=notice, 6=info
func eventPriority(eventID int) int {
	switch eventID {
	case 1102: // Audit log cleared
		return 1 // alert
	case 4625, 4740, 4771: // Failed logon, lockout, kerberos fail
		return 4 // warning
	case 4720, 4722, 4724, 4725, 4726: // Account lifecycle
		return 5 // notice
	case 4728, 4732, 4735, 4756: // Group membership changes
		return 5 // notice
	case 4946, 4947, 4950: // Firewall changes
		return 4 // warning
	default:
		return 6 // info
	}
}

// collectAndAnalyzeDeviceLogs runs after the drift scan to collect
// compliance-relevant Windows Event Logs from scanned targets.
// Events are PHI-scrubbed and archived to Central Command for OCR audit readiness.
// Critical events also generate incidents via the healing pipeline.
//
// Returns all collected entries for cross-host correlation by the threat detector.
func (ds *driftScanner) collectAndAnalyzeDeviceLogs(ctx context.Context, targets []scanTarget) []deviceLogEntry {
	var allEntries []deviceLogEntry

	for _, t := range targets {
		select {
		case <-ctx.Done():
			return allEntries
		default:
		}

		entries := ds.collectWindowsEventLogs(ctx, t)
		if len(entries) == 0 {
			continue
		}

		allEntries = append(allEntries, entries...)

		// Analyze for critical security events that warrant incidents
		for _, e := range entries {
			switch {
			case e.Pri <= 2: // alert or critical (e.g. audit log cleared)
				ds.reportDrift(&driftFinding{
					Hostname:     t.hostname,
					CheckType:    "security_event_critical",
					Expected:     "no critical security events",
					Actual:       fmt.Sprintf("EventID %s: %s", e.Unit, truncate(e.Msg, 200)),
					HIPAAControl: "164.312(b)",
					Severity:     "critical",
				})
			case strings.Contains(e.Unit, "/4625") || strings.Contains(e.Unit, "/4740"):
				// Count failed logons and lockouts — connection health signal
				// Don't create per-event incidents; the drift scan flap detector
				// handles frequency-based escalation.
				// Cross-host correlation is handled by the threat detector.
			}
		}

		// Archive sanitized events to Central Command for WORM storage.
		// Events are PHI-scrubbed before transmission per HIPAA 164.312(e)(1).
		ds.archiveSecurityEvents(ctx, t.hostname, entries)
	}

	return allEntries
}

// archiveSecurityEvents POSTs PHI-scrubbed security events to Central Command
// for WORM archival. Called after event collection — fire and forget pattern.
// All text fields are scrubbed before transmission. Event IDs and timestamps
// are infrastructure data and not scrubbed.
func (ds *driftScanner) archiveSecurityEvents(ctx context.Context, hostname string, entries []deviceLogEntry) {
	cfg := ds.svc.Config
	if cfg.APIEndpoint == "" || cfg.APIKey == "" {
		return
	}

	// Build the archive payload with PHI-scrubbed fields
	type archiveEvent struct {
		EventID    int    `json:"event_id"`
		Timestamp  string `json:"timestamp"`
		Hostname   string `json:"hostname"`
		Message    string `json:"message"`
		SourceHost string `json:"source_host"`
	}

	events := make([]archiveEvent, 0, len(entries))
	for _, e := range entries {
		// Extract numeric event ID from "Security/4625" format
		eventID := 0
		if parts := strings.SplitN(e.Unit, "/", 2); len(parts) == 2 {
			fmt.Sscanf(parts[1], "%d", &eventID)
		}

		events = append(events, archiveEvent{
			EventID:    eventID,
			Timestamp:  e.TS,
			Hostname:   phiscrub.Scrub(hostname),
			Message:    phiscrub.Scrub(e.Msg),
			SourceHost: phiscrub.Scrub(hostname),
		})
	}

	payload := map[string]interface{}{
		"site_id": cfg.SiteID,
		"events":  events,
	}

	body, err := json.Marshal(payload)
	if err != nil {
		log.Printf("[devicelogs] Marshal error for archive: %v", err)
		return
	}

	url := strings.TrimRight(cfg.APIEndpoint, "/") + "/api/security-events/archive"
	archiveCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(archiveCtx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		log.Printf("[devicelogs] Archive request error: %v", err)
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+cfg.APIKey)

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		log.Printf("[devicelogs] Archive POST failed: %v", err)
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusCreated {
		log.Printf("[devicelogs] Archive POST returned %d for %s (%d events)", resp.StatusCode, hostname, len(events))
		return
	}

	log.Printf("[devicelogs] Archived %d security events from %s to Central Command", len(events), hostname)
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}
