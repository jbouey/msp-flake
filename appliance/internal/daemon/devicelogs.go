package daemon

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"strings"

	"github.com/osiriscare/appliance/internal/maputil"
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

	result := ds.daemon.winrmExec.Execute(t.target, script, "EVENTLOG", "devicelogs", 30, 0, 10.0, nil)
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
// Logs stay ON-PREM only — they are NOT shipped to Central Command
// to avoid creating a data liability with auth/access metadata.
// Instead, critical events generate incidents via the healing pipeline.
func (ds *driftScanner) collectAndAnalyzeDeviceLogs(ctx context.Context, targets []scanTarget) {
	for _, t := range targets {
		select {
		case <-ctx.Done():
			return
		default:
		}

		entries := ds.collectWindowsEventLogs(ctx, t)
		if len(entries) == 0 {
			continue
		}

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
				// handles frequency-based escalation
			}
		}
	}
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}
