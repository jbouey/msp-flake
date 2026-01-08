"""
Main compliance agent orchestration.

This module implements the main event loop that:
1. Runs drift detection checks periodically
2. Executes remediation for detected drift
3. Generates evidence bundles
4. Submits evidence to MCP server
5. Queues evidence for retry if offline

The agent is designed to run as a systemd service with graceful shutdown.
"""

import asyncio
import signal
import sys
from datetime import datetime, timezone
from typing import Optional, List
from pathlib import Path
import logging

from .config import AgentConfig
from .crypto import Ed25519Signer, ensure_signing_key
from .drift import DriftDetector
from .healing import HealingEngine
from .evidence import EvidenceGenerator
from .offline_queue import OfflineQueue
from .mcp_client import MCPClient
from .phone_home import PhoneHome
from .models import DriftResult, RemediationResult, EvidenceBundle
from .utils import apply_jitter


logger = logging.getLogger(__name__)


class ComplianceAgent:
    """
    Main compliance agent orchestrator.
    
    Runs the main event loop that:
    - Detects drift from baseline
    - Remediates drift automatically
    - Generates evidence bundles
    - Submits evidence to MCP server
    - Handles offline mode with retry queue
    """
    
    def __init__(self, config: AgentConfig):
        """
        Initialize compliance agent.
        
        Args:
            config: Agent configuration
        """
        self.config = config
        self.running = False
        self.shutdown_event = asyncio.Event()
        
        # Initialize components
        # Ensure signing key exists (auto-generate on first run)
        was_generated, public_key_hex = ensure_signing_key(config.signing_key_file)
        if was_generated:
            logging.getLogger(__name__).info(
                f"First run: generated signing key. Public key: {public_key_hex}"
            )
        self.signer = Ed25519Signer(config.signing_key_file)
        self.drift_detector = DriftDetector(config)
        self.healing_engine = HealingEngine(config)
        self.evidence_generator = EvidenceGenerator(config, self.signer)
        self.offline_queue = OfflineQueue(
            db_path=str(config.state_dir / "offline_queue.db")
        )
        
        # MCP client (optional - may be None in offline mode)
        self.mcp_client: Optional[MCPClient] = None
        if config.mcp_url:
            self.mcp_client = MCPClient(
                base_url=config.mcp_url,
                api_key_file=config.mcp_api_key_file,
                client_cert_file=config.client_cert_file,
                client_key_file=config.client_key_file
            )

        # Phone home client for check-ins with central server
        self.phone_home: Optional[PhoneHome] = None
        if config.mcp_url:
            self.phone_home = PhoneHome(
                config=config,
                drift_detector=self.drift_detector,
                portal_url=None  # Will derive from mcp_url
            )

        # Background tasks
        self._phone_home_task: Optional[asyncio.Task] = None
        self._command_poll_task: Optional[asyncio.Task] = None

        # Statistics
        self.stats = {
            "loops_completed": 0,
            "drift_detected": 0,
            "remediations_attempted": 0,
            "remediations_successful": 0,
            "evidence_generated": 0,
            "evidence_uploaded": 0,
            "evidence_queued": 0,
            "phone_home_sent": 0,
            "commands_received": 0,
            "commands_executed": 0
        }
        
        logger.info(f"ComplianceAgent initialized for site_id={config.site_id}, host_id={config.host_id}")
    
    async def start(self):
        """
        Start the compliance agent.
        
        Runs the main event loop until shutdown.
        """
        if self.running:
            logger.warning("Agent already running")
            return
        
        self.running = True
        logger.info("Starting compliance agent...")
        
        # Setup signal handlers for graceful shutdown
        self._setup_signal_handlers()
        
        try:
            # Initialize MCP client connection
            if self.mcp_client:
                async with self.mcp_client:
                    # Check MCP server health
                    if await self.mcp_client.health_check():
                        logger.info("MCP server is healthy")
                    else:
                        logger.warning("MCP server health check failed, will queue evidence")
                    
                    # Run main loop
                    await self._run_loop()
            else:
                # Offline mode - no MCP client
                logger.info("Running in offline mode (no MCP server configured)")
                await self._run_loop()
        
        except Exception as e:
            logger.error(f"Agent error: {e}", exc_info=True)
            raise
        
        finally:
            await self._shutdown()
    
    async def _run_loop(self):
        """Main event loop."""
        logger.info(f"Starting main loop (poll interval: {self.config.mcp_poll_interval_sec}s)")

        # Start background tasks
        await self._start_background_tasks()

        try:
            while self.running and not self.shutdown_event.is_set():
                try:
                    # Run one iteration
                    await self._run_iteration()

                    self.stats["loops_completed"] += 1

                    # Wait for next iteration (with jitter to prevent thundering herd)
                    wait_time = apply_jitter(
                        float(self.config.mcp_poll_interval_sec),
                        jitter_pct=10.0
                    )

                    logger.debug(f"Waiting {wait_time:.1f}s until next iteration")

                    # Wait with shutdown event check
                    try:
                        await asyncio.wait_for(
                            self.shutdown_event.wait(),
                            timeout=wait_time
                        )
                        # If we get here, shutdown was signaled
                        break
                    except asyncio.TimeoutError:
                        # Normal - timeout means continue to next iteration
                        continue

                except Exception as e:
                    logger.error(f"Error in main loop iteration: {e}", exc_info=True)
                    # Continue running despite errors
                    await asyncio.sleep(60)  # Back off for 1 minute on error
        finally:
            # Stop background tasks on exit
            await self._stop_background_tasks()
    
    async def _run_iteration(self):
        """
        Run one iteration of the agent loop.
        
        1. Detect drift
        2. Remediate drift
        3. Generate evidence
        4. Submit evidence to MCP
        5. Queue evidence if offline
        """
        iteration_start = datetime.now(timezone.utc)
        logger.info(f"Starting iteration at {iteration_start.isoformat()}")
        
        # Step 1: Detect drift
        drift_results = await self.drift_detector.check_all()
        
        drifted = [d for d in drift_results if d.drifted]
        
        if drifted:
            logger.info(f"Detected {len(drifted)} drift(s): {[d.check for d in drifted]}")
            self.stats["drift_detected"] += len(drifted)
        else:
            logger.info("No drift detected")
        
        # Step 2: Remediate drift
        for drift in drifted:
            await self._remediate_and_record(drift)
        
        # Step 3: Process offline queue (upload queued evidence)
        if self.mcp_client:
            await self._process_offline_queue()
        
        iteration_end = datetime.now(timezone.utc)
        duration = (iteration_end - iteration_start).total_seconds()
        logger.info(f"Iteration completed in {duration:.1f}s")
    
    async def _remediate_and_record(self, drift: DriftResult):
        """
        Remediate drift and record evidence.
        
        Args:
            drift: DriftResult from detection
        """
        logger.info(f"Remediating drift: {drift.check} (severity: {drift.severity})")
        
        self.stats["remediations_attempted"] += 1
        timestamp_start = datetime.now(timezone.utc)
        
        # Execute remediation
        try:
            remediation = await self.healing_engine.remediate(drift)
        except Exception as e:
            logger.error(f"Remediation failed with exception: {e}", exc_info=True)
            remediation = RemediationResult(
                check=drift.check,
                outcome="failed",
                pre_state=drift.pre_state,
                error=str(e)
            )
        
        timestamp_end = datetime.now(timezone.utc)
        
        # Log outcome
        logger.info(
            f"Remediation outcome: {remediation.outcome} "
            f"(actions: {len(remediation.actions)})"
        )
        
        if remediation.outcome == "success":
            self.stats["remediations_successful"] += 1
        
        # Generate evidence bundle
        evidence = await self._generate_evidence(
            drift=drift,
            remediation=remediation,
            timestamp_start=timestamp_start,
            timestamp_end=timestamp_end
        )
        
        # Submit evidence to MCP or queue for later
        await self._submit_evidence(evidence)
    
    async def _generate_evidence(
        self,
        drift: DriftResult,
        remediation: RemediationResult,
        timestamp_start: datetime,
        timestamp_end: datetime
    ) -> EvidenceBundle:
        """
        Generate evidence bundle from drift and remediation.
        
        Args:
            drift: DriftResult from detection
            remediation: RemediationResult from healing
            timestamp_start: Start time of remediation
            timestamp_end: End time of remediation
            
        Returns:
            EvidenceBundle
        """
        evidence = await self.evidence_generator.create_evidence(
            check=drift.check,
            outcome=remediation.outcome,
            pre_state=drift.pre_state,
            post_state=remediation.post_state,
            actions=remediation.actions,
            error=remediation.error,
            timestamp_start=timestamp_start,
            timestamp_end=timestamp_end,
            hipaa_controls=drift.hipaa_controls,
            rollback_available=remediation.rollback_available,
            rollback_generation=remediation.rollback_generation
        )
        
        # Store evidence locally
        bundle_path, signature_path = await self.evidence_generator.store_evidence(
            evidence,
            sign=True
        )
        
        logger.info(f"Evidence stored: {bundle_path}")
        self.stats["evidence_generated"] += 1
        
        return evidence
    
    async def _submit_evidence(self, evidence: EvidenceBundle):
        """
        Submit evidence to MCP server or queue for later.
        
        Args:
            evidence: EvidenceBundle to submit
        """
        # Find evidence bundle files
        bundle_path = (
            self.config.evidence_dir /
            str(evidence.timestamp_start.year) /
            f"{evidence.timestamp_start.month:02d}" /
            f"{evidence.timestamp_start.day:02d}" /
            evidence.bundle_id /
            "bundle.json"
        )
        
        signature_path = bundle_path.with_suffix(".sig")
        
        if not bundle_path.exists():
            logger.error(f"Evidence bundle not found: {bundle_path}")
            return
        
        # Try to upload to MCP server
        if self.mcp_client:
            try:
                success = await self.mcp_client.upload_evidence(
                    bundle_path=bundle_path,
                    signature_path=signature_path if signature_path.exists() else None
                )
                
                if success:
                    logger.info(f"Evidence uploaded: {evidence.bundle_id}")
                    self.stats["evidence_uploaded"] += 1
                    return
                else:
                    logger.warning(f"Evidence upload failed: {evidence.bundle_id}")
            
            except Exception as e:
                logger.error(f"Evidence upload error: {e}")
        
        # Queue for later upload
        await self.offline_queue.enqueue(
            bundle_id=evidence.bundle_id,
            bundle_path=str(bundle_path),
            signature_path=str(signature_path) if signature_path.exists() else None
        )
        
        logger.info(f"Evidence queued for later upload: {evidence.bundle_id}")
        self.stats["evidence_queued"] += 1
    
    async def _process_offline_queue(self):
        """Process offline queue - upload pending evidence."""
        pending = await self.offline_queue.get_pending()
        
        if not pending:
            return
        
        logger.info(f"Processing {len(pending)} queued evidence bundle(s)")
        
        for queued in pending:
            try:
                bundle_path = Path(queued.bundle_path)
                signature_path = Path(queued.signature_path) if queued.signature_path else None
                
                if not bundle_path.exists():
                    logger.warning(f"Queued bundle not found: {bundle_path}")
                    await self.offline_queue.mark_uploaded(queued.id)
                    continue
                
                # Attempt upload
                success = await self.mcp_client.upload_evidence(
                    bundle_path=bundle_path,
                    signature_path=signature_path
                )
                
                if success:
                    logger.info(f"Queued evidence uploaded: {queued.bundle_id}")
                    await self.offline_queue.mark_uploaded(queued.id)
                    self.stats["evidence_uploaded"] += 1
                else:
                    logger.warning(f"Queued evidence upload failed: {queued.bundle_id}")
                    await self.offline_queue.increment_retry(
                        queued.id,
                        error="Upload returned False"
                    )
            
            except Exception as e:
                logger.error(f"Error uploading queued evidence: {e}")
                await self.offline_queue.increment_retry(
                    queued.id,
                    error=str(e)
                )
    
    async def _start_background_tasks(self):
        """Start background tasks for phone-home and command polling."""
        # Start phone-home task (check-in with central server)
        if self.phone_home:
            phone_home_interval = getattr(self.config, 'phone_home_interval_sec', 300)
            logger.info(f"Starting phone-home task (interval: {phone_home_interval}s)")
            self._phone_home_task = asyncio.create_task(
                self._phone_home_loop(phone_home_interval)
            )

        # Start command polling task
        if self.mcp_client:
            command_poll_interval = getattr(self.config, 'command_poll_interval_sec', 60)
            logger.info(f"Starting command poll task (interval: {command_poll_interval}s)")
            self._command_poll_task = asyncio.create_task(
                self._command_poll_loop(command_poll_interval)
            )

    async def _stop_background_tasks(self):
        """Stop background tasks gracefully."""
        tasks = []

        if self._phone_home_task and not self._phone_home_task.done():
            self._phone_home_task.cancel()
            tasks.append(self._phone_home_task)

        if self._command_poll_task and not self._command_poll_task.done():
            self._command_poll_task.cancel()
            tasks.append(self._command_poll_task)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("Background tasks stopped")

    async def _phone_home_loop(self, interval_sec: int):
        """Background task for phone-home check-ins."""
        logger.info("Phone-home loop started")

        while self.running and not self.shutdown_event.is_set():
            try:
                if self.phone_home:
                    success = await self.phone_home.send_snapshot()
                    if success:
                        self.stats["phone_home_sent"] += 1
                        logger.debug("Phone-home snapshot sent")
                    else:
                        logger.warning("Phone-home snapshot failed")
            except Exception as e:
                logger.error(f"Phone-home error: {e}")

            # Wait for next iteration
            try:
                await asyncio.wait_for(
                    self.shutdown_event.wait(),
                    timeout=interval_sec
                )
                break  # Shutdown signaled
            except asyncio.TimeoutError:
                continue

        logger.info("Phone-home loop stopped")

    async def _command_poll_loop(self, interval_sec: int):
        """Background task for polling and executing commands from server."""
        logger.info("Command poll loop started")

        while self.running and not self.shutdown_event.is_set():
            try:
                await self._poll_and_execute_commands()
            except Exception as e:
                logger.error(f"Command poll error: {e}")

            # Wait for next iteration
            try:
                await asyncio.wait_for(
                    self.shutdown_event.wait(),
                    timeout=interval_sec
                )
                break  # Shutdown signaled
            except asyncio.TimeoutError:
                continue

        logger.info("Command poll loop stopped")

    async def _poll_and_execute_commands(self):
        """Poll for pending commands and execute them."""
        if not self.mcp_client:
            return

        try:
            # Poll for pending commands
            commands = await self.mcp_client.poll_commands()

            if not commands:
                return

            logger.info(f"Received {len(commands)} command(s) from server")
            self.stats["commands_received"] += len(commands)

            for cmd in commands:
                await self._execute_command(cmd)

        except Exception as e:
            logger.error(f"Failed to poll commands: {e}")

    async def _execute_command(self, command: dict):
        """
        Execute a command received from the server.

        Supported command types:
        - run_check: Run a specific compliance check
        - remediate: Force remediation of a check
        - update_config: Update agent configuration
        - restart_agent: Restart the agent
        - phone_home: Force immediate phone-home
        """
        cmd_id = command.get("id", "unknown")
        cmd_type = command.get("type")
        cmd_params = command.get("params", {})

        logger.info(f"Executing command {cmd_id}: {cmd_type}")

        try:
            result = None

            if cmd_type == "run_check":
                # Run a specific check
                check_name = cmd_params.get("check")
                if check_name:
                    results = await self.drift_detector.check_specific(check_name)
                    result = {"check": check_name, "results": [r.model_dump() for r in results]}

            elif cmd_type == "remediate":
                # Force remediation
                check_name = cmd_params.get("check")
                if check_name:
                    drift = await self.drift_detector.check_specific(check_name)
                    if drift and drift[0].drifted:
                        await self._remediate_and_record(drift[0])
                        result = {"check": check_name, "remediated": True}
                    else:
                        result = {"check": check_name, "remediated": False, "reason": "no drift"}

            elif cmd_type == "phone_home":
                # Force immediate phone-home
                if self.phone_home:
                    success = await self.phone_home.send_snapshot()
                    result = {"success": success}

            elif cmd_type == "health_check":
                # Return health status
                result = await self.health_check()

            else:
                logger.warning(f"Unknown command type: {cmd_type}")
                result = {"error": f"Unknown command type: {cmd_type}"}

            # Acknowledge command completion
            if self.mcp_client:
                await self.mcp_client.acknowledge_command(
                    cmd_id,
                    status="completed",
                    result=result
                )

            self.stats["commands_executed"] += 1
            logger.info(f"Command {cmd_id} executed successfully")

        except Exception as e:
            logger.error(f"Command {cmd_id} failed: {e}")

            # Report failure
            if self.mcp_client:
                await self.mcp_client.acknowledge_command(
                    cmd_id,
                    status="failed",
                    error=str(e)
                )

    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, initiating shutdown...")
            self.running = False
            self.shutdown_event.set()

        # Register handlers for SIGTERM and SIGINT
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

        logger.info("Signal handlers registered (SIGTERM, SIGINT)")
    
    async def _shutdown(self):
        """Graceful shutdown."""
        logger.info("Shutting down compliance agent...")
        
        self.running = False
        
        # Close MCP client
        if self.mcp_client:
            # Context manager handles cleanup
            pass
        
        # Log final statistics
        logger.info(f"Agent statistics: {self.stats}")
        
        logger.info("Compliance agent shutdown complete")
    
    async def health_check(self) -> dict:
        """
        Check agent health.
        
        Returns:
            dict with health status
        """
        health = {
            "status": "healthy" if self.running else "stopped",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "site_id": self.config.site_id,
            "host_id": self.config.host_id,
            "stats": self.stats.copy()
        }
        
        # Check MCP server connectivity
        if self.mcp_client:
            try:
                mcp_healthy = await self.mcp_client.health_check()
                health["mcp_server"] = "healthy" if mcp_healthy else "unhealthy"
            except Exception as e:
                health["mcp_server"] = f"error: {e}"
        else:
            health["mcp_server"] = "not_configured"
        
        # Check offline queue
        try:
            queue_stats = await self.offline_queue.get_stats()
            health["offline_queue"] = queue_stats
        except Exception as e:
            health["offline_queue"] = f"error: {e}"
        
        return health


async def main():
    """Main entry point for running agent as standalone process."""
    import argparse
    
    parser = argparse.ArgumentParser(description="MSP HIPAA Compliance Agent")
    parser.add_argument(
        "--config",
        type=str,
        help="Path to configuration file (uses env vars if not specified)"
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level"
    )
    
    args = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Load configuration
    if args.config:
        config = AgentConfig.from_file(args.config)
    else:
        config = AgentConfig.from_env()
    
    # Create and start agent
    agent = ComplianceAgent(config)
    
    try:
        await agent.start()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
