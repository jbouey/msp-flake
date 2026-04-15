"""Unit tests for identity_chain.

Pins the canonical event byte layout (changing it invalidates every
claim event ever recorded) and the chain_hash function.
"""

from __future__ import annotations

import hashlib
import json

import identity_chain as ch  # type: ignore[import-not-found]


def test_canonical_event_bytes_is_deterministic_and_compact():
    out = ch.canonical_event_bytes(
        claim_event_id=42,
        site_id="site-x",
        mac_address="aa:bb:cc:dd:ee:ff",
        agent_pubkey_hex="ABCD" * 16,
        iso_release_sha="deadbeef",
        claimed_at_iso="2026-04-15T03:45:23Z",
        source="claim",
    )
    # Should be sorted-key compact JSON.
    decoded = json.loads(out)
    assert decoded == {
        "agent_pubkey_hex": ("abcd" * 16),  # lowercased
        "claim_event_id": 42,
        "claimed_at": "2026-04-15T03:45:23Z",
        "iso_release_sha": "deadbeef",
        "mac_address": "AA:BB:CC:DD:EE:FF",  # uppercased
        "site_id": "site-x",
        "source": "claim",
    }
    # No whitespace.
    assert b": " not in out and b", " not in out


def test_canonical_event_bytes_handles_null_iso_release_sha():
    out = ch.canonical_event_bytes(
        claim_event_id=1,
        site_id="s",
        mac_address="AA:BB:CC:DD:EE:FF",
        agent_pubkey_hex="0" * 64,
        iso_release_sha=None,
        claimed_at_iso="2026-04-15T03:45:23Z",
        source="enrollment",
    )
    decoded = json.loads(out)
    assert decoded["iso_release_sha"] == ""


def test_canonical_event_bytes_byte_locked():
    """Pin the exact bytes. Drift here = invalidating every chain
    ever — test failure should be a stop-everything moment."""
    out = ch.canonical_event_bytes(
        claim_event_id=1,
        site_id="s",
        mac_address="AA:BB:CC:DD:EE:FF",
        agent_pubkey_hex="0" * 64,
        iso_release_sha="dead",
        claimed_at_iso="2026-04-15T03:45:23Z",
        source="claim",
    )
    expected = (
        b'{"agent_pubkey_hex":"' + b"0" * 64 + b'",'
        b'"claim_event_id":1,'
        b'"claimed_at":"2026-04-15T03:45:23Z",'
        b'"iso_release_sha":"dead",'
        b'"mac_address":"AA:BB:CC:DD:EE:FF",'
        b'"site_id":"s",'
        b'"source":"claim"}'
    )
    assert out == expected


def test_chain_hash_is_deterministic():
    prev = ch.GENESIS_HASH
    data = ch.canonical_event_bytes(
        claim_event_id=1, site_id="s", mac_address="AA:BB:CC:DD:EE:FF",
        agent_pubkey_hex="0" * 64, iso_release_sha="dead",
        claimed_at_iso="2026-04-15T03:45:23Z", source="claim",
    )
    h1 = ch.chain_hash(prev, data)
    h2 = ch.chain_hash(prev, data)
    assert h1 == h2
    assert len(h1) == 64
    # Recompute the same way independently.
    expected = hashlib.sha256(prev.encode("ascii") + b":" + data).hexdigest()
    assert h1 == expected


def test_chain_hash_changes_with_prev_hash():
    data = b"x"
    h_genesis = ch.chain_hash(ch.GENESIS_HASH, data)
    h_other = ch.chain_hash("a" * 64, data)
    assert h_genesis != h_other


def test_chain_hash_changes_with_event_data():
    h1 = ch.chain_hash(ch.GENESIS_HASH, b"event-1")
    h2 = ch.chain_hash(ch.GENESIS_HASH, b"event-2")
    assert h1 != h2


def test_chain_hash_uses_lowercase_hex():
    h = ch.chain_hash(ch.GENESIS_HASH, b"x")
    assert h == h.lower()


def test_genesis_is_64_zeros():
    assert ch.GENESIS_HASH == "0" * 64


def test_chain_hash_sequential_chain_visible():
    """Build a 3-event chain and confirm each link references the
    previous event correctly. This is what an auditor will do
    to verify integrity."""
    data1 = ch.canonical_event_bytes(
        claim_event_id=1, site_id="s", mac_address="AA:BB:CC:DD:EE:FF",
        agent_pubkey_hex="11" * 32, iso_release_sha="aaa",
        claimed_at_iso="2026-04-15T03:00:00Z", source="claim",
    )
    h1 = ch.chain_hash(ch.GENESIS_HASH, data1)

    data2 = ch.canonical_event_bytes(
        claim_event_id=2, site_id="s", mac_address="AA:BB:CC:DD:EE:FF",
        agent_pubkey_hex="22" * 32, iso_release_sha="bbb",
        claimed_at_iso="2026-04-15T04:00:00Z", source="rotation",
    )
    h2 = ch.chain_hash(h1, data2)

    data3 = ch.canonical_event_bytes(
        claim_event_id=3, site_id="s", mac_address="AA:BB:CC:DD:EE:FF",
        agent_pubkey_hex="33" * 32, iso_release_sha="ccc",
        claimed_at_iso="2026-04-15T05:00:00Z", source="rotation",
    )
    h3 = ch.chain_hash(h2, data3)

    # Every link is unique.
    assert len({ch.GENESIS_HASH, h1, h2, h3}) == 4
    # Each chain_hash was computed from the prior tip.
    assert ch.chain_hash(h2, data3) == h3
    # Tampering with data2 invalidates the rest.
    tampered_h2 = ch.chain_hash(h1, b"tampered-event-2")
    assert tampered_h2 != h2
    assert ch.chain_hash(tampered_h2, data3) != h3
