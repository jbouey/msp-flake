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

    logger.info(f"Local Portal ready on port {config.port}")
    yield

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
