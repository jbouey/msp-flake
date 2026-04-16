#!/usr/bin/env bash
#
# recover_legacy_appliance.sh — operator-driven recovery for an
# appliance whose daemon is too old to auto-rekey (< v0.3.84).
#
# Run from any host that can:
#   * reach https://api.osiriscare.net (to mint the new key)
#   * SSH to the appliance as msp@<ip> (MAC-derived password)
#
# Idempotent: re-running on the same MAC mints a fresh key and
# pushes it again. The api_keys trigger (Migration 209) ensures
# only the newest key is active.
#
# Usage:
#   recover_legacy_appliance.sh <site_id> <mac_address> <ip_address>
#
# Example:
#   recover_legacy_appliance.sh north-valley-branch-2 \
#                               84:3A:5B:1D:0F:E5 \
#                               192.168.88.227

set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "Usage: $0 <site_id> <mac_address> <ip_address>" >&2
  exit 2
fi

SITE_ID="$1"
MAC="$2"
IP="$3"

API="${API_BASE:-https://api.osiriscare.net}"

# 1. Compute the MAC-derived emergency password (8-char hex) that
#    matches msp-first-boot.service in iso/appliance-image.nix.
MAC_LOWER=$(echo "$MAC" | tr 'A-Z' 'a-z')
HASH=$(echo -n "osiriscare-emergency-${MAC_LOWER}" | sha256sum | cut -c1-8)
EMERGENCY_PASS="osiris-${HASH}"

echo "[recover] site=${SITE_ID} mac=${MAC} ip=${IP}"
echo "[recover] derived emergency password: ${EMERGENCY_PASS}"

# 2. Mint a fresh appliance-specific api_key by hitting the rekey
#    endpoint. Trust model: MAC + site_id (already on-record).
echo "[recover] requesting new api_key from ${API}..."
REKEY_JSON=$(curl --fail-with-body -sS -X POST \
  -H "Content-Type: application/json" \
  -d "{\"site_id\":\"${SITE_ID}\",\"mac_address\":\"${MAC}\"}" \
  "${API}/api/provision/rekey")

NEW_KEY=$(echo "$REKEY_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['api_key'])")
if [ -z "$NEW_KEY" ]; then
  echo "[recover] rekey response did not contain api_key:" >&2
  echo "$REKEY_JSON" >&2
  exit 1
fi
echo "[recover] new api_key minted (prefix: ${NEW_KEY:0:8}...)"

# 3. Push the new key into the appliance's config.yaml and restart
#    the daemon. The yq replace is idempotent — re-running just
#    overwrites the same field.
#
# The appliance's `msp` user has wheelNeedsPassword=true (HIPAA
# requirement in appliance-disk-image.nix). We pipe the emergency
# password to sudo -S so the script remains non-interactive and
# zero-friction.
echo "[recover] SSHing to msp@${IP} to push new key..."
sshpass -p "${EMERGENCY_PASS}" ssh \
  -o StrictHostKeyChecking=accept-new \
  -o ConnectTimeout=10 \
  msp@"${IP}" \
  "echo '${EMERGENCY_PASS}' | sudo -S /run/current-system/sw/bin/bash -c '
    set -e
    cd /var/lib/msp
    cp config.yaml config.yaml.bak.\$(date -u +%Y%m%dT%H%M%SZ)
    yq -i \".api_key = \\\"${NEW_KEY}\\\"\" config.yaml
    chmod 600 config.yaml
    systemctl restart appliance-daemon
    echo \"[recover] daemon restarted\"
  '"

# 4. Wait briefly + verify auth_failure_count cleared. The first
#    successful checkin clears the counter via shared.py's
#    success-clear path.
echo "[recover] waiting 90s for first checkin to land..."
sleep 90

if [ -n "${PSQL_DSN:-}" ]; then
  echo "[recover] verifying auth_failure_count cleared..."
  psql "$PSQL_DSN" -tAc \
    "SELECT mac_address, status, auth_failure_count, agent_version, last_checkin
       FROM site_appliances WHERE mac_address='${MAC}';"
fi

echo "[recover] done. Check the substrate-health panel for green."
