"""Ed25519 order signing for admin and fleet orders.

All orders sent to appliances must be signed so the daemon can verify
they originated from Central Command and haven't been tampered with.
"""

import json
import secrets
from datetime import datetime, timezone


def sign_admin_order(
    order_id: str,
    order_type: str,
    parameters: dict,
    created_at: datetime,
    expires_at: datetime,
    target_appliance_id: str = "",
) -> tuple[str, str, str]:
    """Sign an admin order and return (nonce, signature, signed_payload).

    The signed_payload is canonical JSON (sort_keys=True) matching
    the format the Go daemon will reconstruct for verification.

    target_appliance_id binds this order to a specific appliance —
    the daemon will reject orders whose target doesn't match its own ID.
    """
    from main import sign_data  # Lazy import to avoid circular dependency

    nonce = secrets.token_hex(16)

    payload_dict = {
        "order_id": order_id,
        "order_type": order_type,
        "parameters": parameters,
        "nonce": nonce,
        "created_at": created_at.isoformat() if isinstance(created_at, datetime) else str(created_at),
        "expires_at": expires_at.isoformat() if isinstance(expires_at, datetime) else str(expires_at),
    }

    if target_appliance_id:
        payload_dict["target_appliance_id"] = target_appliance_id

    signed_payload = json.dumps(payload_dict, sort_keys=True)
    signature = sign_data(signed_payload)

    return nonce, signature, signed_payload


def sign_fleet_order(
    order_id: int,
    order_type: str,
    parameters: dict,
    created_at: datetime,
    expires_at: datetime,
) -> tuple[str, str, str]:
    """Sign a fleet order. Same mechanism as admin orders.

    Fleet orders are inherently fleet-wide — no target_appliance_id
    is included, so they can be delivered to any appliance.
    """
    from main import sign_data

    nonce = secrets.token_hex(16)

    payload_dict = {
        "order_id": str(order_id),
        "order_type": order_type,
        "parameters": parameters,
        "nonce": nonce,
        "created_at": created_at.isoformat() if isinstance(created_at, datetime) else str(created_at),
        "expires_at": expires_at.isoformat() if isinstance(expires_at, datetime) else str(expires_at),
    }

    signed_payload = json.dumps(payload_dict, sort_keys=True)
    signature = sign_data(signed_payload)

    return nonce, signature, signed_payload
