#!/usr/bin/env python3
"""Fleet order CLI — create, list, and cancel fleet orders.

Runs inside the mcp-server Docker container. Handles Ed25519 signing
and direct database insertion in one step.

Usage:
    python3 fleet_cli.py create nixos_rebuild
    python3 fleet_cli.py create update_daemon --param binary_url=https://... --param binary_sha256=abc --param version=0.3.14
    python3 fleet_cli.py create diagnostic --param command=agent_status
    python3 fleet_cli.py create force_checkin --expires 1
    python3 fleet_cli.py list
    python3 fleet_cli.py list --status active
    python3 fleet_cli.py cancel <order-uuid>
"""

import argparse
import asyncio
import json
import os
import re
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import asyncpg
except ImportError:
    sys.exit("asyncpg not installed. Run: pip install asyncpg")

try:
    from nacl.signing import SigningKey
    from nacl.encoding import HexEncoder
except ImportError:
    sys.exit("PyNaCl not installed. Run: pip install PyNaCl")


SIGNING_KEY_FILE = Path(os.getenv("SIGNING_KEY_FILE", "/app/secrets/signing.key"))

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://mcp:mcp@mcp-postgres:5432/mcp")
# Strip SQLAlchemy dialect prefix if present
DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

VALID_ORDER_TYPES = {
    "force_checkin", "run_drift", "sync_rules", "restart_agent",
    "nixos_rebuild", "update_agent", "update_iso", "view_logs",
    "diagnostic", "deploy_sensor", "remove_sensor",
    "deploy_linux_sensor", "remove_linux_sensor", "sensor_status",
    "sync_promoted_rule", "healing", "update_credentials", "update_daemon",
    "configure_workstation_agent", "validate_credential",
}

DEFAULT_PARAMS = {
    "nixos_rebuild": {"flake_ref": "github:jbouey/msp-flake#osiriscare-appliance-disk"},
}

REQUIRED_PARAMS = {
    "update_daemon": ["binary_url", "binary_sha256", "version"],
    "diagnostic": ["command"],
}

_HEX_RE = re.compile(r"^[0-9a-f]{128}$")


def load_signing_key() -> SigningKey:
    if not SIGNING_KEY_FILE.exists():
        sys.exit(f"Signing key not found at {SIGNING_KEY_FILE}")
    key_hex = SIGNING_KEY_FILE.read_bytes().strip()
    return SigningKey(key_hex, encoder=HexEncoder)


def sign_order(
    signing_key: SigningKey,
    order_type: str,
    parameters: dict,
    created_at: datetime,
    expires_at: datetime,
) -> tuple[str, str, str]:
    """Sign a fleet order. Returns (nonce, signature, signed_payload)."""
    nonce = secrets.token_hex(16)
    payload_dict = {
        "order_id": "0",
        "order_type": order_type,
        "parameters": parameters,
        "nonce": nonce,
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    signed_payload = json.dumps(payload_dict, sort_keys=True)
    signature = signing_key.sign(signed_payload.encode()).signature.hex()
    if not _HEX_RE.match(signature):
        sys.exit(f"Signature validation failed: got {len(signature)} chars")
    return nonce, signature, signed_payload


def parse_params(param_list: list[str] | None) -> dict:
    """Parse --param KEY=VALUE arguments into a dict."""
    if not param_list:
        return {}
    result = {}
    for p in param_list:
        key, sep, value = p.partition("=")
        if not sep:
            sys.exit(f"Invalid --param format: {p!r} (expected KEY=VALUE)")
        result[key] = value
    return result


async def cmd_create(args: argparse.Namespace) -> None:
    order_type = args.order_type
    if order_type not in VALID_ORDER_TYPES:
        sys.exit(
            f"Unknown order type: {order_type!r}\n"
            f"Valid types: {', '.join(sorted(VALID_ORDER_TYPES))}"
        )

    params = DEFAULT_PARAMS.get(order_type, {}).copy()
    params.update(parse_params(args.param))

    if order_type in REQUIRED_PARAMS:
        missing = [k for k in REQUIRED_PARAMS[order_type] if k not in params]
        if missing:
            sys.exit(
                f"Order type {order_type!r} requires: {', '.join(missing)}\n"
                f"Use: --param {missing[0]}=VALUE"
            )

    # Validate binary_url for update_daemon orders
    if order_type == "update_daemon":
        url = params.get("binary_url", "")
        if not url.startswith("https://"):
            sys.exit(
                f"binary_url must be HTTPS (got: {url!r})\n"
                f"Upload binary to VPS and use: https://api.osiriscare.net/updates/<filename>"
            )

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=args.expires)

    signing_key = load_signing_key()
    nonce, signature, signed_payload = sign_order(
        signing_key, order_type, params, now, expires_at
    )

    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO fleet_orders
                (order_type, parameters, skip_version, status, expires_at,
                 created_by, nonce, signature, signed_payload)
            VALUES ($1, $2::jsonb, $3, 'active', $4, $5, $6, $7, $8)
            RETURNING id, order_type, status, created_at, expires_at
            """,
            order_type,
            json.dumps(params),
            args.skip_version,
            expires_at,
            "fleet-cli",
            nonce,
            signature,
            signed_payload,
        )
        print(f"Fleet order created:")
        print(f"  ID:         {row['id']}")
        print(f"  Type:       {row['order_type']}")
        print(f"  Status:     {row['status']}")
        print(f"  Parameters: {json.dumps(params)}")
        print(f"  Created:    {row['created_at'].isoformat()}")
        print(f"  Expires:    {row['expires_at'].isoformat()}")
        if args.skip_version:
            print(f"  Skip ver:   {args.skip_version}")
    finally:
        await conn.close()


async def cmd_list(args: argparse.Namespace) -> None:
    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
    try:
        rows = await conn.fetch(
            """
            SELECT fo.id, fo.order_type, fo.parameters, fo.skip_version,
                   fo.status, fo.created_at, fo.expires_at, fo.created_by,
                   (SELECT COUNT(*) FROM fleet_order_completions c
                    WHERE c.fleet_order_id = fo.id AND c.status = 'completed') as done,
                   (SELECT COUNT(*) FROM fleet_order_completions c
                    WHERE c.fleet_order_id = fo.id AND c.status = 'failed') as fail,
                   (SELECT COUNT(*) FROM fleet_order_completions c
                    WHERE c.fleet_order_id = fo.id AND c.status = 'skipped') as skip
            FROM fleet_orders fo
            WHERE ($1::text IS NULL OR fo.status = $1)
            ORDER BY fo.created_at DESC
            LIMIT $2
            """,
            args.status,
            args.limit,
        )

        if not rows:
            print("No fleet orders found.")
            return

        # Header
        print(f"{'ID':<38} {'TYPE':<18} {'STATUS':<12} {'CREATED':<22} {'EXPIRES':<22} {'BY':<12} {'D':>2} {'F':>2} {'S':>2}")
        print("-" * 132)
        for r in rows:
            oid = str(r["id"])[:36]
            created = r["created_at"].strftime("%Y-%m-%d %H:%M UTC")
            expires = r["expires_at"].strftime("%Y-%m-%d %H:%M UTC")
            by = (r["created_by"] or "")[:11]
            print(
                f"{oid:<38} {r['order_type']:<18} {r['status']:<12} "
                f"{created:<22} {expires:<22} {by:<12} "
                f"{r['done']:>2} {r['fail']:>2} {r['skip']:>2}"
            )
    finally:
        await conn.close()


async def cmd_cancel(args: argparse.Namespace) -> None:
    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
    try:
        result = await conn.execute(
            "UPDATE fleet_orders SET status = 'cancelled' WHERE id = $1 AND status = 'active'",
            args.order_id,
        )
        if result == "UPDATE 1":
            print(f"Cancelled order {args.order_id}")
        else:
            print(f"No active order found with ID {args.order_id}")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fleet order CLI — create, list, and cancel fleet orders."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = sub.add_parser("create", help="Create a new fleet order")
    p_create.add_argument("order_type", help="Order type (e.g. nixos_rebuild, update_daemon)")
    p_create.add_argument("--param", action="append", help="Parameter KEY=VALUE (repeatable)")
    p_create.add_argument("--expires", type=int, default=24, help="Expiry in hours (default: 24)")
    p_create.add_argument("--skip-version", help="Skip appliances at this version")

    # list
    p_list = sub.add_parser("list", help="List fleet orders")
    p_list.add_argument("--status", choices=["active", "cancelled", "completed"], help="Filter by status")
    p_list.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")

    # cancel
    p_cancel = sub.add_parser("cancel", help="Cancel an active fleet order")
    p_cancel.add_argument("order_id", help="Fleet order UUID")

    args = parser.parse_args()

    import uuid as _uuid

    if args.command == "cancel":
        try:
            args.order_id = _uuid.UUID(args.order_id)
        except ValueError:
            sys.exit(f"Invalid UUID: {args.order_id}")

    handler = {"create": cmd_create, "list": cmd_list, "cancel": cmd_cancel}[args.command]
    asyncio.run(handler(args))


if __name__ == "__main__":
    main()
