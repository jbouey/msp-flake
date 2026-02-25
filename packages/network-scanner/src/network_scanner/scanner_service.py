"""
Network Scanner Service - Main orchestration loop.

Coordinates discovery methods, classifies devices, stores results,
and syncs to Central Command.

CRITICAL: Medical devices are EXCLUDED by default.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from aiohttp import web

from ._types import Device, DiscoverySource, now_utc
from .config import ScannerConfig
from .classifier import classify_device, discovered_to_device
from .device_db import DeviceDatabase
from .discovery import (
    DiscoveredDevice,
    DiscoveryMethod,
    ADDiscovery,
    ARPDiscovery,
    NmapDiscovery,
    NmapPingSweep,
    GoAgentListener,
    GoAgentRegistry,
)

logger = logging.getLogger(__name__)


class NetworkScannerService:
    """
    Main network scanner service.

    Orchestrates discovery, classification, and storage of network devices.
    """

    def __init__(self, config: ScannerConfig):
        """
        Initialize scanner service.

        Args:
            config: Scanner configuration
        """
        self.config = config
        self.db = DeviceDatabase(config.db_path)
        self._running = False
        self._shutdown_event = asyncio.Event()

        # Discovery methods
        self._discovery_methods: list[DiscoveryMethod] = []
        self._init_discovery_methods()

        # Go agent listener (runs continuously)
        self._go_agent_listener: Optional[GoAgentListener] = None
        self._go_agent_registry = GoAgentRegistry(stale_timeout_seconds=300)

        # API server for on-demand scans
        self._api_app: Optional[web.Application] = None
        self._api_runner: Optional[web.AppRunner] = None

    def _init_discovery_methods(self) -> None:
        """Initialize enabled discovery methods."""
        if self.config.enable_ad_discovery and self.config.ad_server:
            self._discovery_methods.append(ADDiscovery(
                server=self.config.ad_server,
                base_dn=self.config.ad_base_dn or "",
                bind_dn=self.config.ad_bind_dn,
                bind_password=self.config.ad_bind_password,
            ))
            logger.info("AD discovery enabled")

        if self.config.enable_arp_discovery:
            self._discovery_methods.append(ARPDiscovery())
            logger.info("ARP discovery enabled")

        if self.config.enable_nmap_discovery and self.config.network_ranges:
            self._discovery_methods.append(NmapDiscovery(
                network_ranges=self.config.network_ranges,
                scan_arguments=self.config.nmap_arguments,
                host_timeout=self.config.host_timeout_seconds,
                max_concurrent=self.config.max_concurrent_scans,
            ))
            logger.info(f"Nmap discovery enabled for {self.config.network_ranges}")

    async def start(self) -> None:
        """Start the scanner service."""
        logger.info("Starting Network Scanner Service")
        self._running = True

        # Start Go agent listener if enabled
        if self.config.enable_go_agent_checkins:
            await self._start_go_agent_listener()

        # Start API server
        await self._start_api_server()

        # Run main loop
        await self._main_loop()

    async def stop(self) -> None:
        """Stop the scanner service."""
        logger.info("Stopping Network Scanner Service")
        self._running = False
        self._shutdown_event.set()

        # Stop Go agent listener
        if self._go_agent_listener:
            await self._go_agent_listener.stop()

        # Stop API server
        if self._api_runner:
            await self._api_runner.cleanup()

    async def _start_go_agent_listener(self) -> None:
        """Start Go agent listener."""
        self._go_agent_listener = GoAgentListener(
            host="0.0.0.0",
            port=self.config.api_port + 1,  # Agent port = API port + 1
            stale_timeout=300,
        )
        self._go_agent_listener.registry = self._go_agent_registry
        await self._go_agent_listener.start()
        logger.info(f"Go agent listener started on port {self.config.api_port + 1}")

    async def _start_api_server(self) -> None:
        """Start API server for on-demand scans."""
        self._api_app = web.Application()
        self._api_app.router.add_post("/api/scans/trigger", self._handle_trigger_scan)
        self._api_app.router.add_get("/api/scans/status", self._handle_scan_status)
        self._api_app.router.add_get("/api/devices", self._handle_list_devices)
        self._api_app.router.add_get("/api/devices/{device_id}", self._handle_get_device)
        self._api_app.router.add_put("/api/devices/{device_id}/policy", self._handle_update_policy)
        self._api_app.router.add_get("/api/health", self._handle_health)

        self._api_runner = web.AppRunner(self._api_app)
        await self._api_runner.setup()
        site = web.TCPSite(self._api_runner, self.config.api_host, self.config.api_port)
        await site.start()
        logger.info(f"API server started on {self.config.api_host}:{self.config.api_port}")

    async def _main_loop(self) -> None:
        """Main service loop - schedules and runs scans."""
        logger.info("Scanner main loop started")

        # Run initial scan on startup
        logger.info("Running initial discovery scan")
        await self.run_scan(triggered_by="startup")

        while self._running:
            try:
                # Check if it's time for scheduled scan (2 AM daily)
                now = datetime.now(timezone.utc)
                if (now.hour == self.config.daily_scan_hour and
                    now.minute == self.config.daily_scan_minute):
                    logger.info("Running scheduled daily scan")
                    await self.run_scan(triggered_by="schedule")
                    # Wait until next minute to avoid re-triggering
                    await asyncio.sleep(60)

                # Sleep for a minute before checking again
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=60.0,
                    )
                    # Shutdown requested
                    break
                except asyncio.TimeoutError:
                    # Normal timeout, continue loop
                    pass

            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(60)

        logger.info("Scanner main loop stopped")

    async def run_scan(
        self,
        scan_type: str = "full",
        triggered_by: str = "manual",
    ) -> dict:
        """
        Run a network discovery scan.

        Args:
            scan_type: Type of scan (full, quick)
            triggered_by: Who triggered the scan

        Returns:
            Scan result summary
        """
        import uuid
        scan_id = str(uuid.uuid4())
        started_at = now_utc()

        logger.info(f"Starting {scan_type} scan (id={scan_id}, triggered_by={triggered_by})")

        # Create scan record
        self.db.create_scan_record(scan_id, scan_type, started_at, triggered_by)

        all_discovered: list[DiscoveredDevice] = []
        methods_used: list[str] = []
        medical_count = 0
        new_count = 0
        changed_count = 0

        try:
            # Run each discovery method
            for method in self._discovery_methods:
                try:
                    if not await method.is_available():
                        logger.warning(f"Discovery method {method.name} not available")
                        continue

                    logger.info(f"Running {method.name} discovery")
                    devices = await method.discover()
                    all_discovered.extend(devices)
                    methods_used.append(method.name)
                    logger.info(f"{method.name} found {len(devices)} devices")

                except Exception as e:
                    logger.error(f"Error in {method.name} discovery: {e}")

            # Add Go agent devices
            if self._go_agent_registry:
                from .discovery import GoAgentDiscovery
                agent_discovery = GoAgentDiscovery(self._go_agent_registry)
                agent_devices = await agent_discovery.discover()
                all_discovered.extend(agent_devices)
                if agent_devices:
                    methods_used.append("go_agent")

            # Deduplicate by IP
            unique_devices = self._dedupe_by_ip(all_discovered)
            logger.info(f"Total unique devices discovered: {len(unique_devices)}")

            # Process and store each device
            for discovered in unique_devices:
                try:
                    device = discovered_to_device(discovered)

                    # Track medical devices
                    if device.medical_device:
                        medical_count += 1
                        logger.warning(
                            f"Medical device EXCLUDED: {device.ip_address} "
                            f"({device.hostname or 'unknown'})"
                        )

                    # Store in database
                    is_new, is_changed = self.db.upsert_device(device)
                    if is_new:
                        new_count += 1
                    elif is_changed:
                        changed_count += 1

                    # Store ports if available
                    if discovered.open_ports:
                        from ._types import DevicePort, DeviceStatus
                        ports = [
                            DevicePort(
                                device_id=device.id,
                                port=p,
                                service_name=discovered.port_services.get(p),
                            )
                            for p in discovered.open_ports
                        ]
                        self.db.upsert_device_ports(device.id, ports)

                        # Promote to monitored so compliance checks apply
                        if device.can_be_scanned and device.status == DeviceStatus.DISCOVERED:
                            self.db.update_device_status(device.id, DeviceStatus.MONITORED)

                except Exception as e:
                    logger.error(f"Error processing device {discovered.ip_address}: {e}")

            # Complete scan record
            self.db.complete_scan(
                scan_id=scan_id,
                devices_found=len(unique_devices),
                new_devices=new_count,
                changed_devices=changed_count,
                medical_devices_excluded=medical_count,
                methods_used=methods_used,
                network_ranges=self.config.network_ranges,
            )

            # Run compliance checks on devices with port data
            compliance_summary = {}
            try:
                from .compliance.runner import run_compliance_checks
                compliance_summary = await run_compliance_checks(self.db)
            except Exception as e:
                logger.error(f"Compliance checks failed: {e}")

            result = {
                "scan_id": scan_id,
                "status": "completed",
                "devices_found": len(unique_devices),
                "new_devices": new_count,
                "changed_devices": changed_count,
                "medical_devices_excluded": medical_count,
                "methods_used": methods_used,
                "compliance": compliance_summary,
            }

            logger.info(
                f"Scan completed: {len(unique_devices)} devices found, "
                f"{new_count} new, {changed_count} changed, "
                f"{medical_count} medical excluded"
            )
            if compliance_summary.get("devices_checked"):
                logger.info(
                    f"Compliance: {compliance_summary['devices_checked']} checked — "
                    f"{compliance_summary.get('passed', 0)} compliant, "
                    f"{compliance_summary.get('warned', 0)} warned, "
                    f"{compliance_summary.get('failed', 0)} failed"
                )

            return result

        except Exception as e:
            logger.error(f"Scan failed: {e}")
            self.db.complete_scan(
                scan_id=scan_id,
                devices_found=0,
                new_devices=0,
                changed_devices=0,
                medical_devices_excluded=0,
                methods_used=methods_used,
                network_ranges=self.config.network_ranges,
                error_message=str(e),
            )
            return {
                "scan_id": scan_id,
                "status": "failed",
                "error": str(e),
            }

    def _dedupe_by_ip(
        self,
        devices: list[DiscoveredDevice],
    ) -> list[DiscoveredDevice]:
        """Deduplicate devices by IP, preferring richer data."""
        by_ip: dict[str, DiscoveredDevice] = {}

        for device in devices:
            ip = device.ip_address
            if ip in by_ip:
                # Merge data, preferring non-None values
                existing = by_ip[ip]
                if not existing.hostname and device.hostname:
                    existing.hostname = device.hostname
                if not existing.mac_address and device.mac_address:
                    existing.mac_address = device.mac_address
                if not existing.os_name and device.os_name:
                    existing.os_name = device.os_name
                if device.open_ports:
                    # Merge ports
                    existing_ports = set(existing.open_ports)
                    existing.open_ports = list(existing_ports | set(device.open_ports))
                    existing.port_services.update(device.port_services)
            else:
                by_ip[ip] = device

        return list(by_ip.values())

    # -------------------------------------------------------------------------
    # API Handlers
    # -------------------------------------------------------------------------

    async def _handle_trigger_scan(self, request: web.Request) -> web.Response:
        """Handle POST /api/scans/trigger."""
        try:
            data = await request.json() if request.body_exists else {}
            scan_type = data.get("scan_type", "full")

            # Run scan in background
            asyncio.create_task(self.run_scan(
                scan_type=scan_type,
                triggered_by="api",
            ))

            return web.json_response({
                "status": "started",
                "message": f"Scan triggered ({scan_type})",
            })

        except Exception as e:
            return web.json_response(
                {"status": "error", "message": str(e)},
                status=500,
            )

    async def _handle_scan_status(self, request: web.Request) -> web.Response:
        """Handle GET /api/scans/status."""
        try:
            latest = self.db.get_latest_scan()
            history = self.db.get_scan_history(limit=10)

            return web.json_response({
                "latest": {
                    "id": latest.id,
                    "status": latest.status,
                    "devices_found": latest.devices_found,
                    "started_at": latest.started_at.isoformat(),
                    "completed_at": latest.completed_at.isoformat() if latest.completed_at else None,
                } if latest else None,
                "history": [
                    {
                        "id": s.id,
                        "scan_type": s.scan_type,
                        "status": s.status,
                        "devices_found": s.devices_found,
                        "started_at": s.started_at.isoformat(),
                    }
                    for s in history
                ],
            })

        except Exception as e:
            return web.json_response(
                {"status": "error", "message": str(e)},
                status=500,
            )

    async def _handle_list_devices(self, request: web.Request) -> web.Response:
        """Handle GET /api/devices."""
        try:
            device_type = request.query.get("type")
            status = request.query.get("status")
            limit = int(request.query.get("limit", "100"))
            offset = int(request.query.get("offset", "0"))

            from ._types import DeviceType, DeviceStatus

            type_filter = DeviceType(device_type) if device_type else None
            status_filter = DeviceStatus(status) if status else None

            devices = self.db.get_devices(
                device_type=type_filter,
                status=status_filter,
                limit=limit,
                offset=offset,
            )

            counts = self.db.get_device_counts()

            return web.json_response({
                "devices": [
                    {
                        "id": d.id,
                        "hostname": d.hostname,
                        "ip_address": d.ip_address,
                        "mac_address": d.mac_address,
                        "device_type": d.device_type.value,
                        "os_name": d.os_name,
                        "status": d.status.value,
                        "compliance_status": d.compliance_status.value,
                        "medical_device": d.medical_device,
                        "scan_policy": d.scan_policy.value,
                        "last_seen_at": d.last_seen_at.isoformat(),
                    }
                    for d in devices
                ],
                "total": counts["total"],
                "counts": counts,
            })

        except Exception as e:
            return web.json_response(
                {"status": "error", "message": str(e)},
                status=500,
            )

    async def _handle_get_device(self, request: web.Request) -> web.Response:
        """Handle GET /api/devices/{device_id}."""
        try:
            device_id = request.match_info["device_id"]
            device = self.db.get_device(device_id)

            if not device:
                return web.json_response(
                    {"status": "error", "message": "Device not found"},
                    status=404,
                )

            ports = self.db.get_device_ports(device_id)
            compliance = self.db.get_device_compliance_history(device_id, limit=20)
            notes = self.db.get_device_notes(device_id)

            return web.json_response({
                "device": {
                    "id": device.id,
                    "hostname": device.hostname,
                    "ip_address": device.ip_address,
                    "mac_address": device.mac_address,
                    "device_type": device.device_type.value,
                    "os_name": device.os_name,
                    "os_version": device.os_version,
                    "manufacturer": device.manufacturer,
                    "model": device.model,
                    "status": device.status.value,
                    "compliance_status": device.compliance_status.value,
                    "medical_device": device.medical_device,
                    "scan_policy": device.scan_policy.value,
                    "manually_opted_in": device.manually_opted_in,
                    "phi_access_flag": device.phi_access_flag,
                    "first_seen_at": device.first_seen_at.isoformat(),
                    "last_seen_at": device.last_seen_at.isoformat(),
                    "last_scan_at": device.last_scan_at.isoformat() if device.last_scan_at else None,
                },
                "ports": [
                    {"port": p.port, "protocol": p.protocol, "service": p.service_name}
                    for p in ports
                ],
                "compliance_history": [
                    {
                        "check_type": c.check_type,
                        "status": c.status,
                        "hipaa_control": c.hipaa_control,
                        "checked_at": c.checked_at.isoformat(),
                    }
                    for c in compliance
                ],
                "notes": notes,
            })

        except Exception as e:
            return web.json_response(
                {"status": "error", "message": str(e)},
                status=500,
            )

    async def _handle_update_policy(self, request: web.Request) -> web.Response:
        """Handle PUT /api/devices/{device_id}/policy."""
        try:
            device_id = request.match_info["device_id"]
            data = await request.json()

            from ._types import ScanPolicy

            scan_policy = ScanPolicy(data["scan_policy"]) if "scan_policy" in data else None
            manually_opted_in = data.get("manually_opted_in")
            phi_access_flag = data.get("phi_access_flag")

            success = self.db.update_device_policy(
                device_id,
                scan_policy=scan_policy,
                manually_opted_in=manually_opted_in,
                phi_access_flag=phi_access_flag,
            )

            if success:
                return web.json_response({"status": "ok"})
            else:
                return web.json_response(
                    {"status": "error", "message": "Device not found"},
                    status=404,
                )

        except Exception as e:
            return web.json_response(
                {"status": "error", "message": str(e)},
                status=500,
            )

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Handle GET /api/health."""
        counts = self.db.get_device_counts()
        latest = self.db.get_latest_scan()

        return web.json_response({
            "status": "ok",
            "service": "network-scanner",
            "devices": counts["total"],
            "medical_excluded": counts["medical_excluded"],
            "last_scan": latest.started_at.isoformat() if latest else None,
        })


def main():
    """Entry point for network-scanner service."""
    import argparse

    parser = argparse.ArgumentParser(description="MSP Network Scanner Service")
    parser.add_argument("--config", type=str, help="Path to config file")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="API host")
    parser.add_argument("--port", type=int, default=8082, help="API port")
    parser.add_argument("--log-level", type=str, default="INFO", help="Log level")
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Load configuration
    if args.config:
        config = ScannerConfig.from_yaml(Path(args.config))
    else:
        config = ScannerConfig.from_env()

    # Override with CLI args
    config.api_host = args.host
    config.api_port = args.port
    config.log_level = args.log_level

    # Load credentials from separate file
    config.load_credentials()

    # Validate
    errors = config.validate()
    if errors:
        for error in errors:
            logger.error(f"Config error: {error}")
        if any("CRITICAL" in e for e in errors):
            sys.exit(1)

    # Create service
    service = NetworkScannerService(config)

    # Handle signals
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def signal_handler():
        logger.info("Received shutdown signal")
        asyncio.create_task(service.stop())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    try:
        loop.run_until_complete(service.start())
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        loop.run_until_complete(service.stop())
        loop.close()


if __name__ == "__main__":
    main()
