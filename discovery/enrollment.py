#!/usr/bin/env python3
"""
Automated Enrollment Pipeline
Orchestrates device discovery, classification, and enrollment into monitoring
"""

import asyncio
import logging
import json
import os
from typing import List, Dict, Optional
from datetime import datetime
from pathlib import Path

import aiohttp
import asyncssh

from scanner import NetworkDiscovery, DiscoveredDevice
from classifier import DeviceClassifier

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AutoEnrollmentPipeline:
    """
    Main enrollment pipeline orchestrator
    Handles discovery → classification → enrollment flow
    """

    def __init__(self, config: Dict):
        self.config = config
        self.client_id = config['client_id']
        self.mcp_server_url = config['mcp_server_url']
        self.terraform_path = config.get('terraform_path', '/tmp/terraform')

        # Initialize components
        self.discovery = NetworkDiscovery(
            client_id=self.client_id,
            config=config.get('discovery', {})
        )

        self.classifier = DeviceClassifier(
            config=config.get('classifier', {})
        )

        # State tracking
        self.enrollment_queue = []
        self.enrollment_results = []

    async def run_pipeline(self, subnets: List[str]) -> Dict:
        """
        Execute the full enrollment pipeline
        Returns summary of enrollment actions
        """
        logger.info(f"Starting enrollment pipeline for client {self.client_id}")

        pipeline_start = datetime.utcnow()

        # Step 1: Discover devices
        logger.info("Step 1: Device Discovery")
        discovered_devices = await self.discovery.discover_devices(subnets)
        logger.info(f"Discovered {len(discovered_devices)} devices")

        # Step 2: Classify devices
        logger.info("Step 2: Device Classification")
        classified_devices = []
        for device in discovered_devices:
            device_dict = device.to_dict()
            classified = self.classifier.classify_device(device_dict)
            classified_devices.append(classified)

        # Step 3: Filter and queue for enrollment
        logger.info("Step 3: Enrollment Filtering")
        self.enrollment_queue = self._filter_for_enrollment(classified_devices)
        logger.info(f"Queued {len(self.enrollment_queue)} devices for enrollment")

        # Step 4: Process enrollment queue
        logger.info("Step 4: Processing Enrollments")
        for device in self.enrollment_queue:
            result = await self._process_enrollment(device)
            self.enrollment_results.append(result)

        # Step 5: Generate summary
        pipeline_end = datetime.utcnow()
        duration = (pipeline_end - pipeline_start).total_seconds()

        summary = {
            'client_id': self.client_id,
            'pipeline_start': pipeline_start.isoformat(),
            'pipeline_end': pipeline_end.isoformat(),
            'duration_seconds': duration,
            'discovered_count': len(discovered_devices),
            'classified_count': len(classified_devices),
            'enrollment_queue_count': len(self.enrollment_queue),
            'enrollment_results': self._summarize_results(),
            'classification_report': self.classifier.generate_classification_report(
                classified_devices
            )
        }

        # Save detailed results
        await self._save_results(discovered_devices, classified_devices, summary)

        logger.info(f"Pipeline complete in {duration:.1f}s")

        return summary

    def _filter_for_enrollment(self, devices: List[Dict]) -> List[Dict]:
        """
        Filter devices that should be enrolled
        Based on should_monitor flag and auto_enroll strategy
        """
        enrollment_queue = []

        for device in devices:
            # Skip if shouldn't be monitored
            if not device.get('should_monitor', False):
                logger.debug(f"Skipping {device['ip']}: should_monitor=False")
                continue

            # Check if already monitored
            if device.get('monitored', False):
                logger.debug(f"Skipping {device['ip']}: already monitored")
                continue

            # Check monitoring strategy
            strategy = device.get('monitoring_strategy', {})

            # Auto-enroll if strategy allows
            if strategy.get('auto_enroll', False):
                enrollment_queue.append(device)
                logger.info(
                    f"Queued for auto-enrollment: {device['ip']} "
                    f"({device['device_type']})"
                )

            # Queue for manual review otherwise
            else:
                device['enrollment_status'] = 'manual_review_required'
                logger.info(
                    f"Queued for manual review: {device['ip']} "
                    f"({strategy.get('reason', 'N/A')})"
                )

        return enrollment_queue

    async def _process_enrollment(self, device: Dict) -> Dict:
        """
        Process a single device enrollment
        Returns enrollment result
        """
        logger.info(f"Processing enrollment: {device['ip']}")

        result = {
            'device_ip': device['ip'],
            'device_type': device['device_type'],
            'tier': device['tier'],
            'method': device['monitoring_strategy']['method'],
            'status': 'pending',
            'actions': [],
            'errors': [],
            'timestamp': datetime.utcnow().isoformat()
        }

        try:
            strategy = device['monitoring_strategy']
            method = strategy['method']

            if method == 'agent':
                await self._enroll_agent_based(device, result)
            elif method == 'snmp':
                await self._enroll_snmp_monitoring(device, result)
            elif method == 'agentless':
                await self._enroll_agentless(device, result)
            else:
                result['status'] = 'skipped'
                result['errors'].append(f"Unsupported method: {method}")

        except Exception as e:
            logger.error(f"Enrollment failed for {device['ip']}: {e}")
            result['status'] = 'failed'
            result['errors'].append(str(e))

        return result

    async def _enroll_agent_based(self, device: Dict, result: Dict):
        """
        Deploy full monitoring agent for servers
        """
        logger.info(f"Agent-based enrollment: {device['ip']}")

        # Step 1: Check SSH access
        if not await self._check_ssh_access(device):
            result['status'] = 'failed'
            result['errors'].append('SSH access not available')
            return

        result['actions'].append('ssh_access_verified')

        # Step 2: Generate Terraform configuration
        tf_config = self._generate_terraform_device_config(device)
        result['actions'].append('terraform_config_generated')

        # Step 3: Apply Terraform configuration
        if self.config.get('terraform_auto_apply', False):
            await self._apply_terraform_config(device, tf_config)
            result['actions'].append('terraform_applied')
        else:
            result['actions'].append('terraform_config_saved_for_review')

        # Step 4: Bootstrap agent (if SSH available)
        if await self._check_ssh_access(device):
            await self._bootstrap_agent(device)
            result['actions'].append('agent_bootstrapped')
        else:
            result['actions'].append('agent_bootstrap_queued_manual')

        # Step 5: Register with MCP server
        await self._register_with_mcp(device)
        result['actions'].append('registered_with_mcp')

        result['status'] = 'success'

    async def _enroll_snmp_monitoring(self, device: Dict, result: Dict):
        """
        Configure agentless SNMP monitoring for network gear
        """
        logger.info(f"SNMP enrollment: {device['ip']}")

        monitoring_config = {
            'device_id': f"{device['client_id']}-{device['ip'].replace('.', '-')}",
            'client_id': device['client_id'],
            'ip': device['ip'],
            'hostname': device.get('hostname'),
            'monitoring_method': 'snmp',
            'snmp_version': '2c',
            'poll_interval': 300,
            'metrics': [
                'sysUpTime',
                'ifInOctets',
                'ifOutOctets',
                'ifInErrors',
                'ifOutErrors'
            ]
        }

        # Register with MCP server
        await self._register_with_mcp(monitoring_config)
        result['actions'].append('registered_with_mcp')

        # Add to monitoring configuration
        await self._add_to_monitoring_config(monitoring_config)
        result['actions'].append('added_to_monitoring_config')

        result['status'] = 'success'

    async def _enroll_agentless(self, device: Dict, result: Dict):
        """
        Configure agentless monitoring (syslog, SNMP traps, NetFlow)
        """
        logger.info(f"Agentless enrollment: {device['ip']}")

        monitoring_config = {
            'device_id': f"{device['client_id']}-{device['ip'].replace('.', '-')}",
            'client_id': device['client_id'],
            'ip': device['ip'],
            'monitoring_method': 'agentless',
            'methods': []
        }

        # Check what's available
        if self._supports_syslog(device):
            monitoring_config['methods'].append('syslog')
            await self._configure_syslog_forwarding(device)
            result['actions'].append('syslog_configured')

        if self._supports_snmp(device):
            monitoring_config['methods'].append('snmp')
            await self._configure_snmp_polling(device)
            result['actions'].append('snmp_configured')

        # Register with MCP
        await self._register_with_mcp(monitoring_config)
        result['actions'].append('registered_with_mcp')

        result['status'] = 'success'

    async def _check_ssh_access(self, device: Dict) -> bool:
        """Test SSH connectivity to device"""
        ip = device['ip']
        ssh_config = self.config.get('ssh', {})

        try:
            async with asyncssh.connect(
                ip,
                username=ssh_config.get('username', 'admin'),
                client_keys=ssh_config.get('client_keys'),
                known_hosts=None,
                connect_timeout=5
            ) as conn:
                result = await conn.run('echo test', check=True)
                return result.exit_status == 0
        except Exception as e:
            logger.debug(f"SSH check failed for {ip}: {e}")
            return False

    def _generate_terraform_device_config(self, device: Dict) -> str:
        """Generate Terraform configuration for monitoring this device"""

        device_id = device['ip'].replace('.', '-')

        config = f"""
resource "msp_monitored_device" "{device_id}" {{
  client_id    = "{device['client_id']}"
  device_id    = "{device_id}"
  hostname     = "{device.get('hostname', device['ip'])}"
  ip_address   = "{device['ip']}"
  device_type  = "{device['device_type']}"
  tier         = {device['tier']}
  monitoring   = "{device['monitoring_strategy']['method']}"

  auto_enrolled = true
  discovery_date = "{device['timestamp']}"

  hipaa_controls = {json.dumps(device['monitoring_strategy']['hipaa_controls'])}

  tags = {{
    auto_enrolled = "true"
    discovery_method = "{device.get('discovery_method', 'unknown')}"
    os = "{device.get('os', 'unknown')}"
  }}
}}
"""
        return config

    async def _apply_terraform_config(self, device: Dict, config: str):
        """Write and apply Terraform configuration"""
        device_id = device['ip'].replace('.', '-')
        client_dir = Path(self.terraform_path) / 'clients' / device['client_id']
        client_dir.mkdir(parents=True, exist_ok=True)

        # Write configuration file
        config_file = client_dir / f"device_{device_id}.tf"
        config_file.write_text(config)

        logger.info(f"Wrote Terraform config: {config_file}")

        # Note: Actual terraform apply would be done by CD pipeline
        # This just writes the config files

    async def _bootstrap_agent(self, device: Dict):
        """
        SSH into device and install monitoring agent
        """
        ip = device['ip']
        device_type = device['device_type']
        ssh_config = self.config.get('ssh', {})

        logger.info(f"Bootstrapping agent on {ip}")

        if device_type.startswith('linux'):
            await self._bootstrap_linux_agent(device)
        elif device_type.startswith('windows'):
            await self._bootstrap_windows_agent(device)
        else:
            logger.warning(f"No bootstrap method for {device_type}")

    async def _bootstrap_linux_agent(self, device: Dict):
        """Install NixOS monitoring agent via SSH"""

        bootstrap_script = f"""
# Download and install Nix (if not present)
if ! command -v nix &> /dev/null; then
    curl -L https://nixos.org/nix/install | sh
    . ~/.nix-profile/etc/profile.d/nix.sh
fi

# Install monitoring agent from flake
nix profile install {self.config['flake_git_url']}#watcher

# Configure agent
cat > /etc/msp-watcher.conf <<EOF
client_id: {device['client_id']}
device_id: {device['ip']}
mcp_server: {self.mcp_server_url}
api_key: {{vault:msp/clients/{device['client_id']}/api_key}}
EOF

# Enable and start service
systemctl enable msp-watcher
systemctl start msp-watcher
"""

        ssh_config = self.config.get('ssh', {})

        try:
            async with asyncssh.connect(
                device['ip'],
                username=ssh_config.get('username', 'admin'),
                client_keys=ssh_config.get('client_keys'),
                known_hosts=None
            ) as conn:
                result = await conn.run(bootstrap_script, check=False)

                if result.exit_status == 0:
                    logger.info(f"Agent bootstrapped successfully on {device['ip']}")
                else:
                    logger.error(
                        f"Agent bootstrap failed on {device['ip']}: "
                        f"{result.stderr}"
                    )
        except Exception as e:
            logger.error(f"Failed to bootstrap agent on {device['ip']}: {e}")

    async def _bootstrap_windows_agent(self, device: Dict):
        """Install Windows monitoring agent via WinRM/SSH"""
        # Placeholder for Windows agent deployment
        logger.warning("Windows agent bootstrap not yet implemented")

    async def _register_with_mcp(self, config: Dict):
        """Register device with MCP server"""
        url = f"{self.mcp_server_url}/api/devices/register"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=config) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        logger.info(f"Device registered: {config.get('ip', config.get('device_id'))}")
                        return result
                    else:
                        logger.error(f"Registration failed: {resp.status}")
                        return None
        except Exception as e:
            logger.error(f"Failed to register device: {e}")
            return None

    async def _add_to_monitoring_config(self, config: Dict):
        """Add device to monitoring system configuration"""
        # Placeholder - would integrate with Prometheus, Telegraph, etc.
        logger.info(f"Added to monitoring config: {config['device_id']}")

    def _supports_syslog(self, device: Dict) -> bool:
        """Check if device supports syslog forwarding"""
        # Simple heuristic based on device type
        supported_types = [
            'network_infrastructure',
            'firewall',
            'linux_server',
            'web_server'
        ]
        return device.get('device_type') in supported_types

    def _supports_snmp(self, device: Dict) -> bool:
        """Check if device supports SNMP"""
        services = device.get('services', [])
        return any(
            s.get('port') in [161, 162] and s.get('state') == 'open'
            for s in services
        )

    async def _configure_syslog_forwarding(self, device: Dict):
        """Configure device to forward syslog to central collector"""
        logger.info(f"Syslog forwarding configuration: {device['ip']}")
        # Placeholder - would SSH in and configure rsyslog/syslog-ng

    async def _configure_snmp_polling(self, device: Dict):
        """Configure SNMP polling for device"""
        logger.info(f"SNMP polling configuration: {device['ip']}")
        # Placeholder - would add to Prometheus/Telegraph config

    def _summarize_results(self) -> Dict:
        """Summarize enrollment results"""
        summary = {
            'total': len(self.enrollment_results),
            'success': 0,
            'failed': 0,
            'skipped': 0,
            'by_method': {},
            'by_tier': {}
        }

        for result in self.enrollment_results:
            status = result.get('status', 'unknown')
            summary[status] = summary.get(status, 0) + 1

            method = result.get('method', 'unknown')
            summary['by_method'][method] = summary['by_method'].get(method, 0) + 1

            tier = result.get('tier', 0)
            summary['by_tier'][tier] = summary['by_tier'].get(tier, 0) + 1

        return summary

    async def _save_results(
        self,
        discovered: List[DiscoveredDevice],
        classified: List[Dict],
        summary: Dict
    ):
        """Save pipeline results to disk"""

        output_dir = Path(self.config.get('output_dir', '/tmp/enrollment'))
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')

        # Save discovered devices
        discovered_file = output_dir / f"discovered_{timestamp}.json"
        with open(discovered_file, 'w') as f:
            json.dump([d.to_dict() for d in discovered], f, indent=2)

        # Save classified devices
        classified_file = output_dir / f"classified_{timestamp}.json"
        with open(classified_file, 'w') as f:
            json.dump(classified, f, indent=2)

        # Save enrollment results
        results_file = output_dir / f"enrollment_results_{timestamp}.json"
        with open(results_file, 'w') as f:
            json.dump({
                'summary': summary,
                'detailed_results': self.enrollment_results
            }, f, indent=2)

        logger.info(f"Results saved to {output_dir}")


async def main():
    """Example usage"""

    config = {
        'client_id': 'clinic-001',
        'mcp_server_url': 'https://mcp.example.com',
        'flake_git_url': 'github:yourorg/msp-platform',
        'terraform_path': '/tmp/terraform',
        'terraform_auto_apply': False,
        'output_dir': '/tmp/enrollment',

        'discovery': {
            'methods': ['active_nmap'],
            'snmp_community': 'public'
        },

        'classifier': {
            'auto_enroll_tiers': [1, 2],
            'excluded_types': [
                'windows_workstation',
                'macos_workstation',
                'printer'
            ]
        },

        'ssh': {
            'username': 'admin',
            'client_keys': ['~/.ssh/id_ed25519']
        }
    }

    pipeline = AutoEnrollmentPipeline(config)

    # Run pipeline
    subnets = ['192.168.1.0/24']
    summary = await pipeline.run_pipeline(subnets)

    # Print summary
    print("\n=== Enrollment Pipeline Summary ===")
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
