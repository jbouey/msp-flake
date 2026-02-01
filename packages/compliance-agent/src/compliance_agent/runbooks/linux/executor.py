"""
Linux Runbook Executor via SSH.

Executes Bash runbooks on Linux servers (Ubuntu/RHEL) using asyncssh.
Follows the same patterns as the Windows executor for consistency.

Features:
- Async SSH via asyncssh library
- Connection pooling and session caching
- Automatic distro detection (Ubuntu vs RHEL)
- Evidence collection for compliance
- Retry with exponential backoff
- Timeout handling

Version: 1.0
"""

import asyncio
import logging
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

from compliance_agent.phi_scrubber import PHIScrubber

logger = logging.getLogger(__name__)

# Initialize PHI scrubber for output sanitization
_phi_scrubber = PHIScrubber(hash_redacted=True)


@dataclass
class LinuxTarget:
    """Linux server target configuration."""
    hostname: str
    port: int = 22
    username: str = "root"
    password: Optional[str] = None
    private_key: Optional[str] = None  # PEM-encoded SSH key content
    private_key_path: Optional[str] = None  # Path to key file
    sudo_password: Optional[str] = None  # For non-root users
    distro: Optional[str] = None  # ubuntu, rhel, detected at runtime
    connect_timeout: int = 30
    command_timeout: int = 60

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding sensitive fields."""
        return {
            "hostname": self.hostname,
            "port": self.port,
            "username": self.username,
            "distro": self.distro,
            "has_password": bool(self.password),
            "has_private_key": bool(self.private_key or self.private_key_path),
        }


@dataclass
class LinuxExecutionResult:
    """Result of runbook execution with evidence tracking."""
    success: bool
    runbook_id: str
    target: str
    phase: str  # detect, remediate, verify
    output: Dict[str, Any]
    duration_seconds: float
    error: Optional[str] = None
    timestamp: str = ""
    output_hash: str = ""  # SHA256 of output for evidence
    retry_count: int = 0
    hipaa_controls: List[str] = field(default_factory=list)
    distro: str = ""
    exit_code: int = 0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()
        if not self.output_hash and self.output:
            self.output_hash = hashlib.sha256(
                json.dumps(self.output, sort_keys=True).encode()
            ).hexdigest()[:16]

    def to_evidence(self) -> Dict[str, Any]:
        """Convert to evidence bundle format."""
        return {
            "execution_id": f"{self.runbook_id}-{self.target}-{self.timestamp}",
            "runbook_id": self.runbook_id,
            "target": self.target,
            "phase": self.phase,
            "success": self.success,
            "duration_seconds": self.duration_seconds,
            "timestamp": self.timestamp,
            "output_hash": self.output_hash,
            "hipaa_controls": self.hipaa_controls,
            "error": self.error,
            "retry_count": self.retry_count,
            "distro": self.distro,
            "exit_code": self.exit_code,
        }


class LinuxExecutor:
    """
    Execute Linux Bash runbooks via SSH.

    Uses asyncssh for async SSH communication.
    Supports password and key-based authentication.

    Features:
    - Automatic distro detection (Ubuntu, RHEL, CentOS)
    - Session caching for performance
    - sudo support for non-root users
    - Evidence collection for audit trails
    - Timeout handling
    """

    def __init__(
        self,
        targets: Optional[List[LinuxTarget]] = None,
        default_retries: int = 2,
        retry_backoff: float = 1.5
    ):
        """
        Initialize executor.

        Args:
            targets: List of Linux targets to manage
            default_retries: Default number of retry attempts
            retry_backoff: Multiplier for retry delay (exponential backoff)
        """
        self.targets: Dict[str, LinuxTarget] = {}
        if targets:
            for target in targets:
                self.targets[target.hostname] = target

        self._connection_cache: Dict[str, Any] = {}
        self._connection_timestamps: Dict[str, datetime] = {}
        self._distro_cache: Dict[str, str] = {}
        self._default_retries = default_retries
        self._retry_backoff = retry_backoff
        self._connection_max_age_seconds = 300  # Refresh after 5 minutes

    def add_target(self, target: LinuxTarget):
        """Add a Linux target."""
        self.targets[target.hostname] = target

    def remove_target(self, hostname: str):
        """Remove a Linux target."""
        self.targets.pop(hostname, None)
        self._connection_cache.pop(hostname, None)
        self._distro_cache.pop(hostname, None)

    async def _get_connection(self, target: LinuxTarget, force_new: bool = False):
        """
        Get or create SSH connection for target.

        Args:
            target: Linux target configuration
            force_new: Force creation of new connection

        Returns:
            asyncssh.SSHClientConnection object
        """
        try:
            import asyncssh
        except ImportError:
            raise ImportError(
                "asyncssh is required for Linux execution. "
                "Install with: pip install asyncssh"
            )

        cache_key = target.hostname

        # Check if connection is stale
        if cache_key in self._connection_timestamps:
            age = (datetime.now(timezone.utc) - self._connection_timestamps[cache_key]).total_seconds()
            if age > self._connection_max_age_seconds:
                logger.debug(f"Connection for {target.hostname} is stale ({age:.0f}s), refreshing")
                force_new = True

        # Check if existing connection is still valid
        if not force_new and cache_key in self._connection_cache:
            conn = self._connection_cache[cache_key]
            try:
                # Quick test if connection is alive
                if not conn.is_closed():
                    return conn
                else:
                    logger.debug(f"Connection to {target.hostname} was closed, reconnecting")
            except Exception:
                pass

        # Close old connection if exists
        if cache_key in self._connection_cache:
            try:
                old_conn = self._connection_cache.pop(cache_key)
                old_conn.close()
            except Exception:
                pass

        logger.debug(f"Creating new SSH connection to {target.hostname}:{target.port}")

        # Build connection options
        connect_options = {
            "host": target.hostname,
            "port": target.port,
            "username": target.username,
            "known_hosts": None,  # Accept any host key (prod should verify)
            "connect_timeout": target.connect_timeout,
        }

        # Authentication
        if target.private_key:
            connect_options["client_keys"] = [asyncssh.import_private_key(target.private_key)]
        elif target.private_key_path:
            connect_options["client_keys"] = [target.private_key_path]
        elif target.password:
            connect_options["password"] = target.password

        try:
            conn = await asyncssh.connect(**connect_options)
            self._connection_cache[cache_key] = conn
            self._connection_timestamps[cache_key] = datetime.now(timezone.utc)
            return conn
        except asyncssh.PermissionDenied as e:
            logger.error(f"SSH authentication failed to {target.hostname}: {e}")
            raise
        except asyncssh.HostKeyNotVerifiable as e:
            logger.error(f"SSH host key verification failed for {target.hostname}: {e}")
            raise
        except asyncssh.ConnectionLost as e:
            logger.error(f"SSH connection lost to {target.hostname}: {e}")
            raise
        except asyncssh.Error as e:
            logger.error(f"SSH error connecting to {target.hostname}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error connecting to {target.hostname}: {type(e).__name__}: {e}")
            raise

    def invalidate_connection(self, hostname: str):
        """Invalidate cached connection for hostname."""
        if hostname in self._connection_cache:
            try:
                self._connection_cache[hostname].close()
            except Exception:
                pass
        self._connection_cache.pop(hostname, None)
        self._connection_timestamps.pop(hostname, None)

    async def detect_distro(self, target: LinuxTarget) -> str:
        """
        Detect Linux distribution.

        Returns:
            'ubuntu', 'rhel', 'centos', 'debian', or 'unknown'
        """
        if target.hostname in self._distro_cache:
            return self._distro_cache[target.hostname]

        if target.distro:
            self._distro_cache[target.hostname] = target.distro
            return target.distro

        script = """
        if [ -f /etc/os-release ]; then
            . /etc/os-release
            echo "$ID"
        elif [ -f /etc/redhat-release ]; then
            echo "rhel"
        elif [ -f /etc/debian_version ]; then
            echo "debian"
        else
            echo "unknown"
        fi
        """

        result = await self.execute_script(target, script, timeout=15, retries=1)
        if result.success:
            distro = result.output.get("stdout", "").strip().lower()
            # Normalize distro names
            if distro in ("centos", "rocky", "almalinux", "fedora"):
                distro = "rhel"
            elif distro in ("ubuntu", "debian", "linuxmint"):
                distro = "ubuntu"

            self._distro_cache[target.hostname] = distro
            target.distro = distro
            return distro

        return "unknown"

    async def execute_script(
        self,
        target: LinuxTarget,
        script: str,
        timeout: int = 60,
        retries: int = 0,
        retry_delay: float = 5.0,
        use_sudo: bool = False
    ) -> LinuxExecutionResult:
        """
        Execute Bash script on target with retry support.

        Args:
            target: Linux target
            script: Bash script to execute
            timeout: Execution timeout in seconds
            retries: Number of retry attempts on failure
            retry_delay: Initial delay between retries
            use_sudo: Wrap command with sudo

        Returns:
            LinuxExecutionResult with script output
        """
        if retries == 0:
            retries = self._default_retries

        last_error = None
        retry_count = 0

        for attempt in range(retries + 1):
            start_time = datetime.now(timezone.utc)

            try:
                import asyncssh  # For specific exception handling
                conn = await self._get_connection(target)

                # Wrap with sudo if needed and execute
                # Use base64 encoding to avoid shell quoting issues
                import base64
                encoded_script = base64.b64encode(script.encode()).decode()

                if use_sudo and target.username != "root":
                    if target.sudo_password:
                        # SECURITY: Use stdin for sudo password instead of command line
                        # This prevents password exposure in process listings and logs
                        # The -S flag reads password from stdin
                        cmd = f"sudo -S bash -c \"$(echo {encoded_script} | base64 -d)\""
                        # Pass password via stdin during execution
                        result = await asyncio.wait_for(
                            conn.run(cmd, check=False, input=target.sudo_password + "\n"),
                            timeout=timeout
                        )
                    else:
                        cmd = f"sudo bash -c \"$(echo {encoded_script} | base64 -d)\""
                        result = await asyncio.wait_for(
                            conn.run(cmd, check=False),
                            timeout=timeout
                        )
                else:
                    cmd = f"bash -c \"$(echo {encoded_script} | base64 -d)\""
                    result = await asyncio.wait_for(
                        conn.run(cmd, check=False),
                        timeout=timeout
                    )

                duration = (datetime.now(timezone.utc) - start_time).total_seconds()

                # Scrub PHI/PII from output before processing
                stdout_scrubbed, stdout_result = _phi_scrubber.scrub(result.stdout or "")
                stderr_scrubbed, stderr_result = _phi_scrubber.scrub(result.stderr or "")

                if stdout_result.phi_scrubbed or stderr_result.phi_scrubbed:
                    logger.info(
                        f"PHI scrubbed from output: stdout={stdout_result.patterns_matched}, "
                        f"stderr={stderr_result.patterns_matched}"
                    )

                output = {
                    "stdout": stdout_scrubbed,
                    "stderr": stderr_scrubbed,
                    "exit_code": result.exit_status,
                    "success": result.exit_status == 0,
                    "phi_scrubbed": stdout_result.phi_scrubbed or stderr_result.phi_scrubbed,
                }

                # Try to parse JSON from stdout (after scrubbing)
                if output["stdout"]:
                    try:
                        output["parsed"] = json.loads(output["stdout"])
                    except json.JSONDecodeError:
                        output["parsed"] = None

                return LinuxExecutionResult(
                    success=result.exit_status == 0,
                    runbook_id="",
                    target=target.hostname,
                    phase="execute",
                    output=output,
                    duration_seconds=duration,
                    exit_code=result.exit_status,
                    retry_count=retry_count,
                    distro=self._distro_cache.get(target.hostname, "")
                )

            except asyncio.TimeoutError:
                last_error = f"Execution timed out after {timeout}s"
                logger.warning(f"Timeout on {target.hostname}, attempt {attempt + 1}/{retries + 1}")

            except asyncssh.PermissionDenied as e:
                last_error = f"SSH authentication failed: {e}"
                logger.error(f"Auth failure on {target.hostname}: {e}")
                self.invalidate_connection(target.hostname)
                # Don't retry auth failures - they won't succeed
                break

            except asyncssh.ConnectionLost as e:
                last_error = f"SSH connection lost: {e}"
                logger.warning(f"Connection lost on {target.hostname}: {e}, attempt {attempt + 1}/{retries + 1}")
                self.invalidate_connection(target.hostname)

            except asyncssh.Error as e:
                last_error = f"SSH error: {e}"
                logger.warning(f"SSH error on {target.hostname}: {e}, attempt {attempt + 1}/{retries + 1}")
                self.invalidate_connection(target.hostname)

            except Exception as e:
                last_error = str(e)
                logger.warning(f"Unexpected error on {target.hostname}: {e}, attempt {attempt + 1}/{retries + 1}")

            # Wait before retrying
            if attempt < retries:
                delay = retry_delay * (self._retry_backoff ** attempt)
                logger.info(f"Retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
                retry_count += 1

        # All retries exhausted
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        return LinuxExecutionResult(
            success=False,
            runbook_id="",
            target=target.hostname,
            phase="execute",
            output={},
            duration_seconds=duration,
            error=last_error,
            exit_code=-1,
            retry_count=retry_count
        )

    async def run_runbook(
        self,
        target: LinuxTarget,
        runbook_id: str,
        phases: Optional[List[str]] = None,
        collect_evidence: bool = True
    ) -> List[LinuxExecutionResult]:
        """
        Execute a runbook on target with evidence collection.

        Args:
            target: Linux target
            runbook_id: Runbook ID to execute
            phases: Which phases to run (detect, remediate, verify)
            collect_evidence: Whether to collect pre/post state

        Returns:
            List of LinuxExecutionResult for each phase
        """
        from .runbooks import get_runbook

        runbook = get_runbook(runbook_id)
        if not runbook:
            return [LinuxExecutionResult(
                success=False,
                runbook_id=runbook_id,
                target=target.hostname,
                phase="init",
                output={},
                duration_seconds=0,
                error=f"Runbook not found: {runbook_id}"
            )]

        if phases is None:
            phases = ["detect", "remediate", "verify"]

        # Detect distro for distro-specific scripts
        distro = await self.detect_distro(target)

        results = []

        # Collect pre-state if enabled
        if collect_evidence and "remediate" in phases:
            pre_state = await self._capture_system_state(target, runbook_id)
            if pre_state:
                results.append(pre_state)

        for phase in phases:
            script = self._get_phase_script(runbook, phase, distro)
            if not script:
                continue

            logger.info(f"Executing {runbook_id} phase={phase} on {target.hostname} ({distro})")

            start_time = datetime.now(timezone.utc)

            try:
                exec_result = await self.execute_script(
                    target,
                    script,
                    timeout=runbook.timeout_seconds,
                    retries=2,
                    use_sudo=runbook.requires_sudo
                )

                result = LinuxExecutionResult(
                    success=exec_result.success,
                    runbook_id=runbook_id,
                    target=target.hostname,
                    phase=phase,
                    output=exec_result.output,
                    duration_seconds=exec_result.duration_seconds,
                    error=exec_result.error,
                    retry_count=exec_result.retry_count,
                    hipaa_controls=runbook.hipaa_controls,
                    distro=distro,
                    exit_code=exec_result.exit_code
                )

            except Exception as e:
                duration = (datetime.now(timezone.utc) - start_time).total_seconds()
                # Categorize error type for better debugging
                error_type = type(e).__name__
                error_msg = f"{error_type}: {str(e)}"
                logger.error(f"Runbook {runbook_id} phase {phase} failed on {target.hostname}: {error_msg}")
                result = LinuxExecutionResult(
                    success=False,
                    runbook_id=runbook_id,
                    target=target.hostname,
                    phase=phase,
                    output={},
                    duration_seconds=duration,
                    error=error_msg,
                    hipaa_controls=runbook.hipaa_controls,
                    distro=distro
                )

            results.append(result)

            # If detection shows compliant, skip remediation
            if phase == "detect" and result.success:
                stdout = result.output.get("stdout", "")
                if "COMPLIANT" in stdout or "NO_DRIFT" in stdout:
                    logger.info(f"No drift detected on {target.hostname}, skipping remediation")
                    break

            # If any non-detect phase fails, stop execution
            if not result.success and phase != "detect":
                logger.warning(f"Phase {phase} failed on {target.hostname}, stopping runbook")
                break

        # Collect post-state if enabled and remediation occurred
        if collect_evidence and "remediate" in phases:
            remediate_results = [r for r in results if r.phase == "remediate"]
            if remediate_results and remediate_results[0].success:
                post_state = await self._capture_system_state(target, runbook_id, "post_state")
                if post_state:
                    results.append(post_state)

        return results

    def _get_phase_script(self, runbook, phase: str, distro: str) -> Optional[str]:
        """Get script for runbook phase, with distro-specific handling."""
        if phase == "detect":
            return runbook.detect_script
        elif phase == "remediate":
            # Check for distro-specific remediation
            if distro == "ubuntu" and runbook.remediate_ubuntu:
                return runbook.remediate_ubuntu
            elif distro in ("rhel", "centos") and runbook.remediate_rhel:
                return runbook.remediate_rhel
            return runbook.remediate_script
        elif phase == "verify":
            return runbook.verify_script
        return None

    async def _capture_system_state(
        self,
        target: LinuxTarget,
        runbook_id: str,
        phase: str = "pre_state"
    ) -> Optional[LinuxExecutionResult]:
        """Capture system state for evidence."""
        state_script = '''
        echo "{"
        echo "  \\"timestamp\\": \\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\\","
        echo "  \\"hostname\\": \\"$(hostname)\\","
        echo "  \\"distro\\": \\"$(. /etc/os-release && echo $ID)\\","
        echo "  \\"kernel\\": \\"$(uname -r)\\","
        echo "  \\"uptime_seconds\\": $(cat /proc/uptime | cut -d' ' -f1 | cut -d'.' -f1),"
        echo "  \\"load_average\\": \\"$(cat /proc/loadavg | cut -d' ' -f1-3)\\","
        echo "  \\"memory_total_mb\\": $(free -m | awk '/^Mem:/{print $2}),"
        echo "  \\"memory_used_mb\\": $(free -m | awk '/^Mem:/{print $3}),"
        echo "  \\"disk_usage_percent\\": $(df / | awk 'NR==2{print $5}' | tr -d '%')"
        echo "}"
        '''

        try:
            result = await self.execute_script(target, state_script, timeout=30, retries=1)
            if result.success:
                return LinuxExecutionResult(
                    success=True,
                    runbook_id=runbook_id,
                    target=target.hostname,
                    phase=phase,
                    output=result.output,
                    duration_seconds=result.duration_seconds,
                    distro=self._distro_cache.get(target.hostname, "")
                )
        except Exception as e:
            logger.warning(f"Failed to capture {phase}: {e}")

        return None

    async def check_target_health(self, target: LinuxTarget) -> Dict:
        """
        Quick health check on target.

        Returns:
            Dict with connection status and basic info
        """
        script = '''
        echo "{"
        echo "  \\"hostname\\": \\"$(hostname)\\","
        echo "  \\"distro\\": \\"$(. /etc/os-release 2>/dev/null && echo $ID || echo unknown)\\","
        echo "  \\"kernel\\": \\"$(uname -r)\\","
        echo "  \\"uptime_hours\\": $(awk '{print int($1/3600)}' /proc/uptime),"
        echo "  \\"healthy\\": true"
        echo "}"
        '''

        result = await self.execute_script(target, script, timeout=15)

        if result.success and result.output.get("parsed"):
            return result.output["parsed"]
        else:
            return {
                "hostname": target.hostname,
                "healthy": False,
                "error": result.error or "Connection failed"
            }

    async def run_all_checks(self, target: LinuxTarget) -> Dict[str, LinuxExecutionResult]:
        """
        Run all detection checks on target.

        Returns:
            Dict mapping runbook_id to detection result
        """
        from .runbooks import RUNBOOKS

        results = {}

        for runbook_id, runbook in RUNBOOKS.items():
            logger.info(f"Running detection: {runbook_id} on {target.hostname}")

            exec_results = await self.run_runbook(
                target,
                runbook_id,
                phases=["detect"]
            )

            if exec_results:
                results[runbook_id] = exec_results[0]

        return results

    async def close_all(self):
        """Close all cached connections."""
        for hostname, conn in list(self._connection_cache.items()):
            try:
                conn.close()
            except Exception:
                pass
        self._connection_cache.clear()
        self._connection_timestamps.clear()


# Convenience function for single-target execution
async def execute_on_linux(
    hostname: str,
    username: str,
    password: Optional[str] = None,
    private_key: Optional[str] = None,
    runbook_id: str = "",
    phases: Optional[List[str]] = None
) -> List[LinuxExecutionResult]:
    """
    Execute runbook on a single Linux target.

    Args:
        hostname: Linux server hostname or IP
        username: SSH username
        password: SSH password (or use private_key)
        private_key: PEM-encoded private key
        runbook_id: Runbook ID to execute
        phases: Which phases to run

    Returns:
        List of LinuxExecutionResult for each phase
    """
    target = LinuxTarget(
        hostname=hostname,
        username=username,
        password=password,
        private_key=private_key
    )

    executor = LinuxExecutor([target])
    try:
        return await executor.run_runbook(target, runbook_id, phases)
    finally:
        await executor.close_all()
