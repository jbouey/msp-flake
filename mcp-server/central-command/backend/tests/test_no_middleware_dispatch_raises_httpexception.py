"""Pin gate — no `BaseHTTPMiddleware.dispatch` method may `raise
HTTPException`. Use `return JSONResponse(...)` instead.

Session 220 task #121 (2026-05-11). Real prod bug:
`csrf.py:190` did `raise HTTPException(status_code=403, detail="...")`
inside `CSRFMiddleware(BaseHTTPMiddleware).dispatch`. Starlette wraps
dispatch in `anyio.create_task_group()`; raised exceptions surface
as `BaseExceptionGroup` to FastAPI's exception_handler chain, which
doesn't match `HTTPException` and falls through to the generic 500
path. Customer-visible: every CSRF rejection returned 500 instead
of 403 with actionable copy.

Canonical pattern (already followed by `rate_limiter.py:253/265/277`):
return a `JSONResponse(status_code=N, content={...})` directly
from dispatch.

This gate prevents the regression class structurally. Catches:
1. Existing middleware adds a new `raise HTTPException` in dispatch
2. New middleware module copies the broken pattern

SCOPE: any class inheriting from `BaseHTTPMiddleware` whose
`dispatch` method body contains `raise HTTPException(...)`.

Allowlist mechanism: `# noqa: middleware-raise-allowed` comment on
the raise line — only valid for documented cases where the
intended response IS a 500 (operator error masking, etc).

Sibling pattern:
  - `tests/test_no_silent_db_write_swallow.py` (AST ratchet)
  - `tests/test_escalate_rule_check_type_drift.py` (line-window scan)
"""
from __future__ import annotations

import ast
import pathlib

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent.parent.parent

# Backend directories to scan for middleware definitions.
_SCAN_DIRS = [
    _BACKEND,
    _REPO / "mcp-server",
]


def _is_basehttp_middleware(class_node: ast.ClassDef) -> bool:
    """True if the class inherits from BaseHTTPMiddleware (direct
    or qualified, e.g. `starlette.middleware.base.BaseHTTPMiddleware`)."""
    for base in class_node.bases:
        # Direct name: class X(BaseHTTPMiddleware):
        if isinstance(base, ast.Name) and base.id == "BaseHTTPMiddleware":
            return True
        # Qualified: class X(starlette.middleware.base.BaseHTTPMiddleware):
        if isinstance(base, ast.Attribute) and base.attr == "BaseHTTPMiddleware":
            return True
    return False


def _find_dispatch_method(class_node: ast.ClassDef) -> ast.AsyncFunctionDef | ast.FunctionDef | None:
    for stmt in class_node.body:
        if isinstance(stmt, (ast.AsyncFunctionDef, ast.FunctionDef)) and stmt.name == "dispatch":
            return stmt
    return None


def _find_http_exception_raises(node: ast.AST) -> list[tuple[int, str]]:
    """Return (lineno, source_line) for every `raise HTTPException(...)`
    in the subtree. Includes nested function/class bodies."""
    out: list[tuple[int, str]] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Raise):
            continue
        exc = sub.exc
        if exc is None:
            continue
        # raise HTTPException(...) — Call with func name HTTPException
        if isinstance(exc, ast.Call):
            func = exc.func
            if isinstance(func, ast.Name) and func.id == "HTTPException":
                out.append((sub.lineno, ast.unparse(sub) if hasattr(ast, "unparse") else "raise HTTPException(...)"))
            elif isinstance(func, ast.Attribute) and func.attr == "HTTPException":
                out.append((sub.lineno, ast.unparse(sub) if hasattr(ast, "unparse") else "raise <mod>.HTTPException(...)"))
    return out


def _scan_file(path: pathlib.Path) -> list[tuple[pathlib.Path, str, int, str]]:
    """Return (path, class_name, raise_lineno, raise_source) tuples for
    every middleware dispatch method that raises HTTPException."""
    try:
        src = path.read_text()
    except Exception:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []

    src_lines = src.splitlines()
    out: list[tuple[pathlib.Path, str, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not _is_basehttp_middleware(node):
            continue
        dispatch = _find_dispatch_method(node)
        if dispatch is None:
            continue
        for lineno, source in _find_http_exception_raises(dispatch):
            # Skip if same line has the allowlist marker.
            if 1 <= lineno <= len(src_lines):
                line_text = src_lines[lineno - 1]
                if "# noqa: middleware-raise-allowed" in line_text:
                    continue
            out.append((path, node.name, lineno, source))
    return out


def test_no_middleware_dispatch_raises_httpexception():
    """Scan every Python file under backend + mcp-server for
    `BaseHTTPMiddleware.dispatch` methods that `raise HTTPException`.
    None should exist — use `return JSONResponse(...)` instead.
    Closes the Session 220 task #121 regression class."""
    violations: list[str] = []
    for scan_dir in _SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for path in scan_dir.rglob("*.py"):
            # Skip vendored / generated / venv
            parts = set(path.parts)
            if parts & {"venv", ".venv", "node_modules", "__pycache__", "tests"}:
                continue
            hits = _scan_file(path)
            for hit_path, class_name, lineno, source in hits:
                rel = hit_path.relative_to(_REPO).as_posix()
                source_short = source.replace("\n", " ")[:120]
                violations.append(f"  {rel}:{lineno}  {class_name}.dispatch  →  {source_short}")

    assert not violations, (
        "\n\n`BaseHTTPMiddleware.dispatch` MUST NOT `raise HTTPException`. "
        "Starlette wraps dispatch in anyio TaskGroup; raised exceptions "
        "surface as BaseExceptionGroup to FastAPI's exception_handler "
        "chain, which doesn't match HTTPException and falls through to "
        "the generic 500 path. Use `return JSONResponse(status_code=N, "
        "content={\"error\": ..., \"status_code\": N})` instead.\n\n"
        "Violations:\n"
        + "\n".join(violations)
        + "\n\nCanonical sibling: rate_limiter.py:253/265/277 + csrf.py:190+ "
        "(post-task-#121). Or mark with `# noqa: middleware-raise-allowed` "
        "comment on the raise line if intentional (rare; document why)."
    )


def test_synthetic_violation_caught(tmp_path):
    """Positive control: synthetic middleware that raises HTTPException
    in dispatch MUST be flagged. Prevents the gate from silently rotting
    if the AST walker breaks."""
    bad = tmp_path / "synthetic_bad_middleware.py"
    bad.write_text(
        """
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import HTTPException

class BadMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.method == "POST":
            raise HTTPException(status_code=403, detail="nope")
        return await call_next(request)
"""
    )
    hits = _scan_file(bad)
    assert any(
        h[1] == "BadMiddleware" and "HTTPException" in h[3]
        for h in hits
    ), "extractor missed synthetic BaseHTTPMiddleware.dispatch raising HTTPException"


def test_synthetic_safe_pattern_passes(tmp_path):
    """Negative control: the correct return-not-raise pattern should NOT
    be flagged."""
    good = tmp_path / "synthetic_good_middleware.py"
    good.write_text(
        """
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

class GoodMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.method == "POST":
            return JSONResponse(
                status_code=403,
                content={"error": "nope", "status_code": 403},
            )
        return await call_next(request)
"""
    )
    hits = _scan_file(good)
    assert not hits, f"correct return-not-raise pattern was FLAGGED: {hits!r}"


def test_synthetic_allowlist_marker_passes(tmp_path):
    """Negative control: same-line `# noqa: middleware-raise-allowed`
    marker exempts the raise. Used only for documented intentional
    500-emission cases."""
    flagged = tmp_path / "synthetic_allowlisted_middleware.py"
    flagged.write_text(
        """
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import HTTPException

class AllowlistedMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if not request.url.path:
            raise HTTPException(status_code=500, detail="malformed")  # noqa: middleware-raise-allowed
        return await call_next(request)
"""
    )
    hits = _scan_file(flagged)
    assert not hits, (
        f"allowlist marker should exempt the raise: {hits!r}"
    )
