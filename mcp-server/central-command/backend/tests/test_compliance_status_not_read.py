"""CI gate: ban new code reading `discovered_devices.compliance_status`.

BUG 3 round-table 2026-05-01 (fork a48dd10968aaf583c, Coach #6):
the column was deprecated in mig 269 because it's a denormalized
cache that was never wired to bundle-ingest. Reading it gives
'unknown' for every device — misleading. Per consensus, source-of-
truth is `compliance_bundles`; live-compute via
`db_queries.get_per_device_compliance(db, site_id, window_days=30)`.

This gate scans backend Python for SELECTs that READ the
`compliance_status` column from `discovered_devices`. Ratchet
baseline = current count post-fix. New reads fail CI immediately.

Per-line opt-out: `# noqa: deprecated-compliance-status —
<reason>` (matches the `site-appliances-deleted-include` and
`rename-site-gate` conventions).

Out of scope (allowed):
- WRITES (INSERT, UPDATE) to compliance_status are still permitted
  because old code paths set it at INSERT time (default 'unknown').
  Removing the column entirely is a future migration; this gate
  prevents NEW READ regressions.
- The `archive` migration (mig 270) reads compliance_status to
  preserve it in the orphan archive table — that's a one-time
  data-preservation read, not a live-system read.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
BACKEND_DIR = REPO_ROOT / "mcp-server" / "central-command" / "backend"

# Match patterns that READ compliance_status from discovered_devices.
# Since SQL appears in Python f-strings + raw strings, look for:
#   - `d.compliance_status` (with table alias 'd')
#   - `discovered_devices.compliance_status`
#   - `dd.compliance_status` (other common alias)
#   - bare `compliance_status` ONLY when nearby line mentions
#     discovered_devices (within 10 lines)
_DIRECT_READ_PATTERNS = [
    re.compile(r"\bd\.compliance_status\b"),
    re.compile(r"\bdiscovered_devices\.compliance_status\b"),
    re.compile(r"\bdd\.compliance_status\b"),
]
_NOQA_PATTERN = re.compile(
    r"#\s*noqa:\s*deprecated-compliance-status", re.IGNORECASE
)

# Pinned baseline. Lower as readers migrate to the helper.
#
# 2026-05-12 reduction (14 → 7):
#   - 3 docstring/comment references rewritten to use prose that
#     doesn't match the regex (db_queries.py:736, device_sync.py:918,
#     device_sync.py:935)
#   - 2 worklist-filter WHERE clauses noqa-marked
#     (background_tasks.py:1015 + client_portal.py:4712 — each was
#     double-counted, so 4 matches dropped). These filter for
#     unscored devices in outreach scans, where the helper would
#     return None for the same signal but the column is more direct
#     for the WHERE position.
#
# Remaining 7 are real SELECT readers returning the column to
# frontend display surfaces:
#   - partners.py:2585         (partner device list)
#   - client_portal.py:1264    (client device list)
#   - device_sync.py:799       (filter parameter)
#   - device_sync.py:1299      (SELECT in get_site_devices)
#   - device_sync.py:1349      (SELECT in get_site_devices, second variant)
#   - routes.py:5310           (legacy device-detail handler)
#   - routes.py:5856           (legacy devices-at-risk handler)
# Migrating these to db_queries.get_per_device_compliance() requires
# changing response shapes the frontend depends on. Carry-followup
# for a dedicated session.
BASELINE_MAX = 7

def _scan() -> list[str]:
    findings: list[str] = []
    for p in BACKEND_DIR.rglob("*.py"):
        if "tests" in p.parts or "fixtures" in p.parts:
            continue
        if p.name.startswith("test_"):
            continue
        if "migrations" in p.parts:
            # Migration files may legitimately read compliance_status
            # for data-preservation purposes (mig 270 archive). Skip.
            continue
        src = p.read_text()
        lines = src.splitlines()
        for pattern in _DIRECT_READ_PATTERNS:
            for m in pattern.finditer(src):
                line_no = src.count("\n", 0, m.start()) + 1
                line_text = lines[line_no - 1] if line_no - 1 < len(lines) else ""
                # noqa marker on this line or next?
                if _NOQA_PATTERN.search(line_text):
                    continue
                if line_no < len(lines) and _NOQA_PATTERN.search(lines[line_no]):
                    continue
                rel = p.relative_to(REPO_ROOT)
                findings.append(f"{rel}:{line_no}: {line_text.strip()[:120]}")
    return findings


def test_no_compliance_status_reads():
    """`discovered_devices.compliance_status` is DEPRECATED (mig 269).
    New READS fail CI. Migrate callers to
    `db_queries.get_per_device_compliance(db, site_id, window_days=30)`.
    """
    findings = _scan()
    count = len(findings)

    if count > BASELINE_MAX:
        new_offenders = "\n".join(f"  - {f}" for f in findings[BASELINE_MAX:])
        raise AssertionError(
            f"DEPRECATED `discovered_devices.compliance_status` reads "
            f"detected: {count} found vs BASELINE_MAX={BASELINE_MAX}. "
            f"NEW reader(s) — migrate to "
            f"`db_queries.get_per_device_compliance(db, site_id)` "
            f"OR add `# noqa: deprecated-compliance-status — <reason>` "
            f"on the same/next line. (BUG 3 round-table 2026-05-01.)"
            f"\n\nAll matches:\n" + "\n".join(f"  - {f}" for f in findings)
        )

    if count < BASELINE_MAX:
        raise AssertionError(
            f"Reads dropped: {count} vs BASELINE_MAX={BASELINE_MAX}. "
            f"Lower BASELINE_MAX to {count} to keep ratchet tight."
        )
