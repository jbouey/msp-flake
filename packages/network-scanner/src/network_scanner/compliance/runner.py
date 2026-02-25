"""
Compliance check runner.

Evaluates all applicable network compliance checks against scannable devices
and stores results in the local database.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..device_db import DeviceDatabase
from .base import ComplianceCheck, ComplianceResult
from .network_checks import ALL_NETWORK_CHECKS

logger = logging.getLogger(__name__)


async def run_compliance_checks(
    db: DeviceDatabase,
    checks: Optional[list[ComplianceCheck]] = None,
) -> dict:
    """Run compliance checks on all scannable devices.

    Args:
        db: Device database instance
        checks: List of checks to run (defaults to ALL_NETWORK_CHECKS)

    Returns:
        Summary dict with devices_checked, passed, failed, warned counts.
    """
    if checks is None:
        checks = ALL_NETWORK_CHECKS

    devices = db.get_devices_for_scanning()
    if not devices:
        logger.info("No scannable devices for compliance checks")
        return {"devices_checked": 0, "passed": 0, "failed": 0, "warned": 0}

    total_passed = 0
    total_failed = 0
    total_warned = 0

    for device in devices:
        # Load ports into the device object
        device.open_ports = db.get_device_ports(device.id)

        results: list[ComplianceResult] = []

        for check in checks:
            if not check.is_applicable(device):
                continue
            try:
                result = await check.run(device)
                results.append(result)
            except Exception as e:
                logger.error(
                    f"Check {check.check_type} failed on {device.ip_address}: {e}"
                )

        if not results:
            continue

        # Convert to storage format and persist
        storage_checks = [r.to_check(device.id) for r in results]
        db.store_compliance_results(device.id, storage_checks)

        # Tally
        has_fail = any(r.status == "fail" for r in results)
        has_warn = any(r.status == "warn" for r in results)

        if has_fail:
            total_failed += 1
        elif has_warn:
            total_warned += 1
        else:
            total_passed += 1

        logger.debug(
            f"Compliance: {device.ip_address} — "
            f"{sum(1 for r in results if r.status == 'pass')} pass, "
            f"{sum(1 for r in results if r.status == 'warn')} warn, "
            f"{sum(1 for r in results if r.status == 'fail')} fail"
        )

    summary = {
        "devices_checked": len(devices),
        "passed": total_passed,
        "failed": total_failed,
        "warned": total_warned,
    }

    logger.info(
        f"Compliance checks complete: {summary['devices_checked']} devices — "
        f"{total_passed} compliant, {total_warned} warned, {total_failed} failed"
    )

    return summary
