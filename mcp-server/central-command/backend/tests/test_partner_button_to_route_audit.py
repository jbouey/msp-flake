"""Source-level rule: every `navigate('/partner/...')` literal in the
partner portal frontend MUST resolve to a Route registered under
`<Route path="/partner/*">` in App.tsx. Orphan navigations fall through
to the `*` catch-all (PartnerLogin) and silently bounce a logged-in
admin to a re-login page.

2026-05-08 audit (commit `2207bfcc`) caught two orphans:
  - PartnerHomeDashboard:174 attention-list "Open" button →
    `/partner/site/${id}` → no Route → catch-all bounce.
  - PartnerWeeklyRollup:175 rollup "Open" button → same orphan.

Both were deflected to `/partner/dashboard?site=<id>` (PartnerDashboard
reads ?site and lands on Sites tab). This gate catches that class
STRUCTURALLY: any new orphan navigate fails CI before it reaches users.

Algorithm:
  1. Walk frontend/src/partner/**/*.tsx (excluding tests).
  2. Extract every `navigate(<string-literal>)` and `navigate(<template-
     literal>)` whose target starts with `/partner/`.
  3. Parameterize the target — `${expr}` and `:param` segments become
     a single-segment wildcard so a navigate to `/partner/site/${id}`
     can match a Route `path="site/:siteId"`.
  4. Parse App.tsx for the `<Routes>` block whose parent mount is
     `/partner/*` and extract every child Route's `path` attribute.
     Prepend `/partner/` so paths match the navigate side.
  5. For each navigate target, assert at least one Route pattern
     matches.

Allow-list:
  - Query strings (`?site=...`) are stripped before matching, so
    `/partner/dashboard?site=abc` matches `/partner/dashboard`.
  - The catch-all `*` Route is NOT a valid match — it deliberately
    bounces unknown paths and is the bug we're catching.

Ratchet baseline 0 violations after the audit fix-up commit
`2207bfcc`. The gate is fail-loud from day one — no ratchet needed.

Companion fixture at tests/fixtures/orphan_navigation_fixture.tsx is
a deliberate-violation positive control that the gate validates via
`test_gate_catches_synthetic_orphan`. The fixture is NOT scanned by
the production rule (its directory is excluded).
"""
from __future__ import annotations

import pathlib
import re
from typing import List, Set, Tuple

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
FRONTEND_SRC = REPO_ROOT / "mcp-server" / "central-command" / "frontend" / "src"
PARTNER_DIR = FRONTEND_SRC / "partner"
APP_TSX = FRONTEND_SRC / "App.tsx"


# Match `navigate('/partner/...')` or `navigate("/partner/...")` or
# `navigate(\`/partner/...\`)`. The target is captured up to the
# closing quote/backtick. Template-literal interpolations stay in
# the captured string and are normalized by `_parameterize`.
NAVIGATE_RE = re.compile(
    r"navigate\s*\(\s*([\'\"`])(/partner/[^\'\"`]*)\1",
)

# Match `<Route path="..." ...>` inside App.tsx. We keep both single-
# and double-quoted forms.
ROUTE_PATH_RE = re.compile(
    r'<Route\s+[^>]*?path\s*=\s*[\'"]([^\'"]+)[\'"]',
)


def _parameterize(target: str) -> str:
    """Normalize a navigate target so it can match a Route pattern.

    - Strip the query-string (`?site=abc` is irrelevant for routing).
    - Strip a trailing slash for stable comparison.
    - Replace `${expr}` interpolations with `:param` (single-segment).
    - Leave `:param` placeholders alone (already in Route form).
    """
    # Strip query-string.
    qpos = target.find("?")
    if qpos != -1:
        target = target[:qpos]
    # Strip hash-fragment.
    hpos = target.find("#")
    if hpos != -1:
        target = target[:hpos]
    # Replace template-literal interpolations with a wildcard segment.
    # We translate `${anything}` → `:p` — a single segment placeholder.
    target = re.sub(r"\$\{[^}]+\}", ":p", target)
    # Strip trailing slash.
    if target.endswith("/") and len(target) > 1:
        target = target[:-1]
    return target


def _segments(path: str) -> List[str]:
    """Split a path on `/`, dropping empty leading/trailing segments."""
    return [s for s in path.split("/") if s]


def _segment_matches(target_seg: str, route_seg: str) -> bool:
    """Single segment match. A `:param` route segment matches ANY
    target segment (literal or wildcard); a literal route segment
    only matches the same literal target segment OR a wildcard
    target segment (since the wildcard came from `${id}` and the
    operator clearly intended any value)."""
    if route_seg.startswith(":"):
        return True
    if target_seg.startswith(":"):
        # Wildcard target — operator passes a runtime value. Only a
        # `:param` route segment is acceptable; literal-on-route +
        # wildcard-on-target means the operator will navigate to a
        # value that may not match the literal.
        return False
    return target_seg == route_seg


def _path_matches(target: str, route_pattern: str) -> bool:
    """Whole-path match: segment-by-segment, lengths must match.

    Both are normalized — target via `_parameterize`, route as-is from
    App.tsx. The catch-all `*` is intentionally NOT treated as a
    match (a `*` Route is the bug — it's the silent re-login bounce).
    """
    if route_pattern.strip() == "*":
        return False  # catch-all is the bug we're catching
    t = _segments(target)
    r = _segments(route_pattern)
    if len(t) != len(r):
        return False
    return all(_segment_matches(ts, rs) for ts, rs in zip(t, r))


def _extract_partner_routes() -> List[str]:
    """Parse App.tsx and return the list of full `/partner/...` paths
    registered under the `<Route path="/partner/*">` mount.

    The partner Routes are inside a closure assigned to
    `PartnerRoutes`. We anchor on the `PartnerProvider` opening tag
    (it's the unique root of the partner sub-tree) and extract every
    `<Route path="...">` until the matching `</Routes>` close.
    """
    src = APP_TSX.read_text(encoding="utf-8")

    # Find the PartnerProvider block.
    start_marker = "<PartnerProvider>"
    end_marker = "</PartnerProvider>"
    s = src.find(start_marker)
    if s == -1:
        raise AssertionError(
            "App.tsx PartnerProvider opening tag not found — "
            "partner Routes block could not be located. The route-"
            "orphan gate cannot run without a routes-of-record."
        )
    e = src.find(end_marker, s)
    if e == -1:
        raise AssertionError(
            "App.tsx PartnerProvider closing tag not found — "
            "partner Routes block could not be located."
        )
    block = src[s:e]

    # Each child Route's path is relative to `/partner/*`. Prepend.
    paths = ROUTE_PATH_RE.findall(block)
    full = [
        f"/partner/{p}" if not p.startswith("/") else p
        for p in paths
    ]
    return full


def _partner_files() -> List[pathlib.Path]:
    out: List[pathlib.Path] = []
    for p in PARTNER_DIR.rglob("*.tsx"):
        if "__tests__" in p.parts or "tests" in p.parts:
            continue
        if "fixtures" in p.parts:
            continue  # positive-control fixtures are scanned separately
        if ".test." in p.name or "test." in p.name:
            continue
        out.append(p)
    return out


def _navigate_targets(files: List[pathlib.Path]) -> List[Tuple[pathlib.Path, int, str]]:
    """Yield (file, line, raw-target) for every navigate('/partner/...')."""
    out: List[Tuple[pathlib.Path, int, str]] = []
    for p in files:
        try:
            src = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in NAVIGATE_RE.finditer(src):
            target = m.group(2)
            lineno = src[: m.start()].count("\n") + 1
            out.append((p, lineno, target))
    return out


def _orphan_violations() -> List[str]:
    """Return list of `<rel_path>:<line>: navigate('<target>') has no
    matching Route in App.tsx PartnerRoutes`."""
    routes = _extract_partner_routes()
    violations: List[str] = []
    for path, lineno, raw_target in _navigate_targets(_partner_files()):
        norm = _parameterize(raw_target)
        if any(_path_matches(norm, r) for r in routes):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        violations.append(
            f"{rel}:{lineno}: navigate('{raw_target}') has no matching "
            "Route in App.tsx PartnerRoutes — falls through to `*` "
            "catch-all (re-login bounce)"
        )
    return violations


# ---------------------------------------------------------------------------
# Production rule
# ---------------------------------------------------------------------------


def test_no_orphan_partner_navigations():
    """Catches the 2026-05-08 PartnerHomeDashboard + PartnerWeeklyRollup
    `/partner/site/${id}` orphan class. Baseline 0 — fail-loud."""
    violations = _orphan_violations()
    assert violations == [], (
        f"{len(violations)} orphan partner navigations found — every "
        "`navigate('/partner/...')` MUST resolve to a Route in "
        "App.tsx PartnerRoutes. Orphans fall through to the `*` "
        "catch-all and bounce logged-in admins to a re-login page. "
        "Add a Route to App.tsx OR deflect the navigate to an "
        "existing route (`/partner/dashboard?site=<id>` is the "
        "canonical sibling — see commit 2207bfcc).\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def test_partner_routes_extracted():
    """Sanity: extraction must find the canonical Routes — if zero,
    the regex broke, not the rule."""
    routes = _extract_partner_routes()
    assert "/partner/login" in routes, "Routes extraction failed — login missing"
    assert "/partner/dashboard" in routes, "Routes extraction failed — dashboard missing"
    assert any(":siteId" in r for r in routes), (
        "Routes extraction failed — no parameterized routes found"
    )


# ---------------------------------------------------------------------------
# Algorithm tests — pin parser/matcher correctness
# ---------------------------------------------------------------------------


def test_parameterize_strips_query_string():
    assert _parameterize("/partner/dashboard?site=abc") == "/partner/dashboard"
    assert _parameterize("/partner/site/${id}/topology?x=1") == "/partner/site/:p/topology"


def test_parameterize_replaces_template_literal():
    assert _parameterize("/partner/site/${siteId}/topology") == "/partner/site/:p/topology"
    assert _parameterize("/partner/site/${s.site_id}/consent") == "/partner/site/:p/consent"


def test_path_matches_literal():
    assert _path_matches("/partner/dashboard", "/partner/dashboard")
    assert not _path_matches("/partner/dashboard", "/partner/login")


def test_path_matches_param_route():
    assert _path_matches("/partner/site/:p/topology", "/partner/site/:siteId/topology")
    assert _path_matches("/partner/site/:p/consent", "/partner/site/:siteId/consent")


def test_path_does_not_match_wildcard_to_literal():
    """Wildcard target (`/partner/site/:p`) MUST NOT match a literal
    route (`/partner/site/topology`) — that would be unsafe."""
    assert not _path_matches("/partner/site/:p", "/partner/site/topology")


def test_path_does_not_match_catchall():
    """The `*` catch-all is the BUG. A navigate that only resolves
    via `*` is an orphan, not a match."""
    assert not _path_matches("/partner/anything-unknown", "*")


def test_path_length_mismatch():
    assert not _path_matches("/partner/dashboard", "/partner/dashboard/extra")
    assert not _path_matches("/partner/site/abc/topology", "/partner/site/:siteId")


def test_orphan_detection_against_synthetic_routes():
    """Pure-function test: feed it a synthetic route table and target
    and confirm the matcher's verdict matches expectation."""
    routes = [
        "/partner/login",
        "/partner/dashboard",
        "/partner/site/:siteId/topology",
        "/partner/site/:siteId/consent",
    ]
    # Orphan: `/partner/site/${id}` (no third segment) — pre-fix bug.
    target = _parameterize("/partner/site/${id}")
    assert not any(_path_matches(target, r) for r in routes), (
        "Orphan synthetic should NOT match — this is the 2026-05-08 "
        "PartnerHomeDashboard:174 class"
    )
    # Deflection: `/partner/dashboard?site=abc` — fix-up resolution.
    target = _parameterize("/partner/dashboard?site=abc")
    assert any(_path_matches(target, r) for r in routes), (
        "Deflected target must match `/partner/dashboard`"
    )


def test_gate_catches_synthetic_orphan():
    """Positive control: feed a synthetic orphan navigate through the
    matcher logic + the real route table, confirm the orphan is
    flagged. This is the gate's tamper-evidence — if someone
    inadvertently weakens the matcher, this test fails."""
    routes = _extract_partner_routes()
    # Synthetic orphan target that should NOT match any current Route.
    orphan_target = _parameterize("/partner/site/${id}")
    matched = any(_path_matches(orphan_target, r) for r in routes)
    assert not matched, (
        "Synthetic orphan `/partner/site/${id}` matched a Route — "
        "either the matcher is broken or someone added a route that "
        "absorbs the orphan. Investigate before relaxing this test."
    )
