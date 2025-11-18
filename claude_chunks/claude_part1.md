5. **Audit log** - Append every tool call + output to tamper-evident file

### Rate Limiting Implementation

```python
# guardrails/rate_limits.py
import redis
from datetime import timedelta

class RateLimiter:
    def __init__(self, redis_client):
        self.redis = redis_client
        self.cooldown_seconds = 300  # 5 minutes
    
    def check_and_set(self, client_id: str, hostname: str, tool_name: str) -> bool:
        """Returns True if action is allowed, False if rate limited"""
        key = f"rate:{client_id}:{hostname}:{tool_name}"
        
        if self.redis.exists(key):
            return False
        
        # Set cooldown
        self.redis.setex(key, self.cooldown_seconds, "1")
        return True
    
    def remaining_cooldown(self, client_id: str, hostname: str, tool_name: str) -> int:
        """Returns seconds remaining in cooldown"""
        key = f"rate:{client_id}:{hostname}:{tool_name}"
        return self.redis.ttl(key)
```

### Parameter Validation

```python
# guardrails/validation.py
from typing import Dict, List
from pydantic import BaseModel, validator
import re

class ServiceRestartParams(BaseModel):
    service_name: str
    
    @validator('service_name')
    def validate_service(cls, v):
        # Whitelist of allowed services
        allowed_services = [
            'nginx', 'postgresql', 'redis',
            'docker', 'containerd'
        ]
        
        if v not in allowed_services:
            raise ValueError(f'Service {v} not in whitelist')
        
        # Reject any shell metacharacters
        if re.search(r'[;&|`$()]', v):
            raise ValueError('Invalid characters in service name')
        
        return v

class ClearCacheParams(BaseModel):
    cache_path: str
    
    @validator('cache_path')
    def validate_path(cls, v):
        # Must be in approved directories
        allowed_prefixes = ['/var/cache/', '/tmp/cache/']
        
        if not any(v.startswith(prefix) for prefix in allowed_prefixes):
            raise ValueError('Cache path not in allowed directories')
        
        # Prevent directory traversal
        if '..' in v or v.startswith('/'):
            raise ValueError('Invalid path')
        
        return v
```

---

## Client Deployment

### Terraform Module for New Client

```hcl
# terraform/modules/client-vm/main.tf
variable "client_id" {
  description = "Unique client identifier"
  type        = string
}

variable "client_name" {
  description = "Human-readable client name"
  type        = string
}

variable "ssh_public_key" {
  description = "SSH public key for access"
  type        = string
}

variable "mcp_api_key" {
  description = "API key for MCP server authentication"
  type        = string
  sensitive   = true
}

resource "aws_instance" "client_station" {
  ami           = data.aws_ami.nixos.id
  instance_type = "t3.small"
  
  user_data = templatefile("${path.module}/cloud-init.yaml", {
    client_id     = var.client_id
    mcp_api_key   = var.mcp_api_key
    ssh_key       = var.ssh_public_key
  })
  
  tags = {
    Name      = "msp-client-${var.client_id}"
    Client    = var.client_name
    ManagedBy = "MSP-Platform"
  }
}

resource "aws_security_group" "client_station" {
  name_description = "MSP Client Station - ${var.client_name}"
  
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["YOUR_MSP_IP/32"]
  }
  
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
```

### Cloud-Init Template

```yaml
# terraform/modules/client-vm/cloud-init.yaml
#cloud-config

write_files:
  - path: /etc/nixos/configuration.nix
    content: |
      { config, pkgs, ... }:
      {
        imports = [ <msp-client-base> ];
        
        networking.hostName = "${client_id}";
        
        services.msp-watcher = {
          enable = true;
          apiKey = "${mcp_api_key}";
        };
      }

runcmd:
  - nixos-rebuild switch
  - systemctl enable msp-watcher
  - systemctl start msp-watcher
```

---

## Network Discovery & Automated Enrollment

### Overview

For efficient client onboarding and continuous compliance monitoring, the system needs to automatically discover, classify, and enroll devices on the healthcare network. This eliminates manual inventory management and ensures comprehensive coverage.

### Discovery Methods (Hybrid Approach)

#### 1. Active Discovery (Scanning)

**Best Practice:** Use multiple methods to build comprehensive asset inventory

```python
# discovery/active_scanner.py
import nmap
import asyncio
from typing import List, Dict
import ipaddress

class NetworkDiscovery:
    def __init__(self, subnet: str, client_id: str):
        self.subnet = subnet
        self.client_id = client_id
        self.nm = nmap.PortScanner()
        
    async def discover_devices(self) -> List[Dict]:
        """
        Multi-method active discovery
        Returns list of discovered devices with metadata
        """
        devices = []
        
        # Method 1: Fast ping sweep for live hosts
        live_hosts = await self._ping_sweep()
        
        # Method 2: Service fingerprinting on live hosts
        for host in live_hosts:
            device_info = await self._fingerprint_device(host)
            devices.append(device_info)
        
        # Method 3: SNMP walk for managed devices
        snmp_devices = await self._snmp_discovery(live_hosts)
        devices.extend(snmp_devices)
        
        # Method 4: mDNS/Bonjour for printers/IoT
        mdns_devices = await self._mdns_discovery()
        devices.extend(mdns_devices)
        
        return devices
    
    async def _ping_sweep(self) -> List[str]:
        """Fast ICMP ping sweep of subnet"""
        self.nm.scan(hosts=self.subnet, arguments='-sn -PE -PP')
        live_hosts = [host for host in self.nm.all_hosts() 
                     if self.nm[host].state() == 'up']
        return live_hosts
    
    async def _fingerprint_device(self, host: str) -> Dict:
        """
        Service and OS fingerprinting
        Identifies device type, OS, running services
        """
        # Comprehensive scan: OS detection, version detection, scripts
        self.nm.scan(
            hosts=host,
            arguments='-sV -O --script=banner,ssh-hostkey,http-title'
        )
        
        device = {
            'ip': host,
            'client_id': self.client_id,
            'discovery_method': 'active_scan',
            'timestamp': datetime.utcnow().isoformat(),
            'hostname': None,
            'mac': None,
            'os': None,
            'device_type': None,
            'services': [],
            'tier': None,  # Will be classified
            'monitored': False,
            'enrollment_status': 'discovered'
        }
        
        if host in self.nm.all_hosts():
            host_data = self.nm[host]
            
            # Extract hostname
            if 'hostnames' in host_data:
                device['hostname'] = host_data['hostnames'][0]['name']
            
            # Extract MAC address
            if 'addresses' in host_data and 'mac' in host_data['addresses']:
                device['mac'] = host_data['addresses']['mac']
            
            # Extract OS information
            if 'osmatch' in host_data and len(host_data['osmatch']) > 0:
                device['os'] = host_data['osmatch'][0]['name']
                device['os_accuracy'] = host_data['osmatch'][0]['accuracy']
            
            # Extract services
            for proto in host_data.all_protocols():
                ports = host_data[proto].keys()
                for port in ports:
                    service_info = host_data[proto][port]
                    device['services'].append({
                        'port': port,
                        'protocol': proto,
                        'name': service_info.get('name', 'unknown'),
                        'product': service_info.get('product', ''),
                        'version': service_info.get('version', ''),
                        'state': service_info.get('state', 'unknown')
                    })
        
        # Classify device type and tier
        device['device_type'] = self._classify_device(device)
        device['tier'] = self._assign_tier(device)
        
        return device
    
    async def _snmp_discovery(self, hosts: List[str]) -> List[Dict]:
        """
        SNMP v2c/v3 discovery for managed network equipment
        Query: sysDescr, sysName, sysLocation, interfaces
        """
        from pysnmp.hlapi import *
        
        snmp_devices = []
        community = 'public'  # Should be from vault in production
        
        for host in hosts:
            try:
                # Query system description
                iterator = getCmd(
                    SnmpEngine(),
                    CommunityData(community),
                    UdpTransportTarget((host, 161), timeout=1, retries=0),
                    ContextData(),
                    ObjectType(ObjectIdentity('SNMPv2-MIB', 'sysDescr', 0)),
                    ObjectType(ObjectIdentity('SNMPv2-MIB', 'sysName', 0)),
                    ObjectType(ObjectIdentity('SNMPv2-MIB', 'sysLocation', 0))
                )
                
                errorIndication, errorStatus, errorIndex, varBinds = next(iterator)
                
                if not errorIndication and not errorStatus:
                    device = {
                        'ip': host,
                        'client_id': self.client_id,
                        'discovery_method': 'snmp',
                        'timestamp': datetime.utcnow().isoformat(),
                        'snmp_sys_descr': str(varBinds[0][1]),
                        'hostname': str(varBinds[1][1]),
                        'location': str(varBinds[2][1]),
                        'device_type': 'network_infrastructure',
                        'tier': 1,  # Network gear is Tier 1
                        'monitored': False,
                        'enrollment_status': 'discovered'
                    }
                    snmp_devices.append(device)
            except Exception:
                pass  # Host doesn't respond to SNMP
        
        return snmp_devices
    
    async def _mdns_discovery(self) -> List[Dict]:
        """
        mDNS/DNS-SD discovery for printers, IoT devices
        Often used by medical devices, printers, network cameras
        """
        from zeroconf import ServiceBrowser, Zeroconf
        
        discovered = []
        zeroconf = Zeroconf()
        
        # Common service types in healthcare
        service_types = [
            "_printer._tcp.local.",
            "_http._tcp.local.",
            "_ipp._tcp.local.",
            "_dicom._tcp.local.",  # Medical imaging devices
            "_pacs._tcp.local."    # PACS systems
        ]
        
        # Browse services (simplified - full implementation needs callbacks)
        for service_type in service_types:
            # ServiceBrowser would populate discovered list via callback
            pass
        
        zeroconf.close()
        return discovered
    
    def _classify_device(self, device: Dict) -> str:
        """
        Classify device type based on services and OS
        Returns: server, workstation, network_device, medical_device, printer, etc.
        """
        services = device.get('services', [])
        os = device.get('os', '').lower()
        
        # Server classification
        if any(s['port'] in [22, 80, 443, 3306, 5432, 1433, 389] for s in services):
            if 'linux' in os or 'unix' in os:
                return 'linux_server'
            elif 'windows server' in os:
                return 'windows_server'
            else:
                return 'server_unknown'
        
        # Network infrastructure
        if any(s['port'] in [23, 161, 162] for s in services):
            return 'network_infrastructure'
        
        # Workstation
        if 'windows' in os and 'server' not in os:
            return 'windows_workstation'
        elif 'mac os' in os or 'darwin' in os:
            return 'macos_workstation'
        
        # Printer
        if any(s['port'] in [515, 631, 9100] for s in services):
            return 'printer'
        
        # Medical device indicators
        if any(s['port'] in [104, 2761, 2762] for s in services):  # DICOM ports
            return 'medical_device'
        
        return 'unknown'
    
    def _assign_tier(self, device: Dict) -> int:
        """
        Assign monitoring tier based on device type
        Tier 1: Infrastructure (easy to monitor)
        Tier 2: Applications (moderate difficulty)
        Tier 3: Business processes (complex)
        """
        device_type = device.get('device_type', 'unknown')
        
        tier_1_types = [
            'linux_server', 'windows_server', 'network_infrastructure',
            'firewall', 'vpn_gateway'
        ]
        
        tier_2_types = [
            'database_server', 'application_server', 'web_server',
            'windows_workstation', 'macos_workstation'
        ]
        
        tier_3_types = [
            'medical_device', 'ehr_server', 'pacs_server'
        ]
        
        if device_type in tier_1_types:
            return 1
        elif device_type in tier_2_types:
            return 2
        elif device_type in tier_3_types:
            return 3
        else:
            return 1  # Default to Tier 1 for unknown
```

#### 2. Passive Discovery (Network Flow Monitoring)

**Advantages:** No active scanning, discovers devices organically

```python
# discovery/passive_monitor.py
from scapy.all import sniff, ARP, IP
import asyncio

class PassiveDiscovery:
    def __init__(self, interface: str, client_id: str):
        self.interface = interface
        self.client_id = client_id
        self.discovered_devices = {}
    
    def start_monitoring(self):
        """
        Passive monitoring via packet capture
        Discovers devices from ARP, DNS, DHCP traffic
        """
        sniff(
            iface=self.interface,
            prn=self._process_packet,
            store=False,
            filter="arp or port 53 or port 67"
        )
    
    def _process_packet(self, packet):
        """Process captured packets to identify devices"""
        
        # ARP packets reveal IP-MAC mappings
        if ARP in packet:
            ip = packet[ARP].psrc
            mac = packet[ARP].hwsrc
            self._register_device(ip, mac, 'arp')
        
        # DNS queries reveal hostnames
        if packet.haslayer('DNS'):
            # Extract hostname queries
            pass
        
        # DHCP reveals comprehensive device info
        if packet.haslayer('DHCP'):
            # Extract device info from DHCP requests
            pass
    
    def _register_device(self, ip: str, mac: str, source: str):
        """Add discovered device to tracking"""
        if ip not in self.discovered_devices:
            self.discovered_devices[ip] = {
                'ip': ip,
                'mac': mac,
                'discovery_method': f'passive_{source}',
                'client_id': self.client_id,
                'first_seen': datetime.utcnow().isoformat(),
                'last_seen': datetime.utcnow().isoformat(),
                'enrollment_status': 'discovered'
            }
```

#### 3. Switch/Router API Discovery

**Best for:** Enterprise networks with managed switches

```python
# discovery/network_api.py
import asyncio
import asyncssh

class NetworkDeviceAPI:
    async def discover_from_switch(self, switch_ip: str, credentials: Dict):
        """
        Query switch ARP table and MAC address table
        More reliable than scanning, gets authoritative data
        """
        async with asyncssh.connect(
            switch_ip,
            username=credentials['username'],
            password=credentials['password'],
            known_hosts=None
        ) as conn:
            # Cisco IOS example
            result = await conn.run('show ip arp')
            arp_table = self._parse_arp_table(result.stdout)
            
            # Get MAC address table
            result = await conn.run('show mac address-table')
            mac_table = self._parse_mac_table(result.stdout)
            
            # Combine for complete device list
            devices = self._merge_tables(arp_table, mac_table)
            return devices
```

### Automated Enrollment Pipeline

```python
# discovery/enrollment_pipeline.py
from typing import List, Dict
import asyncio

class AutoEnrollment:
    def __init__(self, mcp_server_url: str, terraform_path: str):
        self.mcp_url = mcp_server_url
        self.terraform_path = terraform_path
    
    async def process_discovered_devices(self, devices: List[Dict]):
        """
        Main enrollment pipeline:
        1. Classify devices by tier
        2. Determine monitoring strategy
        3. Deploy agents or configure agentless monitoring
        4. Register with MCP server
        5. Add to compliance baseline
        """
        for device in devices:
            # Skip devices we're already monitoring
            if device.get('monitored', False):
                continue
            
            # Determine if device should be monitored
            if not self._should_monitor(device):
                await self._mark_excluded(device, reason='out_of_scope')
                continue
            
            # Classify and enroll based on tier
            tier = device.get('tier', 1)
            device_type = device.get('device_type', 'unknown')
            
            if device_type in ['linux_server', 'windows_server']:
                await self._enroll_agent_based(device)
            elif device_type == 'network_infrastructure':
                await self._enroll_snmp_monitoring(device)
            elif device_type in ['windows_workstation', 'macos_workstation']:
                # Workstations typically excluded from infra-only scope
                await self._mark_excluded(device, reason='endpoint_device')
            else:
                await self._enroll_agentless(device)
    
    async def _enroll_agent_based(self, device: Dict):
        """
        Deploy full monitoring agent for servers
        Uses cloud-init or SSH to bootstrap
        """
        enrollment_plan = {
            'device_id': f"{device['client_id']}-{device['ip']}",
            'client_id': device['client_id'],
            'hostname': device.get('hostname', device['ip']),
            'ip': device['ip'],
            'device_type': device['device_type'],
            'tier': device['tier'],
            'monitoring_method': 'agent',
            'agent_type': 'full_watcher'
        }
        
        # Generate Terraform configuration
        await self._generate_terraform_config(enrollment_plan)
        
        # If SSH is available, bootstrap immediately
        if self._has_ssh_access(device):
            await self._bootstrap_agent(device)
        else:
            # Queue for manual intervention
            await self._queue_manual_enrollment(device, 
                reason='ssh_access_required')
    
    async def _enroll_snmp_monitoring(self, device: Dict):
        """
        Configure agentless SNMP monitoring for network gear
        """
        monitoring_config = {
            'device_id': f"{device['client_id']}-{device['ip']}",
            'client_id': device['client_id'],
            'ip': device['ip'],
            'hostname': device.get('hostname'),
            'monitoring_method': 'snmp',
            'snmp_version': '2c',  # Or v3 if available
            'snmp_community': None,  # Fetch from vault
            'poll_interval': 300,  # 5 minutes
            'metrics': [
                'sysUpTime',
                'ifInOctets',
                'ifOutOctets',
                'ifInErrors',
                'ifOutErrors'
            ]
        }
        
        # Add to monitoring system
        await self._register_with_mcp(monitoring_config)
        
        # Add to Prometheus/Telegraph config
        await self._add_to_monitoring_config(monitoring_config)
    
    async def _enroll_agentless(self, device: Dict):
        """
        Configure agentless monitoring (syslog, SNMP traps, NetFlow)
        """
        monitoring_config = {
            'device_id': f"{device['client_id']}-{device['ip']}",
            'client_id': device['client_id'],
            'ip': device['ip'],
            'monitoring_method': 'agentless',
            'methods': []
        }
        
        # Check what's available
        if self._supports_syslog(device):
            monitoring_config['methods'].append('syslog')
            await self._configure_syslog_forwarding(device)
        
        if self._supports_snmp(device):
            monitoring_config['methods'].append('snmp')
            await self._configure_snmp_polling(device)
        
        if self._supports_netflow(device):
            monitoring_config['methods'].append('netflow')
            await self._configure_netflow_export(device)
        
        await self._register_with_mcp(monitoring_config)
    
    def _should_monitor(self, device: Dict) -> bool:
        """
        Determine if device is in scope for compliance monitoring
        """
        device_type = device.get('device_type', 'unknown')
        
        # Infra-only scope - exclude endpoints
        excluded_types = [
            'windows_workstation',
            'macos_workstation',
            'printer',
            'unknown'
        ]
        
        if device_type in excluded_types:
            return False
        
        # Include all servers and network infrastructure
        included_types = [
            'linux_server',
            'windows_server',
            'network_infrastructure',
            'firewall',
            'vpn_gateway',
            'database_server',
            'application_server'
        ]
        
        return device_type in included_types
    
    async def _bootstrap_agent(self, device: Dict):
        """
        SSH into device and install monitoring agent
        Uses NixOS flake for Linux, PowerShell for Windows
        """
        if device['device_type'].startswith('linux'):
            await self._bootstrap_linux_agent(device)
        elif device['device_type'].startswith('windows'):
            await self._bootstrap_windows_agent(device)
    
    async def _bootstrap_linux_agent(self, device: Dict):
        """
        Install NixOS monitoring agent via SSH
        """
        bootstrap_script = f"""
        # Download and install Nix (if not present)
        curl -L https://nixos.org/nix/install | sh
        
        # Source Nix
        . ~/.nix-profile/etc/profile.d/nix.sh
        
        # Install monitoring agent from your flake
        nix profile install github:yourorg/msp-platform#watcher
        
        # Configure agent
        cat > /etc/msp-watcher.conf <<EOF
        client_id: {device['client_id']}
        device_id: {device['ip']}
        mcp_server: {self.mcp_url}
        api_key: {{vault:msp/clients/{device['client_id']}/api_key}}
        EOF
        
        # Enable and start service
        systemctl enable msp-watcher
        systemctl start msp-watcher
        """
        
        async with asyncssh.connect(
            device['ip'],
            known_hosts=None
        ) as conn:
            result = await conn.run(bootstrap_script)
            if result.exit_status == 0:
                await self._mark_enrolled(device, 'agent_installed')
            else:
                await self._queue_manual_enrollment(
                    device,
                    reason=f'bootstrap_failed: {result.stderr}'
                )
    
    async def _generate_terraform_config(self, plan: Dict):
        """
        Generate Terraform configuration for device
        """
        config = f"""
        resource "msp_monitored_device" "{plan['device_id']}" {{
          client_id    = "{plan['client_id']}"
          hostname     = "{plan['hostname']}"
          ip_address   = "{plan['ip']}"
          device_type  = "{plan['device_type']}"
          tier         = {plan['tier']}
          monitoring   = "{plan['monitoring_method']}"
          
          tags = {{
            auto_enrolled = "true"
            discovery_date = "{datetime.utcnow().isoformat()}"
          }}
        }}
        """
        
        # Write to Terraform workspace
        tf_file = f"{self.terraform_path}/clients/{plan['client_id']}/devices.tf"
        # Append to file...
    
    async def _register_with_mcp(self, config: Dict):
        """Register device with MCP server"""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.mcp_url}/api/devices/register",
                json=config
            ) as resp:
                return await resp.json()
```

### NixOS Integration

```nix
# discovery/flake.nix
{
  description = "MSP Network Discovery & Auto-Enrollment";
  
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.05";
  };
  
  outputs = { self, nixpkgs }: {
    packages.x86_64-linux.discovery-service = nixpkgs.legacyPackages.x86_64-linux.python3Packages.buildPythonApplication {
      pname = "msp-discovery";
      version = "0.1.0";
      
      propagatedBuildInputs = with nixpkgs.legacyPackages.x86_64-linux.python3Packages; [
        nmap
        scapy
        pysnmp
        zeroconf
        asyncssh
        aiohttp
      ];
      
      src = ./.;
    };
    
    nixosModules.discovery-service = { config, lib, pkgs, ... }: {
      options.services.msp-discovery = {
        enable = lib.mkEnableOption "MSP Network Discovery Service";
        
        subnets = lib.mkOption {
          type = lib.types.listOf lib.types.str;
          description = "Subnets to scan for devices";
          example = ["192.168.1.0/24"];
        };
        
        scanInterval = lib.mkOption {
          type = lib.types.int;
          default = 3600;  # 1 hour
          description = "Seconds between discovery scans";
        };
        
        clientId = lib.mkOption {
          type = lib.types.str;
          description = "Client identifier";
        };
      };
      
      config = lib.mkIf config.services.msp-discovery.enable {
        systemd.services.msp-discovery = {
          description = "MSP Network Discovery Service";
          wantedBy = [ "multi-user.target" ];
          after = [ "network.target" ];
          
          serviceConfig = {
            ExecStart = "${self.packages.x86_64-linux.discovery-service}/bin/msp-discovery";
            Restart = "always";
            RestartSec = "10s";
          };
          
          environment = {
            MSP_CLIENT_ID = config.services.msp-discovery.clientId;
            MSP_SUBNETS = lib.concatStringsSep "," config.services.msp-discovery.subnets;
            MSP_SCAN_INTERVAL = toString config.services.msp-discovery.scanInterval;
          };
        };
      };
    };
  };
}
```

### HIPAA Considerations for Discovery

**Critical Security Requirements:**

1. **No PHI Exposure During Discovery**
   - Discovery scans system/network layer only
   - Never scan application data directories
   - Block access to EHR/database ports during fingerprinting

2. **Minimal Footprint**
   - Use stealth scanning options where possible
   - Rate-limit scans to avoid DoS
   - Schedule during maintenance windows

3. **Audit Trail**
   - Log every discovery scan with timestamp
   - Record which devices were discovered/enrolled
   - Track enrollment decisions and exclusions

4. **Access Control**
   - Discovery service runs with least-privilege
   - Credentials stored in Vault
   - No persistent SSH keys

5. **Device Classification Privacy**
   - Don't classify devices based on PHI-revealing patterns
   - Use network/service fingerprints only
   - Avoid collecting device hostnames that might reveal patient names

### Deployment Configuration

```yaml
# discovery/config.yaml
discovery:
  client_id: clinic-001
  
  subnets:
    - 192.168.1.0/24    # Main office network
    - 192.168.10.0/24   # Server VLAN
    - 10.0.1.0/24       # Medical devices VLAN (scan with caution)
  
  scan_schedule:
    full_scan: "0 2 * * 0"  # Sunday 2 AM
    quick_scan: "0 */4 * * *"  # Every 4 hours
  
  methods:
    - active_nmap
    - passive_arp
    - snmp_walk
    - mdns_browse
    - switch_api  # If available
  
  enrollment:
    auto_enroll_tiers: [1, 2]  # Auto-enroll Tier 1 & 2 only
    manual_approval_tier: 3    # Tier 3 needs manual approval
    
    excluded_types:
      - windows_workstation
      - macos_workstation
