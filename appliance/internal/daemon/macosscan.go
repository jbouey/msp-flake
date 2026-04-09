package daemon

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/osiriscare/appliance/internal/maputil"
	"github.com/osiriscare/appliance/internal/sshexec"
	"github.com/osiriscare/appliance/internal/winrm"
)

// macosScanScript is a comprehensive bash script that checks macOS security
// controls in a single SSH call. Outputs JSON via python3.
// Compatible with macOS Ventura (13) through Sequoia (15+).
const macosScanScript = `#!/bin/bash
set -o pipefail

# 1. FileVault
filevault_status="unknown"
fv_out=$(sudo fdesetup status 2>/dev/null)
if echo "$fv_out" | grep -q "FileVault is On"; then
    filevault_status="on"
elif echo "$fv_out" | grep -q "FileVault is Off"; then
    filevault_status="off"
fi

# 2. Gatekeeper
gatekeeper_status="unknown"
gk_out=$(spctl --status 2>/dev/null)
if echo "$gk_out" | grep -q "assessments enabled"; then
    gatekeeper_status="enabled"
elif echo "$gk_out" | grep -q "assessments disabled"; then
    gatekeeper_status="disabled"
fi

# 3. SIP (System Integrity Protection)
sip_status="unknown"
sip_out=$(csrutil status 2>/dev/null)
if echo "$sip_out" | grep -q "enabled"; then
    sip_status="enabled"
elif echo "$sip_out" | grep -q "disabled"; then
    sip_status="disabled"
fi

# 4. macOS Firewall
firewall_status="unknown"
fw_out=$(/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate 2>/dev/null)
if echo "$fw_out" | grep -q "enabled"; then
    firewall_status="enabled"
elif echo "$fw_out" | grep -q "disabled"; then
    firewall_status="disabled"
fi

# 5. Auto-updates
auto_update="unknown"
au_val=$(defaults read /Library/Preferences/com.apple.SoftwareUpdate AutomaticCheckEnabled 2>/dev/null)
if [ "$au_val" = "1" ]; then
    auto_update="enabled"
elif [ "$au_val" = "0" ]; then
    auto_update="disabled"
fi

# 6. Screen lock
screen_lock="unknown"
screen_lock_delay=-1
sl_ask=$(defaults read com.apple.screensaver askForPassword 2>/dev/null)
sl_delay=$(defaults read com.apple.screensaver askForPasswordDelay 2>/dev/null)
if [ "$sl_ask" = "1" ]; then
    screen_lock="enabled"
elif [ "$sl_ask" = "0" ]; then
    screen_lock="disabled"
fi
sl_delay=$(echo "$sl_delay" | tr -dc '0-9')
[ -n "$sl_delay" ] && screen_lock_delay=$sl_delay || screen_lock_delay=-1

# 7. Remote Login (SSH)
remote_login="unknown"
rl_out=$(sudo systemsetup -getremotelogin 2>/dev/null)
if echo "$rl_out" | grep -qi "on"; then
    remote_login="on"
elif echo "$rl_out" | grep -qi "off"; then
    remote_login="off"
fi

# 8. File Sharing (SMB)
file_sharing="unknown"
if launchctl list com.apple.smbd >/dev/null 2>&1; then
    file_sharing="on"
else
    file_sharing="off"
fi

# 9. Time Machine — last backup recency + disk accessibility + integrity
tm_status="unknown"
tm_last_epoch=0
tm_disk_accessible="unknown"
tm_integrity="not_tested"
tm_last=$(tmutil latestbackup 2>/dev/null)
if [ -n "$tm_last" ] && [ "$tm_last" != "" ]; then
    # Extract date from backup path (format: /path/YYYY-MM-DD-HHMMSS)
    tm_date=$(echo "$tm_last" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{6}' | tail -1)
    if [ -n "$tm_date" ]; then
        # Convert to epoch for comparison
        tm_formatted=$(echo "$tm_date" | sed 's/\([0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}\)-\([0-9]\{2\}\)\([0-9]\{2\}\)\([0-9]\{2\}\)/\1 \2:\3:\4/')
        tm_last_epoch=$(date -j -f "%Y-%m-%d %H:%M:%S" "$tm_formatted" "+%s" 2>/dev/null || echo 0)
        now_epoch=$(date "+%s")
        if [ "$tm_last_epoch" -gt 0 ] 2>/dev/null; then
            age_days=$(( (now_epoch - tm_last_epoch) / 86400 ))
            if [ "$age_days" -le 7 ]; then
                tm_status="current"
            else
                tm_status="stale_${age_days}d"
            fi
        else
            tm_status="current"
        fi
    else
        tm_status="current"
    fi

    # Check if backup disk is accessible (verify the backup path exists and is readable)
    if [ -d "$tm_last" ]; then
        tm_disk_accessible="yes"
    else
        # Try to check the destination info via tmutil
        tm_dest=$(tmutil destinationinfo 2>/dev/null)
        if echo "$tm_dest" | grep -q "Mount Point"; then
            mount_point=$(echo "$tm_dest" | grep "Mount Point" | head -1 | sed 's/.*: //')
            if [ -d "$mount_point" ]; then
                tm_disk_accessible="yes"
            else
                tm_disk_accessible="unmounted"
            fi
        else
            tm_disk_accessible="no_destination"
        fi
    fi

    # Quick integrity check via tmutil verifychecksum (if available, runs fast on latest only)
    if command -v tmutil >/dev/null 2>&1 && [ "$tm_disk_accessible" = "yes" ]; then
        # verifychecksum is only available on macOS 12.3+, non-blocking quick check
        tm_verify=$(tmutil verifychecksum "$tm_last" 2>&1)
        if [ $? -eq 0 ]; then
            tm_integrity="passed"
        elif echo "$tm_verify" | grep -qi "not recognized\|unknown\|invalid"; then
            tm_integrity="not_available"
        else
            tm_integrity="failed"
        fi
    fi
else
    # No backup found
    tm_status="no_backup"
    # Still check if a TM destination is configured
    tm_dest=$(tmutil destinationinfo 2>/dev/null)
    if echo "$tm_dest" | grep -q "Mount Point"; then
        tm_disk_accessible="yes_but_no_backup"
    else
        tm_disk_accessible="no_destination"
    fi
fi

# 10. NTP
ntp_status="unknown"
ntp_out=$(sudo systemsetup -getusingnetworktime 2>/dev/null)
if echo "$ntp_out" | grep -qi "on"; then
    ntp_status="synced"
elif echo "$ntp_out" | grep -qi "off"; then
    ntp_status="not_synced"
fi

# 11. Open ports (external listeners)
open_port_count=0
open_ports_list=""
if command -v lsof >/dev/null 2>&1; then
    open_ports_list=$(lsof -i -P -n 2>/dev/null | grep LISTEN | grep -v "127\.0\.0\.1\|::1\|\*:.*" | awk '{print $9}' | sed 's/.*://' | sort -un | tr '\n' ',' | sed 's/,$//')
    if [ -n "$open_ports_list" ]; then
        open_port_count=$(echo "$open_ports_list" | tr ',' '\n' | grep -c . 2>/dev/null || echo 0)
    fi
fi
[ -z "$open_ports_list" ] && open_ports_list="none"
open_port_count=$(echo "$open_port_count" | tr -dc '0-9')
[ -z "$open_port_count" ] && open_port_count=0

# 12. Admin users
admin_users=""
admin_count=0
admin_out=$(dscl . -read /Groups/admin GroupMembership 2>/dev/null | sed 's/GroupMembership: //')
if [ -n "$admin_out" ]; then
    admin_users="$admin_out"
    admin_count=$(echo "$admin_out" | tr ' ' '\n' | grep -c . 2>/dev/null || echo 0)
fi
admin_count=$(echo "$admin_count" | tr -dc '0-9')
[ -z "$admin_count" ] && admin_count=0

# 13. Disk space
disk_pct=0
disk_warning="ok"
root_pct=$(df -h / 2>/dev/null | tail -1 | awk '{print $5}' | tr -d '%')
root_pct=$(echo "$root_pct" | tr -dc '0-9')
[ -z "$root_pct" ] && root_pct=0
if [ "$root_pct" -gt 90 ] 2>/dev/null; then
    disk_pct=$root_pct
    disk_warning="/:${root_pct}%"
fi

# 14. Certificate expiry — check common locations
cert_issues="ok"
for cert in /etc/ssl/cert.pem /etc/ssl/certs/server.crt /var/lib/msp/ca/ca.pem; do
    if [ -f "$cert" ]; then
        if ! openssl x509 -checkend 2592000 -noout -in "$cert" 2>/dev/null; then
            expiry=$(openssl x509 -enddate -noout -in "$cert" 2>/dev/null | cut -d= -f2)
            if [ "$cert_issues" = "ok" ]; then
                cert_issues="${cert}:${expiry}"
            else
                cert_issues="${cert_issues},${cert}:${expiry}"
            fi
        fi
    fi
done

# Sanitize numerics
open_port_count=$(echo "$open_port_count" | head -1 | tr -dc '0-9'); [ -z "$open_port_count" ] && open_port_count=0
admin_count=$(echo "$admin_count" | head -1 | tr -dc '0-9'); [ -z "$admin_count" ] && admin_count=0
disk_pct=$(echo "$disk_pct" | head -1 | tr -dc '0-9'); [ -z "$disk_pct" ] && disk_pct=0
screen_lock_delay=$(echo "$screen_lock_delay" | head -1 | tr -dc '0-9-'); [ -z "$screen_lock_delay" ] && screen_lock_delay=-1

python3 -c "
import json
print(json.dumps({
    'filevault': '$filevault_status',
    'gatekeeper': '$gatekeeper_status',
    'sip': '$sip_status',
    'firewall': '$firewall_status',
    'auto_update': '$auto_update',
    'screen_lock': '$screen_lock',
    'screen_lock_delay': $screen_lock_delay,
    'remote_login': '$remote_login',
    'file_sharing': '$file_sharing',
    'time_machine': '$tm_status',
    'tm_disk_accessible': '$tm_disk_accessible',
    'tm_integrity': '$tm_integrity',
    'ntp': '$ntp_status',
    'open_ports': '$open_ports_list',
    'open_port_count': $open_port_count,
    'admin_users': '$admin_users',
    'admin_count': $admin_count,
    'disk': {'warning': '$disk_warning', 'max_pct': $disk_pct},
    'cert_expiry': '$cert_issues'
}))
"
`

// macosScanState is the parsed output of the macOS scan script.
type macosScanState struct {
	FileVault       string `json:"filevault"`
	Gatekeeper      string `json:"gatekeeper"`
	SIP             string `json:"sip"`
	Firewall        string `json:"firewall"`
	AutoUpdate      string `json:"auto_update"`
	ScreenLock      string `json:"screen_lock"`
	ScreenLockDelay int    `json:"screen_lock_delay"`
	RemoteLogin     string `json:"remote_login"`
	FileSharing     string `json:"file_sharing"`
	TimeMachine       string `json:"time_machine"`
	TMDiskAccessible  string `json:"tm_disk_accessible"`
	TMIntegrity       string `json:"tm_integrity"`
	NTP               string `json:"ntp"`
	OpenPorts       string `json:"open_ports"`
	OpenPortCount   int    `json:"open_port_count"`
	AdminUsers      string `json:"admin_users"`
	AdminCount      int    `json:"admin_count"`
	Disk            struct {
		Warning string `json:"warning"`
		MaxPct  int    `json:"max_pct"`
	} `json:"disk"`
	CertExpiry string `json:"cert_expiry"`
}

// sendWakeOnLAN sends a Wake-on-LAN magic packet to the broadcast address
// for the given MAC. Used to wake sleeping macOS targets before SSH scan.
func sendWakeOnLAN(macAddr string) error {
	mac, err := net.ParseMAC(macAddr)
	if err != nil {
		return fmt.Errorf("parse MAC %q: %w", macAddr, err)
	}

	// Magic packet: 6 bytes of 0xFF + MAC repeated 16 times
	var packet [102]byte
	for i := 0; i < 6; i++ {
		packet[i] = 0xFF
	}
	for i := 0; i < 16; i++ {
		copy(packet[6+i*6:], mac)
	}

	conn, err := net.DialUDP("udp4", nil, &net.UDPAddr{IP: net.IPv4bcast, Port: 9})
	if err != nil {
		return fmt.Errorf("dial UDP broadcast: %w", err)
	}
	defer conn.Close()

	_, err = conn.Write(packet[:])
	return err
}

// resolveMAC looks up the MAC address for an IP from /proc/net/arp.
func resolveMAC(ip string) string {
	data, err := os.ReadFile("/proc/net/arp")
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 4 && fields[0] == ip && fields[3] != "00:00:00:00:00:00" {
			return fields[3]
		}
	}
	return ""
}

// scanMacOSRemote scans a remote macOS target via SSH.
// Sends Wake-on-LAN if SSH probe fails (macOS may be sleeping).
func (ds *driftScanner) scanMacOSRemote(ctx context.Context, target *sshexec.Target, label string) []driftFinding {
	// Per-target deadline: WoL probe (5s) + sleep (20s) + SSH Execute (60s + retry 15s + 60s) = ~160s.
	// Use 4 minutes as a hard upper bound.
	targetCtx, targetCancel := context.WithTimeout(ctx, 4*time.Minute)
	defer targetCancel()

	// Quick TCP probe — if SSH port is unreachable, try WoL to wake the Mac
	probeCtx, probeCancel := context.WithTimeout(targetCtx, 5*time.Second)
	defer probeCancel()
	probeConn, probeErr := (&net.Dialer{}).DialContext(probeCtx, "tcp", fmt.Sprintf("%s:%d", target.Hostname, target.Port))
	if probeErr != nil {
		mac := resolveMAC(target.Hostname)
		if mac != "" {
			slog.Info("SSH probe failed, sending Wake-on-LAN", "component", "macosscan", "hostname", target.Hostname, "mac", mac)
			if err := sendWakeOnLAN(mac); err != nil {
				slog.Error("WoL failed", "component", "macosscan", "error", err)
			} else {
				// Wait for Mac to wake up (typically 10-15s).
				// Use select so we bail early if the scan context is cancelled.
				select {
				case <-targetCtx.Done():
					return nil
				case <-time.After(20 * time.Second):
				}
			}
		} else {
			slog.Warn("SSH probe failed, no MAC found for WoL", "component", "macosscan", "hostname", target.Hostname)
		}
	} else {
		probeConn.Close()
	}

	result := ds.svc.SSH.Execute(
		targetCtx, target, macosScanScript,
		"MACOS-DRIFT-SCAN", "driftscan",
		60, 1, 15.0, true, nil,
	)

	if !result.Success {
		stderr := maputil.String(result.Output, "stderr")
		slog.Error("remote scan failed", "component", "macosscan", "hostname", target.Hostname, "label", label, "error", result.Error, "exit_code", result.ExitCode, "stderr", stderr)
		// Report unreachable so it surfaces as an incident in the dashboard.
		// Cooldown in healIncident prevents spam for the same host.
		errMsg := result.Error
		if errMsg == "" && stderr != "" {
			errMsg = stderr
		}
		if errMsg == "" {
			errMsg = fmt.Sprintf("SSH scan failed (exit code %d)", result.ExitCode)
		}
		st := scanTarget{
			hostname: target.Hostname,
			label:    label,
			target:   &winrm.Target{Hostname: target.Hostname, Port: target.Port},
		}
		return ds.unreachableFinding(st, "macos", errMsg)
	}

	stdout := maputil.String(result.Output, "stdout")
	if stdout == "" {
		return nil
	}

	return ds.parseMacOSFindings(stdout, target.Hostname)
}

// parseMacOSFindings parses the JSON output from the macOS scan script
// and converts anomalies into drift findings.
func (ds *driftScanner) parseMacOSFindings(output, hostname string) []driftFinding {
	// Find the JSON in the output (skip any non-JSON lines)
	jsonStart := strings.Index(output, "{")
	if jsonStart < 0 {
		slog.Warn("no JSON in output", "component", "macosscan", "hostname", hostname)
		return nil
	}
	output = output[jsonStart:]

	var state macosScanState
	if err := json.Unmarshal([]byte(output), &state); err != nil {
		slog.Error("parse error", "component", "macosscan", "hostname", hostname, "error", err, "raw_prefix", output[:min(200, len(output))])
		return nil
	}

	var findings []driftFinding

	// 1. FileVault — full disk encryption required for HIPAA ePHI at rest
	if state.FileVault == "off" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "macos_filevault",
			Expected:     "on",
			Actual:       "off",
			HIPAAControl: "164.312(a)(2)(iv)",
			Severity:     "critical",
		})
	}

	// 2. Gatekeeper — code signing enforcement
	if state.Gatekeeper == "disabled" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "macos_gatekeeper",
			Expected:     "enabled",
			Actual:       "disabled",
			HIPAAControl: "164.308(a)(5)(ii)(A)",
			Severity:     "high",
		})
	}

	// 3. SIP — System Integrity Protection
	if state.SIP == "disabled" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "macos_sip",
			Expected:     "enabled",
			Actual:       "disabled",
			HIPAAControl: "164.312(a)(1)",
			Severity:     "critical",
		})
	}

	// 4. macOS Firewall
	if state.Firewall == "disabled" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "macos_firewall",
			Expected:     "enabled",
			Actual:       "disabled",
			HIPAAControl: "164.312(e)(1)",
			Severity:     "high",
		})
	}

	// 5. Auto-updates
	if state.AutoUpdate == "disabled" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "macos_auto_update",
			Expected:     "enabled",
			Actual:       "disabled",
			HIPAAControl: "164.308(a)(5)(ii)(A)",
			Severity:     "medium",
		})
	}

	// 6. Screen lock — must be enabled with reasonable delay
	if state.ScreenLock == "disabled" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "macos_screen_lock",
			Expected:     "enabled",
			Actual:       "disabled",
			HIPAAControl: "164.310(b)",
			Severity:     "medium",
		})
	} else if state.ScreenLock == "enabled" && state.ScreenLockDelay > 5 {
		// Password delay >5 seconds is a concern (HIPAA workstation security)
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "macos_screen_lock",
			Expected:     "delay<=5s",
			Actual:       fmt.Sprintf("delay=%ds", state.ScreenLockDelay),
			HIPAAControl: "164.310(b)",
			Severity:     "low",
		})
	}

	// 7. Remote Login (SSH) — skipped: SSH is required for management scanning.
	//    Flagging it would create an incident every scan cycle for a deliberate
	//    management dependency. Tracked as informational in scan output only.

	// 8. File Sharing (SMB)
	if state.FileSharing == "on" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "macos_file_sharing",
			Expected:     "off",
			Actual:       "on",
			Severity:     "medium",
		})
	}

	// 9. Time Machine — stale backup (>7 days), disk accessibility, integrity
	if state.TimeMachine == "no_backup" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "macos_time_machine",
			Expected:     "recent_backup",
			Actual:       "no_backup",
			HIPAAControl: "164.308(a)(7)(ii)(A)",
			Severity:     "medium",
			Details: map[string]string{
				"disk_accessible": state.TMDiskAccessible,
				"integrity":       state.TMIntegrity,
			},
		})
	} else if strings.HasPrefix(state.TimeMachine, "stale_") {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "macos_time_machine",
			Expected:     "backup_within_7d",
			Actual:       state.TimeMachine,
			HIPAAControl: "164.308(a)(7)(ii)(A)",
			Severity:     "medium",
			Details: map[string]string{
				"disk_accessible": state.TMDiskAccessible,
				"integrity":       state.TMIntegrity,
			},
		})
	}

	// 9b. Time Machine backup disk not accessible
	if state.TMDiskAccessible == "unmounted" || state.TMDiskAccessible == "no_destination" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "macos_time_machine",
			Expected:     "backup_disk_accessible",
			Actual:       fmt.Sprintf("disk_%s", state.TMDiskAccessible),
			HIPAAControl: "164.308(a)(7)(ii)(A)",
			Severity:     "high",
			Details: map[string]string{
				"disk_accessible": state.TMDiskAccessible,
			},
		})
	}

	// 9c. Time Machine integrity check failed
	if state.TMIntegrity == "failed" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "macos_time_machine",
			Expected:     "integrity_passed",
			Actual:       "integrity_failed",
			HIPAAControl: "164.308(a)(7)(ii)(A)",
			Severity:     "high",
			Details: map[string]string{
				"integrity": state.TMIntegrity,
			},
		})
	}

	// 10. NTP not synced
	if state.NTP == "not_synced" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "macos_ntp_sync",
			Expected:     "synced",
			Actual:       "not_synced",
			Severity:     "low",
		})
	}

	// 11. Too many admin users (>3 is suspicious)
	if state.AdminCount > 3 {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "macos_admin_users",
			Expected:     "<=3",
			Actual:       fmt.Sprintf("%d: %s", state.AdminCount, state.AdminUsers),
			HIPAAControl: "164.312(a)(1)",
			Severity:     "high",
			Details:      map[string]string{"count": strconv.Itoa(state.AdminCount), "users": state.AdminUsers},
		})
	}

	// 12. Disk space >90%
	if state.Disk.Warning != "ok" && state.Disk.MaxPct > 90 {
		severity := "medium"
		if state.Disk.MaxPct > 95 {
			severity = "high"
		}
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "macos_disk_space",
			Expected:     "<90%",
			Actual:       state.Disk.Warning,
			Severity:     severity,
		})
	}

	// 13. Certificate expiry
	if state.CertExpiry != "ok" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "macos_cert_expiry",
			Expected:     "valid",
			Actual:       state.CertExpiry,
			HIPAAControl: "164.312(e)(2)(ii)",
			Severity:     "high",
		})
	}

	// Filter out disabled checks
	if len(findings) > 0 {
		filtered := findings[:0]
		for _, f := range findings {
			if !ds.isCheckDisabled(f.CheckType) {
				filtered = append(filtered, f)
			}
		}
		findings = filtered
	}

	if len(findings) > 0 {
		slog.Info("drift findings for target", "component", "macosscan", "hostname", hostname, "count", len(findings))
	}

	return findings
}

// reportMacOSDrift sends a macOS drift finding through the L1→L2→L3 healing pipeline.
func (ds *driftScanner) reportMacOSDrift(f *driftFinding) {
	reportDriftGeneric(ds.daemon, f, "macos", "macosscan", nil)
}
