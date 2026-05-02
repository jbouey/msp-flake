"""CI gate: auditor kit ZIP must auto-include all SECURITY_ADVISORY_*.md.

Followup #41 closure 2026-05-02. Closes the disclosure-first commitment
violation Camila flagged in the AI-independence audit (#52). Pre-fix,
only the 2026-04-09 Merkle disclosure was hardcoded into chain.json.
Newer advisories (2026-05-02 PACKET_GAP) sat in docs/security/ and
weren't in the kit. An auditor downloading the kit had no way to know
they existed without grepping the repo.

Post-fix: download_auditor_kit walks docs/security/SECURITY_ADVISORY_*.md
at request time, includes the parsed metadata in chain.json[disclosures]
AND ships the full markdown under disclosures/<filename> in the ZIP.

This gate is source-level (regex on evidence_chain.py); a runtime
test would require building a live FastAPI app + DB fixture, which is
TIER-2/3 territory.
"""
from __future__ import annotations

import pathlib
import re

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND.parent.parent.parent
_EVIDENCE_CHAIN = _BACKEND / "evidence_chain.py"
_ADVISORIES_DIR = _REPO_ROOT / "docs" / "security"


def _read_evidence_chain() -> str:
    return _EVIDENCE_CHAIN.read_text()


def test_helper_function_exists():
    """The _collect_security_advisories helper must exist."""
    src = _read_evidence_chain()
    assert "def _collect_security_advisories" in src, (
        "evidence_chain.py is missing _collect_security_advisories. The "
        "auditor kit relies on this helper to walk docs/security/ at "
        "request time."
    )


def test_metadata_parser_exists():
    src = _read_evidence_chain()
    assert "def _parse_advisory_metadata" in src, (
        "evidence_chain.py is missing _parse_advisory_metadata. Required "
        "by _collect_security_advisories for parsing advisory headers."
    )


def test_chain_json_uses_dynamic_disclosure_list():
    """chain.json's disclosures field must be built from
    _collect_security_advisories(), not a hardcoded list. The pre-fix
    pattern was a hardcoded list with the Merkle entry only — banned."""
    src = _read_evidence_chain()
    # Find the disclosures: [...] in chain_metadata. Should have the
    # _collect_security_advisories() call inside.
    # Match a window from `"disclosures": [` forward 10 lines.
    m = re.search(r'"disclosures":\s*\[\s*\n(.*?)\n\s*\]', src, re.DOTALL)
    assert m, (
        "Could not find `\"disclosures\": [...]` block in evidence_chain.py."
    )
    disclosures_block = m.group(1)
    assert "_collect_security_advisories()" in disclosures_block, (
        "The disclosures list in chain.json must iterate "
        "_collect_security_advisories() — not be a hardcoded list. "
        "Hardcoding misses newer advisories."
    )


def test_zip_writes_disclosure_files():
    """download_auditor_kit must write each advisory's markdown under
    disclosures/<filename> in the ZIP. Otherwise the chain.json metadata
    points at filenames not present in the kit."""
    src = _read_evidence_chain()
    assert 'zf.writestr(f"disclosures/' in src or "zf.writestr(f'disclosures/" in src, (
        "download_auditor_kit doesn't write to disclosures/ in the ZIP. "
        "The chain.json metadata references disclosures/<filename> — "
        "those files MUST exist in the kit or the auditor kit ships "
        "broken references."
    )


def test_at_least_one_advisory_present_in_repo():
    """Sanity: docs/security/ must have at least one SECURITY_ADVISORY_
    file, otherwise the gate is vacuously passing. If this fails, either
    the directory was renamed/deleted (regression) or someone is running
    the test in a tmp dir without docs/."""
    if not _ADVISORIES_DIR.exists():
        pytest.skip(f"docs/security/ not present at {_ADVISORIES_DIR}")
    advisories = list(_ADVISORIES_DIR.glob("SECURITY_ADVISORY_*.md"))
    assert len(advisories) >= 2, (
        f"Expected ≥2 SECURITY_ADVISORY_*.md files in docs/security/; "
        f"found {len(advisories)}. If this drops below 2 the auto-include "
        f"path is vacuously passing — verify docs/ wasn't accidentally "
        f"truncated."
    )


def test_advisory_files_have_parseable_id():
    """Every advisory must have a parseable `# Security Advisory — <ID>`
    header so the kit metadata is non-null."""
    if not _ADVISORIES_DIR.exists():
        pytest.skip(f"docs/security/ not present at {_ADVISORIES_DIR}")
    pattern = re.compile(r"#\s*Security Advisory\s*[—-]\s*(\S+)", re.IGNORECASE)
    failures = []
    for path in sorted(_ADVISORIES_DIR.glob("SECURITY_ADVISORY_*.md")):
        text = path.read_text()
        if not pattern.search(text):
            failures.append(path.name)
    assert not failures, (
        f"Advisory files missing parseable `# Security Advisory — <ID>` "
        f"header (kit metadata will have id=null for these): {failures}. "
        f"Restore the H1 header — see SECURITY_ADVISORY_2026-04-09_MERKLE.md "
        f"for the canonical format."
    )
