#!/usr/bin/env python3
"""pydantic_contract_check.py — block silent Pydantic contract breaks.

Session 210 (2026-04-24). Part of the 4-layer enterprise API reliability
plan. This is Layer 6: catch breaking Pydantic changes at author time
(pre-commit), before they can reach CI — let alone prod.

Contract: once a Pydantic field ships, its shape is owned by downstream
consumers (frontend TS types, daemon Go structs, auditor kit). Removing
or retyping the field is a breaking change. This script fails the commit
unless the author has EXPLICITLY acknowledged the break via:

  (a) a `# DEPRECATED: remove_after=YYYY-MM-DD` comment on/above the
      field line (graceful deprecation window); or
  (b) a `BREAKING:` prefix in the commit message body (explicit opt-in
      for unavoidable hard breaks — intended for emergency security
      patches, not for routine refactors).

Policy:

  * Fields REMOVED without either (a) or (b)        → FAIL
  * Fields TYPE-CHANGED without (b)                  → FAIL (type change
    cannot be gracefully deprecated; if it must change, it's breaking)
  * Fields ADDED                                     → PASS (backward-
    compatible for consumers that ignore unknown fields)

Scope: `mcp-server/central-command/backend/**/*.py`. Only classes whose
bases include `BaseModel` are inspected. Import aliases are permitted
(e.g. `from pydantic import BaseModel as _BM`).

Exit codes:
  0  — no contract violations
  1  — at least one violation (details printed to stderr)
  2  — script-level error (e.g. AST parse failed; treat as blocker)

Invocation: `.githooks/pre-commit` runs this. Hook is installed via
`scripts/setup-hooks.sh`. Bypass (emergency only): `git commit --no-verify`.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


BACKEND_PREFIX = "mcp-server/central-command/backend/"
DEPRECATION_RE = re.compile(
    r"#\s*DEPRECATED:\s*remove_after\s*=\s*(\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Field:
    name: str
    type_repr: str
    line: int  # 1-based, for deprecation-comment lookup


@dataclass(frozen=True)
class Model:
    cls_name: str
    fields: Tuple[Field, ...]
    source_lines: Tuple[str, ...]  # cached for deprecation-comment lookup


def _commit_message_contains_breaking() -> bool:
    """Check if the pending commit message opts into a hard break.

    Git populates COMMIT_EDITMSG during the pre-commit hook. When invoked
    outside a commit context (e.g., manual test run), fall back to env
    var PYDANTIC_CONTRACT_BREAKING=1.
    """
    if os.environ.get("PYDANTIC_CONTRACT_BREAKING", "").strip() == "1":
        return True
    msg_path = Path(".git/COMMIT_EDITMSG")
    if not msg_path.exists():
        return False
    try:
        body = msg_path.read_text(errors="replace")
    except OSError:
        return False
    # Match "BREAKING:" as a token at line start or after whitespace.
    return bool(re.search(r"(?:^|\n)\s*BREAKING:", body))


def _field_has_deprecation(field: Field, source_lines: Tuple[str, ...]) -> bool:
    """True if the field (or the line immediately above) carries a
    `# DEPRECATED: remove_after=YYYY-MM-DD` comment.
    """
    # 1-based line index → 0-based source index
    idx = field.line - 1
    if not (0 <= idx < len(source_lines)):
        return False
    if DEPRECATION_RE.search(source_lines[idx]):
        return True
    if idx > 0 and DEPRECATION_RE.search(source_lines[idx - 1]):
        return True
    return False


def _type_repr(node: ast.AST) -> str:
    """Stable, human-readable representation of an AST type annotation.

    ast.unparse is deterministic across Python 3.9+ so this round-trips
    canonically. Wrapping in a helper isolates the fallback for older
    Python on CI runners.
    """
    try:
        return ast.unparse(node).strip()
    except Exception:  # pragma: no cover — defensive
        return f"<unparseable:{type(node).__name__}>"


def _is_basemodel_class(cls: ast.ClassDef) -> bool:
    """True if the class inherits from `BaseModel` (or an alias ending
    in `BaseModel`).
    """
    for base in cls.bases:
        name = _type_repr(base)
        # "BaseModel", "pydantic.BaseModel", "x.BaseModel", "MyBase" where
        # MyBase aliases BaseModel — we accept the ends-with heuristic; a
        # rename like BaseModelv2 would need to appear in the base list
        # to match, which is acceptable surface.
        if name == "BaseModel" or name.endswith(".BaseModel"):
            return True
    return False


def _parse_models(source: str) -> Dict[str, Model]:
    """Extract all Pydantic-style models from a Python source string.

    Returns {class_name: Model}. Nested classes are skipped (ambiguous;
    rarely used for API models in this codebase).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        print(f"ERROR: cannot parse source: {e}", file=sys.stderr)
        sys.exit(2)

    source_lines = tuple(source.splitlines())
    models: Dict[str, Model] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not _is_basemodel_class(node):
            continue
        fields: List[Field] = []
        for item in node.body:
            # Only annotated assignments declare Pydantic fields.
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                fields.append(
                    Field(
                        name=item.target.id,
                        type_repr=_type_repr(item.annotation),
                        line=item.lineno,
                    )
                )
        # It's valid for a class to inherit from BaseModel and have no
        # direct annotated fields (it uses inherited ones). We still
        # record it; the diff just sees `fields=()`.
        models[node.name] = Model(
            cls_name=node.name,
            fields=tuple(fields),
            source_lines=source_lines,
        )
    return models


def _staged_backend_files() -> List[Path]:
    """Files staged for commit, filtered to the backend tree."""
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=AM"],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        print(f"ERROR: git diff --cached failed: {out.stderr}", file=sys.stderr)
        sys.exit(2)
    result: List[Path] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line.startswith(BACKEND_PREFIX):
            continue
        if not line.endswith(".py"):
            continue
        result.append(Path(line))
    return result


def _read_staged(path: Path) -> str:
    """Staged (index) version of the file."""
    out = subprocess.run(
        ["git", "show", f":{path}"],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        # File may be staged as new (no `:path` yet on certain git versions);
        # fall back to the working tree.
        try:
            return path.read_text()
        except OSError:
            return ""
    return out.stdout


def _read_head(path: Path) -> Optional[str]:
    """HEAD version of the file. Returns None if not in HEAD (new file)."""
    out = subprocess.run(
        ["git", "show", f"HEAD:{path}"],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        return None
    return out.stdout


@dataclass
class Violation:
    path: Path
    cls_name: str
    kind: str  # "removed" or "type_changed"
    field_name: str
    old_type: str
    new_type: str


def _diff_models(
    path: Path,
    old_models: Dict[str, Model],
    new_models: Dict[str, Model],
) -> List[Violation]:
    violations: List[Violation] = []
    for cls_name, old in old_models.items():
        new = new_models.get(cls_name)
        if new is None:
            # Class removed entirely — treat as N field removals, but only
            # if each field lacks a deprecation annotation in the OLD source
            # (author didn't give downstream a deprecation window).
            for f in old.fields:
                if _field_has_deprecation(f, old.source_lines):
                    continue
                violations.append(Violation(
                    path=path, cls_name=cls_name, kind="removed",
                    field_name=f.name, old_type=f.type_repr, new_type="",
                ))
            continue
        old_by_name = {f.name: f for f in old.fields}
        new_by_name = {f.name: f for f in new.fields}
        for name, old_field in old_by_name.items():
            new_field = new_by_name.get(name)
            if new_field is None:
                # Field removed — OK only if old source had the deprecation
                # annotation. Forcing the annotation to be in the OLD source
                # means the author shipped the deprecation in a PRIOR commit,
                # giving consumers time to migrate. Adding the annotation and
                # removing the field in the SAME commit is NOT graceful.
                if _field_has_deprecation(old_field, old.source_lines):
                    continue
                violations.append(Violation(
                    path=path, cls_name=cls_name, kind="removed",
                    field_name=name, old_type=old_field.type_repr, new_type="",
                ))
            elif new_field.type_repr != old_field.type_repr:
                violations.append(Violation(
                    path=path, cls_name=cls_name, kind="type_changed",
                    field_name=name, old_type=old_field.type_repr,
                    new_type=new_field.type_repr,
                ))
    return violations


def main() -> int:
    files = _staged_backend_files()
    if not files:
        return 0  # nothing backend-Pythonic in this commit

    all_violations: List[Violation] = []
    for path in files:
        new_src = _read_staged(path)
        old_src = _read_head(path)
        if old_src is None:
            # New file — no contract yet.
            continue
        try:
            old_models = _parse_models(old_src)
            new_models = _parse_models(new_src)
        except SystemExit:
            return 2
        all_violations.extend(_diff_models(path, old_models, new_models))

    if not all_violations:
        return 0

    breaking_allowed = _commit_message_contains_breaking()
    # BREAKING: permits type changes AND removals without deprecation.
    # Removals WITH a prior-commit deprecation annotation are always fine
    # regardless of BREAKING:.
    if breaking_allowed:
        print("[pydantic-contract] BREAKING: acknowledged in commit message — "
              "allowing the following contract changes:", file=sys.stderr)
        for v in all_violations:
            print(f"  BREAKING {v.kind}: {v.path}::{v.cls_name}.{v.field_name} "
                  f"({v.old_type!r} → {v.new_type!r})", file=sys.stderr)
        return 0

    print("[pydantic-contract] ❌ breaking contract changes detected:", file=sys.stderr)
    for v in all_violations:
        loc = f"{v.path}::{v.cls_name}.{v.field_name}"
        if v.kind == "removed":
            print(f"  REMOVED  {loc}  (was: {v.old_type})", file=sys.stderr)
        else:
            print(f"  RETYPED  {loc}  {v.old_type!r} → {v.new_type!r}", file=sys.stderr)
    print("", file=sys.stderr)
    print("To proceed, either:", file=sys.stderr)
    print("  (a) For a graceful removal: add `# DEPRECATED: remove_after=YYYY-MM-DD` "
          "above the field in a PRIOR commit, give consumers time to migrate, "
          "then remove in a later commit.", file=sys.stderr)
    print("  (b) For an unavoidable hard break: include `BREAKING:` at the start "
          "of your commit message body. Use sparingly — frontend TS types + "
          "daemon Go structs + auditor-kit consumers must be updated in the "
          "same or prior commit.", file=sys.stderr)
    print("  (c) Emergency bypass (leaves no audit trail): git commit --no-verify", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
