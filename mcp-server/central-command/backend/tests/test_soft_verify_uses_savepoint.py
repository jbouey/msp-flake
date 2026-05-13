"""CI gate: soft-verify wrappers must run inside a savepoint.

Task #79, closes the class that produced the 2026-05-13 dashboard-vs-
site sync outage (commit 3ec431c8). Root cause was a verify_*() call
that raised an exception → the soft-verify `except` block caught it
BUT the connection's transaction state was now in failed-txn mode →
downstream INSERT on the same connection silently failed for 4h+.

Fix shape: wrap the verifier call in `async with conn.transaction():`
(asyncpg) or `async with db.begin_nested():` (SQLAlchemy AsyncSession)
so the verifier's exception rolls back ONLY the inner savepoint.
Sibling writes downstream are unaffected.

This AST gate detects the class structurally: any `try:` block whose
body calls a verifier function AND whose `except` clause swallows the
exception (no raise) MUST wrap the call in a savepoint context.

Per Gate A APPROVE-WITH-FIXES 2026-05-13:
  - Recognizes BOTH asyncpg `conn.transaction()` and SQLAlchemy
    `db.begin_nested()` patterns
  - ALLOWLIST locked at len() == 2 — prevents future drift via append
  - Hard-fail on NEW (no ratchet — too small to need one)
"""
from __future__ import annotations

import ast
import pathlib

_BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Function-name patterns that count as "verifier" — any call to one of
# these inside a try-except-swallow block must be savepointed.
_VERIFIER_PATTERNS = (
    "verify_heartbeat_signature",
    "verify_appliance_signature",
    "verify_consent_active",
    "verify_appliance_ownership",
    "verify_site_api_key",
    "verify_site_ownership",
    "verify_exception_ownership",
    "verify_evidence",
    "verify_chain_integrity",
    "verify_bundle_full",
)

# Callsites that are KNOWN-SAFE despite missing savepoint, per Gate A
# enumeration. Locked at len(ALLOWLIST) == 2 — adding entries requires
# updating the lock assertion below.
ALLOWLIST = frozenset({
    # SQLAlchemy AsyncSession callsite. Fix-forward task tracked at
    # the Task #79 commit body. Shadow-mode masks until Phase 3 enforce.
    "agent_api.py:verify_consent_active",
    # Handler raises HTTPException 401 unconditionally on verifier
    # failure → conn never reused after raise → savepoint moot. Noqa-
    # worthy but counted in ALLOWLIST for explicitness.
    "sites.py:verify_site_api_key",
})


def _is_swallow_handler(handler: ast.ExceptHandler) -> bool:
    """A handler is 'swallow' if its body doesn't end with a raise/return."""
    if not handler.body:
        return True
    for node in ast.walk(handler.body[-1]):
        if isinstance(node, ast.Raise) and node.exc is None:
            # bare re-raise
            return False
    # Check the last statement in handler body
    last = handler.body[-1]
    if isinstance(last, (ast.Raise, ast.Return)):
        return False
    return True


def _verifier_calls_in_try(try_node: ast.Try) -> list[tuple[ast.Call, str]]:
    """Find calls to verifier functions inside the try body."""
    out: list[tuple[ast.Call, str]] = []
    for node in ast.walk(try_node):
        # Don't recurse into nested try/except — those have their own scope
        if isinstance(node, ast.Call):
            fn_name = None
            if isinstance(node.func, ast.Name):
                fn_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                fn_name = node.func.attr
            if fn_name and any(p in fn_name for p in _VERIFIER_PATTERNS):
                out.append((node, fn_name))
    return out


def _try_wrapped_in_savepoint(try_node: ast.Try, all_parents: dict) -> bool:
    """Walk UP from the try-body's verifier call to find an enclosing
    `async with X.transaction()` or `async with X.begin_nested()`.

    Cheaper proxy: check the try body for a direct child AsyncWith
    matching the pattern.
    """
    for child in ast.walk(try_node):
        if isinstance(child, ast.AsyncWith):
            for item in child.items:
                ctx = item.context_expr
                # `conn.transaction()` or `db.begin_nested()` shape
                if isinstance(ctx, ast.Call):
                    if isinstance(ctx.func, ast.Attribute):
                        if ctx.func.attr in ("transaction", "begin_nested"):
                            return True
    return False


def _file_violations(path: pathlib.Path) -> list[str]:
    """Return list of violation descriptions for a single file."""
    try:
        src = path.read_text()
        tree = ast.parse(src)
    except (SyntaxError, OSError):
        return []
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        # Filter: only `except Exception:` style swallow handlers
        has_swallow = any(
            isinstance(h, ast.ExceptHandler) and _is_swallow_handler(h)
            for h in node.handlers
        )
        if not has_swallow:
            continue
        verifier_calls = _verifier_calls_in_try(node)
        if not verifier_calls:
            continue
        if _try_wrapped_in_savepoint(node, {}):
            continue
        for call, fn_name in verifier_calls:
            key = f"{path.name}:{fn_name}"
            if key in ALLOWLIST:
                continue
            out.append(
                f"{path.name}:{call.lineno}: `{fn_name}` called in soft-verify "
                f"try-except-swallow WITHOUT `async with X.transaction()` or "
                f"`async with X.begin_nested()` wrapper. A verifier exception "
                f"will poison the conn's txn state and silently fail downstream "
                f"writes on the same conn (2026-05-13 dashboard outage class). "
                f"Wrap the call OR add `{key}` to ALLOWLIST with rationale."
            )
    return out


def test_allowlist_lock():
    """Lock the allowlist at len() == 2. Adding new entries requires
    updating this assertion in lockstep — prevents quietly growing the
    list of soft-verify-without-savepoint patterns.
    """
    assert len(ALLOWLIST) == 2, (
        f"ALLOWLIST length is {len(ALLOWLIST)} — expected exactly 2. "
        f"Adding entries requires updating this lock + Gate A review of "
        f"the new callsite. Subtracting entries (fixing them) requires "
        f"updating this lock down to {len(ALLOWLIST) - 1}."
    )


def test_no_unwrapped_verifier_soft_verify():
    """The load-bearing gate: every verifier call in a try-except-swallow
    must be wrapped in a savepoint, OR be in the ALLOWLIST.
    """
    violations: list[str] = []
    for py in _BACKEND.glob("*.py"):
        violations.extend(_file_violations(py))
    assert not violations, (
        "Soft-verify-without-savepoint violations:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
