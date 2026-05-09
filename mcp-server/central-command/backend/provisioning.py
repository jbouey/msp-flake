"""Appliance provisioning module.

Handles the appliance-side provisioning flow:
1. Appliance boots in provisioning mode (no site_id configured)
2. User scans QR code or enters provision code
3. Appliance calls /api/provision/claim with code + MAC
4. Server returns site_id, appliance_id, and partner branding
5. Appliance configures itself and begins normal operation

This module provides endpoints that are called by appliances during
initial setup, independent of the partner API.
"""

import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def normalize_mac(mac: str) -> str:
    """Normalize MAC address to uppercase colon-separated format (AA:BB:CC:DD:EE:FF)."""
    clean = mac.upper().replace(':', '').replace('-', '').replace('.', '')
    return ':'.join(clean[i:i+2] for i in range(0, len(clean), 2))

from .fleet import get_pool
from .tenant_middleware import admin_connection, admin_transaction

# API endpoint from environment variable
API_BASE_URL = os.getenv("API_BASE_URL", "https://api.osiriscare.net")

# WireGuard hub configuration
WG_PEER_DIR = os.getenv("WG_PEER_DIR", "/opt/mcp-server/wireguard/peers")
WG_HUB_ENDPOINT = os.getenv("WG_HUB_ENDPOINT", "178.156.162.116:51820")
WG_HUB_PUBKEY = ""
try:
    with open(os.getenv("WG_HUB_PUBKEY_FILE", "/opt/mcp-server/wireguard/hub.pub")) as f:
        WG_HUB_PUBKEY = f.read().strip()
except FileNotFoundError:
    logger.warning("WireGuard hub public key not found — WireGuard provisioning disabled")

router = APIRouter(prefix="/api/provision", tags=["provisioning"])


# =============================================================================
# MODELS
# =============================================================================

class ProvisionClaimRequest(BaseModel):
    """Request to claim a provision code."""
    provision_code: str
    mac_address: str
    hostname: Optional[str] = None
    hardware_id: Optional[str] = None  # SMBIOS/DMI hardware UUID
    public_key: Optional[str] = None  # Appliance's public key for secure comms
    wg_pubkey: Optional[str] = None  # WireGuard public key (Curve25519)
    boot_source: Optional[str] = None  # "live_usb" or "installed_disk"
    all_mac_addresses: Optional[list] = None  # All physical NIC MACs


class ProvisionClaimResponse(BaseModel):
    """Response after successful provision claim."""
    status: str
    site_id: str
    appliance_id: str
    api_endpoint: str
    api_key: Optional[str] = None  # Site-specific API key if using per-site auth
    partner: dict  # Branding info
    config: dict  # Initial configuration
    message: str
    # WireGuard VPN fields (present only when wg_pubkey was provided in request)
    wg_hub_pubkey: Optional[str] = None
    wg_hub_endpoint: Optional[str] = None
    wg_ip: Optional[str] = None


class ProvisionStatusRequest(BaseModel):
    """Update provision status."""
    appliance_id: str
    status: str  # configuring, testing, active, failed
    progress_percent: int = 0
    message: Optional[str] = None


class RekeyRequest(BaseModel):
    """Request to re-key an appliance that lost its API key."""
    site_id: str
    mac_address: str
    hostname: Optional[str] = None
    hardware_id: Optional[str] = None


class HeartbeatRequest(BaseModel):
    """Heartbeat from provisioning appliance."""
    mac_address: str
    hostname: Optional[str] = None
    ip_address: Optional[str] = None
    status: str = "provisioning"


# =============================================================================
# WIREGUARD HELPERS
# =============================================================================

async def _allocate_wg_ip(conn) -> str:
    """Allocate the next available WireGuard VPN IP."""
    result = await conn.fetchval(
        "SELECT wg_ip FROM sites WHERE wg_ip IS NOT NULL ORDER BY wg_ip DESC LIMIT 1"
    )
    if result:
        parts = result.split('.')
        next_octet = int(parts[3]) + 1
        if next_octet > 254:
            raise ValueError("WireGuard IP pool exhausted")
        return f"10.100.0.{next_octet}"
    return "10.100.0.2"  # First appliance (10.100.0.1 is the hub)


async def _add_wg_peer(site_id: str, pubkey: str, vpn_ip: str) -> bool:
    """Add a WireGuard peer config file for the hub.

    Writes a peer config fragment to WG_PEER_DIR (a Docker-mounted volume).
    A systemd path unit on the host watches the directory and runs
    `wg syncconf` to apply changes without restarting the tunnel.
    """
    peer_file = os.path.join(WG_PEER_DIR, f"{site_id}.conf")

    peer_config = (
        f"[Peer]\n"
        f"# {site_id}\n"
        f"PublicKey = {pubkey}\n"
        f"AllowedIPs = {vpn_ip}/32\n"
    )

    try:
        os.makedirs(WG_PEER_DIR, exist_ok=True)
        with open(peer_file, 'w') as f:
            f.write(peer_config)

        # Touch a reload flag so the host-side systemd path unit triggers wg syncconf
        flag_file = os.path.join(WG_PEER_DIR, ".reload")
        with open(flag_file, 'w') as f:
            f.write(str(time.time()))

        logger.info(f"WireGuard peer config written for {site_id} ({vpn_ip})")
        return True
    except Exception as e:
        logger.error(f"Failed to write WireGuard peer config for {site_id}: {e}")
        return False


# =============================================================================
# API ENDPOINTS
# =============================================================================

@router.post("/claim", response_model=ProvisionClaimResponse)
async def claim_provision_code(claim: ProvisionClaimRequest, request: Request):
    """Claim a provision code and get configuration.

    Called by appliance during first boot after QR scan.
    Returns everything the appliance needs to configure itself.
    """
    pool = await get_pool()
    code = claim.provision_code.upper().strip()

    # Coach-sweep ratchet wave-2 2026-05-08: 10-query handler (operator
    # provisioning flow — multi-step appliance claim). A routing miss
    # could half-provision an appliance. admin_transaction.
    async with admin_transaction(pool) as conn:
        # Find and validate provision code
        provision = await conn.fetchrow("""
            SELECT ap.id, ap.partner_id, ap.target_site_id, ap.client_name,
                   ap.status, ap.expires_at, ap.client_contact_email, ap.network_range,
                   ap.notes
            FROM appliance_provisions ap
            WHERE ap.provision_code = $1
        """, code)

        if not provision:
            raise HTTPException(status_code=404, detail="Invalid provision code")

        if provision['status'] != 'pending':
            if provision['status'] == 'claimed':
                raise HTTPException(
                    status_code=400,
                    detail="Provision code already claimed"
                )
            raise HTTPException(
                status_code=400,
                detail=f"Provision code is {provision['status']}"
            )

        if provision['expires_at'] and provision['expires_at'] < datetime.now(timezone.utc):
            await conn.execute("""
                UPDATE appliance_provisions SET status = 'expired' WHERE id = $1
            """, provision['id'])
            raise HTTPException(status_code=400, detail="Provision code expired")

        # Get partner branding
        partner = await conn.fetchrow("""
            SELECT id, slug, brand_name, primary_color, logo_url
            FROM partners WHERE id = $1
        """, provision['partner_id'])

        if not partner:
            raise HTTPException(status_code=500, detail="Partner not found")

        # Generate site_id if not pre-assigned
        site_id = provision['target_site_id']
        if not site_id:
            # Generate from client name, hostname, or MAC
            base = provision['client_name'] or claim.hostname or claim.mac_address
            base = base.lower().replace(' ', '-').replace(':', '-').replace('.', '-')
            base = ''.join(c for c in base if c.isalnum() or c == '-')[:40]
            site_id = f"{base}-{secrets.token_hex(6)}"

        # Generate appliance_id with colon-separated MAC (matches Go checkin canonical format)
        mac_normalized = normalize_mac(claim.mac_address)
        appliance_id = f"{site_id}-{mac_normalized}"

        # Create or update site
        existing_site = await conn.fetchrow("""
            SELECT id FROM sites WHERE site_id = $1
        """, site_id)

        if existing_site:
            # Update existing site with hardware_id and public_key
            await conn.execute("""
                UPDATE sites
                SET hardware_id = COALESCE($1, hardware_id),
                    public_key = COALESCE($2, public_key),
                    updated_at = NOW()
                WHERE site_id = $3
            """, claim.hardware_id, claim.public_key, site_id)
            site_internal_id = existing_site['id']
        else:
            # Create new site
            site_row = await conn.fetchrow("""
                INSERT INTO sites (
                    site_id, clinic_name, partner_id, status, onboarding_stage,
                    client_contact_email, notes, hardware_id, public_key, tier
                ) VALUES ($1, $2, $3, 'pending', 'provisioning', $4, $5, $6, $7, 'standard')
                RETURNING id
            """,
                site_id,
                provision['client_name'] or site_id.replace('-', ' ').title(),
                provision['partner_id'],
                provision['client_contact_email'],
                provision['notes'],
                claim.hardware_id,
                claim.public_key
            )
            site_internal_id = site_row['id']

        # Create or update appliance record.
        # Track boot_source so the dashboard can distinguish installing vs installed.
        boot_source = claim.boot_source or "unknown"
        daemon_health_json = json.dumps({"boot_source": boot_source})

        # If claiming from live USB, set onboarding_stage to 'installing' so the
        # dashboard shows "Installation in Progress" instead of "Pending".
        # This prevents the bug where an admin accepts a MAC from the live USB,
        # pulls the USB early, and thinks the appliance is deployed.
        if boot_source == "live_usb":
            logger.warning(
                f"Provisioning from LIVE USB: {appliance_id} — installation not yet complete. "
                f"Appliance will transition to 'installed' after disk boot."
            )

        client_ip = request.client.host if request.client else None
        await conn.execute("""
            INSERT INTO site_appliances (
                site_id, appliance_id, mac_address, hostname,
                agent_version, status, last_checkin, daemon_health
            ) VALUES ($1, $2, $3, $4, 'provisioning', 'pending', NOW(), $5::jsonb)
            ON CONFLICT (appliance_id) DO UPDATE SET
                hostname = EXCLUDED.hostname,
                last_checkin = NOW(),
                daemon_health = EXCLUDED.daemon_health
        """,
            site_id,
            appliance_id,
            claim.mac_address.upper(),
            claim.hostname,
            daemon_health_json
        )

        # Generate API key for appliance authentication
        import hashlib
        raw_api_key = secrets.token_urlsafe(32)
        api_key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()
        await conn.execute("""
            INSERT INTO api_keys (site_id, key_hash, key_prefix, description, active, created_at)
            VALUES ($1, $2, $3, 'Auto-generated during provisioning', true, NOW())
            ON CONFLICT DO NOTHING
        """, site_id, api_key_hash, raw_api_key[:8])

        # Mark provision as claimed
        await conn.execute("""
            UPDATE appliance_provisions
            SET status = 'claimed',
                claimed_at = NOW(),
                claimed_by_mac = $1,
                claimed_appliance_id = $2,
                claimed_by_site_id = (SELECT id FROM sites WHERE site_id = $3),
                claimed_hardware_id = $4
            WHERE id = $5
        """,
            claim.mac_address.upper(),
            appliance_id,
            site_id,
            claim.hardware_id,
            provision['id']
        )

        # WireGuard VPN provisioning (optional — only when appliance sends a pubkey)
        wg_response = {}
        if claim.wg_pubkey and WG_HUB_PUBKEY:
            try:
                vpn_ip = await _allocate_wg_ip(conn)
                peer_ok = await _add_wg_peer(site_id, claim.wg_pubkey, vpn_ip)
                if peer_ok:
                    await conn.execute(
                        "UPDATE sites SET wg_pubkey = $1, wg_ip = $2 WHERE site_id = $3",
                        claim.wg_pubkey, vpn_ip, site_id
                    )
                    wg_response = {
                        "wg_hub_pubkey": WG_HUB_PUBKEY,
                        "wg_hub_endpoint": WG_HUB_ENDPOINT,
                        "wg_ip": vpn_ip,
                    }
                    logger.info(f"WireGuard provisioned for {site_id}: {vpn_ip}")
                else:
                    logger.warning(f"WireGuard peer file write failed for {site_id}, skipping VPN setup")
            except ValueError as e:
                logger.error(f"WireGuard IP allocation failed: {e}")
            except Exception as e:
                logger.error(f"WireGuard provisioning error for {site_id}: {e}")
        elif claim.wg_pubkey and not WG_HUB_PUBKEY:
            logger.warning(f"Appliance {site_id} sent wg_pubkey but hub key not configured")

        # Initial config returned to the CLI-driven claim flow.
        # Historically this dict carried a dozen feature flags + thresholds
        # (checkin_interval_seconds, discovery_enabled, network_range,
        # logging_level, features{...}) — ALL of which had zero consumers
        # on the appliance side (daemon reads its own keys from config.yaml
        # and ignores this response body; see provisioning.py lockstep
        # audit 2026-04-24). Kept empty to satisfy the ProvisionClaimResponse
        # Pydantic contract; re-add fields here only when a real consumer
        # exists.
        config: dict = {}

        return ProvisionClaimResponse(
            status="claimed",
            site_id=site_id,
            appliance_id=appliance_id,
            api_endpoint=API_BASE_URL,
            api_key=raw_api_key,
            partner={
                "slug": partner['slug'],
                "brand_name": partner['brand_name'],
                "primary_color": partner['primary_color'],
                "logo_url": partner['logo_url'],
            },
            config=config,
            message="Appliance provisioned successfully. Run initial discovery.",
            **wg_response,
        )


@router.get("/validate/{provision_code}")
async def validate_provision_code(provision_code: str):
    """Validate a provision code without claiming it.

    Used by appliance UI to verify code before prompting for confirmation.
    """
    pool = await get_pool()
    code = provision_code.upper().strip()

    async with admin_connection(pool) as conn:
        provision = await conn.fetchrow("""
            SELECT ap.status, ap.client_name, ap.expires_at,
                   p.brand_name, p.primary_color
            FROM appliance_provisions ap
            JOIN partners p ON p.id = ap.partner_id
            WHERE ap.provision_code = $1
        """, code)

        if not provision:
            return {
                "valid": False,
                "error": "Invalid provision code"
            }

        if provision['status'] != 'pending':
            return {
                "valid": False,
                "error": f"Provision code is {provision['status']}"
            }

        if provision['expires_at'] and provision['expires_at'] < datetime.now(timezone.utc):
            return {
                "valid": False,
                "error": "Provision code expired"
            }

        return {
            "valid": True,
            "client_name": provision['client_name'],
            "partner": {
                "brand_name": provision['brand_name'],
                "primary_color": provision['primary_color'],
            }
        }


@router.post("/status")
async def update_provision_status(status: ProvisionStatusRequest):
    """Update provisioning status.

    Called by appliance during setup to report progress.
    """
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        # Update appliance status
        result = await conn.execute("""
            UPDATE site_appliances
            SET status = $1, last_checkin = NOW()
            WHERE appliance_id = $2
        """, status.status, status.appliance_id)

        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Appliance not found")

        # If moving to active, update site onboarding stage + timestamp
        if status.status == "active":
            await conn.execute("""
                UPDATE sites
                SET onboarding_stage = 'connectivity',
                    status = 'active',
                    connectivity_at = NOW()
                WHERE site_id = (
                    SELECT site_id FROM site_appliances WHERE appliance_id = $1
                )
                AND (onboarding_stage IN ('provisioning', 'received', 'shipped')
                     OR onboarding_stage IS NULL)
            """, status.appliance_id)

    return {
        "status": "updated",
        "appliance_id": status.appliance_id,
        "new_status": status.status,
    }


@router.post("/heartbeat")
async def provisioning_heartbeat(heartbeat: HeartbeatRequest, request: Request):
    """Heartbeat from appliance in provisioning mode.

    Allows tracking appliances that are powered on but haven't
    completed provisioning yet.
    """
    pool = await get_pool()
    client_ip = request.client.host if request.client else heartbeat.ip_address

    async with admin_connection(pool) as conn:
        # Look for appliance by MAC
        appliance = await conn.fetchrow("""
            SELECT appliance_id, site_id FROM site_appliances
            WHERE mac_address = $1
        """, heartbeat.mac_address.upper())

        if appliance:
            # Update existing appliance. site_appliances.ip_addresses is
            # jsonb (list); wrap the client IP as a single-element array.
            ip_blob = json.dumps([client_ip]) if client_ip else None
            await conn.execute("""
                UPDATE site_appliances
                SET last_checkin = NOW(),
                    ip_addresses = COALESCE($1::jsonb, ip_addresses),
                    hostname = COALESCE($2, hostname)
                WHERE appliance_id = $3
            """, ip_blob, heartbeat.hostname, appliance['appliance_id'])

            return {
                "status": "known",
                "appliance_id": appliance['appliance_id'],
                "site_id": appliance['site_id'],
            }
        else:
            # Unknown appliance - just acknowledge
            return {
                "status": "unknown",
                "message": "Appliance not provisioned. Use provision code to claim.",
            }


@router.get("/{mac_address}")
async def get_provision_by_mac(mac_address: str):
    """Get provisioning config by MAC address.

    Called by appliance on boot to auto-provision without a code.
    Returns config.yaml contents if MAC is registered in appliance_provisioning table.

    The MAC can be URL-encoded (84%3A3A%3A5B) or plain (84:3A:5B).

    v40.4 (2026-04-23): this endpoint now MINTS A FRESH api_key on every
    claimed-MAC call instead of handing back the stale legacy value
    stored in appliance_provisioning.api_key. Prior behavior caused a
    guaranteed AUTH_KEY_MISMATCH for every reflashed appliance that
    had ever auto-rekeyed via /api/provision/rekey: the stored value
    was the march-27 initial key, the active key in api_keys was the
    post-rekey one, and the daemon's first checkin after reflash was
    always rejected. Minting here is safe because the appliance-side
    idempotency marker (v40.4 msp-auto-provision) guarantees this
    endpoint is called exactly once per fresh disk lifecycle; repeat
    calls skip at the appliance-side. The Migration-209 trigger on
    api_keys auto-deactivates the prior active row for the same
    (site_id, appliance_id) and writes a structured audit entry.
    """
    import hashlib  # local import keeps module-level surface stable
    from urllib.parse import unquote

    pool = await get_pool()
    # Decode URL-encoded MAC and normalize
    mac = unquote(mac_address).upper().replace('-', ':')

    async with admin_connection(pool) as conn:
        # Check the appliance_provisioning table for MAC-based auto-provision
        provision = await conn.fetchrow("""
            SELECT ap.site_id,
                   COALESCE(ap.ssh_authorized_keys, '{}') as appliance_keys,
                   COALESCE(s.ssh_authorized_keys, '{}') as site_keys
            FROM appliance_provisioning ap
            LEFT JOIN sites s ON s.site_id = ap.site_id
            WHERE UPPER(ap.mac_address) = $1
        """, mac)

        if provision and provision['site_id']:
            # v40.4: mint a FRESH api_key + update appliance_provisioning
            # + INSERT into api_keys (migration 209 trigger auto-
            # deactivates prior active row). All three writes in one
            # transaction so we can't end up with api_keys desynced
            # from appliance_provisioning or from what we return.
            site_id_val = provision['site_id']
            appliance_id = f"{site_id_val}-{mac}"
            raw_api_key = secrets.token_urlsafe(32)
            api_key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()
            key_prefix = raw_api_key[:8]

            async with conn.transaction():
                # api_keys: insert fresh active; trigger deactivates siblings.
                await conn.execute("""
                    INSERT INTO api_keys
                        (site_id, appliance_id, key_hash, key_prefix,
                         description, active, created_at)
                    VALUES ($1, $2, $3, $4,
                           'Minted by /api/provision/{mac} — fresh provision',
                           true, NOW())
                """, site_id_val, appliance_id, api_key_hash, key_prefix)

                # v40.6 Split #1 (Principal SWE round-table 2026-04-24):
                # appliance_provisioning.api_key is DEPRECATED. This
                # endpoint now mints on every call and never reads the
                # stored value; no other reader remains. Stop writing
                # the raw key here — the column gets DROPPED in a
                # follow-up migration after a soak period confirms no
                # regression. Still refresh provisioned_at for the
                # audit timestamp.
                await conn.execute("""
                    UPDATE appliance_provisioning
                       SET provisioned_at = COALESCE(provisioned_at, NOW())
                     WHERE UPPER(mac_address) = $1
                """, mac)

            logger.info(
                "[provision] minted fresh api_key site=%s mac=%s "
                "appliance_id=%s prefix=%s",
                site_id_val, mac, appliance_id, key_prefix,
            )

            # Merge appliance-specific and site-level SSH keys
            ssh_keys = list(set(
                list(provision['appliance_keys'] or []) +
                list(provision['site_keys'] or [])
            ))

            config = {
                "site_id": site_id_val,
                "appliance_id": appliance_id,
                "api_key": raw_api_key,
                "api_endpoint": API_BASE_URL,
                "ssh_authorized_keys": ssh_keys
            }

            # Sign the config so the appliance can verify authenticity.
            # The appliance script (appliance-disk-image.nix) expects:
            #   response.config  = the config object
            #   response.signature = Ed25519 hex signature of json.dumps(config, sort_keys=True)
            # Without this signature, the appliance rejects the provisioning response.
            signature = ""
            try:
                from main import sign_data
                config_canonical = json.dumps(config, sort_keys=True)
                signature = sign_data(config_canonical)
            except Exception as e:
                # Signing-key load failure is operationally critical:
                # an unsigned config is still served (appliance daemon
                # rejects updates without server pubkey verification),
                # but the channel for delivering signed orders is broken.
                # Per CLAUDE.md "no silent write failures" — log at ERROR.
                logger.error(
                    "provision_config_sign_failed",
                    exc_info=True,
                    extra={"mac_address": mac,
                           "exception_class": type(e).__name__},
                )

            return {
                "config": config,
                "signature": signature,
                # Top-level fields for backwards compat (appliance also reads these directly)
                "site_id": config["site_id"],
                "api_key": config["api_key"],
                "api_endpoint": config["api_endpoint"],
                "ssh_authorized_keys": ssh_keys,
            }

        if provision:
            # MAC is registered but NOT yet claimed to a site.
            # Return unclaimed status with retry hint so the appliance keeps polling.
            logger.info(f"[provision] Unclaimed appliance polling: MAC={mac}")
            return {
                "status": "unclaimed",
                "mac_address": mac,
                "retry_seconds": 60,
                "message": "Appliance registered. Waiting for site assignment in the dashboard.",
            }

        # MAC not found — register as unclaimed appliance for drop-ship workflow.
        # The appliance will poll periodically. Once an admin claims it to a site
        # in the dashboard, the next poll returns the full config.
        try:
            await conn.execute("""
                INSERT INTO appliance_provisioning (mac_address, notes, registered_at)
                VALUES ($1, 'Auto-registered (unclaimed — awaiting site assignment)', NOW())
                ON CONFLICT (mac_address) DO UPDATE SET registered_at = NOW()
            """, mac)
            logger.info(f"[provision] Unclaimed appliance registered: MAC={mac}")
        except Exception as e:
            # DB write failure on the unclaimed-appliance INSERT is
            # operationally critical: the appliance polls indefinitely
            # without ever appearing in the admin claim queue.
            # Per CLAUDE.md "no silent write failures" — log at ERROR.
            logger.error(
                "provision_unclaimed_register_failed",
                exc_info=True,
                extra={"mac_address": mac,
                       "exception_class": type(e).__name__},
            )

        return {
            "status": "unclaimed",
            "mac_address": mac,
            "retry_seconds": 60,
            "message": "Appliance registered. Waiting for site assignment in the dashboard.",
        }


@router.get("/config/{appliance_id}")
async def get_appliance_config(appliance_id: str):
    """Get current configuration for an appliance.

    Called by appliance after provisioning to get full config.
    """
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        appliance = await conn.fetchrow("""
            SELECT sa.*, s.tier, s.clinic_name,
                   p.brand_name, p.primary_color, p.logo_url
            FROM site_appliances sa
            JOIN sites s ON s.site_id = sa.site_id
            LEFT JOIN partners p ON p.id = s.partner_id
            WHERE sa.appliance_id = $1
        """, appliance_id)

        if not appliance:
            raise HTTPException(status_code=404, detail="Appliance not found")

        # Build config — only fields with a live appliance-side consumer.
        # Pre-cleanup this dict shipped ~10 dead flags (tier, checkin_interval_seconds,
        # compliance_checks_enabled, auto_healing_enabled, evidence_collection_enabled,
        # windows_scanning_enabled, discovery_interval_hours, branding{...}). None
        # were read by the daemon — see 2026-04-24 lockstep audit. Trimmed.
        config = {
            "api_endpoint": API_BASE_URL,
            "site_id": appliance['site_id'],
            "appliance_id": appliance_id,
        }

        return {
            "appliance_id": appliance_id,
            "site_id": appliance['site_id'],
            "clinic_name": appliance['clinic_name'],
            "config": config,
        }


# =============================================================================
# REKEY — Auto-recovery when appliance loses its API key
# =============================================================================

# In-memory rate limit: {appliance_id: last_rekey_time}
_rekey_cooldowns: dict[str, float] = {}
REKEY_COOLDOWN_SECONDS = 3600  # 1 hour

@router.post("/rekey")
async def rekey_appliance(req: RekeyRequest, request: Request):
    """Re-key an appliance that has a mismatched API key.

    Trust model: same as initial provisioning — MAC + site_id identify the
    appliance. hardware_id is verified if available in the DB.
    Rate limited to 1 rekey per appliance per hour.
    """
    import hashlib

    pool = await get_pool()
    mac_normalized = normalize_mac(req.mac_address)
    appliance_id = f"{req.site_id}-{mac_normalized}"

    # Rate limit check
    last_rekey = _rekey_cooldowns.get(appliance_id, 0)
    if time.time() - last_rekey < REKEY_COOLDOWN_SECONDS:
        remaining = int(REKEY_COOLDOWN_SECONDS - (time.time() - last_rekey))
        raise HTTPException(
            status_code=429,
            detail=f"Rekey rate limited. Retry in {remaining}s"
        )

    # admin_transaction wave-9 (Session 219): 5 admin DB calls (verify appliance, rotate api_key, update site_appliances, audit insert); pin SET LOCAL app.is_admin to one PgBouncer backend
    async with admin_transaction(pool) as conn:
        # Verify appliance exists and was previously provisioned
        appliance = await conn.fetchrow("""
            SELECT sa.appliance_id, sa.site_id, sa.first_checkin, sa.mac_address,
                   s.hardware_id
            FROM site_appliances sa
            LEFT JOIN sites s ON s.site_id = sa.site_id
            WHERE sa.appliance_id = $1
              AND sa.site_id = $2
        """, appliance_id, req.site_id)

        if not appliance:
            logger.warning(f"Rekey rejected: unknown appliance {appliance_id}")
            raise HTTPException(status_code=404, detail="Unknown appliance")

        if not appliance['first_checkin']:
            logger.warning(f"Rekey rejected: appliance {appliance_id} never provisioned")
            raise HTTPException(status_code=404, detail="Appliance not provisioned")

        # Verify hardware_id if stored and provided
        if appliance['hardware_id'] and req.hardware_id:
            if appliance['hardware_id'] != req.hardware_id:
                logger.warning(
                    f"Rekey rejected: hardware_id mismatch for {appliance_id} "
                    f"(expected={appliance['hardware_id']}, got={req.hardware_id})"
                )
                raise HTTPException(status_code=403, detail="Hardware ID mismatch")

        # Generate new API key
        raw_api_key = secrets.token_urlsafe(32)
        api_key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()

        # Deactivate old keys for THIS APPLIANCE only (not the whole site).
        # This is the key fix for multi-appliance sites — siblings keep their keys.
        deactivated = await conn.execute("""
            UPDATE api_keys SET active = false
            WHERE site_id = $1 AND appliance_id = $2 AND active = true
        """, req.site_id, appliance_id)
        logger.info(f"Rekey: deactivated {deactivated} old keys for appliance {appliance_id}")

        # Insert new per-appliance key
        await conn.execute("""
            INSERT INTO api_keys (site_id, appliance_id, key_hash, key_prefix, description, active, created_at)
            VALUES ($1, $2, $3, $4, 'Auto-rekeyed after auth failure', true, NOW())
        """, req.site_id, appliance_id, api_key_hash, raw_api_key[:8])

        # Clear auth failure tracking
        await conn.execute("""
            UPDATE site_appliances
            SET auth_failure_since = NULL,
                auth_failure_count = 0,
                last_auth_failure = NULL
            WHERE appliance_id = $1
        """, appliance_id)

        # Audit log
        import json as _json
        client_ip = request.client.host if request.client else None
        await conn.execute("""
            INSERT INTO audit_log (event_type, actor, details, ip_address)
            VALUES ('appliance.rekeyed', $1, $2, $3)
        """,
            appliance_id,
            _json.dumps({"site_id": req.site_id, "mac": mac_normalized, "reason": "auto-rekey after auth failure"}),
            client_ip,
        )

    _rekey_cooldowns[appliance_id] = time.time()
    logger.info(f"Rekey successful: {appliance_id} (site={req.site_id})")

    return {
        "status": "rekeyed",
        "api_key": raw_api_key,
        "appliance_id": appliance_id,
    }


class AdminRestoreRequest(BaseModel):
    """Request to admin-restore an appliance whose site_appliances row
    was deleted (e.g. by a cleanup pass) but whose physical box is
    still alive and trying to check in.

    Distinct from `RekeyRequest` because it explicitly creates the
    site_appliances row if missing, which `/api/provision/rekey`
    rejects as 'unknown appliance.' Reason ≥ 20 chars is required so
    the audit log carries why we re-created the row.
    """
    site_id: str
    mac_address: str
    hostname: Optional[str] = None
    hardware_id: Optional[str] = None
    reason: str  # ≥ 20 chars, free-form audit context


@router.post("/admin/restore")
async def admin_restore_appliance(
    req: AdminRestoreRequest,
    request: Request,
):
    """ADMIN-ONLY recovery for orphaned appliances.

    Use case (Session 210-B 2026-04-25): a manual cleanup pass deleted
    `site_appliances` rows for a MAC but the physical box was still
    alive. The standard `/api/provision/rekey` endpoint refuses to
    re-key because it requires an existing row. This endpoint:

      1. Verifies the site exists
      2. UPSERTs the site_appliances row (status='pending' if new,
         left alone if existing)
      3. Mints a fresh api_key
      4. Deactivates any prior active keys for this (site, appliance)
      5. Writes an audit-log entry with the operator's reason

    Auth: admin Bearer token via `_resolve_admin(request)` (Session 213
    round-table P0 — we keep manual dispatch because auth.py +
    provisioning.py have a known-circular-import history; the lazy-
    import inside `_resolve_admin` keeps the resolution path clear and
    auditable. Functionally identical to `Depends(require_admin)`).

    Rate limited via the same _rekey_cooldowns map as /rekey.

    Returns the same shape as /rekey so the recovery script can be
    a drop-in replacement.
    """
    # Manual admin dispatch — see docstring rationale. Raises 401/403
    # via _resolve_admin → require_auth → require_admin chain. Unit-
    # tested via test_provisioning_state_change.py.
    user = await _resolve_admin(request)

    if not req.reason or len(req.reason.strip()) < 20:
        raise HTTPException(
            status_code=400,
            detail="reason must be ≥ 20 chars — audit-log context required",
        )

    import hashlib

    pool = await get_pool()
    mac_normalized = normalize_mac(req.mac_address)
    appliance_id = f"{req.site_id}-{mac_normalized}"

    # Rate limit (shares cooldown bucket with /rekey).
    last_rekey = _rekey_cooldowns.get(appliance_id, 0)
    if time.time() - last_rekey < REKEY_COOLDOWN_SECONDS:
        remaining = int(REKEY_COOLDOWN_SECONDS - (time.time() - last_rekey))
        raise HTTPException(
            status_code=429,
            detail=f"Recovery rate limited. Retry in {remaining}s",
        )

    # admin_restore_appliance is multi-statement (verify site → UPSERT
    # site_appliances → mint api_key → audit-log). Use admin_transaction
    # so SET LOCAL pins to ONE PgBouncer backend — closes the Session
    # 212 routing-pathology class.
    async with admin_transaction(pool) as conn:
        # Step 1: Verify the site exists.
        site = await conn.fetchrow(
            "SELECT site_id FROM sites WHERE site_id = $1", req.site_id
        )
        if not site:
            raise HTTPException(
                status_code=404,
                detail=f"Site {req.site_id!r} not found — create site first",
            )

        # Step 2: UPSERT site_appliances. Existing row is left alone
        # (no overwrite of last_checkin / agent_version etc — those are
        # daemon-driven). Only the missing-row case INSERTs.
        # noqa: site-appliances-deleted-include — admin recovery path
        # legitimately needs to see soft-deleted rows so it can revive
        # them via a fresh api_key + state reset. BUG 1 round-table
        # 2026-05-01 explicitly approved this opt-out.
        existing = await conn.fetchrow(
            """
            SELECT appliance_id, first_checkin
              FROM site_appliances
             WHERE appliance_id = $1
               AND site_id = $2
            """,
            appliance_id, req.site_id,
        )
        row_was_created = False
        if not existing:
            await conn.execute(
                """
                INSERT INTO site_appliances
                  (site_id, appliance_id, mac_address, hostname, status, created_at)
                VALUES ($1, $2, $3, $4, 'pending', NOW())
                """,
                req.site_id, appliance_id, mac_normalized, req.hostname,
            )
            row_was_created = True
            logger.warning(
                "admin_restore created site_appliances row: site=%s mac=%s reason=%r",
                req.site_id, mac_normalized, req.reason,
            )

        # Step 3: Mint api_key + INSERT (Migration 209 trigger
        # auto-deactivates any prior active rows for this
        # (site_id, appliance_id) pair).
        raw_api_key = secrets.token_urlsafe(32)
        api_key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()

        await conn.execute(
            """
            INSERT INTO api_keys (
                site_id, appliance_id, key_hash, key_prefix,
                description, active, created_at
            )
            VALUES ($1, $2, $3, $4, $5, true, NOW())
            """,
            req.site_id, appliance_id, api_key_hash, raw_api_key[:8],
            f"Admin-restore by {user.get('username','?')}: {req.reason[:200]}",
        )

        # Step 4: Clear any auth-failure tracking (the row may have
        # been re-INSERTed clean above, but if it was a legacy row that
        # got bumped out of auth, this normalizes it).
        await conn.execute(
            """
            UPDATE site_appliances
               SET auth_failure_since = NULL,
                   auth_failure_count = 0,
                   last_auth_failure = NULL
             WHERE appliance_id = $1
            """,
            appliance_id,
        )

        # Step 5: Audit log (NOT just /rekey style — this is operator
        # action with a named human + a free-form reason).
        client_ip = request.client.host if request.client else None
        import json as _json
        await conn.execute(
            """
            INSERT INTO admin_audit_log
              (action, username, target, details, ip_address)
            VALUES ($1, $2, $3, $4, $5)
            """,
            "appliance.admin_restore",
            user.get("username", "unknown"),
            f"appliance:{appliance_id}",
            _json.dumps({
                "site_id": req.site_id,
                "mac": mac_normalized,
                "row_was_created": row_was_created,
                "reason": req.reason,
            }),
            client_ip,
        )

    _rekey_cooldowns[appliance_id] = time.time()
    logger.info(
        "admin_restore successful: appliance=%s site=%s row_was_created=%s actor=%s",
        appliance_id, req.site_id, row_was_created, user.get("username", "?"),
    )

    return {
        "status": "restored",
        "api_key": raw_api_key,
        "appliance_id": appliance_id,
        "row_was_created": row_was_created,
    }


async def _resolve_admin(request: Request):
    """Inline admin auth check.

    Avoids the circular-import chain auth.py → ... → provisioning.py
    by deferring the import. require_admin returns the caller's user
    record dict or raises HTTPException(401/403).
    """
    from .auth import require_auth, require_admin
    user = await require_auth(request)
    return await require_admin(user)
