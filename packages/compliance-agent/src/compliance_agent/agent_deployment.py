"""
Go agent deployment module.

Automatically deploys Go agents to discovered workstations via WinRM.
Handles binary transfer, config creation, and Windows service installation.

HIPAA Relevance:
- §164.308(a)(1) - Risk Analysis (workstation inventory and monitoring)
- §164.312(a)(2)(iv) - Encryption (BitLocker monitoring via agent)
"""

import asyncio
import base64
import logging
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class DeploymentResult:
    """Result of a single agent deployment attempt."""
    hostname: str
    success: bool
    method: str
    error: Optional[str] = None
    agent_version: Optional[str] = None
    deployed_at: Optional[datetime] = None


class GoAgentDeployer:
    """
    Deploys Go agents to Windows workstations.
    
    Deployment methods:
    1. WinRM push - Direct copy and service install (primary)
    2. GPO - Group Policy deployment (future)
    """
    
    # Agent binary location on appliance
    # Options:
    # 1. Embedded in ISO at /var/lib/msp/agent/osiris-agent.exe
    # 2. Downloaded from Central Command on first deployment
    # 3. Available via HTTP server (fallback)
    AGENT_BINARY_PATH = Path("/var/lib/msp/agent/osiris-agent.exe")
    AGENT_VERSION = "0.1.0"
    
    # Alternative: Download from Central Command if not found locally
    AGENT_DOWNLOAD_URL = None  # Set to Central Command URL if needed
    
    # Installation paths on target workstation
    INSTALL_DIR = r"C:\OsirisCare"
    SERVICE_NAME = "OsirisCareAgent"
    
    def __init__(
        self,
        domain: str,
        username: str,
        password: str,
        appliance_addr: str,
        executor,  # WindowsExecutor instance
    ):
        """
        Initialize Go agent deployer.
        
        Args:
            domain: Domain name (e.g., "northvalley.local")
            username: Domain admin username
            password: Domain admin password
            appliance_addr: gRPC address for agent config (e.g., "192.168.88.246:50051")
            executor: WindowsExecutor for WinRM commands
        """
        self.domain = domain
        self.username = username
        self.password = password
        self.appliance_addr = appliance_addr
        self.executor = executor
    
    async def deploy_to_workstations(
        self, 
        workstations: List[Dict],
        max_concurrent: int = 5,
    ) -> List[DeploymentResult]:
        """
        Deploy Go agent to multiple workstations.
        
        Args:
            workstations: List of {"hostname": str, "ip_address": str, "os": str}
            max_concurrent: Max concurrent deployments
            
        Returns:
            List of DeploymentResult
        """
        logger.info(f"Starting Go agent deployment to {len(workstations)} workstations")
        
        # Check if agent binary exists, try to download if not
        if not self.AGENT_BINARY_PATH.exists():
            logger.warning(f"Agent binary not found at {self.AGENT_BINARY_PATH}, attempting download...")
            if not await self._download_agent_binary():
                logger.error(f"Failed to download agent binary, deployment aborted")
                return [
                    DeploymentResult(
                        hostname=ws.get('hostname', 'unknown'),
                        success=False,
                        method="winrm",
                        error=f"Agent binary not available on appliance",
                    )
                    for ws in workstations
                ]
        
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def deploy_with_limit(ws):
            async with semaphore:
                return await self._deploy_single(ws)
        
        results = await asyncio.gather(*[deploy_with_limit(ws) for ws in workstations])
        
        successful = sum(1 for r in results if r.success)
        logger.info(f"Deployment complete: {successful}/{len(workstations)} successful")
        
        return results
    
    async def _deploy_single(self, workstation: Dict) -> DeploymentResult:
        """Deploy Go agent to a single workstation."""
        hostname = workstation.get('hostname') or workstation.get('ip_address')
        
        try:
            # Check if agent already deployed
            status = await self.check_agent_status(hostname)
            if status.get('installed') and status.get('status') == 'Running':
                logger.info(f"Agent already running on {hostname}, skipping deployment")
                return DeploymentResult(
                    hostname=hostname,
                    success=True,
                    method="winrm",
                    agent_version=status.get('version', self.AGENT_VERSION),
                    deployed_at=datetime.now(timezone.utc),
                )
            
            # Deploy via WinRM
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, 
                self._deploy_via_winrm, 
                hostname
            )
            return result
        except Exception as e:
            logger.error(f"Deployment to {hostname} failed: {e}")
            return DeploymentResult(
                hostname=hostname,
                success=False,
                method="winrm",
                error=str(e),
            )
    
    def _deploy_via_winrm(self, hostname: str) -> DeploymentResult:
        """Deploy agent via WinRM (synchronous)."""
        try:
            # Format credentials
            if '\\' not in self.username and '@' not in self.username:
                auth_user = f"{self.domain}\\{self.username}"
            else:
                auth_user = self.username
            
            credentials = {
                "username": auth_user,
                "password": self.password,
            }
            
            # Step 1: Create installation directory
            logger.debug(f"Creating directory on {hostname}...")
            result = self.executor.run_script(
                target=hostname,
                script=f'New-Item -ItemType Directory -Force -Path "{self.INSTALL_DIR}"',
                credentials=credentials,
                timeout_seconds=30,
            )
            
            if not result.success:
                raise Exception(f"Failed to create directory: {result.error}")
            
            # Step 2: Read agent binary and encode as base64
            logger.debug(f"Reading agent binary from {self.AGENT_BINARY_PATH}...")
            with open(self.AGENT_BINARY_PATH, 'rb') as f:
                agent_bytes = f.read()
            agent_b64 = base64.b64encode(agent_bytes).decode()
            
            # Step 3: Write binary from base64 (PowerShell)
            logger.debug(f"Writing agent binary to {hostname}...")
            ps_write = f'''
            $bytes = [Convert]::FromBase64String(@"
{agent_b64}
"@)
            [IO.File]::WriteAllBytes("{self.INSTALL_DIR}\\osiris-agent.exe", $bytes)
            '''
            
            result = self.executor.run_script(
                target=hostname,
                script=ps_write,
                credentials=credentials,
                timeout_seconds=120,  # Large binary may take time
            )
            
            if not result.success:
                raise Exception(f"Failed to write agent binary: {result.error}")
            
            # Step 4: Create config file
            logger.debug(f"Creating config file on {hostname}...")
            config_content = f'''{{
    "appliance_addr": "{self.appliance_addr}",
    "check_interval": 300
}}'''
            
            ps_config = f'''
            Set-Content -Path "{self.INSTALL_DIR}\\config.json" -Value @'
{config_content}
'@ -Encoding UTF8
            '''
            
            result = self.executor.run_script(
                target=hostname,
                script=ps_config,
                credentials=credentials,
                timeout_seconds=30,
            )
            
            if not result.success:
                raise Exception(f"Failed to write config file: {result.error}")
            
            # Step 5: Install as Windows service
            logger.debug(f"Installing service on {hostname}...")
            ps_service = f'''
            $serviceName = "{self.SERVICE_NAME}"
            $exePath = "{self.INSTALL_DIR}\\osiris-agent.exe"
            $configPath = "{self.INSTALL_DIR}\\config.json"
            
            # Remove existing service if present
            $existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
            if ($existing) {{
                Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 2
                sc.exe delete $serviceName
                Start-Sleep -Seconds 2
            }}
            
            # Create new service
            New-Service -Name $serviceName `
                -BinaryPathName "$exePath --config `"$configPath`"" `
                -DisplayName "OsirisCare Compliance Agent" `
                -Description "Compliance monitoring agent for OsirisCare" `
                -StartupType Automatic `
                -ErrorAction Stop
            
            # Start service
            Start-Service -Name $serviceName -ErrorAction Stop
            
            # Verify running
            Start-Sleep -Seconds 3
            $svc = Get-Service -Name $serviceName
            if ($svc.Status -ne "Running") {{
                throw "Service failed to start. Status: $($svc.Status)"
            }}
            
            Write-Output "SUCCESS"
            '''
            
            result = self.executor.run_script(
                target=hostname,
                script=ps_service,
                credentials=credentials,
                timeout_seconds=60,
            )
            
            if not result.success or "SUCCESS" not in result.output.get("stdout", ""):
                raise Exception(f"Service installation failed: {result.error or result.output.get('stderr', 'Unknown error')}")
            
            logger.info(f"Successfully deployed agent to {hostname}")
            
            return DeploymentResult(
                hostname=hostname,
                success=True,
                method="winrm",
                agent_version=self.AGENT_VERSION,
                deployed_at=datetime.now(timezone.utc),
            )
            
        except Exception as e:
            logger.error(f"WinRM deployment to {hostname} failed: {e}")
            return DeploymentResult(
                hostname=hostname,
                success=False,
                method="winrm",
                error=str(e),
            )
    
    async def _download_agent_binary(self) -> bool:
        """
        Download agent binary from Central Command if not found locally.
        
        Returns:
            True if binary is now available, False otherwise
        """
        # For now, return False - binary should be embedded in ISO
        # Future: Implement download from Central Command
        logger.warning("Agent binary download not yet implemented - binary should be embedded in ISO")
        return False
    
    async def check_agent_status(self, hostname: str) -> Dict:
        """Check if agent is running on a workstation."""
        try:
            # Format credentials
            if '\\' not in self.username and '@' not in self.username:
                auth_user = f"{self.domain}\\{self.username}"
            else:
                auth_user = self.username
            
            credentials = {
                "username": auth_user,
                "password": self.password,
            }
            
            loop = asyncio.get_event_loop()
            
            def _check():
                result = self.executor.run_script(
                    target=hostname,
                    script=f'''
                    $svc = Get-Service -Name "{self.SERVICE_NAME}" -ErrorAction SilentlyContinue
                    if ($svc) {{
                        $exePath = (Get-WmiObject Win32_Service -Filter "Name='{self.SERVICE_NAME}'").PathName
                        $version = if (Test-Path $exePath) {{
                            (Get-Item $exePath).VersionInfo.FileVersion
                        }} else {{
                            "unknown"
                        }}
                        @{{
                            "installed" = $true
                            "status" = $svc.Status.ToString()
                            "start_type" = $svc.StartType.ToString()
                            "version" = $version
                        }} | ConvertTo-Json
                    }} else {{
                        @{{"installed" = $false}} | ConvertTo-Json
                    }}
                    ''',
                    credentials=credentials,
                    timeout_seconds=15,
                )
                
                if result.success:
                    import json
                    output = result.output.get("stdout", "{}")
                    return json.loads(output)
                else:
                    return {"installed": False, "error": result.error}
            
            return await loop.run_in_executor(None, _check)
            
        except Exception as e:
            return {"installed": False, "error": str(e)}
