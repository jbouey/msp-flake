"""CI gate: ratchet down `credentials: 'same-origin'` in frontend fetches.

BUG 2 root cause 2026-05-01: SiteComplianceHero.tsx used
`credentials: 'same-origin'` for `/api/dashboard/sites/{site_id}/
compliance-health`. In production behind a Caddy proxy where the
SPA and API mount on different effective origins, `'same-origin'`
does NOT send the session cookie — the request returns 401, the
fetcher's `if (!res.ok) return null` swallows the error, and the
dashboard renders "No Data" on the customer's flagship metric for
the entire pre-cache-warm window.

The codebase's documented posture is `credentials: 'include'`
(see utils/csrf.ts:57 + utils/api.ts:116 fetchApi default).
SiteComplianceHero was the only HOT-path site where this manifested
as a user-visible bug — but a repo-wide grep found 63+ other sites
using the same wrong pattern. Most aren't user-visible because (a)
they're called less frequently, (b) react-query staleTime caches
prior successful responses, OR (c) they happen to run on a
genuinely-same-origin path where the cookie does work.

Round-table consensus 2026-05-01 (fork ab059e8a8bf6dbaed): ship the
1-line SiteComplianceHero fix tonight, ratchet-down the rest as a
scheduled followup, ban NEW occurrences via this CI gate.

Ratchet: starting at the post-fix count (63). New code that adds
`credentials: 'same-origin'` fails CI immediately. As the followup
sweep removes existing sites, lower BASELINE_MAX in step.

Allowlist mechanism: each removed site count is recorded in commit
history. To intentionally KEEP a `same-origin` (extremely rare —
e.g. a public unauthenticated endpoint that explicitly DOES NOT
want cookies), add a per-line `# noqa: same-origin-allowed —
<reason>` marker (matching the existing rename-site-gate pattern
at test_no_direct_site_id_update.py).
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
FRONTEND_SRC = (
    REPO_ROOT / "mcp-server" / "central-command" / "frontend" / "src"
)

# Ratchet baseline. Decrement as the followup sweep removes sites.
# CI fails if found_count > BASELINE_MAX (regression — new same-origin
# added) OR if found_count < BASELINE_MAX (forgot to update baseline).
BASELINE_MAX = 63

# Per-line opt-out marker (matches rename-site-gate convention)
_NOQA_MARKER = re.compile(r"#\s*noqa:\s*same-origin-allowed", re.IGNORECASE)
_OPTED_IN_MARKER = re.compile(r"//\s*same-origin-allowed", re.IGNORECASE)

# The ban target: `credentials: 'same-origin'` or `credentials:
# "same-origin"`. Tolerates whitespace + either quote style.
_BAN_PATTERN = re.compile(
    r"""credentials\s*:\s*['"]same-origin['"]""",
    re.IGNORECASE,
)


def _scan_frontend() -> list[str]:
    findings: list[str] = []
    for p in FRONTEND_SRC.rglob("*"):
        if p.suffix not in (".ts", ".tsx", ".js", ".jsx"):
            continue
        # Skip generated files + tests
        if "__generated__" in p.parts or "node_modules" in p.parts:
            continue
        if p.name.endswith(".test.ts") or p.name.endswith(".test.tsx"):
            continue
        try:
            text = p.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for m in _BAN_PATTERN.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            line = text.splitlines()[line_no - 1] if line_no - 1 < len(text.splitlines()) else ""
            # Per-line allowlist: skip lines with the noqa marker
            if _NOQA_MARKER.search(line) or _OPTED_IN_MARKER.search(line):
                continue
            rel = p.relative_to(REPO_ROOT)
            findings.append(f"{rel}:{line_no}: {line.strip()[:120]}")
    return findings


def test_credentials_same_origin_ratchet():
    """`credentials: 'same-origin'` count must NEVER increase.

    BUG 2 ratchet (2026-05-01). Adding a NEW `same-origin` fails CI.
    Removing one and forgetting to lower BASELINE_MAX also fails CI
    (forces the constants and the codebase to stay in lockstep).

    To remove a site, swap to `credentials: 'include'` AND lower
    BASELINE_MAX in this file by the count removed.

    To intentionally keep one (extremely rare; e.g. unauthenticated
    public endpoint), add `// same-origin-allowed: <reason>` on the
    same line.
    """
    findings = _scan_frontend()
    count = len(findings)

    if count > BASELINE_MAX:
        new_offenders = "\n".join(f"  - {f}" for f in findings[BASELINE_MAX:])
        raise AssertionError(
            f"`credentials: 'same-origin'` count regressed: "
            f"{count} found vs BASELINE_MAX={BASELINE_MAX}. "
            f"NEW offender(s) — switch to `credentials: 'include'` "
            f"OR add `// same-origin-allowed: <reason>` on the same "
            f"line. (BUG 2 round-table 2026-05-01.)\n\n"
            f"All matches:\n" + "\n".join(f"  - {f}" for f in findings)
        )

    if count < BASELINE_MAX:
        raise AssertionError(
            f"`credentials: 'same-origin'` count dropped: {count} "
            f"found vs BASELINE_MAX={BASELINE_MAX}. The followup "
            f"sweep removed sites — lower BASELINE_MAX to {count} "
            f"so the ratchet stays tight."
        )

    # Sanity: BASELINE_MAX should converge to 0 over time
    # (the followup sweep). Asserts BASELINE_MAX hasn't somehow
    # been bumped UP by mistake.
    assert BASELINE_MAX <= 100, (
        f"BASELINE_MAX = {BASELINE_MAX} is unreasonably high. "
        f"The post-BUG-2 baseline was 63; if this is higher, "
        f"someone bumped it up — that's a regression."
    )
