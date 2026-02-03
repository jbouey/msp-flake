"""
Appliance-mode configuration loader.

Loads configuration from YAML file for standalone appliance deployment.
This is an alternative to the environment-variable based config used
when running under the full NixOS module.

Config file location: /var/lib/msp/config.yaml
"""

import logging
import os
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
        default=False,
        description="Dry-run mode: log healing actions without executing (default: off for production)"
    )

    # L2 LLM Planner (for incidents not matched by L1 rules)
    l2_enabled: bool = Field(
        default=False,
        description="Enable L2 LLM planner for complex incidents"
    )

    l2_api_provider: str = Field(
        default="anthropic",
        description="LLM API provider: anthropic, openai"
    )

    l2_api_key: str = Field(
        default="",
        description="API key for LLM provider"
    )

    l2_api_model: str = Field(
        default="claude-3-5-haiku-latest",
        description="LLM model to use for L2 planning"
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

    # Workstation Discovery (Active Directory)
    workstation_enabled: bool = Field(
        default=True,
        description="Enable workstation discovery and compliance checking"
    )

    domain_controller: Optional[str] = Field(
        default=None,
        description="Domain controller hostname/IP for AD workstation discovery"
    )

    dc_username: Optional[str] = Field(
        default=None,
        description="Username for domain controller access (domain\\user or user@domain)"
    )

    dc_password: Optional[str] = Field(
        default=None,
        description="Password for domain controller access"
    )

    # WORM Storage (Immutable Evidence Archive)
    worm_enabled: bool = Field(
        default=False,
        description="Enable WORM storage upload for evidence bundles"
    )

    worm_mode: str = Field(
        default="proxy",
        description="WORM upload mode: proxy (via MCP) or direct (S3)"
    )

    worm_retention_days: int = Field(
        default=90,
        ge=90,
        description="WORM retention period (minimum 90 days for HIPAA)"
    )

    worm_auto_upload: bool = Field(
        default=True,
        description="Auto-upload evidence bundles to WORM storage on creation"
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

    # Environment variable overrides (for systemd service configuration)
    env_overrides = {
        'healing_dry_run': os.environ.get('HEALING_DRY_RUN'),
        'state_dir': os.environ.get('STATE_DIR'),
        'log_level': os.environ.get('LOG_LEVEL'),
    }

    for key, value in env_overrides.items():
        if value is not None:
            # Convert string to appropriate type
            if key == 'healing_dry_run':
                config_dict[key] = value.lower() not in ('false', '0', 'no')
            elif key == 'state_dir':
                config_dict[key] = Path(value)
            else:
                config_dict[key] = value
            logger.info(f"Environment override: {key}={config_dict[key]}")

    return ApplianceConfig(**config_dict)
