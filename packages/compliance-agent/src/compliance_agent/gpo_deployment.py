"""
GPO-based Go agent deployment engine.

Creates a Group Policy Object that automatically deploys the OsirisCare
compliance agent to all domain-joined machines via computer startup script.

Self-healing: startup script runs at every boot. If agent is removed or
machine reimaged, GPO re-installs on next boot from SYSVOL.

HIPAA: 164.308(a)(1) - Security Management Process
"""

import asyncio
import base64
import hashlib
import logging
from pathlib import Path
from typing import Optional, Dict
from dataclasses import dataclass

logger = logging.getLogger(__name__)

GPO_NAME = "OsirisCare Compliance Agent"
SYSVOL_SUBPATH = "OsirisCare"
INSTALL_DIR = r"C:\OsirisCare"
SERVICE_NAME = "OsirisCareAgent"


@dataclass
class GPODeploymentResult:
    """Result of a GPO deployment attempt."""
    success: bool
    gpo_name: str
    gpo_id: Optional[str] = None
    sysvol_path: Optional[str] = None
    error: Optional[str] = None


class GPODeploymentEngine:
    """Creates and manages GPO for automatic agent deployment."""

    # Chunk size for binary transfer via WinRM (500KB before base64)
    CHUNK_SIZE = 500_000

    def __init__(
        self,
        domain: str,
        dc_host: str,
        executor,
        credentials: dict,
    ):
        self.domain = domain
        self.dc_host = dc_host
        self.executor = executor
        self.credentials = credentials

    async def deploy_via_gpo(
        self, agent_binary_path: str
    ) -> GPODeploymentResult:
        """
        Full GPO deployment pipeline:
        1. Upload agent binary to SYSVOL
        2. Create startup script in SYSVOL
        3. Create GPO with startup script
        4. Link GPO to domain root

        On failure at any step, rolls back artifacts created in prior steps.
        """
        artifacts: Dict[str, Optional[str]] = {
            "sysvol_dir": None,
            "script_path": None,
            "gpo_id": None,
        }
        try:
            # Step 1: Upload binary to SYSVOL
            sysvol_dir = await self._upload_to_sysvol(agent_binary_path)
            if not sysvol_dir:
                return GPODeploymentResult(
                    success=False,
                    gpo_name=GPO_NAME,
                    error="Failed to upload binary to SYSVOL",
                )
            artifacts["sysvol_dir"] = sysvol_dir

            # Step 2: Create startup script
            script_path = await self._create_startup_script(sysvol_dir)
            if not script_path:
                await self._rollback(artifacts)
                return GPODeploymentResult(
                    success=False,
                    gpo_name=GPO_NAME,
                    error="Failed to create startup script",
                )
            artifacts["script_path"] = script_path

            # Step 3: Create GPO and link to domain
            gpo_id = await self._create_and_link_gpo(script_path)
            if not gpo_id:
                await self._rollback(artifacts)
                return GPODeploymentResult(
                    success=False,
                    gpo_name=GPO_NAME,
                    error="Failed to create GPO",
                )
            artifacts["gpo_id"] = gpo_id

            return GPODeploymentResult(
                success=True,
                gpo_name=GPO_NAME,
                gpo_id=gpo_id,
                sysvol_path=sysvol_dir,
            )
        except Exception as e:
            logger.error("GPO deployment failed: %s", e)
            await self._rollback(artifacts)
            return GPODeploymentResult(
                success=False, gpo_name=GPO_NAME, error=str(e)
            )

    async def _rollback(self, artifacts: Dict[str, Optional[str]]) -> None:
        """Clean up artifacts from a failed deployment."""
        # Remove GPO if it was created
        if artifacts.get("gpo_id"):
            try:
                ps_remove_gpo = f'''
                Import-Module GroupPolicy
                Remove-GPO -Name "{GPO_NAME}" -ErrorAction SilentlyContinue
                Write-Output "GPO_REMOVED"
                '''
                await self._run_on_dc(ps_remove_gpo, timeout=30)
                logger.info("Rollback: removed GPO '%s'", GPO_NAME)
            except Exception as e:
                logger.warning("Rollback: failed to remove GPO: %s", e)

        # Remove SYSVOL directory if it was created
        if artifacts.get("sysvol_dir"):
            try:
                ps_remove_dir = f'''
                Remove-Item -Recurse -Force -Path "{artifacts["sysvol_dir"]}" -ErrorAction SilentlyContinue
                Write-Output "DIR_REMOVED"
                '''
                await self._run_on_dc(ps_remove_dir, timeout=30)
                logger.info("Rollback: removed SYSVOL dir '%s'", artifacts["sysvol_dir"])
            except Exception as e:
                logger.warning("Rollback: failed to remove SYSVOL dir: %s", e)

    async def _upload_to_sysvol(
        self, local_binary_path: str
    ) -> Optional[str]:
        """Upload agent binary to SYSVOL share on DC."""
        sysvol_dir = (
            f"\\\\{self.domain}\\SYSVOL\\{self.domain}\\{SYSVOL_SUBPATH}"
        )
        remote_exe = f"{sysvol_dir}\\osiris-agent.exe"

        # Create directory
        ps_mkdir = f'''
        New-Item -ItemType Directory -Force -Path "{sysvol_dir}" | Out-Null
        Write-Output "DIR_OK"
        '''
        result = await self._run_on_dc(ps_mkdir)
        if not result or "DIR_OK" not in result:
            return None

        # Transfer binary in chunks
        binary_path = Path(local_binary_path)
        if not binary_path.exists():
            logger.error("Agent binary not found: %s", local_binary_path)
            return None

        binary_data = binary_path.read_bytes()
        local_hash = hashlib.sha256(binary_data).hexdigest().upper()

        # Check if existing binary matches
        ps_check = f'''
        if (Test-Path "{remote_exe}") {{
            $hash = (Get-FileHash -Path "{remote_exe}" -Algorithm SHA256).Hash
            Write-Output "HASH:$hash"
        }} else {{
            Write-Output "HASH:NONE"
        }}
        '''
        check_result = await self._run_on_dc(ps_check)
        if check_result and f"HASH:{local_hash}" in check_result:
            logger.info("Agent binary in SYSVOL is current (hash matches)")
            return sysvol_dir

        logger.info(
            "Uploading agent binary to SYSVOL (%d bytes)...",
            len(binary_data),
        )

        # Split into chunks for WinRM transfer
        chunks = []
        for i in range(0, len(binary_data), self.CHUNK_SIZE):
            chunk = base64.b64encode(
                binary_data[i : i + self.CHUNK_SIZE]
            ).decode()
            chunks.append(chunk)

        # Write first chunk (create file)
        ps_first = f'''
        $bytes = [Convert]::FromBase64String("{chunks[0]}")
        [IO.File]::WriteAllBytes("{remote_exe}", $bytes)
        Write-Output "CHUNK_0_OK"
        '''
        result = await self._run_on_dc(ps_first, timeout=120)
        if not result or "CHUNK_0_OK" not in result:
            logger.error("Failed to write first chunk")
            return None

        # Append remaining chunks
        for i, chunk in enumerate(chunks[1:], 1):
            ps_append = f'''
            $bytes = [Convert]::FromBase64String("{chunk}")
            $stream = [IO.File]::OpenWrite("{remote_exe}")
            $stream.Seek(0, [IO.SeekOrigin]::End) | Out-Null
            $stream.Write($bytes, 0, $bytes.Length)
            $stream.Close()
            Write-Output "CHUNK_{i}_OK"
            '''
            result = await self._run_on_dc(ps_append, timeout=120)
            if not result or f"CHUNK_{i}_OK" not in result:
                logger.error("Failed to write chunk %d", i)
                return None

        # Verify hash
        ps_verify = f'''
        $hash = (Get-FileHash -Path "{remote_exe}" -Algorithm SHA256).Hash
        Write-Output "VERIFY:$hash"
        '''
        verify_result = await self._run_on_dc(ps_verify)
        if not verify_result or f"VERIFY:{local_hash}" not in verify_result:
            logger.error("Binary hash mismatch after transfer")
            return None

        logger.info("Agent binary uploaded to SYSVOL successfully")
        return sysvol_dir

    async def _create_startup_script(
        self, sysvol_dir: str
    ) -> Optional[str]:
        """Create idempotent computer startup script in SYSVOL."""
        # Batch file — not PowerShell — to avoid execution policy issues
        startup_content = f"""@echo off
REM OsirisCare Compliance Agent - GPO Startup Script
REM Runs at every boot. Idempotent: only installs/updates if needed.

set INSTALL_DIR={INSTALL_DIR}
set SERVICE_NAME={SERVICE_NAME}
set SYSVOL_SRC={sysvol_dir}\\osiris-agent.exe

REM Check if service exists and is running
sc.exe query %SERVICE_NAME% >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    REM Service exists - check if binary needs updating
    fc /b "%INSTALL_DIR%\\osiris-agent.exe" "%SYSVOL_SRC%" >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        REM Binary is current
        exit /b 0
    )
    REM Binary needs updating - stop service first
    net stop %SERVICE_NAME% /y >nul 2>&1
    timeout /t 5 /nobreak >nul
)

REM Create install directory
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

REM Copy binary from SYSVOL
copy /y "%SYSVOL_SRC%" "%INSTALL_DIR%\\osiris-agent.exe" >nul

REM Install or update service
sc.exe query %SERVICE_NAME% >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    sc.exe create %SERVICE_NAME% binPath= "%INSTALL_DIR%\\osiris-agent.exe" start= auto DisplayName= "OsirisCare Compliance Agent"
    sc.exe description %SERVICE_NAME% "HIPAA compliance monitoring agent"
    sc.exe failure %SERVICE_NAME% reset= 86400 actions= restart/60000/restart/120000/restart/300000
)

REM Start service
net start %SERVICE_NAME% >nul 2>&1
"""
        script_path = f"{sysvol_dir}\\install-agent.cmd"

        # Write script to SYSVOL via WinRM
        escaped = startup_content.replace("'", "''")
        ps_write = f"""
        Set-Content -Path '{script_path}' -Value @'
{startup_content}
'@ -Encoding ASCII
        Write-Output "SCRIPT_OK"
        """

        result = await self._run_on_dc(ps_write)
        if result and "SCRIPT_OK" in result:
            logger.info("Startup script created: %s", script_path)
            return script_path

        logger.error("Failed to create startup script")
        return None

    async def _create_and_link_gpo(
        self, script_path: str
    ) -> Optional[str]:
        """Create GPO with startup script and link to domain."""
        domain_dn = self._domain_dn()

        ps_gpo = f'''
        Import-Module GroupPolicy

        $gpoName = "{GPO_NAME}"

        # Check if GPO already exists
        $existing = Get-GPO -Name $gpoName -ErrorAction SilentlyContinue
        if ($existing) {{
            Write-Output "GPO_EXISTS:$($existing.Id)"
        }} else {{
            # Create new GPO
            $gpo = New-GPO -Name $gpoName -Comment "Deploys OsirisCare HIPAA compliance agent to all domain machines"
            $gpoId = $gpo.Id.ToString()

            # Configure startup script via scripts.ini
            $gpoPath = "\\\\{self.domain}\\SysVol\\{self.domain}\\Policies\\{{{{$gpoId}}}}\\Machine\\Scripts"
            $startupDir = "$gpoPath\\Startup"

            New-Item -ItemType Directory -Force -Path $startupDir | Out-Null

            # Copy startup script into GPO scripts folder
            Copy-Item -Path "{script_path}" -Destination "$startupDir\\install-agent.cmd" -Force

            # Write scripts.ini (standard GPO startup script configuration)
            $scriptsIni = @"
[Startup]
0CmdLine=install-agent.cmd
0Parameters=
"@
            Set-Content -Path "$gpoPath\\scripts.ini" -Value $scriptsIni -Encoding ASCII

            # Write psscripts.ini (required for GPO processing)
            Set-Content -Path "$gpoPath\\psscripts.ini" -Value "" -Encoding ASCII

            # Link GPO to domain root
            New-GPLink -Name $gpoName -Target "{domain_dn}" -LinkEnabled Yes

            Write-Output "GPO_CREATED:$gpoId"
        }}
        '''

        result = await self._run_on_dc(ps_gpo, timeout=60)
        if not result:
            return None

        if "GPO_CREATED:" in result:
            gpo_id = result.split("GPO_CREATED:")[1].strip().split("\n")[0]
            logger.info("Created GPO '%s' (ID: %s)", GPO_NAME, gpo_id)
            return gpo_id

        if "GPO_EXISTS:" in result:
            gpo_id = result.split("GPO_EXISTS:")[1].strip().split("\n")[0]
            logger.info("GPO '%s' already exists (ID: %s)", GPO_NAME, gpo_id)
            return gpo_id

        return None

    async def update_agent_binary(
        self, agent_binary_path: str
    ) -> bool:
        """Update the agent binary in SYSVOL. Machines pick up the update on next boot."""
        sysvol_dir = await self._upload_to_sysvol(agent_binary_path)
        return sysvol_dir is not None

    async def verify_gpo(self) -> Dict:
        """Verify GPO is properly configured and linked."""
        ps_verify = f'''
        Import-Module GroupPolicy
        $gpo = Get-GPO -Name "{GPO_NAME}" -ErrorAction SilentlyContinue
        if ($gpo) {{
            @{{
                "exists" = $true
                "id" = $gpo.Id.ToString()
                "status" = $gpo.GpoStatus.ToString()
                "created" = $gpo.CreationTime.ToString("o")
                "modified" = $gpo.ModificationTime.ToString("o")
            }} | ConvertTo-Json
        }} else {{
            @{{"exists" = $false}} | ConvertTo-Json
        }}
        '''
        result = await self._run_on_dc(ps_verify)
        if result:
            import json
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                pass
        return {"exists": False, "error": "Failed to verify"}

    def _domain_dn(self) -> str:
        """Convert domain.local to DC=domain,DC=local."""
        parts = self.domain.split(".")
        return ",".join(f"DC={p}" for p in parts)

    async def _run_on_dc(
        self, script: str, timeout: int = 30
    ) -> Optional[str]:
        """Run PowerShell script on DC via WinRM."""
        loop = asyncio.get_event_loop()

        def _exec():
            result = self.executor.run_script(
                target=self.dc_host,
                script=script,
                credentials=self.credentials,
                timeout_seconds=timeout,
            )
            if result.success:
                return result.output.get("stdout", "")
            logger.error("DC script failed: %s", result.error)
            return None

        return await loop.run_in_executor(None, _exec)
