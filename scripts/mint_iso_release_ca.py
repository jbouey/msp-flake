#!/usr/bin/env python3
"""mint_iso_release_ca.py — Week 2 of the composed identity stack.

Mints a per-release Ed25519 CA keypair for the OsirisCare installer
ISO. The private half is consumed by the build pipeline to sign the
embedded claim certificate and is then expected to live in a
short-lived secret store (GitHub Actions secret, sealed-secrets vault,
etc) — never to land in any artifact or git tree. The public half is
registered in iso_release_ca_pubkeys (Migration 210) so the backend
will accept first-boot CSRs signed by the matching private half.

This script is deliberately simple and self-contained: stdlib +
cryptography. No DB connection — emits a SQL fragment the pipeline
runs separately. That keeps the secret-handling boundary at the
build pipeline, where it belongs.

Usage:
    python3 scripts/mint_iso_release_ca.py \\
        --iso-release-sha <git-sha> \\
        --valid-days 90 \\
        --out-dir build/iso-ca

Outputs:
    build/iso-ca/<sha>.key      — 32-byte Ed25519 seed (HEX), 0600
    build/iso-ca/<sha>.pub      — 32-byte Ed25519 pubkey (HEX)
    build/iso-ca/<sha>.cert     — claim cert signed by <sha>.key,
                                  to embed in /etc/installer/claim.cert
    build/iso-ca/<sha>.sql      — INSERT INTO iso_release_ca_pubkeys ...
                                  for the deploy step to apply.

The claim cert is a JSON document of:
    {
        "iso_release_sha": "<sha>",
        "ca_pubkey_hex":   "<hex>",
        "issued_at":       "<RFC3339>",
        "valid_until":     "<RFC3339>",
        "version":         1
    }
plus a base64url Ed25519 signature over the canonical JSON
(sort_keys=True, separators=(',', ':')) by the CA private key.
The daemon validates this cert against the embedded ca_pubkey at
first boot and uses it to sign its CSR.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    PrivateFormat,
    NoEncryption,
)


def canonical_cert_json(payload: dict) -> bytes:
    """Same canonicalization the daemon will apply when verifying."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def fingerprint_of(pub_hex: str) -> str:
    return hashlib.sha256(bytes.fromhex(pub_hex)).hexdigest()[:16]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iso-release-sha", required=True,
                   help="git SHA of the source tree this ISO was built from (40 hex chars)")
    p.add_argument("--valid-days", type=int, default=90,
                   help="how long the claim cert is valid for (default 90 days)")
    p.add_argument("--out-dir", required=True,
                   help="directory to write key/pub/cert/sql artifacts")
    p.add_argument("--reuse-existing", action="store_true",
                   help="if the .key file already exists, reuse it (CI re-runs)")
    args = p.parse_args()

    sha = args.iso_release_sha.lower()
    if len(sha) != 40 or not all(c in "0123456789abcdef" for c in sha):
        print(f"--iso-release-sha must be 40 hex chars, got {sha!r}", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    key_path = out_dir / f"{sha}.key"
    pub_path = out_dir / f"{sha}.pub"
    cert_path = out_dir / f"{sha}.cert"
    sql_path = out_dir / f"{sha}.sql"

    if key_path.exists() and not args.reuse_existing:
        print(f"refusing to overwrite existing {key_path} — pass --reuse-existing", file=sys.stderr)
        return 2

    # 1. Mint or load CA keypair.
    if key_path.exists():
        seed = bytes.fromhex(key_path.read_text().strip())
        priv = Ed25519PrivateKey.from_private_bytes(seed)
    else:
        priv = Ed25519PrivateKey.generate()
        seed = priv.private_bytes(
            Encoding.Raw, PrivateFormat.Raw, NoEncryption(),
        )
        key_path.write_text(seed.hex() + "\n")
        os.chmod(key_path, 0o600)

    pub = priv.public_key()
    pub_bytes = pub.public_bytes(Encoding.Raw, PublicFormat.Raw)
    pub_hex = pub_bytes.hex()
    pub_path.write_text(pub_hex + "\n")
    os.chmod(pub_path, 0o644)

    # 2. Build claim cert.
    issued_at = datetime.now(timezone.utc).replace(microsecond=0)
    valid_until = issued_at + timedelta(days=args.valid_days)

    payload = {
        "iso_release_sha": sha,
        "ca_pubkey_hex": pub_hex,
        "issued_at": issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "valid_until": valid_until.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": 1,
    }
    canonical = canonical_cert_json(payload)
    sig = priv.sign(canonical)
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")

    cert_doc = {
        "payload": payload,
        "signature_b64": sig_b64,
        "algorithm": "ed25519",
    }
    cert_path.write_text(json.dumps(cert_doc, indent=2) + "\n")
    os.chmod(cert_path, 0o644)

    # 3. Emit SQL for the deploy step. Note: we do NOT execute it here;
    # the build pipeline pipes this into psql via the deploy job that
    # already has DB credentials.
    sql = f"""\
-- Auto-generated by mint_iso_release_ca.py at {issued_at.isoformat()}
INSERT INTO iso_release_ca_pubkeys
    (iso_release_sha, ca_pubkey_hex, valid_from, valid_until, notes)
VALUES
    ('{sha}',
     '{pub_hex}',
     '{issued_at.isoformat()}'::timestamptz,
     '{valid_until.isoformat()}'::timestamptz,
     '{{"fingerprint": "{fingerprint_of(pub_hex)}"}}'::jsonb)
ON CONFLICT (iso_release_sha) DO UPDATE
    SET ca_pubkey_hex = EXCLUDED.ca_pubkey_hex,
        valid_until   = EXCLUDED.valid_until,
        notes         = EXCLUDED.notes;
"""
    sql_path.write_text(sql)
    os.chmod(sql_path, 0o644)

    # 4. Output a friendly summary so the build log shows what landed.
    print(f"Minted ISO release CA for sha={sha}")
    print(f"  pubkey_hex  : {pub_hex}")
    print(f"  fingerprint : {fingerprint_of(pub_hex)}")
    print(f"  valid_until : {valid_until.isoformat()}")
    print(f"  artifacts   :")
    print(f"    {key_path}     (PRIVATE — do not commit)")
    print(f"    {pub_path}")
    print(f"    {cert_path}    (embed at /etc/installer/claim.cert)")
    print(f"    {sql_path}     (apply via psql in deploy step)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
