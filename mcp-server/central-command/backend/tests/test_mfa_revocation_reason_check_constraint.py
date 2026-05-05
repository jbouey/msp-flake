"""CI gate: chk_mfa_revocation_reason_length stays at LENGTH(reason) >= 40.

Maya P1-4 (round-table 2026-05-05) on commit 069a8da3: the existing
`test_migration_276_reason_min_40` source-grep is satisfied by the
literal `LENGTH(reason) >= 40` appearing anywhere in mig 276 — a future
migration could silently DROP CONSTRAINT chk_mfa_revocation_reason_length
or ALTER it to a lower threshold and that test would still pass.

Steve P3 mit B explicitly chose ≥40 (not ≥20) for revoke reason as a
*friction* gate: the highest-risk privileged action requires elaborated
business context for forensic reconstruction. Lowering it would erode
that friction without re-running the round-table.

This source-level gate scans EVERY migration for:
  1. The original CREATE statement in mig 276 (positive control)
  2. Any DROP CONSTRAINT chk_mfa_revocation_reason_length anywhere in
     subsequent migrations
  3. Any ALTER … ADD CONSTRAINT chk_mfa_revocation_reason_length that
     re-defines it with a lower threshold

Behavior: CI fails immediately on any future migration that drops or
weakens the constraint without an explicit override (and a re-run of
the Steve mit B round-table).
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_MIG_DIR = _BACKEND / "migrations"


def test_constraint_definition_in_mig_276():
    """Positive control: mig 276 still defines the constraint at >=40."""
    mig = _MIG_DIR / "276_mfa_admin_overrides.sql"
    src = mig.read_text()
    assert "chk_mfa_revocation_reason_length" in src
    # Allow whitespace + line breaks in CHECK predicate.
    pattern = re.compile(
        r"chk_mfa_revocation_reason_length\s+CHECK\s*\(\s*LENGTH\(reason\)\s*>=\s*(\d+)\s*\)",
        re.IGNORECASE,
    )
    m = pattern.search(src)
    assert m, (
        "mig 276 lost its `LENGTH(reason) >= N` CHECK definition for "
        "chk_mfa_revocation_reason_length. Steve mit B requires this "
        "as the higher-friction gate on revoke reason."
    )
    threshold = int(m.group(1))
    assert threshold >= 40, (
        f"mig 276 chk_mfa_revocation_reason_length threshold dropped to "
        f"{threshold} (expected >= 40 per Steve P3 mit B). Lowering this "
        f"is a friction-erosion change and requires re-running the "
        f"round-table that decided ≥40."
    )


def test_constraint_not_dropped_in_later_migrations():
    """Negative control: no later migration may DROP CONSTRAINT
    chk_mfa_revocation_reason_length without an explicit `# noqa:
    mfa-revoke-reason-friction` override marker."""
    drop_pattern = re.compile(
        r"DROP\s+CONSTRAINT\s+(?:IF\s+EXISTS\s+)?chk_mfa_revocation_reason_length",
        re.IGNORECASE,
    )
    for mig_path in sorted(_MIG_DIR.glob("*.sql")):
        if mig_path.name == "276_mfa_admin_overrides.sql":
            continue
        src = mig_path.read_text()
        for line_num, line in enumerate(src.splitlines(), start=1):
            if drop_pattern.search(line):
                if "noqa: mfa-revoke-reason-friction" in line.lower():
                    continue
                raise AssertionError(
                    f"{mig_path.name}:{line_num} drops "
                    f"chk_mfa_revocation_reason_length — Steve P3 mit B "
                    f"friction gate. If this is intentional, add "
                    f"# noqa: mfa-revoke-reason-friction with a re-run "
                    f"round-table reference in the same line."
                )


def test_constraint_not_re_added_at_lower_threshold():
    """Negative control: no later migration may ADD CONSTRAINT with the
    same name at a lower threshold."""
    add_pattern = re.compile(
        r"ADD\s+CONSTRAINT\s+chk_mfa_revocation_reason_length\s+CHECK\s*\(\s*LENGTH\(reason\)\s*>=\s*(\d+)\s*\)",
        re.IGNORECASE,
    )
    for mig_path in sorted(_MIG_DIR.glob("*.sql")):
        if mig_path.name == "276_mfa_admin_overrides.sql":
            continue
        src = mig_path.read_text()
        for m in add_pattern.finditer(src):
            threshold = int(m.group(1))
            if threshold < 40:
                raise AssertionError(
                    f"{mig_path.name} re-adds chk_mfa_revocation_reason_length "
                    f"at LENGTH(reason) >= {threshold} — Steve P3 mit B "
                    f"requires >= 40. Lowering the threshold is a "
                    f"friction-erosion change."
                )
