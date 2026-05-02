"""CI gate: no NEW inline score-threshold comparisons in frontend.

D2 collapse 2026-05-02 — there are 3 canonical helpers in
constants/status.ts:
  - getScoreStatus(score)        → full StatusConfig (label, color, etc.)
  - scoreToBadgeVariant(score)   → 'success' | 'warning' | 'error'
  - scoreToBarColor(score)       → 'bg-health-healthy' | …
  - riskScoreToColor(score)      → INVERSE semantic for risk scores

Inline `score >= 80 ? red : blue` comparisons drift over time —
different files end up using different thresholds (we found 80/50
in OrgDashboard while the canon was 90/70/50, plus 80/40 in
style-tokens, plus 50/25 inline in SRAWizard). Drift = customer-
visible inconsistency between dashboards.

Pattern enforced: any TSX/TS file under frontend/src that compares
a `score`-named or `risk_score`-named variable against numeric
literals 25/40/50/70/80/90 must use a helper from constants/status.

Ratchet baseline starts at the post-D2 count. Each PR must drive
the count down to zero — no new violations land.

To declare a hit intentional (rare):
  - inline `// noqa: score-threshold-gate — <reason>` on the line
"""
from __future__ import annotations

import pathlib
import re

import pytest


_FRONTEND_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend" / "src"

# Match: (score-like-identifier) [<>] [=]? <number>
# Known canonical thresholds: 90/70/50 (compliance), 50/25 (risk inverse),
# 80/40 (legacy style-tokens). Catching any of these inline is a violation.
#
# Adversarial-audit broadening 2026-05-02: original regex missed
# camelCase identifiers (coveredPct, avgCompliance) and bare names
# (avg, rate, success_rate). Now matches any identifier ending in
# pct/score/rate/success_rate/risk* OR exactly 'avg', 'pct', 'score',
# 'rate' as standalone names.
_THRESHOLD_PATTERN = re.compile(
    r"\b(?:[a-zA-Z_][a-zA-Z0-9_]*?(?:[Pp]ct|[Ss]core|[Rr]ate|Success[Rr]ate)"
    r"|score|pct|rate|avg|risk[_a-z]*)"
    r"\s*[><]=?\s*"
    r"(?:25|40|50|60|70|80|90)\b",
    re.IGNORECASE,
)

# Files that DEFINE the canon — exempt from the gate.
_CANON_FILES = {
    "constants/status.ts",
    "tokens/style-tokens.ts",  # legacy helper; tracked separately
}

# Per-line opt-out marker
_NOQA_MARKER = re.compile(r"//\s*noqa\s*:\s*score-threshold-gate\b")

# Ratchet baseline — current count of violations as of D2 collapse.
# Must DECREASE in each PR; cannot increase. Adjusting upward without
# explicit deviation justification = violation.
#
# 2026-05-02 D2 collapse — first pass with narrow regex (matched only
# `score|pct|risk*` standalone) found 5 sites; collapsed to 0.
# Adversarial audit re-ran with broader regex (matches camelCase
# identifiers ending in Pct/Score/Rate, plus `avg`/`rate` standalones)
# and surfaced ~27 additional sites. Honest baseline starts at 27;
# ratchet target is 0. P1 followup: collapse the remaining 27.
BASELINE_MAX = 27


def _walk_frontend_files():
    if not _FRONTEND_DIR.exists():
        pytest.skip(f"Frontend dir not found: {_FRONTEND_DIR}")
    for ext in ("*.tsx", "*.ts"):
        for path in _FRONTEND_DIR.rglob(ext):
            rel = path.relative_to(_FRONTEND_DIR).as_posix()
            if any(rel == c or rel.endswith("/" + c) for c in _CANON_FILES):
                continue
            if rel.endswith(".d.ts") or rel.endswith(".test.ts") or rel.endswith(".test.tsx"):
                continue
            yield path, rel


def _scan_file(path: pathlib.Path) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    try:
        text = path.read_text()
    except Exception:
        return hits
    for ln, line in enumerate(text.splitlines(), start=1):
        if _NOQA_MARKER.search(line):
            continue
        if _THRESHOLD_PATTERN.search(line):
            hits.append((ln, line.strip()[:160]))
    return hits


def test_no_new_inline_score_thresholds():
    all_hits: list[str] = []
    for path, rel in _walk_frontend_files():
        for ln, snippet in _scan_file(path):
            all_hits.append(f"{rel}:{ln}  {snippet}")
    count = len(all_hits)

    if count > BASELINE_MAX:
        listing = "\n  ".join(all_hits)
        raise AssertionError(
            f"Inline score-threshold comparisons exceed ratchet baseline "
            f"({count} > {BASELINE_MAX}). Use helpers in "
            f"constants/status.ts (getScoreStatus, scoreToBadgeVariant, "
            f"scoreToBarColor, riskScoreToColor) instead. To opt out a "
            f"line intentionally, add `// noqa: score-threshold-gate — "
            f"<reason>`. Hits:\n  {listing}"
        )
    if count < BASELINE_MAX:
        # Ratchet — congrats, lower the baseline
        raise AssertionError(
            f"Inline score-threshold count is now {count}, less than "
            f"BASELINE_MAX={BASELINE_MAX}. Update BASELINE_MAX={count} in "
            f"this file to ratchet the limit down. (This is a deliberate "
            f"failure to force ratcheting — converge toward 0.)"
        )
