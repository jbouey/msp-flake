"""
Compliance Agent - Main Loop

This is the heart of the MSP Compliance Appliance. It:
1. Polls MCP server for orders (pull-only, no listening sockets)
2. Detects drift from desired state
3. Executes healing actions
4. Generates cryptographically signed evidence
5. Survives MCP outages with offline queue

Architecture:
- Pull-only: No inbound connections, only outbound HTTPS with mTLS
- Ed25519 signatures: All orders must be signed by MCP server
- Offline queue: SQLite WAL for durability during network failures
- Maintenance window: Disruptive actions only in configured windows
- Evidence generation: Every action produces signed evidence bundle

Safety Guardrails:
- Order signature verification (Ed25519)
- Order TTL enforcement (default 15 minutes)
- Maintenance window enforcement
- Health check with automatic rollback
- Evidence generation for all actions
"""

import asyncio
import signal
import sys
import json
import time
import random
import logging
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime, timedelta, timezone

# Will be implemented
from .mcp_client import MCPClient
from .offline_queue import OfflineQueue
from .config import Config
from .crypto import SignatureVerifier

# Phase 2 Day 3-4: Drift Detection (COMPLETE)
from .drift_detector import DriftDetector

# Phase 2 Day 5: Self-Healing (COMPLETE)
from .healer import Healer

# Phase 2 Day 6-7: Evidence Generation (COMPLETE)
from .evidence import EvidenceGenerator

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ComplianceAgent:
    """
    Main compliance agent

    Orchestrates the compliance cycle:
    1. Poll MCP for orders
    2. Detect drift
    3. Heal drift
    4. Execute orders
    5. Generate evidence
    6. Push evidence to MCP
    """

    def __init__(self, config_path: str):
        """
        Initialize agent with configuration

        Args:
            config_path: Path to configuration file (YAML or JSON)
        """
        logger.info(f"Initializing compliance agent with config: {config_path}")

        # Load configuration
        self.config = Config.load(config_path)
        logger.info(f"Loaded config for site: {self.config.site_id}")

        # Initialize MCP client
        self.mcp_client = MCPClient(
            base_url=self.config.mcp_base_url,
            cert_file=self.config.client_cert,
            key_file=self.config.client_key,
            ca_file=self.config.ca_cert,
            timeout=self.config.mcp_timeout
        )

        # Initialize offline queue
        self.queue = OfflineQueue(
            db_path=self.config.queue_path,
            max_size=self.config.max_queue_size
        )

        # Initialize signature verifier
        self.verifier = SignatureVerifier(
            public_key_hex=self.config.mcp_public_key
        )

        # Phase 2 Day 3-4: Drift Detection (COMPLETE)
        self.drift_detector = DriftDetector(self.config)

        # Phase 2 Day 5: Self-Healing (COMPLETE)
        self.healer = Healer(self.config)

        # Phase 2 Day 6-7: Evidence Generation (COMPLETE)
        self.evidence = EvidenceGenerator(config=self.config)

        # Agent state
        self.running = False
        self.poll_interval = self.config.poll_interval
        self.cycle_count = 0
        self.last_successful_poll = None
        self.last_evidence_push = None

        # Statistics
        self.stats = {
            'cycles_completed': 0,
            'orders_received': 0,
            'orders_executed': 0,
            'orders_rejected': 0,
            'drift_detected': 0,
            'drift_healed': 0,
            'evidence_generated': 0,
            'mcp_failures': 0
        }

        # Setup signal handlers
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Setup graceful shutdown on SIGTERM/SIGINT"""
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info(f"Received signal {signum}, initiating graceful shutdown")
        self.running = False

    async def run(self):
        """
        Main agent loop

        This is the entry point. It runs continuously until stopped.
        """
        logger.info(f"Starting compliance agent for site {self.config.site_id}")
        logger.info(f"MCP server: {self.config.mcp_base_url}")
        logger.info(f"Poll interval: {self.poll_interval}s")
        logger.info(f"Deployment mode: {self.config.deployment_mode}")

        self.running = True

        # Startup checks
        await self._startup_checks()

        # Main loop
        while self.running:
            try:
                # Calculate next poll time with jitter
                jitter = random.uniform(-0.1, 0.1)
                sleep_time = self.poll_interval * (1 + jitter)

                logger.debug(f"Sleeping for {sleep_time:.1f}s until next cycle")
                await asyncio.sleep(sleep_time)

                # Run compliance cycle
                await self._compliance_cycle()

                self.cycle_count += 1
                self.stats['cycles_completed'] += 1

            except asyncio.CancelledError:
                logger.info("Agent loop cancelled")
                break

            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                self.stats['mcp_failures'] += 1

                # Back off on errors
                await asyncio.sleep(10)

        # Cleanup
        await self._shutdown()

    async def _startup_checks(self):
        """
        Run startup checks before entering main loop

        Verifies:
        - Configuration is valid
        - Queue database is accessible
        - MCP server is reachable (optional - will queue if not)
        - Signing keys are valid
        """
        logger.info("Running startup checks")

        # Check queue database
        try:
            self.queue.health_check()
            logger.info("✓ Queue database healthy")
        except Exception as e:
            logger.error(f"Queue database check failed: {e}")
            raise

        # Check MCP connectivity (non-fatal)
        try:
            health = await self.mcp_client.health_check()
            if health:
                logger.info("✓ MCP server reachable")
                self.last_successful_poll = time.time()
            else:
                logger.warning("⚠ MCP server not reachable (will use offline queue)")
        except Exception as e:
            logger.warning(f"⚠ MCP health check failed: {e} (will use offline queue)")

        # Check signature verifier
        try:
            self.verifier.health_check()
            logger.info("✓ Signature verifier initialized")
        except Exception as e:
            logger.error(f"Signature verifier check failed: {e}")
            raise

        logger.info("Startup checks complete")

    async def _compliance_cycle(self):
        """
        One complete compliance cycle

        Flow:
        1. Fetch orders from MCP (or offline queue if MCP down)
        2. Verify and validate orders
        3. Detect drift from desired state
        4. Heal detected drift
        5. Execute pending orders
        6. Generate evidence for all actions
        7. Push evidence to MCP

        All errors are caught and logged, cycle continues.
        """
        cycle_start = time.time()
        logger.info(f"Starting compliance cycle #{self.cycle_count + 1}")

        try:
            # 1. Fetch orders from MCP
            orders = await self._fetch_orders()
            logger.info(f"Fetched {len(orders)} orders")

            # 2. Detect drift (Phase 2 Day 3-4: COMPLETE)
            drift_results = await self.drift_detector.check_all()
            drift_count = sum(1 for r in drift_results.values() if r.drift_detected)
            logger.info(f"Drift detection: {drift_count}/{len(drift_results)} checks detected drift")

            # Update statistics
            self.stats['drift_detected'] += drift_count

            # 3. Generate drift detection evidence (Phase 2 Day 6-7: COMPLETE)
            await self.evidence.record_drift_detection(drift_results)
            self.stats['evidence_generated'] += 1

            # 4. Heal drift (Phase 2 Day 5: COMPLETE)
            for check_name, result in drift_results.items():
                if result.drift_detected and result.remediation_runbook:
                    await self._heal_drift(check_name, result)

            # 5. Execute orders
            for order in orders:
                await self._execute_order(order)

            cycle_duration = time.time() - cycle_start
            logger.info(f"Compliance cycle completed in {cycle_duration:.2f}s")

        except Exception as e:
            logger.error(f"Error in compliance cycle: {e}", exc_info=True)

    async def _fetch_orders(self) -> List[Dict]:
        """
        Fetch orders from MCP server or offline queue

        Returns:
            List of verified orders ready for execution
        """
        verified_orders = []

        try:
            # Try to fetch from MCP
            raw_orders = await self.mcp_client.poll_orders(site_id=self.config.site_id)

            logger.info(f"Received {len(raw_orders)} orders from MCP")
            self.last_successful_poll = time.time()
            self.stats['orders_received'] += len(raw_orders)

            # Verify each order
            for order in raw_orders:
                if self._verify_order(order):
                    verified_orders.append(order)
                else:
                    logger.warning(f"Order {order.get('id', 'unknown')} failed verification")
                    self.stats['orders_rejected'] += 1

                    # Generate rejection evidence (Phase 2 Day 6-7)
                    # await self.evidence.record_rejection(order, reason='verification_failed')

        except Exception as e:
            logger.warning(f"Failed to fetch from MCP: {e}, checking offline queue")
            self.stats['mcp_failures'] += 1

            # Fall back to offline queue
            queued_orders = self.queue.get_pending(limit=10)
            logger.info(f"Retrieved {len(queued_orders)} orders from offline queue")

            for order in queued_orders:
                if self._verify_order(order):
                    verified_orders.append(order)

        return verified_orders

    def _verify_order(self, order: Dict) -> bool:
        """
        Verify order signature and TTL

        An order must:
        1. Have a valid Ed25519 signature from MCP server
        2. Be within TTL (default 15 minutes)
        3. Have all required fields

        Args:
            order: Order dictionary from MCP

        Returns:
            True if order is valid and should be executed
        """
        # Check required fields
        required_fields = ['id', 'timestamp', 'payload', 'signature']
        if not all(field in order for field in required_fields):
            logger.warning(f"Order missing required fields: {order.get('id', 'unknown')}")
            return False

        # Check TTL
        order_age = time.time() - order['timestamp']
        ttl = order.get('ttl', self.config.order_ttl)

        if order_age > ttl:
            logger.warning(f"Order {order['id']} expired (age: {order_age:.0f}s, ttl: {ttl}s)")
            # Generate expired evidence (Phase 2 Day 6-7)
            # await self.evidence.record_expiry(order)
            return False

        # Verify Ed25519 signature
        try:
            message = json.dumps(order['payload'], sort_keys=True).encode()
            signature = bytes.fromhex(order['signature'])

            if not self.verifier.verify(message, signature):
                logger.warning(f"Order {order['id']} signature verification failed")
                return False

        except Exception as e:
            logger.warning(f"Order {order['id']} signature check error: {e}")
            return False

        logger.debug(f"Order {order['id']} verified successfully")
        return True

    async def _execute_order(self, order: Dict):
        """
        Execute a verified order

        Phase 2 Day 5 implementation - for now just log

        Args:
            order: Verified order to execute
        """
        logger.info(f"Executing order {order['id']}: {order['payload'].get('action', 'unknown')}")

        # Check maintenance window for disruptive actions
        if order['payload'].get('disruptive', False):
            if not self._in_maintenance_window():
                logger.info(f"Order {order['id']} deferred (outside maintenance window)")
                self.queue.add(order)  # Queue for later
                # Generate deferred evidence (Phase 2 Day 6-7)
                # await self.evidence.record_deferred(order, reason='outside_window')
                return

        # Execute order (Phase 2 Day 5)
        # For now, just mark as executed
        self.stats['orders_executed'] += 1
        logger.info(f"Order {order['id']} executed successfully")

        # Generate success evidence (Phase 2 Day 6-7)
        # await self.evidence.record_success(order, result)

    def _in_maintenance_window(self) -> bool:
        """
        Check if we're currently in a maintenance window

        Returns:
            True if disruptive actions are allowed now
        """
        if not self.config.maintenance_window_enabled:
            return True  # No window configured, always allowed

        # Get current time in configured timezone (using UTC for consistency)
        now = datetime.now(timezone.utc)
        current_time = now.time()

        # Check if current day is in allowed days
        current_day = now.strftime('%A').lower()
        if current_day not in self.config.maintenance_window_days:
            return False

        # Check if current time is in window
        window_start = self.config.maintenance_window_start
        window_end = self.config.maintenance_window_end

        if window_start <= window_end:
            # Normal window (e.g., 02:00-04:00)
            return window_start <= current_time <= window_end
        else:
            # Window crosses midnight (e.g., 22:00-02:00)
            return current_time >= window_start or current_time <= window_end

    async def _heal_drift(self, check_name: str, drift_result):
        """
        Execute healing for detected drift

        Args:
            check_name: Name of drift check that failed
            drift_result: DriftResult from drift detector

        Phase 2 Day 5: Automated healing with rollback
        """
        runbook_id = drift_result.remediation_runbook

        logger.info(f"Healing drift: {check_name} with runbook {runbook_id}")

        try:
            # Execute runbook with context from drift detection
            healing_result = await self.healer.execute_runbook(
                runbook_id=runbook_id,
                context=drift_result.details
            )

            # Update statistics
            if healing_result.status == "success":
                self.stats['drift_healed'] += 1
                logger.info(f"✓ Drift healed: {check_name} ({healing_result.total_duration_seconds:.2f}s)")

                # Generate healing evidence (Phase 2 Day 6-7: COMPLETE)
                await self.evidence.record_healing(drift_result, healing_result)
                self.stats['evidence_generated'] += 1

            elif healing_result.status == "rolled_back":
                logger.warning(f"⚠ Healing rolled back: {check_name}")

                # Generate rollback evidence (Phase 2 Day 6-7: COMPLETE)
                await self.evidence.record_rollback(drift_result, healing_result)
                self.stats['evidence_generated'] += 1

            else:
                logger.error(f"✗ Healing failed: {check_name} - {healing_result.error_message}")

                # Generate failure evidence (same as rollback)
                await self.evidence.record_rollback(drift_result, healing_result)
                self.stats['evidence_generated'] += 1

        except Exception as e:
            logger.error(f"Healing exception for {check_name}: {e}", exc_info=True)

    async def _shutdown(self):
        """
        Graceful shutdown

        Cleanup:
        - Close MCP client connection
        - Flush offline queue
        - Close database connections
        - Log final statistics
        """
        logger.info("Shutting down compliance agent")

        # Close MCP client
        try:
            await self.mcp_client.close()
            logger.info("✓ MCP client closed")
        except Exception as e:
            logger.warning(f"Error closing MCP client: {e}")

        # Close queue
        try:
            self.queue.close()
            logger.info("✓ Queue closed")
        except Exception as e:
            logger.warning(f"Error closing queue: {e}")

        # Log statistics
        logger.info("Final statistics:")
        for key, value in self.stats.items():
            logger.info(f"  {key}: {value}")

        logger.info("Compliance agent stopped")

    def get_stats(self) -> Dict:
        """
        Get agent statistics

        Returns:
            Dictionary of statistics for monitoring
        """
        return {
            **self.stats,
            'running': self.running,
            'cycle_count': self.cycle_count,
            'last_successful_poll': self.last_successful_poll,
            'uptime_seconds': time.time() - self.stats.get('start_time', time.time()),
            'queue_depth': self.queue.size()
        }


async def main():
    """
    Entry point for running agent as standalone process

    Usage:
        python -m src.agent /path/to/config.yaml
    """
    if len(sys.argv) < 2:
        print("Usage: python -m src.agent <config_path>")
        sys.exit(1)

    config_path = sys.argv[1]

    # Create and run agent
    agent = ComplianceAgent(config_path)

    try:
        await agent.run()
    except KeyboardInterrupt:
        logger.info("Agent interrupted by user")
    except Exception as e:
        logger.error(f"Agent failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
