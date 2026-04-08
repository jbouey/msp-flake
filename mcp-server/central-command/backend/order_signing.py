"""Ed25519 order signing for admin and fleet orders.

All orders sent to appliances must be signed so the daemon can verify
they originated from Central Command and haven't been tampered with.

IMPORTANT: Signatures MUST be hex-encoded (128 chars for Ed25519).
The Go daemon calls hex.DecodeString() on the signature — base64 will fail
with "invalid byte" errors. Python's nacl sign().signature.hex() is correct.
"""

import json
import re
import secrets
from datetime import datetime, timezone


_HEX_PATTERN = re.compile(r"^[0-9a-f]{128}$")


def _validate_signature_hex(signature: str) -> str:
    """Validate that a signature is 128-char lowercase hex (Ed25519).

    The Go daemon expects hex — base64 causes 'invalid byte' errors.
    This catches misconfiguration early on the Python side.
    """
    if not _HEX_PATTERN.match(signature):
        raise ValueError(
            f"Signature must be 128 lowercase hex chars (Ed25519). "
            f"Got {len(signature)} chars. "
            f"Ensure sign_data() returns .signature.hex(), not base64."
        )
    return signature


def _sign_order(
    order_id: str,
    order_type: str,
    parameters: dict,
    created_at: datetime,
    expires_at: datetime,
    target_appliance_id: str = "",
) -> tuple[str, str, str]:
    """Core signing logic. Returns (nonce, signature, signed_payload).

    The signed_payload is canonical JSON (sort_keys=True) matching
    the format the Go daemon will reconstruct for verification.
    """
    from main import sign_data  # Lazy import to avoid circular dependency

    nonce = secrets.token_hex(16)

    payload_dict = {
        "order_id": str(order_id),
        "order_type": order_type,
        "parameters": parameters,
        "nonce": nonce,
        "created_at": created_at.isoformat() if isinstance(created_at, datetime) else str(created_at),
        "expires_at": expires_at.isoformat() if isinstance(expires_at, datetime) else str(expires_at),
    }

    if target_appliance_id:
        payload_dict["target_appliance_id"] = target_appliance_id

    signed_payload = json.dumps(payload_dict, sort_keys=True)
    signature = _validate_signature_hex(sign_data(signed_payload))

    return nonce, signature, signed_payload


def sign_admin_order(
    order_id: str,
    order_type: str,
    parameters: dict,
    created_at: datetime,
    expires_at: datetime,
    target_appliance_id: str = "",
) -> tuple[str, str, str]:
    """Sign an admin order. target_appliance_id binds to a specific appliance."""
    return _sign_order(order_id, order_type, parameters, created_at, expires_at, target_appliance_id)


def sign_fleet_order(
    order_id: int,
    order_type: str,
    parameters: dict,
    created_at: datetime,
    expires_at: datetime,
) -> tuple[str, str, str]:
    """Sign a fleet order. Fleet-wide — no target_appliance_id."""
    return _sign_order(str(order_id), order_type, parameters, created_at, expires_at)
