"""Pin gate — every `JSONResponse(status_code=N>=400, content=...)` returned
from a `BaseHTTPMiddleware.dispatch` body must use a `{"detail": ...}` envelope.

Session 220 task #123 (2026-05-12). Frontend parsers (`utils/api.ts:139/1643`,
`utils/portalFetch.ts:43-46`, `utils/integrationsApi.ts:30`) all read `.detail`;
zero callsites read `.error` or `.status_code`. Sibling pattern at
`rate_limiter.py:253/265/277` already uses `{"detail"}`. Closes the orphan-
envelope class introduced by the 2026-05-11 csrf.py 403-unwrap fix (which
generalized from a Starlette uncustomized 500-fallback shape that no part
of the codebase actually reads).

SCOPE: every `return JSONResponse(status_code=N, content={...})` inside a
`BaseHTTPMiddleware.dispatch` method body where N is an integer literal >= 400.
Asserts the `content` dict literal has a `"detail"` key.

Allowlist marker: `# noqa: envelope-shape-allowed` on the same line as the
JSONResponse opening — only for documented non-error 4xx/5xx responses or
for endpoints whose consumers specifically read alternate keys.

Sibling pattern: `tests/test_no_middleware_dispatch_raises_httpexception.py`
(task #121).
"""
from __future__ import annotations

import ast
import pathlib

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent.parent.parent

_SCAN_DIRS = [
    _BACKEND,
    _REPO / "mcp-server",
]


def _is_basehttp_middleware(class_node: ast.ClassDef) -> bool:
    for base in class_node.bases:
        if isinstance(base, ast.Name) and base.id == "BaseHTTPMiddleware":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "BaseHTTPMiddleware":
            return True
    return False


def _find_dispatch_method(class_node: ast.ClassDef) -> ast.AsyncFunctionDef | ast.FunctionDef | None:
    for stmt in class_node.body:
        if isinstance(stmt, (ast.AsyncFunctionDef, ast.FunctionDef)) and stmt.name == "dispatch":
            return stmt
    return None


def _extract_jsonresponse_violations(
    dispatch: ast.AsyncFunctionDef | ast.FunctionDef,
    src_lines: list[str],
) -> list[tuple[int, str]]:
    """Return (lineno, message) tuples for each JSONResponse(status_code>=400)
    whose content dict literal lacks a `"detail"` key.

    Only handles direct-literal content dicts. Variable-referenced content
    (e.g. `content=my_payload`) is skipped — those callsites must be reviewed
    manually since the literal isn't inspectable at AST time."""
    out: list[tuple[int, str]] = []
    for node in ast.walk(dispatch):
        if not isinstance(node, ast.Return):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        # Match JSONResponse(...) — Name or Attribute ending in JSONResponse.
        if isinstance(func, ast.Name):
            if func.id != "JSONResponse":
                continue
        elif isinstance(func, ast.Attribute):
            if func.attr != "JSONResponse":
                continue
        else:
            continue
        # Extract status_code + content kwargs (or positional).
        status_code: int | None = None
        content: ast.AST | None = None
        for kw in call.keywords:
            if kw.arg == "status_code" and isinstance(kw.value, ast.Constant):
                if isinstance(kw.value.value, int):
                    status_code = kw.value.value
            elif kw.arg == "content":
                content = kw.value
        # Skip if status_code unknown or < 400 (success responses don't need detail).
        if status_code is None or status_code < 400:
            continue
        # Skip if content is not a direct literal Dict (can't inspect variable refs).
        if not isinstance(content, ast.Dict):
            continue
        # Skip if allowlist marker is on the JSONResponse opening line.
        if 1 <= call.lineno <= len(src_lines):
            line_text = src_lines[call.lineno - 1]
            if "# noqa: envelope-shape-allowed" in line_text:
                continue
        # Check the dict has a "detail" key literal.
        has_detail = False
        for k in content.keys:
            if isinstance(k, ast.Constant) and k.value == "detail":
                has_detail = True
                break
        if not has_detail:
            keys_found = sorted(
                repr(k.value) for k in content.keys
                if isinstance(k, ast.Constant)
            )
            out.append((
                call.lineno,
                f"  envelope missing `detail` key; got {keys_found}",
            ))
    return out


def _scan_file(path: pathlib.Path) -> list[tuple[pathlib.Path, str, int, str]]:
    """Return (path, class_name, lineno, message) tuples for every violation."""
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
        for lineno, msg in _extract_jsonresponse_violations(dispatch, src_lines):
            out.append((path, node.name, lineno, msg))
    return out


def test_middleware_jsonresponse_uses_detail_envelope():
    """Every `JSONResponse(status_code>=400, content={...})` inside a
    `BaseHTTPMiddleware.dispatch` method body must use `{"detail": ...}`.

    Closes the Session 220 task #123 class — csrf.py shipped `{"error",
    "status_code"}` envelope which is opaque to every frontend parser
    (they all read `.detail`). Pattern parity with rate_limiter.py."""
    violations: list[str] = []
    for scan_dir in _SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for path in scan_dir.rglob("*.py"):
            parts = set(path.parts)
            if parts & {"venv", ".venv", "node_modules", "__pycache__", "tests"}:
                continue
            for hit_path, class_name, lineno, msg in _scan_file(path):
                rel = hit_path.relative_to(_REPO).as_posix()
                violations.append(f"  {rel}:{lineno}  {class_name}.dispatch\n{msg}")

    assert not violations, (
        "\n\n`BaseHTTPMiddleware.dispatch` MUST emit `{\"detail\": ...}` "
        "envelope on 4xx/5xx JSONResponse — every frontend parser "
        "(utils/api.ts, portalFetch.ts, integrationsApi.ts) reads "
        "`.detail`. Sibling pattern: rate_limiter.py:253/265/277. "
        "Marker `# noqa: envelope-shape-allowed` allows opt-out for "
        "documented exceptions.\n\nViolations:\n"
        + "\n".join(violations)
    )


def test_synthetic_violation_caught(tmp_path):
    """Positive control: synthetic middleware returning a non-detail
    envelope MUST be flagged."""
    bad = tmp_path / "synthetic_bad_envelope.py"
    bad.write_text(
        '''
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

class BadEnvelopeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        return JSONResponse(
            status_code=403,
            content={"error": "nope", "status_code": 403},
        )
'''
    )
    hits = _scan_file(bad)
    assert any(
        h[1] == "BadEnvelopeMiddleware" and "missing `detail`" in h[3]
        for h in hits
    ), f"extractor missed orphan-envelope shape: {hits!r}"


def test_synthetic_safe_envelope_passes(tmp_path):
    """Negative control: canonical `{"detail"}` shape MUST NOT fire."""
    good = tmp_path / "synthetic_good_envelope.py"
    good.write_text(
        '''
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

class GoodEnvelopeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        return JSONResponse(
            status_code=429,
            content={"detail": "slow down", "retry_after": 60},
        )
'''
    )
    hits = _scan_file(good)
    assert not hits, f"canonical detail-envelope was flagged: {hits!r}"


def test_synthetic_success_response_not_flagged(tmp_path):
    """Negative control: success responses (status < 400) are OUT OF
    SCOPE for envelope harmony (no caller is parsing them as errors)."""
    good = tmp_path / "synthetic_success_envelope.py"
    good.write_text(
        '''
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

class SuccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        return JSONResponse(
            status_code=200,
            content={"result": "ok", "anything": "goes"},
        )
'''
    )
    hits = _scan_file(good)
    assert not hits, f"success response was flagged: {hits!r}"


def test_synthetic_allowlist_marker_passes(tmp_path):
    """Negative control: `# noqa: envelope-shape-allowed` on the
    JSONResponse opening line exempts from the gate."""
    flagged = tmp_path / "synthetic_allowlisted_envelope.py"
    flagged.write_text(
        '''
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

class AllowlistedMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        return JSONResponse(  # noqa: envelope-shape-allowed
            status_code=403,
            content={"custom": "schema"},
        )
'''
    )
    hits = _scan_file(flagged)
    assert not hits, f"allowlist marker should exempt: {hits!r}"
