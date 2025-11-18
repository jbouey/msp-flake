"""
Tests for utility functions.
"""

import pytest
from datetime import datetime, time, timedelta
import asyncio

from compliance_agent.utils import (
    MaintenanceWindow,
    apply_jitter,
    CommandResult,
    run_command
)


def test_maintenance_window_basic():
    """Test basic maintenance window check."""
    # Window: 02:00-04:00
    window = MaintenanceWindow(time(2, 0), time(4, 0))

    # Inside window
    assert window.is_in_window(datetime(2025, 11, 6, 2, 30)) is True
    assert window.is_in_window(datetime(2025, 11, 6, 3, 0)) is True
    assert window.is_in_window(datetime(2025, 11, 6, 4, 0)) is True

    # Outside window
    assert window.is_in_window(datetime(2025, 11, 6, 1, 59)) is False
    assert window.is_in_window(datetime(2025, 11, 6, 4, 1)) is False
    assert window.is_in_window(datetime(2025, 11, 6, 12, 0)) is False


def test_maintenance_window_crosses_midnight():
    """Test maintenance window that crosses midnight."""
    # Window: 22:00-02:00
    window = MaintenanceWindow(time(22, 0), time(2, 0))

    # Inside window (before midnight)
    assert window.is_in_window(datetime(2025, 11, 6, 22, 30)) is True
    assert window.is_in_window(datetime(2025, 11, 6, 23, 59)) is True

    # Inside window (after midnight)
    assert window.is_in_window(datetime(2025, 11, 6, 0, 0)) is True
    assert window.is_in_window(datetime(2025, 11, 6, 1, 30)) is True

    # Outside window
    assert window.is_in_window(datetime(2025, 11, 6, 21, 59)) is False
    assert window.is_in_window(datetime(2025, 11, 6, 2, 1)) is False
    assert window.is_in_window(datetime(2025, 11, 6, 12, 0)) is False


def test_next_window_start():
    """Test calculating next window start time."""
    window = MaintenanceWindow(time(2, 0), time(4, 0))

    # Before window today
    now = datetime(2025, 11, 6, 1, 0)
    next_start = window.next_window_start(now)
    assert next_start == datetime(2025, 11, 6, 2, 0)

    # After window today
    now = datetime(2025, 11, 6, 5, 0)
    next_start = window.next_window_start(now)
    assert next_start == datetime(2025, 11, 7, 2, 0)


def test_time_until_window():
    """Test calculating time until window."""
    window = MaintenanceWindow(time(2, 0), time(4, 0))

    # Before window
    now = datetime(2025, 11, 6, 1, 0)
    time_until = window.time_until_window(now)
    assert time_until == timedelta(hours=1)

    # Inside window
    now = datetime(2025, 11, 6, 3, 0)
    time_until = window.time_until_window(now)
    assert time_until == timedelta(0)


def test_apply_jitter():
    """Test jitter application."""
    base = 60
    jitter_pct = 0.1

    # Run multiple times to ensure it varies
    results = set()
    for _ in range(100):
        jittered = apply_jitter(base, jitter_pct)
        results.add(jittered)
        # Should be within ±10% of base
        assert 54 <= jittered <= 66

    # Should have gotten multiple different values
    assert len(results) > 10


@pytest.mark.asyncio
async def test_run_command_success():
    """Test successful command execution."""
    result = await run_command(['echo', 'hello'])

    assert result.success is True
    assert result.exit_code == 0
    assert 'hello' in result.stdout
    assert result.stderr == ''
    assert result.duration_sec > 0


@pytest.mark.asyncio
async def test_run_command_failure():
    """Test failed command execution."""
    # Command that will fail
    with pytest.raises(Exception):  # CalledProcessError
        await run_command(['false'], check=True)

    # With check=False, should return result
    result = await run_command(['false'], check=False)
    assert result.success is False
    assert result.exit_code != 0


@pytest.mark.asyncio
async def test_run_command_timeout():
    """Test command timeout."""
    with pytest.raises(asyncio.TimeoutError):
        await run_command(['sleep', '10'], timeout=1)


def test_command_result():
    """Test CommandResult class."""
    result = CommandResult(
        exit_code=0,
        stdout="output",
        stderr="",
        duration_sec=1.5
    )

    assert result.success is True
    assert result.exit_code == 0
    assert result.stdout == "output"

    # Failed command
    result_fail = CommandResult(
        exit_code=1,
        stdout="",
        stderr="error",
        duration_sec=0.5
    )

    assert result_fail.success is False
