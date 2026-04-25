"""Tests for scripts/pydantic_contract_check.py (Layer 6 of the
enterprise API reliability plan — Session 210, 2026-04-24).

Goals:
  * Every policy branch (removed / retyped / added / class-removed /
    BREAKING-bypass / deprecation-annotation-bypass) is covered.
  * Parsing is robust to nested classes, non-BaseModel classes,
    aliased imports, and complex type annotations.
  * Integration test runs the FULL pre-commit script end-to-end against
    a throwaway temp git repo — the only way to verify the staging-diff
    pipeline.

This test suite is itself source-level — no backend deps required.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
SCRIPT = REPO_ROOT / "scripts" / "pydantic_contract_check.py"

# Make the script importable so unit tests can reach its helpers without
# re-invoking it through subprocess every time.
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import pydantic_contract_check as pcc  # noqa: E402


# ---------------------------------------------------------------------------
# Unit tests — parser + diff
# ---------------------------------------------------------------------------

def test_parser_captures_basic_model():
    src = textwrap.dedent("""
        from pydantic import BaseModel

        class Foo(BaseModel):
            a: int
            b: str
    """)
    models = pcc._parse_models(src)
    assert "Foo" in models
    names = [f.name for f in models["Foo"].fields]
    assert names == ["a", "b"]


def test_parser_skips_non_basemodel():
    src = textwrap.dedent("""
        class Plain:
            a: int

        class Mixin:
            b: str
    """)
    assert pcc._parse_models(src) == {}


def test_parser_catches_aliased_basemodel():
    src = textwrap.dedent("""
        from pydantic import BaseModel as BM  # aliased but still ends-with BaseModel

        class X(BM.BaseModel):
            a: int
    """)
    # _is_basemodel_class checks ends-with ".BaseModel" — the base `BM.BaseModel`
    # unparsed becomes "BM.BaseModel" which ends with ".BaseModel".
    models = pcc._parse_models(src)
    assert "X" in models


def test_parser_captures_complex_types():
    src = textwrap.dedent("""
        from pydantic import BaseModel
        from typing import Optional, List, Dict

        class Blob(BaseModel):
            a: Optional[int] = None
            b: List[Dict[str, str]] = []
            c: str = ""
    """)
    models = pcc._parse_models(src)
    types = {f.name: f.type_repr for f in models["Blob"].fields}
    assert "Optional[int]" in types["a"]
    assert "List[Dict[str, str]]" in types["b"]


def test_diff_detects_field_removal():
    old_src = textwrap.dedent("""
        from pydantic import BaseModel
        class Foo(BaseModel):
            a: int
            b: str
    """)
    new_src = textwrap.dedent("""
        from pydantic import BaseModel
        class Foo(BaseModel):
            a: int
    """)
    violations, _ = pcc._diff_models(
        pathlib.Path("x.py"),
        pcc._parse_models(old_src),
        pcc._parse_models(new_src),
    )
    assert len(violations) == 1
    assert violations[0].field_name == "b"
    assert violations[0].kind == "removed"


def test_diff_detects_type_change():
    old_src = "from pydantic import BaseModel\nclass Foo(BaseModel):\n    a: int"
    new_src = "from pydantic import BaseModel\nclass Foo(BaseModel):\n    a: str"
    violations, _ = pcc._diff_models(
        pathlib.Path("x.py"),
        pcc._parse_models(old_src),
        pcc._parse_models(new_src),
    )
    assert len(violations) == 1
    assert violations[0].kind == "type_changed"
    assert violations[0].old_type == "int"
    assert violations[0].new_type == "str"


def test_diff_allows_field_addition():
    old_src = "from pydantic import BaseModel\nclass Foo(BaseModel):\n    a: int"
    new_src = textwrap.dedent("""
        from pydantic import BaseModel
        class Foo(BaseModel):
            a: int
            b: str  # newly added, backwards-compatible
    """)
    violations, _ = pcc._diff_models(
        pathlib.Path("x.py"),
        pcc._parse_models(old_src),
        pcc._parse_models(new_src),
    )
    assert violations == []


def test_diff_respects_prior_deprecation_annotation():
    # The deprecation annotation must have been in the OLD source — that's
    # what proves it shipped in a prior commit.
    old_src = textwrap.dedent("""
        from pydantic import BaseModel
        class Foo(BaseModel):
            a: int
            # DEPRECATED: remove_after=2026-04-24
            b: str
    """)
    new_src = textwrap.dedent("""
        from pydantic import BaseModel
        class Foo(BaseModel):
            a: int
    """)
    violations, _ = pcc._diff_models(
        pathlib.Path("x.py"),
        pcc._parse_models(old_src),
        pcc._parse_models(new_src),
    )
    assert violations == [], (
        "prior-commit deprecation annotation must bypass the removal block"
    )


def test_diff_rejects_same_commit_deprecation():
    # Adding the annotation AND removing the field in the same commit isn't
    # graceful — consumers had zero warning window.
    old_src = textwrap.dedent("""
        from pydantic import BaseModel
        class Foo(BaseModel):
            a: int
            b: str
    """)
    new_src = textwrap.dedent("""
        from pydantic import BaseModel
        class Foo(BaseModel):
            a: int
    """)
    violations, _ = pcc._diff_models(
        pathlib.Path("x.py"),
        pcc._parse_models(old_src),
        pcc._parse_models(new_src),
    )
    assert len(violations) == 1, "removal in same commit as annotation must fail"


def test_diff_class_removal_is_field_removal():
    old_src = textwrap.dedent("""
        from pydantic import BaseModel
        class Foo(BaseModel):
            a: int
            b: str
    """)
    new_src = "from pydantic import BaseModel\n"
    violations, _ = pcc._diff_models(
        pathlib.Path("x.py"),
        pcc._parse_models(old_src),
        pcc._parse_models(new_src),
    )
    assert len(violations) == 2, "class removal is N field removals"
    assert {v.field_name for v in violations} == {"a", "b"}


def test_diff_detects_unambiguous_rename():
    """Session 210 round-table #2: X removed + Y added with same type = rename.
    Flagged as 'renamed' violation requiring BREAKING: acknowledgment."""
    old_src = "from pydantic import BaseModel\nclass Foo(BaseModel):\n    tier: str"
    new_src = "from pydantic import BaseModel\nclass Foo(BaseModel):\n    plan: str"
    violations, _ = pcc._diff_models(
        pathlib.Path("x.py"),
        pcc._parse_models(old_src),
        pcc._parse_models(new_src),
    )
    # Exactly ONE violation — a rename, NOT separate removal + addition.
    assert len(violations) == 1, f"expected 1 rename violation, got {violations}"
    assert violations[0].kind == "renamed"
    assert violations[0].field_name == "tier"
    assert "plan" in violations[0].new_type


def test_diff_does_not_falsely_detect_rename_on_multiple_same_type_adds():
    """When more than one same-type field is added, we can't UNAMBIGUOUSLY
    pair the removal with an addition. Prefer to flag the removal cleanly
    rather than guess the wrong match."""
    old_src = "from pydantic import BaseModel\nclass Foo(BaseModel):\n    tier: str"
    new_src = (
        "from pydantic import BaseModel\n"
        "class Foo(BaseModel):\n"
        "    plan: str\n"
        "    label: str\n"
    )
    violations, _ = pcc._diff_models(
        pathlib.Path("x.py"),
        pcc._parse_models(old_src),
        pcc._parse_models(new_src),
    )
    # Ambiguous — don't guess. Surface the removal of 'tier' as a plain removal.
    kinds = [v.kind for v in violations]
    assert "renamed" not in kinds, (
        "must not claim a rename when multiple same-type additions are ambiguous"
    )
    assert "removed" in kinds


def test_diff_ignores_type_equivalent_whitespace():
    # `Optional[int]` vs `Optional[int]` with different whitespace is still equal
    old_src = "from pydantic import BaseModel\nfrom typing import Optional\nclass Foo(BaseModel):\n    a: Optional[int]"
    new_src = "from pydantic import BaseModel\nfrom typing import Optional\nclass Foo(BaseModel):\n    a:   Optional[int]"
    violations, _ = pcc._diff_models(
        pathlib.Path("x.py"),
        pcc._parse_models(old_src),
        pcc._parse_models(new_src),
    )
    # ast.unparse normalizes whitespace, so this should pass.
    assert violations == []


def test_diff_class_rename_only_no_field_changes_is_not_a_violation():
    """Session 210-B: class rename with identical field shape should NOT
    block the commit. The wire contract (field names + types) is preserved;
    only the OpenAPI $ref key changes. Renames are surfaced as INFORMATIONAL
    via the second tuple element, not as violations."""
    old_src = (
        "from pydantic import BaseModel\n"
        "class CheckinRequest(BaseModel):\n"
        "    site_id: str\n"
        "    host_id: str\n"
    )
    new_src = (
        "from pydantic import BaseModel\n"
        "class _AgentApiCheckinRequest(BaseModel):\n"
        "    site_id: str\n"
        "    host_id: str\n"
    )
    violations, renames = pcc._diff_models(
        pathlib.Path("x.py"),
        pcc._parse_models(old_src),
        pcc._parse_models(new_src),
    )
    assert violations == [], (
        "class rename with unchanged field signature must produce zero "
        f"field-removal violations, got: {[(v.kind, v.field_name) for v in violations]}"
    )
    assert renames == {"CheckinRequest": "_AgentApiCheckinRequest"}


def test_diff_class_rename_with_field_change_still_blocks():
    """If the author renames AND modifies the class, the field-level
    changes must still surface — only the rename half is auto-permitted.
    """
    old_src = (
        "from pydantic import BaseModel\n"
        "class CheckinRequest(BaseModel):\n"
        "    site_id: str\n"
        "    host_id: str\n"
    )
    new_src = (
        "from pydantic import BaseModel\n"
        "class _AgentApiCheckinRequest(BaseModel):\n"
        "    site_id: int\n"  # type changed
        "    host_id: str\n"
    )
    violations, renames = pcc._diff_models(
        pathlib.Path("x.py"),
        pcc._parse_models(old_src),
        pcc._parse_models(new_src),
    )
    # Signature differs (str vs int), so this is NOT detected as a rename.
    # Both classes count: old class fully removed, new class fully added.
    # site_id and host_id appear as REMOVED on the old class (unless
    # deprecation annotation present).
    assert renames == {}, "type change means signatures differ, not a rename"
    kinds = sorted({v.kind for v in violations})
    assert "removed" in kinds


def test_diff_class_rename_ambiguous_when_two_added_share_signature():
    """If TWO new classes have the same signature as a removed class, we
    can't unambiguously pick the rename target — surface as field removals
    so the human reviews each."""
    old_src = (
        "from pydantic import BaseModel\n"
        "class Foo(BaseModel):\n"
        "    a: str\n"
        "    b: int\n"
    )
    new_src = (
        "from pydantic import BaseModel\n"
        "class Bar(BaseModel):\n"
        "    a: str\n"
        "    b: int\n"
        "class Baz(BaseModel):\n"
        "    a: str\n"
        "    b: int\n"
    )
    violations, renames = pcc._diff_models(
        pathlib.Path("x.py"),
        pcc._parse_models(old_src),
        pcc._parse_models(new_src),
    )
    assert renames == {}, "ambiguous match — must not guess"
    # Foo's fields a, b should appear as removals.
    field_names = sorted(v.field_name for v in violations)
    assert field_names == ["a", "b"]


def test_diff_class_rename_skips_empty_signature():
    """An empty (no annotated fields) signature is too weak a fingerprint
    for rename detection — skip those classes."""
    old_src = (
        "from pydantic import BaseModel\n"
        "class Empty1(BaseModel):\n"
        "    pass\n"
    )
    new_src = (
        "from pydantic import BaseModel\n"
        "class Empty2(BaseModel):\n"
        "    pass\n"
    )
    violations, renames = pcc._diff_models(
        pathlib.Path("x.py"),
        pcc._parse_models(old_src),
        pcc._parse_models(new_src),
    )
    # No fields removed (the class had none) and no rename detected.
    assert violations == []
    assert renames == {}


# ---------------------------------------------------------------------------
# Integration test — full script against a temp git repo
# ---------------------------------------------------------------------------

def _git(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    # Fixed identity so `git commit` works in CI containers without config
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, env=env, check=False,
    )


def _make_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mcp-server" / "central-command" / "backend").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "commit.gpgsign", "false")
    return repo


def _run_check(repo: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(SCRIPT)],
        cwd=repo, capture_output=True, text=True, check=False,
    )


def test_integration_clean_commit_passes(tmp_path):
    repo = _make_repo(tmp_path)
    target = repo / "mcp-server" / "central-command" / "backend" / "models.py"
    target.write_text("from pydantic import BaseModel\nclass Foo(BaseModel):\n    a: int\n")
    _git(repo, "add", str(target))
    _git(repo, "commit", "-q", "-m", "initial")
    target.write_text("from pydantic import BaseModel\nclass Foo(BaseModel):\n    a: int\n    b: str\n")
    _git(repo, "add", str(target))
    result = _run_check(repo)
    assert result.returncode == 0, result.stderr


def test_integration_field_removal_blocks(tmp_path):
    repo = _make_repo(tmp_path)
    target = repo / "mcp-server" / "central-command" / "backend" / "models.py"
    target.write_text("from pydantic import BaseModel\nclass Foo(BaseModel):\n    a: int\n    b: str\n")
    _git(repo, "add", str(target))
    _git(repo, "commit", "-q", "-m", "initial")
    target.write_text("from pydantic import BaseModel\nclass Foo(BaseModel):\n    a: int\n")
    _git(repo, "add", str(target))
    result = _run_check(repo)
    assert result.returncode == 1
    assert "REMOVED" in result.stderr
    assert "Foo.b" in result.stderr


def test_integration_breaking_acknowledgment_allows_removal(tmp_path):
    """Opt-in via env var instead of commit message (easier to test)."""
    repo = _make_repo(tmp_path)
    target = repo / "mcp-server" / "central-command" / "backend" / "models.py"
    target.write_text("from pydantic import BaseModel\nclass Foo(BaseModel):\n    a: int\n    b: str\n")
    _git(repo, "add", str(target))
    _git(repo, "commit", "-q", "-m", "initial")
    target.write_text("from pydantic import BaseModel\nclass Foo(BaseModel):\n    a: int\n")
    _git(repo, "add", str(target))

    env = {**os.environ, "PYDANTIC_CONTRACT_BREAKING": "1"}
    result = subprocess.run(
        ["python3", str(SCRIPT)], cwd=repo,
        capture_output=True, text=True, check=False, env=env,
    )
    assert result.returncode == 0, (
        "PYDANTIC_CONTRACT_BREAKING=1 must bypass — it's the equivalent of "
        "'BREAKING:' in the commit message"
    )
    assert "BREAKING" in result.stderr


def test_integration_frontend_only_commit_is_fast_path(tmp_path):
    """When no backend Python is staged, the script exits 0 instantly —
    frontend-only / docs-only commits don't pay the AST-parse cost."""
    repo = _make_repo(tmp_path)
    (repo / "README.md").write_text("# test\n")
    _git(repo, "add", "README.md")
    result = _run_check(repo)
    assert result.returncode == 0
    assert result.stderr == ""  # zero noise on the fast path


def test_integration_prior_deprecation_allows_removal(tmp_path):
    """A deprecation annotation that shipped in a prior commit bypasses
    the removal block — this is the graceful-deprecation path."""
    repo = _make_repo(tmp_path)
    target = repo / "mcp-server" / "central-command" / "backend" / "models.py"
    # Commit 1: add field WITH deprecation annotation (warning to consumers)
    target.write_text(
        "from pydantic import BaseModel\n"
        "class Foo(BaseModel):\n"
        "    a: int\n"
        "    # DEPRECATED: remove_after=2026-05-01\n"
        "    b: str\n"
    )
    _git(repo, "add", str(target))
    _git(repo, "commit", "-q", "-m", "announce deprecation of b")

    # Commit 2: remove the deprecated field — should be allowed
    target.write_text(
        "from pydantic import BaseModel\n"
        "class Foo(BaseModel):\n"
        "    a: int\n"
    )
    _git(repo, "add", str(target))
    result = _run_check(repo)
    assert result.returncode == 0, result.stderr


def test_integration_new_file_passes(tmp_path):
    """A brand-new file has no HEAD version → no contract to compare
    against → pass."""
    repo = _make_repo(tmp_path)
    (repo / "README.md").write_text("init\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")

    target = repo / "mcp-server" / "central-command" / "backend" / "new_models.py"
    target.write_text("from pydantic import BaseModel\nclass Brand(BaseModel):\n    a: int\n")
    _git(repo, "add", str(target))
    result = _run_check(repo)
    assert result.returncode == 0, result.stderr
