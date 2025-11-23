"""
Self-healing remediation engine for compliance drift.

This module implements automated remediation actions for the 6 drift types
detected by the DriftDetector. Each remediation follows a pattern:
1. Check maintenance window (if disruptive)
2. Capture pre-state
3. Execute remediation steps
4. Verify post-state health check
5. Generate RemediationResult with evidence

All remediations support rollback where applicable.
"""

import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, List
from pathlib import Path
import json

from .models import (
    RemediationResult,
    ActionTaken,
    DriftResult,
)
from .config import AgentConfig
from .utils import (
    run_command,
    is_within_maintenance_window,
    AsyncCommandError
)


class HealingEngine:
    """
    Self-healing engine for automated remediation.
    
    Implements 6 remediation actions:
    1. update_to_baseline_generation - Switch NixOS generation
    2. restart_av_service - Restart AV/EDR service
    3. run_backup_job - Trigger backup manually
    4. restart_logging_services - Restart logging stack
    5. restore_firewall_baseline - Reapply firewall ruleset
    6. enable_volume_encryption - Alert for manual intervention
    """
    
    def __init__(self, config: AgentConfig):
        """
        Initialize healing engine.
        
        Args:
            config: Agent configuration
        """
        self.config = config
        self.deployment_mode = config.deployment_mode
        self.maintenance_window_start = config.maintenance_window_start
        self.maintenance_window_end = config.maintenance_window_end
        
    async def remediate(self, drift: DriftResult) -> RemediationResult:
        """
        Main remediation dispatcher.
        
        Routes drift to appropriate remediation handler based on check type.
        
        Args:
            drift: DriftResult from detection
            
        Returns:
            RemediationResult with outcome and evidence
        """
        remediation_map = {
            "patching": self.update_to_baseline_generation,
            "av_edr_health": self.restart_av_service,
            "backup_verification": self.run_backup_job,
            "logging_continuity": self.restart_logging_services,
            "firewall_baseline": self.restore_firewall_baseline,
            "encryption": self.enable_volume_encryption
        }
        
        handler = remediation_map.get(drift.check)
        
        if not handler:
            return RemediationResult(
                check=drift.check,
                outcome="failed",
                error=f"No remediation handler for check: {drift.check}"
            )
        
        try:
            return await handler(drift)
        except Exception as e:
            return RemediationResult(
                check=drift.check,
                outcome="failed",
                pre_state=drift.pre_state,
                error=str(e)
            )
    
    # ========================================================================
    # Remediation Action 1: Update to Baseline Generation
    # ========================================================================
    
    async def update_to_baseline_generation(
        self,
        drift: DriftResult
    ) -> RemediationResult:
        """
        Switch to baseline NixOS generation.
        
        This is a disruptive action that requires maintenance window.
        Supports rollback to previous generation.
        
        Args:
            drift: Patching drift result
            
        Returns:
            RemediationResult with rollback info
        """
        # Check maintenance window (disruptive action)
        if not is_within_maintenance_window(
            self.maintenance_window_start,
            self.maintenance_window_end
        ):
            return RemediationResult(
                check="patching",
                outcome="deferred",
                pre_state=drift.pre_state,
                error="Outside maintenance window"
            )
        
        actions = []
        pre_state = drift.pre_state.copy()
        
        # Get current generation for rollback
        try:
            result = await run_command(
                'nixos-rebuild list-generations | tail -1',
                timeout=10.0
            )
            current_gen = int(result.stdout.strip().split()[0])
        except Exception as e:
            return RemediationResult(
                check="patching",
                outcome="failed",
                pre_state=pre_state,
                error=f"Failed to get current generation: {e}"
            )
        
        actions.append(ActionTaken(
            action="capture_current_generation",
            timestamp=datetime.utcnow(),
            details={"generation": current_gen}
        ))
        
        # Get target generation from baseline
        target_gen = pre_state.get("baseline_generation")
        
        if not target_gen:
            return RemediationResult(
                check="patching",
                outcome="failed",
                pre_state=pre_state,
                error="No target generation in baseline"
            )
        
        # Switch to target generation
        try:
            result = await run_command(
                f'nixos-rebuild switch --rollback {target_gen}',
                timeout=300.0
            )
            
            actions.append(ActionTaken(
                action="switch_generation",
                timestamp=datetime.utcnow(),
                command=f'nixos-rebuild switch --rollback {target_gen}',
                exit_code=0,
                details={"target_generation": target_gen}
            ))
        except AsyncCommandError as e:
            actions.append(ActionTaken(
                action="switch_generation",
                timestamp=datetime.utcnow(),
                command=f'nixos-rebuild switch --rollback {target_gen}',
                exit_code=e.exit_code,
                stdout=e.stdout,
                stderr=e.stderr
            ))
            
            return RemediationResult(
                check="patching",
                outcome="failed",
                pre_state=pre_state,
                actions=actions,
                error=f"Generation switch failed: {e.stderr}",
                rollback_available=True,
                rollback_generation=current_gen
            )
        
        # Health check: verify new generation is active
        try:
            result = await run_command(
                'nixos-rebuild list-generations | grep current | awk \'{print $1}\'',
                timeout=10.0
            )
            new_gen = int(result.stdout.strip())
            
            post_state = {
                "generation": new_gen,
                "verified": new_gen == target_gen
            }
            
            actions.append(ActionTaken(
                action="verify_generation",
                timestamp=datetime.utcnow(),
                details=post_state
            ))
            
            if new_gen != target_gen:
                # Rollback to previous generation
                await run_command(
                    f'nixos-rebuild switch --rollback {current_gen}',
                    timeout=300.0
                )
                
                return RemediationResult(
                    check="patching",
                    outcome="reverted",
                    pre_state=pre_state,
                    post_state=post_state,
                    actions=actions,
                    error="Generation switch verification failed, rolled back"
                )
            
            return RemediationResult(
                check="patching",
                outcome="success",
                pre_state=pre_state,
                post_state=post_state,
                actions=actions,
                rollback_available=True,
                rollback_generation=current_gen
            )
            
        except Exception as e:
            return RemediationResult(
                check="patching",
                outcome="failed",
                pre_state=pre_state,
                actions=actions,
                error=f"Health check failed: {e}",
                rollback_available=True,
                rollback_generation=current_gen
            )
    
    # ========================================================================
    # Remediation Action 2: Restart AV/EDR Service
    # ========================================================================
    
    async def restart_av_service(
        self,
        drift: DriftResult
    ) -> RemediationResult:
        """
        Restart AV/EDR service.
        
        This is minimally disruptive and doesn't require maintenance window.
        No rollback needed (service restart is idempotent).
        
        Args:
            drift: AV/EDR health drift result
            
        Returns:
            RemediationResult with service status
        """
        actions = []
        pre_state = drift.pre_state.copy()
        
        # Get service name from pre_state or use default
        service_name = pre_state.get("av_service", "clamav-daemon")
        
        # Restart service
        try:
            result = await run_command(
                f'systemctl restart {service_name}',
                timeout=60.0
            )
            
            actions.append(ActionTaken(
                action="restart_service",
                timestamp=datetime.utcnow(),
                command=f'systemctl restart {service_name}',
                exit_code=0,
                details={"service": service_name}
            ))
        except AsyncCommandError as e:
            actions.append(ActionTaken(
                action="restart_service",
                timestamp=datetime.utcnow(),
                command=f'systemctl restart {service_name}',
                exit_code=e.exit_code,
                stdout=e.stdout,
                stderr=e.stderr
            ))
            
            return RemediationResult(
                check="av_edr_health",
                outcome="failed",
                pre_state=pre_state,
                actions=actions,
                error=f"Service restart failed: {e.stderr}"
            )
        
        # Health check: verify service is active
        try:
            result = await run_command(
                f'systemctl is-active {service_name}',
                timeout=10.0
            )
            
            is_active = result.stdout.strip() == "active"
            
            # Verify binary hash (if available in pre_state)
            binary_hash = None
            if "av_binary_path" in pre_state:
                hash_result = await run_command(
                    f'sha256sum {pre_state["av_binary_path"]} | awk \'{{print $1}}\'',
                    timeout=10.0
                )
                binary_hash = hash_result.stdout.strip()
            
            post_state = {
                "service_active": is_active,
                "binary_hash": binary_hash
            }
            
            actions.append(ActionTaken(
                action="verify_service",
                timestamp=datetime.utcnow(),
                details=post_state
            ))
            
            if not is_active:
                return RemediationResult(
                    check="av_edr_health",
                    outcome="failed",
                    pre_state=pre_state,
                    post_state=post_state,
                    actions=actions,
                    error="Service not active after restart"
                )
            
            return RemediationResult(
                check="av_edr_health",
                outcome="success",
                pre_state=pre_state,
                post_state=post_state,
                actions=actions
            )
            
        except Exception as e:
            return RemediationResult(
                check="av_edr_health",
                outcome="failed",
                pre_state=pre_state,
                actions=actions,
                error=f"Health check failed: {e}"
            )
    
    # ========================================================================
    # Remediation Action 3: Run Backup Job
    # ========================================================================
    
    async def run_backup_job(
        self,
        drift: DriftResult
    ) -> RemediationResult:
        """
        Trigger backup job manually.
        
        This is minimally disruptive and doesn't require maintenance window.
        No rollback needed (backup is append-only).
        
        Args:
            drift: Backup verification drift result
            
        Returns:
            RemediationResult with backup status
        """
        actions = []
        pre_state = drift.pre_state.copy()
        
        # Get backup service from pre_state or use default
        backup_service = pre_state.get("backup_service", "restic-backup")
        
        # Trigger backup
        try:
            result = await run_command(
                f'systemctl start {backup_service}',
                timeout=600.0  # Backups can take time
            )
            
            actions.append(ActionTaken(
                action="trigger_backup",
                timestamp=datetime.utcnow(),
                command=f'systemctl start {backup_service}',
                exit_code=0,
                details={"service": backup_service}
            ))
        except AsyncCommandError as e:
            actions.append(ActionTaken(
                action="trigger_backup",
                timestamp=datetime.utcnow(),
                command=f'systemctl start {backup_service}',
                exit_code=e.exit_code,
                stdout=e.stdout,
                stderr=e.stderr
            ))
            
            return RemediationResult(
                check="backup_verification",
                outcome="failed",
                pre_state=pre_state,
                actions=actions,
                error=f"Backup job failed: {e.stderr}"
            )
        
        # Health check: verify backup completed successfully
        try:
            # Wait for service to finish
            await asyncio.sleep(5)
            
            result = await run_command(
                f'systemctl status {backup_service}',
                timeout=10.0
            )
            
            # Check for success indicators in output
            backup_success = "succeeded" in result.stdout.lower() or \
                           "completed" in result.stdout.lower()
            
            # Get backup timestamp and checksum (if available)
            backup_timestamp = datetime.utcnow().isoformat()
            backup_checksum = None
            
            if "backup_repo" in pre_state:
                try:
                    checksum_result = await run_command(
                        f'restic -r {pre_state["backup_repo"]} snapshots --latest 1 --json',
                        timeout=30.0
                    )
                    snapshots = json.loads(checksum_result.stdout)
                    if snapshots:
                        backup_checksum = snapshots[0].get("id")
                except Exception:
                    pass
            
            post_state = {
                "backup_success": backup_success,
                "backup_timestamp": backup_timestamp,
                "backup_checksum": backup_checksum
            }
            
            actions.append(ActionTaken(
                action="verify_backup",
                timestamp=datetime.utcnow(),
                details=post_state
            ))
            
            if not backup_success:
                return RemediationResult(
                    check="backup_verification",
                    outcome="failed",
                    pre_state=pre_state,
                    post_state=post_state,
                    actions=actions,
                    error="Backup job did not complete successfully"
                )
            
            return RemediationResult(
                check="backup_verification",
                outcome="success",
                pre_state=pre_state,
                post_state=post_state,
                actions=actions
            )
            
        except Exception as e:
            return RemediationResult(
                check="backup_verification",
                outcome="failed",
                pre_state=pre_state,
                actions=actions,
                error=f"Health check failed: {e}"
            )
    
    # ========================================================================
    # Remediation Action 4: Restart Logging Services
    # ========================================================================
    
    async def restart_logging_services(
        self,
        drift: DriftResult
    ) -> RemediationResult:
        """
        Restart logging stack services.
        
        This is minimally disruptive and doesn't require maintenance window.
        Restarts rsyslog and journald.
        
        Args:
            drift: Logging continuity drift result
            
        Returns:
            RemediationResult with service statuses
        """
        actions = []
        pre_state = drift.pre_state.copy()
        
        # Services to restart
        logging_services = pre_state.get("logging_services", [
            "rsyslog",
            "systemd-journald"
        ])
        
        # Restart each service
        for service in logging_services:
            try:
                result = await run_command(
                    f'systemctl restart {service}',
                    timeout=30.0
                )
                
                actions.append(ActionTaken(
                    action="restart_service",
                    timestamp=datetime.utcnow(),
                    command=f'systemctl restart {service}',
                    exit_code=0,
                    details={"service": service}
                ))
            except AsyncCommandError as e:
                actions.append(ActionTaken(
                    action="restart_service",
                    timestamp=datetime.utcnow(),
                    command=f'systemctl restart {service}',
                    exit_code=e.exit_code,
                    stdout=e.stdout,
                    stderr=e.stderr
                ))
                
                return RemediationResult(
                    check="logging_continuity",
                    outcome="failed",
                    pre_state=pre_state,
                    actions=actions,
                    error=f"Service restart failed for {service}: {e.stderr}"
                )
        
        # Health check: verify all services are active
        try:
            service_statuses = {}
            
            for service in logging_services:
                result = await run_command(
                    f'systemctl is-active {service}',
                    timeout=10.0
                )
                service_statuses[service] = result.stdout.strip() == "active"
            
            # Write canary log entry
            canary_msg = f"MSP Compliance Agent - Logging Health Check - {datetime.utcnow().isoformat()}"
            await run_command(
                f'logger -t msp-agent "{canary_msg}"',
                timeout=5.0
            )
            
            # Verify canary appears in journal
            await asyncio.sleep(2)
            result = await run_command(
                f'journalctl -t msp-agent --since "10 seconds ago" | grep "{canary_msg}"',
                timeout=10.0
            )
            canary_found = canary_msg in result.stdout
            
            post_state = {
                "service_statuses": service_statuses,
                "canary_verified": canary_found
            }
            
            actions.append(ActionTaken(
                action="verify_logging",
                timestamp=datetime.utcnow(),
                details=post_state
            ))
            
            all_active = all(service_statuses.values())
            
            if not all_active or not canary_found:
                return RemediationResult(
                    check="logging_continuity",
                    outcome="failed",
                    pre_state=pre_state,
                    post_state=post_state,
                    actions=actions,
                    error="Logging services not fully operational after restart"
                )
            
            return RemediationResult(
                check="logging_continuity",
                outcome="success",
                pre_state=pre_state,
                post_state=post_state,
                actions=actions
            )
            
        except Exception as e:
            return RemediationResult(
                check="logging_continuity",
                outcome="failed",
                pre_state=pre_state,
                actions=actions,
                error=f"Health check failed: {e}"
            )
    
    # ========================================================================
    # Remediation Action 5: Restore Firewall Baseline
    # ========================================================================
    
    async def restore_firewall_baseline(
        self,
        drift: DriftResult
    ) -> RemediationResult:
        """
        Restore firewall rules to baseline.
        
        This is a disruptive action that requires maintenance window.
        Supports rollback by saving current rules.
        
        Args:
            drift: Firewall baseline drift result
            
        Returns:
            RemediationResult with rollback info
        """
        # Check maintenance window (disruptive action)
        if not is_within_maintenance_window(
            self.maintenance_window_start,
            self.maintenance_window_end
        ):
            return RemediationResult(
                check="firewall_baseline",
                outcome="deferred",
                pre_state=drift.pre_state,
                error="Outside maintenance window"
            )
        
        actions = []
        pre_state = drift.pre_state.copy()
        
        # Save current firewall rules for rollback
        try:
            result = await run_command(
                'iptables-save',
                timeout=10.0
            )
            current_rules = result.stdout
            
            # Write to temp file for rollback
            rollback_path = Path("/tmp/msp-firewall-rollback.rules")
            rollback_path.write_text(current_rules)
            
            actions.append(ActionTaken(
                action="save_current_rules",
                timestamp=datetime.utcnow(),
                details={"rollback_path": str(rollback_path)}
            ))
        except Exception as e:
            return RemediationResult(
                check="firewall_baseline",
                outcome="failed",
                pre_state=pre_state,
                error=f"Failed to save current rules: {e}"
            )
        
        # Get baseline rules path
        baseline_rules_path = pre_state.get("baseline_rules_path", "/etc/firewall/baseline.rules")
        
        if not Path(baseline_rules_path).exists():
            return RemediationResult(
                check="firewall_baseline",
                outcome="failed",
                pre_state=pre_state,
                actions=actions,
                error=f"Baseline rules not found at {baseline_rules_path}"
            )
        
        # Apply baseline rules
        try:
            result = await run_command(
                f'iptables-restore < {baseline_rules_path}',
                timeout=30.0
            )
            
            actions.append(ActionTaken(
                action="apply_baseline_rules",
                timestamp=datetime.utcnow(),
                command=f'iptables-restore < {baseline_rules_path}',
                exit_code=0,
                details={"baseline_path": baseline_rules_path}
            ))
        except AsyncCommandError as e:
            actions.append(ActionTaken(
                action="apply_baseline_rules",
                timestamp=datetime.utcnow(),
                command=f'iptables-restore < {baseline_rules_path}',
                exit_code=e.exit_code,
                stdout=e.stdout,
                stderr=e.stderr
            ))
            
            # Rollback to previous rules
            try:
                await run_command(
                    f'iptables-restore < {rollback_path}',
                    timeout=30.0
                )
            except Exception:
                pass
            
            return RemediationResult(
                check="firewall_baseline",
                outcome="reverted",
                pre_state=pre_state,
                actions=actions,
                error=f"Baseline apply failed, rolled back: {e.stderr}",
                rollback_available=True
            )
        
        # Health check: verify rules hash matches baseline
        try:
            result = await run_command(
                'iptables-save | sha256sum | awk \'{print $1}\'',
                timeout=10.0
            )
            new_hash = result.stdout.strip()
            
            baseline_hash = pre_state.get("baseline_hash")
            
            post_state = {
                "rules_hash": new_hash,
                "matches_baseline": new_hash == baseline_hash
            }
            
            actions.append(ActionTaken(
                action="verify_rules",
                timestamp=datetime.utcnow(),
                details=post_state
            ))
            
            if new_hash != baseline_hash:
                # Rollback to previous rules
                await run_command(
                    f'iptables-restore < {rollback_path}',
                    timeout=30.0
                )
                
                return RemediationResult(
                    check="firewall_baseline",
                    outcome="reverted",
                    pre_state=pre_state,
                    post_state=post_state,
                    actions=actions,
                    error="Rules hash mismatch after apply, rolled back"
                )
            
            return RemediationResult(
                check="firewall_baseline",
                outcome="success",
                pre_state=pre_state,
                post_state=post_state,
                actions=actions,
                rollback_available=True
            )
            
        except Exception as e:
            # Rollback on health check failure
            try:
                await run_command(
                    f'iptables-restore < {rollback_path}',
                    timeout=30.0
                )
            except Exception:
                pass
            
            return RemediationResult(
                check="firewall_baseline",
                outcome="reverted",
                pre_state=pre_state,
                actions=actions,
                error=f"Health check failed, rolled back: {e}",
                rollback_available=True
            )
    
    # ========================================================================
    # Remediation Action 6: Enable Volume Encryption (Alert Only)
    # ========================================================================
    
    async def enable_volume_encryption(
        self,
        drift: DriftResult
    ) -> RemediationResult:
        """
        Alert for manual encryption intervention.
        
        Encryption cannot be enabled automatically on mounted volumes.
        This creates an alert outcome for administrator action.
        
        Args:
            drift: Encryption drift result
            
        Returns:
            RemediationResult with alert outcome
        """
        actions = []
        pre_state = drift.pre_state.copy()
        
        # Document unencrypted volumes
        unencrypted_volumes = pre_state.get("unencrypted_volumes", [])
        
        actions.append(ActionTaken(
            action="document_unencrypted_volumes",
            timestamp=datetime.utcnow(),
            details={
                "unencrypted_volumes": unencrypted_volumes,
                "requires_manual_intervention": True
            }
        ))
        
        # Create alert for administrator
        alert_message = (
            f"MANUAL INTERVENTION REQUIRED: "
            f"{len(unencrypted_volumes)} unencrypted volume(s) detected. "
            f"Volumes: {', '.join(unencrypted_volumes)}. "
            f"Encryption must be enabled manually (requires unmounting and data migration)."
        )
        
        # Log alert
        try:
            await run_command(
                f'logger -t msp-agent -p user.warning "{alert_message}"',
                timeout=5.0
            )
            
            actions.append(ActionTaken(
                action="log_alert",
                timestamp=datetime.utcnow(),
                details={"alert_message": alert_message}
            ))
        except Exception:
            pass
        
        return RemediationResult(
            check="encryption",
            outcome="alert",
            pre_state=pre_state,
            post_state={"alert_logged": True},
            actions=actions,
            error=alert_message
        )
