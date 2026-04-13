"""Tests for .agent/scripts/context-manager.py.

Run with: pytest .agent/scripts/test_context_manager.py -v
(or python3 -m pytest from repo root)

The validator and session helpers are the load-bearing parts — if they
break, memory hygiene loop stops and Claude sessions start with stale
state. These tests are the regression fence.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".agent/scripts/context-manager.py"


def _run(args, cwd=None):
    """Invoke context-manager.py as a subprocess, return (rc, stdout, stderr)."""
    cwd = cwd or REPO_ROOT
    p = subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    return p.returncode, p.stdout, p.stderr


# ─── Baseline: the live repo state validates ─────────────────────


def test_validate_passes_on_current_repo():
    """Committed state MUST pass validate. If this fails, fix memory
    hygiene before merging."""
    rc, out, err = _run(["validate"])
    assert rc == 0, f"validate returned {rc}\nstdout: {out}\nstderr: {err}"
    assert "passed" in out.lower()


def test_progress_json_is_valid_v2():
    """progress.json must load, have schema_version 2.0, active_tasks empty."""
    path = REPO_ROOT / ".agent/claude-progress.json"
    data = json.loads(path.read_text())
    assert data.get("schema_version") == "2.0"
    assert data.get("active_tasks") == []
    assert isinstance(data.get("system_health"), list)
    for entry in data["system_health"]:
        assert isinstance(entry, dict)
        assert "component" in entry
        assert "status" in entry
        assert "last_verified" in entry


# ─── Validate catches the common breakage modes ──────────────────


def _import_context_manager():
    """Import context-manager.py as a module (its filename has a hyphen
    so it can't be `import`ed normally — use importlib.util)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("context_manager", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_validate_fails_on_non_empty_active_tasks(capsys):
    """active_tasks should be empty (TaskCreate owns it). If someone
    regresses to v1-style inline tasks, validate must fail."""
    mod = _import_context_manager()
    bad = {
        "schema_version": "2.0",
        "session": 1,
        "updated": "2026-04-13T00:00:00Z",
        "versions": {},
        "system_health": [],
        "active_tasks": ["nope, this should fail"],
    }
    with patch.object(mod, "load_progress", return_value=bad):
        result = mod.validate()
    captured = capsys.readouterr()
    assert result is False
    assert "active_tasks" in captured.out


def test_validate_fails_on_wrong_schema_version(capsys):
    mod = _import_context_manager()
    bad = {
        "schema_version": "1.0",
        "session": 1,
        "updated": "2026-04-13T00:00:00Z",
        "versions": {},
        "system_health": [],
        "active_tasks": [],
    }
    with patch.object(mod, "load_progress", return_value=bad):
        result = mod.validate()
    captured = capsys.readouterr()
    assert result is False
    assert "schema_version" in captured.out


def test_validate_fails_on_dict_system_health(capsys):
    """v1 used a dict for system_health; v2 requires list."""
    mod = _import_context_manager()
    bad = {
        "schema_version": "2.0",
        "session": 1,
        "updated": "2026-04-13T00:00:00Z",
        "versions": {},
        "system_health": {"vps": "up"},  # ← v1 shape, must fail
        "active_tasks": [],
    }
    with patch.object(mod, "load_progress", return_value=bad):
        result = mod.validate()
    captured = capsys.readouterr()
    assert result is False
    assert "system_health" in captured.out


# ─── Memory-hygiene checks (hit the actual files) ────────────────


def test_memory_md_under_truncation_cap():
    """System truncates ~200 lines. Must stay under."""
    memory_root = Path.home() / ".claude/projects"
    if not memory_root.exists():
        pytest.skip("Not a Claude-Code env — user memory dir absent")

    mem_md = None
    for d in memory_root.iterdir():
        candidate = d / "memory" / "MEMORY.md"
        if candidate.exists() and "msp" in d.name.lower().replace("-", "_"):
            mem_md = candidate
            break
    if mem_md is None:
        pytest.skip("No MSP memory dir found")

    lines = mem_md.read_text().splitlines()
    assert len(lines) <= 200, (
        f"MEMORY.md has {len(lines)} lines (cap 200). "
        f"Move detail to topic files."
    )


def test_all_topic_files_have_frontmatter():
    memory_root = Path.home() / ".claude/projects"
    if not memory_root.exists():
        pytest.skip("Not a Claude-Code env — user memory dir absent")

    mem_dir = None
    for d in memory_root.iterdir():
        candidate = d / "memory"
        if candidate.exists() and "msp" in d.name.lower().replace("-", "_"):
            mem_dir = candidate
            break
    if mem_dir is None:
        pytest.skip("No MSP memory dir found")

    missing = []
    for topic in mem_dir.glob("*.md"):
        if topic.name == "MEMORY.md":
            continue
        head = topic.read_text(errors="replace").lstrip().splitlines()[:1]
        if not head or not head[0].startswith("---"):
            missing.append(topic.name)
    assert missing == [], f"Topic files missing frontmatter: {missing}"


def test_memory_md_cross_refs_resolve():
    """Every .md reference in MEMORY.md must point at a real file."""
    import re
    memory_root = Path.home() / ".claude/projects"
    if not memory_root.exists():
        pytest.skip("Not a Claude-Code env")
    mem_dir = None
    for d in memory_root.iterdir():
        candidate = d / "memory"
        if candidate.exists() and "msp" in d.name.lower().replace("-", "_"):
            mem_dir = candidate
            break
    if mem_dir is None:
        pytest.skip()

    text = (mem_dir / "MEMORY.md").read_text()
    refs = set(re.findall(r"\(([a-z][a-z0-9_]*\.md)\)", text))
    missing = [r for r in refs if not (mem_dir / r).exists()]
    assert missing == [], f"Dead refs in MEMORY.md: {missing}"


# ─── CLI surface smoke ───────────────────────────────────────────


def test_status_command_runs():
    """status shouldn't crash on the real progress.json."""
    rc, out, err = _run(["status"])
    assert rc == 0, f"status failed: stdout={out} stderr={err}"


def test_unknown_command_fails_cleanly():
    rc, out, err = _run(["not-a-real-command"])
    # Should exit non-zero or print help — not crash
    assert rc != 0 or "Usage" in out or "Commands" in out
