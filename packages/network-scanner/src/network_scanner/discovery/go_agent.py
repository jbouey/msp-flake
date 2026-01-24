"""
Go Agent check-in listener.

Receives check-ins from Go agents running on Windows workstations.
This provides real-time discovery of managed endpoints.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from aiohttp import web

from .._types import DiscoverySource, now_utc
from .base import DiscoveredDevice, DiscoveryMethod

logger = logging.getLogger(__name__)


@dataclass
class AgentInfo:
    """Information about a registered Go agent."""
    host_id: str
    hostname: str
    ip_address: str
    os_name: str
    os_version: str
    agent_version: str
    last_checkin: datetime = field(default_factory=now_utc)
    capabilities: list[str] = field(default_factory=list)


class GoAgentRegistry:
    """
    Registry of Go agents that have checked in.

    Maintains a list of known agents and their last check-in time.
    """

    def __init__(self, stale_timeout_seconds: int = 300):
        """
        Initialize agent registry.

        Args:
            stale_timeout_seconds: Time after which an agent is considered stale
        """
        self._agents: dict[str, AgentInfo] = {}
        self._stale_timeout = stale_timeout_seconds
        self._lock = asyncio.Lock()

    async def register(self, agent_info: AgentInfo) -> None:
        """Register or update an agent."""
        async with self._lock:
            self._agents[agent_info.host_id] = agent_info
            logger.info(f"Agent registered: {agent_info.hostname} ({agent_info.ip_address})")

    async def unregister(self, host_id: str) -> None:
        """Remove an agent from registry."""
        async with self._lock:
            if host_id in self._agents:
                del self._agents[host_id]

    async def get_all(self) -> list[AgentInfo]:
        """Get all registered agents."""
        async with self._lock:
            return list(self._agents.values())

    async def get_active(self) -> list[AgentInfo]:
        """Get all active (non-stale) agents."""
        now = datetime.now(timezone.utc)
        async with self._lock:
            return [
                agent for agent in self._agents.values()
                if (now - agent.last_checkin).total_seconds() < self._stale_timeout
            ]

    async def get_stale(self) -> list[AgentInfo]:
        """Get all stale agents."""
        now = datetime.now(timezone.utc)
        async with self._lock:
            return [
                agent for agent in self._agents.values()
                if (now - agent.last_checkin).total_seconds() >= self._stale_timeout
            ]

    async def cleanup_stale(self) -> int:
        """Remove stale agents and return count removed."""
        now = datetime.now(timezone.utc)
        async with self._lock:
            stale_ids = [
                host_id for host_id, agent in self._agents.items()
                if (now - agent.last_checkin).total_seconds() >= self._stale_timeout
            ]
            for host_id in stale_ids:
                del self._agents[host_id]
            return len(stale_ids)


class GoAgentListener(DiscoveryMethod):
    """
    Listen for Go agent check-ins to discover managed endpoints.

    Runs an HTTP server that receives agent registrations.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8082,
        stale_timeout: int = 300,
    ):
        """
        Initialize Go agent listener.

        Args:
            host: Host to bind to
            port: Port to listen on
            stale_timeout: Seconds before agent is considered stale
        """
        self.host = host
        self.port = port
        self.registry = GoAgentRegistry(stale_timeout_seconds=stale_timeout)
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._on_agent_callback: Optional[Callable[[AgentInfo], None]] = None

    @property
    def name(self) -> str:
        return "go_agent"

    def set_callback(self, callback: Callable[[AgentInfo], None]) -> None:
        """Set callback for agent registration events."""
        self._on_agent_callback = callback

    async def start(self) -> None:
        """Start the HTTP listener."""
        self._app = web.Application()
        self._app.router.add_post("/agent/checkin", self._handle_checkin)
        self._app.router.add_get("/agent/health", self._handle_health)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info(f"Go agent listener started on {self.host}:{self.port}")

    async def stop(self) -> None:
        """Stop the HTTP listener."""
        if self._runner:
            await self._runner.cleanup()
            logger.info("Go agent listener stopped")

    async def _handle_checkin(self, request: web.Request) -> web.Response:
        """Handle agent check-in request."""
        try:
            data = await request.json()

            agent_info = AgentInfo(
                host_id=data.get("host_id", ""),
                hostname=data.get("hostname", ""),
                ip_address=request.remote or data.get("ip_address", ""),
                os_name=data.get("os_name", "Windows"),
                os_version=data.get("os_version", ""),
                agent_version=data.get("agent_version", ""),
                last_checkin=now_utc(),
                capabilities=data.get("capabilities", []),
            )

            await self.registry.register(agent_info)

            if self._on_agent_callback:
                self._on_agent_callback(agent_info)

            return web.json_response({"status": "ok"})

        except Exception as e:
            logger.error(f"Error handling agent checkin: {e}")
            return web.json_response(
                {"status": "error", "message": str(e)},
                status=400,
            )

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        active = await self.registry.get_active()
        return web.json_response({
            "status": "ok",
            "active_agents": len(active),
        })

    async def is_available(self) -> bool:
        """Check if listener is running."""
        return self._runner is not None

    async def discover(self) -> list[DiscoveredDevice]:
        """
        Get discovered devices from registered agents.

        Returns list of devices from active Go agents.
        """
        agents = await self.registry.get_active()

        devices = []
        for agent in agents:
            device = DiscoveredDevice(
                ip_address=agent.ip_address,
                hostname=agent.hostname,
                os_name=agent.os_name,
                os_version=agent.os_version,
                discovery_source=DiscoverySource.GO_AGENT,
            )
            devices.append(device)

        logger.info(f"Go agent discovery found {len(devices)} endpoints")
        return devices


class GoAgentDiscovery(DiscoveryMethod):
    """
    Read-only discovery from an existing Go agent registry.

    Use this when the listener is managed elsewhere but you want
    to include registered agents in discovery results.
    """

    def __init__(self, registry: GoAgentRegistry):
        """
        Initialize with existing registry.

        Args:
            registry: GoAgentRegistry instance to read from
        """
        self.registry = registry

    @property
    def name(self) -> str:
        return "go_agent"

    async def is_available(self) -> bool:
        """Always available if registry exists."""
        return True

    async def discover(self) -> list[DiscoveredDevice]:
        """Get devices from registry."""
        agents = await self.registry.get_active()

        return [
            DiscoveredDevice(
                ip_address=agent.ip_address,
                hostname=agent.hostname,
                os_name=agent.os_name,
                os_version=agent.os_version,
                discovery_source=DiscoverySource.GO_AGENT,
            )
            for agent in agents
        ]
