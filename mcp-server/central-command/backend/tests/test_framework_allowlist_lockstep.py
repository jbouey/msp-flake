"""CI gate — single-source SUPPORTED_FRAMEWORKS lockstep.

#41 Phase A P0-4 closure per audit/coach-41-soc2-glba-question-
banks-gate-a-2026-05-17.md.

Pre-fix: `valid_frameworks = {"hipaa", "soc2", "pci_dss", "nist_csf",
"cis"}` literal set was duplicated at frameworks.py:389 + 483, plus
framework_templates.py used `if framework == "x"` branches + a
silent HIPAA fallback. This drift class meant a framework could
appear in one allowlist but be missing from another → silent
data-leak or silent-zero-score.

Post-fix:
  - compliance_frameworks.SUPPORTED_FRAMEWORKS is the canonical
    frozenset
  - frameworks.py both call sites bind to that import (no literal)
  - framework_templates.py's branches MUST cover every member of
    SUPPORTED_FRAMEWORKS (gate enforces)
  - silent HIPAA fallback replaced by UnsupportedFrameworkError

CI gate enforces:
  1. SUPPORTED_FRAMEWORKS is a frozenset defined exactly once
  2. NO `valid_frameworks = {...}` literal in frameworks.py
  3. framework_templates.get_assessment_questions has a branch
     for EVERY framework in SUPPORTED_FRAMEWORKS
  4. framework_templates.get_policy_templates has a branch for
     EVERY framework in SUPPORTED_FRAMEWORKS
  5. UnsupportedFrameworkError exists + framework_templates raises
     it (no silent HIPAA fallback)
"""
from __future__ import annotations

import pathlib
import re

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
# Single-source allowlist lives in the zero-dep framework_allowlist.py
# module per Gate A P0-4 (extracted so callers don't drag SQLAlchemy
# through compliance_frameworks.py just to read the constant).
_ALLOW = _BACKEND / "framework_allowlist.py"
_CF = _BACKEND / "compliance_frameworks.py"
_F = _BACKEND / "frameworks.py"
_FT = _BACKEND / "framework_templates.py"


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


def _supported_set_from_source() -> set[str]:
    """Parse SUPPORTED_FRAMEWORKS literal from framework_allowlist.py
    without importing (avoids any dep chain locally)."""
    src = _read(_ALLOW)
    m = re.search(
        r"SUPPORTED_FRAMEWORKS\s*:\s*frozenset\[str\]\s*=\s*frozenset\(\s*\{([^}]+)\}\s*\)",
        src, re.DOTALL,
    )
    assert m, (
        "SUPPORTED_FRAMEWORKS frozenset literal not found in "
        "framework_allowlist.py (Gate A P0-4 binding)."
    )
    return {tok for tok in re.findall(r'"([^"]+)"', m.group(1))}


def test_supported_frameworks_is_frozenset_in_framework_allowlist():
    assert _ALLOW.exists(), (
        "framework_allowlist.py must exist as the zero-dep single-"
        "source module (Gate A P0-4)."
    )
    src = _read(_ALLOW)
    assert "SUPPORTED_FRAMEWORKS: frozenset[str] = frozenset(" in src, (
        "framework_allowlist.py must define SUPPORTED_FRAMEWORKS as "
        "a typed frozenset literal."
    )
    supported = _supported_set_from_source()
    assert "hipaa" in supported, "hipaa must remain in SUPPORTED_FRAMEWORKS"
    assert "glba" in supported, (
        "glba must be in SUPPORTED_FRAMEWORKS post-#41 (Gate A P0-2)"
    )


def test_compliance_frameworks_re_exports_from_allowlist():
    """compliance_frameworks.py must re-export — NOT redefine —
    SUPPORTED_FRAMEWORKS, so both modules surface the same constant
    without dependency-graph dragging."""
    src = _read(_CF)
    assert "from .framework_allowlist import SUPPORTED_FRAMEWORKS" in src or \
           "from framework_allowlist import SUPPORTED_FRAMEWORKS" in src, (
        "compliance_frameworks.py must re-export SUPPORTED_FRAMEWORKS "
        "from framework_allowlist (relative-then-absolute fallback)."
    )
    # And it must NOT redefine the literal (that would be drift).
    assert "SUPPORTED_FRAMEWORKS = frozenset({" not in src, (
        "compliance_frameworks.py must NOT redefine SUPPORTED_"
        "FRAMEWORKS as a literal — it re-exports from "
        "framework_allowlist."
    )


def test_frameworks_py_has_no_literal_valid_frameworks_set():
    """frameworks.py used to define `valid_frameworks = {...}` literal
    at 2 sites. Post-#41 it MUST bind to SUPPORTED_FRAMEWORKS instead."""
    src = _read(_F)
    # Match the antipattern shape: `valid_frameworks = {"...", "..."}`
    # (curly-brace literal). Allow `valid_frameworks = SUPPORTED_FRAMEWORKS`.
    bad = re.findall(
        r"valid_frameworks\s*=\s*\{[^}]+\}",
        src,
    )
    assert not bad, (
        f"frameworks.py contains {len(bad)} literal-set definition(s) of "
        f"valid_frameworks. Bind to compliance_frameworks."
        f"SUPPORTED_FRAMEWORKS instead (Gate A P0-2 + P0-4 lockstep).\n"
        f"Offenders: {bad}"
    )
    assert "valid_frameworks = SUPPORTED_FRAMEWORKS" in src, (
        "frameworks.py must rebind `valid_frameworks = "
        "SUPPORTED_FRAMEWORKS` at the configured callsites."
    )


def test_framework_templates_has_branch_for_every_supported():
    """get_assessment_questions + get_policy_templates must include
    an `if framework == "X"` branch for EVERY entry in SUPPORTED_
    FRAMEWORKS. Gate against "added to allowlist but forgot branch"."""
    src = _read(_FT)
    supported = _supported_set_from_source()
    for fw in supported:
        # The branch in framework_templates uses `framework == "X"` shape.
        assert f'framework == "{fw}"' in src, (
            f"framework_templates.py missing branch for "
            f"`framework == \"{fw}\"`. Per Gate A: every SUPPORTED_"
            f"FRAMEWORKS member needs a question-bank + policy-template "
            f"branch."
        )


def test_framework_templates_raises_on_unknown_no_silent_fallback():
    """Gate A P0-1: silent HIPAA fallback REMOVED. Unknown framework
    must raise UnsupportedFrameworkError."""
    src = _read(_FT)
    assert "class UnsupportedFrameworkError" in src, (
        "framework_templates.py must define UnsupportedFrameworkError "
        "(subclass of ValueError) for the no-silent-fallback contract."
    )
    # The functions must raise the error for non-allowlisted frameworks.
    assert "raise UnsupportedFrameworkError" in src
    # Sanity: zero un-guarded "else: ... from .hipaa_templates"
    # patterns (the old fallback shape).
    assert "else:\n        # Fallback to HIPAA" not in src
    assert "else:\n        try:\n            from .hipaa_templates" not in src


def test_no_silent_fallback_in_branches():
    """Per Gate A P0-1: an unguarded `else: from .hipaa_templates`
    shape was the bug. Sentinel: NO `else:` block inside the
    function that pulls HIPAA templates — that's the silent-fallback
    shape. The legitimate HIPAA branch (inside `if framework ==
    "hipaa":`) uses both relative + absolute imports (try/except
    fallback per CLAUDE.md import-shape rule); that's 2 imports in
    ONE branch, not a fallback class."""
    src = _read(_FT)
    fn_match = re.search(
        r"def get_assessment_questions.*?(?=\ndef |\Z)",
        src, re.DOTALL,
    )
    assert fn_match
    fn = fn_match.group(0)
    # Search for the specific silent-fallback shape: `else:` followed
    # within a few lines by a hipaa_templates import. (The legitimate
    # `if framework == "hipaa":` branch has the imports — but NOT
    # under an `else:` since each framework gets its own `if`.)
    silent_fallback = re.search(
        r"\n\s+else:\s*\n[^\n]*\n?[^\n]*from\s+\.?hipaa_templates",
        fn,
    )
    assert silent_fallback is None, (
        "get_assessment_questions has an `else: ... from "
        "hipaa_templates import` block — the silent HIPAA fallback "
        "regression class. Replace with `raise "
        "UnsupportedFrameworkError(...)`."
    )


def test_compliance_frameworks_docstring_lists_only_end_to_end_bound():
    """Gate A P0-3 (Counsel Rule 5 — no stale doc authority): the
    module docstring must NOT advertise frameworks that lack end-to-
    end binding. Pre-fix it claimed 10 frameworks; reality was 1."""
    src = _read(_CF)
    # The new docstring must use the explicit "Currently end-to-end-
    # bound frameworks" framing OR mark non-bound as scaffolding.
    assert "end-to-end-bound" in src, (
        "compliance_frameworks.py docstring must explicitly mark the "
        "wired-vs-scaffolding distinction (Counsel Rule 5)."
    )
    # And it must reference the SUPPORTED_FRAMEWORKS constant.
    assert "SUPPORTED_FRAMEWORKS" in src
