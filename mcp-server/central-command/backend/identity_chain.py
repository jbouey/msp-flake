"""identity_chain — Week 3 of the composed identity stack.

Provides hash-chain extension that joins compliance_bundles ↔
provisioning_claim_events into a single per-site cryptographic
chain. A customer or auditor verifying their evidence chain
automatically also verifies their identity-binding history.

Chain shape:

  GENESIS = "0" * 64

  For each new claim event E:
      prev_hash  = latest chain_hash for site across BOTH tables
                   (or GENESIS if none)
      event_data = canonical_event_bytes(E)
      chain_hash = sha256(prev_hash + ":" + event_data).hexdigest()

The canonical_event_bytes layout is FROZEN: changing it invalidates
every claim event ever recorded. Tests pin the byte format.

This module is pure (no DB imports). It defines:
  * canonical_event_bytes(...)  — byte layout for the per-event hash
  * chain_hash(prev, event_data) — sha256 helper
  * GENESIS_HASH constant

The DB-touching wrapper that resolves prev_hash lives in iso_ca.py
so test surface stays small.
"""

from __future__ import annotations

import hashlib
import json

GENESIS_HASH = "0" * 64
CHAIN_SEPARATOR = b":"


def canonical_event_bytes(
    *,
    claim_event_id: int,
    site_id: str,
    mac_address: str,
    agent_pubkey_hex: str,
    iso_release_sha: str | None,
    claimed_at_iso: str,
    source: str,
) -> bytes:
    """Canonical bytes that get hashed to produce the per-event leaf.

    Sort_keys + compact separators MUST match Python's
    json.dumps(..., sort_keys=True, separators=(',',':')) and Go's
    json.Marshal of an equivalently-keyed map (which sorts keys
    alphabetically). Identical layout to iso_ca_helpers.canonical_*.
    """
    payload = {
        "claim_event_id": claim_event_id,
        "site_id": site_id,
        "mac_address": mac_address.upper(),
        "agent_pubkey_hex": agent_pubkey_hex.lower(),
        "iso_release_sha": iso_release_sha or "",
        "claimed_at": claimed_at_iso,
        "source": source,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def chain_hash(prev_hash: str, event_data: bytes) -> str:
    """sha256(prev_hash_hex_bytes + ":" + event_data) → lowercase hex.

    prev_hash is taken AS HEX (not raw bytes) — same convention the
    compliance_bundles chain uses. That makes prev/curr comparable
    by string equality without decode steps.
    """
    h = hashlib.sha256()
    h.update(prev_hash.encode("ascii"))
    h.update(CHAIN_SEPARATOR)
    h.update(event_data)
    return h.hexdigest()
