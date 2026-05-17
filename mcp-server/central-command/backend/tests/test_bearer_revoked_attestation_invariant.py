"""#123 Sub-A — source-shape sentinels for the bearer_revoked_without_
attestation substrate invariant.

Pins:
  1. Assertion registered at sev1 (not sev2 — Counsel Rule 3 + §164.308
     (a)(4) workforce-access controls; under-severing would page below
     threshold).
  2. The invariant SQL filters synthetic sites OUT (carve-out for the
     load_test_api.py:415-449 path). Without this, load-test teardown
     would constantly trip the invariant.
  3. The invariant SQL filters soft-deleted appliances out (deleted_at
     IS NULL). Revoking a deleted appliance is moot.
  4. The invariant SQL pins check_type='privileged_access' and
     event_type='bulk_bearer_revoke' — matching the admin endpoint's
     attestation shape (Sub-B). Without this pin, ANY privileged_access
     bundle on the site would clear the violation = false negative.
  5. The invariant SQL pins the target_appliance_ids array containment
     — the bundle must list THIS appliance_id, not just SOME revocation
     on the site.
  6. Runbook exists at the expected path with sev1 framing.
  7. _DISPLAY_METADATA registered with operator-actionable
     recommended_action.

TIER-1 (no DB, no asyncpg, no pynacl) — runs in local pre-push sweep.
"""
from __future__ import annotations

import pathlib
import re


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_ASSERTIONS = _BACKEND / "assertions.py"
_RUNBOOK = (
    _BACKEND
    / "substrate_runbooks"
    / "bearer_revoked_without_attestation.md"
)


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


def test_invariant_registered_at_sev1():
    src = _read(_ASSERTIONS)
    m = re.search(
        r'Assertion\(\s*name="bearer_revoked_without_attestation"\s*,\s*'
        r'severity="(\w+)"',
        src,
    )
    assert m, (
        "bearer_revoked_without_attestation not registered in "
        "ALL_ASSERTIONS. #123 Sub-A invariant missing."
    )
    assert m.group(1) == "sev1", (
        f"severity={m.group(1)!r} — must be sev1. Bearer revocation is "
        f"a §164.308(a)(4) workforce-access action; an unattested "
        f"revocation is a chain-of-custody gap auditors flag. sev2 "
        f"falls below page-on-call threshold."
    )


def _body() -> str:
    src = _read(_ASSERTIONS)
    m = re.search(
        r"async def _check_bearer_revoked_without_attestation.*?"
        r"(?=\nasync def |\Z)",
        src,
        re.DOTALL,
    )
    assert m, "_check_bearer_revoked_without_attestation not found"
    return m.group(0)


def test_invariant_carves_out_synthetic_sites():
    body = _body()
    assert "COALESCE(s.synthetic, FALSE) = FALSE" in body, (
        "Invariant must filter sites.synthetic=TRUE OUT — the load-test "
        "path (load_test_api.py:415-449) legitimately revokes synthetic "
        "bearers without attestation."
    )


def test_invariant_filters_soft_deleted_appliances():
    body = _body()
    assert "sa.deleted_at IS NULL" in body, (
        "Invariant must filter sa.deleted_at IS NOT NULL out — "
        "revocation on a soft-deleted appliance is moot."
    )


def test_invariant_pins_attestation_event_type():
    body = _body()
    assert "'privileged_access'" in body, (
        "Invariant must filter compliance_bundles.check_type="
        "'privileged_access' — any other check_type would not be a "
        "bearer-revocation attestation."
    )
    assert "'bulk_bearer_revoke'" in body, (
        "Invariant must pin event_type='bulk_bearer_revoke' — without "
        "this, ANY privileged_access bundle on the site would clear "
        "the violation (false negative)."
    )


def test_invariant_pins_per_appliance_array_containment():
    body = _body()
    assert "target_appliance_ids" in body, (
        "Invariant must pin target_appliance_ids array containment — "
        "the attesting bundle must list THIS appliance_id, not just "
        "be a bulk_bearer_revoke for SOME other appliance at the site."
    )
    assert "sa.appliance_id::text" in body, (
        "Invariant must cast appliance_id::text for jsonb ? operator "
        "containment check (jsonb array elements are text)."
    )


def test_runbook_exists_with_sev1_framing():
    assert _RUNBOOK.exists(), f"runbook missing: {_RUNBOOK}"
    content = _read(_RUNBOOK)
    assert "Severity:** sev1" in content
    assert "§164.308(a)(4)" in content, (
        "Runbook must cite §164.308(a)(4) workforce-access — the "
        "regulatory basis for sev1 severity."
    )
    assert "synthetic = TRUE" in content or "synthetic=TRUE" in content, (
        "Runbook must document the synthetic-site carve-out so "
        "investigators don't chase load-test rows."
    )


def test_display_metadata_registered_with_actionable_recommendation():
    src = _read(_ASSERTIONS)
    m = re.search(
        r'"bearer_revoked_without_attestation"\s*:\s*\{(.*?)\n\s*\}',
        src,
        re.DOTALL,
    )
    assert m, (
        "bearer_revoked_without_attestation missing from "
        "_DISPLAY_METADATA — operator panel would show raw assertion "
        "name without context."
    )
    block = m.group(1)
    assert "recommended_action" in block, (
        "_DISPLAY_METADATA entry must include recommended_action so "
        "operator panel has a one-line next step."
    )
    assert "substrate_runbooks/bearer_revoked_without_attestation" in block, (
        "_DISPLAY_METADATA must link the runbook path so operators "
        "can drill from panel → runbook in one click."
    )
