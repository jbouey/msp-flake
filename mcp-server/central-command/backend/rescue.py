"""
Rescue endpoint: one-command recovery for appliances stuck in the
msp-auto-provision sig-verify loop.

Root cause (Session 206, 2026-04-14): `/etc/msp/central-command.pub` +
server keys match, but the installer's inline Python signature verify
imports `from nacl.signing import VerifyKey`, and `pynacl` is not in
the installed system's global Python packages. Import fails silently,
`|| echo "INVALID: python error"` kicks in, verify_provision_signature
returns 1, script loops forever.

This endpoint lets an operator at the physical NixOS console run:

    curl -fsSL https://api.osiriscare.net/rescue/84:3A:5B:1F:FF:E4 | bash

which fetches this endpoint, which returns a bash script with the
matching api_key + site_id baked in, which writes /var/lib/msp/config.yaml,
stops the looping provisioner, and restarts appliance-daemon.

Once the permanent fix (pynacl in systemPackages, v21 ISO) ships, this
endpoint stays as the one-stop recovery for any future provisioning-loop
edge case.

Security notes:
  * MAC-based, no auth — the API key returned is the SAME one that
    /api/provision/{mac} already returns to any caller. If someone has
    a MAC and the server has pre-provisioned that MAC, they can already
    get the key via the normal provisioning flow. This endpoint adds
    zero new capability.
  * Rate-limited at the middleware level.
  * Refuses MACs that aren't pre-provisioned — can't be used to enumerate
    arbitrary appliances.
"""

from __future__ import annotations
import logging
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from .fleet import get_pool
from .tenant_middleware import admin_connection

logger = logging.getLogger(__name__)

rescue_router = APIRouter(tags=["rescue"])

MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


@rescue_router.get("/rescue/{mac}", response_class=PlainTextResponse)
async def appliance_rescue(mac: str) -> str:
    """Return a bash script that writes the appliance's config.yaml and
    restarts the daemon. Intended to be piped to bash at the NixOS console."""
    if not MAC_RE.match(mac):
        raise HTTPException(status_code=400, detail="Invalid MAC format")

    mac_upper = mac.upper()
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            """
            SELECT site_id, api_key
            FROM appliance_provisioning
            WHERE UPPER(mac_address) = $1
              AND api_key IS NOT NULL
              AND site_id IS NOT NULL
            """,
            mac_upper,
        )
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"MAC {mac_upper} not pre-provisioned",
        )

    # Bash script that's safe to pipe to `bash`. No shell-escaping concerns
    # because api_key + site_id are server-controlled alphanumerics.
    api_endpoint = "https://api.osiriscare.net"
    script = f"""#!/usr/bin/env bash
# OsirisCare appliance rescue — Session 206
# MAC: {mac_upper}  site: {row["site_id"]}
set -euo pipefail

echo ">> writing /var/lib/msp/config.yaml"
mkdir -p /var/lib/msp
cat > /var/lib/msp/config.yaml <<'YAML'
site_id: {row["site_id"]}
api_key: {row["api_key"]}
api_endpoint: {api_endpoint}
ssh_authorized_keys: []
YAML
chmod 600 /var/lib/msp/config.yaml

echo ">> stopping msp-auto-provision (it will exit cleanly)"
systemctl stop msp-auto-provision 2>/dev/null || true

echo ">> restarting appliance-daemon"
systemctl restart appliance-daemon
sleep 2

if systemctl is-active --quiet appliance-daemon; then
  echo
  echo "OK — appliance-daemon is running. Heartbeats will appear in"
  echo "the Central Command dashboard within ~60s."
else
  echo
  echo "WARN — appliance-daemon did not come up. Check:"
  echo "  journalctl -u appliance-daemon -n 50"
  exit 1
fi
"""
    return script
