"""
Configuration management for compliance agent.

Loads settings from environment variables (set by NixOS module).
Validates all required settings and provides typed access.
"""

import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field, validator
from datetime import time


class AgentConfig(BaseModel):
    """Compliance agent configuration loaded from environment."""

    # ========================================================================
    # Site Identification
    # ========================================================================

    site_id: str = Field(..., description="Unique site identifier")
    host_id: str = Field(..., description="Host identifier")

    # ========================================================================
    # Deployment Mode
    # ========================================================================

    deployment_mode: str = Field(
        default="reseller",
        description="Deployment mode: reseller or direct"
    )
    reseller_id: Optional[str] = Field(
        default=None,
        description="MSP reseller ID (required if mode=reseller)"
    )

    # ========================================================================
    # MCP Connection
    # ========================================================================

    mcp_url: str = Field(
        default="https://mcp.local",
        description="MCP base URL"
    )
    mcp_api_key_file: Optional[Path] = Field(
        default=None,
        description="Path to MCP API key file"
    )

    # ========================================================================
    # Policy
    # ========================================================================

    policy_version: str = Field(
        default="1.0",
        description="Policy version identifier"
    )
    baseline_path: Path = Field(
        ...,
        description="Path to NixOS baseline configuration"
    )

    # ========================================================================
    # Timing
    # ========================================================================

    poll_interval: int = Field(
        default=60,
        ge=10,
        le=3600,
        description="Poll MCP every N seconds"
    )

    order_ttl: int = Field(
        default=900,
        ge=60,
        description="Order TTL in seconds"
    )

    maintenance_window: str = Field(
        default="02:00-04:00",
        description="Maintenance window HH:MM-HH:MM UTC"
    )

    allow_disruptive_outside_window: bool = Field(
        default=False,
        description="Allow disruptive actions outside maintenance window"
    )

    # ========================================================================
    # Secrets
    # ========================================================================

    client_cert_file: Path = Field(
        ...,
        description="Path to mTLS client certificate"
    )

    client_key_file: Path = Field(
        ...,
        description="Path to mTLS client key"
    )

    signing_key_file: Path = Field(
        ...,
        description="Path to Ed25519 signing key"
    )

    webhook_secret_file: Optional[Path] = Field(
        default=None,
        description="Path to webhook HMAC secret"
    )

    # ========================================================================
    # Storage Paths
    # ========================================================================

    state_dir: Path = Field(
        default=Path("/var/lib/msp-compliance-agent"),
        description="State directory for queue database, etc."
    )
    evidence_dir: Optional[Path] = Field(
        default=None,
        description="Evidence storage directory (defaults to state_dir/evidence)"
    )

    @validator('evidence_dir', always=True)
    def set_evidence_dir(cls, v, values):
        """Set evidence_dir from state_dir if not explicitly provided."""
        if v is None and 'state_dir' in values:
            return values['state_dir'] / 'evidence'
        return v

    # ========================================================================
    # Evidence
    # ========================================================================

    evidence_retention: int = Field(
        default=200,
        ge=10,
        description="Keep last N evidence bundles"
    )

    prune_retention_days: int = Field(
        default=90,
        ge=1,
        description="Never delete bundles < N days old"
    )

    # ========================================================================
    # Clock & Time
    # ========================================================================

    ntp_max_skew_ms: int = Field(
        default=5000,
        ge=100,
        description="Maximum NTP offset in milliseconds"
    )

    # ========================================================================
    # Health Checks
    # ========================================================================

    rebuild_health_check_timeout: int = Field(
        default=60,
        ge=10,
        le=300,
        description="Seconds to wait for health check after rebuild"
    )

    # ========================================================================
    # Reseller Integrations
    # ========================================================================

    rmm_webhook_url: Optional[str] = Field(
        default=None,
        description="RMM/PSA webhook URL (reseller mode)"
    )

    syslog_target: Optional[str] = Field(
        default=None,
        description="Syslog target host:port (reseller mode)"
    )

    # ========================================================================
    # Logging
    # ========================================================================

    log_level: str = Field(
        default="INFO",
        description="Agent log level"
    )

    # ========================================================================
    # Validators
    # ========================================================================

    @validator('deployment_mode')
    def validate_deployment_mode(cls, v):
        if v not in ['reseller', 'direct']:
            raise ValueError('deployment_mode must be reseller or direct')
        return v

    @validator('reseller_id')
    def validate_reseller_id(cls, v, values):
        if values.get('deployment_mode') == 'reseller' and not v:
            raise ValueError('reseller_id required when deployment_mode=reseller')
        return v

    @validator('maintenance_window')
    def validate_maintenance_window(cls, v):
        import re
        if not re.match(r'^\d{2}:\d{2}-\d{2}:\d{2}$', v):
            raise ValueError('maintenance_window must be HH:MM-HH:MM format')
        return v

    @validator('baseline_path', 'client_cert_file', 'client_key_file', 'signing_key_file')
    def validate_file_exists(cls, v):
        if v and not Path(v).exists():
            raise ValueError(f'File does not exist: {v}')
        return Path(v)

    @validator('log_level')
    def validate_log_level(cls, v):
        if v not in ['DEBUG', 'INFO', 'WARNING', 'ERROR']:
            raise ValueError('log_level must be DEBUG, INFO, WARNING, or ERROR')
        return v

    # ========================================================================
    # Parsed Properties
    # ========================================================================

    @property
    def maintenance_window_start(self) -> time:
        """Parse maintenance window start time."""
        start_str = self.maintenance_window.split('-')[0]
        hour, minute = map(int, start_str.split(':'))
        return time(hour, minute)

    @property
    def maintenance_window_end(self) -> time:
        """Parse maintenance window end time."""
        end_str = self.maintenance_window.split('-')[1]
        hour, minute = map(int, end_str.split(':'))
        return time(hour, minute)

    @property
    def is_reseller_mode(self) -> bool:
        """Check if in reseller deployment mode."""
        return self.deployment_mode == 'reseller'

    @property
    def queue_db_path(self) -> Path:
        """SQLite queue database path."""
        return self.state_dir / 'queue.db'

    @property
    def mcp_poll_interval_sec(self) -> int:
        """Alias for poll_interval for backward compatibility."""
        return self.poll_interval

    class Config:
        """Pydantic config."""
        validate_assignment = True
        extra = 'forbid'  # Reject unknown fields


def load_config() -> AgentConfig:
    """
    Load configuration from environment variables.

    Environment variables are set by the NixOS module.

    Returns:
        AgentConfig: Validated configuration

    Raises:
        ValueError: If required settings missing or invalid
    """

    # Map environment variables to config fields
    config_dict = {
        # Site identification
        'site_id': os.environ['SITE_ID'],
        'host_id': os.environ['HOST_ID'],

        # Deployment mode
        'deployment_mode': os.environ.get('DEPLOYMENT_MODE', 'reseller'),
        'reseller_id': os.environ.get('RESELLER_ID') or None,

        # MCP connection
        'mcp_url': os.environ.get('MCP_URL', 'https://mcp.local'),

        # Policy
        'policy_version': os.environ.get('POLICY_VERSION', '1.0'),
        'baseline_path': os.environ['BASELINE_PATH'],

        # Timing
        'poll_interval': int(os.environ.get('POLL_INTERVAL', '60')),
        'order_ttl': int(os.environ.get('ORDER_TTL', '900')),
        'maintenance_window': os.environ.get('MAINTENANCE_WINDOW', '02:00-04:00'),
        'allow_disruptive_outside_window': os.environ.get('ALLOW_DISRUPTIVE_OUTSIDE_WINDOW', 'false').lower() == 'true',

        # Secrets
        'client_cert_file': os.environ['CLIENT_CERT_FILE'],
        'client_key_file': os.environ['CLIENT_KEY_FILE'],
        'signing_key_file': os.environ['SIGNING_KEY_FILE'],
        'webhook_secret_file': os.environ.get('WEBHOOK_SECRET_FILE') or None,

        # Storage paths
        'state_dir': Path(os.environ.get('STATE_DIR', '/var/lib/msp-compliance-agent')),

        # Evidence
        'evidence_retention': int(os.environ.get('EVIDENCE_RETENTION', '200')),
        'prune_retention_days': int(os.environ.get('PRUNE_RETENTION_DAYS', '90')),

        # Clock
        'ntp_max_skew_ms': int(os.environ.get('NTP_MAX_SKEW_MS', '5000')),

        # Health checks
        'rebuild_health_check_timeout': int(os.environ.get('REBUILD_HEALTH_CHECK_TIMEOUT', '60')),

        # Reseller integrations
        'rmm_webhook_url': os.environ.get('RMM_WEBHOOK_URL') or None,
        'syslog_target': os.environ.get('SYSLOG_TARGET') or None,

        # Logging
        'log_level': os.environ.get('LOG_LEVEL', 'INFO'),
    }

    return AgentConfig(**config_dict)
