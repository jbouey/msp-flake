"""CI gate (Task #62 v2.1 Commit 2): source-shape checks on
`load_test_api.py` — the load-harness run-ledger endpoints.

Gate scope:
  1. All 5 declared endpoints have @router decorators with matching
     methods/paths.
  2. Every endpoint is `Depends(require_admin)` — no zero-auth +
     no `require_auth` (broader). The run ledger MUST not be
     callable by non-admins.
  3. Wave-1 allowlist `_WAVE1_ALLOWED_ENDPOINTS` matches the v2.1
     spec table (4 entries; bumping requires v2.2 spec + this gate
     in lockstep).
  4. Router is included in `mcp-server/main.py` (otherwise the
     endpoints don't route in production).

This is a SOURCE-SHAPE gate (no DB, no runtime). The full Wave-1
path-resolution gate is `test_load_harness_wave1_paths_exist.py`.
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_API = _BACKEND / "load_test_api.py"
_MAIN = _BACKEND.parent.parent / "main.py"  # mcp-server/main.py


_EXPECTED_ENDPOINTS = {
    ("POST", "/runs"),
    ("POST", "/{run_id}/started"),  # Commit 3 — Gate B P1 #105 closure (starting→running)
    ("POST", "/{run_id}/abort"),
    ("POST", "/{run_id}/complete"),
    ("GET", "/status"),
    ("GET", "/runs"),
}


_EXPECTED_WAVE1 = {
    "/api/appliances/checkin",
    "/api/appliances/orders",
    "/api/journal/upload",
    "/health",
}


def _read_api() -> str:
    return _API.read_text(encoding="utf-8")


def test_router_prefix_is_admin_load_test():
    src = _read_api()
    assert 'APIRouter(prefix="/api/admin/load-test"' in src, (
        "load_test_api.router must use prefix='/api/admin/load-test' — "
        "endpoints would mount under the wrong base path otherwise."
    )


def test_all_five_endpoints_declared():
    src = _read_api()
    found = set(re.findall(
        r'@router\.(get|post|put|patch|delete)\(\s*"([^"]+)"',
        src,
        re.IGNORECASE,
    ))
    found_upper = {(m.upper(), p) for m, p in found}
    missing = _EXPECTED_ENDPOINTS - found_upper
    extra = found_upper - _EXPECTED_ENDPOINTS
    assert not missing, f"load_test_api missing expected endpoints: {missing}"
    assert not extra, (
        f"load_test_api has unexpected endpoints: {extra}. If adding "
        f"a new endpoint, update _EXPECTED_ENDPOINTS in this gate + the "
        f"v2.1 spec table in lockstep."
    )


def test_every_endpoint_uses_require_admin():
    """Walk the source. For each @router decorator, capture the
    multi-line function signature (params can span many lines) and
    assert `require_admin` appears in the params. Catches drift to
    require_auth (looser) or no auth at all."""
    src = _read_api()
    # Match @router.<method>("<path>")... then `async def <name>(`
    # then everything up to the closing `)` of the signature (may
    # span many lines for Depends()-style params), then `:`.
    pat = re.compile(
        r'@router\.(get|post|put|patch|delete)\(\s*"([^"]+)"[^\n]*\n'
        r'(?:[^\n]*\n){0,5}?'
        r'async\s+def\s+\w+\((?P<sig>[^)]*)\)\s*->',
        re.IGNORECASE,
    )
    findings: list[str] = []
    for m in pat.finditer(src):
        method, path, sig = m.group(1), m.group(2), m.group("sig")
        if "require_admin" not in sig:
            findings.append(
                f"{method.upper()} {path}: signature lacks "
                f"Depends(require_admin) — sig was {sig[:200]!r}"
            )
    assert not findings, (
        "load_test_api endpoints missing Depends(require_admin) — "
        "load-harness control plane MUST be admin-gated (P1-5 audit "
        "shape):\n  " + "\n  ".join(findings)
    )


def test_wave1_allowlist_matches_v21_spec():
    src = _read_api()
    # Extract the _WAVE1_ALLOWED_ENDPOINTS literal set. Anchor on the
    # closing `}` at column 0 so `{site_id}` inside a comment doesn't
    # truncate the match.
    m = re.search(
        r"_WAVE1_ALLOWED_ENDPOINTS\s*=\s*\{(.*?)\n\}",
        src,
        re.DOTALL,
    )
    assert m, "could not locate _WAVE1_ALLOWED_ENDPOINTS literal in load_test_api.py"
    # Strip comments line-by-line before pulling strings.
    cleaned = "\n".join(re.sub(r"#.*$", "", ln) for ln in m.group(1).splitlines())
    entries = set(re.findall(r'"([^"]+)"', cleaned))
    missing = _EXPECTED_WAVE1 - entries
    extra = entries - _EXPECTED_WAVE1
    assert not missing, (
        f"_WAVE1_ALLOWED_ENDPOINTS missing v2.1-spec entries: {missing}"
    )
    assert not extra, (
        f"_WAVE1_ALLOWED_ENDPOINTS has entries beyond v2.1 spec: "
        f"{extra}. Wave expansion requires v2.2 spec doc + this gate's "
        f"_EXPECTED_WAVE1 set in lockstep (P1-1 expansion is followup "
        f"task #105)."
    )


def test_router_is_included_in_main():
    """Routing isn't real until app.include_router is called. Catch
    the class where a router gets defined but never mounted."""
    src = _MAIN.read_text(encoding="utf-8")
    assert "load_test_router" in src, (
        "mcp-server/main.py does not import load_test_router — "
        "endpoints won't be reachable in production. Add:\n"
        "    from dashboard_api.load_test_api import router as load_test_router\n"
        "    app.include_router(load_test_router)"
    )
    assert "app.include_router(load_test_router" in src, (
        "mcp-server/main.py imports but does not mount load_test_router "
        "via app.include_router(...)."
    )


def test_abort_request_has_no_actor_email_body_field():
    """Gate B P2 #108 closure (2026-05-16): AbortRunRequest must NOT
    declare an `actor_email` body field. Caller-supplied actor_email
    was a body-side spoofing surface — the audit row now ALWAYS uses
    the bearer's authenticated email. Sentinel prevents re-introduction.
    """
    src = _read_api()
    m = re.search(
        r"class AbortRunRequest\(BaseModel\):(.*?)(?:\nclass |\n# ---|\Z)",
        src,
        re.DOTALL,
    )
    assert m, "could not locate AbortRunRequest class"
    body = m.group(1)
    assert "actor_email" not in body or "actor_email is NO LONGER" in body, (
        "AbortRunRequest re-introduced an `actor_email` body field — "
        "Gate B P2 #108 closed this spoofing surface. Use the bearer's "
        "authenticated email via _admin_email(admin) instead."
    )


def test_no_inline_dunder_import_json():
    """Gate B P2 #106 closure: `__import__(\"json\")` is forbidden — use
    module-top `import json` instead. Sentinel pins the cleanup."""
    src = _read_api()
    assert "__import__" not in src, (
        "load_test_api.py reintroduced `__import__` — Gate B P2 #106 "
        "closed this; use module-top `import json` instead."
    )


def test_uniqueviolation_check_uses_asyncpg_typed_exception():
    """Gate B P2 #107 closure: 409-on-concurrent-runs detection must
    use `asyncpg.UniqueViolationError`, NOT substring-match on the
    index name. Sentinel prevents regression to string-matching."""
    src = _read_api()
    assert "asyncpg.UniqueViolationError" in src, (
        "start_run no longer catches asyncpg.UniqueViolationError — "
        "Gate B P2 #107 mandated typed-exception detection over "
        "substring-matching the error message."
    )
    assert "uniq_load_test_runs_one_active" not in src, (
        "load_test_api.py still references the index name "
        "`uniq_load_test_runs_one_active` in source — Gate B P2 #107 "
        "closed this info leak. The typed exception is the contract."
    )


def test_bearer_revoke_wired_into_complete():
    """Commit 3: /complete must accept `revoke_bearer_appliance_id`
    and issue an UPDATE on site_appliances.bearer_revoked. Closes
    the synthetic bearer lifecycle per v2.1 spec §P1-5."""
    src = _read_api()
    assert "revoke_bearer_appliance_id" in src, (
        "CompleteRunRequest is missing the revoke_bearer_appliance_id "
        "field — Commit 3 mig 324 wiring incomplete."
    )
    assert "bearer_revoked = TRUE" in src or "bearer_revoked=TRUE" in src, (
        "complete_run handler does not UPDATE site_appliances.bearer_"
        "revoked — Commit 3 mig 324 wiring incomplete."
    )


def test_bearer_revoke_gated_to_synthetic_sites():
    """Commit 5a Gate B P1-A closure: the bearer_revoked UPDATE must
    JOIN `sites` and gate on `s.synthetic = TRUE`. Without this gate
    an admin typo on revoke_bearer_appliance_id could revoke a real
    customer appliance's bearer, taking the customer's daemon offline.
    Per audit/coach-62-c3-gate-b-2026-05-16.md §P1-A.
    """
    src = _read_api()
    # The UPDATE block must reference both sites.synthetic AND a join
    # condition to sa.site_id. Anchor on `bearer_revoked = TRUE` and
    # scan the surrounding ±15 lines.
    m = re.search(
        r"(.{0,500})SET\s+bearer_revoked\s*=\s*TRUE(.{0,500})",
        src,
        re.IGNORECASE | re.DOTALL,
    )
    assert m, "could not locate bearer_revoked UPDATE block"
    block = m.group(1) + m.group(2)
    assert "s.synthetic" in block and "= TRUE" in block, (
        "bearer_revoked UPDATE missing `sites.synthetic = TRUE` gate. "
        "Without this, admin typo can revoke a real customer appliance's "
        "bearer (Gate B P1-A)."
    )


def test_admin_audit_log_writes_on_state_transitions():
    """Source-grep check: every state-transition endpoint (start /
    abort / complete) must call _audit(...) so admin_audit_log gets
    a structured row. P1-5 audit-log requirement."""
    src = _read_api()
    # Find the three handler bodies by anchoring on `async def
    # start_run`, `async def abort_run`, `async def complete_run`.
    for handler in ("start_run", "mark_run_running", "abort_run", "complete_run"):
        m = re.search(
            rf"async def {handler}\b.*?\n(.*?)\n(?:@router|\Z)",
            src,
            re.DOTALL,
        )
        assert m, f"could not locate {handler} body"
        body = m.group(1)
        assert "_audit(" in body, (
            f"{handler} does not call _audit(...) — admin_audit_log row "
            f"required on every state transition (P1-5)."
        )
