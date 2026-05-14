"""CI regression gate: customer-facing BAA artifacts must not conflate
"heartbeat" with verification/signing language (Task #70, 2026-05-14).

Background — Gate B FU-4 feared the master BAA might have implied that
heartbeat-signature *verification* was active while it was inert in
prod for 13 days (2026-04-30 -> 2026-05-13, fixed in adb7671a). Task #70
Gate A (audit/coach-baa-d1-soak-gate-gate-a-2026-05-14.md) read
MASTER_BAA_v1.0_INTERIM.md in full and found the fear does NOT
materialize: every "cryptographically signed" claim in the BAA and in
the customer-facing artifacts scopes to EVIDENCE BUNDLES
(`compliance_bundles`) — which were Ed25519-signed + OTS-anchored
continuously, including throughout the inert window. The word
"heartbeat" appears nowhere in those artifacts.

This gate is the BACKSTOP, not a fix — there is nothing to fix today.
It PINS the currently-correct scoping so a future copy edit cannot
silently re-introduce the conflation FU-4 feared: a v2.0-hardening
draft (or any template change) that puts "heartbeat" next to
"verified"/"signed"/"cryptographic" in a customer-facing artifact
fails CI immediately.

If a heartbeat-verification claim is ever LEGITIMATELY warranted, it
must clear the D1 soak bar in docs/legal/v2.0-hardening-prerequisites.md
(PRE-1) first — and only then is the co-occurrence intentional, at
which point add a documented carve-out here.

Ratchet: BASELINE_MAX = 0. The scoped artifacts have zero "heartbeat"
mentions today, so zero co-occurrences. Any new one fails CI.
"""
from __future__ import annotations

import pathlib
import re

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
_BACKEND = _REPO_ROOT / "mcp-server" / "central-command" / "backend"

# Customer-facing artifact scope (Task #70 Gate A enumeration).
_TEMPLATE_DIRS = [
    _BACKEND / "templates" / "attestation_letter",
    _BACKEND / "templates" / "wall_cert",
    _BACKEND / "templates" / "quarterly_summary",
    _BACKEND / "templates" / "auditor_kit",
]
_LEGAL_GLOB_ROOT = _REPO_ROOT / "docs" / "legal"

# File suffixes that carry customer-facing copy.
_COPY_SUFFIXES = (".j2", ".html", ".md", ".sh", ".txt")

# How many whitespace-delimited tokens count as "co-occurring".
_WINDOW_TOKENS = 12

_HEARTBEAT_RE = re.compile(r"heartbeat", re.IGNORECASE)
# "verif" covers verify/verified/verification; "signed"/"signature";
# "cryptograph" covers cryptographic/cryptographically.
_VERIFY_RE = re.compile(r"verif|signed|signature|cryptograph", re.IGNORECASE)

BASELINE_MAX = 0


def _scoped_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for d in _TEMPLATE_DIRS:
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if p.is_file() and p.suffix in _COPY_SUFFIXES:
                files.append(p)
    for p in sorted(_LEGAL_GLOB_ROOT.glob("MASTER_BAA*.md")):
        files.append(p)
    return files


def _cooccurrences(text: str) -> list[str]:
    """Return a snippet for every 'heartbeat' that has a verification
    word within _WINDOW_TOKENS tokens on either side."""
    tokens = text.split()
    hits: list[str] = []
    for i, tok in enumerate(tokens):
        if not _HEARTBEAT_RE.search(tok):
            continue
        lo = max(0, i - _WINDOW_TOKENS)
        hi = min(len(tokens), i + _WINDOW_TOKENS + 1)
        window = tokens[lo:hi]
        if any(_VERIFY_RE.search(w) for w in window):
            hits.append(" ".join(window))
    return hits


def test_scope_is_nonempty():
    """Guard against a path refactor silently emptying the scan set."""
    files = _scoped_files()
    assert files, (
        "test_baa_artifacts_no_heartbeat_verification_overclaim found no "
        "files to scan — the template/legal paths drifted. Fix the scope "
        "constants."
    )
    # The MASTER_BAA must always be in scope — it is the load-bearing
    # artifact this gate exists to protect.
    assert any(f.name.startswith("MASTER_BAA") for f in files), (
        "MASTER_BAA*.md is not in the scan scope — check _LEGAL_GLOB_ROOT."
    )


def test_no_heartbeat_verification_overclaim():
    """No customer-facing BAA artifact may place 'heartbeat' within
    ~12 tokens of verification/signing language. Pins the currently-
    correct scoping (every signed-claim scopes to evidence bundles, not
    heartbeats) against future copy regression — see Task #70 Gate A.
    """
    violations: list[str] = []
    for path in _scoped_files():
        try:
            text = path.read_text()
        except OSError:
            continue
        for snippet in _cooccurrences(text):
            rel = path.relative_to(_REPO_ROOT)
            violations.append(f"  {rel}: ...{snippet}...")
    assert len(violations) <= BASELINE_MAX, (
        f"{len(violations)} heartbeat/verification co-occurrence(s) > "
        f"baseline {BASELINE_MAX} in customer-facing BAA artifacts "
        f"(Task #70 over-claim regression class).\n"
        f"A customer-facing artifact now conflates 'heartbeat' with "
        f"verification/signing language. If the claim is intended, it "
        f"MUST first clear the D1 soak bar in "
        f"docs/legal/v2.0-hardening-prerequisites.md (PRE-1).\n"
        + "\n".join(violations)
    )


def test_baseline_is_zero():
    """Pin the ratchet at 0 — this is a true zero-baseline backstop."""
    assert BASELINE_MAX == 0, (
        "BASELINE_MAX must stay 0 — a heartbeat-verification claim was "
        "accepted into a customer-facing artifact. That requires the "
        "D1 soak bar (v2.0-hardening-prerequisites.md PRE-1), not a "
        "baseline bump."
    )
