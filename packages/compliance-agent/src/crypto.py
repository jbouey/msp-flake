"""
Cryptographic Signature Verification - Ed25519

This module provides Ed25519 signature verification for MCP orders.

Security:
- Uses Ed25519 (Curve25519) for fast, secure signatures
- Public key verification only (agent never signs)
- Constant-time operations to prevent timing attacks
- Explicit key validation

Guardrail #1: Order auth - All orders must have valid Ed25519 signature
from MCP server's known public key.

Usage:
    verifier = SignatureVerifier(public_key_hex="abc123...")

    message = json.dumps(order['payload'], sort_keys=True).encode()
    signature = bytes.fromhex(order['signature'])

    if verifier.verify(message, signature):
        # Order is authentic
        pass
"""

import logging
from typing import Optional
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

logger = logging.getLogger(__name__)


class SignatureVerifier:
    """
    Ed25519 signature verifier for MCP orders

    Guardrail #1: Ensures all orders are cryptographically signed by MCP server
    """

    def __init__(self, public_key_hex: str):
        """
        Initialize verifier with MCP server's public key

        Args:
            public_key_hex: Ed25519 public key in hex format (64 hex chars = 32 bytes)

        Raises:
            ValueError: If public key is invalid format
        """
        self.public_key_hex = public_key_hex

        try:
            # Convert hex string to bytes
            public_key_bytes = bytes.fromhex(public_key_hex)

            # Validate length (Ed25519 public keys are always 32 bytes)
            if len(public_key_bytes) != 32:
                raise ValueError(f"Ed25519 public key must be 32 bytes, got {len(public_key_bytes)}")

            # Load public key
            self.public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)

            logger.info(f"✓ Ed25519 public key loaded: {public_key_hex[:16]}...")

        except ValueError as e:
            logger.error(f"Invalid public key format: {e}")
            raise ValueError(f"Failed to load Ed25519 public key: {e}")

    def verify(self, message: bytes, signature: bytes) -> bool:
        """
        Verify Ed25519 signature on message

        Args:
            message: Raw message bytes (typically JSON payload)
            signature: Ed25519 signature bytes (64 bytes)

        Returns:
            True if signature is valid, False otherwise
        """
        # Validate signature length
        if len(signature) != 64:
            logger.warning(f"Invalid signature length: {len(signature)} (expected 64)")
            return False

        try:
            # Verify signature (raises InvalidSignature if verification fails)
            self.public_key.verify(signature, message)

            logger.debug(f"✓ Signature verified for message: {message[:50]}...")
            return True

        except InvalidSignature:
            logger.warning(f"✗ Signature verification failed for message: {message[:50]}...")
            return False

        except Exception as e:
            logger.error(f"Unexpected error during signature verification: {e}")
            return False

    def health_check(self):
        """
        Verify verifier is operational

        Raises:
            Exception if verifier is not healthy
        """
        # Test with known message/signature pair
        test_message = b"health_check_test_message"

        # We can't actually verify without a valid signature from the MCP server,
        # but we can verify the public key is loaded
        if self.public_key is None:
            raise Exception("Public key not loaded")

        logger.debug("✓ Signature verifier healthy")

    def __repr__(self) -> str:
        return f"SignatureVerifier(public_key={self.public_key_hex[:16]}...)"


class SignatureError(Exception):
    """Base exception for signature-related errors"""
    pass


class InvalidSignatureError(SignatureError):
    """Raised when signature verification fails"""
    pass


class InvalidKeyError(SignatureError):
    """Raised when public key is invalid or malformed"""
    pass


def verify_order_signature(order: dict, public_key_hex: str) -> bool:
    """
    Convenience function to verify order signature

    Args:
        order: Order dictionary with 'payload' and 'signature' keys
        public_key_hex: MCP server's public key in hex format

    Returns:
        True if signature is valid, False otherwise

    Example:
        order = {
            'id': 'ord_123',
            'timestamp': 1698765432,
            'payload': {'action': 'restart_service', 'service': 'nginx'},
            'signature': 'abc123...'  # 128 hex chars (64 bytes)
        }

        if verify_order_signature(order, mcp_public_key):
            execute_order(order)
    """
    try:
        # Extract payload and signature
        payload = order.get('payload')
        signature_hex = order.get('signature')

        if not payload or not signature_hex:
            logger.warning("Order missing payload or signature")
            return False

        # Import json here to avoid circular dependency
        import json

        # Canonicalize payload (sort keys for consistent serialization)
        message = json.dumps(payload, sort_keys=True).encode('utf-8')

        # Convert signature to bytes
        signature = bytes.fromhex(signature_hex)

        # Verify
        verifier = SignatureVerifier(public_key_hex)
        return verifier.verify(message, signature)

    except Exception as e:
        logger.error(f"Error verifying order signature: {e}")
        return False


def generate_keypair() -> tuple[str, str]:
    """
    Generate new Ed25519 keypair (for testing/setup only)

    Returns:
        Tuple of (private_key_hex, public_key_hex)

    Note:
        This should ONLY be used for initial setup or testing.
        In production, keys should be generated securely and stored in Vault/SOPS.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    # Generate private key
    private_key = Ed25519PrivateKey.generate()

    # Extract public key
    public_key = private_key.public_key()

    # Convert to hex
    private_key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption()
    )

    public_key_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )

    private_key_hex = private_key_bytes.hex()
    public_key_hex = public_key_bytes.hex()

    logger.warning("⚠️  Generated new keypair - store private key securely!")
    logger.info(f"Public key: {public_key_hex}")

    return (private_key_hex, public_key_hex)


# Example usage
if __name__ == '__main__':
    import json

    logging.basicConfig(level=logging.DEBUG)

    # Example: Generate keypair (testing only)
    print("Generating test keypair...")
    from cryptography.hazmat.primitives import serialization
    private_hex, public_hex = generate_keypair()
    print(f"Public key: {public_hex}")
    print(f"Private key: {private_hex} (keep secret!)")

    # Example: Verify signature
    print("\nTesting signature verification...")

    # Create test order
    test_order = {
        'id': 'ord_test_001',
        'timestamp': 1698765432,
        'payload': {
            'action': 'restart_service',
            'service': 'nginx'
        },
        'signature': '0' * 128  # Dummy signature (will fail)
    }

    # Try to verify (will fail with dummy signature)
    result = verify_order_signature(test_order, public_hex)
    print(f"Verification result: {result}")

    print("\n✓ crypto.py module ready")
