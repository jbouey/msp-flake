#!/usr/bin/env python3
"""rescue_appliance.py — one-command appliance auth rescue.

Action item #5 from the 2026-04-23 post-mortem (docs/postmortems/
2026-04-23-v40-appliance-brick-class.md).

Replaces the hand-crafted `psql -c "INSERT INTO api_keys ..."` pattern
used during that incident. Any engineer can now rescue a wedged
appliance with one command:

    python3 scripts/rescue_appliance.py <mac>

The script SSHes into the appliance, reads the daemon's live
config.yaml top-level api_key (the value the Go daemon actually uses
per its flat Config struct unmarshal), computes sha256, and issues an
audited INSERT into api_keys with active=true. Migration 209's trigger
auto-deactivates the prior active row. Next daemon checkin cycle (60s)
returns 200.

Ops discipline:
  * Dry-run by default. --apply to execute.
  * --reason is required when --apply is set (>=20 chars) — written
    to description + admin_audit_log.
  * Idempotent: if the current config.yaml hash ALREADY matches an
    active api_keys row, the script exits 0 without writing.
  * Every rescue writes a structured admin_audit_log entry via the
    Migration 209 trigger on api_keys INSERT. The trigger fires
    regardless of SQL client, so this script's writes appear in
    the standard audit trail — no parallel pipeline.

Security posture:
  * No raw keys are printed in stdout; only prefixes + hashes.
  * --reason goes to admin_audit_log. Empty or <20-char reason with
    --apply is rejected.
  * Requires VPS SSH (for psql) + appliance SSH (for config.yaml).
    Caller must have both; script fails loud if either is missing.

Usage:
    python3 rescue_appliance.py 84:3A:5B:1D:0F:E5                    # dry-run
    python3 rescue_appliance.py 7C:D3:0A:7C:55:18 --apply \\
        --reason "daemon wedged post-rekey rate-limit, v40.7 reflash in 24h"

Exit codes:
  0  — rescue not needed (already in sync) OR rescue applied cleanly
  1  — cannot reach appliance via SSH (network / key auth fail)
  2  — cannot read config.yaml (appliance daemon hasn't provisioned)
  3  — cannot reach VPS Postgres
  4  — INSERT failed (permissions / trigger / constraint)
  5  — --apply without valid --reason
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from typing import Tuple


VPS_SSH = "root@178.156.162.116"
PG_CONTAINER = "mcp-postgres"
DB_USER = "mcp"
DB_NAME = "mcp"
DEFAULT_SITE_ID = "north-valley-branch-2"  # override via --site-id


def _run(cmd: list[str], *, input: str | None = None) -> Tuple[int, str, str]:
    """Execute a subprocess; return (rc, stdout, stderr)."""
    p = subprocess.run(cmd, input=input, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def _ssh_appliance(ip: str, command: str) -> Tuple[int, str, str]:
    """SSH into an appliance as msp (ed25519 key-auth) and run a command."""
    return _run([
        "ssh",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=8",
        "-i", "/Users/dad/.ssh/id_ed25519",
        f"msp@{ip}",
        command,
    ])


def _psql(sql: str) -> Tuple[int, str, str]:
    """Run a SQL statement against the VPS Postgres via docker exec."""
    return _run([
        "ssh", VPS_SSH,
        f"docker exec {PG_CONTAINER} psql -U {DB_USER} -d {DB_NAME} -tAc {_shell_quote(sql)}",
    ])


def _shell_quote(s: str) -> str:
    """Quote a string for bash single-quote context."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _normalize_mac(raw: str) -> str:
    """Normalize MAC to uppercase colon-separated AA:BB:CC:DD:EE:FF."""
    clean = raw.upper().replace(":", "").replace("-", "").replace(".", "")
    if len(clean) != 12 or not re.fullmatch(r"[0-9A-F]{12}", clean):
        raise ValueError(f"invalid MAC: {raw!r}")
    return ":".join(clean[i:i + 2] for i in range(0, 12, 2))


def _ip_from_mac(mac: str) -> str:
    """Resolve MAC → IP via `arp -an`. Caller's responsibility to be on the LAN."""
    rc, out, _ = _run(["arp", "-an"])
    if rc != 0:
        raise RuntimeError("arp -an failed — are you on the appliance LAN?")
    mac_short = mac.lower().lstrip("0").replace(":0", ":")
    # arp on macOS prints "? (IP) at MAC on en0 ifscope [ethernet]"
    # MACs are printed without leading zeros per octet. Try both forms.
    mac_variants = {mac.lower(), mac_short, mac.lower().replace("0", "", 1)}
    for line in out.splitlines():
        for v in mac_variants:
            if v in line.lower():
                m = re.search(r"\(([0-9.]+)\)", line)
                if m:
                    return m.group(1)
    raise RuntimeError(f"MAC {mac} not found in arp table. Is the box on this LAN?")


def read_daemon_config_key(ip: str) -> str:
    """Return the TOP-LEVEL api_key string from the daemon's config.yaml.

    The Go daemon unmarshals against a flat Config struct, so it reads the
    top-level fields (not the legacy nested `config:` block present in pre-v40.6
    config.yaml files). Always read top-level for consistency with the running
    daemon.

    Strategy: SSH+cat the whole file, parse in Python. Avoids shell-escape
    hell with `tr -d` / `awk` inside a double-quoted ssh arg.
    """
    rc, out, err = _ssh_appliance(ip, "sudo -n cat /var/lib/msp/config.yaml")
    if rc != 0:
        raise RuntimeError(f"config.yaml read failed (exit {rc}): {err.strip()}")
    # Walk line by line. Top-level api_key has NO leading whitespace.
    # Nested one (under `config:`) has 2+ spaces of indent — skip those.
    for line in out.splitlines():
        if not line.startswith("api_key:"):
            continue
        # `api_key: VALUE` — split once on ":" to handle values containing "="
        value = line.split(":", 1)[1].strip()
        # Strip optional YAML quotes around the value
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        if value:
            return value
    raise RuntimeError("no top-level api_key line found in config.yaml")


def fetch_active_key_hash(appliance_id: str) -> str | None:
    """Return the currently-active api_keys.key_hash for this appliance, or None."""
    rc, out, err = _psql(
        f"SELECT key_hash FROM api_keys "
        f"WHERE appliance_id = '{appliance_id}' AND active = true LIMIT 1"
    )
    if rc != 0:
        raise RuntimeError(f"psql query failed: {err.strip()}")
    return out.strip() or None


def apply_rescue(site_id: str, appliance_id: str, raw_key: str, reason: str) -> None:
    """INSERT an active api_keys row whose hash matches the daemon's current key.

    Migration 209's trigger auto-deactivates the prior active row for this
    appliance + writes the admin_audit_log entry. This function does not need
    to touch admin_audit_log directly.
    """
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    prefix = raw_key[:8]
    description = f"Rescue CLI 2026-04-24+: {reason}"
    # Escape single quotes in the description for SQL safety
    description_esc = description.replace("'", "''")
    sql = (
        "INSERT INTO api_keys "
        "(site_id, appliance_id, key_hash, key_prefix, description, active, created_at) "
        f"VALUES ('{site_id}', '{appliance_id}', '{key_hash}', '{prefix}', "
        f"'{description_esc}', true, NOW());"
    )
    rc, out, err = _psql(sql)
    if rc != 0:
        raise RuntimeError(f"INSERT failed: {err.strip()}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rescue an appliance's auth state by syncing api_keys.active with its live config.yaml.",
    )
    parser.add_argument("mac", help="Appliance MAC address (AA:BB:CC:DD:EE:FF or compact)")
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually run the INSERT. Without this, dry-run only.",
    )
    parser.add_argument(
        "--reason", default="",
        help="Required with --apply. >=20 chars. Written to description + audit log.",
    )
    parser.add_argument(
        "--site-id", default=DEFAULT_SITE_ID,
        help=f"Site ID (default: {DEFAULT_SITE_ID}).",
    )
    parser.add_argument(
        "--ip", default=None,
        help="Appliance IP (default: resolved from MAC via arp -an).",
    )
    args = parser.parse_args()

    if args.apply:
        if len(args.reason.strip()) < 20:
            print("ERROR: --apply requires --reason with >=20 characters "
                  "(audit trail requirement).", file=sys.stderr)
            return 5

    try:
        mac = _normalize_mac(args.mac)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 5

    appliance_id = f"{args.site_id}-{mac}"
    print(f"MAC:          {mac}")
    print(f"Appliance:    {appliance_id}")

    ip = args.ip or _ip_from_mac(mac)
    print(f"IP:           {ip}")

    try:
        key = read_daemon_config_key(ip)
    except RuntimeError as e:
        print(f"ERROR reading config.yaml: {e}", file=sys.stderr)
        return 2
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    print(f"config.yaml:  prefix={key[:8]} hash={key_hash[:16]}...")

    try:
        active = fetch_active_key_hash(appliance_id)
    except RuntimeError as e:
        print(f"ERROR querying api_keys: {e}", file=sys.stderr)
        return 3
    print(f"DB active:    {active[:16] + '...' if active else '(none)'}")

    if active == key_hash:
        print("OK: active api_keys row already matches daemon's config.yaml. No rescue needed.")
        return 0

    print("DRIFT DETECTED: daemon's config.yaml key does NOT match the active api_keys row.")
    if not args.apply:
        print("(dry-run — pass --apply with --reason to fix)")
        return 0

    print(f"Applying rescue — reason: {args.reason!r}")
    try:
        apply_rescue(args.site_id, appliance_id, key, args.reason)
    except RuntimeError as e:
        print(f"ERROR applying rescue: {e}", file=sys.stderr)
        return 4

    # Verify
    new_active = fetch_active_key_hash(appliance_id)
    if new_active == key_hash:
        print(f"SUCCESS: api_keys now active with hash {key_hash[:16]}... — "
              f"daemon's next checkin (within 60s) should return 200.")
        return 0
    else:
        print(f"UNEXPECTED: INSERT returned success but active key hash is "
              f"{(new_active or 'NULL')[:16]} — investigate manually.",
              file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
