"""Block CI / prod version drift at the test layer.

Session 210 (2026-04-24) post-mortem. The 3-hour deploy outage happened
because:
  * mcp-server/requirements.lock (container image): fastapi==0.115.6
  * mcp-server/central-command/backend/requirements.txt (CI tests): fastapi==0.135.3

FastAPI 0.135.3 silently RELAXED the `status_code=204 + return-annotation`
assertion that 0.115.6 enforces. Two commits (632d3408 + 11d80a44)
passed CI smoke-import (0.135.3) but crashed the container on import
(0.115.6), triggering auto-rollback loops.

This test locks the two files' framework pins in lockstep so the next
author who bumps either side MUST bump both. Adding new deps is fine;
having a version disagree between the two files is a CI-test
falsification and fails hard.

If the pins legitimately need to differ (e.g. during a gradual
upgrade), document the reason + update this test's allowlist.
"""
from __future__ import annotations

import pathlib
import re

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
LOCK = REPO_ROOT / "mcp-server" / "requirements.lock"
CI_REQ = REPO_ROOT / "mcp-server" / "central-command" / "backend" / "requirements.txt"


# Framework packages whose behavior differs meaningfully between versions.
# These MUST be pin-identical across the two files. Adding a new framework
# to this list is a conscious policy choice; adding it here hardens the
# lockstep for that package specifically.
LOCKSTEP_PACKAGES = {
    "fastapi",
    "pydantic",
    "sqlalchemy",
    "asyncpg",
    "starlette",
    "uvicorn",
}


# Accept `pkg==x.y.z` and `pkg[extras]==x.y.z` — pip both.
_PIN_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9._-]+)(?:\[[^\]]*\])?==(?P<version>[0-9A-Za-z._+-]+)\s*$"
)


def _parse_pins(path: pathlib.Path) -> dict[str, str]:
    """Return {normalized_package_name: version} for every pinned line."""
    if not path.exists():
        pytest.skip(f"{path} missing on this checkout — skipping lockstep check")
    pins: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _PIN_RE.match(line)
        if m:
            name = m.group("name").lower().replace("_", "-")
            pins[name] = m.group("version")
    return pins


def test_framework_pins_lockstep_between_ci_and_prod() -> None:
    lock = _parse_pins(LOCK)
    ci = _parse_pins(CI_REQ)
    mismatches: list[tuple[str, str, str]] = []
    for pkg in LOCKSTEP_PACKAGES:
        lv = lock.get(pkg)
        cv = ci.get(pkg)
        if lv is None or cv is None:
            # One file doesn't pin this package — fine; can't be in lockstep,
            # can't be in drift.
            continue
        if lv != cv:
            mismatches.append((pkg, lv, cv))
    if mismatches:
        lines = "\n".join(
            f"  {pkg}: requirements.lock={lv!r}  backend/requirements.txt={cv!r}"
            for pkg, lv, cv in mismatches
        )
        raise AssertionError(
            "CI/prod framework version DRIFT detected — CI is testing against "
            "a different version than the container runs.\n\n"
            f"{lines}\n\n"
            "Fix: align both files to the same version. Prefer the "
            "requirements.lock version (that's what actually runs in prod). "
            "Update backend/requirements.txt to match.\n\n"
            "Session 210 post-mortem: fastapi==0.135.3 (CI) vs 0.115.6 (prod) "
            "caused a 3-hour deploy outage because 0.135.3 relaxed a "
            "FastAPI route assertion that 0.115.6 enforces. CI was green, "
            "prod crashlooped on import, auto-rollback looped."
        )


def test_lockstep_set_is_non_empty() -> None:
    """Guard against a future refactor silently emptying LOCKSTEP_PACKAGES,
    which would make this test trivially pass on any drift."""
    assert LOCKSTEP_PACKAGES, "LOCKSTEP_PACKAGES must not be empty"
    assert "fastapi" in LOCKSTEP_PACKAGES, (
        "fastapi MUST remain in LOCKSTEP_PACKAGES — it was the 2026-04-24 offender"
    )
