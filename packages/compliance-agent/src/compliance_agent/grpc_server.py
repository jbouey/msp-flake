"""
gRPC server for receiving drift events from Go agents.

This runs alongside the existing FastAPI sensor API (port 8080).
Go agents connect via gRPC (port 50051) for persistent streaming.

The server provides:
- Agent registration with capability tier assignment
- Bidirectional streaming for drift events
- Heartbeat monitoring for agent health
- RMM status reporting for strategic intelligence
"""

import asyncio
import logging
import uuid
from concurrent import futures
from datetime import datetime, timezone
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)

# Try to import grpc - it's optional
try:
    import grpc
    from grpc import aio
    from . import compliance_pb2
    from . import compliance_pb2_grpc
    GRPC_AVAILABLE = True
except ImportError as e:
    GRPC_AVAILABLE = False
    logger.warning(f"gRPC not available: {e}")


class AgentState:
    """Track state of a connected Go agent."""

    def __init__(self, agent_id: str, hostname: str, tier: int):
        self.agent_id = agent_id
        self.hostname = hostname
        self.tier = tier
        self.connected_at = datetime.now(timezone.utc)
        self.last_heartbeat = datetime.now(timezone.utc)
        self.drift_count = 0
        self.rmm_agents: list = []


class AgentRegistry:
    """Registry of connected Go agents."""

    def __init__(self):
        self.agents: Dict[str, AgentState] = {}
        self._config_version = 1

    def register(self, state: AgentState):
        """Register an agent."""
        self.agents[state.agent_id] = state
        logger.info(f"Registered Go agent {state.agent_id} ({state.hostname})")

    def unregister(self, agent_id: str):
        """Unregister an agent."""
        if agent_id in self.agents:
            hostname = self.agents[agent_id].hostname
            del self.agents[agent_id]
            logger.info(f"Unregistered Go agent {agent_id} ({hostname})")

    def get_connected_count(self) -> int:
        """Get count of connected agents."""
        return len(self.agents)

    def get_agent(self, agent_id: str) -> Optional[AgentState]:
        """Get agent state by ID."""
        return self.agents.get(agent_id)

    def config_version_changed(self, agent_id: str) -> bool:
        """Check if config version changed since agent registered."""
        # TODO: Implement config versioning for push updates
        return False

    def get_all_agents(self) -> list:
        """Get list of all agent states."""
        return list(self.agents.values())


if GRPC_AVAILABLE:
    class ComplianceAgentServicer(compliance_pb2_grpc.ComplianceAgentServicer):
        """gRPC service implementation for Go agent communication."""

        # Capability tier constants (must match proto enum)
        MONITOR_ONLY = 0
        SELF_HEAL = 1
        FULL_REMEDIATION = 2

        def __init__(
            self,
            agent_registry: AgentRegistry,
            mcp_client=None,
            healing_engine=None,
            config=None,
        ):
            self.registry = agent_registry
            self.mcp_client = mcp_client
            self.healing_engine = healing_engine
            self.config = config

        def Register(self, request, context):
            """Handle agent registration."""
            logger.info(f"Go agent registration: {request.hostname}")

            # Generate agent ID
            agent_id = f"go-{request.hostname}-{uuid.uuid4().hex[:8]}"

            # Default to MONITOR_ONLY for MSP-deployed agents
            tier = self.MONITOR_ONLY

            # Register agent
            self.registry.register(AgentState(
                agent_id=agent_id,
                hostname=request.hostname,
                tier=tier,
            ))

            # Log RMM detection from installed_software
            if request.installed_software:
                logger.info(
                    f"Go agent {request.hostname} software: "
                    f"{list(request.installed_software)[:5]}"
                )

            # Build response using protobuf message
            return compliance_pb2.RegisterResponse(
                agent_id=agent_id,
                check_interval_seconds=300,  # 5 minutes for workstations
                enabled_checks=[
                    "bitlocker",
                    "defender",
                    "patches",
                    "firewall",
                    "screenlock",
                    "rmm_detection",
                ],
                capability_tier=tier,
                check_config={},
            )

        def ReportDrift(self, request_iterator, context):
            """Handle streaming drift events from agent."""
            for event in request_iterator:
                logger.info(
                    f"Go agent drift: {event.hostname}/{event.check_type} "
                    f"passed={event.passed}"
                )

                # Update agent stats
                agent = self.registry.get_agent(event.agent_id)
                if agent:
                    agent.drift_count += 1
                    agent.last_heartbeat = datetime.now(timezone.utc)

                # Route failed checks to healing engine
                if not event.passed:
                    self._route_drift_to_healing_sync(event)

                # Acknowledge receipt
                yield compliance_pb2.DriftAck(
                    event_id=f"{event.agent_id}-{event.timestamp}",
                    received=True,
                )

        def ReportHealing(self, request, context):
            """Handle healing results from SELF_HEAL tier agents."""
            logger.info(
                f"Go agent healing: {request.hostname}/{request.check_type} "
                f"success={request.success}"
            )

            # Handle artifacts (e.g., BitLocker recovery key)
            if request.artifacts:
                self._handle_artifacts_sync(request)

            return compliance_pb2.HealingAck(
                event_id=f"{request.agent_id}-{request.timestamp}",
                received=True,
            )

        def Heartbeat(self, request, context):
            """Handle agent heartbeats."""
            agent = self.registry.get_agent(request.agent_id)
            if agent:
                agent.last_heartbeat = datetime.now(timezone.utc)
                logger.debug(f"Heartbeat from {request.agent_id}")

            # Check if config changed (triggers re-registration)
            config_changed = self.registry.config_version_changed(request.agent_id)

            return compliance_pb2.HeartbeatResponse(
                acknowledged=True,
                config_changed=config_changed,
            )

        def ReportRMMStatus(self, request, context):
            """Handle RMM detection reports - strategic intelligence."""
            logger.info(
                f"Go agent RMM status from {request.hostname}: "
                f"{len(request.detected_agents)} agents"
            )

            for rmm_agent in request.detected_agents:
                logger.info(
                    f"  - {rmm_agent.name} v{rmm_agent.version} "
                    f"running={rmm_agent.running}"
                )

            # Update agent state
            agent = self.registry.get_agent(request.agent_id)
            if agent:
                agent.rmm_agents = list(request.detected_agents)
                agent.last_heartbeat = datetime.now(timezone.utc)

            return compliance_pb2.RMMAck(received=True)

        def _route_drift_to_healing_sync(self, event) -> None:
            """Route drift event through existing healing pipeline (sync wrapper)."""
            if not self.healing_engine:
                logger.warning(
                    f"Healing not configured - Go agent drift from "
                    f"{event.hostname} not processed"
                )
                return

            try:
                from .incident_db import Incident

                incident = Incident(
                    id=f"GO-{uuid.uuid4().hex[:12]}",
                    site_id=self.config.site_id if self.config else "unknown",
                    host_id=event.hostname,
                    incident_type=event.check_type,
                    severity="high" if event.hipaa_control else "medium",
                    raw_data={
                        "check_type": event.check_type,
                        "drift_detected": True,
                        "go_agent": True,
                        "expected": event.expected,
                        "actual": event.actual,
                        **dict(event.metadata),
                    },
                    created_at=datetime.now(timezone.utc).isoformat(),
                    pattern_signature=f"go_agent:{event.check_type}:{event.hostname}",
                )

                # Run healing in a new event loop (gRPC runs in thread pool)
                try:
                    asyncio.run(self._async_heal(incident))
                except RuntimeError:
                    # Fallback: create new loop if asyncio.run fails
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(self._async_heal(incident))
                    finally:
                        loop.close()

            except Exception as e:
                logger.error(f"Error routing Go agent drift to healing: {e}")

        async def _async_heal(self, incident) -> None:
            """Run healing asynchronously."""
            try:
                result = await self.healing_engine.heal(
                    site_id=incident.site_id,
                    host_id=incident.host_id,
                    incident_type=incident.incident_type,
                    severity=incident.severity,
                    raw_data=incident.raw_data,
                )
                if result and result.success:
                    logger.info(
                        f"Healed Go agent drift: {incident.host_id}/{incident.incident_type} "
                        f"via {getattr(result, 'runbook_id', 'unknown')}"
                    )
                else:
                    reason = (
                        getattr(result, 'reason', 'Unknown error')
                        if result else 'No result'
                    )
                    logger.warning(
                        f"Healing failed for Go agent drift: "
                        f"{incident.host_id}/{incident.incident_type} - {reason}"
                    )
            except Exception as e:
                logger.error(f"Async healing error: {e}")

        def _handle_artifacts_sync(self, result) -> None:
            """Handle artifacts like BitLocker recovery keys."""
            if "recovery_key" in result.artifacts:
                logger.info(
                    f"Storing BitLocker recovery key for {result.hostname}"
                )
                # TODO: Store via existing BitLocker key backup infrastructure

else:
    # Stub when gRPC is not available
    class ComplianceAgentServicer:
        """Stub servicer when grpcio is not installed."""
        def __init__(self, *args, **kwargs):
            pass


def serve_sync(
    port: int,
    agent_registry: AgentRegistry,
    mcp_client=None,
    healing_engine=None,
    config=None,
) -> None:
    """Start the gRPC server (synchronous version for threading)."""
    if not GRPC_AVAILABLE:
        logger.warning("gRPC server not started - grpcio not installed")
        return

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    servicer = ComplianceAgentServicer(
        agent_registry,
        mcp_client,
        healing_engine,
        config,
    )

    # Register the servicer with the server
    compliance_pb2_grpc.add_ComplianceAgentServicer_to_server(servicer, server)

    # Listen on all interfaces
    listen_addr = f"[::]:{port}"
    server.add_insecure_port(listen_addr)  # TODO: Add TLS

    logger.info(f"Starting gRPC server on {listen_addr}")
    server.start()
    server.wait_for_termination()


async def serve(
    port: int,
    agent_registry: AgentRegistry,
    mcp_client=None,
    healing_engine=None,
    config=None,
) -> None:
    """Start the gRPC server (async version)."""
    if not GRPC_AVAILABLE:
        logger.warning("gRPC server not started - grpcio not installed")
        return

    server = aio.server(futures.ThreadPoolExecutor(max_workers=10))

    servicer = ComplianceAgentServicer(
        agent_registry,
        mcp_client,
        healing_engine,
        config,
    )

    # Register the servicer with the server
    compliance_pb2_grpc.add_ComplianceAgentServicer_to_server(servicer, server)

    # Listen on all interfaces
    listen_addr = f"[::]:{port}"
    server.add_insecure_port(listen_addr)  # TODO: Add TLS

    logger.info(f"Starting gRPC server on {listen_addr}")
    await server.start()
    await server.wait_for_termination()


def get_grpc_stats(registry: AgentRegistry) -> Dict[str, Any]:
    """Get statistics about connected Go agents."""
    now = datetime.now(timezone.utc)

    agents = []
    for agent in registry.get_all_agents():
        age = (now - agent.last_heartbeat).total_seconds()
        agents.append({
            "agent_id": agent.agent_id,
            "hostname": agent.hostname,
            "tier": agent.tier,
            "connected_at": agent.connected_at.isoformat(),
            "last_heartbeat": agent.last_heartbeat.isoformat(),
            "heartbeat_age_seconds": int(age),
            "drift_count": agent.drift_count,
            "rmm_agents": len(agent.rmm_agents),
        })

    return {
        "grpc_available": GRPC_AVAILABLE,
        "connected_agents": len(agents),
        "agents": agents,
    }
