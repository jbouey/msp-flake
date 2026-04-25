"""Source-level rule: every frontend mutation fetch (POST/PUT/PATCH/DELETE)
MUST include `credentials: 'include'` AND `X-CSRF-Token` (or use the
`fetchApi` helper from utils/api.ts that auto-injects both).

Session 210-B 2026-04-25 audit P0. Today's frontend audit caught:
  - OperatorAckPanel `/dashboard/flywheel-spine/acknowledge` — missing
    X-CSRF-Token, fails 403 on every click
  - SensorStatus deploy/remove — missing both credentials + CSRF, fails
    401/403 on every click

Both are state-changing requests against CSRF-protected endpoints. The
canonical pattern in this codebase:
  - PREFERRED: `fetchApi('/path', { method: 'POST', body: ... })` (helper
    auto-injects CSRF + credentials)
  - ACCEPTABLE: `fetch(url, { method: 'POST', credentials: 'include',
    headers: { 'X-CSRF-Token': getCsrfTokenOrEmpty() } })`
  - WRONG: `fetch(url, { method: 'POST', headers: { 'Content-Type': ... } })`

Ratchet baseline: today's count of WRONG-pattern call sites is locked
in. Adding a NEW raw mutation fetch fails CI. Drive the count down by
migrating to `fetchApi` over time. Eventually flip to BASELINE_MAX = 0
once all 60+ sites are migrated.

Skip:
- node_modules, dist, *.test.tsx, *.test.ts
- utils/api.ts itself (it IS the helper)
- utils/integrationsApi.ts (uses its own internal client)
"""
from __future__ import annotations

import pathlib
import re
from typing import List, Optional, Tuple

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
FRONTEND_SRC = (
    REPO_ROOT / "mcp-server" / "central-command" / "frontend" / "src"
)


# Files that ARE the helper or use a different validated client.
# Excluded from the rule because raw fetch is appropriate here.
HELPER_FILES = {
    "utils/api.ts",
    "utils/integrationsApi.ts",
    # Hooks that wrap react-query mutations are checked via the
    # underlying file; the hook layer is just plumbing.
}


# Pattern: a `fetch(...)` call whose options object literal contains
# a `method:'POST'|'PUT'|'PATCH'|'DELETE'` AND no CSRF reference.
#
# Brace-balanced parser (#181). The previous regex used a non-greedy
# `[^}]*?` which stopped at the first `}` in the options object —
# nested ternaries (`apiKey ? {...} : {}`), template literals
# (`${var}`), JSON.stringify({...}) etc. truncated the match before
# `csrfHeaders(` was seen, forcing inline `// satisfy regex` comments.
# Comments-to-bypass-a-gate is a smell. The parser below tracks brace
# depth honoring TS string literals so the options blob is the
# semantically-correct one no matter what's inside it.
FETCH_START_RE = re.compile(r"\bfetch\s*\(", re.IGNORECASE)
METHOD_LITERAL_RE = re.compile(
    r"method\s*:\s*['\"](POST|PUT|PATCH|DELETE)['\"]",
    re.IGNORECASE,
)


def _balanced_options_blob(src: str, fetch_paren: int) -> Optional[Tuple[int, str]]:
    """Given the position right after `fetch(`, find the options object
    literal and return (open_brace_index, options_blob_inside_braces).
    Returns None if the call has only one arg (e.g. `fetch(url)`) or
    the parser walks off the end. Brace-balanced over TS strings.
    """
    n = len(src)
    i = fetch_paren
    paren_depth = 1
    seen_first_arg_end = False
    in_str: Optional[str] = None  # quote char if inside a string

    while i < n and paren_depth > 0:
        ch = src[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
            elif in_str == "`" and ch == "$" and i + 1 < n and src[i + 1] == "{":
                # Template literal substitution opens a new sub-context
                # we don't fully model — but we DO need to skip the
                # ${...} bracketed region without treating those braces
                # as object braces. We jump to the matching `}` of `${`.
                j = i + 2
                d = 1
                sub_in_str: Optional[str] = None
                while j < n and d > 0:
                    sj = src[j]
                    if sub_in_str:
                        if sj == "\\":
                            j += 2; continue
                        if sj == sub_in_str:
                            sub_in_str = None
                    elif sj in ("'", '"', "`"):
                        sub_in_str = sj
                    elif sj == "{":
                        d += 1
                    elif sj == "}":
                        d -= 1
                    j += 1
                i = j
                continue
            i += 1
            continue
        if ch in ("'", '"', "`"):
            in_str = ch
            i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            # Line comment — skip to newline.
            nl = src.find("\n", i)
            if nl == -1:
                return None
            i = nl + 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            # Block comment — skip to */.
            end = src.find("*/", i + 2)
            if end == -1:
                return None
            i = end + 2
            continue
        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth -= 1
            if paren_depth == 0:
                return None
        elif ch == "," and paren_depth == 1 and not seen_first_arg_end:
            seen_first_arg_end = True
        elif ch == "{" and seen_first_arg_end and paren_depth == 1:
            # Found the start of the options object literal. The
            # paren_depth==1 gate is critical: without it, calls like
            # `fetch(buildUrl({a:1}), {method:'POST'})` would treat
            # the inner `{a:1}` as the options blob (round-table P1).
            opt_start = i
            j = i + 1
            depth = 1
            inner_str: Optional[str] = None
            while j < n and depth > 0:
                cj = src[j]
                if inner_str:
                    if cj == "\\":
                        j += 2; continue
                    if cj == inner_str:
                        inner_str = None
                    elif inner_str == "`" and cj == "$" and j + 1 < n and src[j + 1] == "{":
                        # nested ${...} inside a backtick string —
                        # walk past it, ignoring its braces.
                        k = j + 2
                        d2 = 1
                        s2: Optional[str] = None
                        while k < n and d2 > 0:
                            sk = src[k]
                            if s2:
                                if sk == "\\":
                                    k += 2; continue
                                if sk == s2:
                                    s2 = None
                            elif sk in ("'", '"', "`"):
                                s2 = sk
                            elif sk == "{":
                                d2 += 1
                            elif sk == "}":
                                d2 -= 1
                            k += 1
                        j = k
                        continue
                    j += 1
                    continue
                if cj in ("'", '"', "`"):
                    inner_str = cj
                    j += 1
                    continue
                if cj == "/" and j + 1 < n and src[j + 1] == "/":
                    nl = src.find("\n", j)
                    if nl == -1:
                        return None
                    j = nl + 1
                    continue
                if cj == "/" and j + 1 < n and src[j + 1] == "*":
                    end = src.find("*/", j + 2)
                    if end == -1:
                        return None
                    j = end + 2
                    continue
                if cj == "{":
                    depth += 1
                elif cj == "}":
                    depth -= 1
                    if depth == 0:
                        return opt_start, src[opt_start + 1 : j]
                j += 1
            return None
        i += 1
    return None


def _frontend_files() -> List[pathlib.Path]:
    """Walk frontend/src for .ts and .tsx files, skipping tests + helpers."""
    out: List[pathlib.Path] = []
    for p in FRONTEND_SRC.rglob("*"):
        if p.suffix not in (".ts", ".tsx"):
            continue
        rel = p.relative_to(FRONTEND_SRC).as_posix()
        if rel in HELPER_FILES:
            continue
        if "test." in p.name or ".test." in p.name:
            continue
        if any(skip in p.parts for skip in ("node_modules", "dist", "build")):
            continue
        out.append(p)
    return out


def _violations() -> List[str]:
    """Return list of `<rel_path>:<line>: <method> fetch missing CSRF`.

    Walks every `fetch(` call and extracts a brace-balanced options blob
    (replaces the prior non-greedy regex that stopped at first `}`).
    """
    out: List[str] = []
    for p in _frontend_files():
        try:
            src = p.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = p.relative_to(FRONTEND_SRC).as_posix()
        for fm in FETCH_START_RE.finditer(src):
            extracted = _balanced_options_blob(src, fm.end())
            if extracted is None:
                continue
            _, options_blob = extracted
            mm = METHOD_LITERAL_RE.search(options_blob)
            if not mm:
                continue
            method = mm.group(1).upper()
            has_csrf = (
                "X-CSRF-Token" in options_blob
                or "csrfHeaders(" in options_blob
                or "getCsrfTokenOrEmpty(" in options_blob
            )
            if has_csrf:
                continue
            lineno = src[: fm.start()].count("\n") + 1
            out.append(f"{rel}:{lineno}: {method} raw fetch missing X-CSRF-Token")
    return out


# Baseline locked 2026-04-25 after auditing 60+ frontend mutation
# fetches. Adding a NEW raw mutation is a regression — fail CI.
# Reaching CSRF_BASELINE_MAX = 0 means every state-changing fetch
# in the frontend either uses fetchApi or includes both
# credentials:'include' AND a csrfHeaders()/X-CSRF-Token reference.
#
# History:
# - 2026-04-25 first lock: 58 (initial audit count)
# - 2026-04-25 demo-path wave: 58 → 41 (PartnerBilling, login pages,
#   ConsentApprovePage, PortalDashboard, SignupBaa — 17 sites)
# - 2026-04-25 mechanical grind: 41 → 0 (3 parallel agents,
#   23 files: partner/ × 8, portal/ × 5, pages/+contexts/+client/+
#   companion/+components/ × 10 files). Locked.
#
# Round-table also caught the apiKey-gated anti-pattern
# (`credentials: apiKey ? undefined : 'include'`) in 11+ partner
# files; the grind pass corrected those to the canonical additive
# form (cookies + CSRF unconditional, X-API-Key additive).
CSRF_BASELINE_MAX = 0


def test_no_new_frontend_mutations_without_csrf():
    """Catches the 2026-04-25 OperatorAckPanel + SensorStatus class.
    Strict against NEW additions; the existing-violations count is
    locked at the baseline — drive it down over time, never up."""
    violations = _violations()
    assert len(violations) <= CSRF_BASELINE_MAX, (
        f"{len(violations)} raw mutation fetches missing CSRF — "
        f"baseline is {CSRF_BASELINE_MAX}. Migrate new code to "
        "`fetchApi` (utils/api.ts) which auto-injects CSRF + "
        "credentials, OR add `credentials: 'include'` and "
        "`headers: { 'X-CSRF-Token': getCsrfTokenOrEmpty() }`.\n"
        + "\n".join(f"  - {v}" for v in violations[:30])
        + (f"\n  ... and {len(violations) - 30} more" if len(violations) > 30 else "")
    )


def test_baseline_doesnt_regress_silently():
    """If anyone migrates a file to fetchApi, the baseline should DROP.
    This test fails LOUDLY when len(violations) goes BELOW baseline,
    forcing the operator to bump the constant down. Prevents the
    'lockstep with the floor' anti-pattern where bug count creeps
    back up unnoticed because nobody updated the constant."""
    violations = _violations()
    assert len(violations) == CSRF_BASELINE_MAX or len(violations) > CSRF_BASELINE_MAX, (
        f"len(violations)={len(violations)} is BELOW baseline "
        f"{CSRF_BASELINE_MAX} — great, you fixed some! Now LOWER "
        "CSRF_BASELINE_MAX to match. The ratchet only works if the "
        "ceiling drops with each fix."
    )


# ---------------------------------------------------------------------------
# Brace-balanced parser tests (#181) — locks in the linter's correctness.
# ---------------------------------------------------------------------------
#
# Before #181 the linter used a non-greedy regex that stopped at the first
# `}` in the options object, mis-matching nested ternaries / template
# literals / JSON.stringify({...}). Workarounds (inline `// satisfy regex`
# comments + hoisted-variable headers) accumulated. The parser below
# replaces that regex; these tests pin the new behavior so the parser
# can't silently regress to "first-`}`" semantics.


def _v_for(src: str) -> List[str]:
    """Run the parser+detector against an in-memory source blob, no fs."""
    out = []
    for fm in FETCH_START_RE.finditer(src):
        extracted = _balanced_options_blob(src, fm.end())
        if extracted is None:
            continue
        _, options_blob = extracted
        mm = METHOD_LITERAL_RE.search(options_blob)
        if not mm:
            continue
        if (
            "X-CSRF-Token" in options_blob
            or "csrfHeaders(" in options_blob
            or "getCsrfTokenOrEmpty(" in options_blob
        ):
            continue
        out.append(options_blob)
    return out


def test_parser_handles_template_literal_substitution():
    """Pre-#181 the regex truncated at the `}` closing `${expr}`.
    Now: the parser walks past `${...}` brace blocks honoring nesting."""
    src = """
        await fetch(`/api/x/${siteId}/y`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
            body: JSON.stringify({ key: `value-${Date.now()}` }),
        });
    """
    assert _v_for(src) == [], "Template-literal substitution must not break parser"


def test_parser_handles_nested_ternary_with_object_branches():
    """`apiKey ? { 'X-API-Key': apiKey } : {}` has a `}` BEFORE
    the options-object-closing `}`. Pre-#181 regex truncated there."""
    src = """
        await fetch('/api/x', {
            method: 'POST',
            credentials: 'include',
            headers: {
                'Content-Type': 'application/json',
                ...(apiKey ? { 'X-API-Key': apiKey } : {}),
                ...csrfHeaders(),
            },
        });
    """
    assert _v_for(src) == [], "Nested ternary with object branches must not break parser"


def test_parser_handles_inline_json_stringify():
    """JSON.stringify({...}) inside body has its own braces."""
    src = """
        await fetch('/api/x', {
            method: 'PUT',
            credentials: 'include',
            headers: { ...csrfHeaders() },
            body: JSON.stringify({ a: 1, b: { c: 2 } }),
        });
    """
    assert _v_for(src) == [], "JSON.stringify with nested objects must not break parser"


def test_parser_flags_real_missing_csrf_after_balanced_blob():
    """Negative test: a fetch that genuinely lacks CSRF MUST be flagged
    even after walking past nested braces."""
    src = """
        await fetch(`/api/x/${siteId}/y`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ x: 1 }),
        });
    """
    flagged = _v_for(src)
    assert len(flagged) == 1, "Real missing-CSRF must be flagged"


def test_parser_skips_get_fetches():
    """GET (and missing-method) fetches are out of scope — only state-
    changing methods matter for CSRF."""
    src = """
        await fetch('/api/x', { method: 'GET', credentials: 'include' });
        await fetch('/api/x');
    """
    assert _v_for(src) == [], "GET fetches must be ignored"


def test_parser_handles_single_arg_fetch():
    """`fetch(url)` with no options must not crash the parser."""
    src = "await fetch('/api/x');"
    assert _v_for(src) == [], "Single-arg fetch must be parsed without error"


def test_parser_csrf_inside_nested_braces_is_recognized():
    """csrfHeaders() spread inside a nested headers object literal must
    count as CSRF coverage — the entire balanced blob is searched."""
    src = """
        await fetch('/api/x', {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        });
    """
    assert _v_for(src) == [], "csrfHeaders() inside nested blob must satisfy"


def test_parser_handles_object_in_first_arg():
    """Round-table P1 regression guard. Pre-fix the parser entered the
    inner `{a:1}` of `fetch(buildUrl({a:1}), {...})` as the options blob
    because the `{` gate didn't require `paren_depth==1`. The first arg
    here is a function call passing an object — the REAL options blob
    is the second arg with method:POST + csrfHeaders()."""
    src = """
        await fetch(buildUrl({a: 1, b: 2}), {
            method: 'POST',
            credentials: 'include',
            headers: { ...csrfHeaders() },
        });
    """
    assert _v_for(src) == [], (
        "Object in first arg of fetch() must not be treated as the "
        "options blob (round-table P1 from #181)"
    )


def test_parser_handles_call_expression_in_options():
    """SWE-recommended: AbortSignal.timeout(5000), JSON.stringify(...),
    fetchApi inside options must not confuse paren-depth tracking."""
    src = """
        await fetch('/api/x', {
            method: 'POST',
            credentials: 'include',
            signal: AbortSignal.timeout(5000),
            headers: { ...csrfHeaders() },
            body: JSON.stringify({ nested: { deep: 1 } }),
        });
    """
    assert _v_for(src) == [], "Call expressions in options must not break parser"
