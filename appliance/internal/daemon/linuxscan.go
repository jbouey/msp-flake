package daemon

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"strconv"
	"strings"
	"time"

	"github.com/osiriscare/appliance/internal/evidence"
	"github.com/osiriscare/appliance/internal/maputil"
	"github.com/osiriscare/appliance/internal/sshexec"
	"github.com/osiriscare/appliance/internal/winrm"
)

// linuxScanScript is a comprehensive bash script that checks all Linux security
// controls in a single SSH call. Outputs JSON to minimize round-trips.
const linuxScanScript = `#!/bin/bash
set -o pipefail
result='{}'

add_json() { result=$(echo "$result" | python3 -c "
import sys,json
d=json.load(sys.stdin)
d['$1']=$2
print(json.dumps(d))
" 2>/dev/null || echo "$result"); }

# Helper: safe JSON via python3 (available on NixOS)
to_json() { python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))" 2>/dev/null; }

# 1. Firewall — check nftables/iptables/ufw rules exist
fw_rules=0
if command -v ufw >/dev/null 2>&1; then
    ufw_out=$(ufw status 2>/dev/null)
    if echo "$ufw_out" | grep -q "Status: active"; then
        fw_rules=$(echo "$ufw_out" | grep -c "ALLOW\|DENY\|REJECT\|LIMIT" || true)
    fi
fi
if [ "$fw_rules" -eq 0 ] 2>/dev/null && command -v nft >/dev/null 2>&1; then
    fw_rules=$(nft list ruleset 2>/dev/null | grep -c "rule" || true)
fi
if [ "$fw_rules" -eq 0 ] 2>/dev/null && command -v iptables >/dev/null 2>&1; then
    fw_rules=$(iptables -L -n 2>/dev/null | grep -c -v "^Chain\|^target\|^$" || true)
fi
fw_rules=$(echo "$fw_rules" | head -1 | tr -dc '0-9')
[ -z "$fw_rules" ] && fw_rules=0
fw_status="active"
[ "$fw_rules" -eq 0 ] && fw_status="no_rules"

# 2. SSH config hardening
ssh_root="unknown"
ssh_passauth="unknown"
ssh_port="22"
if [ -f /etc/ssh/sshd_config ]; then
    ssh_root=$(grep -i "^PermitRootLogin" /etc/ssh/sshd_config 2>/dev/null | awk '{print $2}' | head -1)
    ssh_passauth=$(grep -i "^PasswordAuthentication" /etc/ssh/sshd_config 2>/dev/null | awk '{print $2}' | head -1)
    ssh_port=$(grep -i "^Port " /etc/ssh/sshd_config 2>/dev/null | awk '{print $2}' | head -1)
    [ -z "$ssh_root" ] && ssh_root="prohibit-password"
    [ -z "$ssh_passauth" ] && ssh_passauth="yes"
    [ -z "$ssh_port" ] && ssh_port="22"
fi

# 3. Failed systemd services
failed_svcs=$(systemctl --failed --no-legend --no-pager 2>/dev/null | awk '{print $1}' | tr '\n' ',' | sed 's/,$//')
failed_count=$(echo "$failed_svcs" | tr ',' '\n' | grep -c . 2>/dev/null || echo 0)
failed_count=$(echo "$failed_count" | head -1 | tr -dc '0-9')
[ -z "$failed_count" ] && failed_count=0
[ -z "$failed_svcs" ] && { failed_svcs="none"; failed_count=0; }

# 4. Disk space — check if any mount is >90% full
disk_warning=""
disk_pct=0
while IFS= read -r line; do
    pct=$(echo "$line" | awk '{print $5}' | tr -d '%')
    mount=$(echo "$line" | awk '{print $6}')
    if [ "$pct" -gt 90 ] 2>/dev/null; then
        disk_warning="${disk_warning}${mount}:${pct}%,"
        [ "$pct" -gt "$disk_pct" ] && disk_pct=$pct
    fi
done < <(df -h 2>/dev/null | grep '^/' | grep -v 'tmpfs\|devtmpfs')
disk_warning=${disk_warning%,}
[ -z "$disk_warning" ] && disk_warning="ok"

# 5. SUID binaries — find unexpected setuid files
known_suid="/usr/bin/sudo /usr/bin/passwd /usr/bin/chsh /usr/bin/chfn /usr/bin/newgrp /usr/bin/su /usr/bin/mount /usr/bin/umount /usr/lib/dbus-1.0/dbus-daemon-launch-helper /run/wrappers/bin/sudo /run/wrappers/bin/su /run/wrappers/bin/mount /run/wrappers/bin/umount /run/wrappers/bin/passwd /run/wrappers/bin/sg /run/wrappers/bin/newgrp"
unknown_suid=""
while IFS= read -r f; do
    # NixOS: /nix/store paths are declaratively managed — skip them
    case "$f" in /nix/store/*) continue ;; esac
    is_known=false
    for k in $known_suid; do
        [ "$f" = "$k" ] && { is_known=true; break; }
    done
    $is_known || unknown_suid="${unknown_suid}${f},"
done < <(find / -perm -4000 -type f 2>/dev/null | head -50)
unknown_suid=${unknown_suid%,}
[ -z "$unknown_suid" ] && unknown_suid="none"

# 6. Audit logging — check if auditd or journald persistent logging
audit_status="none"
if systemctl is-active auditd >/dev/null 2>&1; then
    audit_status="auditd"
elif [ -d /var/log/journal ]; then
    audit_status="journald_persistent"
else
    audit_status="journald_volatile"
fi

# 7. NTP synchronization
ntp_synced="unknown"
if command -v timedatectl >/dev/null 2>&1; then
    ntp_synced=$(timedatectl show --property=NTPSynchronized --value 2>/dev/null || echo "unknown")
elif command -v chronyc >/dev/null 2>&1; then
    chronyc tracking >/dev/null 2>&1 && ntp_synced="yes" || ntp_synced="no"
fi

# 8. Kernel security parameters
sysctl_ipfwd=$(sysctl -n net.ipv4.ip_forward 2>/dev/null || echo "unknown")
sysctl_syncookies=$(sysctl -n net.ipv4.tcp_syncookies 2>/dev/null || echo "unknown")
sysctl_rp_filter=$(sysctl -n net.ipv4.conf.all.rp_filter 2>/dev/null || echo "unknown")
sysctl_accept_redirects=$(sysctl -n net.ipv4.conf.all.accept_redirects 2>/dev/null || echo "unknown")

# 9. Open ports listening externally (not just localhost)
open_ports=""
if command -v ss >/dev/null 2>&1; then
    open_ports=$(ss -tlnp 2>/dev/null | grep -v "127.0.0.1\|::1\|Local" | awk '{print $4}' | sed 's/.*://' | sort -un | tr '\n' ',' | sed 's/,$//')
fi
[ -z "$open_ports" ] && open_ports="none"

# 10. User accounts — check for unexpected users with login shells
unexpected_users=""
while IFS=: read -r user _ uid _ _ _ shell; do
    [ "$uid" -ge 1000 ] 2>/dev/null && [ "$uid" -lt 65534 ] 2>/dev/null && {
        case "$shell" in
            */nologin|*/false) ;;
            *) unexpected_users="${unexpected_users}${user}(${uid})," ;;
        esac
    }
done < /etc/passwd
unexpected_users=${unexpected_users%,}
[ -z "$unexpected_users" ] && unexpected_users="none"

# 11. File permissions — critical config files
perms_issues=""
for f in /etc/shadow /etc/gshadow; do
    [ -f "$f" ] && {
        mode=$(stat -c '%a' "$f" 2>/dev/null)
        [ "$mode" != "640" ] && [ "$mode" != "600" ] && [ "$mode" != "000" ] && perms_issues="${perms_issues}${f}:${mode},"
    }
done
for f in /etc/passwd /etc/group; do
    [ -f "$f" ] && {
        mode=$(stat -c '%a' "$f" 2>/dev/null)
        [ "$mode" != "644" ] && perms_issues="${perms_issues}${f}:${mode},"
    }
done
perms_issues=${perms_issues%,}
[ -z "$perms_issues" ] && perms_issues="ok"

# 12. Unattended upgrades — check if auto-update is configured (NixOS: nixos-rebuild via timer)
auto_update="none"
if systemctl is-active nixos-upgrade.timer >/dev/null 2>&1; then
    auto_update="nixos_upgrade_timer"
elif systemctl is-active unattended-upgrades.timer >/dev/null 2>&1; then
    auto_update="unattended_upgrades"
elif systemctl is-active dnf-automatic.timer >/dev/null 2>&1; then
    auto_update="dnf_automatic"
fi

# 13. Log forwarding — check if syslog forwarding, remote journald, or journald persistent
log_fwd="none"
if [ -f /etc/rsyslog.conf ] && grep -q '@@\|@' /etc/rsyslog.conf 2>/dev/null; then
    log_fwd="rsyslog"
elif systemctl is-active systemd-journal-upload.service >/dev/null 2>&1; then
    log_fwd="journal_upload"
elif grep -qE "^Storage=persistent" /etc/systemd/journald.conf 2>/dev/null; then
    log_fwd="journald_persistent"
fi

# 14. Cron review — non-system cron jobs
cron_jobs=""
for u in $(cut -d: -f1 /etc/passwd 2>/dev/null); do
    jobs=$(crontab -u "$u" -l 2>/dev/null | grep -v '^#\|^$\|^MAILTO\|^PATH\|^SHELL' | head -5)
    [ -n "$jobs" ] && cron_jobs="${cron_jobs}${u}:$(echo $jobs | tr '\n' ';'),"
done
cron_jobs=${cron_jobs%,}
[ -z "$cron_jobs" ] && cron_jobs="none"

# 15. Certificate expiry — check TLS certs in common locations
cert_issues=""
for cert in /etc/ssl/certs/appliance.pem /var/lib/msp/ca/ca.pem /etc/ssl/certs/server.crt; do
    [ -f "$cert" ] && {
        if openssl x509 -checkend 2592000 -noout -in "$cert" 2>/dev/null; then
            : # cert valid for >30 days
        else
            expiry=$(openssl x509 -enddate -noout -in "$cert" 2>/dev/null | cut -d= -f2)
            cert_issues="${cert_issues}${cert}:${expiry},"
        fi
    }
done
cert_issues=${cert_issues%,}
[ -z "$cert_issues" ] && cert_issues="ok"

# 16. Backup status — check common Linux backup tools
backup_tool="none"
backup_last=""
backup_age_days=-1
backup_status="missing"
backup_details=""

# Check restic (if binary exists and RESTIC_REPOSITORY is set or common paths)
if command -v restic >/dev/null 2>&1; then
    for repo in /var/backups/restic /backup/restic /srv/backup; do
        if [ -d "$repo" ]; then
            snap_json=$(restic -r "$repo" snapshots --latest 1 --json 2>/dev/null)
            if [ -n "$snap_json" ] && echo "$snap_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['time'])" 2>/dev/null; then
                backup_tool="restic"
                backup_last=$(echo "$snap_json" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['time'])" 2>/dev/null)
                backup_details="repo:$repo"
                break
            fi
        fi
    done
fi

# Check borg (if binary exists)
if [ "$backup_tool" = "none" ] && command -v borg >/dev/null 2>&1; then
    for repo in /var/backups/borg /backup/borg /srv/backup; do
        if [ -d "$repo" ]; then
            borg_json=$(borg list --last 1 --json "$repo" 2>/dev/null)
            if [ -n "$borg_json" ] && echo "$borg_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['archives'][0]['start'])" 2>/dev/null; then
                backup_tool="borg"
                backup_last=$(echo "$borg_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['archives'][0]['start'])" 2>/dev/null)
                backup_details="repo:$repo"
                break
            fi
        fi
    done
fi

# Check rsnapshot (look for latest snapshot directory)
if [ "$backup_tool" = "none" ]; then
    for snapdir in /var/cache/rsnapshot /backup/rsnapshot /.snapshots; do
        if [ -d "$snapdir" ]; then
            latest=$(ls -1td "$snapdir"/*/ 2>/dev/null | head -1)
            if [ -n "$latest" ]; then
                backup_tool="rsnapshot"
                # Get mtime of the latest snapshot dir
                snap_epoch=$(stat -c '%Y' "$latest" 2>/dev/null || stat -f '%m' "$latest" 2>/dev/null)
                if [ -n "$snap_epoch" ]; then
                    backup_last=$(date -u -d "@$snap_epoch" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -r "$snap_epoch" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)
                fi
                backup_details="dir:$latest"
                break
            fi
        fi
    done
fi

# Generic: check /var/backups for recent files
if [ "$backup_tool" = "none" ] && [ -d /var/backups ]; then
    recent_backup=$(find /var/backups -type f -mtime -7 2>/dev/null | head -1)
    if [ -n "$recent_backup" ]; then
        backup_tool="generic"
        snap_epoch=$(stat -c '%Y' "$recent_backup" 2>/dev/null || stat -f '%m' "$recent_backup" 2>/dev/null)
        if [ -n "$snap_epoch" ]; then
            backup_last=$(date -u -d "@$snap_epoch" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -r "$snap_epoch" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)
        fi
        backup_details="file:$recent_backup"
    fi
fi

# Calculate age if we have a last backup timestamp
if [ -n "$backup_last" ]; then
    backup_epoch=$(date -u -d "$backup_last" '+%s' 2>/dev/null || date -u -j -f '%Y-%m-%dT%H:%M:%SZ' "$backup_last" '+%s' 2>/dev/null)
    now_epoch=$(date '+%s')
    if [ -n "$backup_epoch" ] && [ "$backup_epoch" -gt 0 ] 2>/dev/null; then
        age_secs=$((now_epoch - backup_epoch))
        backup_age_days=$((age_secs / 86400))
        if [ "$backup_age_days" -le 7 ]; then
            backup_status="current"
        else
            backup_status="stale"
        fi
    else
        backup_status="current"
        backup_age_days=0
    fi
fi

# Sanitize
backup_age_days=$(echo "$backup_age_days" | head -1 | tr -dc '0-9-'); [ -z "$backup_age_days" ] && backup_age_days=-1

# 17. Disk encryption — LUKS/dm-crypt check
# Detect if mounted block devices sit on encrypted volumes
encryption_status="unknown"
unencrypted_mounts=""
if command -v lsblk >/dev/null 2>&1; then
    # Get JSON block device layout
    lsblk_json=$(lsblk -o NAME,TYPE,FSTYPE,MOUNTPOINT -J 2>/dev/null)
    if [ -n "$lsblk_json" ]; then
        # Use python3 to parse: find mounted devices not on LUKS
        unencrypted_mounts=$(python3 -c "
import json, sys
try:
    data = json.loads('''$lsblk_json''')
    devices = data.get('blockdevices', [])
    # Collect all LUKS parent device names
    luks_children = set()
    def find_luks(devs, parent_is_luks=False):
        for d in devs:
            is_luks = d.get('fstype') == 'crypto_LUKS'
            children = d.get('children', [])
            if is_luks:
                for c in children:
                    luks_children.add(c.get('name', ''))
                    for gc in c.get('children', []):
                        luks_children.add(gc.get('name', ''))
            if parent_is_luks:
                luks_children.add(d.get('name', ''))
            find_luks(children, parent_is_luks or is_luks)
    find_luks(devices)

    # Find mounted partitions not under LUKS (exclude tmpfs, swap, devtmpfs, boot)
    unenc = []
    def check_mounted(devs, depth=0):
        for d in devs:
            mp = d.get('mountpoint', '') or ''
            fstype = d.get('fstype', '') or ''
            name = d.get('name', '')
            children = d.get('children', [])
            if mp and mp != '[SWAP]' and fstype not in ('tmpfs', 'devtmpfs', 'sysfs', 'proc', 'efivarfs', 'vfat'):
                if mp in ('/boot', '/boot/efi', '/boot/EFI'):
                    pass  # boot partitions are typically unencrypted
                elif name not in luks_children:
                    unenc.append(f'{name}:{mp}')
            check_mounted(children, depth+1)
    check_mounted(devices)
    print(','.join(unenc) if unenc else 'ok')
except Exception as e:
    print('check_error')
" 2>/dev/null)
        [ -z "$unencrypted_mounts" ] && unencrypted_mounts="check_error"
        if [ "$unencrypted_mounts" = "ok" ]; then
            encryption_status="encrypted"
        elif [ "$unencrypted_mounts" = "check_error" ]; then
            encryption_status="unknown"
        else
            encryption_status="unencrypted"
        fi
    fi
fi

# Sanitize all numeric vars to prevent Python syntax errors
fw_rules=$(echo "$fw_rules" | head -1 | tr -dc '0-9'); [ -z "$fw_rules" ] && fw_rules=0
failed_count=$(echo "$failed_count" | head -1 | tr -dc '0-9'); [ -z "$failed_count" ] && failed_count=0
disk_pct=$(echo "$disk_pct" | head -1 | tr -dc '0-9'); [ -z "$disk_pct" ] && disk_pct=0

# Build final JSON output
python3 -c "
import json
print(json.dumps({
    'firewall': {'status': '$fw_status', 'rules': $fw_rules},
    'ssh': {'root_login': '$ssh_root', 'password_auth': '$ssh_passauth', 'port': '$ssh_port'},
    'failed_services': {'count': $failed_count, 'services': '$failed_svcs'},
    'disk': {'warning': '$disk_warning', 'max_pct': $disk_pct},
    'suid': '$unknown_suid',
    'audit': '$audit_status',
    'ntp_synced': '$ntp_synced',
    'kernel': {'ip_forward': '$sysctl_ipfwd', 'syncookies': '$sysctl_syncookies', 'rp_filter': '$sysctl_rp_filter', 'accept_redirects': '$sysctl_accept_redirects'},
    'open_ports': '$open_ports',
    'users': '$unexpected_users',
    'permissions': '$perms_issues',
    'auto_update': '$auto_update',
    'log_forwarding': '$log_fwd',
    'cron': '$cron_jobs',
    'cert_expiry': '$cert_issues',
    'backup': {'tool': '$backup_tool', 'last_backup': '$backup_last', 'age_days': $backup_age_days, 'status': '$backup_status', 'details': '$backup_details'},
    'encryption': {'status': '$encryption_status', 'unencrypted_mounts': '$unencrypted_mounts'}
}))
"
`

// linuxScanState is the parsed output of the Linux scan script.
type linuxScanState struct {
	Firewall struct {
		Status string `json:"status"`
		Rules  int    `json:"rules"`
	} `json:"firewall"`
	SSH struct {
		RootLogin    string `json:"root_login"`
		PasswordAuth string `json:"password_auth"`
		Port         string `json:"port"`
	} `json:"ssh"`
	FailedServices struct {
		Count    int    `json:"count"`
		Services string `json:"services"`
	} `json:"failed_services"`
	Disk struct {
		Warning string `json:"warning"`
		MaxPct  int    `json:"max_pct"`
	} `json:"disk"`
	SUID           string `json:"suid"`
	Audit          string `json:"audit"`
	NTPSynced      string `json:"ntp_synced"`
	Kernel         struct {
		IPForward       string `json:"ip_forward"`
		Syncookies      string `json:"syncookies"`
		RPFilter        string `json:"rp_filter"`
		AcceptRedirects string `json:"accept_redirects"`
	} `json:"kernel"`
	OpenPorts      string `json:"open_ports"`
	Users          string `json:"users"`
	Permissions    string `json:"permissions"`
	AutoUpdate     string `json:"auto_update"`
	LogForwarding  string `json:"log_forwarding"`
	Cron           string `json:"cron"`
	CertExpiry     string `json:"cert_expiry"`
	Backup struct {
		Tool       string `json:"tool"`
		LastBackup string `json:"last_backup"`
		AgeDays    int    `json:"age_days"`
		Status     string `json:"status"`
		Details    string `json:"details"`
	} `json:"backup"`
	Encryption struct {
		Status           string `json:"status"`
		UnencryptedMounts string `json:"unencrypted_mounts"`
	} `json:"encryption"`
}

// scanLinuxTargets scans all Linux targets for security drift.
// Only scans remote linux_targets received from the checkin response.
// The NixOS appliance itself is NOT scanned — it is declaratively managed
// and self-scanning produces false positives (NixOS SUID wrappers, nftables
// vs iptables, NixOS-specific sysctl paths, etc.).
func (ds *driftScanner) scanLinuxTargets(ctx context.Context) {
	// Overall deadline prevents the entire scan from hanging forever.
	// Per-target SSH has a 60s command timeout + 1 retry, plus macOS WoL adds ~25s,
	// so each target can take up to ~160s. With 10 targets + healing + evidence
	// submission, 10 minutes is a generous but finite upper bound.
	scanCtx, scanCancel := context.WithTimeout(ctx, 10*time.Minute)
	defer scanCancel()

	var allFindings []driftFinding
	var scannedHosts []string

	// Remote Linux targets from checkin response
	targets := ds.svc.Targets.GetLinuxTargets()

	// Mesh filter: only scan targets this appliance owns on the hash ring.
	// Key canonicalized to IP to prevent hostname/IP hash divergence between appliances.
	if ds.svc.Mesh != nil && ds.svc.Mesh.PeerCount() > 0 {
		var owned []linuxTarget
		for _, lt := range targets {
			key := lt.Hostname
			if net.ParseIP(key) == nil {
				if addrs, err := net.LookupHost(key); err == nil && len(addrs) > 0 {
					key = addrs[0]
				}
			}
			if ds.svc.Mesh.OwnsTarget(key) {
				owned = append(owned, lt)
			}
		}
		if len(owned) < len(targets) {
			log.Printf("[linuxscan] Mesh filter: %d/%d Linux targets owned by this appliance", len(owned), len(targets))
		}
		targets = owned
	}

	for _, lt := range targets {
		select {
		case <-scanCtx.Done():
			log.Printf("[linuxscan] Scan aborted: %v (scanned %d/%d targets)", scanCtx.Err(), len(scannedHosts), len(targets))
			return
		default:
		}

		target := &sshexec.Target{
			Hostname: lt.Hostname,
			Port:     lt.Port,
			Username: lt.Username,
		}
		if lt.Password != "" {
			target.Password = &lt.Password
		}
		if lt.PrivateKey != "" {
			target.PrivateKey = &lt.PrivateKey
		}
		if lt.SudoPassword != "" {
			target.SudoPassword = &lt.SudoPassword
		}

		var findings []driftFinding
		if lt.Label == "macos" {
			findings = ds.scanMacOSRemote(scanCtx, target, lt.Label)
		} else {
			findings = ds.scanLinuxRemote(scanCtx, target, lt.Label)
		}
		allFindings = append(allFindings, findings...)
		scannedHosts = append(scannedHosts, lt.Hostname)
	}

	// Report drifts through healing pipeline
	for i := range allFindings {
		if strings.HasPrefix(allFindings[i].CheckType, "macos_") {
			ds.reportMacOSDrift(&allFindings[i])
		} else {
			ds.reportLinuxDrift(&allFindings[i])
		}
	}

	log.Printf("[linuxscan] Scan complete: targets=%d, drifts_found=%d",
		len(scannedHosts), len(allFindings))

	// Update adaptive scan interval with Linux findings
	ds.updateLastDriftHosts(allFindings, scannedHosts)

	// Log healing rate after each scan cycle
	ds.daemon.logHealingRate()

	// Submit evidence bundle
	if ds.daemon.evidenceSubmitter != nil && len(scannedHosts) > 0 {
		evFindings := make([]evidence.DriftFinding, len(allFindings))
		for i, f := range allFindings {
			evFindings[i] = evidence.DriftFinding{
				Hostname:     f.Hostname,
				CheckType:    f.CheckType,
				Expected:     f.Expected,
				Actual:       f.Actual,
				HIPAAControl: f.HIPAAControl,
				Severity:     f.Severity,
			}
		}
		if err := ds.daemon.evidenceSubmitter.BuildAndSubmitLinux(scanCtx, evFindings, scannedHosts); err != nil {
			log.Printf("[linuxscan] Evidence submission failed: %v", err)
		}
	}
}

// scanLinuxRemote scans a remote Linux target via SSH.
func (ds *driftScanner) scanLinuxRemote(ctx context.Context, target *sshexec.Target, label string) []driftFinding {
	// Per-target deadline: 60s command timeout + 1 retry (15s delay + 60s) = ~135s.
	// Use 3 minutes as a hard upper bound.
	targetCtx, targetCancel := context.WithTimeout(ctx, 3*time.Minute)
	defer targetCancel()

	result := ds.svc.SSH.Execute(
		targetCtx, target, linuxScanScript,
		"LINUX-DRIFT-SCAN", "driftscan",
		60, 1, 15.0, true, nil,
	)

	if !result.Success {
		stderr := maputil.String(result.Output, "stderr")
		log.Printf("[linuxscan] Remote scan failed for %s (%s): error=%q exit=%d stderr=%q",
			target.Hostname, label, result.Error, result.ExitCode, stderr)
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
		return ds.unreachableFinding(st, "linux", errMsg)
	}

	stdout := maputil.String(result.Output, "stdout")
	if stdout == "" {
		return nil
	}

	return ds.parseLinuxFindings(stdout, target.Hostname)
}

// parseLinuxFindings parses the JSON output from the Linux scan script
// and converts anomalies into drift findings.
func (ds *driftScanner) parseLinuxFindings(output, hostname string) []driftFinding {
	// Find the JSON in the output (skip any non-JSON lines)
	jsonStart := strings.Index(output, "{")
	if jsonStart < 0 {
		log.Printf("[linuxscan] No JSON in output for %s", hostname)
		return nil
	}
	output = output[jsonStart:]

	var state linuxScanState
	if err := json.Unmarshal([]byte(output), &state); err != nil {
		log.Printf("[linuxscan] Parse error for %s: %v (raw: %.200s)", hostname, err, output)
		return nil
	}

	var findings []driftFinding

	// 1. Firewall — must have active rules
	if state.Firewall.Status == "no_rules" || state.Firewall.Rules == 0 {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "linux_firewall",
			Expected:     "active_rules",
			Actual:       fmt.Sprintf("%s (%d rules)", state.Firewall.Status, state.Firewall.Rules),
			HIPAAControl: "164.312(e)(1)",
			Severity:     "high",
		})
	}

	// 2. SSH hardening
	sshIssues := []string{}
	if state.SSH.RootLogin == "yes" {
		sshIssues = append(sshIssues, "root_login=yes")
	}
	if state.SSH.PasswordAuth == "yes" {
		sshIssues = append(sshIssues, "password_auth=yes")
	}
	if len(sshIssues) > 0 {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "linux_ssh_config",
			Expected:     "hardened",
			Actual:       strings.Join(sshIssues, ", "),
			HIPAAControl: "164.312(a)(2)(i)",
			Severity:     "high",
			Details:      map[string]string{"root_login": state.SSH.RootLogin, "password_auth": state.SSH.PasswordAuth},
		})
	}

	// 3. Failed services
	if state.FailedServices.Count > 0 {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "linux_failed_services",
			Expected:     "none",
			Actual:       state.FailedServices.Services,
			HIPAAControl: "164.308(a)(5)(ii)(B)",
			Severity:     "medium",
			Details:      map[string]string{"count": strconv.Itoa(state.FailedServices.Count)},
		})
	}

	// 4. Disk space
	if state.Disk.Warning != "ok" && state.Disk.MaxPct > 90 {
		severity := "medium"
		if state.Disk.MaxPct > 95 {
			severity = "high"
		}
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "linux_disk_space",
			Expected:     "<90%",
			Actual:       state.Disk.Warning,
			HIPAAControl: "164.308(a)(7)(ii)(A)",
			Severity:     severity,
		})
	}

	// 5. SUID binaries
	if state.SUID != "none" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "linux_suid_binaries",
			Expected:     "none_unexpected",
			Actual:       state.SUID,
			HIPAAControl: "164.312(a)(1)",
			Severity:     "high",
			Details:      map[string]string{"binaries": state.SUID},
		})
	}

	// 6. Audit logging
	if state.Audit == "none" || state.Audit == "journald_volatile" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "linux_audit_logging",
			Expected:     "persistent",
			Actual:       state.Audit,
			HIPAAControl: "164.312(b)",
			Severity:     "critical",
		})
	}

	// 7. NTP sync
	if state.NTPSynced != "yes" && state.NTPSynced != "unknown" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "linux_ntp_sync",
			Expected:     "synchronized",
			Actual:       state.NTPSynced,
			HIPAAControl: "164.312(b)",
			Severity:     "medium",
		})
	}

	// 8. Kernel security parameters
	kernelIssues := []string{}
	if state.Kernel.IPForward == "1" {
		kernelIssues = append(kernelIssues, "ip_forward=1")
	}
	if state.Kernel.Syncookies != "1" {
		kernelIssues = append(kernelIssues, "syncookies="+state.Kernel.Syncookies)
	}
	if state.Kernel.AcceptRedirects == "1" {
		kernelIssues = append(kernelIssues, "accept_redirects=1")
	}
	if len(kernelIssues) > 0 {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "linux_kernel_params",
			Expected:     "hardened",
			Actual:       strings.Join(kernelIssues, ", "),
			HIPAAControl: "164.312(e)(1)",
			Severity:     "medium",
		})
	}

	// 9. Open ports — report for visibility (not necessarily drift)
	if state.OpenPorts != "none" {
		// Count ports — more than 5 external ports is suspicious
		portCount := len(strings.Split(state.OpenPorts, ","))
		if portCount > 5 {
			findings = append(findings, driftFinding{
				Hostname:     hostname,
				CheckType:    "linux_open_ports",
				Expected:     "minimal",
				Actual:       fmt.Sprintf("%d ports: %s", portCount, state.OpenPorts),
				HIPAAControl: "164.312(e)(1)",
				Severity:     "medium",
				Details:      map[string]string{"ports": state.OpenPorts},
			})
		}
	}

	// 10. User accounts
	if state.Users != "none" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "linux_user_accounts",
			Expected:     "known_only",
			Actual:       state.Users,
			HIPAAControl: "164.312(a)(1)",
			Severity:     "high",
			Details:      map[string]string{"users": state.Users},
		})
	}

	// 11. File permissions
	if state.Permissions != "ok" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "linux_file_permissions",
			Expected:     "secure",
			Actual:       state.Permissions,
			HIPAAControl: "164.312(a)(1)",
			Severity:     "high",
		})
	}

	// 12. Auto-update
	if state.AutoUpdate == "none" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "linux_unattended_upgrades",
			Expected:     "enabled",
			Actual:       "none",
			HIPAAControl: "164.308(a)(5)(ii)(A)",
			Severity:     "medium",
		})
	}

	// 13. Log forwarding
	if state.LogForwarding == "none" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "linux_log_forwarding",
			Expected:     "configured",
			Actual:       "none",
			HIPAAControl: "164.312(b)",
			Severity:     "low",
		})
	}

	// 14. Cron review (informational — only flag if unexpected cron jobs exist)
	if state.Cron != "none" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "linux_cron_review",
			Expected:     "reviewed",
			Actual:       state.Cron,
			HIPAAControl: "164.308(a)(1)(ii)(D)",
			Severity:     "low",
			Details:      map[string]string{"jobs": state.Cron},
		})
	}

	// 15. Certificate expiry
	if state.CertExpiry != "ok" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "linux_cert_expiry",
			Expected:     "valid",
			Actual:       state.CertExpiry,
			HIPAAControl: "164.312(e)(2)(ii)",
			Severity:     "high",
		})
	}

	// 16. Backup status — must have recent backup (within 7 days)
	if state.Backup.Status == "missing" || state.Backup.Tool == "none" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "linux_backup_status",
			Expected:     "backup_within_7d",
			Actual:       "no backup found",
			HIPAAControl: "164.308(a)(7)(ii)(A)",
			Severity:     "medium",
			Details: map[string]string{
				"backup_tool":   state.Backup.Tool,
				"backup_status": state.Backup.Status,
			},
		})
	} else if state.Backup.Status == "stale" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "linux_backup_status",
			Expected:     "backup_within_7d",
			Actual:       fmt.Sprintf("last backup %d days ago (%s)", state.Backup.AgeDays, state.Backup.Tool),
			HIPAAControl: "164.308(a)(7)(ii)(A)",
			Severity:     "medium",
			Details: map[string]string{
				"backup_tool":   state.Backup.Tool,
				"backup_status": state.Backup.Status,
				"backup_age":    fmt.Sprintf("%d", state.Backup.AgeDays),
				"last_backup":   state.Backup.LastBackup,
			},
		})
	}

	// 17. Disk encryption — LUKS/dm-crypt required for HIPAA encryption at rest
	if state.Encryption.Status == "unencrypted" && state.Encryption.UnencryptedMounts != "" {
		findings = append(findings, driftFinding{
			Hostname:     hostname,
			CheckType:    "linux_encryption",
			Expected:     "all volumes encrypted (LUKS/dm-crypt)",
			Actual:       fmt.Sprintf("unencrypted: %s", state.Encryption.UnencryptedMounts),
			HIPAAControl: "164.312(a)(2)(iv)",
			Severity:     "critical",
			Details:      map[string]string{"unencrypted_mounts": state.Encryption.UnencryptedMounts},
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
		log.Printf("[linuxscan] %s: %d drift findings", hostname, len(findings))
	}

	return findings
}

// reportLinuxDrift sends a Linux drift finding through the L1→L2→L3 healing pipeline.
func (ds *driftScanner) reportLinuxDrift(f *driftFinding) {
	reportDriftGeneric(ds.daemon, f, "linux", "linuxscan", nil)
}

// linuxTarget represents a remote Linux machine to scan.
type linuxTarget struct {
	Hostname     string `json:"hostname"`
	Port         int    `json:"port"`
	Username     string `json:"username"`
	Password     string `json:"password,omitempty"`
	SudoPassword string `json:"sudo_password,omitempty"`
	PrivateKey   string `json:"private_key,omitempty"`
	Label        string `json:"label"`
}

// parseLinuxTargets extracts Linux targets from the checkin response.
func parseLinuxTargets(raw []map[string]interface{}) []linuxTarget {
	var targets []linuxTarget
	for _, m := range raw {
		hostname := maputil.String(m, "hostname")
		if hostname == "" {
			continue
		}
		port := 22
		if p, ok := m["port"].(float64); ok {
			port = int(p)
		}
		username := maputil.StringDefault(m, "username", "root")
		password := maputil.String(m, "password")
		sudoPassword := maputil.String(m, "sudo_password")
		if sudoPassword == "" && password != "" {
			sudoPassword = password // fallback: use password as sudo password
		}
		key := maputil.String(m, "private_key")
		label := maputil.String(m, "label")
		if label == "" {
			label = "linux"
		}

		targets = append(targets, linuxTarget{
			Hostname:     hostname,
			Port:         port,
			Username:     username,
			Password:     password,
			SudoPassword: sudoPassword,
			PrivateKey:   key,
			Label:        label,
		})
	}
	return targets
}
