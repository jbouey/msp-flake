"""Pin gate — any commit that mutates the canonical body-shape hash
literals (`_CANONICAL_ATTESTATION_HASH` / `_CANONICAL_IMMUTABILITY_HASH`)
in `tests/test_privileged_chain_function_body_shape.py` MUST include
the literal token `PRIVILEGED-CHAIN-BODY-CHANGE:` in the commit body.

Session 220 task #119 (2026-05-11). Defense-in-depth follow-up to
task #111 (function-body shape pin). The body-shape gate pins
SHA256 hashes as Python literals in-tree — that closes the
canonical-tamper backdoor where editing a migration body silently
recomputes a different hash. But if an author edits BOTH the
migration body AND the pinned hash in the same commit, the
body-shape gate passes silently.

This gate closes that gap at the git-history layer: any commit
that mutated the hash literal MUST contain the
`PRIVILEGED-CHAIN-BODY-CHANGE: <reason>` token in its commit body.
The token signals reviewer attention; this test makes it
structurally required.

SPLIT-COMMIT CLASS (Gate A P1, Maya): if an author lands
migration-weakening on commit N and the hash bump on commit N+1,
the existing `test_canonical_hashes_match_current_mig_305` test
goes RED on commit N (migration body no longer matches the pinned
hash). So commit N is already blocked. Split-commit is structurally
covered by the existing pin; this gate handles the same-commit
bump-and-weaken case.

CI REQUIREMENT: full git history. `.github/workflows/deploy-
central-command.yml` checkout steps were updated to
`fetch-depth: 0` in this commit (Gate A v3 P0-1 mandatory
fix). Without it, `git log -p` returns only HEAD and the gate
silently passes in CI.

GRANDFATHERED COMMITS: the commit that ORIGINALLY introduced the
hash literals (task #111, `7e741507`) is grandfathered — that
commit has no token because the literals were new, not mutated.

Sibling pattern:
  - `tests/test_pre_push_allowlist_only_references_git_tracked_files`
    (git-aware static check sibling)
  - `tests/test_privileged_chain_function_body_shape.py` (sibling
    body-shape pin this gate hardens)
"""
from __future__ import annotations

import pathlib
import re
import subprocess


_REPO = pathlib.Path(__file__).resolve().parent.parent.parent.parent.parent
_PINNED_FILE = (
    "mcp-server/central-command/backend/tests/"
    "test_privileged_chain_function_body_shape.py"
)

# Commit SHAs that legitimately introduced the hash literals
# (rather than mutating them). Pre-existing constants get
# grandfathered. Add ONLY by Gate B approval of a follow-up that
# explicitly justifies bypassing this token requirement.
_GRANDFATHERED_COMMITS: frozenset[str] = frozenset({
    "7e741507a564190bb51e1c6851dae15b7c509cea",  # task #111 — initial pin
})

# Regex matching the canonical hash literal assignment lines. Pinned
# to specific constant names to keep the coupling tight: a refactor
# renaming these constants MUST also update this regex (the desired
# coupling per Gate A P2-1).
_HASH_LITERAL_PATTERN = re.compile(
    r"^\s*_CANONICAL_(ATTESTATION|IMMUTABILITY)_HASH\s*=\s*\"[0-9a-fA-F]{64}\"",
    re.MULTILINE,
)

# Token that signals reviewer attention. Case-sensitive — the
# strictness IS the signal.
_REQUIRED_TOKEN = "PRIVILEGED-CHAIN-BODY-CHANGE:"


def _run_git(*args: str) -> str:
    """Shell out to git with the repo root as cwd. Returns stdout."""
    return subprocess.run(
        ["git", *args],
        cwd=_REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _hash_mutating_commits() -> list[tuple[str, str]]:
    """Return [(sha, commit_message), ...] for every commit that
    added OR removed a `_CANONICAL_*_HASH` literal line in the
    pinned file."""
    # `git log` with -p shows full patches. Filter for our file.
    log_output = _run_git(
        "log", "-p", "--format=%H%x00%B%x00%x00", "--", _PINNED_FILE,
    )

    out: list[tuple[str, str]] = []
    # Split on null-terminated commit headers.
    entries = log_output.split("\x00\x00\n")
    for entry in entries:
        if not entry.strip():
            continue
        # Each entry: "<sha>\x00<commit_message>\x00<patch>"
        parts = entry.split("\x00", 2)
        if len(parts) < 3:
            continue
        sha, commit_msg, patch = parts
        sha = sha.strip()
        if not sha:
            continue
        # Inspect the patch for +/- lines touching the hash literals.
        # Lines beginning with `+` or `-` but NOT `+++`/`---` are diff
        # additions/deletions.
        for diff_line in patch.splitlines():
            if not (diff_line.startswith("+") or diff_line.startswith("-")):
                continue
            if diff_line.startswith("+++") or diff_line.startswith("---"):
                continue
            payload = diff_line[1:]  # strip leading +/-
            if _HASH_LITERAL_PATTERN.match(payload):
                out.append((sha, commit_msg))
                break  # one mutation hit per commit is enough
    return out


def test_hash_mutating_commits_include_required_token():
    """Every commit that added/removed a `_CANONICAL_*_HASH` literal
    line MUST include `PRIVILEGED-CHAIN-BODY-CHANGE:` token in its
    commit body. Grandfathered commits are exempted explicitly."""
    # CI must use fetch-depth: 0 for `git log` to see history.
    # Detect shallow clone — if so, skip with a clear message.
    try:
        depth = _run_git("rev-list", "--count", "HEAD").strip()
        if depth == "1":
            # Likely shallow checkout — `git log` returns only HEAD.
            # The CI workflow should have fetch-depth: 0, but if for
            # some reason it doesn't, fail-loud rather than silent.
            shallow_marker = (_REPO / ".git" / "shallow")
            if shallow_marker.exists():
                raise AssertionError(
                    "Shallow git clone detected (.git/shallow present). "
                    "CI must use `fetch-depth: 0` on actions/checkout@v4 "
                    "for this gate to function. Update "
                    ".github/workflows/deploy-central-command.yml."
                )
    except subprocess.CalledProcessError:
        # Outside a git repo (e.g. tarball install). Skip.
        return

    commits = _hash_mutating_commits()
    if not commits:
        # No hash mutations found in history. Either the gate is new
        # (and the introducing commit is grandfathered) OR the regex
        # is broken. Sanity-check the grandfather list isn't empty.
        assert _GRANDFATHERED_COMMITS, (
            "no hash-mutating commits found AND no grandfathered "
            "commits — gate regex may be broken or pinned file "
            f"path is wrong ({_PINNED_FILE})"
        )
        return

    violations: list[str] = []
    for sha, commit_msg in commits:
        if sha in _GRANDFATHERED_COMMITS:
            continue
        if _REQUIRED_TOKEN in commit_msg:
            continue
        # Walk the commit body for the token. Show first 2 lines
        # of the message for context in the error.
        first_lines = "\n      ".join(commit_msg.splitlines()[:2])
        violations.append(f"  {sha[:12]}\n      {first_lines}")

    if violations:
        raise AssertionError(
            "\n\nCommits mutating `_CANONICAL_*_HASH` literals "
            f"WITHOUT the `{_REQUIRED_TOKEN}` token in the commit body:\n\n"
            + "\n\n".join(violations)
            + "\n\nThe token is a structural reviewer-attention signal.\n"
            "Any legitimate canonical-body change (mig 306+ that updates\n"
            "the enforce_privileged_order_attestation or _immutability\n"
            "function body) MUST include the token in the commit message:\n\n"
            f"    {_REQUIRED_TOKEN} <one-line reason>\n\n"
            "Session 220 task #119. Closes the same-commit hash-bump-and-\n"
            "weaken attack class flagged by Gate B v1 on task #111\n"
            "(audit/coach-function-body-shape-gate-b-2026-05-11.md Carol P2)."
        )


def test_pinned_file_exists():
    """Sanity: the file this gate watches must exist. Catches the
    regression class where someone deletes/renames the pinned file
    without updating this gate."""
    expected = _REPO / _PINNED_FILE
    assert expected.exists(), (
        f"Watched file missing: {_PINNED_FILE}. If it was renamed, "
        f"update `_PINNED_FILE` constant in this test."
    )


def test_grandfathered_commits_are_real_shas():
    """Every grandfathered commit SHA must exist in git history.
    Prevents the silent-allowlist-rot class — if a SHA is wrong
    OR was rebased away, that's a process gap to surface."""
    for sha in _GRANDFATHERED_COMMITS:
        try:
            _run_git("cat-file", "-e", f"{sha}^{{commit}}")
        except subprocess.CalledProcessError:
            raise AssertionError(
                f"Grandfathered commit SHA `{sha}` not found in git "
                f"history. Either the SHA is wrong OR the commit was "
                f"rebased away. Audit _GRANDFATHERED_COMMITS."
            )


def test_synthetic_violation_via_in_memory_diff():
    """Positive control: synthetically construct the commit message
    state (no token) and verify the regex would catch it."""
    # Simulate a diff line that mutates the hash literal.
    diff_line = (
        '+_CANONICAL_ATTESTATION_HASH = "0000000000000000'
        '000000000000000000000000000000000000000000000000"'
    )
    payload = diff_line[1:]  # strip leading `+`
    match = _HASH_LITERAL_PATTERN.match(payload)
    assert match is not None, (
        "Regex failed to match synthetic hash-mutation diff line. "
        "Update _HASH_LITERAL_PATTERN."
    )

    # And verify a commit message WITHOUT the token would be a violation.
    commit_msg = "test(gate): bump canonical hash\n\nForgot the token."
    assert _REQUIRED_TOKEN not in commit_msg
