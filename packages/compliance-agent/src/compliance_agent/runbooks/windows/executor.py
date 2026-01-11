"""
Windows Runbook Executor via WinRM.

Executes PowerShell runbooks on Windows Server targets using WinRM/PSRP.
Supports both HTTP and HTTPS connections with various authentication methods.

Features:
- Automatic retry with exponential backoff
- Connection pooling and session caching
- Evidence collection for compliance
- Timeout handling with graceful cleanup
- Pre/post state capture for audit trails

Version: 2.0
"""

import asyncio
import logging
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

from .runbooks import WindowsRunbook, get_runbook, PS_HELPERS

logger = logging.getLogger(__name__)


@dataclass
class WindowsTarget:
    """Windows server target configuration.

    Security: Defaults to HTTPS (port 5986) with certificate validation.
    HTTP (port 5985) should only be used in isolated lab environments.
    """
    hostname: str
    port: int = 5986  # WinRM HTTPS (secure default)
    username: str = ""
    password: str = ""  # Prefer Kerberos/certificate auth over passwords
    use_ssl: bool = True  # HTTPS by default - credentials encrypted in transit
    verify_ssl: bool = True  # Always validate server certificates
    transport: str = "ntlm"  # ntlm, kerberos, certificate


@dataclass
class ExecutionResult:
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

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()
        if not self.output_hash and self.output:
            import json
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
            "retry_count": self.retry_count
        }


class WindowsExecutor:
    """
    Execute Windows PowerShell runbooks via WinRM.

    Uses pywinrm library for WinRM/PSRP communication.
    Supports NTLM, Kerberos, and certificate authentication.

    Features:
    - Automatic retry with configurable backoff
    - Session caching for performance
    - Evidence collection for audit trails
    - Timeout handling
    """

    def __init__(
        self,
        targets: Optional[List[WindowsTarget]] = None,
        default_retries: int = 2,
        retry_backoff: float = 1.5
    ):
        """
        Initialize executor.

        Args:
            targets: List of Windows targets to manage
            default_retries: Default number of retry attempts
            retry_backoff: Multiplier for retry delay (exponential backoff)
        """
        self.targets: Dict[str, WindowsTarget] = {}
        if targets:
            for target in targets:
                self.targets[target.hostname] = target

        self._session_cache: Dict[str, Any] = {}
        self._session_timestamps: Dict[str, datetime] = {}
        self._default_retries = default_retries
        self._retry_backoff = retry_backoff
        self._session_max_age_seconds = 300  # Refresh sessions after 5 minutes

    def add_target(self, target: WindowsTarget):
        """Add a Windows target."""
        self.targets[target.hostname] = target

    def remove_target(self, hostname: str):
        """Remove a Windows target."""
        self.targets.pop(hostname, None)
        self._session_cache.pop(hostname, None)

    def _get_session(self, target: WindowsTarget, force_new: bool = False):
        """
        Get or create WinRM session for target.

        Args:
            target: Windows target configuration
            force_new: Force creation of new session

        Returns:
            winrm.Session object
        """
        try:
            import winrm
        except ImportError:
            raise ImportError(
                "pywinrm is required for Windows execution. "
                "Install with: pip install pywinrm"
            )

        cache_key = target.hostname

        # Check if session is stale
        if cache_key in self._session_timestamps:
            age = (datetime.now(timezone.utc) - self._session_timestamps[cache_key]).total_seconds()
            if age > self._session_max_age_seconds:
                logger.debug(f"Session for {target.hostname} is stale ({age:.0f}s), refreshing")
                force_new = True

        if force_new or cache_key not in self._session_cache:
            protocol = "https" if target.use_ssl else "http"
            endpoint = f"{protocol}://{target.hostname}:{target.port}/wsman"

            # Security warnings for insecure configurations
            if not target.use_ssl:
                logger.warning(
                    f"SECURITY: WinRM connection to {target.hostname} using HTTP - "
                    "credentials transmitted in PLAINTEXT. Use HTTPS (port 5986) in production."
                )
            if target.use_ssl and not target.verify_ssl:
                logger.warning(
                    f"SECURITY: SSL certificate validation DISABLED for {target.hostname} - "
                    "vulnerable to man-in-the-middle attacks."
                )

            logger.debug(f"Creating new WinRM session to {endpoint}")

            session = winrm.Session(
                endpoint,
                auth=(target.username, target.password),
                transport=target.transport,
                server_cert_validation='validate' if target.verify_ssl else 'ignore',
                read_timeout_sec=60,
                operation_timeout_sec=55
            )
            self._session_cache[cache_key] = session
            self._session_timestamps[cache_key] = datetime.now(timezone.utc)

        return self._session_cache[cache_key]

    def invalidate_session(self, hostname: str):
        """Invalidate cached session for hostname."""
        self._session_cache.pop(hostname, None)
        self._session_timestamps.pop(hostname, None)

    async def execute_script(
        self,
        target: WindowsTarget,
        script: str,
        timeout: int = 300,
        retries: int = 0,
        retry_delay: float = 30.0
    ) -> ExecutionResult:
        """
        Execute PowerShell script on target with retry support.

        Args:
            target: Windows target
            script: PowerShell script to execute
            timeout: Execution timeout in seconds
            retries: Number of retry attempts on failure
            retry_delay: Initial delay between retries (uses exponential backoff)

        Returns:
            ExecutionResult with script output
        """
        if retries == 0:
            retries = self._default_retries

        last_error = None
        retry_count = 0

        for attempt in range(retries + 1):
            start_time = datetime.now(timezone.utc)

            try:
                # Inject helper functions
                full_script = f"{PS_HELPERS}\n\n{script}"

                # Run in thread pool since pywinrm is synchronous
                loop = asyncio.get_event_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        self._execute_sync,
                        target,
                        full_script
                    ),
                    timeout=timeout
                )

                duration = (datetime.now(timezone.utc) - start_time).total_seconds()

                return ExecutionResult(
                    success=result.get("success", False),
                    runbook_id="",
                    target=target.hostname,
                    phase="execute",
                    output=result,
                    duration_seconds=duration,
                    retry_count=retry_count
                )

            except asyncio.TimeoutError:
                last_error = f"Execution timed out after {timeout}s"
                logger.warning(f"Timeout on {target.hostname}, attempt {attempt + 1}/{retries + 1}")

            except Exception as e:
                last_error = str(e)
                logger.warning(f"Error on {target.hostname}: {e}, attempt {attempt + 1}/{retries + 1}")
                # Invalidate session on connection errors
                if "connection" in str(e).lower() or "winrm" in str(e).lower():
                    self.invalidate_session(target.hostname)

            # If we have more attempts, wait before retrying
            if attempt < retries:
                delay = retry_delay * (self._retry_backoff ** attempt)
                logger.info(f"Retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
                retry_count += 1

        # All retries exhausted
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        return ExecutionResult(
            success=False,
            runbook_id="",
            target=target.hostname,
            phase="execute",
            output={},
            duration_seconds=duration,
            error=last_error,
            retry_count=retry_count
        )

    def _execute_sync(self, target: WindowsTarget, script: str) -> Dict:
        """Synchronous script execution (runs in thread pool)."""
        import json

        session = self._get_session(target)

        # Execute PowerShell script
        result = session.run_ps(script)

        output = {
            "status_code": result.status_code,
            "std_out": result.std_out.decode('utf-8', errors='replace') if result.std_out else "",
            "std_err": result.std_err.decode('utf-8', errors='replace') if result.std_err else "",
            "success": result.status_code == 0
        }

        # Try to parse JSON output
        if output["std_out"]:
            try:
                output["parsed"] = json.loads(output["std_out"])
            except json.JSONDecodeError:
                output["parsed"] = None

        return output

    async def run_runbook(
        self,
        target: WindowsTarget,
        runbook_id: str,
        phases: List[str] = None,
        collect_evidence: bool = True
    ) -> List[ExecutionResult]:
        """
        Execute a runbook on target with evidence collection.

        Args:
            target: Windows target
            runbook_id: Runbook ID to execute
            phases: Which phases to run (detect, remediate, verify)
                   Default: all phases
            collect_evidence: Whether to collect pre/post state

        Returns:
            List of ExecutionResult for each phase
        """
        runbook = get_runbook(runbook_id)
        if not runbook:
            return [ExecutionResult(
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

        results = []

        # Get retry settings from runbook constraints
        max_retries = runbook.constraints.max_retries
        retry_delay = runbook.constraints.retry_delay_seconds

        # Collect pre-state if enabled
        if collect_evidence and runbook.capture_pre_state and "remediate" in phases:
            pre_state = await self._capture_system_state(target, runbook_id)
            if pre_state:
                results.append(pre_state)

        for phase in phases:
            script = self._get_phase_script(runbook, phase)
            if not script:
                continue

            logger.info(f"Executing {runbook_id} phase={phase} on {target.hostname}")

            start_time = datetime.now(timezone.utc)

            try:
                exec_result = await self.execute_script(
                    target,
                    script,
                    timeout=runbook.timeout_seconds,
                    retries=max_retries,
                    retry_delay=float(retry_delay)
                )

                result = ExecutionResult(
                    success=exec_result.success,
                    runbook_id=runbook_id,
                    target=target.hostname,
                    phase=phase,
                    output=exec_result.output,
                    duration_seconds=exec_result.duration_seconds,
                    error=exec_result.error,
                    retry_count=exec_result.retry_count,
                    hipaa_controls=runbook.hipaa_controls
                )

            except Exception as e:
                duration = (datetime.now(timezone.utc) - start_time).total_seconds()
                result = ExecutionResult(
                    success=False,
                    runbook_id=runbook_id,
                    target=target.hostname,
                    phase=phase,
                    output={},
                    duration_seconds=duration,
                    error=str(e),
                    hipaa_controls=runbook.hipaa_controls
                )

            results.append(result)

            # If detection shows no drift, skip remediation
            if phase == "detect" and result.success:
                parsed = result.output.get("parsed", {})
                if parsed and not parsed.get("Drifted", True):
                    logger.info(f"No drift detected on {target.hostname}, skipping remediation")
                    break

            # If any phase fails, stop execution
            if not result.success and phase != "detect":
                logger.warning(f"Phase {phase} failed on {target.hostname}, stopping runbook")
                break

        # Collect post-state if enabled and remediation occurred
        if collect_evidence and runbook.capture_post_state and "remediate" in phases:
            remediate_results = [r for r in results if r.phase == "remediate"]
            if remediate_results and remediate_results[0].success:
                post_state = await self._capture_system_state(target, runbook_id, "post_state")
                if post_state:
                    results.append(post_state)

        return results

    async def _capture_system_state(
        self,
        target: WindowsTarget,
        runbook_id: str,
        phase: str = "pre_state"
    ) -> Optional[ExecutionResult]:
        """Capture system state for evidence."""
        state_script = r'''
        @{
            Timestamp = Get-Timestamp
            Hostname = $env:COMPUTERNAME
            OSVersion = (Get-WmiObject Win32_OperatingSystem).Caption
            LastBoot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString("o")
            Services = @(Get-Service | Where-Object { $_.Status -ne 'Stopped' } | Select-Object Name, Status)
            DiskSpace = @(Get-WmiObject Win32_LogicalDisk | Select-Object DeviceID, @{N='FreeGB';E={[math]::Round($_.FreeSpace/1GB,2)}})
        } | ConvertTo-Json -Depth 3
        '''

        try:
            result = await self.execute_script(target, state_script, timeout=30, retries=1)
            if result.success:
                return ExecutionResult(
                    success=True,
                    runbook_id=runbook_id,
                    target=target.hostname,
                    phase=phase,
                    output=result.output,
                    duration_seconds=result.duration_seconds
                )
        except Exception as e:
            logger.warning(f"Failed to capture {phase}: {e}")

        return None

    def _get_phase_script(self, runbook: WindowsRunbook, phase: str) -> Optional[str]:
        """Get script for runbook phase."""
        scripts = {
            "detect": runbook.detect_script,
            "remediate": runbook.remediate_script,
            "verify": runbook.verify_script
        }
        return scripts.get(phase)

    async def check_target_health(self, target: WindowsTarget) -> Dict:
        """
        Quick health check on target.

        Returns:
            Dict with connection status and basic info
        """
        script = r'''
        @{
            Hostname = $env:COMPUTERNAME
            Domain = $env:USERDOMAIN
            OSVersion = (Get-WmiObject Win32_OperatingSystem).Caption
            LastBoot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString("o")
            Uptime = ((Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime).TotalHours
            Healthy = $true
        } | ConvertTo-Json
        '''

        result = await self.execute_script(target, script, timeout=30)

        if result.success and result.output.get("parsed"):
            return result.output["parsed"]
        else:
            return {
                "Hostname": target.hostname,
                "Healthy": False,
                "Error": result.error or "Connection failed"
            }

    async def run_all_checks(
        self,
        target: WindowsTarget
    ) -> Dict[str, ExecutionResult]:
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


# Convenience function for single-target execution
async def execute_on_windows(
    hostname: str,
    username: str,
    password: str,
    runbook_id: str,
    use_ssl: bool = False,
    phases: List[str] = None
) -> List[ExecutionResult]:
    """
    Execute runbook on a single Windows target.

    Args:
        hostname: Windows server hostname or IP
        username: Windows username (DOMAIN\\user or user@domain)
        password: Windows password
        runbook_id: Runbook ID to execute
        use_ssl: Use HTTPS (port 5986) instead of HTTP (port 5985)
        phases: Which phases to run

    Returns:
        List of ExecutionResult for each phase
    """
    target = WindowsTarget(
        hostname=hostname,
        port=5986 if use_ssl else 5985,
        username=username,
        password=password,
        use_ssl=use_ssl
    )

    executor = WindowsExecutor([target])
    return await executor.run_runbook(target, runbook_id, phases)
