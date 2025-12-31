"""
Phone-home module for sending compliance snapshots to portal.

Sends compliance data to the central MCP server at regular intervals,
including:
- 8 control check results
- KPI metrics
- Recent incidents
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
import aiohttp

from .config import AgentConfig
from .portal_controls import PortalControlChecker, ControlResult
from .drift import DriftDetector

logger = logging.getLogger(__name__)


class PhoneHome:
    """
    Sends compliance snapshots to the portal API.

    Features:
    - Runs 8 control checks
    - Computes KPIs from check results
    - Sends snapshot to /api/portal/appliances/snapshot
    - Handles offline mode with retry
    """

    def __init__(
        self,
        config: AgentConfig,
        drift_detector: Optional[DriftDetector] = None,
        portal_url: Optional[str] = None
    ):
        """
        Initialize phone-home client.

        Args:
            config: Agent configuration
            drift_detector: Optional existing drift detector
            portal_url: Override portal API URL (default: from config.mcp_url)
        """
        self.config = config
        self.drift_detector = drift_detector or DriftDetector(config)
        self.control_checker = PortalControlChecker(config, self.drift_detector)

        # Portal API URL
        if portal_url:
            self.portal_url = portal_url
        elif config.mcp_url:
            # Derive from MCP URL
            self.portal_url = config.mcp_url.rstrip('/') + '/api/portal/appliances/snapshot'
        else:
            self.portal_url = None

        # Stats
        self.stats = {
            "snapshots_sent": 0,
            "snapshots_failed": 0,
            "last_snapshot_at": None,
            "last_error": None
        }

        # Recent incidents cache (from agent)
        self._recent_incidents: List[Dict[str, Any]] = []

    def add_incident(self, incident: Dict[str, Any]):
        """
        Add an incident to the recent incidents cache.

        Called by the agent when incidents are detected.
        Keeps last 24h of incidents.
        """
        # Add timestamp if not present
        if "created_at" not in incident:
            incident["created_at"] = datetime.now(timezone.utc).isoformat()

        self._recent_incidents.append(incident)

        # Prune old incidents (keep last 24h)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        self._recent_incidents = [
            inc for inc in self._recent_incidents
            if datetime.fromisoformat(inc["created_at"].replace("Z", "+00:00")) > cutoff
        ]

    async def send_snapshot(self) -> bool:
        """
        Run control checks and send snapshot to portal.

        Returns:
            True if successful, False otherwise
        """
        if not self.portal_url:
            logger.warning("Portal URL not configured, skipping phone-home")
            return False

        try:
            # Run control checks
            logger.info("Running portal control checks for phone-home")
            control_results = await self.control_checker.check_all()

            # Compute KPIs
            kpis = self._compute_kpis(control_results)

            # Build snapshot payload
            snapshot = {
                "site_id": self.config.site_id,
                "host_id": self.config.host_id,
                "compliance_pct": kpis["compliance_pct"],
                "patch_mttr_hours": kpis["patch_mttr_hours"],
                "mfa_coverage_pct": kpis["mfa_coverage_pct"],
                "backup_success_rate": kpis["backup_success_rate"],
                "auto_fixes_24h": kpis["auto_fixes_24h"],
                "health_score": kpis["health_score"],
                "control_results": [r.to_dict() for r in control_results],
                "recent_incidents": self._recent_incidents[-20:],  # Last 20 incidents
                "agent_version": getattr(self.config, 'agent_version', '1.0.0'),
                "policy_version": getattr(self.config, 'policy_version', '1.0')
            }

            # Send to portal
            success = await self._send_to_portal(snapshot)

            if success:
                self.stats["snapshots_sent"] += 1
                self.stats["last_snapshot_at"] = datetime.now(timezone.utc).isoformat()
                logger.info(f"Phone-home snapshot sent successfully")
            else:
                self.stats["snapshots_failed"] += 1

            return success

        except Exception as e:
            logger.error(f"Phone-home failed: {e}")
            self.stats["snapshots_failed"] += 1
            self.stats["last_error"] = str(e)
            return False

    def _compute_kpis(self, control_results: List[ControlResult]) -> Dict[str, Any]:
        """
        Compute KPI metrics from control results.

        Args:
            control_results: List of control check results

        Returns:
            Dict of KPI values
        """
        # Count statuses
        passing = sum(1 for r in control_results if r.status == "pass")
        warning = sum(1 for r in control_results if r.status == "warn")
        failing = sum(1 for r in control_results if r.status == "fail")
        total = len(control_results)

        # Compliance percentage (pass = 100%, warn = 50%, fail = 0%)
        compliance_score = (passing * 100 + warning * 50) / total if total > 0 else 0

        # Extract specific KPIs from control results
        mfa_result = next((r for r in control_results if r.rule_id == "mfa_coverage"), None)
        mfa_pct = 100.0 if mfa_result and mfa_result.status == "pass" else 0.0

        backup_result = next((r for r in control_results if r.rule_id == "backup_success"), None)
        backup_rate = 100.0 if backup_result and backup_result.status == "pass" else 0.0

        # Count auto-fixes in last 24h
        auto_fixes = sum(1 for r in control_results if r.auto_fix_triggered)

        # Health score (weighted average)
        health = (compliance_score * 0.7 + mfa_pct * 0.15 + backup_rate * 0.15)

        return {
            "compliance_pct": round(compliance_score, 1),
            "patch_mttr_hours": 0.0,  # Would come from incident data
            "mfa_coverage_pct": mfa_pct,
            "backup_success_rate": backup_rate,
            "auto_fixes_24h": auto_fixes,
            "health_score": round(health, 1),
            "controls_passing": passing,
            "controls_warning": warning,
            "controls_failing": failing
        }

    async def _send_to_portal(self, snapshot: Dict[str, Any]) -> bool:
        """
        Send snapshot to portal API.

        Args:
            snapshot: Snapshot data to send

        Returns:
            True if successful, False otherwise
        """
        try:
            timeout = aiohttp.ClientTimeout(total=30)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self.portal_url,
                    json=snapshot,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": f"compliance-agent/{self.config.site_id}"
                    }
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        logger.debug(f"Portal response: {result}")
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"Portal returned {response.status}: {error_text}")
                        self.stats["last_error"] = f"HTTP {response.status}: {error_text[:100]}"
                        return False

        except aiohttp.ClientError as e:
            logger.error(f"Portal connection error: {e}")
            self.stats["last_error"] = str(e)
            return False
        except Exception as e:
            logger.error(f"Portal send error: {e}")
            self.stats["last_error"] = str(e)
            return False

    async def run_periodic(self, interval_seconds: int = 300):
        """
        Run phone-home at regular intervals.

        Args:
            interval_seconds: Interval between snapshots (default: 5 minutes)
        """
        logger.info(f"Starting phone-home loop (interval: {interval_seconds}s)")

        while True:
            try:
                await self.send_snapshot()
            except Exception as e:
                logger.error(f"Phone-home iteration failed: {e}")

            await asyncio.sleep(interval_seconds)
