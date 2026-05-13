# RESERVED_MIGRATIONS Ledger + CI Collision Gate — Design v3 (Task #59)

> **v3 changes (Gate A v2 BLOCK → 2 new P0s + 1 new P1 applied 2026-05-13):**
>
> - **P0-NEW #1 (Steve + Coach, self-eating regex):** v2's `<!-- mig-claim: NNN -->` marker was captured by the gate from the design's OWN example lines + v1 verdict's prose mentions. 5+ examples in §4 of v2 design got falsely classified as claims. Fix: **line-anchored regex + mandatory `task:#NN` sigil + code-fence stripping + audit-doc filename filter (`coach-*.md` excluded)**. The marker must be the WHOLE LINE, must carry a task ID, and must NOT appear inside a code fence or in a verdict doc. Examples in prose can omit the task sigil to stay non-claiming.
> - **P0-NEW #2 (Steve, doc-scoped stale justification):** v2's `_STALE_JUSTIFICATION_RE.search(text)` was doc-scoped — ONE justification anywhere silently exempted EVERY stale row. Fix: **per-row inline justification** in the Notes column with the shape `<!-- stale-justification: ... -->` parsed against the matching row only.
> - **P1-NEW #3 (Steve, range bound):** `mig-claim: 099` would be parsed (3-digit shape) but unreasonable. Fix: regex constrains 100..999.
>
> ---
>
> **v2 changes (Gate A v1 BLOCK → all 3 P0s + 2 P1s + 1 P2 applied 2026-05-13):**
>
> - **P0 #1 (Steve regex over-capture):** Greedy `\bmig\s+\d{3}\b` regex captures 50+ historical references in audit/ docs ("mig 146, 2026-03", "mig 138 partitioned compliance_bundles", "mig 257 site-rename allowlist", etc.). Fix: replace with explicit **`<!-- mig-claim: NNN -->` HTML-comment marker**. Only marker-bearing references count as claims. Historical citations stay unmarked.
> - **P0 #2 (Coach verdict-doc filter hole):** 17 of 98 coach docs (e.g., `-2nd-eye`, `-redo`, `-enterprise-backlog`, `-heartbeat-timestamp-protocol`, `-15-commit-adversarial-audit`) ECHO design claims but weren't in the filter. **Eliminated by P0 #1 fix** — verdict docs don't carry the claim marker, so they're invisible to the gate by construction.
> - **P0 #3 (PM ordering trap):** Single-phase ship would fail CI on Day 1. Fix: **4-commit ordering** (companion renumbers → ledger drop → CI gate enable → CLAUDE.md + template).
> - **P1 #1 (PM + Counsel Rule 5 stale-doc):** Add `expected_ship_date` column + 30-day warn-threshold + ≤30-row hard cap on ledger size. Without these the ledger itself becomes a future Rule 5 violation.
> - **P1 #2 (Maya + Counsel Rule 4 orphan-coverage):** Add explicit scope-clarification header — "**backend-only; `appliance/` and `agent/` have no migration spaces today**" (verified empty via `find appliance/ agent/ -name "*.sql"`).
> - **P2 (PM):** Add `BLOCKED` 4th lifecycle status — Vault P0 #43 mig 311 is the canonical first user (BLOCKED on staging precondition).

---

## §1 — The contract (v2)

Every design doc that claims a migration number declares it with an **explicit HTML-comment marker on a line by itself, with a task-ID sigil**:

```
{{ MARKER-EXAMPLE — see §10 for the literal shape; intentionally not literal here to avoid self-capture }}
```

Literal shape (per §10): `<!-- mig-claim:<NNN> task:#<TASK> -->` as the WHOLE LINE.

The marker appears once per claimed number in the design doc body, OUTSIDE any code fence. The CI gate `tests/test_migration_number_collision.py` strips fenced regions, filters out `coach-*-gate-{a,b}*.md` verdict docs, and enforces three invariants on the remaining design docs:

1. **No two design docs in `audit/` carry a `mig-claim: NNN` marker for the same un-shipped number.**
2. **No design doc carries a `mig-claim: NNN` marker for a number that already exists on disk** in `migrations/NNN_*.sql`.
3. **Every `mig-claim: NNN` marker must have a matching ledger row** (claim must be registered).

Historical prose references like "mig 257 site-rename allowlist" or "mig 138 partitioned compliance_bundles" are **NOT claims** — they're citations. The marker pattern is opt-in by design author.

---

## §2 — Ledger format (v2)

`mcp-server/central-command/backend/migrations/RESERVED_MIGRATIONS.md`:

```markdown
# Reserved migration numbers — backend only

> **Scope:** this ledger covers `mcp-server/central-command/backend/migrations/` only. The `appliance/` and `agent/` repos have no migration number-spaces as of 2026-05-13 (verified empty via `find appliance/ agent/ -name "*.sql"` — zero hits).
>
> **Lifecycle:** `reserved` → `in_progress` → `BLOCKED` (waiting on external precondition) → SHIPPED (file lands on disk; row REMOVED in the same commit).
>
> **Stale-reservation rule:** rows older than `expected_ship_date + 30 days` are flagged by CI as STALE — must either ship, mark BLOCKED with justification, or release the number.
>
> **Hard cap:** at most 30 active rows. Beyond this signals coordination breakdown; surface to round-table.

| Number | Status | Claimed-by (design doc) | Claimed-at | Expected ship | Task | Notes |
|--------|--------|------------------------|------------|---------------|------|-------|
| 311 | BLOCKED | audit/vault-phase-c-design-... | 2026-05-12 | 2026-05-27 | #43 | BLOCKED on staging precondition |
| 314 | in_progress | audit/canonical-metric-drift-invariant-design-2026-05-13.md | 2026-05-13 | 2026-05-20 | #50 | Phase 2a mig 314 canonical_metric_samples |
| 315 | reserved | audit/substrate-mttr-soak-v2-design-2026-05-13.md (renumber pending) | 2026-05-13 | 2026-05-20 | #98 | Renumber from claimed 311 |
| 316 | reserved | audit/load-harness-v2.1-design-... (v2.1 pending) | 2026-05-13 | 2026-05-23 | #38 | Renumber from claimed 310 |
| 317 | reserved | audit/p-f9-profitability-v2-design-... (v2 pending) | 2026-05-13 | 2026-05-30 | #58 | Renumber from claimed 314 (assumptions table) |
| 318 | reserved | audit/p-f9-profitability-v2-design-... (v2 pending) | 2026-05-13 | 2026-05-30 | #58 | Companion (packets table) |
```

### Removal rule
When a migration file lands in `migrations/NNN_*.sql`, the matching ledger row MUST be removed in the same commit. The on-disk SQL is post-ship authority.

### Stale-flag rule
CI gate runs `expected_ship + 30d < today` and asserts no STALE rows OR each STALE row carries a `<!-- stale-justification: ... -->` HTML comment. Forces operator decision.

---

## §3 — CI gate `tests/test_migration_number_collision.py`

```python
"""Cross-design migration-number collision gate (Task #59 v2).

Three Gate A cycles on 2026-05-13 burned on bookkeeping collisions
(load-harness mig 310, MTTR-soak mig 311, P-F9 mig 314). This gate
closes the class structurally via explicit `mig-claim:` markers.

Sibling-precedent: tests/test_pre_push_ci_parity.py (cross-file
invariant check) + tests/test_no_direct_site_id_update.py (ratchet-
style enforcement).
"""
from __future__ import annotations

import datetime
import pathlib
import re

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent.parent
_MIGRATIONS_DIR = _REPO / "mcp-server/central-command/backend/migrations"
_AUDIT_DIR = _REPO / "audit"
_LEDGER = _MIGRATIONS_DIR / "RESERVED_MIGRATIONS.md"

# Line-anchored claim marker with mandatory task sigil + 100..999 range
# bound. Examples in prose that omit the `task:#NN` sigil are non-claiming.
# Code-fenced blocks are stripped before matching. (v3 fix per Gate A v2.)
_CLAIM_MARKER_RE = re.compile(
    r"^<!--\s*mig-claim:\s*([1-9]\d{2})\s+task:#(\d+)\s*-->\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
# Per-row stale justification — parsed inside the row Notes column only.
_PER_ROW_JUSTIFICATION_RE = re.compile(
    r"<!--\s*stale-justification:[^>]+?-->", re.IGNORECASE
)
_LEDGER_ROW_RE = re.compile(
    r"^\|\s*(\d{3})\s*\|\s*(\w+)\s*\|.*?\|\s*(\d{4}-\d{2}-\d{2})\s*\|"
    r"\s*(\d{4}-\d{2}-\d{2}|—|TBD)\s*\|",
    re.MULTILINE,
)
_STALE_JUSTIFICATION_RE = re.compile(
    r"<!--\s*stale-justification:.+?-->", re.IGNORECASE | re.DOTALL
)

_MAX_LEDGER_ROWS = 30
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
        n = int(m.group(1))
        status = m.group(2).strip()
        claimed_at = m.group(3)
        expected_ship = m.group(4)
        out.append({
            "n": n,
            "status": status,
            "claimed_at": claimed_at,
            "expected_ship": expected_ship,
        })
    return out


def _claim_markers() -> dict[int, list[str]]:
    """Returns {mig_number: [doc_paths_with_claim_marker]} — line-anchored
    claims only, code fences stripped, verdict docs excluded.
    """
    out: dict[int, list[str]] = {}
    for doc in _AUDIT_DIR.glob("*.md"):
        # Exclude Gate-A/B verdict docs by filename — they ECHO design
        # claims in prose, not as own claims.
        if doc.name.startswith("coach-") and (
            "gate-a" in doc.name or "gate-b" in doc.name
        ):
            continue
        text = doc.read_text(errors="ignore")
        # Strip fenced code blocks so examples inside ``` ``` don't count.
        text_stripped = _CODE_FENCE_RE.sub("", text)
        for m in _CLAIM_MARKER_RE.finditer(text_stripped):
            n = int(m.group(1))
            out.setdefault(n, []).append(doc.name)
    return out


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
    rows = _ledger_rows()
    stale: list[str] = []
    for r in rows:
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
        f"Ledger has {len(rows)} rows; cap is {_MAX_LEDGER_ROWS}. "
        f"Coordination breakdown — surface to round-table."
    )


def _row_line_for(n: int) -> str | None:
    """Return the full Markdown line text for the ledger row with mig
    number `n`, or None. Used for per-row inline marker parsing.
    """
    if not _LEDGER.exists():
        return None
    for line in _LEDGER.read_text().splitlines():
        m = re.match(rf"^\|\s*0*{n}\s*\|", line)
        if m:
            return line
    return None


def test_no_stale_ledger_rows_without_justification():
    today = datetime.date.today()
    rows = _ledger_rows()
    stale: list[str] = []
    for r in rows:
        if r["expected_ship"] in ("—", "TBD"):
            continue
        try:
            exp = datetime.date.fromisoformat(r["expected_ship"])
        except ValueError:
            continue
        if (today - exp).days <= _STALE_WARN_DAYS:
            continue
        row_line = _row_line_for(r["n"]) or ""
        # Per-row justification — must appear in THIS row's Notes column.
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
```

---

## §4 — 4-commit ordering (P0 #3 fix)

CI gate fail-hard from Day 1 would break Day 1. Ship in this order so each commit is green:

**Commit 1 — companion renumbers** (Tasks #58/#61/#62):
- MTTR soak v2 design: `<!-- mig-claim: 315 -->` (was historical claim of 311)
- Load harness v2.1 design: `<!-- mig-claim: 316 -->` (was historical claim of 310)
- P-F9 v2 design: `<!-- mig-claim: 317 -->` + `<!-- mig-claim: 318 -->` (was historical claim of 314+315)
- Task #50 canonical_metric_samples design: `<!-- mig-claim: 314 -->` (already in-progress)
- Task #43 Vault P0: `<!-- mig-claim: 311 -->`
- This commit only TOUCHES design docs — no code, no CI.

**Commit 2 — ledger drop:**
- Create `RESERVED_MIGRATIONS.md` with the 5 rows.
- Update CLAUDE.md "Rules" with one-liner pointing at ledger.

**Commit 3 — CI gate enable:**
- Add `tests/test_migration_number_collision.py`.
- Add to `.githooks/pre-push` SOURCE_LEVEL_TESTS.
- All 6 tests pass green on first run (commits 1+2 already aligned the state).

**Commit 4 — template + memory:**
- Add design-doc template `audit/_template-design.md` with the marker pre-populated as a TODO.
- Save memory file `feedback_migration_number_claim_marker.md` capturing the convention.

---

## §5 — Scope decisions (v2)

**(a) Backend-only — VERIFIED EMPTY in other repos.** P1 #2 fix: header in ledger states scope explicitly. `find appliance/ agent/ -name "*.sql"` returns zero — no other migration number-spaces today.

**(b) Marker placement.** Place the marker on a line BY ITSELF near the §Schema or §Migration section of the design doc. Linters / human readers spot it immediately.

**(c) Multi-claim per design.** A design that introduces 2+ migrations (e.g., P-F9 with 317 + 318) carries 2 markers. The regex captures all; the ledger lists each as a separate row.

**(d) Stale-reservation cleanup.** P1 #1 fix: every row carries `expected_ship_date`. CI flags rows >30d past expected unless `<!-- stale-justification: ... -->` is present. Forces decisions, not silent staleness.

**(e) BLOCKED status.** P2 fix: 4th lifecycle state. Vault P0 #43 (mig 311) is the canonical first BLOCKED row — its `expected_ship_date` is set, but staging-env precondition is documented in Notes column.

---

## §6 — Counsel-rule check (v2)

- **Rule 1 (canonical metric):** N/A — operator-internal coordination, no metric.
- **Rule 2 (PHI boundary):** N/A — no data egress.
- **Rule 3 (privileged chain):** N/A — no privileged action.
- **Rule 4 (orphan coverage):** addressed by §2 explicit scope header. If `appliance/` or `agent/` later acquires its own migration space, this design must be re-scoped.
- **Rule 5 (no stale doc as authority):** addressed by P1 #1 stale-flag rule + hard 30-row cap. The ledger remains current or fires CI.
- **Rule 6 (BAA-in-memory):** N/A.
- **Rule 7 (unauthenticated context):** N/A — internal-only.

---

## §7 — Open questions for Gate A v2

- **(a)** Should the design-doc template (commit 4) be required for *new* designs only, or backfilled to all in-flight designs? Recommend new-designs-only; backfill is opt-in via the renumber commits.
- **(b)** Cadence of ledger hygiene review — monthly via `context-manager.py validate` integration? Or pure-CI?
- **(c)** When a number is RELEASED (design dropped, reservation withdrawn): how is the row removed without it looking like an out-of-band edit? Recommend git-trailer convention: `Mig-released: NNN — reason`.

---

## §10 — Literal marker shape (per-row reference)

The marker is intentionally not written literally elsewhere in this doc to avoid self-capture. It is:

`<` `!` `-` `-` ` mig-claim:` `NNN` ` task:#` `MM` ` ` `-` `-` `>` — as a complete single line, outside fenced code, where `NNN` is a 100..999 integer and `MM` is the TaskCreate task ID.

When v3 ships, the §4 commit-1 design docs each receive ONE such line near their migration section. The regex `^<!--\s*mig-claim:\s*([1-9]\d{2})\s+task:#(\d+)\s*-->\s*$` (MULTILINE) captures both fields.

Verdict docs that need to discuss the marker (like the Gate A reports for this design) reference it WITHOUT the task sigil — e.g., "use the `mig-claim:NNN` pattern" — and are also filtered by the `coach-*-gate-{a,b}*.md` filename rule as defense-in-depth.

---

## §8 — Multi-device-enterprise lens

At enterprise scale (multiple teams shipping designs in parallel), the O(M²) collision rate of unmanaged numbering is the dominant friction. The marker-based design adds ~3 chars per design doc and one PR row per claim — overhead is sub-1%. The collision-save is measured in Gate A cycles (today's 3 collisions × ~3 min fork compute = ~9 min saved, plus design-cycle churn). Net positive at any scale ≥3 in-flight designs.
