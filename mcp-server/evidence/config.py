#!/usr/bin/env python3
"""
Evidence Pipeline Configuration

Central configuration for evidence collection, signing, and storage.
Uses environment variables with sensible defaults for testing.
"""

import os
from pathlib import Path


class EvidenceConfig:
    """Configuration for evidence pipeline"""

    # Evidence storage directory
    EVIDENCE_DIR = Path(os.getenv(
        'MCP_EVIDENCE_DIR',
        os.path.expanduser('~/msp-production/evidence')
    ))

    # Signing key paths
    SIGNING_KEY_DIR = Path(os.getenv(
        'MCP_SIGNING_KEY_DIR',
        os.path.expanduser('~/msp-production/signing-keys')
    ))

    PRIVATE_KEY = SIGNING_KEY_DIR / 'private-key.key'
    PUBLIC_KEY = SIGNING_KEY_DIR / 'private-key.pub'

    # Schema path
    SCHEMA_PATH = Path(os.getenv(
        'MCP_SCHEMA_PATH',
        str(Path(__file__).parent.parent.parent / 'opt/msp/evidence/schema/evidence-bundle-v1.schema.json')
    ))

    # Cosign password (for production, use SOPS/Vault)
    COSIGN_PASSWORD = os.getenv('COSIGN_PASSWORD', 'production-password-change-in-real-deployment')

    # Client ID (will be passed at runtime, this is just default for testing)
    DEFAULT_CLIENT_ID = os.getenv('MCP_CLIENT_ID', 'test-client-001')

    @classmethod
    def validate(cls) -> bool:
        """
        Validate configuration - ensure all required paths exist

        Returns:
            True if valid, raises exception otherwise
        """
        errors = []

        if not cls.EVIDENCE_DIR.exists():
            errors.append(f"Evidence directory does not exist: {cls.EVIDENCE_DIR}")

        if not cls.SIGNING_KEY_DIR.exists():
            errors.append(f"Signing key directory does not exist: {cls.SIGNING_KEY_DIR}")

        if not cls.PRIVATE_KEY.exists():
            errors.append(f"Private key not found: {cls.PRIVATE_KEY}")

        if not cls.PUBLIC_KEY.exists():
            errors.append(f"Public key not found: {cls.PUBLIC_KEY}")

        if not cls.SCHEMA_PATH.exists():
            errors.append(f"Schema not found: {cls.SCHEMA_PATH}")

        if not cls.COSIGN_PASSWORD:
            errors.append("COSIGN_PASSWORD environment variable not set")

        if errors:
            raise ValueError("Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

        return True

    @classmethod
    def print_config(cls):
        """Print current configuration for debugging"""
        print("Evidence Pipeline Configuration:")
        print(f"  Evidence Directory: {cls.EVIDENCE_DIR}")
        print(f"  Signing Key Directory: {cls.SIGNING_KEY_DIR}")
        print(f"  Private Key: {cls.PRIVATE_KEY} (exists: {cls.PRIVATE_KEY.exists()})")
        print(f"  Public Key: {cls.PUBLIC_KEY} (exists: {cls.PUBLIC_KEY.exists()})")
        print(f"  Schema Path: {cls.SCHEMA_PATH} (exists: {cls.SCHEMA_PATH.exists()})")
        print(f"  Cosign Password: {'***' if cls.COSIGN_PASSWORD else '(not set)'}")
        print(f"  Default Client ID: {cls.DEFAULT_CLIENT_ID}")


if __name__ == "__main__":
    # Validate and print config when run directly
    try:
        EvidenceConfig.validate()
        print("✅ Configuration valid")
        print()
        EvidenceConfig.print_config()
    except ValueError as e:
        print(f"❌ Configuration invalid:\n{e}")
        exit(1)
