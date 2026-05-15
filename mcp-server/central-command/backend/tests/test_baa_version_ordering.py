"""Pin BAA version comparison to numeric, not lexical (Task #52 P1-1).

`baa_status.baa_enforcement_ok()` decides whether a signature's
`baa_version` is "current or later" by comparing the parsed
(major, minor) tuple from `_parse_baa_version()`. A naive lexical
string compare would put `v10.0` BELOW `v2.0` — so once the BAA
reaches a two-digit major version, a current signature would be
silently rejected and every org blocked.

This gate pins the numeric-comparison contract so a future refactor
can't regress it to a string compare.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import baa_status  # noqa: E402


def test_parse_known_version_shapes():
    """The shapes that actually appear in baa_signatures.baa_version."""
    assert baa_status._parse_baa_version("v1.0-INTERIM") == (1, 0)
    assert baa_status._parse_baa_version("v1.0-2026-04-15") == (1, 0)
    assert baa_status._parse_baa_version("v2.0") == (2, 0)
    assert baa_status._parse_baa_version("1.0") == (1, 0)  # no 'v' prefix


def test_unparseable_sorts_below_everything():
    """None / garbage must sort below any real version so an
    unparseable signature never satisfies the gate (fail-closed)."""
    assert baa_status._parse_baa_version(None) == (-1, -1)
    assert baa_status._parse_baa_version("") == (-1, -1)
    assert baa_status._parse_baa_version("garbage") == (-1, -1)
    assert baa_status._parse_baa_version(None) < baa_status._parse_baa_version("v1.0-INTERIM")


def test_two_digit_major_orders_numerically_not_lexically():
    """The core bug class: lexical compare puts 'v10.0' < 'v2.0'.
    Numeric (major, minor) tuple compare must put v10 ABOVE v2."""
    v2 = baa_status._parse_baa_version("v2.0")
    v10 = baa_status._parse_baa_version("v10.0")
    assert v10 > v2, "v10.0 must order ABOVE v2.0 (numeric, not lexical)"
    # And the lexical trap, made explicit so the intent is undeniable:
    assert "v10.0" < "v2.0", "sanity: lexical compare IS broken — that's why we parse"


def test_minor_version_ordering():
    assert baa_status._parse_baa_version("v2.1") > baa_status._parse_baa_version("v2.0")
    assert baa_status._parse_baa_version("v2.0") > baa_status._parse_baa_version("v1.9")


def test_current_required_version_is_parseable():
    """CURRENT_REQUIRED_BAA_VERSION must itself parse to a real
    (>= (0,0)) tuple — a typo'd constant would make every org pass
    or fail wrongly."""
    parsed = baa_status._parse_baa_version(baa_status.CURRENT_REQUIRED_BAA_VERSION)
    assert parsed >= (1, 0), (
        f"CURRENT_REQUIRED_BAA_VERSION="
        f"{baa_status.CURRENT_REQUIRED_BAA_VERSION!r} parsed to {parsed} — "
        f"must be a real version >= (1, 0)"
    )
