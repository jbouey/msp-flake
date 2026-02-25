"""
Central Command sync service.

Pushes device inventory from the local appliance to Central Command
for fleet-wide visibility.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from ..db import PortalDatabase

logger = logging.getLogger(__name__)


class CentralSyncService:
    """Service for syncing device inventory to Central Command."""

    def __init__(
        self,
        db: PortalDatabase,
        central_url: str,
        appliance_id: str,
        site_id: str,
        api_key: Optional[str] = None,
    ):
        self.db = db
        self.central_url = central_url.rstrip("/")
        self.appliance_id = appliance_id
        self.site_id = site_id
        self.api_key = api_key

    async def sync_devices(self) -> dict:
        """
        Push current device inventory to Central Command.

        Returns sync result with counts.
        """
        # Get all devices from local database
        devices = self.db.get_devices(limit=10000)
        counts = self.db.get_device_counts()
        compliance = self.db.get_compliance_summary()

        # Build sync report
        device_entries = []
        for device in devices:
            # Get ports for device
            ports_raw = self.db.get_device_ports(device["id"])
            open_ports = [p["port"] for p in ports_raw]

            # Get compliance check details for this device
            compliance_checks = self.db.get_device_compliance_checks(device["id"])
            compliance_details = [
                {
                    "check_type": c["check_type"],
                    "hipaa_control": c.get("hipaa_control"),
                    "status": c["status"],
                    "details": c.get("details"),
                    "checked_at": c["checked_at"],
                }
                for c in compliance_checks
            ]

            device_entries.append({
                "device_id": device["id"],
                "hostname": device["hostname"],
                "ip_address": device["ip_address"],
                "mac_address": device["mac_address"],
                "device_type": device["device_type"],
                "os_name": device.get("os_name"),
                "os_version": device.get("os_version"),
                "medical_device": bool(device.get("medical_device")),
                "scan_policy": device.get("scan_policy", "standard"),
                "manually_opted_in": bool(device.get("manually_opted_in")),
                "compliance_status": device.get("compliance_status", "unknown"),
                "open_ports": open_ports,
                "compliance_details": compliance_details,
                "discovery_source": device.get("discovery_source", "nmap"),
                "first_seen_at": device["first_seen_at"],
                "last_seen_at": device["last_seen_at"],
                "last_scan_at": device.get("last_scan_at"),
            })

        report = {
            "appliance_id": self.appliance_id,
            "site_id": self.site_id,
            "scan_timestamp": datetime.now(timezone.utc).isoformat(),
            "devices": device_entries,
            "total_devices": counts["total"],
            "monitored_devices": counts["monitored"],
            "excluded_devices": counts["excluded"],
            "medical_devices": counts["medical"],
            "compliance_rate": compliance["compliance_rate"],
        }

        # Send to Central Command
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.central_url}/api/devices/sync",
                    json=report,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        logger.info(
                            f"Synced {result['devices_received']} devices to Central Command: "
                            f"{result['devices_created']} new, {result['devices_updated']} updated"
                        )
                        return {
                            "status": "success",
                            "devices_synced": result["devices_received"],
                            "devices_created": result["devices_created"],
                            "devices_updated": result["devices_updated"],
                            "message": result["message"],
                        }
                    else:
                        error_text = await resp.text()
                        logger.error(f"Central Command sync failed: {resp.status} - {error_text}")
                        return {
                            "status": "error",
                            "error": f"HTTP {resp.status}: {error_text}",
                        }
        except aiohttp.ClientError as e:
            logger.error(f"Central Command sync connection error: {e}")
            return {
                "status": "error",
                "error": f"Connection failed: {str(e)}",
            }
        except Exception as e:
            logger.error(f"Central Command sync error: {e}")
            return {
                "status": "error",
                "error": str(e),
            }


async def sync_to_central(
    db: PortalDatabase,
    central_url: str,
    appliance_id: str,
    site_id: str,
    api_key: Optional[str] = None,
) -> dict:
    """
    Convenience function to sync devices to Central Command.
    """
    service = CentralSyncService(
        db=db,
        central_url=central_url,
        appliance_id=appliance_id,
        site_id=site_id,
        api_key=api_key,
    )
    return await service.sync_devices()
