"""Regression gate — every read endpoint in `evidence_chain.py` MUST
declare an auth dependency.

The 2026-05-09 multi-tenant Phase 0 audit
(`audit/multi-tenant-phase0-inventory-2026-05-09.md` F-P0-1) caught 5
GET endpoints that shipped without `Depends(require_evidence_view_access)`,
leaking per-site chain metadata to ANY caller (no auth, no cookie, no
token). Sibling endpoints in the same router DID use the helper —
sibling-parity drift.

Patched in commit `10a82b73`:
    GET /sites/{site_id}/signing-status
    GET /sites/{site_id}/summary
    GET /ots/status/{site_id}
    GET /sites/{site_id}/compliance-packet
    GET /organizations/{org_id}/bundle

This gate keeps them fixed AND blocks the next sibling-parity drift
before it lands. AST scan: every `@router.{get,post,put,patch,delete}`
decorated function in `evidence_chain.py` must take a parameter whose
default is `Depends(<auth helper>)` for one of:

    require_evidence_view_access
    require_appliance_bearer
    require_admin
    require_auth
    require_partner_role / require_partner / require_operator

If a route is intentionally unauthenticated (e.g. a `/public/...`
verify endpoint that takes a hash and is rate-limited at the edge),
add it to UNAUTH_ALLOWLIST with a why-justified comment.

Sibling pattern: `tests/test_no_partner_mutation_uses_bare_require_partner.py`
+ `tests/test_partner_endpoints_filter_partner_id.py` (latter
shipped 2026-05-09 in the partner-RLS migration, P1-2).
"""
from __future__ import annotations

import ast
import pathlib

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_TARGET = _BACKEND / "evidence_chain.py"

# Routes that are intentionally unauthenticated. Each entry must
# include a why-justified rationale that survives code review.
#
# IMPORTANT: ALL public verify endpoints are auditor-facing by
# design. The customer-facing tamper-evidence promise depends on
# anyone-with-a-kit being able to verify. Adding new verify
# endpoints to this allowlist requires round-table sign-off
# (Carol + Steve at minimum) — every entry expands the public
# attack surface even if only by a fingerprint-leak vector.
UNAUTH_ALLOWLIST: dict[str, str] = {
    # `/public-key` returns the server's Ed25519 verification key —
    # by design public so anyone can verify any signed bundle. Not
    # a tenant-scoped endpoint; no per-customer data leaks.
    "get_public_key": (
        "/public-key returns the server's Ed25519 verification key "
        "by design — required for offline verifiers"
    ),
    # `/sites/{site_id}/verify/{bundle_id}` — bundle-by-id verifier.
    # Caller MUST know bundle_id (auditor-kit ships them). Returns
    # only valid/invalid + ots_status — same info already in the
    # public auditor-kit ZIP the caller used to find the bundle_id.
    "verify_evidence": (
        "/sites/{site_id}/verify/{bundle_id} is the public bundle "
        "verifier — auditor-kit consumers verify bundle_ids they "
        "already have. No new info leaked."
    ),
    # `/sites/{id}/verify-merkle/{bundle_id}` — Merkle path verifier.
    # Same rationale as above: caller already has bundle_id from kit.
    "verify_merkle_proof_endpoint": (
        "Merkle proof verifier — auditor-kit consumers run this "
        "with bundle_ids they already received. Public by design."
    ),
    # `/{bundle_id}/verify` — full bundle verifier (sibling of
    # verify_evidence, lives at the org-level path).
    "verify_bundle_full": (
        "/{bundle_id}/verify is the public verifier endpoint — "
        "auditor-kit includes pre-signed bundle_ids, anyone with "
        "a kit can verify any bundle. No new info leaked."
    ),
    # Session 220 task #120 PR-A (2026-05-12): verify_ots_bitcoin
    # handler deleted (Gate A v2 P0-2: unauthenticated state mutation
    # + blockstream.info DoS amplification + zero frontend callers).
    # No allowlist entry needed — the handler no longer exists.
    # `/verify-batch` — batch verifier; same shape as the others.
    "verify_batch": (
        "Batch verifier — accepts caller-supplied bundle_ids + "
        "proofs, runs the same verification logic as the per-bundle "
        "endpoint. Public by design."
    ),
    # `/chain-health` — server-wide aggregate health (no site_id).
    # Returns total bundle count + OTS posture distribution.
    "get_chain_health": (
        "/chain-health returns aggregate counts only (no site-level "
        "data); used by status pages + uptime monitors"
    ),
}

# Auth dependencies that count as "authenticated" for this gate.
AUTH_HELPERS = {
    "require_evidence_view_access",
    "require_appliance_bearer",
    "require_admin",
    "require_auth",
    "require_partner",
    "require_partner_role",
    "require_operator",
    "require_scrape_or_admin",  # for prom-style endpoints
}

_ROUTE_DECORATORS = {"get", "post", "put", "patch", "delete"}


def _function_has_auth_dep(fn: ast.AST) -> bool:
    """Walk a FunctionDef / AsyncFunctionDef and return True if any
    parameter's default is a `Depends(<auth-helper>)` call."""
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    args = fn.args
    # All possible defaults: positional + kw-only
    defaults = list(args.defaults) + list(args.kw_defaults)
    for d in defaults:
        if d is None:
            continue
        # Match Depends(<helper>) or Depends(<helper>())
        if not isinstance(d, ast.Call):
            continue
        f = d.func
        if isinstance(f, ast.Name) and f.id == "Depends":
            if not d.args:
                continue
            arg0 = d.args[0]
            if isinstance(arg0, ast.Name) and arg0.id in AUTH_HELPERS:
                return True
            # Depends(require_partner_role("admin"))
            if isinstance(arg0, ast.Call) and isinstance(arg0.func, ast.Name) \
                    and arg0.func.id in AUTH_HELPERS:
                return True
            # Depends(auth_module.require_auth)
            if isinstance(arg0, ast.Attribute) and arg0.attr in AUTH_HELPERS:
                return True
    return False


def _route_decorator_method(node: ast.AST) -> str | None:
    """If the function has a @router.<method>(...) decorator, return
    the method name. Else None."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    for d in node.decorator_list:
        if not isinstance(d, ast.Call):
            continue
        f = d.func
        if isinstance(f, ast.Attribute) and f.attr in _ROUTE_DECORATORS:
            return f.attr
    return None


def _collect_unauth_routes() -> list[str]:
    """Return file:line list of routes in evidence_chain.py that lack
    an auth Depends()."""
    src = _TARGET.read_text()
    tree = ast.parse(src)
    out: list[str] = []
    for node in ast.walk(tree):
        method = _route_decorator_method(node)
        if method is None:
            continue
        name = node.name
        if name in UNAUTH_ALLOWLIST:
            continue
        if _function_has_auth_dep(node):
            continue
        out.append(f"evidence_chain.py:{node.lineno} {method.upper()} `{name}`")
    return out


def test_every_evidence_route_has_auth():
    """Baseline 0 — every router-decorated function in evidence_chain.py
    declares an auth Depends() OR is in UNAUTH_ALLOWLIST with rationale.
    """
    violations = _collect_unauth_routes()
    assert not violations, (
        "evidence_chain.py route(s) without auth dependency. "
        "Per the 2026-05-09 multi-tenant Phase 0 audit (F-P0-1), "
        "5 unauth GETs leaked chain metadata to anyone on the open "
        "internet. Add `Depends(require_evidence_view_access)` (or "
        "another helper from AUTH_HELPERS) to the function signature, "
        "OR if the route is intentionally public, add the function "
        "name to `UNAUTH_ALLOWLIST` with a why-justified rationale.\n\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def test_synthetic_unauth_route_caught():
    """Positive control — a synthetic route without auth must be flagged."""
    src = '''
from fastapi import APIRouter, Depends
router = APIRouter()

@router.get("/sites/{site_id}/leaky")
async def leaky_endpoint(site_id: str, db = Depends(get_db)):
    return {"data": "leaked"}
'''
    tree = ast.parse(src)
    found_unauth = False
    for node in ast.walk(tree):
        method = _route_decorator_method(node)
        if method is None:
            continue
        if not _function_has_auth_dep(node):
            found_unauth = True
    assert found_unauth, "matcher should flag the synthetic unauth route"


def test_synthetic_authed_route_passes():
    """Negative control — a synthetic route WITH auth must NOT be flagged."""
    src = '''
from fastapi import APIRouter, Depends
router = APIRouter()

@router.get("/sites/{site_id}/safe")
async def safe_endpoint(
    site_id: str,
    _auth = Depends(require_evidence_view_access),
):
    return {"data": "ok"}
'''
    tree = ast.parse(src)
    for node in ast.walk(tree):
        method = _route_decorator_method(node)
        if method is None:
            continue
        assert _function_has_auth_dep(node), (
            "matcher should NOT flag a route with require_evidence_view_access"
        )


def test_audit_named_routes_are_now_authed():
    """Pin the 5 audit-named routes: each MUST have the auth dep.
    If any regresses, fail loudly with the specific route name."""
    src = _TARGET.read_text()
    tree = ast.parse(src)
    audit_routes = {
        "get_signing_status",
        "get_evidence_summary",
        "get_ots_status",
        "generate_compliance_packet",
        "get_org_evidence_bundle",
    }
    found: dict[str, bool] = {n: False for n in audit_routes}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in audit_routes:
            continue
        if _function_has_auth_dep(node):
            found[node.name] = True
    for name, ok in found.items():
        assert ok, (
            f"audit-named route `{name}` is missing its auth dep — "
            f"regression of the 2026-05-09 multi-tenant Phase 0 P0-1 "
            f"fix (commit 10a82b73). See audit deliverable."
        )
