"""Shared provision-code primitives.

Extracted from partners.py per Task #119 Gate A P1-1: importing
`partners.generate_provision_code` from `fleet_cli` would drag
FastAPI / routing / SQLAlchemy into the CLI startup path. Shared
modules with no framework dependencies are the right home.

Single producer: this module.
Two consumers: `partners.py` (HTTP endpoint) + `fleet_cli.py`
(operator CLI). Both call the same factory so the codes have
identical shape + entropy.
"""
from __future__ import annotations

import re
import secrets


# 16-char hex string (8 bytes). Matches the existing prod-shipped
# format (partners.py:188) — DO NOT change without coordinating
# with QR-decode logic on the appliance side + auditor kit references.
# appliance_provisions.provision_code is varchar(32); 16 chars fits
# with 16 chars of headroom for any future format extension.
def generate_provision_code() -> str:
    """Generate a fresh provision code (16-char uppercase hex).

    Each call uses cryptographically-strong entropy via secrets;
    collision probability is astronomically low (2^-64).

    Not idempotent: re-running with the same operator inputs yields
    DIFFERENT codes. Callers that want idempotency must check for
    existing rows themselves.
    """
    return secrets.token_hex(8).upper()


# Conservative site_id shape: lowercase ASCII letters, digits, hyphens.
# Mirrors the convention used across existing site_id literals
# ('synthetic-mttr-soak', 'load-test-chain-contention-site',
# customer site_ids). Length capped via the column width (varchar(50)
# per prod_column_widths.json).
_SITE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")


def is_valid_site_id(s: str) -> bool:
    """True if `s` is shape-valid as an `appliance_provisions.
    target_site_id` value. Used by fleet_cli's CSV pre-flight
    validation (Task #119 Gate A Carol P0-3 binding)."""
    return bool(_SITE_ID_RE.match(s))
