"""CI gate: every privileged @router endpoint must carry an auth dep.

Closes the class surfaced by the 2026-05-12 zero-auth audit fork
(.agent/digests/2026-05-12-zero-auth-endpoint-audit.md). Pre-gate,
the audit walked every @router.{verb} decorator under
mcp-server/central-command/backend/ and found 25 TRUE_LEAK endpoints
(3 P0, 6 P1, 16 P2) with no auth gate at all. Today's commits closed
the 3 P0s (bf4d1ac3, d9beabf6) + 5 P1s (c6eebf35). This gate pins
the remaining 11 as a RATCHET — each entry has a justification, and
the test fails if a NEW anonymous privileged endpoint lands or if a
ratchet entry is no longer anonymous (forcing the operator to delete
the entry once it's gated).

Same shape as test_no_silent_db_write_swallow.py + the privileged-
chain four-list lockstep gates.
"""
from __future__ import annotations

import ast
import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Symbols that count as authentication. Last-segment match against the
# inner of `Depends(...)`. Sourced from the audit fork's empirical
# walk of the codebase (the codebase has accumulated many auth
# helpers; this is the comprehensive list as of 2026-05-12).
AUTH_TOKENS = {
    "require_auth", "require_admin", "require_appliance_bearer",
    "require_appliance_bearer_full", "require_appliance_auth",
    "require_partner", "require_partner_role", "require_partner_admin",
    "require_partner_admin_role", "require_partner_owner",
    "require_client", "require_client_session", "require_client_admin",
    "require_client_auth", "require_client_owner", "require_client_user",
    "require_evidence_view_access", "require_portal_session",
    "require_internal", "require_internal_or_admin",
    "require_role", "require_owner", "require_signed_request",
    "require_bearer", "require_witness_bearer",
    "require_companion", "require_companion_session",
    "require_install_token", "_require_install_token",
    "require_csrf", "require_scrape_or_admin", "require_stripe",
    "require_active_partner_agreements", "require_super_admin",
    "require_session", "require_user", "require_billing_role",
    "require_tech_role", "require_operator",
    "require_signing_authority", "require_signed_appliance",
    "verify_token", "verify_admin_token", "verify_appliance_token",
    "verify_appliance_signature", "verify_partner_session",
    "verify_partner_token", "verify_client_session", "verify_client_token",
    "verify_admin_action", "verify_appliance", "verify_signature",
    "verify_alertmanager_token", "verify_magic_link",
    "verify_webhook_signature",
    "check_breakglass_token", "authenticate_partner", "authenticate_client",
    "enforce_admin", "sigauth_verify",
    "get_current_user", "get_current_admin", "get_current_partner",
    "get_current_client", "_require_partner_session", "_shared_auth",
}

# Endpoints / prefixes that are anonymous-by-design.
ALLOWED_ANON_PATHS = {
    "/", "/health", "/api/version",
    "/api/auth/login", "/api/auth/logout", "/api/auth/magic-link",
    "/api/auth/me",  # returns null when unauthed; not privileged
    "/api/auth/csrf-token",
    "/api/evidence/verify", "/api/evidence/public-key",
    "/api/billing/plans", "/api/billing/config", "/api/billing/calculate",
    # Provision-code is the secret in the URL; the QR endpoint renders
    # a provision token for installer-side onboarding.
    "/api/partners/provision/{provision_code}/qr",
}
ALLOWED_ANON_PREFIXES = (
    "/api/portal/",          # token-only client portal (?token=...)
    "/api/public/",          # explicitly public
    "/static/",
    "/.well-known/",
    "/api/oauth/", "/oauth/",  # OAuth callbacks + authorize pre-auth
    "/sitemap", "/robots", "/favicon",
    # Login + signup flows (verified per-endpoint anon-by-design in
    # the 2026-05-12 audit fork; auth happens INSIDE the flow).
    "/partner-auth/",        # Partner login flow (email/oauth/totp)
    # Pre-auth installer + provisioning. Appliances boot without
    # credentials; rekey / heartbeat happen via provision_code or MAC.
    "/api/provision/",
    "/api/install/",
    # Public verification surfaces. Auditors check attestations + BA
    # rosters + portfolio + quarterly summaries without login.
    "/api/verify/",
    "/api/iso/",             # Public CA bundle + transparency log
    # Public catalog of check types + HIPAA mapping. Marketing surface.
    "/api/check-catalog/",
    # Partner invite token-validate (magic-link-style).
    "/api/partner-invites/",
)

# Substrings that mark a path as login/signup/webhook (anon by design).
ANON_KEYWORDS = (
    "/login", "/logout", "/signup", "/register",
    "/magic-link", "/forgot-password", "/reset-password",
    "/webhook", "/callback", "/authorize",
    "/oauth", "/sso/authorize", "/sso/callback",
    "/rescue", "/provision/claim",
    "/verify-totp", "/auth/magic", "/auth/totp",
    "/change-email/confirm", "/change-email/cancel",
    "/claim",  # provision claim + partner claim
    "/auth/google", "/auth/microsoft",
    "/auth/providers", "/auth/email-login", "/auth/email-signup",
)

# RATCHET: known-anonymous privileged endpoints still on disk. Each
# entry justifies WHY it's still anonymous. Delete an entry only after
# (a) the endpoint has been auth-gated, (b) the corresponding
# require_* dep has been added, and (c) the deploy has succeeded.
# Adding a NEW entry requires a code-review-visible reason.
#
# 2026-05-12: 11 P2 audit entries all closed in the same session that
# landed this gate (P2 batch commit). Ratchet now empty — any new
# anonymous privileged endpoint trips the gate.
RATCHET_ANONYMOUS: dict = {}

_AUTH_RE = re.compile(r"Depends\(\s*([\w.]+)")

# Inline-auth patterns: handler body contains `await require_*(request)`
# or `<token>_hash`-based magic-link lookup, or HMAC compare for webhooks.
_INLINE_AUTH_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in sorted(AUTH_TOKENS, key=len, reverse=True)) + r")\b"
)
_HMAC_RE = re.compile(r"\bhmac\.compare_digest\b")
_MAGIC_LINK_RE = re.compile(r"\b(?:magic_link|token_hash|invite_token|claim_token|provision_token)\b")


def _has_auth_token(deps_unparse: str) -> bool:
    if not deps_unparse:
        return False
    for match in _AUTH_RE.finditer(deps_unparse):
        last_segment = match.group(1).strip().split(".")[-1]
        if last_segment in AUTH_TOKENS:
            return True
    for token in AUTH_TOKENS:
        if re.search(r"\b" + re.escape(token) + r"\b", deps_unparse):
            return True
    return False


def _handler_body_authed(node: ast.AST) -> bool:
    """True if the handler body contains inline auth: await require_*(request),
    HMAC token compare, or magic-link/invite token-hash lookup.
    """
    try:
        body_src = ast.unparse(node)
    except Exception:
        return False
    if _INLINE_AUTH_RE.search(body_src):
        return True
    if _HMAC_RE.search(body_src):
        return True
    if _MAGIC_LINK_RE.search(body_src):
        return True
    return False


def _is_anon_by_design(path: str) -> bool:
    if path in ALLOWED_ANON_PATHS:
        return True
    if any(path.startswith(prefix) for prefix in ALLOWED_ANON_PREFIXES):
        return True
    return any(keyword in path for keyword in ANON_KEYWORDS)


def _collect_router_info(tree: ast.AST) -> dict:
    """Map router variable name -> (prefix, has_router_level_auth)."""
    info = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        func_name = func.attr if isinstance(func, ast.Attribute) else (
            func.id if isinstance(func, ast.Name) else ""
        )
        if func_name != "APIRouter":
            continue
        prefix = ""
        deps_unparse = ""
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                prefix = kw.value.value or ""
            elif kw.arg == "dependencies":
                try:
                    deps_unparse = ast.unparse(kw.value)
                except Exception:
                    deps_unparse = ""
        has_auth = _has_auth_token(deps_unparse)
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                info[tgt.id] = (prefix, has_auth)
    return info


def _walk_endpoints():
    """Yield (file_relative, method, path_full, handler_has_auth, router_has_auth)."""
    for path in sorted(_BACKEND.rglob("*.py")):
        if any(seg in path.parts for seg in (
            "tests", "migrations", "substrate_runbooks", "templates",
            "__pycache__", "scripts", "venv",
        )):
            continue
        try:
            src = path.read_text()
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue

        router_info = _collect_router_info(tree)

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                if not isinstance(dec.func, ast.Attribute):
                    continue
                method = dec.func.attr
                if method not in ("get", "post", "put", "patch", "delete"):
                    continue
                if not isinstance(dec.func.value, ast.Name):
                    continue
                router_name = dec.func.value.id
                rprefix, rauth = router_info.get(router_name, ("", False))

                route_path = ""
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    route_path = dec.args[0].value or ""
                full_path = (rprefix or "") + (route_path or "")

                try:
                    sig_str = ast.unparse(node.args)
                except Exception:
                    sig_str = ""
                handler_auth = _has_auth_token(sig_str)
                # Body-scan: a handler may receive `Request` and auth
                # inline via `await require_*(request)` (login flows,
                # magic-link/invite/claim flows, webhook HMAC compare).
                if not handler_auth and not rauth:
                    handler_auth = _handler_body_authed(node)

                yield (path.name, method, full_path, handler_auth, rauth)


def test_no_new_anonymous_privileged_endpoints():
    """Every @router endpoint must have an auth dep OR be in the
    ALLOWED_ANON list OR (transitionally) be in RATCHET_ANONYMOUS.
    """
    violations: list[str] = []
    ratchet_hits: set = set()

    for file_name, method, full_path, handler_auth, router_auth in _walk_endpoints():
        if handler_auth or router_auth:
            continue
        if _is_anon_by_design(full_path):
            continue
        key = (file_name, full_path, method)
        if key in RATCHET_ANONYMOUS:
            ratchet_hits.add(key)
            continue
        violations.append(
            f"{file_name} {method.upper()} {full_path} — no auth dep "
            "(handler sig + router-level both checked). Add Depends("
            "require_*) or — if anonymous by design — add the path to "
            "ALLOWED_ANON_PATHS / ALLOWED_ANON_PREFIXES / ANON_KEYWORDS. "
            "Transitional: add to RATCHET_ANONYMOUS with reason."
        )

    assert not violations, (
        f"{len(violations)} new anonymous privileged endpoint(s):\n  "
        + "\n  ".join(violations)
    )


def test_ratchet_entries_are_still_anonymous():
    """A RATCHET entry that is no longer anonymous (got gated) must be
    REMOVED — otherwise the ratchet stops being a ratchet. This test
    forces the operator to delete the entry when the endpoint is fixed.
    """
    stale: list[str] = []
    seen_anon: set = set()
    for file_name, method, full_path, handler_auth, router_auth in _walk_endpoints():
        key = (file_name, full_path, method)
        if key not in RATCHET_ANONYMOUS:
            continue
        if handler_auth or router_auth:
            stale.append(
                f"{file_name} {method.upper()} {full_path} is now auth-gated "
                "— delete its RATCHET_ANONYMOUS entry."
            )
        else:
            seen_anon.add(key)
    missing = set(RATCHET_ANONYMOUS) - seen_anon
    if missing:
        stale.extend([
            f"RATCHET entry has no matching @router decorator on disk: "
            f"{file_name} {method.upper()} {full_path}"
            for (file_name, full_path, method) in missing
        ])
    assert not stale, "Stale RATCHET_ANONYMOUS entries:\n  " + "\n  ".join(stale)
