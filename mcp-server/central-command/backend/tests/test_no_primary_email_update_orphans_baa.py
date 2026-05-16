"""CI gate: ban `UPDATE client_orgs SET primary_email = …` outside a
BAA-aware rename helper (Task #91, Counsel Rule 6).

## Status update 2026-05-16 — orphan class structurally closed

Task #93 v2 Commit 2 (`4af4ddc9`) migrated all 4 BAA readers in
`baa_status.py` + `client_attestation_letter.py` from the email-join
shape (`LOWER(bs.email) = LOWER(co.primary_email)`) to the FK-join
shape (`bs.client_org_id = co.id`) backed by `baa_signatures.client_
org_id` (mig 321 — NOT NULL FK). The original safety motivation for
this gate — "a primary_email rename silently orphans the BAA
signature" — is now **structurally impossible** for the enforcement
predicate, the attestation letter lookup, and the signature_status
read. A primary_email change leaves the BAA signatures intact and
fully reachable via FK; `baa_enforcement_ok()` continues to return
the correct answer pre/post-rename.

Task #94 (BAA-aware primary_email rename helper) was **superseded
by #93 C2** for the safety class. The display surfaces that render
`baa_signatures.email` continue to show the signer email at time of
signing, which is the legally correct rendering (the signature
attaches to the email at the moment of commitment — re-rendering
with current-primary-email would misrepresent the signature event
per §164.504(e)).

## Why the gate stays active

The gate continues to enforce "no bare `UPDATE primary_email`" for
three reasons distinct from the original safety class:

  1. **Audit-trail discipline** — any primary_email mutation should
     flow through a controlled path that writes admin_audit_log +
     captures the actor + reason. Bare UPDATEs from routes.py would
     bypass that.
  2. **No live caller needs primary_email rename today** — until a
     concrete consumer surfaces, the cleanest posture is "blocked
     by default." Re-enabling rename can ship in the same commit
     that introduces a real caller.
  3. **Future-proofing** — if customers later want self-service
     primary_email rename (rare; usually paired with OAuth-provider
     change), the helper path is built once + audited once + the
     gate enforces a single re-entry point.

## Exemption mechanism (mirrors test_no_direct_site_id_update.py)

  * Per-line: append `# noqa: primary-email-baa-gate — <reason>`
    (Python), `// noqa: primary-email-baa-gate — <reason>` (TS/Go),
    or `-- noqa: primary-email-baa-gate — <reason>` (SQL) on the line
    that contains the SET clause. The reason is mandatory.
  * File-level: only this test file carries the marker today. A
    future BAA-aware rename helper (when a real consumer surfaces)
    will need the exemption.

Comment-only lines (starting with `--`, `#`, `//`) are skipped to avoid
false positives on documentation.

Ratchet: the count of `noqa: primary-email-baa-gate` markers cannot
grow without explicit baseline bump (forces operator review of any
new exemption).
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

SCAN_ROOTS = [
    REPO_ROOT / "mcp-server",
    REPO_ROOT / "appliance",
    REPO_ROOT / "agent",
]

EXTENSIONS = {".py", ".sql", ".go", ".ts", ".tsx"}

# Match `SET primary_email =` anywhere on a line — covers literal SQL
# strings, both inline and assembled.
PATTERN = re.compile(r"\bSET\s+primary_email\s*=", re.IGNORECASE)

# Per-line exemption marker. Anywhere in the same line counts.
NOQA_MARKER = "noqa: primary-email-baa-gate"

# Comment-only line prefixes (after leading whitespace).
COMMENT_PREFIXES = ("--", "#", "//", "/*", "*")

# File-level exemptions. This test itself documents the pattern in its
# docstring + carries the marker token; no production file is exempt
# today.
EXEMPT_PATHS = {
    "mcp-server/central-command/backend/tests/test_no_primary_email_update_orphans_baa.py",
}

# Ratchet: count of `noqa: primary-email-baa-gate` markers across the
# codebase. Cannot grow without bumping this baseline. 0 today.
NOQA_BASELINE_MAX = 0


def _is_comment_line(line: str) -> bool:
    stripped = line.lstrip()
    return any(stripped.startswith(p) for p in COMMENT_PREFIXES)


def _scan_repo() -> tuple[list[str], int]:
    """Return (violations, noqa_count)."""
    violations: list[str] = []
    noqa_count = 0
    for root in SCAN_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in EXTENSIONS:
                continue
            rel = str(path.relative_to(REPO_ROOT))
            if rel in EXEMPT_PATHS:
                continue
            try:
                text_body = path.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            for line_no, line in enumerate(text_body.splitlines(), 1):
                if not PATTERN.search(line):
                    continue
                if _is_comment_line(line):
                    continue
                if NOQA_MARKER in line:
                    noqa_count += 1
                    continue
                violations.append(f"  {rel}:{line_no}: {line.strip()}")
    return violations, noqa_count


def test_no_direct_primary_email_update():
    """No code path may issue `SET primary_email = …` against
    client_orgs without the per-line exemption marker."""
    violations, _ = _scan_repo()
    assert not violations, (
        f"{len(violations)} unmarked `SET primary_email =` callsite(s) — "
        f"these orphan baa_signatures and break baa_enforcement_ok() for "
        f"the org. Use the BAA-aware rename helper (task #91-FU-B) or "
        f"append `noqa: primary-email-baa-gate — <reason>` on the line:\n"
        + "\n".join(violations)
    )


def test_noqa_markers_under_ratchet_baseline():
    """The number of exemption markers cannot grow without operator
    review — bump NOQA_BASELINE_MAX in this file with a justification."""
    _, noqa_count = _scan_repo()
    assert noqa_count <= NOQA_BASELINE_MAX, (
        f"`noqa: primary-email-baa-gate` markers ({noqa_count}) exceed "
        f"baseline ({NOQA_BASELINE_MAX}). A new exemption was added — "
        f"either remove it (use the BAA-aware rename helper instead) or "
        f"bump NOQA_BASELINE_MAX with a comment explaining why the "
        f"exemption is BAA-safe (signatures re-anchored in same txn?)."
    )
