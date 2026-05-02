"""CI gate: cryptographic compliance chain components MUST stay LLM-free.

AI-independence audit dim 8 closure (2026-05-02). Camila's claim
verified by audit dim 4: the chain (Ed25519 + hash-chain + OTS +
Merkle + BAA + auditor kit + compliance scores + privileged-access
chain + audit logs) has ZERO LLM imports today.

This gate pins that property as a ratchet. Any future PR that adds
an LLM dependency to a chain-component file fails CI. HIPAA §164.312(b)
integrity controls cannot derive state from unverified AI output.

If a chain component LEGITIMATELY needs to import LLM-related code
(extremely unlikely; would require round-table approval), add the
file to _CHAIN_COMPONENT_LLM_TOUCH_EXEMPT below with one-line reason.

Verdict pinned today: 0 chain components touch LLM. Sev0 count: 0.
"""
from __future__ import annotations

import pathlib
import re

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent

# The 10 chain components per audit dim 4. File paths are relative to
# _BACKEND. Add new chain-class files here when shipping new
# cryptographic primitives.
_CHAIN_COMPONENT_FILES = [
    "evidence_chain.py",
    "signing_backend.py",
    "order_signing.py",
    "compliance_packet.py",
    "framework_mapper.py",
    "privileged_access_attestation.py",
    "client_signup.py",
    "audit_package.py",
    "audit_report.py",
]

# Migration files that ship cryptographic-chain SQL functions
# (e.g. mig 271 calculate_compliance_score). Same LLM-free rule applies
# to plpgsql function bodies.
_CHAIN_COMPONENT_MIGRATIONS = [
    "migrations/271_evidence_framework_mappings_per_control_status.sql",
    "migrations/151_audit_evidence_immutability_triggers.sql",
    "migrations/175_privileged_access_chain_trigger.sql",
    "migrations/138_partition_compliance_bundles.sql",
    "migrations/141_compliance_packets_table.sql",
    "migrations/148_merkle_batch_id_uniqueness_backfill.sql",
    "migrations/256_canonical_site_id_function.sql",
]

# Forbidden patterns in chain-component code. Each is a strong
# indicator of LLM dependency. Hits on any of these = sev0.
_LLM_PATTERNS = [
    re.compile(r"\bimport\s+(?:anthropic|openai|litellm|claude_sdk)\b"),
    re.compile(r"\bfrom\s+(?:anthropic|openai|litellm|claude_sdk)\s+import\b"),
    re.compile(r"\bmessages\.create\b"),
    re.compile(r"\bchat\.completions\.create\b"),
    re.compile(r"api\.anthropic\.com|api\.openai\.com"),
    re.compile(r"model\s*=\s*['\"](?:claude-|gpt-)"),
    re.compile(r"\bANTHROPIC_API_KEY\b"),
    re.compile(r"\bOPENAI_API_KEY\b"),
]

# If a chain-component file LEGITIMATELY needs LLM (extremely unlikely),
# add it here with one-line reason. Empty today.
_CHAIN_COMPONENT_LLM_TOUCH_EXEMPT: dict[str, str] = {}


def _scan_file(path: pathlib.Path) -> list[tuple[int, str, str]]:
    """Return [(line_no, matched_text, pattern_repr), ...] for any
    LLM-pattern hits in the file."""
    if not path.exists():
        return []
    hits: list[tuple[int, str, str]] = []
    for line_no, line in enumerate(path.read_text().splitlines(), start=1):
        # Skip comments — LLM-pattern in a comment is documentation,
        # not behavior. Bash-style # and Python # only; SQL -- handled
        # by stripping comment-only lines.
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped.startswith("--"):
            continue
        for pat in _LLM_PATTERNS:
            m = pat.search(line)
            if m:
                hits.append((line_no, m.group(0), pat.pattern))
    return hits


@pytest.mark.parametrize("relpath", _CHAIN_COMPONENT_FILES + _CHAIN_COMPONENT_MIGRATIONS)
def test_chain_component_is_llm_free(relpath: str):
    """Each chain-component file MUST have zero LLM-pattern hits."""
    path = _BACKEND / relpath
    if not path.exists():
        pytest.skip(f"Chain component file not present: {relpath}")
    if relpath in _CHAIN_COMPONENT_LLM_TOUCH_EXEMPT:
        pytest.skip(
            f"Exempt: {relpath} — {_CHAIN_COMPONENT_LLM_TOUCH_EXEMPT[relpath]}"
        )
    hits = _scan_file(path)
    assert not hits, (
        f"Sev0 violation — {relpath} has LLM-pattern hits:\n"
        + "\n".join(f"  line {ln}: {text!r}  (pattern: {pat})"
                    for ln, text, pat in hits)
        + f"\n\nThe cryptographic compliance chain MUST stay LLM-free per "
        f"HIPAA §164.312(b). If this LLM use is intentional and verified "
        f"to NOT touch chain-relevant computation, add {relpath} to "
        f"_CHAIN_COMPONENT_LLM_TOUCH_EXEMPT in this test with a one-line "
        f"reason. Round-table review required before exempting."
    )


def test_chain_component_files_exist():
    """Sanity: every file in _CHAIN_COMPONENT_FILES must exist (catches
    typos + deleted files). Migration files use skip if not present
    (acceptable — old migrations may be archived)."""
    missing = []
    for relpath in _CHAIN_COMPONENT_FILES:
        if not (_BACKEND / relpath).exists():
            missing.append(relpath)
    assert not missing, (
        f"_CHAIN_COMPONENT_FILES references non-existent files: {missing}. "
        f"Either fix the typo or remove the entry."
    )


def test_chain_component_list_is_documented():
    """Sanity: the list must have a reasonable count (deletion-by-typo
    guard)."""
    assert len(_CHAIN_COMPONENT_FILES) >= 8, (
        f"_CHAIN_COMPONENT_FILES is suspiciously short ({len(_CHAIN_COMPONENT_FILES)}). "
        f"Expected ≥8 chain-component files. Did someone delete entries "
        f"by accident?"
    )
