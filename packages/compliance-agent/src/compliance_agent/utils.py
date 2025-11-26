"""
Utility functions for compliance agent.

Includes:
- Maintenance window checks
- Jitter calculation
- NTP offset monitoring
- Process execution helpers
"""

import asyncio
import random
import subprocess
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any
import json
import logging

logger = logging.getLogger(__name__)


class AsyncCommandError(Exception):
    """Error raised when an async command fails."""

    def __init__(self, cmd: list, exit_code: int, stdout: str = "", stderr: str = ""):
        self.cmd = cmd
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(f"Command {cmd} failed with exit code {exit_code}: {stderr}")


class MaintenanceWindow:
    """Check if current time is within maintenance window."""

    def __init__(self, start: time, end: time):
        """
        Initialize maintenance window.

        Args:
            start: Window start time (UTC)
            end: Window end time (UTC)
        """
        self.start = start
        self.end = end

    def is_in_window(self, now: Optional[datetime] = None) -> bool:
        """
        Check if current time is within maintenance window.

        Args:
            now: Time to check (default: datetime.now(timezone.utc))

        Returns:
            True if in window, False otherwise
        """
        if now is None:
            now = datetime.now(timezone.utc)

        current_time = now.time()

        # Handle window that crosses midnight
        if self.start > self.end:
            # e.g., 22:00-02:00
            return current_time >= self.start or current_time <= self.end
        else:
            # e.g., 02:00-04:00
            return self.start <= current_time <= self.end

    def next_window_start(self, now: Optional[datetime] = None) -> datetime:
        """
        Calculate when the next maintenance window starts.

        Args:
            now: Current time (default: datetime.now(timezone.utc))

        Returns:
            Datetime when next window starts
        """
        if now is None:
            now = datetime.now(timezone.utc)

        # Calculate today's window start
        today_start = datetime.combine(now.date(), self.start)

        if now.time() < self.start:
            # Window hasn't started today yet
            return today_start
        else:
            # Window already passed today, return tomorrow's
            return today_start + timedelta(days=1)

    def time_until_window(self, now: Optional[datetime] = None) -> timedelta:
        """
        Calculate time until next maintenance window.

        Args:
            now: Current time (default: datetime.now(timezone.utc))

        Returns:
            Timedelta until next window starts
        """
        if now is None:
            now = datetime.now(timezone.utc)

        if self.is_in_window(now):
            return timedelta(0)

        next_start = self.next_window_start(now)
        return next_start - now


def is_within_maintenance_window(
    window_str: Optional[str],
    now: Optional[datetime] = None
) -> bool:
    """
    Check if current time is within maintenance window.

    Args:
        window_str: Maintenance window string in format "HH:MM-HH:MM" (UTC)
        now: Time to check (default: datetime.now(timezone.utc))

    Returns:
        True if in window or no window defined, False otherwise
    """
    if not window_str:
        return False  # No window defined = not in window

    if now is None:
        now = datetime.now(timezone.utc)

    try:
        start_str, end_str = window_str.split('-')
        start_h, start_m = map(int, start_str.split(':'))
        end_h, end_m = map(int, end_str.split(':'))

        window = MaintenanceWindow(
            start=time(start_h, start_m),
            end=time(end_h, end_m)
        )
        return window.is_in_window(now)
    except (ValueError, AttributeError) as e:
        logger.warning(f"Invalid maintenance window format '{window_str}': {e}")
        return False


def apply_jitter(base_value: int, jitter_pct: float = 0.1) -> int:
    """
    Apply random jitter to a value.

    Args:
        base_value: Base value in seconds
        jitter_pct: Jitter percentage (default 10%)

    Returns:
        Value with ±jitter_pct random variation
    """
    jitter = int(base_value * jitter_pct)
    return base_value + random.randint(-jitter, jitter)


async def get_ntp_offset_ms() -> Optional[int]:
    """
    Get current NTP offset in milliseconds.

    Uses timedatectl to query systemd-timesyncd status.

    Returns:
        NTP offset in milliseconds, or None if unavailable
    """
    try:
        result = await run_command(['timedatectl', 'timesync-status'])

        # Parse output for offset line
        # Example: "Offset: +0.000123s"
        for line in result.stdout.splitlines():
            if 'Offset:' in line:
                # Extract offset value
                offset_str = line.split(':')[1].strip()
                # Remove 's' suffix
                offset_str = offset_str.rstrip('s')
                # Convert to milliseconds
                offset_sec = float(offset_str)
                return int(offset_sec * 1000)

        return None

    except Exception as e:
        logger.warning(f"Failed to get NTP offset: {e}")
        return None


async def is_system_running() -> bool:
    """
    Check if system is in 'running' state.

    Returns:
        True if systemctl is-system-running returns 'running'
    """
    try:
        result = await run_command(['systemctl', 'is-system-running'], check=False)
        return result.stdout.strip() == 'running'
    except Exception as e:
        logger.error(f"Failed to check system running state: {e}")
        return False


async def get_nixos_generation() -> Optional[int]:
    """
    Get current NixOS system generation number.

    Returns:
        Current generation number, or None if unavailable
    """
    try:
        # Current generation is pointed to by /run/current-system
        current_system = Path('/run/current-system')
        if not current_system.exists():
            return None

        # Resolve symlink
        resolved = current_system.resolve()

        # Extract generation number from path
        # Format: /nix/store/<hash>-nixos-system-<hostname>-<generation>
        # Or look at /nix/var/nix/profiles/system-*-link
        profile_dir = Path('/nix/var/nix/profiles')
        system_links = sorted(profile_dir.glob('system-*-link'), reverse=True)

        if system_links:
            # Extract generation from filename
            # Format: system-123-link
            latest = system_links[0].name
            gen_str = latest.replace('system-', '').replace('-link', '')
            return int(gen_str)

        return None

    except Exception as e:
        logger.warning(f"Failed to get NixOS generation: {e}")
        return None


class CommandResult:
    """Result of a command execution."""

    def __init__(
        self,
        exit_code: int,
        stdout: str,
        stderr: str,
        duration_sec: float
    ):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_sec = duration_sec
        self.success = exit_code == 0

    def __repr__(self):
        return f"CommandResult(exit_code={self.exit_code}, success={self.success})"


async def run_command(
    cmd: list[str],
    timeout: Optional[int] = None,
    check: bool = True,
    capture_output: bool = True
) -> CommandResult:
    """
    Run a command asynchronously.

    Args:
        cmd: Command and arguments as list
        timeout: Timeout in seconds (None = no timeout)
        check: Raise exception if exit code != 0
        capture_output: Capture stdout/stderr

    Returns:
        CommandResult with exit code, stdout, stderr, duration

    Raises:
        subprocess.CalledProcessError: If check=True and command fails
        asyncio.TimeoutError: If timeout exceeded
    """
    start_time = datetime.now(timezone.utc)

    try:
        if capture_output:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
        else:
            process = await asyncio.create_subprocess_exec(*cmd)

        # Wait for completion with optional timeout
        if timeout:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )
        else:
            stdout, stderr = await process.communicate()

        duration = (datetime.now(timezone.utc) - start_time).total_seconds()

        result = CommandResult(
            exit_code=process.returncode,
            stdout=stdout.decode('utf-8') if stdout else '',
            stderr=stderr.decode('utf-8') if stderr else '',
            duration_sec=duration
        )

        if check and result.exit_code != 0:
            raise subprocess.CalledProcessError(
                result.exit_code,
                cmd,
                stdout=result.stdout,
                stderr=result.stderr
            )

        return result

    except asyncio.TimeoutError:
        # Kill process on timeout
        try:
            process.kill()
            await process.wait()
        except Exception:
            pass
        raise


async def read_secret_file(path: Path) -> str:
    """
    Read a secret file (async).

    Args:
        path: Path to secret file

    Returns:
        File contents as string (stripped)

    Raises:
        FileNotFoundError: If file doesn't exist
    """
    return await asyncio.to_thread(_read_file_sync, path)


def _read_file_sync(path: Path) -> str:
    """Synchronous file read helper."""
    with open(path, 'r') as f:
        return f.read().strip()


async def write_json_file(path: Path, data: Dict[Any, Any]) -> None:
    """
    Write JSON to file (async).

    Args:
        path: Path to write to
        data: Data to serialize as JSON
    """
    await asyncio.to_thread(_write_json_sync, path, data)


def _write_json_sync(path: Path, data: Dict[Any, Any]) -> None:
    """Synchronous JSON write helper."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, sort_keys=True)


async def read_json_file(path: Path) -> Dict[Any, Any]:
    """
    Read JSON from file (async).

    Args:
        path: Path to read from

    Returns:
        Parsed JSON data
    """
    return await asyncio.to_thread(_read_json_sync, path)


def _read_json_sync(path: Path) -> Dict[Any, Any]:
    """Synchronous JSON read helper."""
    with open(path, 'r') as f:
        return json.load(f)


def setup_logging(log_level: str = 'INFO') -> None:
    """
    Configure logging for the agent.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
    """
    logging.basicConfig(
        level=getattr(logging, log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
