"""
Parameter Validation - Guardrails for runbook parameters
Prevents injection attacks and validates inputs
"""
import re
from typing import Dict, List, Any
from pydantic import BaseModel, validator, Field


# Whitelists for critical parameters
ALLOWED_SERVICES = [
    "nginx",
    "apache2",
    "postgresql",
    "mysql",
    "redis",
    "docker",
    "containerd",
    "grafana",
    "prometheus",
    "restic-backup"
]

ALLOWED_FILESYSTEMS = [
    "/",
    "/home",
    "/var",
    "/backup",
    "/data"
]

ALLOWED_LOG_PATHS = [
    "/var/log",
    "/var/log/nginx",
    "/var/log/apache2",
    "/var/log/syslog",
    "/var/log/restic"
]


class ServiceRestartParams(BaseModel):
    """Validated parameters for service restart action"""
    service_name: str = Field(..., description="Name of systemd service to restart")

    @validator('service_name')
    def validate_service(cls, v):
        # Must be in whitelist
        if v not in ALLOWED_SERVICES:
            raise ValueError(f'Service {v} not in whitelist: {ALLOWED_SERVICES}')

        # Reject shell metacharacters
        if re.search(r'[;&|`$()<>]', v):
            raise ValueError('Invalid characters in service name')

        # Reject path traversal
        if '..' in v or '/' in v:
            raise ValueError('Path traversal attempt in service name')

        return v


class DiskSpaceCheckParams(BaseModel):
    """Validated parameters for disk space check action"""
    filesystem: str = Field(..., description="Filesystem path to check")
    min_free_gb: int = Field(default=10, ge=1, le=10000, description="Minimum free space in GB")

    @validator('filesystem')
    def validate_filesystem(cls, v):
        # Must be in allowed filesystems or subdirectory
        if not any(v.startswith(allowed) for allowed in ALLOWED_FILESYSTEMS):
            raise ValueError(f'Filesystem {v} not in allowed paths')

        # Reject path traversal
        if '..' in v:
            raise ValueError('Path traversal not allowed')

        # Must be absolute path
        if not v.startswith('/'):
            raise ValueError('Filesystem path must be absolute')

        return v


class LogCheckParams(BaseModel):
    """Validated parameters for log checking action"""
    log_path: str = Field(..., description="Path to log file")
    lines: int = Field(default=100, ge=1, le=10000, description="Number of lines to retrieve")

    @validator('log_path')
    def validate_log_path(cls, v):
        # Must be in allowed log directories
        if not any(v.startswith(allowed) for allowed in ALLOWED_LOG_PATHS):
            raise ValueError(f'Log path {v} not in allowed directories')

        # Reject path traversal
        if '..' in v:
            raise ValueError('Path traversal not allowed')

        # Must end with .log
        if not v.endswith('.log'):
            raise ValueError('Log path must end with .log')

        return v


class CertificateCheckParams(BaseModel):
    """Validated parameters for certificate checking"""
    cert_path: str = Field(..., description="Path to certificate file")
    domains: List[str] = Field(..., description="Expected domains in certificate")

    @validator('cert_path')
    def validate_cert_path(cls, v):
        # Must be in standard cert locations
        allowed_cert_dirs = ['/etc/ssl/certs', '/etc/letsencrypt/live']

        if not any(v.startswith(allowed) for allowed in allowed_cert_dirs):
            raise ValueError(f'Certificate path must be in {allowed_cert_dirs}')

        # Reject path traversal
        if '..' in v:
            raise ValueError('Path traversal not allowed')

        # Must end with .crt, .pem, or .cert
        if not any(v.endswith(ext) for ext in ['.crt', '.pem', '.cert']):
            raise ValueError('Invalid certificate file extension')

        return v

    @validator('domains')
    def validate_domains(cls, v):
        # Validate each domain
        domain_pattern = re.compile(
            r'^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$'
        )

        for domain in v:
            if not domain_pattern.match(domain):
                raise ValueError(f'Invalid domain format: {domain}')

            # Reject wildcard domains for security
            if domain.startswith('*'):
                raise ValueError('Wildcard domains not allowed')

        return v


class BackupRestoreParams(BaseModel):
    """Validated parameters for backup restore testing"""
    repository: str = Field(..., description="Backup repository path")
    age_days_max: int = Field(default=7, ge=1, le=90, description="Maximum backup age in days")

    @validator('repository')
    def validate_repository(cls, v):
        # Must be in allowed backup locations
        allowed_backup_dirs = ['/backup', '/var/backup', '/mnt/backup']

        if not any(v.startswith(allowed) for allowed in allowed_backup_dirs):
            raise ValueError(f'Backup repository must be in {allowed_backup_dirs}')

        # Reject path traversal
        if '..' in v:
            raise ValueError('Path traversal not allowed')

        return v


class CleanupParams(BaseModel):
    """Validated parameters for cleanup actions"""
    paths: List[str] = Field(..., description="Paths to clean")
    older_than_days: int = Field(default=7, ge=1, le=365, description="Files older than N days")
    exclude_patterns: List[str] = Field(default=[], description="Patterns to exclude from cleanup")

    @validator('paths')
    def validate_paths(cls, v):
        # Only allow cleanup in specific directories
        allowed_cleanup_dirs = ['/tmp', '/var/tmp', '/var/cache']

        for path in v:
            if not any(path.startswith(allowed) for allowed in allowed_cleanup_dirs):
                raise ValueError(f'Cleanup path {path} not in allowed directories')

            # Reject path traversal
            if '..' in path:
                raise ValueError('Path traversal not allowed')

        return v

    @validator('exclude_patterns')
    def validate_exclude_patterns(cls, v):
        # Ensure patterns don't contain dangerous characters
        for pattern in v:
            if re.search(r'[;&|`$()<>]', pattern):
                raise ValueError(f'Invalid characters in exclude pattern: {pattern}')

        return v


# Parameter validator dispatcher
PARAM_VALIDATORS = {
    "restart_service": ServiceRestartParams,
    "check_disk_usage": DiskSpaceCheckParams,
    "verify_disk_space": DiskSpaceCheckParams,
    "check_backup_logs": LogCheckParams,
    "check_certificate_status": CertificateCheckParams,
    "restore_files_to_scratch": BackupRestoreParams,
    "clean_tmp_directories": CleanupParams,
    "compress_old_logs": CleanupParams,
}


def validate_action_params(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate parameters for a given action

    Args:
        action: Action name (e.g., "restart_service")
        params: Parameters to validate

    Returns:
        Validated parameters

    Raises:
        ValueError: If validation fails
    """

    # Get validator for this action
    validator_class = PARAM_VALIDATORS.get(action)

    if not validator_class:
        # No specific validator - do basic sanity checks
        return _basic_validation(params)

    # Validate with Pydantic model
    try:
        validated = validator_class(**params)
        return validated.dict()
    except Exception as e:
        raise ValueError(f"Parameter validation failed for {action}: {str(e)}")


def _basic_validation(params: Dict[str, Any]) -> Dict[str, Any]:
    """Basic validation for actions without specific validators"""

    # Check for obvious injection attempts in all string values
    for key, value in params.items():
        if isinstance(value, str):
            # Reject shell metacharacters
            if re.search(r'[;&|`$()<>]', value):
                raise ValueError(f'Invalid characters in parameter {key}: {value}')

            # Reject path traversal
            if '..' in value:
                raise ValueError(f'Path traversal in parameter {key}: {value}')

    return params


# Helper function for testing
if __name__ == "__main__":
    # Test valid parameters
    print("Testing Parameter Validation\n")

    # Valid service restart
    try:
        result = validate_action_params("restart_service", {"service_name": "nginx"})
        print(f"✅ Valid service restart: {result}")
    except ValueError as e:
        print(f"❌ {e}")

    # Invalid service (not in whitelist)
    try:
        result = validate_action_params("restart_service", {"service_name": "malicious-service"})
        print(f"✅ Invalid service accepted: {result}")
    except ValueError as e:
        print(f"✅ Invalid service rejected: {e}")

    # Injection attempt
    try:
        result = validate_action_params("restart_service", {"service_name": "nginx; rm -rf /"})
        print(f"❌ Injection accepted: {result}")
    except ValueError as e:
        print(f"✅ Injection blocked: {e}")

    # Path traversal
    try:
        result = validate_action_params("check_disk_usage", {"filesystem": "/../etc/passwd", "min_free_gb": 10})
        print(f"❌ Path traversal accepted: {result}")
    except ValueError as e:
        print(f"✅ Path traversal blocked: {e}")

    print("\n✅ All validation tests passed!")
