"""CI gates for #116 Sub-A — Vault key-version `known_good` admin
approval surface.

Per audit/coach-116-vault-admin-approval-gate-a-2026-05-17.md.

Pins the intentional ALLOWED_EVENTS-only asymmetry:
  - `vault_key_version_approved` event is in `ALLOWED_EVENTS`
  - NOT in `fleet_cli.PRIVILEGED_ORDER_TYPES` (admin-API class,
    not a fleet_order with daemon consumer)
  - NOT in mig 305+ `v_privileged_types` trigger array (no
    site_id anchor — Vault keys are fleet-global; mig 175's
    enforce_privileged_order_attestation can't gate them)

This asymmetry is the canonical pattern when an event has no
appliance target AND no natural site anchor. The lockstep checker
permits ALLOWED_EVENTS ⊇ {PRIVILEGED_ORDER_TYPES, v_privileged_
types}; the reverse is not required.

Sister tests:
  - tests/test_privileged_order_four_list_lockstep.py (cross-list
    drift checker)
  - tests/test_no_compliance_bundles_appliance_id_writes.py (#122
    deprecation example of the same asymmetry pattern)
"""
from __future__ import annotations

import pathlib
import re

import pytest

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_ATTESTATION = _BACKEND / "privileged_access_attestation.py"
_FLEET_CLI = _BACKEND / "fleet_cli.py"
_ASSERTIONS = _BACKEND / "assertions.py"
_MIG_328 = _BACKEND / "migrations" / "328_vault_key_version_attestation_binding.sql"
_RUNBOOK = (
    _BACKEND / "substrate_runbooks"
    / "vault_key_version_approved_without_attestation.md"
)
_MIGRATIONS_DIR = _BACKEND / "migrations"


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


def _extract_python_set(src: str, var_name: str) -> set[str]:
    """Extract a `VAR = {...}` Python set literal (mirrors the
    sibling lockstep test's extractor)."""
    m = re.search(
        rf"{var_name}\s*=\s*\{{(.*?)\n\}}",
        src, re.DOTALL,
    )
    assert m, f"could not locate {var_name} set literal"
    items: set[str] = set()
    for line in m.group(1).splitlines():
        line = line.split("#", 1)[0].strip()
        for token in re.findall(r'"([^"]+)"', line):
            items.add(token)
    return items


# ── ALLOWED_EVENTS membership ────────────────────────────────────


def test_vault_key_version_approved_is_in_allowed_events():
    """The new event must be registered in ALLOWED_EVENTS for the
    attestation chain to accept it."""
    src = _read(_ATTESTATION)
    events = _extract_python_set(src, "ALLOWED_EVENTS")
    assert "vault_key_version_approved" in events, (
        "vault_key_version_approved must be in ALLOWED_EVENTS. Per "
        "Gate A Option B: admin-API class events register here so "
        "the attestation chain accepts them."
    )


# ── Asymmetry: NOT in fleet_cli.PRIVILEGED_ORDER_TYPES ───────────


def test_vault_key_version_approved_NOT_in_privileged_order_types():
    """vault_key_version_approved is an admin-API class event (no
    daemon consumer, no fleet_order issuance). Adding it to
    PRIVILEGED_ORDER_TYPES would create dead fleet_orders. Per
    Gate A Option B + RT21 feature-flag precedent."""
    src = _read(_FLEET_CLI)
    types = _extract_python_set(src, "PRIVILEGED_ORDER_TYPES")
    assert "vault_key_version_approved" not in types, (
        "vault_key_version_approved must NOT be in fleet_cli."
        "PRIVILEGED_ORDER_TYPES. Per Gate A: this is an admin-API "
        "event (UPDATE on vault_signing_key_versions), NOT a fleet_"
        "order with daemon consumer. Adding it would create dead "
        "fleet_orders + force a fake site_id to satisfy mig 175."
    )


# ── Asymmetry: NOT in any mig v_privileged_types ─────────────────


def test_vault_key_version_approved_NOT_in_v_privileged_types():
    """Walk every migration that CREATE OR REPLACE FUNCTION
    enforce_privileged_order_attestation — none may include
    'vault_key_version_approved' in v_privileged_types ARRAY."""
    offenders: list[str] = []
    for mig in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        body = _read(mig)
        if "CREATE OR REPLACE FUNCTION enforce_privileged_order_attestation" not in body:
            continue
        # Find the v_privileged_types ARRAY literal in this file.
        m = re.search(
            r"v_privileged_types\s+TEXT\[\]\s*:=\s*ARRAY\s*\[([^\]]+)\]",
            body, re.DOTALL,
        )
        if not m:
            continue
        if "'vault_key_version_approved'" in m.group(1):
            offenders.append(mig.name)
    assert not offenders, (
        "vault_key_version_approved must NOT appear in any mig's "
        "v_privileged_types ARRAY. Per Gate A Option B: no site_id "
        "anchor exists for Vault keys; mig 175's enforce_privileged_"
        "order_attestation trigger requires site_id and can't gate. "
        f"Offending migrations: {offenders}"
    )


# ── Mig 328 schema-level binding ─────────────────────────────────


def test_mig_328_exists_and_adds_attestation_bundle_id_column():
    assert _MIG_328.exists(), f"mig 328 missing: {_MIG_328}"
    body = _read(_MIG_328)
    assert "ADD COLUMN IF NOT EXISTS attestation_bundle_id TEXT" in body
    assert "vault_signing_key_versions_known_good_ck" in body, (
        "mig 328 must DROP + re-ADD the named CHECK constraint from "
        "mig 311 to extend it with attestation_bundle_id IS NOT NULL"
    )
    # The extended CHECK must include the new requirement.
    assert "attestation_bundle_id IS NOT NULL" in body, (
        "mig 328 CHECK must require attestation_bundle_id when "
        "known_good=TRUE (Gate A P0-1)"
    )


def test_mig_328_extended_check_keeps_approved_by_and_approved_at():
    """The Sub-C.1-lesson class: when extending an existing CHECK,
    never weaken the prior conditions. Mig 311's CHECK required
    approved_by AND approved_at; mig 328 must keep both AND add
    attestation_bundle_id."""
    body = _read(_MIG_328)
    # Find the ADD CONSTRAINT block
    m = re.search(
        r"ADD CONSTRAINT vault_signing_key_versions_known_good_ck CHECK\s*\((.*?)\);",
        body, re.DOTALL,
    )
    assert m, "extended CHECK not found in mig 328"
    check_body = m.group(1)
    assert "approved_by IS NOT NULL" in check_body
    assert "approved_at IS NOT NULL" in check_body
    assert "attestation_bundle_id IS NOT NULL" in check_body
    assert "NOT known_good OR" in check_body, (
        "CHECK must be `NOT known_good OR (... AND ... AND ...)` so "
        "the CHECK passes for unapproved rows and enforces the "
        "trio only when known_good=TRUE"
    )


# ── Substrate invariant + runbook ────────────────────────────────


def test_substrate_invariant_registered_at_sev1():
    src = _read(_ASSERTIONS)
    m = re.search(
        r'Assertion\(\s*name="vault_key_version_approved_without_attestation"\s*,\s*'
        r'severity="(\w+)"',
        src,
    )
    assert m, (
        "vault_key_version_approved_without_attestation not registered "
        "in ALL_ASSERTIONS"
    )
    assert m.group(1) == "sev1", (
        f"severity={m.group(1)!r} — must be sev1. The Vault key is "
        f"the trust root for the entire fleet's signing pathway; "
        f"an unattested approval propagates silent trust to every "
        f"downstream Ed25519 signature. sev2 would fall below "
        f"page-on-call threshold."
    )


def test_substrate_invariant_checks_synthetic_anchor_shape():
    """Per Gate A P0-3: the attestation's site_id MUST be
    'vault:<key_name>:v<key_version>'. The invariant must include
    this shape check."""
    src = _read(_ASSERTIONS)
    m = re.search(
        r"async def _check_vault_key_version_approved_without_attestation.*?"
        r"(?=\nasync def |\Z)",
        src, re.DOTALL,
    )
    assert m
    body = m.group(0)
    assert "'vault:'" in body and "':v'" in body, (
        "Invariant must verify the synthetic anchor shape 'vault:"
        "<key_name>:v<key_version>' (Gate A P0-3 anchor namespace)."
    )
    assert "'privileged_access'" in body, (
        "Invariant must verify cb.check_type='privileged_access' "
        "(not just any bundle)."
    )


def test_runbook_exists_and_documents_quarantine():
    assert _RUNBOOK.exists(), f"runbook missing: {_RUNBOOK}"
    content = _read(_RUNBOOK)
    assert "Severity:** sev1" in content
    assert "quarantine" in content.lower()
    assert "known_good = FALSE" in content or "known_good=FALSE" in content
    assert "mig 328" in content


# ── Schema fixture parity ────────────────────────────────────────


def test_prod_columns_fixture_includes_attestation_bundle_id():
    """Per CLAUDE.md schema-fixture-blind rule: prod_columns.json
    must include the new column or future column-presence tests
    will false-positive."""
    fixture = (
        _BACKEND / "tests" / "fixtures" / "schema" / "prod_columns.json"
    )
    body = _read(fixture)
    # Find the vault_signing_key_versions block.
    m = re.search(
        r'"vault_signing_key_versions":\s*\[(.*?)\]',
        body, re.DOTALL,
    )
    assert m
    block = m.group(1)
    assert '"attestation_bundle_id"' in block, (
        "prod_columns.json must include vault_signing_key_versions."
        "attestation_bundle_id (added by mig 328 — #116 Sub-A)"
    )
