#!/usr/bin/env bash
# vps_housekeeping.sh — daily VPS disk hygiene
#
# 2026-04-15 incident: VPS filled to 100% mid-session after 6 consecutive
# ISO builds; postgres crash-looped for 30min, 8 CI deploys failed
# silently. The weekly nix-gc timer wasn't shaped for our iteration
# cadence.
#
# This script is meant to run DAILY as a systemd timer on the VPS.
# Install:
#
#     cat > /etc/systemd/system/vps-housekeeping.service <<UNIT
#     [Unit]
#     Description=VPS housekeeping — nix gc + ISO retention + backup rotation
#     [Service]
#     Type=oneshot
#     ExecStart=/root/Msp_Flakes/scripts/vps_housekeeping.sh
#     UNIT
#
#     cat > /etc/systemd/system/vps-housekeeping.timer <<UNIT
#     [Unit]
#     Description=Daily VPS housekeeping
#     [Timer]
#     OnCalendar=daily
#     RandomizedDelaySec=30min
#     Persistent=true
#     [Install]
#     WantedBy=timers.target
#     UNIT
#
#     systemctl daemon-reload
#     systemctl enable --now vps-housekeeping.timer
#
# What the script does (each step is independent — one failure doesn't
# abort the rest):
#
#   1. nix-collect-garbage --delete-older-than 7d     (was: 14d weekly)
#   2. keep the 3 most-recent osiriscare-*.iso files in /opt + /root;
#      delete the rest
#   3. prune /var/backups to the 7 most-recent entries
#   4. prune /opt/backups to the 7 most-recent entries
#   5. log free space before/after to /var/log/vps-housekeeping.log so
#      an auditor can trace how much each run reclaimed
#
# Non-destructive defaults: each prune keeps at least N latest files.
# The script NEVER touches /nix/store outside nix-collect-garbage; never
# touches postgres data; never recurses into unexpected mount points.

set -u

LOG=/var/log/vps-housekeeping.log
mkdir -p /var/log
exec >> "$LOG" 2>&1
echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) vps-housekeeping start ==="

before_free=$(df -BG / | awk 'NR==2 {print $4}')
echo "Free at start: $before_free"

# ─── 1. Nix garbage collection ─────────────────────────────────────
echo "--- nix-collect-garbage ---"
if command -v nix-collect-garbage >/dev/null 2>&1; then
    # 7-day retention window — was 14 (too loose), we iterate fast
    nix-collect-garbage --delete-older-than 7d 2>&1 | tail -5 || \
        echo "nix-collect-garbage failed; continuing"
else
    echo "nix not present, skipping GC"
fi

# ─── 2. ISO retention: keep the 3 most recent ─────────────────────
# Each ISO is ~2.2 GB — 13 ISOs = 28 GB dead weight. Shell glob sorted
# by mtime newest-first, tail -n +4 drops the 3 newest, rest get rm.
echo "--- ISO retention (keep 3 newest per dir) ---"
for dir in /opt /root; do
    mapfile -t isos < <(ls -1t "$dir"/osiriscare-*.iso 2>/dev/null || true)
    if [ "${#isos[@]}" -gt 3 ]; then
        for old in "${isos[@]:3}"; do
            echo "  rm $old ($(du -h "$old" | cut -f1))"
            rm -f "$old"
        done
    else
        echo "  $dir: ${#isos[@]} ISO(s), under retention floor"
    fi
done

# Also purge loose .img / .raw files older than 14d in /opt (these
# accumulate from pre-ISO testing, rarely needed after that).
find /opt -maxdepth 2 -type f \( -name '*.img' -o -name '*.raw' \) \
    -mtime +14 -print -delete 2>/dev/null || true

# ─── 3+4. Backup directory rotation (keep 7 newest per dir) ───────
echo "--- backup dir rotation (keep 7 newest per dir) ---"
for dir in /var/backups /opt/backups; do
    [ -d "$dir" ] || continue
    mapfile -t files < <(ls -1t "$dir" 2>/dev/null | grep -v '^$')
    if [ "${#files[@]}" -gt 7 ]; then
        for old in "${files[@]:7}"; do
            path="$dir/$old"
            if [ -e "$path" ]; then
                sz=$(du -sh "$path" 2>/dev/null | cut -f1)
                echo "  rm -rf $path ($sz)"
                rm -rf "$path"
            fi
        done
    else
        echo "  $dir: ${#files[@]} item(s), under retention floor"
    fi
done

# ─── 5. Record reclaim ─────────────────────────────────────────────
after_free=$(df -BG / | awk 'NR==2 {print $4}')
echo "Free at end: $after_free (delta: started $before_free)"
echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) vps-housekeeping done ==="

# Never exit non-zero; the script is best-effort and a single failing
# prune shouldn't fail the whole run.
exit 0
