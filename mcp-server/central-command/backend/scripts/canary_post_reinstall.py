"""Post-reinstall canary: fire a nixos_rebuild admin_order at a
freshly-reinstalled appliance and let the substrate report the outcome.

Usage:
  python3 canary_post_reinstall.py <MAC>
  python3 canary_post_reinstall.py <MAC> <SITE_ID>

Examples:
  python3 canary_post_reinstall.py 7C:D3:0A:7C:55:18
  python3 canary_post_reinstall.py 84:3A:5B:1D:0F:E5 north-valley-branch-2

Pass criteria: admin_order completes with status='completed' and no
error_message. See docs/APPLIANCE_REINSTALL_V39_RUNBOOK.md §Step 6 for
the full pass/fail matrix.

Intended to run INSIDE the mcp-server container (has DATABASE_URL +
signing key mount). Typical invocation:

  docker cp canary_post_reinstall.py mcp-server:/tmp/canary.py
  docker exec mcp-server python3 /tmp/canary.py 7C:D3:0A:7C:55:18
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/dashboard_api")

import asyncpg  # type: ignore[import]
import main as main_mod  # type: ignore[import]
main_mod.load_or_create_signing_key()
from dashboard_api.order_signing import sign_admin_order  # type: ignore[import]


DEFAULT_SITE_ID = "north-valley-branch-2"
ORDER_TYPE = "nixos_rebuild"
FLAKE_REF = "github:jbouey/msp-flake#osiriscare-appliance-disk"

MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")


def _parse_args(argv: list[str]) -> tuple[str, str]:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(2)
    mac = argv[1].upper()
    if not MAC_RE.match(mac):
        print(f"error: MAC must be colon-separated hex (got {argv[1]!r})",
              file=sys.stderr)
        sys.exit(2)
    site_id = argv[2] if len(argv) > 2 else DEFAULT_SITE_ID
    return mac, site_id


async def _main() -> int:
    mac, site_id = _parse_args(sys.argv)
    target_appliance = f"{site_id}-{mac}"
    order_id = f"post-reinstall-canary-{secrets.token_hex(6)}"
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=2)
    parameters = {"flake_ref": FLAKE_REF}

    nonce, signature, signed_payload = sign_admin_order(
        order_id=order_id,
        order_type=ORDER_TYPE,
        parameters=parameters,
        created_at=now,
        expires_at=expires,
        target_appliance_id=target_appliance,
    )

    dsn = os.environ["DATABASE_URL"].replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL app.is_admin = 'true'")
            row = await conn.fetchrow(
                """
                INSERT INTO admin_orders
                    (order_id, order_type, parameters, status,
                     appliance_id, site_id, created_at, expires_at,
                     created_by, nonce, signature, signed_payload)
                VALUES
                    ($1, $2, $3::jsonb, 'pending',
                     $4, $5, $6, $7,
                     $8, $9, $10, $11)
                RETURNING id, order_id
                """,
                order_id, ORDER_TYPE, json.dumps(parameters),
                target_appliance, site_id, now, expires,
                f"post-reinstall-canary-{now.date().isoformat()}",
                nonce, signature, signed_payload,
            )
            print(
                f"OK inserted id={row['id']} order_id={row['order_id']} "
                f"target={target_appliance}"
            )
            print(
                "Monitor with:\n"
                f"  docker exec mcp-postgres psql -U mcp -d mcp -c "
                f"\"SELECT status, completed_at, LEFT(error_message, 400) "
                f"FROM admin_orders WHERE order_id='{row['order_id']}';\""
            )
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
