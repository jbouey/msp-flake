"""CI gate (Task #62 v2.1 Commit 1, Gate A P0-2 + P1-6): no code path
in the backend may INSERT a row into `compliance_bundles` carrying the
load-test synthetic marker (`details->>'synthetic'='load_test'`).

Gate A finding (audit/coach-62-load-harness-v1-gate-a-2026-05-16.md
§P0-2): synthetic writes to `compliance_bundles` corrupt the Ed25519
chain + OTS anchoring + auditor-kit determinism contract. The v1
design's `/evidence/upload` Wave-1 entry was dropped per this P0;
this CI gate is the structural backstop that prevents any future
re-introduction.

Auditor lens (P1-6) elevated this from v1 P2-2: kit hash pins to
compliance_bundles content; ANY load-test write that lands flips the
kit hash between consecutive downloads — visible as a
tamper-evidence violation.

This is a SOURCE-SHAPE gate (no DB). The substrate invariant
`load_test_marker_in_compliance_bundles` (sev1, ships in v2.1 Commit
5) is the runtime backstop. Both layers required per defense-in-depth.

Scan logic:
  1. Find every INSERT INTO compliance_bundles in the backend tree.
  2. For each, look for `'load_test'` literal or `synthetic=load_test`
     in a ±20-line window (the JSONB-build context).
  3. If found → gate fails loudly with the file:line.

Allowlist: backend code that LEGITIMATELY tests the gate (this file
itself + any other test_*.py that constructs the forbidden shape as
a negative-control fixture) is excluded.
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_INSERT_PATTERN = re.compile(
    r"INSERT\s+INTO\s+compliance_bundles\b", re.IGNORECASE
)
_LOAD_TEST_MARKER_PATTERN = re.compile(
    r"['\"]load_test['\"]|\bsynthetic\s*[:=]\s*['\"]?load_test", re.IGNORECASE
)


def _scan_for_load_test_writes() -> list[str]:
    """Return list of `file:line: snippet` for any INSERT INTO
    compliance_bundles within ±20 lines of a `'load_test'` literal."""
    findings: list[str] = []
    for py in _BACKEND.rglob("*.py"):
        if py.name.startswith("test_"):
            continue
        if "tests" in py.parts or "fixtures" in py.parts:
            continue
        if "migrations" in py.parts:
            continue
        try:
            src = py.read_text(encoding="utf-8")
        except OSError:
            continue
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if not _INSERT_PATTERN.search(line):
                continue
            window_start = max(0, i - 20)
            window_end = min(len(lines), i + 20)
            window = "\n".join(lines[window_start:window_end])
            if _LOAD_TEST_MARKER_PATTERN.search(window):
                rel = py.relative_to(_BACKEND)
                findings.append(
                    f"{rel}:{i + 1}: {line.strip()[:100]} "
                    f"(load_test marker found in ±20-line window)"
                )
    return findings


def test_no_load_test_marker_in_compliance_bundles_inserts():
    """No backend code may INSERT into compliance_bundles with the
    `'load_test'` synthetic marker — that would corrupt the Ed25519
    chain + auditor-kit determinism (Gate A P0-2 + P1-6, Counsel
    Rule 1 + Rule 9).

    Load harness Wave 1 covers volume-critical bearer endpoints that
    do NOT write to compliance_bundles. Evidence-submission throughput
    is a Wave 2 concern requiring its own `load_test_bundles` table
    design (separate Class-B Gate A).
    """
    findings = _scan_for_load_test_writes()
    assert not findings, (
        "Backend code is writing load-test-marked rows into "
        "compliance_bundles — this corrupts the Ed25519 chain + OTS "
        "anchoring + auditor-kit determinism contract (Gate A P0-2 + "
        "P1-6):\n"
        + "\n".join(f"  - {f}" for f in findings)
        + "\n\nIf load-volume testing of evidence-submission is needed, "
        "design a separate `load_test_bundles` table per the v2.1 spec "
        "(Wave 2 scope, separate Class-B Gate A required)."
    )


def test_load_test_marker_value_pinned_in_spec():
    """The load-test marker value `'load_test'` is the canonical
    enum-string per v2.1 spec (Gate A P0-3 marker unification with
    plan-24's `'mttr_soak'`). Pinning here ensures the marker can't
    silently drift to a different string that would bypass this
    gate's scan.
    """
    spec_path = pathlib.Path(__file__).resolve().parents[3] / ".agent" / "plans" / "40-load-testing-harness-design-v2.1-2026-05-16.md"
    if not spec_path.exists():
        # Spec not in repo yet (test ships before spec lands in some
        # CI ordering). Soft-skip.
        return
    spec = spec_path.read_text()
    assert "'load_test'" in spec or "\"load_test\"" in spec or "= 'load_test'" in spec, (
        "v2.1 spec doc no longer pins the load-test marker value "
        "`'load_test'`. If the marker value changed, update this gate's "
        "_LOAD_TEST_MARKER_PATTERN in lockstep."
    )
