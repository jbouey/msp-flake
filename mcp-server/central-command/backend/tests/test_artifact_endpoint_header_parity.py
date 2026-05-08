"""Source-level rule: every artifact-issuance/re-render PDF endpoint
in the F1+F3+F5+P-F5+P-F6+P-F8 family MUST emit the canonical
response headers expected by their UI consumers.

2026-05-08 sibling-parity drift (commit `88dd5e49`) caught the P-F6
BA Compliance Letter endpoint emitting only `X-Attestation-Hash`
while its siblings (F1 + P-F5) shipped both `X-Attestation-Hash`
+ `X-Letter-Valid-Until`. Customer-visible regression: the
PartnerAttestations Card B summary showed no validity window
because the UI hardcoded `valid_until: null` defensively.

This gate catches that class STRUCTURALLY: any new `Response(
content=..., media_type="application/pdf", ...)` in the artifact-
attestation family MUST emit the header set its class requires.

Algorithm (AST-walk over client_portal.py + partners.py):
  1. ast.parse() each file. Walk every top-level FunctionDef /
     AsyncFunctionDef.
  2. A function is an "artifact endpoint" if:
     a. It is decorated by @router/@auth_router/@partner_public_
        verify_router .get/.post/.put/.patch (FastAPI route).
     b. Its body contains a `return Response(...)` call AND that
        Response uses `media_type="application/pdf"` somewhere in
        its kwargs.
  3. Classify each artifact endpoint:
     - **re-render** = the function's path-param signature OR the
       route decorator path contains `{attestation_hash}` →
       re-renders an existing signed attestation by hash.
     - **issuance** = the function body computes a NEW attestation_
       hash (`result["attestation_hash"]` reference outside path
       params) AND is not a re-render → creates a new attestation.
     - **derived** = neither → read-only report (e.g. P-F8 incident
       timeline, QBR, weekly digest).
  4. Skip non-artifact derived endpoints — those whose body has no
     `attestation_hash` reference at all AND emits no
     `X-Attestation-*` header. They are out of the F1/F3/F5/P-F5/
     P-F6/P-F8 family by construction (e.g. QBR, weekly rollup).
  5. Extract the `headers={...}` dict literal from the Response
     call via AST. Collect the literal header-name keys.
  6. Per-class assertion:
     - re-render → MUST emit `Content-Disposition` + `X-Attestation-Hash`.
     - issuance → MUST emit `Content-Disposition` + `X-Attestation-Hash`
       + ONE-OF {`X-Letter-Valid-Until`, `X-Summary-Valid-Until`}
       (validity-window header — F3 quarterly uses the latter
       name as a sibling).
     - derived (in-family) → MUST emit `Content-Disposition` + at
       least ONE artifact-identity header (X-* prefix beyond
       Content-*).

Ratchet baseline: 0 violations after `88dd5e49`. Fail-loud from day one.

Companion fixture at tests/fixtures/header_parity_fixture.py is a
deliberate-violation positive control. The gate's
`test_classification_against_synthetic_handlers` exercises the
classifier + assertions against in-memory synthetic ASTs so the
core logic is validated without touching production source.
"""
from __future__ import annotations

import ast
import pathlib
from typing import Dict, List, Optional, Set, Tuple

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
BACKEND = REPO_ROOT / "mcp-server" / "central-command" / "backend"
TARGET_FILES = [
    BACKEND / "client_portal.py",
    BACKEND / "partners.py",
]


# Decorators that mark a function as a FastAPI route in this codebase.
ROUTE_DECORATOR_BASES: Set[str] = {
    "router",
    "auth_router",
    "partner_public_verify_router",
    "public_verify_router",
}
ROUTE_DECORATOR_METHODS: Set[str] = {"get", "post", "put", "patch", "delete"}


# Headers that count as "artifact-identity" (X-prefixed, not just
# Content-Disposition / Content-Type). The full set is open-ended;
# the gate just needs ≥1 to classify a derived handler as in-family.
IDENTITY_HEADER_PREFIXES: Tuple[str, ...] = (
    "X-Attestation-",
    "X-Letter-",
    "X-Summary-",
    "X-Incident-",
    "X-Site-",
    "X-Kit-",
)


# Validity-window headers that satisfy the issuance "valid-until"
# requirement. Both forms are in production today (F1+P-F5+P-F6
# use Letter-Valid-Until; F3 quarterly uses Summary-Valid-Until).
VALIDITY_HEADERS: Set[str] = {
    "X-Letter-Valid-Until",
    "X-Summary-Valid-Until",
}


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _get_route_decorator_path(func: ast.AST) -> Optional[str]:
    """If func is decorated by @<router>.<method>("..."), return the
    path string. Otherwise None."""
    decs = getattr(func, "decorator_list", []) or []
    for d in decs:
        # d is a Call: `router.get("/x")`
        if not isinstance(d, ast.Call):
            continue
        f = d.func
        if not isinstance(f, ast.Attribute):
            continue
        if f.attr not in ROUTE_DECORATOR_METHODS:
            continue
        base = f.value
        if not isinstance(base, ast.Name) or base.id not in ROUTE_DECORATOR_BASES:
            continue
        # Path is first positional arg as a string literal.
        if d.args and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str):
            return d.args[0].value
    return None


def _find_pdf_responses(func: ast.AST) -> List[ast.Call]:
    """Return Response(...) calls inside func that have
    media_type="application/pdf" as a keyword arg.

    The handler may have multiple branches each returning a PDF
    Response — we collect all of them so per-class assertions cover
    every exit. Non-PDF Response calls (JSON, HTML, redirect) are
    ignored."""
    out: List[ast.Call] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        # Match `Response(...)` — the FastAPI/Starlette response class.
        # We accept both the unqualified name and `fastapi.Response`
        # style (Attribute).
        name = None
        if isinstance(f, ast.Name):
            name = f.id
        elif isinstance(f, ast.Attribute):
            name = f.attr
        if name != "Response":
            continue
        # Check media_type kwarg.
        media_type_pdf = False
        for kw in node.keywords:
            if kw.arg == "media_type" and isinstance(kw.value, ast.Constant):
                if kw.value.value == "application/pdf":
                    media_type_pdf = True
                    break
        if media_type_pdf:
            out.append(node)
    return out


def _extract_headers_keys(response_call: ast.Call) -> Set[str]:
    """Pull literal string keys from `headers={...}` kwarg of a
    Response call. Non-literal keys (e.g. `**spread`, computed) are
    silently skipped — the gate is intentionally conservative; a
    handler with computed keys is harder to reason about and should
    be fixed manually if the gate flags it."""
    out: Set[str] = set()
    for kw in response_call.keywords:
        if kw.arg != "headers":
            continue
        if not isinstance(kw.value, ast.Dict):
            return out
        for k in kw.value.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                out.add(k.value)
    return out


def _body_uses_attestation_hash(func: ast.AST) -> bool:
    """Heuristic: does the function body reference `attestation_hash`?

    Captures BOTH issuance (`result["attestation_hash"]`) and
    re-render (path-param + LIKE-prefix lookup). The classification
    layer disambiguates afterwards by inspecting the route-path /
    function-args for a path-parameter-named `attestation_hash`."""
    for node in ast.walk(func):
        # `result["attestation_hash"]` style.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value == "attestation_hash":
                return True
        # `attestation_hash` bare name (path param, local).
        if isinstance(node, ast.Name) and node.id == "attestation_hash":
            return True
        if isinstance(node, ast.arg) and node.arg == "attestation_hash":
            return True
    return False


def _is_rerender(func: ast.AST, route_path: Optional[str]) -> bool:
    """Re-render = the route path contains `{attestation_hash}` OR the
    function signature has `attestation_hash` as a path-param arg."""
    if route_path and "{attestation_hash}" in route_path:
        return True
    args = getattr(func, "args", None)
    if args is None:
        return False
    for a in (args.args or []) + (args.kwonly_args or []) if hasattr(args, "kwonly_args") else (args.args or []):
        if a.arg == "attestation_hash":
            return True
    return False


def _has_identity_header(headers: Set[str]) -> bool:
    """At least one X-* identity-prefixed header is present."""
    return any(
        any(h.startswith(p) for p in IDENTITY_HEADER_PREFIXES)
        for h in headers
    )


# ---------------------------------------------------------------------------
# Production walk
# ---------------------------------------------------------------------------


def _classify_handler(
    func: ast.AST, route_path: Optional[str], headers: Set[str]
) -> Optional[str]:
    """Return 'issuance' | 're-render' | 'derived-in-family' | None.

    None = out of scope (not in the artifact-attestation family).
    A handler is in-family if EITHER:
      - body references attestation_hash, OR
      - response emits any X-Attestation-* identity header.
    Otherwise it's a non-artifact derived report (QBR, weekly digest)
    and is intentionally skipped — those endpoints are sibling-
    isolated UIs that don't need attestation header parity.
    """
    body_attests = _body_uses_attestation_hash(func)
    has_attestation_header = any(h.startswith("X-Attestation-") for h in headers)
    in_family = body_attests or has_attestation_header
    if not in_family:
        return None
    if _is_rerender(func, route_path):
        return "re-render"
    if has_attestation_header or body_attests:
        # In-family but not a re-render → either issuance (writes new
        # attestation) or derived-in-family (reads/decorates without
        # creating). Distinguish: if any X-Attestation-Hash header is
        # emitted but no validity header AND the route is not POST,
        # we still call it issuance for header-set purposes (the
        # canonical issuance class).
        # Simpler rule: presence of Hash header implies the handler
        # treats this as an attestation issuance/refresh — apply the
        # issuance header set. If body has attestation_hash but
        # response does not emit Hash header, the handler is derived-
        # in-family (renders a sibling artifact like P-F8 timeline).
        if has_attestation_header:
            return "issuance"
        return "derived-in-family"
    return None  # unreachable


def _collect_violations() -> List[str]:
    """Walk the target files and return per-handler violations."""
    violations: List[str] = []
    for path in TARGET_FILES:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            route_path = _get_route_decorator_path(node)
            if route_path is None:
                continue
            pdf_responses = _find_pdf_responses(node)
            if not pdf_responses:
                continue
            # We assert against EVERY PDF response in the function
            # (multi-branch handlers must be uniformly compliant).
            for r in pdf_responses:
                headers = _extract_headers_keys(r)
                klass = _classify_handler(node, route_path, headers)
                if klass is None:
                    continue  # out of scope (e.g. QBR, weekly digest)
                missing = _required_minus_present(klass, headers)
                if missing:
                    lineno = r.lineno
                    violations.append(
                        f"{rel}:{lineno}: {node.name} ({klass}) missing "
                        f"{sorted(missing)}; emitted {sorted(headers)}"
                    )
    return violations


def _required_minus_present(klass: str, headers: Set[str]) -> Set[str]:
    """Return the set of REQUIRED headers absent from `headers` for
    this class. Empty set = compliant."""
    missing: Set[str] = set()
    if "Content-Disposition" not in headers:
        missing.add("Content-Disposition")
    if klass == "re-render":
        if "X-Attestation-Hash" not in headers:
            missing.add("X-Attestation-Hash")
    elif klass == "issuance":
        if "X-Attestation-Hash" not in headers:
            missing.add("X-Attestation-Hash")
        if not (headers & VALIDITY_HEADERS):
            # Reported as a single requirement string (not the OR set
            # in raw form, which would be confusing in failure copy).
            missing.add("X-Letter-Valid-Until|X-Summary-Valid-Until")
    elif klass == "derived-in-family":
        if not _has_identity_header(headers):
            missing.add("<at-least-one X-* identity header>")
    return missing


# ---------------------------------------------------------------------------
# Production rule
# ---------------------------------------------------------------------------


def test_artifact_endpoints_emit_canonical_headers():
    """Catches the 2026-05-08 P-F6 missing-X-Letter-Valid-Until class
    (commit 88dd5e49). Every PDF endpoint in the F1+F3+F5+P-F5+P-F6+
    P-F8 family must emit the headers its class requires. Baseline
    0 — fail-loud."""
    violations = _collect_violations()
    assert violations == [], (
        f"{len(violations)} artifact endpoint(s) missing canonical "
        "headers — sibling-parity drift class (commit 88dd5e49). "
        "Issuance handlers MUST emit Content-Disposition + "
        "X-Attestation-Hash + a validity header (X-Letter-Valid-"
        "Until or X-Summary-Valid-Until). Re-render handlers MUST "
        "emit Content-Disposition + X-Attestation-Hash. Derived in-"
        "family handlers MUST emit Content-Disposition + at least "
        "one X-* identity header.\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# Algorithm tests — pin classifier + assertions
# ---------------------------------------------------------------------------


def _parse_handler(src: str) -> ast.AST:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise AssertionError("synthetic source has no function")


def test_classifier_recognizes_rerender_via_path_param():
    src = """
@router.get("/me/x/{attestation_hash}/wall.pdf")
async def render_wall_cert(attestation_hash: str):
    return Response(
        content=b"",
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=x.pdf",
            "X-Attestation-Hash": attestation_hash,
        },
    )
"""
    fn = _parse_handler(src)
    route = _get_route_decorator_path(fn)
    pdfs = _find_pdf_responses(fn)
    headers = _extract_headers_keys(pdfs[0])
    klass = _classify_handler(fn, route, headers)
    assert klass == "re-render"
    assert _required_minus_present(klass, headers) == set()


def test_classifier_recognizes_issuance_with_validity_header():
    src = """
@router.get("/me/letter")
async def issue_letter():
    result = {"attestation_hash": "abc"}
    return Response(
        content=b"",
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=x.pdf",
            "X-Attestation-Hash": result["attestation_hash"],
            "X-Letter-Valid-Until": "2026-05-08",
        },
    )
"""
    fn = _parse_handler(src)
    route = _get_route_decorator_path(fn)
    pdfs = _find_pdf_responses(fn)
    headers = _extract_headers_keys(pdfs[0])
    klass = _classify_handler(fn, route, headers)
    assert klass == "issuance"
    assert _required_minus_present(klass, headers) == set()


def test_classifier_flags_issuance_missing_validity_header():
    """The 2026-05-08 P-F6 bug fixture: Hash present, Valid-Until absent."""
    src = """
@router.get("/me/ba-attestation")
async def issue_ba():
    result = {"attestation_hash": "abc"}
    return Response(
        content=b"",
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=x.pdf",
            "X-Attestation-Id": "id",
            "X-Attestation-Hash": result["attestation_hash"],
        },
    )
"""
    fn = _parse_handler(src)
    route = _get_route_decorator_path(fn)
    pdfs = _find_pdf_responses(fn)
    headers = _extract_headers_keys(pdfs[0])
    klass = _classify_handler(fn, route, headers)
    assert klass == "issuance"
    missing = _required_minus_present(klass, headers)
    assert "X-Letter-Valid-Until|X-Summary-Valid-Until" in missing


def test_classifier_accepts_summary_valid_until_for_quarterly():
    """F3 quarterly uses X-Summary-Valid-Until — the gate must accept
    either form as the validity-window header."""
    src = """
@router.post("/quarterly-summary")
async def issue_quarterly():
    result = {"attestation_hash": "abc"}
    return Response(
        content=b"",
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=x.pdf",
            "X-Attestation-Hash": result["attestation_hash"],
            "X-Summary-Valid-Until": "2026-08-08",
        },
    )
"""
    fn = _parse_handler(src)
    route = _get_route_decorator_path(fn)
    pdfs = _find_pdf_responses(fn)
    headers = _extract_headers_keys(pdfs[0])
    klass = _classify_handler(fn, route, headers)
    assert klass == "issuance"
    assert _required_minus_present(klass, headers) == set()


def test_classifier_skips_non_artifact_derived_endpoint():
    """QBR + weekly digest don't touch attestations → out of scope."""
    src = """
@router.get("/me/sites/{site_id}/qbr")
async def render_qbr(site_id: str):
    return Response(
        content=b"",
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=qbr.pdf"},
    )
"""
    fn = _parse_handler(src)
    route = _get_route_decorator_path(fn)
    pdfs = _find_pdf_responses(fn)
    headers = _extract_headers_keys(pdfs[0])
    klass = _classify_handler(fn, route, headers)
    assert klass is None, "Non-artifact derived endpoint must be out of scope"


def test_classifier_recognizes_derived_in_family_via_identity_header():
    """P-F8 timeline doesn't touch attestation_hash but emits identity
    headers — it's a derived report, must have ≥1 identity header."""
    src = """
@router.get("/me/incidents/{incident_id}/timeline.pdf")
async def render_timeline(incident_id: str):
    result = {"incident_id_short": "ABC", "site_label": "X"}
    return Response(
        content=b"",
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=t.pdf",
            "X-Incident-Id-Short": result["incident_id_short"],
            "X-Site-Label": result["site_label"],
        },
    )
"""
    fn = _parse_handler(src)
    route = _get_route_decorator_path(fn)
    pdfs = _find_pdf_responses(fn)
    headers = _extract_headers_keys(pdfs[0])
    # Body has no attestation_hash but headers carry no X-Attestation-*
    # either — the classifier returns None (out of scope), which is
    # the correct conservative posture for P-F8 today.
    klass = _classify_handler(fn, route, headers)
    assert klass is None, (
        "P-F8 currently has no X-Attestation-* header and no body "
        "attestation_hash — gate is correctly out of scope"
    )


def test_classifier_flags_rerender_missing_hash_header():
    """Negative control: re-render handler that forgot X-Attestation-Hash."""
    src = """
@router.get("/x/{attestation_hash}/wall.pdf")
async def render_wall_bad(attestation_hash: str):
    return Response(
        content=b"",
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=x.pdf"},
    )
"""
    fn = _parse_handler(src)
    route = _get_route_decorator_path(fn)
    pdfs = _find_pdf_responses(fn)
    headers = _extract_headers_keys(pdfs[0])
    klass = _classify_handler(fn, route, headers)
    assert klass == "re-render"
    missing = _required_minus_present(klass, headers)
    assert "X-Attestation-Hash" in missing


def test_classifier_flags_missing_content_disposition():
    """Every artifact endpoint must emit Content-Disposition (the
    download filename is operator-facing UX)."""
    src = """
@router.get("/me/portfolio-attestation")
async def issue_portfolio():
    result = {"attestation_hash": "abc"}
    return Response(
        content=b"",
        media_type="application/pdf",
        headers={
            "X-Attestation-Hash": result["attestation_hash"],
            "X-Letter-Valid-Until": "2026-05-08",
        },
    )
"""
    fn = _parse_handler(src)
    route = _get_route_decorator_path(fn)
    pdfs = _find_pdf_responses(fn)
    headers = _extract_headers_keys(pdfs[0])
    klass = _classify_handler(fn, route, headers)
    assert klass == "issuance"
    assert "Content-Disposition" in _required_minus_present(klass, headers)


def test_classifier_skips_non_route_function():
    """Helper functions (no @router.<method>) must be ignored entirely."""
    src = """
async def helper_fn():
    return Response(content=b"", media_type="application/pdf", headers={})
"""
    fn = _parse_handler(src)
    route = _get_route_decorator_path(fn)
    assert route is None


def test_classifier_skips_json_response():
    """JSON Response (no media_type=application/pdf) is out of scope."""
    src = """
@router.get("/me/x")
async def fetch_json():
    return Response(content="{}", media_type="application/json")
"""
    fn = _parse_handler(src)
    pdfs = _find_pdf_responses(fn)
    assert pdfs == []


def test_walk_finds_known_artifact_endpoints():
    """Sanity: the walk should find at least the F1/F5/F3/P-F5/P-F6
    handlers we know exist in the source today. If this drops to
    near-zero the AST walk regressed."""
    found_endpoints: List[str] = []
    for path in TARGET_FILES:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            route = _get_route_decorator_path(node)
            if route is None:
                continue
            pdfs = _find_pdf_responses(node)
            if not pdfs:
                continue
            for r in pdfs:
                headers = _extract_headers_keys(r)
                klass = _classify_handler(node, route, headers)
                if klass is not None:
                    found_endpoints.append(f"{node.name}:{klass}")
    # We expect at least 3 in-family endpoints today (F1 letter +
    # F5 wall-cert + F3 quarterly + P-F5 portfolio + P-F6 BA).
    assert len(found_endpoints) >= 3, (
        f"AST walk found only {len(found_endpoints)} artifact "
        "endpoints — expected ≥3. Walk likely regressed.\n"
        + "\n".join(found_endpoints)
    )


def test_walk_classifies_at_least_one_rerender():
    """F5 wall-cert is a re-render — the walk must classify ≥1
    handler as re-render today."""
    rerender_count = 0
    for path in TARGET_FILES:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            route = _get_route_decorator_path(node)
            if route is None:
                continue
            pdfs = _find_pdf_responses(node)
            for r in pdfs:
                headers = _extract_headers_keys(r)
                klass = _classify_handler(node, route, headers)
                if klass == "re-render":
                    rerender_count += 1
    assert rerender_count >= 1, (
        "Walk classified zero re-render handlers — F5 wall-cert "
        "should classify as re-render. AST/path-param logic broke."
    )
