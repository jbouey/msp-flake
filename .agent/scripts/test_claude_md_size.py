"""Ratchet CLAUDE.md size to prevent regrowth.

CLAUDE.md is loaded into the model's system prompt on every session
start. Anthropic's published guidance is ~5-10k chars; the model's
attention degrades measurably past ~40k. This file is a ratchet:
the current measured size is the new ceiling.

Session 215 (2026-05-04) extracted Sessions 200-209 prose to
docs/lessons/sessions-200-209.md, taking the file from 117k to ~78k.
Future batches will continue extracting Sessions 210+ until under 40k.

When you legitimately need to add bytes (a new INVIOLABLE rule, a new
top-level section), bump CEILING_BYTES by the size of the addition.
When you extract bytes to a docs/lessons/ file, drop CEILING_BYTES
to the new measured size. Either way the ratchet only moves
intentionally — silent regrowth fails CI.

Long-term target: CEILING_BYTES <= 40000 (Anthropic-recommended).
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"

# Set 2026-05-04 (Session 215, batches 1-3 extraction complete:
# Sessions 200-209, 210-212, 213-215 → docs/lessons/). Now under
# Anthropic's 40k recommendation. Drop further only when extracting
# more content; do NOT raise without an explicit reason in the
# commit message.
CEILING_BYTES = 31000

# Long-term goal — fails CI as a soft warning when CEILING_BYTES > this.
ANTHROPIC_RECOMMENDED_BYTES = 40000


def test_claude_md_under_ceiling():
    actual = CLAUDE_MD.stat().st_size
    assert actual <= CEILING_BYTES, (
        f"CLAUDE.md grew to {actual} bytes (ceiling {CEILING_BYTES}). "
        f"Either extract content to docs/lessons/<topic>.md and drop "
        f"CEILING_BYTES, OR raise CEILING_BYTES intentionally if this "
        f"is a new INVIOLABLE rule. Silent regrowth dilutes attention "
        f"on every session start."
    )


def test_claude_md_progress_toward_anthropic_target():
    # Soft tracker: skip when already under target, fail when ceiling
    # somehow grew above the original 117k baseline (catches a wholesale
    # revert of the extraction work).
    if CEILING_BYTES <= ANTHROPIC_RECOMMENDED_BYTES:
        pytest.skip("Already under Anthropic-recommended size")
    assert CEILING_BYTES <= 117000, (
        "CEILING_BYTES is back above the pre-extraction baseline. "
        "Did batch 1 of the extraction get reverted? See "
        "docs/lessons/sessions-200-209.md and Session 215 commit log."
    )


def test_lessons_file_present():
    """The extracted lessons file must exist as long as CLAUDE.md
    references it."""
    lessons = REPO_ROOT / "docs/lessons/sessions-200-209.md"
    claude_text = CLAUDE_MD.read_text()
    if "sessions-200-209.md" in claude_text:
        assert lessons.exists(), (
            f"CLAUDE.md references {lessons} but the file is missing. "
            f"Either restore the lessons file or remove the reference."
        )
