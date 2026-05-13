"""Cross-design migration-number collision gate (Task #59).

Three Gate A cycles on 2026-05-13 burned on bookkeeping collisions
(load-harness mig 310, MTTR-soak mig 311, P-F9 mig 314). This gate
closes the class structurally via explicit `mig-claim:` markers.

Design v3 + Gate A v3 APPROVE at:
  audit/reserved-migrations-ledger-design-2026-05-13.md
  audit/coach-reserved-migrations-ledger-gate-a-v3-2026-05-13.md

Sibling-precedent: tests/test_pre_push_ci_parity.py (cross-file
invariant check) + tests/test_no_direct_site_id_update.py (ratchet-
style enforcement).

Three layers of false-positive defense:
  1. Line-anchor    — marker must be on a line BY ITSELF.
  2. Task sigil     — marker must carry `task:#NN`.
  3. Code fences    — fenced blocks stripped before matching.
  4. Verdict-doc   — coach-*.md docs excluded (echo claims in prose).
"""
from __future__ import annotations

import datetime
import pathlib
import re

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent.parent.parent
_MIGRATIONS_DIR = _REPO / "mcp-server/central-command/backend/migrations"
_AUDIT_DIR = _REPO / "audit"
_LEDGER = _MIGRATIONS_DIR / "RESERVED_MIGRATIONS.md"

_CLAIM_MARKER_RE = re.compile(
    r"^<!--\s*mig-claim:\s*([1-9]\d{2})\s+task:#(\d+)\s*-->\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_LEDGER_ROW_RE = re.compile(
    r"^\|\s*(\d{3})\s*\|\s*(\w+)\s*\|.*?\|\s*(\d{4}-\d{2}-\d{2})\s*\|"
    r"\s*(\d{4}-\d{2}-\d{2}|—|TBD)\s*\|",
    re.MULTILINE,
)
_PER_ROW_JUSTIFICATION_RE = re.compile(
    r"<!--\s*stale-justification:[^>]+?-->", re.IGNORECASE
)
_EXPECTED_LEDGER_HEADER = (
    "| Number | Status | Claimed-by (design doc) | Claimed-at | "
    "Expected ship | Task | Notes |"
)

_MAX_LEDGER_ROWS = 30
_SOFT_WARN_ROWS = 25
_STALE_WARN_DAYS = 30


def _shipped_migrations() -> set[int]:
    out: set[int] = set()
    for f in _MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"):
        out.add(int(f.name[:3]))
    return out


def _ledger_rows() -> list[dict]:
    if not _LEDGER.exists():
        return []
    text = _LEDGER.read_text()
    out: list[dict] = []
    for m in _LEDGER_ROW_RE.finditer(text):
        out.append({
            "n": int(m.group(1)),
            "status": m.group(2).strip(),
            "claimed_at": m.group(3),
            "expected_ship": m.group(4),
        })
    return out


def _claim_markers() -> dict[int, list[str]]:
    """Returns {mig_number: [doc_paths_with_claim_marker]} — line-anchored
    claims only, code fences stripped, all coach-*.md verdict/audit docs
    excluded (Gate A v3 P2 #1 broadened filter for defense-in-depth).
    """
    out: dict[int, list[str]] = {}
    for doc in _AUDIT_DIR.glob("*.md"):
        if doc.name.startswith("coach-"):
            continue
        text = doc.read_text(errors="ignore")
        text_stripped = _CODE_FENCE_RE.sub("", text)
        for m in _CLAIM_MARKER_RE.finditer(text_stripped):
            n = int(m.group(1))
            out.setdefault(n, []).append(doc.name)
    return out


def _row_line_for(n: int) -> str | None:
    if not _LEDGER.exists():
        return None
    for line in _LEDGER.read_text().splitlines():
        if re.match(rf"^\|\s*{n}\s*\|", line):
            return line
    return None


def test_no_claim_marker_for_shipped_migration():
    shipped = _shipped_migrations()
    markers = _claim_markers()
    collisions: list[str] = []
    for n, docs in markers.items():
        if n in shipped:
            collisions.append(
                f"mig {n} is shipped on disk but claimed by: {', '.join(docs)}"
            )
    assert not collisions, (
        "Design docs carry `mig-claim:` markers for shipped migrations. "
        "Renumber the design.\n" + "\n".join(collisions)
    )


def test_no_two_docs_claim_same_unshipped_migration():
    shipped = _shipped_migrations()
    markers = _claim_markers()
    collisions: list[str] = []
    for n, docs in markers.items():
        if n in shipped:
            continue
        uniq = sorted(set(docs))
        if len(uniq) > 1:
            collisions.append(f"mig {n} claimed by: {', '.join(uniq)}")
    assert not collisions, (
        "Multiple design docs claim the same unshipped migration via "
        "`mig-claim:` marker. Update the ledger and renumber one.\n"
        + "\n".join(collisions)
    )


def test_every_claim_marker_in_ledger():
    shipped = _shipped_migrations()
    ledger_nums = {r["n"] for r in _ledger_rows()}
    markers = _claim_markers()
    missing: list[str] = []
    for n, docs in markers.items():
        if n in shipped:
            continue
        if n not in ledger_nums:
            missing.append(f"mig {n} claimed by {docs[0]} not in ledger")
    assert not missing, (
        f"`mig-claim:` markers without ledger entry in "
        f"{_LEDGER.relative_to(_REPO)}.\n" + "\n".join(missing)
    )


def test_no_ledger_row_for_shipped_migration():
    shipped = _shipped_migrations()
    stale: list[str] = []
    for r in _ledger_rows():
        if r["n"] in shipped:
            stale.append(
                f"mig {r['n']} shipped on disk but ledger row remains"
            )
    assert not stale, (
        "Ledger has rows for migrations already shipped. Remove them in "
        "the same commit as the migration file.\n" + "\n".join(stale)
    )


def test_ledger_row_count_under_hard_cap():
    rows = _ledger_rows()
    assert len(rows) <= _MAX_LEDGER_ROWS, (
        f"Ledger has {len(rows)} rows; hard cap is {_MAX_LEDGER_ROWS}. "
        f"Coordination breakdown — surface to round-table."
    )


def test_ledger_row_count_under_soft_warn(capsys):
    """Soft warning at 25 rows — gives 5-row coordination buffer per
    Gate A v3 P2 #2. Test passes but emits a captured stderr signal.
    """
    rows = _ledger_rows()
    if len(rows) >= _SOFT_WARN_ROWS:
        print(
            f"WARN: ledger has {len(rows)} rows (soft-warn at "
            f"{_SOFT_WARN_ROWS}, hard cap {_MAX_LEDGER_ROWS}). "
            f"Coordination pressure rising; surface to round-table."
        )


def test_no_stale_ledger_rows_without_justification():
    today = datetime.date.today()
    stale: list[str] = []
    for r in _ledger_rows():
        if r["expected_ship"] in ("—", "TBD"):
            continue
        try:
            exp = datetime.date.fromisoformat(r["expected_ship"])
        except ValueError:
            continue
        if (today - exp).days <= _STALE_WARN_DAYS:
            continue
        row_line = _row_line_for(r["n"]) or ""
        if not _PER_ROW_JUSTIFICATION_RE.search(row_line):
            stale.append(
                f"mig {r['n']} expected ship {r['expected_ship']} is "
                f">{_STALE_WARN_DAYS}d stale; row Notes column missing "
                f"<!-- stale-justification: ... --> marker"
            )
    assert not stale, (
        "Stale ledger rows must carry a per-row stale-justification "
        "marker in the Notes column.\n" + "\n".join(stale)
    )


def test_ledger_header_unchanged():
    """Catch silent column-reordering drift per Gate A v3 P2 #3. The
    row regex depends on column order; if a future PR reorders columns
    the row regex silently captures wrong fields.
    """
    if not _LEDGER.exists():
        return
    lines = _LEDGER.read_text().splitlines()
    header = next(
        (line for line in lines if line.startswith("| Number |")), None
    )
    assert header == _EXPECTED_LEDGER_HEADER, (
        f"Ledger column header drifted. Expected:\n"
        f"  {_EXPECTED_LEDGER_HEADER}\n"
        f"Got:\n  {header!r}\n"
        f"If you intentionally reordered columns, update "
        f"_LEDGER_ROW_RE + _EXPECTED_LEDGER_HEADER together."
    )
