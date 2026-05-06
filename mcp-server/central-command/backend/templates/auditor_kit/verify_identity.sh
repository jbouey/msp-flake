#!/usr/bin/env bash
# verify_identity.sh — Auditor Kit v2.
# Recomputes the chain_hash of every claim event in identity_chain.json
# and compares to the stored value. Drift = tampering.
#
# Requires: jq, sha256sum (or shasum on macOS).
set -euo pipefail
KIT_DIR="${1:-.}"
IDENTITY="$KIT_DIR/identity_chain.json"
[ -f "$IDENTITY" ] || { echo "missing $IDENTITY"; exit 2; }

# Pick the right sha256 cli for the OS.
if command -v sha256sum >/dev/null 2>&1; then
    SHA() { sha256sum | awk '{print $1}'; }
else
    SHA() { shasum -a 256 | awk '{print $1}'; }
fi

GENESIS=$(printf '0%.0s' {1..64})
events_count=$(jq '.events | length' "$IDENTITY")
echo "Verifying $events_count claim event(s) from $IDENTITY ..."
fail=0
prev_expected="$GENESIS"
for i in $(seq 0 $((events_count - 1))); do
    ev=$(jq ".events[$i]" "$IDENTITY")

    # Build canonical event JSON the same way the server does:
    # sort_keys=True, separators=(',',':'). jq's `--sort-keys -c`
    # produces exactly that.
    canonical=$(jq --sort-keys -c '{
        agent_pubkey_hex: (.agent_pubkey_hex | ascii_downcase),
        claim_event_id:   .claim_event_id,
        claimed_at:       .claimed_at,
        iso_release_sha:  (.iso_release_sha // ""),
        mac_address:      (.mac_address | ascii_upcase),
        site_id:          ($SITE),
        source:           .source
    }' --arg SITE "$(jq -r .site_id "$IDENTITY")" <<< "$ev")

    prev=$(jq -r .chain_prev_hash <<< "$ev")
    stored=$(jq -r .chain_hash <<< "$ev")

    expected=$(printf "%s:%s" "$prev" "$canonical" | SHA)

    if [ "$expected" != "$stored" ]; then
        echo "  FAIL idx=$i  expected=$expected  stored=$stored"
        fail=$((fail + 1))
    elif [ "$prev" != "$prev_expected" ]; then
        echo "  FAIL idx=$i  chain break — prev=$prev  expected_prev=$prev_expected"
        fail=$((fail + 1))
    fi
    prev_expected="$stored"
done

if [ "$fail" -eq 0 ]; then
    echo "[PASS] all $events_count claim events recompute + chain to genesis"
else
    echo "[FAIL] $fail of $events_count failed"
    exit 1
fi
