"""Credential envelope encryption using NaCl Box (X25519 + XSalsa20-Poly1305).

When an appliance provides its X25519 public key in the checkin request,
credentials are encrypted with an ephemeral NaCl box so they're protected
even on the Docker internal network (Caddy -> mcp-server is unencrypted HTTP).

The appliance holds the corresponding private key and can decrypt.
"""

import json
from nacl.public import PrivateKey, PublicKey, Box
from nacl.utils import random as nacl_random


def encrypt_credentials(
    appliance_public_key_hex: str,
    windows_targets: list,
    linux_targets: list,
) -> dict:
    """Encrypt credentials for a specific appliance.

    Returns a dict with ephemeral_public_key, nonce, and ciphertext (all hex).
    """
    # Parse appliance X25519 public key
    appliance_pub_bytes = bytes.fromhex(appliance_public_key_hex)
    if len(appliance_pub_bytes) != 32:
        raise ValueError(f"Invalid X25519 public key length: {len(appliance_pub_bytes)}")
    appliance_pub = PublicKey(appliance_pub_bytes)

    # Generate ephemeral X25519 keypair (forward secrecy)
    ephemeral_priv = PrivateKey.generate()
    ephemeral_pub = ephemeral_priv.public_key

    # Create NaCl Box
    the_box = Box(ephemeral_priv, appliance_pub)

    # Encrypt credential payload
    payload = json.dumps({
        "windows_targets": windows_targets,
        "linux_targets": linux_targets,
    }).encode()

    nonce = nacl_random(Box.NONCE_SIZE)
    ciphertext = the_box.encrypt(payload, nonce)
    # Box.encrypt prepends the nonce; strip the nonce prefix since we pass it separately
    actual_ciphertext = ciphertext[Box.NONCE_SIZE:]

    return {
        "ephemeral_public_key": ephemeral_pub.encode().hex(),
        "nonce": nonce.hex(),
        "ciphertext": actual_ciphertext.hex(),
    }
