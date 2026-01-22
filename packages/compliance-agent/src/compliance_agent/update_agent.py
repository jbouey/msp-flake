"""Update Agent - Zero-Touch Fleet Update System.

Phase 13: Handles ISO downloads, verification, partition switching, and health gates.
Designed for A/B partition scheme with automatic rollback on failure.
"""

import asyncio
import hashlib
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone, time
from pathlib import Path
from typing import Optional, Dict, Any

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class UpdateInfo:
    """Information about a pending update."""
    update_id: str
    rollout_id: str
    version: str
    iso_url: str
    sha256: str
    size_bytes: Optional[int]
    maintenance_window: Dict[str, Any]
    current_status: str


@dataclass
class PartitionInfo:
    """A/B partition information."""
    active: str  # 'A' or 'B'
    standby: str  # 'A' or 'B'
    active_device: str  # /dev/sda2 or /dev/sda3
    standby_device: str  # /dev/sda2 or /dev/sda3
    boot_count: int


class UpdateAgent:
    """Manages zero-touch updates for NixOS appliances.

    Supports A/B partition scheme with:
    - Staged rollout participation
    - Maintenance window enforcement
    - SHA256 checksum verification
    - Automatic rollback on health check failure
    """

    # Partition layout (configured in iso/appliance-image.nix)
    PARTITION_A = "/dev/sda2"
    PARTITION_B = "/dev/sda3"
    DATA_PARTITION = "/dev/sda4"

    # State files
    STATE_DIR = Path("/var/lib/msp/update")
    AB_STATE_FILE = Path("/boot/ab_state")
    BOOT_COUNT_FILE = Path("/var/lib/msp/update/boot_count")
    UPDATE_STATE_FILE = Path("/var/lib/msp/update/update_state.json")

    # Health check settings
    MAX_BOOT_ATTEMPTS = 3
    HEALTH_CHECK_TIMEOUT = 60  # seconds

    def __init__(
        self,
        api_base_url: str,
        api_key: str,
        appliance_id: str,
        download_dir: Path = Path("/var/lib/msp/update/downloads"),
    ):
        self.api_base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.appliance_id = appliance_id
        self.download_dir = download_dir

        # Ensure directories exist
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.STATE_DIR.mkdir(parents=True, exist_ok=True)

    # =========================================================================
    # Partition Management
    # =========================================================================

    def get_partition_info(self) -> PartitionInfo:
        """Get current A/B partition state."""
        import re

        active = None

        # Priority 1: Kernel command line (most reliable during boot)
        try:
            cmdline = Path("/proc/cmdline").read_text()
            if "ab.partition=B" in cmdline:
                active = 'B'
            elif "ab.partition=A" in cmdline:
                active = 'A'
        except Exception:
            pass

        # Priority 2: Read ab_state file
        if active is None and self.AB_STATE_FILE.exists():
            try:
                content = self.AB_STATE_FILE.read_text().strip()
                # Handle GRUB source format: set active_partition="A"
                if 'active_partition=' in content:
                    match = re.search(r'active_partition="?([AB])"?', content)
                    if match:
                        active = match.group(1)
                # Handle simple format: just "A" or "B"
                elif content.upper() in ('A', 'B'):
                    active = content.upper()
            except Exception:
                pass

        # Priority 3: Detect from current mount
        if active is None:
            try:
                result = subprocess.run(
                    ["findmnt", "-n", "-o", "SOURCE", "/"],
                    capture_output=True,
                    text=True,
                )
                root_device = result.stdout.strip()
                if self.PARTITION_B in root_device:
                    active = 'B'
                else:
                    active = 'A'
            except Exception:
                active = 'A'

        standby = 'B' if active == 'A' else 'A'
        active_device = self.PARTITION_A if active == 'A' else self.PARTITION_B
        standby_device = self.PARTITION_B if active == 'A' else self.PARTITION_A

        # Read boot count
        boot_count = 0
        if self.BOOT_COUNT_FILE.exists():
            try:
                boot_count = int(self.BOOT_COUNT_FILE.read_text().strip())
            except ValueError:
                pass

        return PartitionInfo(
            active=active,
            standby=standby,
            active_device=active_device,
            standby_device=standby_device,
            boot_count=boot_count,
        )

    def set_next_boot(self, partition: str) -> bool:
        """Configure bootloader to boot from specified partition on next reboot.

        Writes ab_state in GRUB-compatible source format that can be included
        via `source $ab_state_file` in grub.cfg.

        Args:
            partition: 'A' or 'B'

        Returns:
            True if successful
        """
        if partition not in ('A', 'B'):
            logger.error(f"Invalid partition: {partition}")
            return False

        try:
            # Ensure /boot directory exists
            self.AB_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

            # Write ab_state in GRUB source format
            # This allows: source $ab_state_file in grub.cfg
            self.AB_STATE_FILE.write_text(f'set active_partition="{partition}"\n')

            logger.info(f"Set next boot partition to {partition}")
            return True
        except Exception as e:
            logger.error(f"Failed to set next boot partition: {e}")
            return False

    def increment_boot_count(self) -> int:
        """Increment boot attempt counter."""
        info = self.get_partition_info()
        new_count = info.boot_count + 1
        self.BOOT_COUNT_FILE.write_text(str(new_count))
        return new_count

    def clear_boot_count(self):
        """Clear boot attempt counter (called after successful health check)."""
        if self.BOOT_COUNT_FILE.exists():
            self.BOOT_COUNT_FILE.write_text("0")

    def mark_current_as_good(self):
        """Mark current partition as known-good."""
        info = self.get_partition_info()
        self.set_next_boot(info.active)
        self.clear_boot_count()
        logger.info(f"Marked partition {info.active} as good")

    # =========================================================================
    # API Communication
    # =========================================================================

    async def check_for_update(self) -> Optional[UpdateInfo]:
        """Check Central Command for pending updates."""
        url = f"{self.api_base_url}/api/fleet/appliances/{self.appliance_id}/pending-update"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"Failed to check for updates: {resp.status}")
                        return None

                    data = await resp.json()

                    if not data.get("update_available"):
                        return None

                    update = data["update"]
                    return UpdateInfo(
                        update_id=update["update_id"],
                        rollout_id=update["rollout_id"],
                        version=update["version"],
                        iso_url=update["iso_url"],
                        sha256=update["sha256"],
                        size_bytes=update.get("size_bytes"),
                        maintenance_window=update.get("maintenance_window", {}),
                        current_status=update.get("current_status", "notified"),
                    )
        except Exception as e:
            logger.error(f"Error checking for updates: {e}")
            return None

    async def report_status(
        self,
        status: str,
        error_message: Optional[str] = None,
        error_code: Optional[str] = None,
        boot_attempts: Optional[int] = None,
        health_check_result: Optional[dict] = None,
    ) -> bool:
        """Report update status to Central Command."""
        url = f"{self.api_base_url}/api/fleet/appliances/{self.appliance_id}/update-status"

        payload = {"status": status}
        if error_message:
            payload["error_message"] = error_message
        if error_code:
            payload["error_code"] = error_code
        if boot_attempts is not None:
            payload["boot_attempts"] = boot_attempts
        if health_check_result:
            payload["health_check_result"] = health_check_result

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        logger.info(f"Reported status: {status}")
                        return True
                    else:
                        logger.warning(f"Failed to report status: {resp.status}")
                        return False
        except Exception as e:
            logger.error(f"Error reporting status: {e}")
            return False

    # =========================================================================
    # Download & Verification
    # =========================================================================

    async def download_iso(self, update: UpdateInfo, progress_callback=None) -> Optional[Path]:
        """Download ISO to standby partition or temp location.

        Args:
            update: Update information
            progress_callback: Optional callback(bytes_downloaded, total_bytes)

        Returns:
            Path to downloaded file, or None on failure
        """
        iso_path = self.download_dir / f"{update.version}.iso"

        # Resume support: check if partial download exists
        existing_size = 0
        if iso_path.exists():
            existing_size = iso_path.stat().st_size
            if update.size_bytes and existing_size == update.size_bytes:
                # Already downloaded
                logger.info(f"ISO already downloaded: {iso_path}")
                return iso_path

        try:
            await self.report_status("downloading")

            async with aiohttp.ClientSession() as session:
                headers = {}
                if existing_size > 0:
                    headers["Range"] = f"bytes={existing_size}-"

                async with session.get(
                    update.iso_url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=3600),  # 1 hour for large downloads
                ) as resp:
                    if resp.status not in (200, 206):
                        logger.error(f"Download failed: {resp.status}")
                        await self.report_status("failed", error_message=f"Download failed: {resp.status}")
                        return None

                    total = int(resp.headers.get("Content-Length", 0)) + existing_size
                    downloaded = existing_size

                    mode = "ab" if existing_size > 0 else "wb"
                    with open(iso_path, mode) as f:
                        async for chunk in resp.content.iter_chunked(1024 * 1024):  # 1MB chunks
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_callback:
                                progress_callback(downloaded, total)

            logger.info(f"Downloaded ISO to {iso_path}")
            return iso_path

        except Exception as e:
            logger.error(f"Download error: {e}")
            await self.report_status("failed", error_message=str(e), error_code="DOWNLOAD_ERROR")
            return None

    def verify_checksum(self, iso_path: Path, expected_sha256: str) -> bool:
        """Verify ISO checksum."""
        sha256 = hashlib.sha256()

        with open(iso_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                sha256.update(chunk)

        actual = sha256.hexdigest()
        if actual.lower() != expected_sha256.lower():
            logger.error(f"Checksum mismatch: expected {expected_sha256}, got {actual}")
            return False

        logger.info("Checksum verified")
        return True

    # =========================================================================
    # Update Application
    # =========================================================================

    async def apply_update(self, update: UpdateInfo, iso_path: Path) -> bool:
        """Apply update to standby partition.

        Args:
            update: Update information
            iso_path: Path to verified ISO

        Returns:
            True if ready for reboot
        """
        info = self.get_partition_info()

        try:
            # Write ISO to standby partition
            logger.info(f"Writing ISO to standby partition {info.standby_device}")

            result = subprocess.run(
                ["dd", f"if={iso_path}", f"of={info.standby_device}", "bs=4M", "status=progress"],
                capture_output=True,
                text=True,
                timeout=1800,  # 30 minutes
            )

            if result.returncode != 0:
                logger.error(f"dd failed: {result.stderr}")
                await self.report_status("failed", error_message=result.stderr, error_code="DD_FAILED")
                return False

            # Sync to ensure writes are flushed
            subprocess.run(["sync"], check=True)

            # Set next boot to standby partition
            if not self.set_next_boot(info.standby):
                await self.report_status("failed", error_message="Failed to set boot partition")
                return False

            # Save update state
            self._save_update_state(update, info.standby)

            await self.report_status("ready")
            logger.info(f"Update applied, ready to reboot into partition {info.standby}")
            return True

        except subprocess.TimeoutExpired:
            logger.error("dd timed out")
            await self.report_status("failed", error_message="Write timed out", error_code="DD_TIMEOUT")
            return False
        except Exception as e:
            logger.error(f"Apply error: {e}")
            await self.report_status("failed", error_message=str(e))
            return False

    def _save_update_state(self, update: UpdateInfo, target_partition: str):
        """Save update state for post-reboot verification."""
        state = {
            "update_id": update.update_id,
            "rollout_id": update.rollout_id,
            "version": update.version,
            "target_partition": target_partition,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        self.UPDATE_STATE_FILE.write_text(json.dumps(state))

    def _load_update_state(self) -> Optional[dict]:
        """Load saved update state."""
        if self.UPDATE_STATE_FILE.exists():
            try:
                return json.loads(self.UPDATE_STATE_FILE.read_text())
            except Exception:
                return None
        return None

    def _clear_update_state(self):
        """Clear update state after completion."""
        if self.UPDATE_STATE_FILE.exists():
            self.UPDATE_STATE_FILE.unlink()

    # =========================================================================
    # Maintenance Window
    # =========================================================================

    def is_in_maintenance_window(self, window: Dict[str, Any]) -> bool:
        """Check if current time is within maintenance window."""
        if not window:
            return True  # No window configured, always OK

        now = datetime.now()

        # Check day of week
        days = window.get("days", [])
        if days:
            current_day = now.strftime("%A").lower()
            if current_day not in [d.lower() for d in days]:
                return False

        # Check time range
        start_str = window.get("start", "00:00")
        end_str = window.get("end", "23:59")

        start_time = time(*map(int, start_str.split(":")))
        end_time = time(*map(int, end_str.split(":")))
        current_time = now.time()

        if start_time <= end_time:
            return start_time <= current_time <= end_time
        else:
            # Window crosses midnight
            return current_time >= start_time or current_time <= end_time

    async def wait_for_maintenance_window(self, window: Dict[str, Any]) -> bool:
        """Wait until maintenance window, checking periodically.

        Returns False if window never arrives (shouldn't happen).
        """
        check_interval = 300  # 5 minutes
        max_wait = 86400  # 24 hours
        waited = 0

        while waited < max_wait:
            if self.is_in_maintenance_window(window):
                return True

            logger.info(f"Waiting for maintenance window, next check in {check_interval}s")
            await asyncio.sleep(check_interval)
            waited += check_interval

        logger.warning("Gave up waiting for maintenance window")
        return False

    # =========================================================================
    # Health Checks
    # =========================================================================

    async def run_health_checks(self) -> tuple[bool, Dict[str, Any]]:
        """Run post-boot health checks.

        Returns:
            (success, results_dict)
        """
        results = {}
        all_passed = True

        # Check 1: Network connectivity
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_base_url}/health",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    results["network"] = {"passed": resp.status == 200}
        except Exception as e:
            results["network"] = {"passed": False, "error": str(e)}

        if not results["network"]["passed"]:
            all_passed = False

        # Check 2: Compliance agent running
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "compliance-agent"],
                capture_output=True,
                text=True,
            )
            results["agent"] = {"passed": result.returncode == 0}
        except Exception as e:
            results["agent"] = {"passed": False, "error": str(e)}

        if not results["agent"]["passed"]:
            all_passed = False

        # Check 3: NTP sync
        try:
            result = subprocess.run(
                ["timedatectl", "show", "--property=NTPSynchronized"],
                capture_output=True,
                text=True,
            )
            synced = "NTPSynchronized=yes" in result.stdout
            results["ntp"] = {"passed": synced}
        except Exception as e:
            results["ntp"] = {"passed": False, "error": str(e)}

        # NTP is non-critical, don't fail overall check

        # Check 4: Disk space
        try:
            result = subprocess.run(
                ["df", "/var/lib/msp", "--output=pcent"],
                capture_output=True,
                text=True,
            )
            # Parse percentage
            lines = result.stdout.strip().split("\n")
            if len(lines) >= 2:
                pct = int(lines[1].strip().rstrip("%"))
                results["disk"] = {"passed": pct < 90, "usage_percent": pct}
            else:
                results["disk"] = {"passed": True}
        except Exception as e:
            results["disk"] = {"passed": False, "error": str(e)}

        return all_passed, results

    async def post_boot_health_gate(self) -> bool:
        """Run health gate after boot.

        Called early in boot to verify system health after update.
        Will trigger rollback if health checks fail repeatedly.
        """
        # Check if we just completed an update
        state = self._load_update_state()
        if not state:
            # No pending update, nothing to verify
            return True

        info = self.get_partition_info()

        # Are we on the expected partition?
        if info.active != state.get("target_partition"):
            # We're on the wrong partition - rollback already happened
            logger.warning("Not on expected partition, rollback may have occurred")
            self._clear_update_state()
            return False

        boot_count = self.increment_boot_count()
        logger.info(f"Post-boot health check, attempt {boot_count}/{self.MAX_BOOT_ATTEMPTS}")

        # Report that we're verifying
        await self.report_status("verifying", boot_attempts=boot_count)

        # Run health checks
        passed, results = await self.run_health_checks()

        if passed:
            # Success!
            self.mark_current_as_good()
            self._clear_update_state()
            await self.report_status("succeeded", health_check_result=results)
            logger.info("Update completed successfully")
            return True
        else:
            logger.warning(f"Health check failed: {results}")

            if boot_count >= self.MAX_BOOT_ATTEMPTS:
                # Too many failures, rollback
                logger.error(f"Max boot attempts ({self.MAX_BOOT_ATTEMPTS}) exceeded, rolling back")
                await self.report_status("rolled_back",
                    error_message=f"Health check failed after {boot_count} attempts",
                    health_check_result=results)

                # Switch back to other partition
                other_partition = 'B' if info.active == 'A' else 'A'
                self.set_next_boot(other_partition)
                self._clear_update_state()

                # Reboot to rollback
                logger.info(f"Rebooting to partition {other_partition}")
                subprocess.run(["systemctl", "reboot"])
                return False
            else:
                # Will retry on next boot
                await self.report_status("verifying",
                    error_message=f"Health check failed, will retry",
                    boot_attempts=boot_count,
                    health_check_result=results)
                return False

    # =========================================================================
    # Main Update Flow
    # =========================================================================

    async def run_update_cycle(self):
        """Main update cycle - check for updates and apply if available."""
        # First, handle any pending post-boot verification
        await self.post_boot_health_gate()

        # Check for new updates
        update = await self.check_for_update()
        if not update:
            logger.debug("No updates available")
            return

        logger.info(f"Update available: {update.version}")

        # Already downloading or ready?
        if update.current_status in ("downloading", "ready", "rebooting"):
            logger.info(f"Update already in progress: {update.current_status}")
            return

        # Download ISO
        iso_path = await self.download_iso(update)
        if not iso_path:
            return

        # Verify checksum
        if not self.verify_checksum(iso_path, update.sha256):
            await self.report_status("failed",
                error_message="Checksum verification failed",
                error_code="CHECKSUM_MISMATCH")
            iso_path.unlink()
            return

        # Apply update (write to standby partition)
        if not await self.apply_update(update, iso_path):
            return

        # Clean up download
        iso_path.unlink()

        # Wait for maintenance window
        if not await self.wait_for_maintenance_window(update.maintenance_window):
            logger.warning("Failed to enter maintenance window")
            return

        # Report rebooting
        await self.report_status("rebooting")

        # Reboot!
        logger.info("Rebooting to apply update")
        subprocess.run(["systemctl", "reboot"])


# CLI for manual operations
def main():
    """CLI entry point for update agent."""
    import argparse

    parser = argparse.ArgumentParser(description="OsirisCare Update Agent")
    parser.add_argument("--check", action="store_true", help="Check for updates")
    parser.add_argument("--status", action="store_true", help="Show partition status")
    parser.add_argument("--rollback", action="store_true", help="Rollback to previous partition")
    parser.add_argument("--health", action="store_true", help="Run health checks")
    args = parser.parse_args()

    # Load config
    config_path = Path("/var/lib/msp/config.yaml")
    if not config_path.exists():
        print("Error: Config file not found")
        return 1

    import yaml
    config = yaml.safe_load(config_path.read_text())

    # Support both site_id (new) and appliance_id (legacy) config keys
    # Backend accepts either UUID or site_id
    appliance_id = config.get("site_id") or config.get("appliance_id", "unknown")

    agent = UpdateAgent(
        api_base_url=config.get("api_endpoint", config.get("api_base_url", "https://api.osiriscare.net")),
        api_key=config.get("api_key", ""),
        appliance_id=appliance_id,
    )

    if args.status:
        info = agent.get_partition_info()
        print(f"Active partition: {info.active}")
        print(f"Standby partition: {info.standby}")
        print(f"Active device: {info.active_device}")
        print(f"Standby device: {info.standby_device}")
        print(f"Boot count: {info.boot_count}")
        return 0

    if args.rollback:
        info = agent.get_partition_info()
        other = 'B' if info.active == 'A' else 'A'
        print(f"Rolling back to partition {other}")
        agent.set_next_boot(other)
        print("Reboot to complete rollback")
        return 0

    if args.health:
        async def run_health():
            passed, results = await agent.run_health_checks()
            print(f"Health check {'PASSED' if passed else 'FAILED'}")
            for name, result in results.items():
                status = "OK" if result.get("passed") else "FAIL"
                print(f"  {name}: {status}")
            return 0 if passed else 1

        return asyncio.run(run_health())

    if args.check:
        async def run_check():
            update = await agent.check_for_update()
            if update:
                print(f"Update available: {update.version}")
                print(f"  URL: {update.iso_url}")
                print(f"  SHA256: {update.sha256}")
            else:
                print("No updates available")
            return 0

        return asyncio.run(run_check())

    # Default: run update cycle
    asyncio.run(agent.run_update_cycle())
    return 0


if __name__ == "__main__":
    exit(main())
