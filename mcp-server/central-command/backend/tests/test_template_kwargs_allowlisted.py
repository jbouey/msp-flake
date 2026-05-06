"""CI gate: customer-facing template kwargs are security-allow-listed.

Maya P1 (round-table 2026-05-06): without this gate, a future PR can
add ``patient_email`` (or any §164.514 identifier) to a template's
``required_kwargs`` set without triggering security review. The
template registry already raises if a kwarg name is not on the
allow-list (``_KWARGS_SECURITY_ALLOWLIST`` in
``backend/templates/__init__.py``), but that's a runtime guard. This
test pins the allow-list at CI time so:

  1. Adding to the allow-list requires a deliberate diff that a
     reviewer must approve.
  2. Each registered template's ``required_kwargs`` is verified
     against the allow-list at test-collection time, BEFORE any
     deploy.
  3. The allow-list itself is bounded — anything PHI-shaped is
     forbidden by static-name match.

When the allow-list grows, update both this test and the source
allow-list in lockstep.
"""
from __future__ import annotations

import pathlib
import sys


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Import side-effects — registers all customer templates.
from templates import (
    _KWARGS_SECURITY_ALLOWLIST,
    _REGISTRY,
    list_registered_templates,
)


# Names that are FORBIDDEN regardless of allow-list state. Adding
# any of these to a template's required_kwargs is a §164.514 / PHI
# regression; this gate fails before the allow-list check even
# runs. Catches the failure mode where a reviewer mistakenly
# approves an allow-list expansion that shouldn't have been.
_PHI_FORBIDDEN_KWARGS = frozenset({
    "patient_email",
    "patient_id",
    "patient_name",
    "diagnosis",
    "treatment",
    "provider_npi",
    "provider_dea",
    "ssn",
    "dob",
    "mrn",
    "encounter_id",
})


def test_security_allowlist_does_not_contain_phi_kwargs():
    """The security allow-list itself MUST NOT contain any PHI-
    shaped name. Catches a bad allow-list expansion before runtime."""
    overlap = _KWARGS_SECURITY_ALLOWLIST & _PHI_FORBIDDEN_KWARGS
    assert not overlap, (
        f"Security allow-list contains PHI-forbidden names: "
        f"{sorted(overlap)}. Remove these from "
        f"_KWARGS_SECURITY_ALLOWLIST in backend/templates/__init__.py "
        f"— customer-facing templates must never receive PHI as a "
        f"render kwarg."
    )


def test_every_registered_template_uses_only_allowlisted_kwargs():
    """Every registered template's required_kwargs must be a subset
    of the security allow-list. The registry enforces this at
    register time; this test pins it at CI."""
    violations = []
    for name in list_registered_templates():
        reg = _REGISTRY[name]
        forbidden = reg.required_kwargs - _KWARGS_SECURITY_ALLOWLIST
        if forbidden:
            violations.append(
                f"{name}: required_kwargs include non-allowlisted "
                f"names: {sorted(forbidden)}"
            )
    assert not violations, (
        "Registered templates violate the kwargs allow-list:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def test_no_registered_template_uses_phi_kwarg_names():
    """Direct match against the PHI denylist — defense in depth
    if the allow-list expansion was incorrect."""
    violations = []
    for name in list_registered_templates():
        reg = _REGISTRY[name]
        bad = reg.required_kwargs & _PHI_FORBIDDEN_KWARGS
        if bad:
            violations.append(f"{name}: PHI-shaped kwarg names: {sorted(bad)}")
    assert not violations, (
        "PHI-shaped kwarg names in registered templates:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def test_allowlist_is_a_frozenset():
    """Mutability guard. A list/set could be mutated at runtime by
    a future contributor; frozenset prevents that. Forces additions
    to land via source diff."""
    assert isinstance(_KWARGS_SECURITY_ALLOWLIST, frozenset), (
        "_KWARGS_SECURITY_ALLOWLIST must be a frozenset to prevent "
        "runtime mutation."
    )
