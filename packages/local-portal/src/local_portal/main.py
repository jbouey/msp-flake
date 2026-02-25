"""
Local Portal FastAPI Application.

Provides local web UI for device transparency without Central Command dependency.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .routes import devices, scans, compliance, exports, dashboard, sync
from .config import PortalConfig

logger = logging.getLogger(__name__)


def _load_daemon_config() -> dict:
    """Load site_id, api_key, api_endpoint from the Go daemon's config.yaml."""
    config_path = Path("/var/lib/msp/config.yaml")
    if not config_path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        # Fallback: parse simple key: value lines
        result = {}
        for line in config_path.read_text().splitlines():
            line = line.strip()
            if ":" in line and not line.startswith("#") and not line.startswith("-"):
                key, _, val = line.partition(":")
                result[key.strip()] = val.strip().strip('"')
        return result
    else:
        return yaml.safe_load(config_path.read_text()) or {}


async def _auto_sync_loop(db, interval: int = 300):
    """Background task: sync device inventory to Central Command every `interval` seconds."""
    from .services.central_sync import sync_to_central

    daemon_cfg = _load_daemon_config()
    central_url = (
        os.environ.get("CENTRAL_COMMAND_URL")
        or daemon_cfg.get("api_endpoint")
    )
    api_key = (
        os.environ.get("CENTRAL_COMMAND_API_KEY")
        or daemon_cfg.get("api_key")
    )
    site_id = os.environ.get("SITE_ID") or daemon_cfg.get("site_id")

    # Derive appliance_id from hostname (matches Go daemon checkin)
    appliance_id = os.environ.get("APPLIANCE_ID") or "osiriscare-appliance"

    if not central_url or not site_id:
        logger.warning("Auto-sync disabled: no central_url or site_id configured")
        return

    logger.info(f"Auto-sync enabled: {central_url} site={site_id} every {interval}s")

    # Wait 30s for scanner to populate DB on first boot
    await asyncio.sleep(30)

    while True:
        try:
            result = await sync_to_central(
                db=db,
                central_url=central_url,
                appliance_id=appliance_id,
                site_id=site_id,
                api_key=api_key,
            )
            if result.get("status") == "success":
                logger.info(
                    f"Auto-sync OK: {result.get('devices_synced', 0)} devices "
                    f"({result.get('devices_created', 0)} new, {result.get('devices_updated', 0)} updated)"
                )
            else:
                logger.warning(f"Auto-sync failed: {result.get('error', 'unknown')}")
        except Exception as e:
            logger.error(f"Auto-sync error: {e}")

        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("Local Portal starting up...")

    # Load configuration
    config = PortalConfig.from_env()
    app.state.config = config

    # Connect to scanner database (read-only for most operations)
    from .db import get_db
    app.state.db = get_db(config.scanner_db_path)

    # Start background auto-sync to Central Command
    sync_task = asyncio.create_task(_auto_sync_loop(app.state.db))

    logger.info(f"Local Portal ready on port {config.port}")
    yield

    sync_task.cancel()
    logger.info("Local Portal shutting down...")


def create_app() -> FastAPI:
    """Create FastAPI application."""
    app = FastAPI(
        title="MSP Local Portal",
        description="Local device transparency and compliance visibility",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS for local development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Local only, so permissive
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routes
    app.include_router(dashboard.router, prefix="/api", tags=["dashboard"])
    app.include_router(devices.router, prefix="/api/devices", tags=["devices"])
    app.include_router(scans.router, prefix="/api/scans", tags=["scans"])
    app.include_router(compliance.router, prefix="/api/compliance", tags=["compliance"])
    app.include_router(exports.router, prefix="/api/exports", tags=["exports"])
    app.include_router(sync.router, prefix="/api", tags=["sync"])

    # Health check
    @app.get("/health")
    async def health():
        return {"status": "healthy", "service": "local-portal"}

    return app


app = create_app()


def main():
    """Entry point for local-portal CLI."""
    import argparse

    parser = argparse.ArgumentParser(description="MSP Local Portal")
    parser.add_argument("--port", type=int, default=8083, help="Port to listen on")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    uvicorn.run(
        "local_portal.main:app",
        host=args.host,
        port=args.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
