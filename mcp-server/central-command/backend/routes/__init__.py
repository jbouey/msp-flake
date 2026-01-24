"""FastAPI routes for Central Command backend."""

from .device_sync import router as device_sync_router

__all__ = ["device_sync_router"]
