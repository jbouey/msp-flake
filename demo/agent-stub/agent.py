#!/usr/bin/env python3
"""
Compliance Agent Stub for Demo/Testing

This is a minimal agent that demonstrates the poll-detect-heal-evidence loop.
It connects to the MCP server and:

1. Polls for orders
2. Simulates drift detection
3. Generates mock evidence bundles
4. Uploads evidence to MCP

DEV ONLY - Not for production use.
"""

import json
import logging
import os
import random
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

MCP_URL = os.getenv("MCP_URL", "http://localhost:8001")
SITE_ID = os.getenv("SITE_ID", "demo-site-001")
HOST_ID = os.getenv("HOST_ID", "demo-host-001")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "30"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("agent-stub")

# -----------------------------------------------------------------------------
# Simulated Checks
# -----------------------------------------------------------------------------

DEMO_CHECKS = [
    {
        "check_type": "patching",
        "description": "NixOS generation check",
        "drift_probability": 0.1
    },
    {
        "check_type": "av_edr_health",
        "description": "Antivirus service status",
        "drift_probability": 0.05
    },
    {
        "check_type": "backup_verification",
        "description": "Backup timestamp validation",
        "drift_probability": 0.15
    },
    {
        "check_type": "logging_continuity",
        "description": "Logging service health",
        "drift_probability": 0.08
    },
    {
        "check_type": "firewall_baseline",
        "description": "Firewall ruleset hash",
        "drift_probability": 0.03
    },
    {
        "check_type": "encryption_status",
        "description": "LUKS encryption status",
        "drift_probability": 0.02
    }
]

# -----------------------------------------------------------------------------
# Agent Class
# -----------------------------------------------------------------------------

class DemoAgent:
    def __init__(self):
        self.running = True
        self.client = httpx.Client(timeout=30.0)
        self.iteration = 0

        # Setup signal handlers
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

    def run(self):
        """Main agent loop"""
        logger.info(f"Starting demo agent: site={SITE_ID}, host={HOST_ID}")
        logger.info(f"MCP URL: {MCP_URL}")
        logger.info(f"Poll interval: {POLL_INTERVAL}s")

        # Wait for MCP to be ready
        self._wait_for_mcp()

        while self.running:
            try:
                self.iteration += 1
                logger.info(f"=== Iteration {self.iteration} ===")

                # 1. Poll for orders
                orders = self._poll_orders()
                if orders:
                    for order in orders:
                        self._process_order(order)

                # 2. Run drift detection
                drift_results = self._detect_drift()

                # 3. Generate evidence for any drift
                for result in drift_results:
                    self._submit_evidence(result)

                # 4. Sleep with jitter
                jitter = random.uniform(-0.1, 0.1) * POLL_INTERVAL
                sleep_time = POLL_INTERVAL + jitter
                logger.debug(f"Sleeping for {sleep_time:.1f}s")
                time.sleep(sleep_time)

            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(5)

        logger.info("Agent stopped")

    def _wait_for_mcp(self, max_retries: int = 30):
        """Wait for MCP server to be available"""
        for i in range(max_retries):
            try:
                response = self.client.get(f"{MCP_URL}/health")
                if response.status_code == 200:
                    logger.info("MCP server is ready")
                    return
            except Exception:
                pass
            logger.info(f"Waiting for MCP server... ({i + 1}/{max_retries})")
            time.sleep(2)

        logger.error("MCP server not available after max retries")
        sys.exit(1)

    def _poll_orders(self) -> list:
        """Poll MCP for pending orders"""
        try:
            response = self.client.get(
                f"{MCP_URL}/orders",
                params={"site_id": SITE_ID, "host_id": HOST_ID}
            )
            if response.status_code == 200:
                data = response.json()
                orders = data.get("orders", [])
                if orders:
                    logger.info(f"Received {len(orders)} orders")
                return orders
        except Exception as e:
            logger.error(f"Error polling orders: {e}")
        return []

    def _process_order(self, order: dict):
        """Process a single order"""
        order_id = order.get("order_id")
        runbook_id = order.get("runbook_id")

        logger.info(f"Processing order {order_id}: runbook={runbook_id}")

        # Simulate execution
        time.sleep(random.uniform(0.5, 2.0))
        success = random.random() > 0.1  # 90% success rate

        # Update order status
        try:
            self.client.patch(
                f"{MCP_URL}/orders/{order_id}",
                params={
                    "status": "completed" if success else "failed",
                },
                json={"result": {"success": success, "demo": True}}
            )
            logger.info(f"Order {order_id} {'completed' if success else 'failed'}")
        except Exception as e:
            logger.error(f"Error updating order: {e}")

    def _detect_drift(self) -> list:
        """Simulate drift detection"""
        results = []

        for check in DEMO_CHECKS:
            # Simulate drift based on probability
            has_drift = random.random() < check["drift_probability"]

            if has_drift:
                logger.warning(f"Drift detected: {check['check_type']}")
                results.append({
                    "check_type": check["check_type"],
                    "has_drift": True,
                    "pre_state": {"compliant": False, "demo": True},
                    "post_state": {"compliant": True, "demo": True},
                    "action_taken": f"remediated_{check['check_type']}",
                    "outcome": "success"
                })
            else:
                # Occasionally report healthy checks too
                if random.random() < 0.1:
                    results.append({
                        "check_type": check["check_type"],
                        "has_drift": False,
                        "pre_state": {"compliant": True},
                        "post_state": {"compliant": True},
                        "action_taken": "none",
                        "outcome": "no_drift"
                    })

        return results

    def _submit_evidence(self, result: dict):
        """Submit evidence bundle to MCP"""
        bundle_id = f"EB-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"

        evidence = {
            "bundle_id": bundle_id,
            "site_id": SITE_ID,
            "host_id": HOST_ID,
            "check_type": result["check_type"],
            "outcome": result["outcome"],
            "pre_state": result["pre_state"],
            "post_state": result["post_state"],
            "action_taken": result["action_taken"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "policy_version": "1.0-demo",
            "nixos_revision": "demo-stub",
            "deployment_mode": "demo"
        }

        try:
            response = self.client.post(
                f"{MCP_URL}/evidence",
                data={
                    "bundle": json.dumps(evidence),
                    "signature": "demo-signature-not-real"
                }
            )
            if response.status_code == 200:
                logger.info(f"Evidence submitted: {bundle_id}")
            else:
                logger.error(f"Evidence upload failed: {response.status_code}")
        except Exception as e:
            logger.error(f"Error submitting evidence: {e}")


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    agent = DemoAgent()
    agent.run()
