#!/usr/bin/env bash
#
# recover_appliance_full.sh — atomic recovery for orphaned appliances.
#
# Drop-in replacement for `recover_legacy_appliance.sh` that ALSO handles
# the case where the `site_appliances` row was deleted (Session 210-B
# 2026-04-25 orphan class). Calls `/api/provision/admin/restore` instead
# of `/api/provision/rekey` — the admin endpoint UPSERTs the row before
# minting the key, so it works whether the row exists or not.
#
# Idempotent. Re-running mints a fresh key + the api_keys trigger
# auto-deactivates the prior one.
#
# Usage:
#   recover_appliance_full.sh <site_id> <mac> <ip> <reason>
#
# Example (today's orphan):
#   recover_appliance_full.sh \
#       physical-appliance-pilot-1aea78 \
#       84:3A:5B:91:B6:61 \
#       192.168.88.245 \
#       "Session 210-B cleanup over-deleted site_appliances row; restoring"
#
# Auth: ADMIN_TOKEN env var (admin Bearer token from /api/auth/login).
#       Get one with: curl -d '{"username":"admin","password":"..."}' \
#           https://api.osiriscare.net/api/auth/login | jq -r .token

set -euo pipefail

if [ "$#" -lt 4 ]; then
  echo "Usage: $0 <site_id> <mac_address> <ip_address> <reason>" >&2
  echo "" >&2
  echo "  reason must be ≥ 20 chars — written to admin_audit_log." >&2
  echo "  ADMIN_TOKEN env var must hold an admin Bearer token." >&2
  exit 2
fi

SITE_ID="$1"
MAC="$2"
IP="$3"
REASON="$4"
API="${API_BASE:-https://api.osiriscare.net}"

if [ -z "${ADMIN_TOKEN:-}" ]; then
  echo "[recover-full] ADMIN_TOKEN env var required" >&2
  echo "[recover-full]   Get one via: curl -sf -d '{\"username\":...}' $API/api/auth/login | jq -r .token" >&2
  exit 2
fi

if [ "${#REASON}" -lt 20 ]; then
  echo "[recover-full] reason must be ≥ 20 chars; got ${#REASON}" >&2
  exit 2
fi

# 1. Compute the MAC-derived emergency password (matches msp-first-boot.service
#    in iso/appliance-image.nix). 8-char hex prefix of sha256.
MAC_LOWER=$(echo "$MAC" | tr 'A-Z' 'a-z')
HASH=$(echo -n "osiriscare-emergency-${MAC_LOWER}" | sha256sum | cut -c1-8)
EMERGENCY_PASS="osiris-${HASH}"

echo "[recover-full] site=${SITE_ID} mac=${MAC} ip=${IP}"
echo "[recover-full] emergency password derived (prefix: ${EMERGENCY_PASS:0:12}...)"

# 2. Hit /api/provision/admin/restore. This UPSERTs the site_appliances
#    row if missing AND mints the key in a single transaction, atomically.
echo "[recover-full] requesting admin-restore from ${API}..."
REQ_BODY=$(python3 -c '
import json, sys
print(json.dumps({
    "site_id": sys.argv[1],
    "mac_address": sys.argv[2],
    "reason": sys.argv[3],
}))' "$SITE_ID" "$MAC" "$REASON")

RESP_JSON=$(curl --fail-with-body -sS -X POST \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -d "$REQ_BODY" \
  "${API}/api/provision/admin/restore")

NEW_KEY=$(echo "$RESP_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('api_key',''))")
ROW_CREATED=$(echo "$RESP_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print('yes' if d.get('row_was_created') else 'no')")
if [ -z "$NEW_KEY" ]; then
  echo "[recover-full] admin-restore did not return api_key:" >&2
  echo "$RESP_JSON" >&2
  exit 1
fi
echo "[recover-full] api_key minted (prefix: ${NEW_KEY:0:8}...) row_was_created=${ROW_CREATED}"

# 3. SSH to the appliance + push the key into config.yaml. Same flow as
#    recover_legacy_appliance.sh — sshpass with the MAC-derived emergency
#    password, sudo -S non-interactive, atomic config write via yq.
echo "[recover-full] SSHing to msp@${IP} to push new key..."
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
    echo \"[recover-full] daemon restarted\"
  '"

# 4. Wait + verify. The daemon's first checkin clears auth_failure_count
#    via shared.py's success-clear path AND populates last_checkin.
echo "[recover-full] waiting 90s for first checkin to land..."
sleep 90

if [ -n "${PSQL_DSN:-}" ]; then
  echo "[recover-full] verifying recovery..."
  psql "$PSQL_DSN" -tAc "
    SELECT mac_address, status, auth_failure_count, agent_version,
           to_char(last_checkin, 'HH24:MI:SS') AS last_checkin
      FROM site_appliances WHERE mac_address='${MAC}';
  "
fi

echo "[recover-full] done. Check the substrate-health panel for green."
