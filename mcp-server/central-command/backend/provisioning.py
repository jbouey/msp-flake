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

import logging
import os
import secrets
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def normalize_mac(mac: str) -> str:
    """Normalize MAC address to uppercase colon-separated format (AA:BB:CC:DD:EE:FF)."""
    clean = mac.upper().replace(':', '').replace('-', '').replace('.', '')
    return ':'.join(clean[i:i+2] for i in range(0, len(clean), 2))

from .fleet import get_pool
from .tenant_middleware import admin_connection

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

    async with admin_connection(pool) as conn:
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

        # Create or update appliance record
        client_ip = request.client.host if request.client else None
        await conn.execute("""
            INSERT INTO site_appliances (
                site_id, appliance_id, mac_address, hostname,
                agent_version, status, last_checkin
            ) VALUES ($1, $2, $3, $4, 'provisioning', 'pending', NOW())
            ON CONFLICT (appliance_id) DO UPDATE SET
                hostname = EXCLUDED.hostname,
                last_checkin = NOW()
        """,
            site_id,
            appliance_id,
            claim.mac_address.upper(),
            claim.hostname
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

        # Build initial config
        config = {
            "api_endpoint": API_BASE_URL,
            "checkin_interval_seconds": 300,  # 5 min during provisioning
            "discovery_enabled": True,
            "network_range": provision['network_range'],  # May be None
            "logging_level": "INFO",
            "features": {
                "compliance_checks": True,
                "auto_healing": False,  # Disabled until fully activated
                "evidence_collection": True,
                "windows_scanning": True,
            },
        }

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
            # Update existing appliance
            await conn.execute("""
                UPDATE site_appliances
                SET last_checkin = NOW(),
                    ip_address = COALESCE($1, ip_address),
                    hostname = COALESCE($2, hostname)
                WHERE appliance_id = $3
            """, client_ip, heartbeat.hostname, appliance['appliance_id'])

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
    """
    from urllib.parse import unquote

    pool = await get_pool()
    # Decode URL-encoded MAC and normalize
    mac = unquote(mac_address).upper().replace('-', ':')

    async with admin_connection(pool) as conn:
        # Check the appliance_provisioning table for MAC-based auto-provision
        provision = await conn.fetchrow("""
            SELECT ap.site_id, ap.api_key,
                   COALESCE(ap.ssh_authorized_keys, '{}') as appliance_keys,
                   COALESCE(s.ssh_authorized_keys, '{}') as site_keys
            FROM appliance_provisioning ap
            LEFT JOIN sites s ON s.site_id = ap.site_id
            WHERE UPPER(ap.mac_address) = $1
        """, mac)

        if provision:
            # Mark as provisioned if not already
            await conn.execute("""
                UPDATE appliance_provisioning
                SET provisioned_at = COALESCE(provisioned_at, NOW())
                WHERE UPPER(mac_address) = $1
            """, mac)

            # Merge appliance-specific and site-level SSH keys
            ssh_keys = list(set(
                list(provision['appliance_keys'] or []) +
                list(provision['site_keys'] or [])
            ))

            return {
                "site_id": provision['site_id'],
                "api_key": provision['api_key'],
                "api_endpoint": API_BASE_URL,
                "ssh_authorized_keys": ssh_keys
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
            logger.warning(f"[provision] Failed to register unclaimed appliance {mac}: {e}")

        return {
            "status": "unclaimed",
            "mac_address": mac,
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

        # Build config based on tier
        tier = appliance['tier'] or 'standard'

        config = {
            "api_endpoint": API_BASE_URL,
            "site_id": appliance['site_id'],
            "appliance_id": appliance_id,
            "tier": tier,
            "checkin_interval_seconds": 300 if tier == "premium" else 600,
            "compliance_checks_enabled": True,
            "auto_healing_enabled": tier in ("premium", "enterprise"),
            "evidence_collection_enabled": True,
            "windows_scanning_enabled": True,
            "discovery_interval_hours": 24,
            "branding": {
                "brand_name": appliance['brand_name'],
                "primary_color": appliance['primary_color'],
                "logo_url": appliance['logo_url'],
            },
        }

        return {
            "appliance_id": appliance_id,
            "site_id": appliance['site_id'],
            "clinic_name": appliance['clinic_name'],
            "config": config,
        }
