"""Regression gate — ban `except Exception: pass` immediately following a
DB-write call (`conn.execute / db.execute / await db.execute`).

CLAUDE.md inviolable rule:

  > "`except Exception: pass` on DB writes BANNED.
  >  `logger.warning` on DB failures BANNED → `logger.error(exc_info=True)`."

The 2026-05-08 E2E attestation audit (audit/coach-e2e-attestation-
audit-2026-05-08.md F-P0-3) caught 4 specific sites violating this:

    privileged_access_attestation.py:471 — logger.warning(...) ← FIXED
    evidence_chain.py:1156               — logger.warning(...) ← FIXED
    evidence_chain.py:1190-1191          — except: pass         ← FIXED
    evidence_chain.py:1217               — except: pass         ← FIXED

This gate keeps them fixed forever. The DETECTOR is conservative:
it scans every `try` block in every backend `*.py` (excluding
tests/) and flags any whose body contains a DB-write call AND
whose handler is exactly `except Exception: pass` with no logger
call between the except and the pass.

A handler that calls `logger.error(...)` or `raise` (or both) is OK.
A handler that calls only `logger.warning(...)` after a DB write is
an additional violation class — that is not enforced HERE (separate
gate to land alongside bulk migration of legacy sites). This gate
covers the most acute class — true silent swallow.
"""
from __future__ import annotations

import ast
import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Per-line allowlist. If you must add an entry, include a why-justified
# comment that survives code review.
SWALLOW_ALLOWLIST: list[tuple[str, int]] = [
    # ("path.py", line_no),  # rationale...
]

# Files exempt entirely (e.g. legacy migration scripts intentionally
# tolerant of write failures).
FILE_EXEMPT: set[str] = set()


def _backend_python_files() -> list[pathlib.Path]:
    files = []
    for py in _BACKEND.rglob("*.py"):
        rel = py.relative_to(_BACKEND)
        if rel.parts[0] in {"tests", "venv", ".venv", "__pycache__", "scripts"}:
            continue
        if py.name in FILE_EXEMPT:
            continue
        files.append(py)
    return files


_DB_WRITE_RE = re.compile(
    r"\b(conn|db)\.(execute|executemany)\s*\("
    r"|"
    r"\bawait\s+(conn|db)\.(execute|executemany)\s*\("
)


def _try_writes_to_db(try_node: ast.Try) -> bool:
    """Return True if the try-block body contains a DB-write call."""
    for sub in ast.walk(try_node):
        if not isinstance(sub, ast.Call):
            continue
        f = sub.func
        # `conn.execute(...)` / `db.execute(...)` / `db.executemany(...)`
        if isinstance(f, ast.Attribute) and f.attr in ("execute", "executemany"):
            obj = f.value
            if isinstance(obj, ast.Name) and obj.id in (
                "conn", "db", "_conn", "_db", "self_conn",
            ):
                return True
            # `tenant_conn.execute(text(...))` etc — look for any Name
            # ending in conn/db.
            if isinstance(obj, ast.Name) and (obj.id.endswith("conn") or obj.id.endswith("db")):
                return True
    return False


def _handler_is_silent_swallow(h: ast.ExceptHandler) -> bool:
    """Return True if the except-handler is `except Exception:` (or
    bare `except:`) and its body is exactly `pass` with no logger or
    raise call.
    """
    # Type filter: catch Exception OR bare except
    matches_excclass = False
    if h.type is None:
        matches_excclass = True
    elif isinstance(h.type, ast.Name) and h.type.id == "Exception":
        matches_excclass = True
    elif isinstance(h.type, ast.Tuple):
        for el in h.type.elts:
            if isinstance(el, ast.Name) and el.id == "Exception":
                matches_excclass = True
                break
    if not matches_excclass:
        return False
    # Body must be ONLY pass (length 1, ast.Pass) — anything else
    # (including a logger or raise) is acceptable here.
    body = h.body
    if len(body) != 1:
        return False
    if not isinstance(body[0], ast.Pass):
        return False
    return True


def _collect_violations() -> list[str]:
    out: list[str] = []
    for py in _backend_python_files():
        rel = str(py.relative_to(_BACKEND))
        try:
            src = py.read_text()
        except Exception:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            if not _try_writes_to_db(node):
                continue
            for h in node.handlers:
                if not _handler_is_silent_swallow(h):
                    continue
                # Allowlisted?
                if (rel, h.lineno) in SWALLOW_ALLOWLIST:
                    continue
                out.append(
                    f"{rel}:{h.lineno} — `except {ast.unparse(h.type) if h.type else ''}: pass` "
                    f"silently swallows a DB-write failure. CLAUDE.md "
                    f"inviolable rule: log at ERROR with exc_info OR "
                    f"re-raise. See audit F-P0-3."
                )
    return out


# Ratchet baseline as of 2026-05-08 — the audit's 4 named sites
# (privileged_access_attestation.py:471, evidence_chain.py:1156,
# evidence_chain.py:1190, evidence_chain.py:1217) are FIXED in the
# commit that introduced this gate. The 14 legacy violations that
# pre-dated the gate were bulk-migrated to `logger.error(..., exc_info=True)`
# in a follow-up commit (silent-swallow ratchet drive-down 14 → 0).
# Baseline is now 0 — any new violation MUST be fixed at the source,
# not bumped here.
#
# Sibling pattern: tests/test_admin_connection_no_multi_query.py
# ADMIN_CONN_MULTI_BASELINE_MAX (Session 212 P0 #2). Same enterprise
# ratchet shape: fail-loud BLOCK on new violations, fail-loud
# DOWNWARD on bug fixes that should drop the constant.
SILENT_SWALLOW_BASELINE_MAX = 0


def test_no_silent_db_write_swallow():
    """Ratchet — silent-swallow violations must NOT exceed the
    baseline. Each migrated site SHOULD drop the baseline; new code
    MUST never add a new violation."""
    violations = _collect_violations()
    assert len(violations) <= SILENT_SWALLOW_BASELINE_MAX, (
        f"NEW silent-DB-write-swallow violation(s). Count="
        f"{len(violations)} but baseline={SILENT_SWALLOW_BASELINE_MAX}. "
        f"CLAUDE.md inviolable rule: replace `except Exception: pass` "
        f"with `except Exception as e: logger.error(..., exc_info=True)` "
        f"OR re-raise. See audit/coach-e2e-attestation-audit-"
        f"2026-05-08.md F-P0-3.\n\n"
        + "\n".join(f"  - {v}" for v in violations[:12])
        + ("\n  ... " + str(len(violations) - 12) + " more"
           if len(violations) > 12 else "")
    )


def test_baseline_doesnt_regress_silently():
    """When a legacy site is migrated to logger.error/re-raise, the
    baseline MUST drop in the same commit. This test fails LOUDLY when
    the actual count is BELOW the constant — forcing the operator to
    ratchet SILENT_SWALLOW_BASELINE_MAX down."""
    actual = len(_collect_violations())
    assert actual == SILENT_SWALLOW_BASELINE_MAX, (
        f"actual={actual} but SILENT_SWALLOW_BASELINE_MAX="
        f"{SILENT_SWALLOW_BASELINE_MAX}. Adjust the constant in this "
        f"file to match, then commit. (If actual > baseline, a NEW "
        f"violation snuck in — fix the violation, don't bump the "
        f"baseline.)"
    )


def test_audit_named_sites_remain_fixed():
    """Pin the 4 audit-named sites to their fixed shape. If any
    regresses, fail loudly with a specific message naming the file."""
    # The 4 sites the audit named. Each was fixed in the same commit
    # that introduced this gate (commit landing this push).
    fixed_sites = [
        ("privileged_access_attestation.py", "admin_audit_log_mirror_failed"),
        ("evidence_chain.py", "evidence_rejection_tracking_failed"),
        ("evidence_chain.py", "agent_public_key_auto_register_failed"),
        ("evidence_chain.py", "evidence_accept_heartbeat_update_failed"),
    ]
    for fname, sentinel in fixed_sites:
        path = _BACKEND / fname
        assert path.exists(), f"{fname} missing"
        src = path.read_text()
        assert sentinel in src, (
            f"`{sentinel}` missing in {fname} — the audit-named "
            f"silent-swallow site appears to have regressed. "
            f"See audit F-P0-3."
        )


def test_synthetic_violation_caught():
    """Positive control — a synthetic try/except shape with DB-write
    + silent-swallow MUST be caught by the matcher."""
    src = """
async def synthetic():
    try:
        await db.execute("UPDATE x SET y = 1")
    except Exception:
        pass
"""
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if not _try_writes_to_db(node):
            continue
        for h in node.handlers:
            if _handler_is_silent_swallow(h):
                found = True
                break
    assert found, "matcher should flag synthetic db-write + silent-swallow"


def test_synthetic_safe_with_logger_error_passes():
    """Negative control — a try/except with a logger.error call
    (instead of pass) is NOT a violation."""
    src = """
async def synthetic_safe():
    try:
        await db.execute("UPDATE x SET y = 1")
    except Exception as e:
        logger.error("failed", exc_info=True)
"""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if not _try_writes_to_db(node):
            continue
        for h in node.handlers:
            assert not _handler_is_silent_swallow(h), (
                "matcher should NOT flag a handler with a logger call"
            )
