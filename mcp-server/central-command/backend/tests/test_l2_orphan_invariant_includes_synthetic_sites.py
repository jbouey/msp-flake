"""Positive-control ratchet: the L2-resolution-without-decision-record
substrate invariant MUST tick on the synthetic site (Task #102,
deferred from #66 B1 Gate A).

Background
----------
The MTTR-soak synthetic site (mig 315, status='inactive', site_id
prefix 'synthetic-') exists as ground truth for substrate-engine
behavior. The whole reason the synthetic-marker column was added to
substrate_violations (mig 323) was so the soak can inject L2 orphans
on the synthetic site and verify the substrate engine DETECTS them
— without those rows polluting customer-facing metrics, the
universal-filter ratchet, or partner rollups.

For the soak to be load-bearing, the L2-orphan invariant
(`_check_l2_resolution_without_decision_record`) MUST scan
incidents on the synthetic site. If a future refactor adds a
`synthetic IS NOT TRUE` filter to that invariant's SQL, the soak
becomes a no-op — substrate violations never get OPENED for the
synthetic site, and we lose the positive control.

This test pins TWO invariants:

  (A) `_check_l2_resolution_without_decision_record`'s SQL must
      NOT filter on the `synthetic` column (no exclusion of the
      synthetic site).
  (B) The substrate_violations INSERT must derive `synthetic` at
      INSERT time from the site_id pattern (`$3 LIKE 'synthetic-%'`)
      — this is what allows the soak's positive-control rows to be
      tagged AND segregated from customer rollups in the same write.

Together (A) + (B) preserve the soak's ground-truth property:
synthetic-site violations exist in substrate_violations (proving
the engine ticked) but are filtered out of every customer-facing
read by `synthetic IS NOT TRUE`.
"""
from __future__ import annotations

import pathlib
import re


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_ASSERTIONS = _BACKEND / "assertions.py"


def _function_body(name: str) -> str:
    """Extract a top-level async function body (up to the next
    top-level `async def` / `def` / class)."""
    src = _ASSERTIONS.read_text()
    pat = re.compile(rf"^async def {re.escape(name)}\(", re.MULTILINE)
    m = pat.search(src)
    assert m, f"{name} not found in assertions.py"
    rest = src[m.start():]
    # Find next top-level function/class — anchored at column 0.
    next_def = re.search(r"\n(?:async def |def |class )", rest[100:])
    if next_def is None:
        return rest
    return rest[: 100 + next_def.start()]


def test_l2_orphan_invariant_does_not_exclude_synthetic_sites():
    """The L2-orphan invariant's SQL must NOT filter rows by the
    `synthetic` column. Excluding synthetic sites here would silently
    break the MTTR soak's positive control."""
    body = _function_body("_check_l2_resolution_without_decision_record")
    # The query itself is between triple-quoted strings — but searching
    # the whole function body is fine; the surrounding Python doesn't
    # reference `synthetic`.
    forbidden = [
        r"synthetic\s+IS\s+NOT\s+TRUE",
        r"synthetic\s+IS\s+FALSE",
        r"synthetic\s*=\s*FALSE",
        r"NOT\s+s\.synthetic",
        r"NOT\s+i\.synthetic",
    ]
    for pattern in forbidden:
        m = re.search(pattern, body, re.IGNORECASE)
        assert m is None, (
            f"_check_l2_resolution_without_decision_record's SQL "
            f"contains a synthetic-site exclusion ({m.group(0)!r}) — "
            f"this breaks the MTTR-soak positive control. The "
            f"invariant MUST scan synthetic sites; segregation "
            f"happens at substrate_violations INSERT time via the "
            f"derived `synthetic` column, NOT at scan time."
        )


def test_substrate_violations_insert_derives_synthetic_at_write_time():
    """The substrate_violations INSERT in run_assertions_once must
    derive `synthetic` inline from the site_id pattern. If this is
    refactored away (e.g. defaulted FALSE for all writes, or
    threaded through the Violation dataclass and forgotten by a
    caller), the synthetic site's rows leak into customer rollups."""
    src = _ASSERTIONS.read_text()

    # Look for the INSERT and the derivation in the same window.
    insert_idx = src.find("INSERT INTO substrate_violations")
    assert insert_idx != -1, (
        "INSERT INTO substrate_violations not found — the substrate "
        "engine's write path has been refactored. Verify that "
        "synthetic-marker derivation is preserved in the new shape."
    )
    window = src[insert_idx : insert_idx + 1200]

    # The synthetic column must appear in the column list.
    assert re.search(r"\bsynthetic\b", window), (
        "substrate_violations INSERT no longer references the "
        "`synthetic` column. mig 323 added it NOT NULL — the INSERT "
        "must populate it (Task #66 B1)."
    )

    # The value must be derived from the site_id pattern, not a
    # hardcoded FALSE.
    assert re.search(r"LIKE\s+'synthetic-%'", window), (
        "substrate_violations INSERT no longer derives `synthetic` "
        "from the site_id pattern. If a refactor changed this to a "
        "hardcoded FALSE or routed the value through the Violation "
        "dataclass, callers will forget to set it and synthetic-site "
        "violations will leak into customer rollups. The canonical "
        "shape is `$<param> LIKE 'synthetic-%'` in the VALUES clause."
    )


def test_synthetic_allowlisted_noqa_carveouts_present():
    """Several invariant queries JOIN `sites s` with a
    `# noqa: synthetic-allowlisted` carve-out — these are the
    invariants that MUST tick on the synthetic site per #66 B1.
    Removing the carve-out without removing the synthetic filter
    would silently mute those invariants on synthetic; removing
    both would inadvertently let those invariants OPEN violations
    that leak into rollups (without the marker column doing its
    job). Pin both carve-outs are present so the engine keeps
    ticking on synthetic where it should."""
    src = _ASSERTIONS.read_text()
    carveouts = re.findall(
        r"#\s*noqa:\s*synthetic-allowlisted",
        src,
    )
    assert len(carveouts) >= 2, (
        f"Expected at least 2 `# noqa: synthetic-allowlisted` "
        f"carve-outs in assertions.py (chain-orphan + onboarding "
        f"per #66 B1), found {len(carveouts)}. If invariants were "
        f"removed, ensure synthetic-tick coverage is preserved "
        f"elsewhere."
    )
