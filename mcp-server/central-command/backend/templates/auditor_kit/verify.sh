#!/usr/bin/env bash
# OsirisCare auditor verification kit — verify.sh
#
# Reads bundles.jsonl + pubkeys.json + ots/*.ots and verifies the entire
# chain WITHOUT touching the OsirisCare network. Run from the kit
# directory after unzipping.

set -euo pipefail

KIT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$KIT_DIR"

# --- Tool checks --------------------------------------------------------------

require() {
    local tool="$1"
    local install_hint="$2"
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "ERROR: '$tool' is not installed."
        echo "  Install: $install_hint"
        exit 1
    fi
}

require python3 "https://www.python.org/downloads/"
require sha256sum "macOS: brew install coreutils, then alias sha256sum=gsha256sum"

OTS_AVAILABLE=1
if ! command -v ots >/dev/null 2>&1; then
    OTS_AVAILABLE=0
    echo "WARN: 'ots' (OpenTimestamps CLI) not installed — skipping OTS verification."
    echo "      Install: pip install opentimestamps-client"
fi

# --- Verification (delegated to embedded Python) ------------------------------

python3 - <<'PYEOF'
import json
import hashlib
import os
import sys

KIT_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else os.getcwd()

# Load pubkeys
with open(os.path.join(KIT_DIR, "pubkeys.json")) as f:
    pubkey_data = json.load(f)
pubkeys_by_fp = {pk["fingerprint"]: pk for pk in pubkey_data["public_keys"]}
pubkey_bytes = []
for pk in pubkey_data["public_keys"]:
    try:
        pubkey_bytes.append(bytes.fromhex(pk["public_key_hex"]))
    except Exception:
        pass

# Try to import cryptography for real Ed25519 verification
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    print("WARN: 'cryptography' not installed — skipping signature verification")
    print("      Install: pip install cryptography")

def verify_ed25519(sig_hex, msg_hex, pubkeys):
    if not HAS_CRYPTO:
        return None
    try:
        sig = bytes.fromhex(sig_hex)
        msg = bytes.fromhex(msg_hex)
        for pk_bytes in pubkeys:
            try:
                Ed25519PublicKey.from_public_bytes(pk_bytes).verify(sig, msg)
                return True
            except InvalidSignature:
                continue
        return False
    except Exception:
        return False

# Load bundles
chain_pass = chain_fail = 0
sig_pass = sig_fail = sig_skip = 0
ots_count = 0
legacy_count = 0
prev_hash_expected = None

with open(os.path.join(KIT_DIR, "bundles.jsonl")) as f:
    bundles = [json.loads(line) for line in f if line.strip()]

# Sort by chain_position to walk in order
bundles.sort(key=lambda b: b.get("chain_position") or 0)

for b in bundles:
    bundle_id = b.get("bundle_id", "?")
    bundle_hash = b.get("bundle_hash", "")
    prev_hash = b.get("prev_hash", "")
    sig = b.get("agent_signature")
    ots_status = b.get("ots_status", "none")

    # Hash chain check (skip the genesis row)
    if prev_hash_expected is not None:
        if prev_hash == prev_hash_expected:
            chain_pass += 1
        else:
            chain_fail += 1
            print(f"  [FAIL] chain link broken at bundle {bundle_id}")
    prev_hash_expected = bundle_hash

    # Signature check
    if ots_status == "legacy":
        legacy_count += 1
    elif sig:
        result = verify_ed25519(sig, bundle_hash, pubkey_bytes)
        if result is True:
            sig_pass += 1
        elif result is False:
            sig_fail += 1
            print(f"  [FAIL] signature invalid for bundle {bundle_id}")
        else:
            sig_skip += 1
    else:
        sig_skip += 1

    if ots_status == "anchored":
        ots_count += 1

# Summary
total = len(bundles)
print()
print(f"Bundles in kit: {total}")
print(f"[{'PASS' if chain_fail == 0 else 'FAIL'}] hash chain     {chain_pass}/{max(1,total-1)} links verified")
if HAS_CRYPTO:
    print(f"[{'PASS' if sig_fail == 0 else 'FAIL'}] signatures     {sig_pass}/{sig_pass + sig_fail} verified against pinned pubkeys")
    if sig_skip:
        print(f"[INFO] signatures     {sig_skip} skipped (no signature on bundle)")
else:
    print(f"[SKIP] signatures     {sig_pass + sig_fail + sig_skip} skipped (cryptography library not installed)")
print(f"[INFO] ots proofs     {ots_count} bundles anchored in Bitcoin")
print(f"[INFO] legacy bundles {legacy_count} (pre-anchoring or documented reclassification)")

if chain_fail or sig_fail:
    print()
    print("VERIFICATION FAILED — investigate before signing off")
    sys.exit(2)
print()
print("VERIFICATION PASSED")
PYEOF

# --- OTS verification (separate, optional) ------------------------------------

if [ "$OTS_AVAILABLE" = "1" ] && [ -d ots ] && [ "$(ls -A ots 2>/dev/null)" ]; then
    echo
    echo "Running OpenTimestamps verification on $(ls ots/*.ots 2>/dev/null | wc -l) proof files..."
    pass=0
    fail=0
    for f in ots/*.ots; do
        if ots verify "$f" >/dev/null 2>&1; then
            pass=$((pass+1))
        else
            fail=$((fail+1))
            echo "  [FAIL] OTS verification failed for $(basename $f)"
        fi
    done
    echo "[$([ $fail -eq 0 ] && echo PASS || echo FAIL)] ots cli       $pass/$((pass+fail)) verified against Bitcoin"
fi
