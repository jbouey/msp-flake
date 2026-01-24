"""
Local Portal configuration.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class PortalConfig:
    """Configuration for Local Portal."""

    # Network settings
    port: int = 8083
    host: str = "0.0.0.0"

    # Database paths (read from network-scanner)
    scanner_db_path: Path = field(
        default_factory=lambda: Path("/var/lib/msp/devices.db")
    )

    # Scanner API for triggering scans
    scanner_api_url: str = "http://127.0.0.1:8082"

    # Export settings
    export_dir: Path = field(
        default_factory=lambda: Path("/var/lib/msp/exports")
    )

    # Appliance info
    appliance_id: Optional[str] = None
    site_name: str = "Local Site"

    @classmethod
    def from_env(cls) -> "PortalConfig":
        """Load configuration from environment variables."""
        config = cls()

        if port := os.environ.get("LOCAL_PORTAL_PORT"):
            config.port = int(port)

        if host := os.environ.get("LOCAL_PORTAL_HOST"):
            config.host = host

        if db_path := os.environ.get("SCANNER_DB_PATH"):
            config.scanner_db_path = Path(db_path)

        if scanner_url := os.environ.get("SCANNER_API_URL"):
            config.scanner_api_url = scanner_url

        if export_dir := os.environ.get("EXPORT_DIR"):
            config.export_dir = Path(export_dir)

        if appliance_id := os.environ.get("APPLIANCE_ID"):
            config.appliance_id = appliance_id

        if site_name := os.environ.get("SITE_NAME"):
            config.site_name = site_name

        return config
