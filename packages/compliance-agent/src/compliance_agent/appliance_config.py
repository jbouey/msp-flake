"""
Appliance-mode configuration loader.

Loads configuration from YAML file for standalone appliance deployment.
This is an alternative to the environment-variable based config used
when running under the full NixOS module.

Config file location: /var/lib/msp/config.yaml
"""

import logging
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field, field_validator

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("/var/lib/msp/config.yaml")


class ApplianceConfig(BaseModel):
    """Configuration for appliance-mode agent."""

    # Required settings
    site_id: str = Field(..., description="Site identifier from Central Command")
    api_key: str = Field(..., description="API key for authentication")

    # API connection
    api_endpoint: str = Field(
        default="https://api.osiriscare.net",
        description="Central Command API endpoint"
    )

    # Timing
    poll_interval: int = Field(
        default=60,
        ge=10,
        le=3600,
        description="Poll interval in seconds"
    )

    # Features
    enable_drift_detection: bool = Field(
        default=True,
        description="Enable drift detection checks"
    )

    enable_evidence_upload: bool = Field(
        default=True,
        description="Upload evidence bundles to Central Command"
    )

    enable_l1_sync: bool = Field(
        default=True,
        description="Sync L1 rules from Central Command"
    )

    # Auto-Healing (Three-Tier System)
    healing_enabled: bool = Field(
        default=True,
        description="Enable three-tier auto-healing system"
    )

    healing_dry_run: bool = Field(
        default=True,
        description="Dry-run mode: log healing actions without executing"
    )

    # Paths
    state_dir: Path = Field(
        default=Path("/var/lib/msp"),
        description="State directory for evidence, queue, etc."
    )

    # Logging
    log_level: str = Field(
        default="INFO",
        description="Log level (DEBUG, INFO, WARNING, ERROR)"
    )

    # Optional Windows management
    windows_targets: list[dict] = Field(
        default_factory=list,
        description="Windows servers to manage via WinRM"
    )

    @field_validator('log_level')
    @classmethod
    def validate_log_level(cls, v):
        if v.upper() not in ['DEBUG', 'INFO', 'WARNING', 'ERROR']:
            raise ValueError('log_level must be DEBUG, INFO, WARNING, or ERROR')
        return v.upper()

    @property
    def evidence_dir(self) -> Path:
        """Evidence storage directory."""
        return self.state_dir / "evidence"

    @property
    def queue_db_path(self) -> Path:
        """SQLite queue database path."""
        return self.state_dir / "queue.db"

    @property
    def rules_dir(self) -> Path:
        """L1 rules directory."""
        return self.state_dir / "rules"


def load_appliance_config(config_path: Optional[Path] = None) -> ApplianceConfig:
    """
    Load appliance configuration from YAML file.

    Args:
        config_path: Path to config file (default: /var/lib/msp/config.yaml)

    Returns:
        ApplianceConfig instance

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If config is invalid
    """
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    logger.info(f"Loading config from {config_path}")

    with open(config_path, 'r') as f:
        config_dict = yaml.safe_load(f)

    if config_dict is None:
        raise ValueError(f"Config file is empty: {config_path}")

    return ApplianceConfig(**config_dict)
