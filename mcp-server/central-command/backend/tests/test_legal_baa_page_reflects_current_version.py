"""CI gate (Task #100, follow-up to #56): the customer-facing
/legal/baa page in Legal.tsx must reflect the CURRENT master BAA
version, not the stale pre-v1.0-INTERIM 6-bullet "best practice"
summary.

Counsel Rule 5 (no stale document outranks the current posture
overlay). When the v1.0-INTERIM master BAA shipped 2026-05-13, the
old Legal.tsx baa entry framing — "as a matter of best practice"
— became actively misleading: it implied OsirisCare's BAA was
non-binding/optional. The new v1.0-INTERIM IS the binding HIPAA-
core compliance instrument; the Legal page must say so.

This gate prevents drift: if anyone ever rewrites the baa entry
without preserving the version pin + effective-date pin, CI fails
loudly.

Decay path: when v2.0 supersedes v1.0-INTERIM, BUMP the literals
in this test in the same commit that bumps Legal.tsx. The lockstep
is the safety property.
"""
from __future__ import annotations

import pathlib
import re


_FRONTEND = (
    pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
)
_LEGAL_TSX = _FRONTEND / "Legal.tsx"


def _baa_entry_text() -> str:
    """Extract the `baa:` entry block from Legal.tsx."""
    src = _LEGAL_TSX.read_text()
    # Match `baa: { ... content: [ ... ] ... }` — tolerate whitespace.
    m = re.search(
        r"baa:\s*\{[^}]*?title:\s*'([^']*)'[^}]*?content:\s*\[(.*?)\]\s*,\s*\}",
        src,
        re.DOTALL,
    )
    assert m, (
        "Could not extract baa entry from Legal.tsx. "
        "Has the LEGAL_CONTENT shape changed? Update this test."
    )
    return m.group(2)


def test_legal_baa_page_pins_v1_0_interim_version():
    """The /legal/baa page must reference the current BAA version
    pin. When v2.0 supersedes, bump BOTH the page AND this test
    in lockstep."""
    body = _baa_entry_text()
    assert "v1.0-INTERIM" in body, (
        "/legal/baa page no longer references the v1.0-INTERIM "
        "version. Either v2.0 has shipped (bump this test pin in "
        "the same commit) OR the page has silently regressed to "
        "stale framing. Master BAA at docs/legal/MASTER_BAA_v1.0_"
        "INTERIM.md is the source of truth."
    )


def test_legal_baa_page_pins_effective_date():
    """The /legal/baa page must reference the v1.0-INTERIM effective
    date (May 13, 2026). Without an effective-date anchor, the page
    can drift further from contract reality each time the BAA
    contract updates."""
    body = _baa_entry_text()
    assert "May 13, 2026" in body, (
        "/legal/baa page no longer references the v1.0-INTERIM "
        "effective date (May 13, 2026). Either v2.0 has shipped "
        "(bump this test pin) OR the date anchor has been stripped "
        "(restore it)."
    )


def test_legal_baa_page_does_not_use_stale_best_practice_framing():
    """Counsel Rule 5: the pre-v1.0-INTERIM framing — that BAAs were
    executed 'as a matter of best practice' — was active stale-doc
    drift after 2026-05-13 because it implied the BAA is optional/
    non-binding. Forbid the phrase."""
    body = _baa_entry_text()
    assert "best practice" not in body.lower(), (
        "/legal/baa page contains the stale 'best practice' framing "
        "that pre-dates v1.0-INTERIM. The current BAA is a HIPAA-"
        "core compliance instrument, NOT an optional best practice. "
        "Counsel Rule 5: no stale-doc framing may outrank the "
        "current posture overlay."
    )


def test_legal_baa_page_references_signup_signing_flow():
    """The page must direct users to where they can view/sign the
    BAA (signup flow OR contact email). Without an actionable next-
    step, the page is decorative; the v1.0-INTERIM rollout assumes
    the page hands off to the e-signature flow."""
    body = _baa_entry_text().lower()
    assert "signup flow" in body or "administrator@osiriscare.net" in body, (
        "/legal/baa page no longer tells users how to view or sign "
        "the master BAA. Restore the signup-flow OR contact-email "
        "handoff (Task #100)."
    )
