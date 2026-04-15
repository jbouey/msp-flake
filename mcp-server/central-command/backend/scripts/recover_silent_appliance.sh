#!/usr/bin/env bash
# recover_silent_appliance.sh — diagnose a `installed_but_silent` appliance
#
# Companion to the substrate invariant + the installed-system local status
# beacon + /boot/msp-boot-diag.json dump. Given a site_id + MAC, this tool:
#
#   1. LAN-pings candidate IPs (from discovered_devices) for the beacon
#   2. curl's http://<ip>:8443/ to fetch beacon.json
#   3. If the beacon responds, prints the JSON for operator diagnosis
#   4. If the beacon does NOT respond, falls back to SSH via the MAC-derived
#      emergency password and cat's the /boot/msp-boot-diag.json dump
#   5. Pushes whatever it got into admin_audit_log + attaches to the open
#      substrate_violations row for this appliance
#
# Usage:
#   recover_silent_appliance.sh <site_id> <mac_address> [actor_email]
#
# Environment:
#   MCP_PG_CONTAINER  — default: mcp-postgres
#   MCP_API           — default: https://api.osiriscare.net
#
# Exit codes:
#   0 — beacon OR boot-diag reached, diagnostics attached
#   1 — neither reached; operator needs physical access
#   2 — argument / environment error

set -euo pipefail

if [ "$#" -lt 2 ]; then
    echo "usage: $0 <site_id> <mac_address> [actor_email]" >&2
    exit 2
fi

SITE_ID="$1"
MAC_RAW="$2"
ACTOR="${3:-recover_silent_appliance.sh}"
PG_CTR="${MCP_PG_CONTAINER:-mcp-postgres}"

# Canonicalize MAC to upper-colon-separated.
MAC=$(printf '%s' "$MAC_RAW" | tr 'a-z' 'A-Z' | tr -d ':-' \
    | sed 's/\(..\)/\1:/g; s/:$//')

# MAC-derived emergency password (matches msp-first-boot.service convention)
HASH=$(printf 'osiriscare-emergency-%s' "$MAC" | sha256sum | cut -c1-8)
EMERGENCY_PASS="osiris-$HASH"

pg() {
    docker exec -i "$PG_CTR" psql -U mcp -d mcp -t -A "$@"
}

echo "[$(date -u +%H:%M:%SZ)] recovery: site=$SITE_ID mac=$MAC"

# ─── 1. Candidate IPs from discovered_devices + site_appliances ─────────
mapfile -t CANDIDATE_IPS < <(pg -c "
    SELECT DISTINCT ip FROM (
        SELECT ip_address::text AS ip
          FROM discovered_devices
         WHERE site_id = '$SITE_ID' AND UPPER(mac_address) = '$MAC'
           AND last_seen_at > NOW() - INTERVAL '7 days'
        UNION
        SELECT jsonb_array_elements_text(ip_addresses) AS ip
          FROM site_appliances
         WHERE site_id = '$SITE_ID' AND UPPER(mac_address) = '$MAC'
    ) t
    WHERE ip NOT LIKE '169.254.%'
      AND ip NOT LIKE '10.100.%'
      AND ip NOT LIKE '127.%'
      AND ip IS NOT NULL AND ip != ''
    ORDER BY ip;
" 2>/dev/null || true)

if [ "${#CANDIDATE_IPS[@]}" -eq 0 ]; then
    echo "  no LAN IPs known for this MAC; fleet hasn't seen it recently"
fi

BEACON_JSON=""
BEACON_IP=""
for IP in "${CANDIDATE_IPS[@]}"; do
    [ -z "$IP" ] && continue
    echo "  probing http://$IP:8443/ ..."
    if BEACON_JSON=$(curl -sS --max-time 5 "http://$IP:8443/" 2>/dev/null); then
        if [ -n "$BEACON_JSON" ]; then
            BEACON_IP="$IP"
            break
        fi
    fi
done

DIAG_SOURCE=""
DIAG_PAYLOAD=""

if [ -n "$BEACON_JSON" ]; then
    echo "  ✓ beacon reached at $BEACON_IP"
    DIAG_SOURCE="beacon:$BEACON_IP"
    DIAG_PAYLOAD="$BEACON_JSON"
else
    echo "  ✗ beacon unreachable on all candidate IPs; trying SSH fallback"
    # SSH fallback: cat /boot/msp-boot-diag.json via emergency password.
    # This only works if sshd is running AND PermitRootLogin + password
    # auth are enabled for the emergency account — the installer defaults
    # allow this.
    if ! command -v sshpass >/dev/null; then
        echo "  sshpass not installed — skipping SSH fallback" >&2
    else
        for IP in "${CANDIDATE_IPS[@]}"; do
            [ -z "$IP" ] && continue
            echo "  sshpass msp@$IP ..."
            if OUT=$(sshpass -p "$EMERGENCY_PASS" \
                ssh -o ConnectTimeout=5 \
                    -o StrictHostKeyChecking=no \
                    -o LogLevel=ERROR \
                    -o PreferredAuthentications=password \
                    -o PubkeyAuthentication=no \
                    "msp@$IP" 'cat /boot/msp-boot-diag.json 2>/dev/null || cat /var/lib/msp/beacon.json 2>/dev/null' 2>/dev/null); then
                if [ -n "$OUT" ]; then
                    echo "  ✓ SSH fallback got diagnostics from $IP"
                    DIAG_SOURCE="ssh:$IP"
                    DIAG_PAYLOAD="$OUT"
                    break
                fi
            fi
        done
    fi
fi

if [ -z "$DIAG_PAYLOAD" ]; then
    echo "[$(date -u +%H:%M:%SZ)] recovery FAILED — neither beacon nor SSH reached; physical access required" >&2
    exit 1
fi

# ─── 2. Attach diagnostics to substrate_violations + admin_audit_log ────
echo ""
echo "Diagnostics payload from $DIAG_SOURCE:"
echo "$DIAG_PAYLOAD" | head -c 2000
echo ""

# Escape payload for inclusion in JSON. Use jq to be safe.
if command -v jq >/dev/null; then
    DIAG_JSON=$(jq -Rs . <<< "$DIAG_PAYLOAD")
else
    # Fallback: naïve replace — OK for this operational path.
    DIAG_JSON=\"$(printf '%s' "$DIAG_PAYLOAD" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr '\n' ' ')\"
fi

pg <<SQL
UPDATE substrate_violations
   SET details = details || jsonb_build_object(
       'recovery_diagnostics', $DIAG_JSON::jsonb,
       'recovery_source', '$DIAG_SOURCE',
       'recovery_attempted_at', NOW()::text,
       'recovery_actor', '$ACTOR'
   )
 WHERE invariant_name = 'installed_but_silent'
   AND resolved_at IS NULL
   AND details->>'mac_address' = '$MAC';

INSERT INTO admin_audit_log (username, action, target, details, created_at)
VALUES (
    '$ACTOR',
    'APPLIANCE_RECOVERY_DIAGNOSTIC',
    'appliance:$MAC',
    jsonb_build_object(
        'site_id', '$SITE_ID',
        'mac_address', '$MAC',
        'recovery_source', '$DIAG_SOURCE',
        'payload_preview', substring($DIAG_JSON::text, 1, 500)
    ),
    NOW()
);
SQL

echo "[$(date -u +%H:%M:%SZ)] recovery: diagnostics attached to substrate_violations + admin_audit_log"
