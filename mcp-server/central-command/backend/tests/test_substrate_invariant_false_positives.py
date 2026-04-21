"""Regression tests for substrate-invariant false positives that caused
the 2026-04-20 alert storm on north-valley-branch-2 (214 sev1/sev2
emails in 48h, all ghost alerts).

Three fixes verified here:
  1. _parse_semver — correct ordering (0.4.10 > 0.4.9, lexically fails)
  2. _check_agent_version_lag — running > expected is NOT lag
  3. Installer-hostname rows are excluded from offline/freshness/version checks
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from assertions import _parse_semver  # noqa: E402


def test_parse_semver_orders_numerically_not_lexically():
    assert _parse_semver("0.4.10") > _parse_semver("0.4.9")
    assert _parse_semver("0.4.5") > _parse_semver("0.3.91")
    assert _parse_semver("1.0.0") > _parse_semver("0.99.99")


def test_parse_semver_equal():
    assert _parse_semver("0.4.5") == _parse_semver("0.4.5")


def test_parse_semver_strips_prerelease_and_build():
    assert _parse_semver("0.4.5-rc1") == (0, 4, 5)
    assert _parse_semver("0.4.5+build.7") == (0, 4, 5)


def test_parse_semver_handles_bad_input():
    assert _parse_semver(None) is None
    assert _parse_semver("") is None
    assert _parse_semver("not-a-version") is None
    assert _parse_semver("0.a.0") is None


def test_agent_version_lag_query_excludes_installer_and_ahead():
    """Sanity check: the SQL pins hostname IS DISTINCT FROM installer,
    and the Python filter drops anything where running >= expected.
    """
    import assertions
    import inspect

    src = inspect.getsource(assertions._check_agent_version_lag)
    assert "_INSTALLER_HOSTNAME" in src or "osiriscare-installer" in src, (
        "agent_version_lag must exclude installer-hostname rows"
    )
    assert "running >= expected" in src, (
        "agent_version_lag must treat 'ahead' as non-violating"
    )


def test_offline_and_freshness_checks_exclude_installer():
    """The installer hostname belongs to pre-install USB boot sessions
    and must not fire offline/freshness alerts."""
    import assertions
    import inspect

    offline_src = inspect.getsource(assertions._check_offline_appliance_long)
    freshness_src = inspect.getsource(assertions._check_discovered_devices_freshness)
    for name, src in (
        ("offline_appliance_over_1h", offline_src),
        ("discovered_devices_freshness", freshness_src),
    ):
        assert "_INSTALLER_HOSTNAME" in src, (
            f"{name} must filter out hostname={assertions._INSTALLER_HOSTNAME!r}"
        )
