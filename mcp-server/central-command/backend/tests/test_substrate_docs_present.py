"""CI gate: every assertions.ALL_ASSERTIONS entry has a
backend/substrate_runbooks/<name>.md file with the required section
headings. Fails the build on drift.

Scope:
  1. Every registered invariant MUST have a runbook doc.
  2. Every runbook doc MUST carry the seven template sections (so a
     deep-link page and the runbook drawer always find the prose they
     expect).
  3. Every runbook doc MUST match a registered invariant — orphaned
     docs fail so stale runbooks don't mislead the operator.

Drift this test catches:
  - Added an Assertion, forgot to generate/populate the stub.
  - Renamed an Assertion, orphaned the old doc.
  - Deleted an Assertion, left a zombie doc.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

# Add backend directory to sys.path so backend modules are importable.
_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from assertions import ALL_ASSERTIONS

# Runbook stubs live under backend/substrate_runbooks/ (mirrors the
# runtime path in substrate_action_api._DOCS_DIR). Keeping them inside
# the backend package ensures the deploy rsync picks them up.
DOCS_DIR = _BACKEND / "substrate_runbooks"

REQUIRED_SECTIONS = [
    "## What this means",
    "## Root cause categories",
    "## Immediate action",
    "## Verification",
    "## Escalation",
    "## Related runbooks",
    "## Change log",
]


@pytest.mark.parametrize(
    "assertion", ALL_ASSERTIONS, ids=lambda a: a.name,
)
def test_doc_exists_and_has_sections(assertion):
    """Every invariant has a matching runbook doc with all required sections."""
    path = DOCS_DIR / f"{assertion.name}.md"
    assert path.exists(), (
        f"Missing runbook doc: {path}. "
        "Run `python3 scripts/generate_substrate_doc_stubs.py` from backend/ "
        "then fill in the prose sections."
    )
    body = path.read_text()
    for section in REQUIRED_SECTIONS:
        assert section in body, (
            f"Runbook {path} missing required section: {section!r}. "
            "See backend/substrate_runbooks/_TEMPLATE.md for the canonical structure."
        )


def test_no_orphaned_docs():
    """Every runbook doc matches a registered invariant (no zombie docs)."""
    known = {a.name for a in ALL_ASSERTIONS}
    # _TEMPLATE.md is the generator seed, not a runbook. Keep it.
    known.add("_TEMPLATE")
    orphans = []
    for md_file in sorted(DOCS_DIR.glob("*.md")):
        stem = md_file.stem
        if stem not in known:
            orphans.append(str(md_file))
    assert not orphans, (
        "Orphan runbook file(s) found — no matching entry in "
        "ALL_ASSERTIONS:\n  " + "\n  ".join(orphans)
        + "\nEither remove the file or add the invariant to assertions.py."
    )
