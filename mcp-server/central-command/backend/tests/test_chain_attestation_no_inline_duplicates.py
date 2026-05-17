"""CI gate: chain-attestation primitives are SHARED — no inline duplicates.

Round-table 32 (2026-05-05) Maya P0 anti-regression. Pre-fix, 5 modules
each carried near-identical implementations of the chain-gap escalation
+ Ed25519 attestation pattern. The DRY closure extracted them to
`chain_attestation.py`. This gate fails if a future commit reintroduces
inline duplicates — i.e. calls `create_privileged_access_attestation`
or composes the chain-gap pattern (`P0-CHAIN-GAP` + `[ATTESTATION-MISSING]`)
outside of `chain_attestation.py` itself.

Allowlisted callsites (legacy operator-class endpoints that pre-date
the helper extraction; not blocking — flagged for future migration):
  - partners.py operator-class POST /{partner_id}/users + magic-link
    (calls create_privileged_access_attestation directly inline; OK
    because the round-table-32 plan explicitly deferred touching
    these working operator-class paths in this commit).
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_CHAIN = _BACKEND / "chain_attestation.py"

# Modules that legitimately call create_privileged_access_attestation
# directly. These are GRANDFATHERED legacy sites — the gate's purpose
# is to prevent NEW inline calls (round-table 32 mandate). Migrating
# the legacy sites is a separate task with its own round-table because
# each carries operator-class context (admin auth, fleet_cli paths,
# break-glass) that the canonical helper doesn't yet model.
#
# DO NOT add new entries to this allowlist without round-table approval.
# When migrating a grandfathered module, REMOVE its entry here.
ALLOWED_DIRECT_CALL_MODULES = {
    "chain_attestation.py",       # the canonical helper IS the call site
    "privileged_access_attestation.py",  # defines the function
    # — Grandfathered legacy sites (round-table 32 deferred migration) —
    "partners.py",                # operator-class POST /{partner_id}/users
                                  # + magic-link; admin-class auth, predates
                                  # helper extraction.
    "partner_admin_transfer.py",  # has additional inline call site
                                  # at line 704 (specialized state-machine
                                  # transition); the helper SHIM at the
                                  # top of the module DOES delegate.
    "client_portal.py",           # additional inline call sites for
                                  # legacy privileged_access_api integration
    "client_owner_transfer.py",   # additional inline call site at 1157
                                  # (state-machine transition); shim at
                                  # top delegates.
    "org_management.py",          # org-provision attestation; admin-class.
    "appliance_relocation_api.py",  # operator-class.
    "privileged_access_api.py",   # the privileged_access API itself —
                                  # root of the chain, calls the
                                  # underlying create directly.
    "fleet_cli.py",               # operator-class fleet ops.
    "breakglass_api.py",          # break-glass; admin-class.
    "client_privacy_officer.py",  # F2 round-table 2026-05-06 — Privacy
                                  # Officer designation. Specialized
                                  # state-machine transition (designate +
                                  # revoke) where the chain-anchored
                                  # attestation IS the cryptographic
                                  # evidence the Letter (F1) embeds.
                                  # Same posture as client_owner_transfer
                                  # (org-state class) — direct call is
                                  # the right shape; the helper would
                                  # add an indirection that obscures
                                  # the evidence-chain link.
    "partner_ba_compliance.py",   # P-F6 round-table 2026-05-08 —
                                  # partner BAA roster (mig 290).
                                  # Add + revoke transitions write
                                  # chain attestations directly (anchor
                                  # at partner_org:<partner_id>).
                                  # Same posture as
                                  # client_privacy_officer.py.
    "vault_key_approval_api.py",  # Task #116 Sub-B 2026-05-17 —
                                  # vault key-version known_good
                                  # admin approval. Specialized
                                  # state-machine transition (single
                                  # one-way flip pending → known_good)
                                  # where the chain-anchored
                                  # attestation IS the cryptographic
                                  # evidence mig 328 CHECK requires.
                                  # Same posture as client_privacy_
                                  # officer + partner_ba_compliance —
                                  # admin-class, ALLOWED_EVENTS-only,
                                  # synthetic anchor (vault:<key_name>:
                                  # v<key_version>).
}

# Modules permitted to compose the chain-gap pattern inline (P0-CHAIN-GAP +
# [ATTESTATION-MISSING] strings). Same grandfathering posture: gate
# prevents NEW inline pattern instances.
ALLOWED_CHAIN_GAP_LITERAL_MODULES = {
    "chain_attestation.py",  # canonical implementation
    # Legacy hooks composed the pattern inline before extraction. New
    # callers should use chain_attestation.send_chain_aware_operator_alert.
    "client_portal.py",
    "client_owner_transfer.py",
    "partner_admin_transfer.py",
    "partners.py",
    "org_management.py",
    "breakglass_api.py",
}


def _gather_files() -> list[pathlib.Path]:
    files = []
    for py in _BACKEND.rglob("*.py"):
        rel = py.relative_to(_BACKEND)
        if rel.parts[0] in {"tests", "venv", ".venv", "__pycache__"}:
            continue
        files.append(py)
    return files


def test_chain_gap_escalation_pattern_centralized():
    """The chain-gap escalation literal — P0-CHAIN-GAP +
    [ATTESTATION-MISSING] — should appear in chain_attestation.py
    (canonical) and ONLY in modules grandfathered on the
    ALLOWED_CHAIN_GAP_LITERAL_MODULES list. Any NEW file outside that
    list with both literals = inline reimplementation = regression."""
    bad = []
    for py in _gather_files():
        if py.name in ALLOWED_CHAIN_GAP_LITERAL_MODULES:
            continue
        try:
            txt = py.read_text()
        except Exception:
            continue
        if "P0-CHAIN-GAP" in txt and "[ATTESTATION-MISSING]" in txt:
            bad.append(str(py.relative_to(_BACKEND)))
    assert not bad, (
        "Files contain both `P0-CHAIN-GAP` AND `[ATTESTATION-MISSING]` "
        "literals — chain-gap escalation pattern reimplemented inline "
        "in a non-grandfathered file. Round-table 32 closed this DRY "
        "gap; the rule's canonical home is chain_attestation."
        "send_chain_aware_operator_alert. Either delegate via that "
        "helper, OR (with round-table approval) add the file to "
        "ALLOWED_CHAIN_GAP_LITERAL_MODULES.\n\n"
        + "\n".join(f"  - {f}" for f in bad)
    )


def test_create_privileged_access_attestation_callers_allowlisted():
    """Direct calls to `create_privileged_access_attestation` outside
    the canonical helper are allowed only on the explicit allowlist
    (operator-class paths that pre-date the helper)."""
    bad = []
    for py in _gather_files():
        if py.name in ALLOWED_DIRECT_CALL_MODULES:
            continue
        try:
            txt = py.read_text()
        except Exception:
            continue
        # Match a call site, not just an import (the import line is OK).
        # Skip lines that are pure imports.
        for line_num, line in enumerate(txt.splitlines(), 1):
            stripped = line.strip()
            if (
                "create_privileged_access_attestation(" in line
                and not stripped.startswith("from ")
                and not stripped.startswith("import ")
            ):
                bad.append(
                    f"{py.relative_to(_BACKEND)}:{line_num} — {stripped[:120]}"
                )
                break  # one finding per file is enough
    assert not bad, (
        "Modules call create_privileged_access_attestation directly "
        "outside the chain_attestation.py canonical helper. Round-table "
        "32 closed this DRY gap. Either delegate via "
        "chain_attestation.emit_privileged_attestation, OR add the file "
        "to ALLOWED_DIRECT_CALL_MODULES with a justifying comment "
        "(e.g. operator-class endpoint pre-dating extraction).\n\n"
        + "\n".join(f"  - {b}" for b in bad)
    )


def test_chain_attestation_module_exports_canonical_api():
    """The canonical helper's public API must remain stable. Pinning
    the function names so a refactor that removes one breaks loudly."""
    src = _CHAIN.read_text()
    for fn in [
        "async def resolve_client_anchor_site_id",
        "async def emit_privileged_attestation",
        "def send_chain_aware_operator_alert",
    ]:
        assert fn in src, (
            f"chain_attestation.py is missing canonical helper `{fn}`. "
            f"Round-table 32 contract requires all three to remain "
            f"exported."
        )
    # The (failed, bundle_id) tuple-return contract
    assert "tuple[bool, Optional[str]]" in src or "Tuple[bool, Optional[str]]" in src, (
        "emit_privileged_attestation must return (failed, bundle_id) "
        "tuple per round-table 32 — caller relies on `failed` flag for "
        "chain-gap escalation, NOT on `bundle_id is None` inference."
    )
