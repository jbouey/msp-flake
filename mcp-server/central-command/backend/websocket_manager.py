"""
WebSocket manager for real-time event push to frontend clients.

Events: appliance_checkin, incident_created, incident_resolved,
        notification_created, compliance_drift, order_status_changed
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Dict, Set, Any, Optional
from fastapi import WebSocket, WebSocketDisconnect
import structlog

logger = structlog.get_logger()


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts events."""

    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        logger.info("ws_client_connected", total=len(self._connections))

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self._connections.discard(websocket)
        logger.info("ws_client_disconnected", total=len(self._connections))

    async def broadcast(self, event_type: str, payload: Dict[str, Any]):
        """Broadcast an event to all connected clients."""
        message = json.dumps({
            "type": event_type,
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        async with self._lock:
            dead: Set[WebSocket] = set()
            for ws in self._connections:
                try:
                    await ws.send_text(message)
                except Exception:
                    dead.add(ws)
            self._connections -= dead

    @property
    def active_count(self) -> int:
        return len(self._connections)


# Singleton instance
ws_manager = ConnectionManager()


async def broadcast_event(event_type: str, payload: Dict[str, Any]):
    """Convenience function to broadcast from anywhere in the app."""
    await ws_manager.broadcast(event_type, payload)
