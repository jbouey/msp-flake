"""Pin gate — MinIO evidence uploader MUST validate Object Lock on init.

WORM (Write-Once-Read-Many) is the load-bearing tamper-evidence guarantee
for evidence_bundles storage. If a future refactor drops the
`_validate_bucket()` call from `EvidenceUploader.__init__`, an
operator could spin up a non-locked bucket and the uploader would
happily write evidence to it. Without Object Lock, evidence is mutable
— breaks the chain.

Task #102 caught a one-time misconfiguration where Object Lock was
NOT enabled on the bucket. This gate prevents the CODE PATH from
regressing — a complementary substrate invariant (Gate A pending)
would check the LIVE bucket state on a tick cadence.

What this gate checks (static AST + source walk):
  1. `EvidenceUploader.__init__` references `_validate_bucket`
  2. `_validate_bucket` checks `ObjectLockEnabled == 'Enabled'`
  3. `_validate_bucket` raises on `ObjectLockConfigurationNotFoundError`

Sibling pattern: `test_privileged_chain_allowed_events_lockstep.py`
(structural pin on operator-discipline rule); `test_email_opacity_
harmonized.py` (structural pin on banned-shape rule).
"""
from __future__ import annotations

import ast
import pathlib

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent.parent.parent
_UPLOADER = _REPO / "mcp-server" / "evidence" / "uploader.py"


def _load_uploader_ast() -> ast.Module:
    src = _UPLOADER.read_text()
    return ast.parse(src)


def _find_class(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} missing from {_UPLOADER}")


def _find_method(cls: ast.ClassDef, name: str) -> ast.FunctionDef:
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(
        f"method {name} missing from class {cls.name} in {_UPLOADER}"
    )


def test_uploader_init_calls_validate_bucket():
    """`EvidenceUploader.__init__` MUST call `_validate_bucket()`.

    The validation guarantees the bucket has Object Lock enabled before
    any evidence is written. Without this call, an operator-misconfigured
    bucket (Object Lock disabled) would silently accept mutable evidence
    uploads — breaking the WORM tamper-evidence guarantee.
    """
    tree = _load_uploader_ast()
    cls = _find_class(tree, "EvidenceUploader")
    init = _find_method(cls, "__init__")
    src = ast.unparse(init)
    assert "_validate_bucket(" in src, (
        f"EvidenceUploader.__init__ MUST invoke self._validate_bucket() "
        f"before returning. WORM tamper-evidence guarantee depends on it. "
        f"Task #102 caught the prod state where Object Lock was disabled — "
        f"this pin prevents the CODE PATH from regressing back. If a "
        f"future refactor inlines the check or moves it, update this test."
    )


def test_validate_bucket_checks_object_lock_enabled_status():
    """`_validate_bucket` MUST check `ObjectLockEnabled == 'Enabled'`.

    The S3 API returns `ObjectLockConfiguration` even for "disabled"
    buckets; only the `ObjectLockEnabled` field distinguishes locked
    from unlocked. Checking only that the configuration EXISTS is
    insufficient.
    """
    tree = _load_uploader_ast()
    cls = _find_class(tree, "EvidenceUploader")
    method = _find_method(cls, "_validate_bucket")
    src = ast.unparse(method)
    assert "ObjectLockEnabled" in src, (
        "_validate_bucket must reference 'ObjectLockEnabled' — the field "
        "that distinguishes a locked bucket from an unlocked one."
    )
    assert "'Enabled'" in src, (
        "_validate_bucket must compare against the literal 'Enabled' "
        "string (S3 API contract). Anything else is permissive."
    )


def test_validate_bucket_handles_no_lock_config_error():
    """`_validate_bucket` MUST raise on `ObjectLockConfigurationNotFoundError`.

    Some bucket-creation paths return success on `head_bucket` but raise
    this specific error on `get_object_lock_configuration`. Catching
    everything as a generic ClientError would silently allow misconfigured
    buckets through. The validator must explicitly fail on this branch.
    """
    tree = _load_uploader_ast()
    cls = _find_class(tree, "EvidenceUploader")
    method = _find_method(cls, "_validate_bucket")
    src = ast.unparse(method)
    assert "ObjectLockConfigurationNotFoundError" in src, (
        "_validate_bucket must explicitly handle "
        "ObjectLockConfigurationNotFoundError — the S3 error code for "
        "buckets where Object Lock was never enabled. Without this "
        "branch, misconfigured buckets silently pass head_bucket."
    )
    # The error branch MUST raise ValueError (operator-visible failure),
    # not silently log/continue.
    method_src_lines = src.splitlines()
    error_block_starts = [
        i for i, line in enumerate(method_src_lines)
        if "ObjectLockConfigurationNotFoundError" in line
    ]
    assert error_block_starts, "block not found"
    # Look at next 5 lines for a raise statement.
    err_block = "\n".join(method_src_lines[error_block_starts[0]:error_block_starts[0] + 5])
    assert "raise" in err_block, (
        "_validate_bucket must `raise` (not log/continue) when the "
        "bucket has no Object Lock configuration. Silent fall-through "
        "would allow misconfigured buckets to proceed."
    )
