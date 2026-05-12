"""CI gate: SECURITY_ADVISORY filename and internal OSIRIS-* ID must
stay in lockstep.

The Maya P2-M1 finding from the 2026-05-12 P1 Gate B review surfaced a
class of drift: two of the four existing advisories on disk had IDs
that didn't match their filenames — `MERKLE` filename → `MERKLE-COLLISION`
ID; `PACKET_GAP` filename → `PACKET-AUTOGEN-GAP` ID. The drift is
benign in isolation (the auditor kit's `_collect_security_advisories`
indexes by filename, not ID), but compounds when:
  * an auditor cites the ID in a working paper and later can't grep the
    repo by that string
  * a future migration cross-references the ID and ships before the
    underlying advisory is renamed
  * a sibling reference in a runbook or invariant uses the ID form

Canonical form: filename `SECURITY_ADVISORY_<DATE>_<TITLE>.md` MUST have
H1 `# Security Advisory — OSIRIS-<DATE>-<TITLE_WITH_DASHES>`.

Two pre-existing legacy drifters are pinned in `LEGACY_DRIFT_ALLOWLIST`
with the reason; new advisories MUST match canonical form. The allowlist
is a ratchet — to remove an entry you must rename the file (and update
every inbound reference). Adding an entry requires a code-review-visible
justification next to the entry.
"""
from __future__ import annotations

import pathlib
import re

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
_ADVISORY_DIR = _REPO_ROOT / "docs" / "security"

_H1_RE = re.compile(r"^# Security Advisory\s+—\s+(OSIRIS-\S+?)\s*$")


# Legacy drift pinned at the moment this gate landed (2026-05-12). To
# delete an entry: rename the .md file to match canonical form, update
# any inbound references (auditor kit indexes by filename so the ZIP
# layout will change), then remove the line.
LEGACY_DRIFT_ALLOWLIST = {
    # Filename trimmed "COLLISION" to fit the SECURITY_ADVISORY_<DATE>_<TITLE>
    # naming brevity; ID retained the full "MERKLE-COLLISION" disambiguator
    # so it's distinguishable from any future Merkle-related advisory.
    "SECURITY_ADVISORY_2026-04-09_MERKLE.md": "OSIRIS-2026-04-09-MERKLE-COLLISION",
    # Filename was created with the short "PACKET_GAP" while the round-table
    # later landed on "PACKET-AUTOGEN-GAP" as the canonical ID (the gap is
    # specifically in the auto-generation path, not packets generally).
    "SECURITY_ADVISORY_2026-05-02_PACKET_GAP.md": "OSIRIS-2026-05-02-PACKET-AUTOGEN-GAP",
}


def _canonical_id_from_filename(filename: str) -> str:
    stem = filename.removeprefix("SECURITY_ADVISORY_").removesuffix(".md")
    return "OSIRIS-" + stem.replace("_", "-")


def _read_h1(path: pathlib.Path) -> str:
    with path.open() as f:
        return f.readline().rstrip("\n")


def test_every_advisory_has_canonical_id():
    advisories = sorted(_ADVISORY_DIR.glob("SECURITY_ADVISORY_*.md"))
    assert advisories, (
        f"No advisories found at {_ADVISORY_DIR}. If you moved the "
        "directory, update _ADVISORY_DIR in this test."
    )

    failures: list[str] = []
    for md in advisories:
        first_line = _read_h1(md)
        m = _H1_RE.match(first_line)
        if not m:
            failures.append(
                f"{md.name}: first line must match "
                "'# Security Advisory — OSIRIS-<DATE>-<TITLE>'; "
                f"got: {first_line!r}"
            )
            continue
        actual = m.group(1).rstrip(".,;:")
        expected = _canonical_id_from_filename(md.name)
        if actual == expected:
            continue
        legacy = LEGACY_DRIFT_ALLOWLIST.get(md.name)
        if legacy is not None:
            if legacy != actual:
                failures.append(
                    f"{md.name}: legacy allowlist no longer matches. "
                    f"allowlist says {legacy!r}, file H1 says {actual!r}. "
                    "Either update the file to match the allowlist, OR "
                    "update LEGACY_DRIFT_ALLOWLIST + cite the renaming "
                    "round-table decision."
                )
            continue
        failures.append(
            f"{md.name}: H1 ID {actual!r} != canonical "
            f"{expected!r}. Either (a) rename the file to "
            f"SECURITY_ADVISORY_<canonicalized-{actual}>.md and update "
            "any references, OR (b) add to LEGACY_DRIFT_ALLOWLIST with a "
            "comment explaining the deliberate drift."
        )

    assert not failures, (
        "Advisory ID/filename parity violations:\n  "
        + "\n  ".join(failures)
    )


def test_legacy_allowlist_is_a_ratchet():
    """LEGACY_DRIFT_ALLOWLIST entries must reference files that still
    exist on disk. Once a legacy drifter is renamed canonical, its
    allowlist entry MUST be deleted (otherwise the ratchet is meaningless).
    """
    missing = [
        fname
        for fname in LEGACY_DRIFT_ALLOWLIST
        if not (_ADVISORY_DIR / fname).exists()
    ]
    assert not missing, (
        "LEGACY_DRIFT_ALLOWLIST has stale entries (file not on disk):\n  "
        + "\n  ".join(missing)
        + "\nDelete each stale entry — the file has been renamed/removed."
    )


def test_no_emojis_in_advisory_h1():
    """Advisory H1 must contain only the em-dash separator and the
    OSIRIS-* ID. Catches drift where an author adds an emoji or
    severity marker into the title line — auditor working papers
    quote the H1 verbatim and any non-ASCII character breaks
    text-match across systems.
    """
    failures: list[str] = []
    allowed_non_ascii = {"—"}  # em-dash separator
    for md in sorted(_ADVISORY_DIR.glob("SECURITY_ADVISORY_*.md")):
        first_line = _read_h1(md)
        bad = [
            ch
            for ch in first_line
            if ord(ch) > 127 and ch not in allowed_non_ascii
        ]
        if bad:
            failures.append(
                f"{md.name}: H1 contains non-ASCII characters "
                f"{bad!r}; only em-dash is allowed."
            )
    assert not failures, (
        "Non-ASCII characters in advisory H1:\n  " + "\n  ".join(failures)
    )
