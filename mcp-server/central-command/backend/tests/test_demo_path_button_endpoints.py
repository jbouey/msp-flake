"""Source-level regression test for the demo-path button → backend
endpoint contract.

The 2026-04-25 audit found OperatorAckPanel + SensorStatus 500'ing
because the buttons targeted endpoints whose shapes had drifted (CSRF
headers missing, routes moved). The CSRF linter
(test_frontend_mutation_csrf.py) catches the header-shape class. This
test catches the orthogonal "endpoint disappeared" / "endpoint
renamed" class — the URL the button POSTs to MUST exist server-side.

It's source-level (no FastAPI instantiation needed): grep the .tsx
files for fetch URLs the buttons issue, then grep the backend Python
for matching route decorators. Cheap, fast, no DB.

Adding a new demo-path button: append to ENDPOINT_CONTRACTS below.
The test will fail if either side disappears.
"""
from __future__ import annotations

import pathlib
import re
from typing import List, Tuple

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
FRONTEND_SRC = REPO_ROOT / "mcp-server" / "central-command" / "frontend" / "src"
BACKEND_DIR = REPO_ROOT / "mcp-server" / "central-command" / "backend"


# (frontend_file, fetch_url_substring, http_method, backend_route_pattern)
# `backend_route_pattern` is a regex that must match somewhere in any
# backend `*.py` file — typically a FastAPI decorator like
# `@router.post("/x/y")` or `@app.post("/x/y")`. Use a substring that
# uniquely identifies the route, since path parameters vary.
ENDPOINT_CONTRACTS: List[Tuple[str, str, str, str]] = [
    # OperatorAckPanel — Session 206 round-table R5
    (
        "components/command-center/OperatorAckPanel.tsx",
        "/api/dashboard/flywheel-spine/acknowledge",
        "POST",
        r"""[@\.]\w*\.post\(\s*["']/?flywheel-spine/acknowledge""",
    ),
    # Session 220 task #120 PR-A (2026-05-12): SensorStatus.tsx +
    # backend sensors.py both deleted as fully-orphan dead code.
    # Their entries are removed from this DEMO_BUTTON list.
]


def _backend_py_files() -> List[pathlib.Path]:
    out: List[pathlib.Path] = []
    for p in BACKEND_DIR.rglob("*.py"):
        if any(skip in p.parts for skip in (
            "tests", "archived", "venv", "__pycache__", "node_modules",
        )):
            continue
        out.append(p)
    return out


def _backend_concat() -> str:
    """One big string of every backend .py — enables a single regex
    sweep instead of N file-opens."""
    chunks = []
    for p in _backend_py_files():
        try:
            chunks.append(p.read_text(encoding="utf-8"))
        except OSError:
            continue
    return "\n\n".join(chunks)


def test_operator_ack_button_endpoint_exists():
    """OperatorAckPanel.tsx POSTs to /api/dashboard/flywheel-spine/acknowledge.
    Backend must register that route — otherwise the button silently 404s."""
    src = (FRONTEND_SRC / "components/command-center/OperatorAckPanel.tsx").read_text()
    assert "/api/dashboard/flywheel-spine/acknowledge" in src, (
        "OperatorAckPanel.tsx no longer fetches the acknowledge endpoint. "
        "If you renamed the URL, update both the frontend AND this test."
    )
    backend = _backend_concat()
    assert re.search(
        r"""[@\.]\w*\.post\(\s*["']/?flywheel-spine/acknowledge""",
        backend,
    ), (
        "Backend has no @router.post('/flywheel-spine/acknowledge') route. "
        "OperatorAckPanel will 404. Either restore the route or update "
        "the frontend URL."
    )


# Session 220 task #120 PR-A (2026-05-12): test_sensor_status_*_endpoint_exists
# tests and SensorStatus.tsx CSRF-helper test removed. SensorStatus.tsx
# was fully-orphan (zero parent imports — never mounted in the app tree);
# backend sensors.py was unregistered (no main.py include_router). Both
# deleted together. OperatorAckPanel CSRF assertion still has its own
# coverage via test_frontend_mutation_csrf.py.


def test_operator_ack_button_has_csrf_header_or_helper():
    """Carry-forward from the prior 2026-04-25 audit: assert the
    OperatorAckPanel mutation button retains credentials:'include'
    + a CSRF reference. If someone hand-rewrites it without using
    fetchApi, this test fails fast."""
    rel = "components/command-center/OperatorAckPanel.tsx"
    src = (FRONTEND_SRC / rel).read_text()
    assert "credentials: 'include'" in src or "credentials:'include'" in src, (
        f"{rel} dropped credentials:'include' from its mutation fetch."
    )
    has_csrf = (
        "X-CSRF-Token" in src
        or "csrfHeaders(" in src
        or "getCsrfTokenOrEmpty(" in src
    )
    assert has_csrf, (
        f"{rel} dropped its CSRF-token reference from the mutation fetch."
    )
