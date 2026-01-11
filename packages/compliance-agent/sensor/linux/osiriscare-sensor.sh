#!/bin/bash
#===============================================================================
# OsirisCare Linux Sensor - Lightweight Drift Detection Agent
#===============================================================================
# Purpose: Detect configuration drift and security issues on Linux servers
# Mode: Push-based detection (read-only), SSH-based remediation
# Install: curl -sSL https://appliance:8443/sensor/install.sh | bash
#===============================================================================

set -euo pipefail

# Configuration (loaded from /etc/osiriscare/sensor.env)
SENSOR_VERSION="1.0.0"
SENSOR_ID=""
APPLIANCE_URL=""
API_KEY=""
CHECK_INTERVAL="${CHECK_INTERVAL:-10}"
LOG_FILE="/var/log/osiriscare-sensor.log"
STATE_DIR="/var/lib/osiriscare"
HOSTNAME=$(hostname -f 2>/dev/null || hostname)

#===============================================================================
# Logging
#===============================================================================

log() {
    local level="$1"
    shift
    local msg="$*"
    local ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    echo "[$ts] [$level] $msg" >> "$LOG_FILE"
    if [[ "$level" == "ERROR" ]]; then
        echo "[$ts] [$level] $msg" >&2
    fi
}

log_info()  { log "INFO"  "$@"; }
log_warn()  { log "WARN"  "$@"; }
log_error() { log "ERROR" "$@"; }
log_debug() { [[ "${DEBUG:-0}" == "1" ]] && log "DEBUG" "$@" || true; }

#===============================================================================
# Configuration Loading
#===============================================================================

load_config() {
    local config_file="/etc/osiriscare/sensor.env"

    if [[ ! -f "$config_file" ]]; then
        log_error "Config file not found: $config_file"
        exit 1
    fi

    # shellcheck source=/dev/null
    source "$config_file"

    # Validate required fields
    if [[ -z "${SENSOR_ID:-}" ]]; then
        log_error "SENSOR_ID not set in config"
        exit 1
    fi

    if [[ -z "${APPLIANCE_URL:-}" ]]; then
        log_error "APPLIANCE_URL not set in config"
        exit 1
    fi

    if [[ -z "${API_KEY:-}" ]]; then
        log_error "API_KEY not set in config"
        exit 1
    fi

    # Create state directory
    mkdir -p "$STATE_DIR"

    log_info "Configuration loaded: sensor=$SENSOR_ID appliance=$APPLIANCE_URL"
}

#===============================================================================
# API Communication
#===============================================================================

send_event() {
    local check_type="$1"
    local severity="$2"
    local title="$3"
    local details="$4"
    local current_value="${5:-}"
    local expected_value="${6:-}"

    local payload
    payload=$(cat <<EOF
{
    "sensor_id": "${SENSOR_ID}",
    "hostname": "${HOSTNAME}",
    "check_type": "${check_type}",
    "severity": "${severity}",
    "title": "${title}",
    "details": "${details}",
    "current_value": "${current_value}",
    "expected_value": "${expected_value}",
    "timestamp": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOF
)

    log_debug "Sending event: $check_type ($severity)"

    # Send to appliance API
    local response
    response=$(curl -s -w "\n%{http_code}" \
        --max-time 10 \
        -X POST \
        -H "Content-Type: application/json" \
        -H "X-API-Key: ${API_KEY}" \
        -d "$payload" \
        "${APPLIANCE_URL}/sensor/event" 2>/dev/null) || {
        log_warn "Failed to send event to appliance"
        return 1
    }

    local http_code
    http_code=$(echo "$response" | tail -n1)

    if [[ "$http_code" -ge 200 && "$http_code" -lt 300 ]]; then
        log_debug "Event sent successfully: $check_type"
        return 0
    else
        log_warn "Event send failed with HTTP $http_code"
        return 1
    fi
}

send_heartbeat() {
    local payload
    payload=$(cat <<EOF
{
    "sensor_id": "${SENSOR_ID}",
    "hostname": "${HOSTNAME}",
    "version": "${SENSOR_VERSION}",
    "uptime": $(cat /proc/uptime | cut -d' ' -f1 | cut -d. -f1),
    "timestamp": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOF
)

    curl -s --max-time 5 \
        -X POST \
        -H "Content-Type: application/json" \
        -H "X-API-Key: ${API_KEY}" \
        -d "$payload" \
        "${APPLIANCE_URL}/sensor/heartbeat" >/dev/null 2>&1 || true
}

#===============================================================================
# Drift Detection Checks
#===============================================================================

# Check 1: SSH Configuration Drift
check_ssh_config() {
    local check_type="ssh_config"
    local ssh_config="/etc/ssh/sshd_config"

    [[ ! -f "$ssh_config" ]] && return 0

    # Check for password authentication
    if grep -qi "^PasswordAuthentication\s*yes" "$ssh_config" 2>/dev/null; then
        send_event "$check_type" "high" \
            "SSH Password Authentication Enabled" \
            "Password authentication is enabled in sshd_config. Key-based auth recommended." \
            "PasswordAuthentication yes" \
            "PasswordAuthentication no"
    fi

    # Check for root login
    if grep -qi "^PermitRootLogin\s*yes" "$ssh_config" 2>/dev/null; then
        send_event "$check_type" "high" \
            "SSH Root Login Permitted" \
            "Direct root login is permitted via SSH." \
            "PermitRootLogin yes" \
            "PermitRootLogin no"
    fi

    # Check for empty passwords
    if grep -qi "^PermitEmptyPasswords\s*yes" "$ssh_config" 2>/dev/null; then
        send_event "$check_type" "critical" \
            "SSH Empty Passwords Permitted" \
            "Empty passwords are permitted for SSH login." \
            "PermitEmptyPasswords yes" \
            "PermitEmptyPasswords no"
    fi
}

# Check 2: Firewall Status
check_firewall() {
    local check_type="firewall"

    # Check iptables
    if command -v iptables &>/dev/null; then
        local rule_count
        rule_count=$(iptables -L -n 2>/dev/null | grep -c -v "^Chain\|^target\|^$" || echo "0")

        if [[ "$rule_count" -lt 3 ]]; then
            send_event "$check_type" "high" \
                "Firewall Rules Minimal" \
                "iptables has fewer than 3 rules. Firewall may not be properly configured." \
                "$rule_count rules" \
                "3+ rules"
        fi
    fi

    # Check ufw if available
    if command -v ufw &>/dev/null; then
        local ufw_status
        ufw_status=$(ufw status 2>/dev/null | head -1 || echo "unknown")

        if [[ "$ufw_status" == *"inactive"* ]]; then
            send_event "$check_type" "medium" \
                "UFW Firewall Inactive" \
                "UFW firewall is installed but not active." \
                "inactive" \
                "active"
        fi
    fi

    # Check firewalld if available
    if command -v firewall-cmd &>/dev/null; then
        if ! firewall-cmd --state &>/dev/null; then
            send_event "$check_type" "medium" \
                "FirewallD Not Running" \
                "firewalld is installed but not running." \
                "not running" \
                "running"
        fi
    fi
}

# Check 3: Failed Login Attempts
check_failed_logins() {
    local check_type="failed_logins"
    local threshold=10
    local time_window=3600  # 1 hour

    local failed_count=0

    # Check auth.log (Debian/Ubuntu)
    if [[ -f /var/log/auth.log ]]; then
        failed_count=$(grep -c "Failed password\|authentication failure" \
            /var/log/auth.log 2>/dev/null | tail -1 || echo "0")
    # Check secure log (RHEL/CentOS)
    elif [[ -f /var/log/secure ]]; then
        failed_count=$(grep -c "Failed password\|authentication failure" \
            /var/log/secure 2>/dev/null | tail -1 || echo "0")
    # Check journalctl
    elif command -v journalctl &>/dev/null; then
        failed_count=$(journalctl -u sshd --since "1 hour ago" 2>/dev/null | \
            grep -c "Failed password\|authentication failure" || echo "0")
    fi

    if [[ "$failed_count" -gt "$threshold" ]]; then
        send_event "$check_type" "high" \
            "High Failed Login Attempts" \
            "Detected $failed_count failed login attempts in the last hour." \
            "$failed_count" \
            "<$threshold"
    fi
}

# Check 4: Disk Space
check_disk_space() {
    local check_type="disk_space"
    local critical_threshold=90
    local warning_threshold=80

    while IFS= read -r line; do
        local mount usage
        mount=$(echo "$line" | awk '{print $6}')
        usage=$(echo "$line" | awk '{print $5}' | tr -d '%')

        if [[ "$usage" -ge "$critical_threshold" ]]; then
            send_event "$check_type" "critical" \
                "Disk Space Critical: $mount" \
                "Filesystem $mount is ${usage}% full." \
                "${usage}%" \
                "<${critical_threshold}%"
        elif [[ "$usage" -ge "$warning_threshold" ]]; then
            send_event "$check_type" "medium" \
                "Disk Space Warning: $mount" \
                "Filesystem $mount is ${usage}% full." \
                "${usage}%" \
                "<${warning_threshold}%"
        fi
    done < <(df -h 2>/dev/null | grep -E "^/dev/" | grep -v "loop")
}

# Check 5: Memory Usage
check_memory() {
    local check_type="memory"
    local critical_threshold=95
    local warning_threshold=85

    local mem_info
    mem_info=$(free | grep Mem)
    local total=$(echo "$mem_info" | awk '{print $2}')
    local used=$(echo "$mem_info" | awk '{print $3}')
    local usage=$((used * 100 / total))

    if [[ "$usage" -ge "$critical_threshold" ]]; then
        send_event "$check_type" "critical" \
            "Memory Usage Critical" \
            "Memory usage is at ${usage}%." \
            "${usage}%" \
            "<${critical_threshold}%"
    elif [[ "$usage" -ge "$warning_threshold" ]]; then
        send_event "$check_type" "medium" \
            "Memory Usage High" \
            "Memory usage is at ${usage}%." \
            "${usage}%" \
            "<${warning_threshold}%"
    fi
}

# Check 6: Unauthorized Users
check_users() {
    local check_type="users"
    local state_file="$STATE_DIR/known_users"

    # Get current users with shell access
    local current_users
    current_users=$(grep -v "nologin\|false" /etc/passwd | cut -d: -f1 | sort)

    # First run - save baseline
    if [[ ! -f "$state_file" ]]; then
        echo "$current_users" > "$state_file"
        log_info "User baseline saved"
        return 0
    fi

    # Compare with baseline
    local known_users
    known_users=$(cat "$state_file")

    local new_users
    new_users=$(comm -23 <(echo "$current_users") <(echo "$known_users"))

    if [[ -n "$new_users" ]]; then
        send_event "$check_type" "high" \
            "New User Account Detected" \
            "New user(s) with shell access: $new_users" \
            "$new_users" \
            "No new users"
    fi

    # Update baseline
    echo "$current_users" > "$state_file"
}

# Check 7: Critical Service Status
check_services() {
    local check_type="services"

    # List of critical services to monitor
    local services=("sshd" "rsyslog" "cron" "systemd-journald")

    for svc in "${services[@]}"; do
        if systemctl is-enabled "$svc" &>/dev/null; then
            if ! systemctl is-active "$svc" &>/dev/null; then
                send_event "$check_type" "high" \
                    "Critical Service Down: $svc" \
                    "Service $svc is enabled but not running." \
                    "stopped" \
                    "running"
            fi
        fi
    done
}

# Check 8: File Integrity (critical files)
check_file_integrity() {
    local check_type="file_integrity"
    local state_file="$STATE_DIR/file_hashes"

    # Critical files to monitor
    local files=(
        "/etc/passwd"
        "/etc/shadow"
        "/etc/sudoers"
        "/etc/ssh/sshd_config"
    )

    local current_hashes=""
    for file in "${files[@]}"; do
        if [[ -f "$file" ]]; then
            local hash
            hash=$(sha256sum "$file" 2>/dev/null | cut -d' ' -f1)
            current_hashes+="$file:$hash\n"
        fi
    done

    # First run - save baseline
    if [[ ! -f "$state_file" ]]; then
        echo -e "$current_hashes" > "$state_file"
        log_info "File hash baseline saved"
        return 0
    fi

    # Compare with baseline
    local known_hashes
    known_hashes=$(cat "$state_file")

    for file in "${files[@]}"; do
        if [[ -f "$file" ]]; then
            local current_hash known_hash
            current_hash=$(echo -e "$current_hashes" | grep "^$file:" | cut -d: -f2)
            known_hash=$(echo "$known_hashes" | grep "^$file:" | cut -d: -f2)

            if [[ -n "$known_hash" && "$current_hash" != "$known_hash" ]]; then
                send_event "$check_type" "critical" \
                    "Critical File Modified: $file" \
                    "Hash changed from ${known_hash:0:16}... to ${current_hash:0:16}..." \
                    "${current_hash:0:16}..." \
                    "${known_hash:0:16}..."
            fi
        fi
    done

    # Update baseline
    echo -e "$current_hashes" > "$state_file"
}

# Check 9: Open Ports
check_open_ports() {
    local check_type="open_ports"
    local state_file="$STATE_DIR/known_ports"

    # Get listening ports
    local current_ports
    current_ports=$(ss -tlnp 2>/dev/null | grep LISTEN | awk '{print $4}' | \
        sed 's/.*://' | sort -n | uniq)

    # First run - save baseline
    if [[ ! -f "$state_file" ]]; then
        echo "$current_ports" > "$state_file"
        log_info "Port baseline saved"
        return 0
    fi

    # Compare with baseline
    local known_ports
    known_ports=$(cat "$state_file")

    local new_ports
    new_ports=$(comm -23 <(echo "$current_ports") <(echo "$known_ports"))

    if [[ -n "$new_ports" ]]; then
        send_event "$check_type" "high" \
            "New Listening Port Detected" \
            "New port(s) opened: $new_ports" \
            "$new_ports" \
            "No new ports"
    fi

    # Update baseline
    echo "$current_ports" > "$state_file"
}

# Check 10: System Updates
check_updates() {
    local check_type="updates"
    local state_file="$STATE_DIR/last_update_check"
    local check_interval=86400  # Check once per day

    # Skip if checked recently
    if [[ -f "$state_file" ]]; then
        local last_check
        last_check=$(cat "$state_file")
        local now
        now=$(date +%s)
        if [[ $((now - last_check)) -lt $check_interval ]]; then
            return 0
        fi
    fi

    local security_updates=0

    # Debian/Ubuntu
    if command -v apt-get &>/dev/null; then
        apt-get update -qq 2>/dev/null || true
        security_updates=$(apt-get -s upgrade 2>/dev/null | \
            grep -c "^Inst.*security" || echo "0")
    # RHEL/CentOS
    elif command -v yum &>/dev/null; then
        security_updates=$(yum check-update --security 2>/dev/null | \
            grep -c "^[a-zA-Z]" || echo "0")
    fi

    if [[ "$security_updates" -gt 0 ]]; then
        send_event "$check_type" "medium" \
            "Security Updates Available" \
            "$security_updates security update(s) pending." \
            "$security_updates pending" \
            "0 pending"
    fi

    # Update last check time
    date +%s > "$state_file"
}

# Check 11: Audit Logs
check_audit_logs() {
    local check_type="audit_logs"

    # Check if auditd is running
    if command -v auditctl &>/dev/null; then
        if ! systemctl is-active auditd &>/dev/null; then
            send_event "$check_type" "medium" \
                "Audit Daemon Not Running" \
                "auditd is installed but not running." \
                "stopped" \
                "running"
        fi
    fi

    # Check for log rotation issues
    local log_dir="/var/log"
    local old_logs
    old_logs=$(find "$log_dir" -name "*.log" -mtime +30 -size +100M 2>/dev/null | wc -l)

    if [[ "$old_logs" -gt 0 ]]; then
        send_event "$check_type" "low" \
            "Old Log Files Detected" \
            "$old_logs log file(s) older than 30 days and >100MB." \
            "$old_logs files" \
            "0 files"
    fi
}

# Check 12: Cron Jobs
check_cron() {
    local check_type="cron_jobs"
    local state_file="$STATE_DIR/known_cron"

    # Collect all cron jobs
    local current_cron=""

    # System crontab
    if [[ -f /etc/crontab ]]; then
        current_cron+=$(cat /etc/crontab | grep -v "^#\|^$")
    fi

    # Cron.d directory
    if [[ -d /etc/cron.d ]]; then
        current_cron+=$(cat /etc/cron.d/* 2>/dev/null | grep -v "^#\|^$")
    fi

    # User crontabs
    for user in $(cut -d: -f1 /etc/passwd); do
        local user_cron
        user_cron=$(crontab -l -u "$user" 2>/dev/null | grep -v "^#\|^$" || true)
        if [[ -n "$user_cron" ]]; then
            current_cron+="$user: $user_cron\n"
        fi
    done

    local current_hash
    current_hash=$(echo -e "$current_cron" | sha256sum | cut -d' ' -f1)

    # First run - save baseline
    if [[ ! -f "$state_file" ]]; then
        echo "$current_hash" > "$state_file"
        log_info "Cron baseline saved"
        return 0
    fi

    # Compare with baseline
    local known_hash
    known_hash=$(cat "$state_file")

    if [[ "$current_hash" != "$known_hash" ]]; then
        send_event "$check_type" "high" \
            "Cron Jobs Modified" \
            "System cron configuration has changed." \
            "${current_hash:0:16}..." \
            "${known_hash:0:16}..."
    fi

    # Update baseline
    echo "$current_hash" > "$state_file"
}

#===============================================================================
# Main Loop
#===============================================================================

run_all_checks() {
    log_debug "Running drift detection checks..."

    check_ssh_config
    check_firewall
    check_failed_logins
    check_disk_space
    check_memory
    check_users
    check_services
    check_file_integrity
    check_open_ports
    check_updates
    check_audit_logs
    check_cron

    log_debug "Drift detection checks complete"
}

main() {
    log_info "OsirisCare Sensor v${SENSOR_VERSION} starting..."

    load_config

    local heartbeat_counter=0
    local heartbeat_interval=6  # Every 60 seconds (6 * 10s)

    while true; do
        run_all_checks

        # Send heartbeat every N iterations
        heartbeat_counter=$((heartbeat_counter + 1))
        if [[ $heartbeat_counter -ge $heartbeat_interval ]]; then
            send_heartbeat
            heartbeat_counter=0
        fi

        sleep "$CHECK_INTERVAL"
    done
}

# Handle signals
trap 'log_info "Sensor shutting down..."; exit 0' SIGTERM SIGINT

# Run if executed directly (not sourced)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
