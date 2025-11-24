"""
Windows Runbook Executor via WinRM.

Executes PowerShell runbooks on Windows Server targets using WinRM/PSRP.
Supports both HTTP and HTTPS connections with various authentication methods.
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from .runbooks import WindowsRunbook, get_runbook

logger = logging.getLogger(__name__)


@dataclass
class WindowsTarget:
    """Windows server target configuration."""
    hostname: str
    port: int = 5985  # WinRM HTTP (5986 for HTTPS)
    username: str = ""
    password: str = ""  # Or use Kerberos/certificate auth
    use_ssl: bool = False
    verify_ssl: bool = True
    transport: str = "ntlm"  # ntlm, kerberos, certificate


@dataclass
class ExecutionResult:
    """Result of runbook execution."""
    success: bool
    runbook_id: str
    target: str
    phase: str  # detect, remediate, verify
    output: Dict[str, Any]
    duration_seconds: float
    error: Optional[str] = None
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat()


class WindowsExecutor:
    """
    Execute Windows PowerShell runbooks via WinRM.

    Uses pywinrm library for WinRM/PSRP communication.
    Supports NTLM, Kerberos, and certificate authentication.
    """

    def __init__(self, targets: Optional[List[WindowsTarget]] = None):
        """
        Initialize executor.

        Args:
            targets: List of Windows targets to manage
        """
        self.targets: Dict[str, WindowsTarget] = {}
        if targets:
            for target in targets:
                self.targets[target.hostname] = target

        self._session_cache: Dict[str, Any] = {}

    def add_target(self, target: WindowsTarget):
        """Add a Windows target."""
        self.targets[target.hostname] = target

    def remove_target(self, hostname: str):
        """Remove a Windows target."""
        self.targets.pop(hostname, None)
        self._session_cache.pop(hostname, None)

    def _get_session(self, target: WindowsTarget):
        """
        Get or create WinRM session for target.

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

        if cache_key not in self._session_cache:
            protocol = "https" if target.use_ssl else "http"
            endpoint = f"{protocol}://{target.hostname}:{target.port}/wsman"

            session = winrm.Session(
                endpoint,
                auth=(target.username, target.password),
                transport=target.transport,
                server_cert_validation='validate' if target.verify_ssl else 'ignore'
            )
            self._session_cache[cache_key] = session

        return self._session_cache[cache_key]

    async def execute_script(
        self,
        target: WindowsTarget,
        script: str,
        timeout: int = 300
    ) -> ExecutionResult:
        """
        Execute PowerShell script on target.

        Args:
            target: Windows target
            script: PowerShell script to execute
            timeout: Execution timeout in seconds

        Returns:
            ExecutionResult with script output
        """
        start_time = datetime.utcnow()

        try:
            # Run in thread pool since pywinrm is synchronous
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._execute_sync,
                    target,
                    script
                ),
                timeout=timeout
            )

            duration = (datetime.utcnow() - start_time).total_seconds()

            return ExecutionResult(
                success=result.get("success", False),
                runbook_id="",
                target=target.hostname,
                phase="execute",
                output=result,
                duration_seconds=duration
            )

        except asyncio.TimeoutError:
            duration = (datetime.utcnow() - start_time).total_seconds()
            return ExecutionResult(
                success=False,
                runbook_id="",
                target=target.hostname,
                phase="execute",
                output={},
                duration_seconds=duration,
                error=f"Execution timed out after {timeout}s"
            )

        except Exception as e:
            duration = (datetime.utcnow() - start_time).total_seconds()
            logger.exception(f"Script execution failed on {target.hostname}")
            return ExecutionResult(
                success=False,
                runbook_id="",
                target=target.hostname,
                phase="execute",
                output={},
                duration_seconds=duration,
                error=str(e)
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
        phases: List[str] = None
    ) -> List[ExecutionResult]:
        """
        Execute a runbook on target.

        Args:
            target: Windows target
            runbook_id: Runbook ID to execute
            phases: Which phases to run (detect, remediate, verify)
                   Default: all phases

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

        for phase in phases:
            script = self._get_phase_script(runbook, phase)
            if not script:
                continue

            logger.info(f"Executing {runbook_id} phase={phase} on {target.hostname}")

            start_time = datetime.utcnow()

            try:
                exec_result = await self.execute_script(
                    target,
                    script,
                    timeout=runbook.timeout_seconds
                )

                result = ExecutionResult(
                    success=exec_result.success,
                    runbook_id=runbook_id,
                    target=target.hostname,
                    phase=phase,
                    output=exec_result.output,
                    duration_seconds=exec_result.duration_seconds,
                    error=exec_result.error
                )

            except Exception as e:
                duration = (datetime.utcnow() - start_time).total_seconds()
                result = ExecutionResult(
                    success=False,
                    runbook_id=runbook_id,
                    target=target.hostname,
                    phase=phase,
                    output={},
                    duration_seconds=duration,
                    error=str(e)
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

        return results

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
