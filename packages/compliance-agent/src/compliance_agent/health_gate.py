"""Health Gate - Post-boot health verification for A/B updates.

Phase 13: Runs early in boot to verify system health after updates.
If health checks fail repeatedly, triggers automatic rollback to previous partition.

This module is designed to be run as a systemd service that starts
before the main compliance-agent service.
"""

import asyncio
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)

# Configuration constants
MAX_BOOT_ATTEMPTS = 3
HEALTH_CHECK_TIMEOUT = 60  # seconds

# State file paths
STATE_DIR = Path("/var/lib/msp/update")
UPDATE_STATE_FILE = STATE_DIR / "update_state.json"
BOOT_COUNT_FILE = STATE_DIR / "boot_count"
AB_STATE_FILE = Path("/boot/ab_state")

# Partition layout
PARTITION_A = "/dev/sda2"
PARTITION_B = "/dev/sda3"


def get_active_partition_from_cmdline() -> Optional[str]:
    """Detect active partition from kernel command line."""
    try:
        cmdline = Path("/proc/cmdline").read_text()
        if "ab.partition=B" in cmdline:
            return "B"
        elif "ab.partition=A" in cmdline:
            return "A"
    except Exception:
        pass
    return None


def get_active_partition_from_state() -> Optional[str]:
    """Read active partition from ab_state file."""
    if AB_STATE_FILE.exists():
        try:
            content = AB_STATE_FILE.read_text().strip()
            # Handle GRUB source format: set active_partition="A"
            if 'active_partition=' in content:
                # Extract value from set active_partition="X"
                import re
                match = re.search(r'active_partition="?([AB])"?', content)
                if match:
                    return match.group(1)
            # Handle simple format: just "A" or "B"
            elif content.upper() in ('A', 'B'):
                return content.upper()
        except Exception:
            pass
    return None


def get_active_partition() -> str:
    """Get the currently active partition with fallback detection."""
    # Priority 1: Kernel command line (most reliable)
    partition = get_active_partition_from_cmdline()
    if partition:
        return partition

    # Priority 2: ab_state file
    partition = get_active_partition_from_state()
    if partition:
        return partition

    # Priority 3: Mount point detection
    try:
        result = subprocess.run(
            ["findmnt", "-n", "-o", "SOURCE", "/"],
            capture_output=True,
            text=True,
        )
        root_device = result.stdout.strip()
        if PARTITION_B in root_device:
            return 'B'
    except Exception:
        pass

    # Default to A
    return 'A'


def load_update_state() -> Optional[Dict[str, Any]]:
    """Load saved update state from post-update verification."""
    if UPDATE_STATE_FILE.exists():
        try:
            return json.loads(UPDATE_STATE_FILE.read_text())
        except Exception as e:
            logger.warning(f"Failed to load update state: {e}")
    return None


def clear_update_state():
    """Clear update state after completion."""
    if UPDATE_STATE_FILE.exists():
        try:
            UPDATE_STATE_FILE.unlink()
            logger.info("Cleared update state")
        except Exception as e:
            logger.warning(f"Failed to clear update state: {e}")


def get_boot_count() -> int:
    """Get current boot attempt count."""
    if BOOT_COUNT_FILE.exists():
        try:
            return int(BOOT_COUNT_FILE.read_text().strip())
        except (ValueError, OSError):
            pass
    return 0


def increment_boot_count() -> int:
    """Increment and return boot attempt counter."""
    count = get_boot_count() + 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    BOOT_COUNT_FILE.write_text(str(count))
    return count


def clear_boot_count():
    """Clear boot attempt counter."""
    if BOOT_COUNT_FILE.exists():
        BOOT_COUNT_FILE.write_text("0")


def set_next_boot(partition: str) -> bool:
    """Set bootloader to boot from specified partition on next reboot.

    Writes in GRUB-compatible source format that can be included via `source`.
    """
    if partition not in ('A', 'B'):
        logger.error(f"Invalid partition: {partition}")
        return False

    try:
        # Ensure /boot is writable
        AB_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Write in GRUB source format
        AB_STATE_FILE.write_text(f'set active_partition="{partition}"\n')
        logger.info(f"Set next boot partition to {partition}")
        return True
    except Exception as e:
        logger.error(f"Failed to set next boot partition: {e}")
        return False


def mark_current_as_good():
    """Mark current partition as known-good."""
    active = get_active_partition()
    set_next_boot(active)
    clear_boot_count()
    logger.info(f"Marked partition {active} as good")


async def check_network(api_base_url: str) -> Dict[str, Any]:
    """Check network connectivity to Central Command."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{api_base_url}/health",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return {"passed": resp.status == 200}
    except Exception as e:
        return {"passed": False, "error": str(e)}


async def check_agent_service() -> Dict[str, Any]:
    """Check if compliance-agent service is active."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "compliance-agent"],
            capture_output=True,
            text=True,
        )
        return {"passed": result.returncode == 0}
    except Exception as e:
        return {"passed": False, "error": str(e)}


async def check_ntp_sync() -> Dict[str, Any]:
    """Check NTP synchronization status."""
    try:
        result = subprocess.run(
            ["timedatectl", "show", "--property=NTPSynchronized"],
            capture_output=True,
            text=True,
        )
        synced = "NTPSynchronized=yes" in result.stdout
        return {"passed": synced}
    except Exception as e:
        return {"passed": False, "error": str(e)}


async def check_disk_space() -> Dict[str, Any]:
    """Check disk space on data partition."""
    try:
        result = subprocess.run(
            ["df", "/var/lib/msp", "--output=pcent"],
            capture_output=True,
            text=True,
        )
        lines = result.stdout.strip().split("\n")
        if len(lines) >= 2:
            pct = int(lines[1].strip().rstrip("%"))
            return {"passed": pct < 90, "usage_percent": pct}
        return {"passed": True}
    except Exception as e:
        return {"passed": False, "error": str(e)}


async def run_health_checks(api_base_url: str) -> Tuple[bool, Dict[str, Any]]:
    """Run all post-boot health checks.

    Returns:
        (success, results_dict)
    """
    results = {}
    all_passed = True

    # Critical checks (must pass)
    results["network"] = await check_network(api_base_url)
    if not results["network"]["passed"]:
        all_passed = False

    # Note: Agent service check is tricky since health-gate runs before agent
    # We skip this check - if agent fails to start, it will be caught by systemd

    # Non-critical checks
    results["ntp"] = await check_ntp_sync()
    results["disk"] = await check_disk_space()

    # Disk space is critical if too low
    if not results["disk"].get("passed", True):
        all_passed = False

    return all_passed, results


async def report_status(
    api_base_url: str,
    api_key: str,
    appliance_id: str,
    status: str,
    error_message: Optional[str] = None,
    boot_attempts: Optional[int] = None,
    health_check_result: Optional[Dict] = None,
) -> bool:
    """Report health gate status to Central Command."""
    url = f"{api_base_url}/api/fleet/appliances/{appliance_id}/update-status"

    payload = {"status": status}
    if error_message:
        payload["error_message"] = error_message
    if boot_attempts is not None:
        payload["boot_attempts"] = boot_attempts
    if health_check_result:
        payload["health_check_result"] = health_check_result

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
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


def load_config() -> Optional[Dict[str, Any]]:
    """Load appliance config."""
    import yaml

    config_path = Path("/var/lib/msp/config.yaml")
    if config_path.exists():
        try:
            return yaml.safe_load(config_path.read_text())
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
    return None


async def run_health_gate() -> int:
    """Main health gate logic.

    Returns:
        0 if healthy (or no update pending)
        1 if unhealthy but will retry
        2 if rollback triggered
    """
    # Check if we're verifying a post-update boot
    state = load_update_state()
    if not state:
        # No pending update, nothing to verify
        logger.info("No update state found, health gate pass-through")
        return 0

    active = get_active_partition()
    target = state.get("target_partition")

    # Are we on the expected partition?
    if active != target:
        logger.warning(
            f"Not on expected partition (active={active}, target={target}), "
            "rollback may have occurred"
        )
        clear_update_state()
        return 0

    # Increment boot count
    boot_count = increment_boot_count()
    logger.info(f"Post-boot health check, attempt {boot_count}/{MAX_BOOT_ATTEMPTS}")

    # Load config for API communication
    config = load_config()
    if not config:
        logger.error("No config found, cannot verify update")
        return 1

    api_base_url = config.get("api_base_url", "https://api.osiriscare.net")
    api_key = config.get("api_key", "")
    appliance_id = config.get("appliance_id", "unknown")

    # Report that we're verifying
    await report_status(
        api_base_url, api_key, appliance_id,
        "verifying",
        boot_attempts=boot_count
    )

    # Run health checks
    passed, results = await run_health_checks(api_base_url)

    if passed:
        # Success!
        mark_current_as_good()
        clear_update_state()
        await report_status(
            api_base_url, api_key, appliance_id,
            "succeeded",
            health_check_result=results
        )
        logger.info("Update completed successfully, system healthy")
        return 0
    else:
        logger.warning(f"Health check failed: {results}")

        if boot_count >= MAX_BOOT_ATTEMPTS:
            # Too many failures, rollback
            logger.error(
                f"Max boot attempts ({MAX_BOOT_ATTEMPTS}) exceeded, rolling back"
            )
            await report_status(
                api_base_url, api_key, appliance_id,
                "rolled_back",
                error_message=f"Health check failed after {boot_count} attempts",
                health_check_result=results
            )

            # Switch back to other partition
            other_partition = 'B' if active == 'A' else 'A'
            set_next_boot(other_partition)
            clear_update_state()
            clear_boot_count()

            # Reboot to rollback
            logger.info(f"Rebooting to partition {other_partition}")
            subprocess.run(["systemctl", "reboot"])
            return 2
        else:
            # Will retry on next boot
            await report_status(
                api_base_url, api_key, appliance_id,
                "verifying",
                error_message="Health check failed, will retry",
                boot_attempts=boot_count,
                health_check_result=results
            )
            return 1


def main() -> int:
    """CLI entry point for health gate."""
    import argparse

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="OsirisCare Health Gate")
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current partition and update status"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run health checks without boot gating logic"
    )
    args = parser.parse_args()

    if args.status:
        active = get_active_partition()
        boot_count = get_boot_count()
        state = load_update_state()

        print(f"Active partition: {active}")
        print(f"Boot attempts: {boot_count}/{MAX_BOOT_ATTEMPTS}")
        if state:
            print(f"Pending update:")
            print(f"  Version: {state.get('version', 'unknown')}")
            print(f"  Target: {state.get('target_partition', 'unknown')}")
            print(f"  Started: {state.get('started_at', 'unknown')}")
        else:
            print("No pending update")
        return 0

    if args.check:
        config = load_config()
        if not config:
            print("No config found")
            return 1

        api_base_url = config.get("api_base_url", "https://api.osiriscare.net")

        async def run_checks():
            passed, results = await run_health_checks(api_base_url)
            print(f"Health check {'PASSED' if passed else 'FAILED'}")
            for name, result in results.items():
                status = "OK" if result.get("passed") else "FAIL"
                extra = ""
                if "usage_percent" in result:
                    extra = f" ({result['usage_percent']}%)"
                if "error" in result:
                    extra = f" ({result['error']})"
                print(f"  {name}: {status}{extra}")
            return 0 if passed else 1

        return asyncio.run(run_checks())

    # Default: run health gate
    return asyncio.run(run_health_gate())


if __name__ == "__main__":
    sys.exit(main())
