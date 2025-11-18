"""
Configuration Management - Agent Settings

This module handles loading and validation of agent configuration from YAML/JSON files.

Features:
- YAML/JSON config file support
- Pydantic validation with type checking
- Environment variable overrides
- Secrets integration (SOPS, Vault paths)
- Maintenance window parsing
- Sensible defaults for all optional fields

Configuration Sources (in priority order):
1. Environment variables (override everything)
2. Config file (YAML or JSON)
3. Defaults (built-in sensible values)

Example config.yaml:
    site_id: clinic-001
    mcp_base_url: https://mcp.example.com
    client_cert: /run/secrets/client-cert.pem
    client_key: /run/secrets/client-key.pem
    ca_cert: /run/secrets/ca-cert.pem
    mcp_public_key: abc123...
    queue_path: /var/lib/msp/queue.db
    poll_interval: 60
"""

import os
import json
import logging
from pathlib import Path
from typing import Optional, List
from datetime import time
from pydantic import BaseModel, Field, validator, root_validator
import yaml

logger = logging.getLogger(__name__)


class MaintenanceWindow(BaseModel):
    """Maintenance window configuration"""

    enabled: bool = False
    start: time = time(2, 0)  # 02:00
    end: time = time(4, 0)    # 04:00
    days: List[str] = ["sunday"]  # Days of week (lowercase)
    timezone: str = "UTC"

    @validator('days')
    def validate_days(cls, v):
        """Ensure days are valid"""
        valid_days = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
        days = [d.lower() for d in v]

        for day in days:
            if day not in valid_days:
                raise ValueError(f"Invalid day: {day}. Must be one of {valid_days}")

        return days


class Config(BaseModel):
    """
    Agent configuration with validation

    All required fields must be provided either via config file or environment variables.
    Optional fields have sensible defaults.
    """

    # Identity
    site_id: str = Field(..., description="Unique site identifier")

    # MCP Connection
    mcp_base_url: str = Field(..., description="MCP server base URL")
    mcp_timeout: int = Field(30, description="MCP request timeout (seconds)")
    mcp_public_key: str = Field(..., description="MCP server's Ed25519 public key (hex)")

    # mTLS Certificates
    client_cert: Path = Field(..., description="Path to client certificate")
    client_key: Path = Field(..., description="Path to client private key")
    ca_cert: Path = Field(..., description="Path to CA certificate")

    # Queue Configuration
    queue_path: Path = Field("/var/lib/msp/queue.db", description="Path to SQLite queue database")
    max_queue_size: int = Field(1000, description="Maximum queued orders")

    # Poll Configuration
    poll_interval: int = Field(60, description="Seconds between MCP polls")

    # Order Configuration
    order_ttl: int = Field(900, description="Default order TTL (seconds)")

    # Maintenance Window
    maintenance_window_enabled: bool = Field(False, description="Enable maintenance windows")
    maintenance_window_start: time = Field(time(2, 0), description="Maintenance window start time")
    maintenance_window_end: time = Field(time(4, 0), description="Maintenance window end time")
    maintenance_window_days: List[str] = Field(["sunday"], description="Maintenance window days")

    # Deployment Mode
    deployment_mode: str = Field("direct", description="Deployment mode: direct or reseller")

    # Logging
    log_level: str = Field("INFO", description="Logging level")
    log_file: Optional[Path] = Field(None, description="Log file path")

    @validator('client_cert', 'client_key', 'ca_cert')
    def validate_cert_paths(cls, v):
        """Ensure certificate paths exist"""
        if not v.exists():
            raise ValueError(f"Certificate file not found: {v}")
        return v

    @validator('mcp_public_key')
    def validate_public_key(cls, v):
        """Ensure public key is valid hex and correct length"""
        try:
            key_bytes = bytes.fromhex(v)
            if len(key_bytes) != 32:
                raise ValueError(f"Ed25519 public key must be 32 bytes, got {len(key_bytes)}")
        except ValueError as e:
            raise ValueError(f"Invalid public key hex: {e}")
        return v

    @validator('deployment_mode')
    def validate_deployment_mode(cls, v):
        """Ensure deployment mode is valid"""
        valid_modes = {"direct", "reseller"}
        if v not in valid_modes:
            raise ValueError(f"Invalid deployment_mode: {v}. Must be one of {valid_modes}")
        return v

    @validator('log_level')
    def validate_log_level(cls, v):
        """Ensure log level is valid"""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v_upper = v.upper()
        if v_upper not in valid_levels:
            raise ValueError(f"Invalid log_level: {v}. Must be one of {valid_levels}")
        return v_upper

    @validator('queue_path')
    def ensure_queue_dir(cls, v):
        """Ensure queue directory exists"""
        v.parent.mkdir(parents=True, exist_ok=True)
        return v

    @root_validator
    def validate_maintenance_window(cls, values):
        """Validate maintenance window configuration"""
        if values.get('maintenance_window_enabled'):
            start = values.get('maintenance_window_start')
            end = values.get('maintenance_window_end')
            days = values.get('maintenance_window_days')

            if not days:
                raise ValueError("maintenance_window_days must be specified when enabled")

        return values

    @classmethod
    def load(cls, config_path: str) -> 'Config':
        """
        Load configuration from file with environment variable overrides

        Args:
            config_path: Path to YAML or JSON config file

        Returns:
            Validated Config object

        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If config is invalid
        """
        config_file = Path(config_path)

        if not config_file.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        logger.info(f"Loading configuration from {config_path}")

        # Load config file
        with open(config_file, 'r') as f:
            if config_file.suffix in {'.yaml', '.yml'}:
                config_data = yaml.safe_load(f)
            elif config_file.suffix == '.json':
                config_data = json.load(f)
            else:
                raise ValueError(f"Unsupported config format: {config_file.suffix}")

        # Apply environment variable overrides
        env_overrides = cls._load_env_overrides()
        config_data.update(env_overrides)

        # Validate and create Config object
        try:
            config = cls(**config_data)
            logger.info(f"✓ Configuration loaded for site: {config.site_id}")
            return config

        except Exception as e:
            logger.error(f"Configuration validation failed: {e}")
            raise ValueError(f"Invalid configuration: {e}")

    @classmethod
    def _load_env_overrides(cls) -> dict:
        """
        Load configuration from environment variables

        Environment variables use MSP_ prefix:
        - MSP_SITE_ID
        - MSP_MCP_BASE_URL
        - MSP_MCP_PUBLIC_KEY
        - etc.
        """
        env_mapping = {
            'MSP_SITE_ID': 'site_id',
            'MSP_MCP_BASE_URL': 'mcp_base_url',
            'MSP_MCP_TIMEOUT': 'mcp_timeout',
            'MSP_MCP_PUBLIC_KEY': 'mcp_public_key',
            'MSP_CLIENT_CERT': 'client_cert',
            'MSP_CLIENT_KEY': 'client_key',
            'MSP_CA_CERT': 'ca_cert',
            'MSP_QUEUE_PATH': 'queue_path',
            'MSP_MAX_QUEUE_SIZE': 'max_queue_size',
            'MSP_POLL_INTERVAL': 'poll_interval',
            'MSP_ORDER_TTL': 'order_ttl',
            'MSP_MAINTENANCE_ENABLED': 'maintenance_window_enabled',
            'MSP_DEPLOYMENT_MODE': 'deployment_mode',
            'MSP_LOG_LEVEL': 'log_level',
            'MSP_LOG_FILE': 'log_file'
        }

        overrides = {}

        for env_var, config_key in env_mapping.items():
            value = os.getenv(env_var)
            if value is not None:
                # Type conversion for non-string fields
                if config_key in {'mcp_timeout', 'max_queue_size', 'poll_interval', 'order_ttl'}:
                    value = int(value)
                elif config_key == 'maintenance_window_enabled':
                    value = value.lower() in {'true', '1', 'yes'}

                overrides[config_key] = value
                logger.debug(f"Environment override: {config_key} = {value}")

        return overrides

    def apply_logging(self):
        """Configure logging based on config settings"""
        numeric_level = getattr(logging, self.log_level.upper())

        # Configure root logger
        logging.basicConfig(
            level=numeric_level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(),
                *([logging.FileHandler(self.log_file)] if self.log_file else [])
            ]
        )

        logger.info(f"Logging configured: level={self.log_level}, file={self.log_file}")

    def to_dict(self) -> dict:
        """Export config as dictionary (for debugging/logging)"""
        data = self.dict()

        # Redact sensitive fields
        if 'mcp_public_key' in data:
            data['mcp_public_key'] = data['mcp_public_key'][:16] + '...'

        return data

    def __repr__(self) -> str:
        return f"Config(site_id='{self.site_id}', deployment_mode='{self.deployment_mode}')"


def load_config(config_path: str) -> Config:
    """
    Convenience function to load configuration

    Args:
        config_path: Path to YAML or JSON config file

    Returns:
        Validated Config object
    """
    return Config.load(config_path)


# Example usage and testing
if __name__ == '__main__':
    import tempfile

    logging.basicConfig(level=logging.DEBUG)

    # Create example config file
    example_config = {
        'site_id': 'clinic-test-001',
        'mcp_base_url': 'https://mcp.example.com',
        'mcp_public_key': '0' * 64,  # 32 bytes in hex
        'client_cert': '/tmp/client-cert.pem',
        'client_key': '/tmp/client-key.pem',
        'ca_cert': '/tmp/ca-cert.pem',
        'poll_interval': 60,
        'deployment_mode': 'direct'
    }

    # Create temporary cert files for testing
    for cert_path in ['/tmp/client-cert.pem', '/tmp/client-key.pem', '/tmp/ca-cert.pem']:
        Path(cert_path).touch()

    # Write config to temp file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(example_config, f)
        temp_config_path = f.name

    try:
        # Load and validate config
        print("Loading configuration...")
        config = Config.load(temp_config_path)

        print(f"\n✓ Configuration loaded successfully:")
        print(f"  Site ID: {config.site_id}")
        print(f"  MCP URL: {config.mcp_base_url}")
        print(f"  Poll interval: {config.poll_interval}s")
        print(f"  Deployment mode: {config.deployment_mode}")

        # Test environment override
        print("\nTesting environment override...")
        os.environ['MSP_POLL_INTERVAL'] = '120'
        config2 = Config.load(temp_config_path)
        print(f"  Poll interval (overridden): {config2.poll_interval}s")

        print("\n✓ config.py module ready")

    finally:
        # Cleanup
        os.unlink(temp_config_path)
        for cert_path in ['/tmp/client-cert.pem', '/tmp/client-key.pem', '/tmp/ca-cert.pem']:
            try:
                os.unlink(cert_path)
            except FileNotFoundError:
                pass
