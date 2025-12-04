# Network Discovery & Automated Enrollment

## Overview

For efficient client onboarding and continuous compliance monitoring, the system automatically discovers, classifies, and enrolls devices on the healthcare network.

## Discovery Methods

### 1. Active Discovery (Scanning)

Multi-method approach for comprehensive asset inventory:

```python
class NetworkDiscovery:
    def __init__(self, subnet: str, client_id: str):
        self.subnet = subnet
        self.client_id = client_id
        self.nm = nmap.PortScanner()

    async def discover_devices(self) -> List[Dict]:
        devices = []

        # Method 1: Fast ping sweep
        live_hosts = await self._ping_sweep()

        # Method 2: Service fingerprinting
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
```

### 2. Passive Discovery (Network Flow Monitoring)

No active scanning - discovers devices from ARP, DNS, DHCP traffic:

```python
class PassiveDiscovery:
    def start_monitoring(self):
        sniff(
            iface=self.interface,
            prn=self._process_packet,
            store=False,
            filter="arp or port 53 or port 67"
        )

    def _process_packet(self, packet):
        if ARP in packet:
            ip = packet[ARP].psrc
            mac = packet[ARP].hwsrc
            self._register_device(ip, mac, 'arp')
```

### 3. Switch/Router API Discovery

Query switch ARP and MAC tables directly:

```python
class NetworkDeviceAPI:
    async def discover_from_switch(self, switch_ip: str, credentials: Dict):
        async with asyncssh.connect(switch_ip, ...) as conn:
            result = await conn.run('show ip arp')
            arp_table = self._parse_arp_table(result.stdout)

            result = await conn.run('show mac address-table')
            mac_table = self._parse_mac_table(result.stdout)

            return self._merge_tables(arp_table, mac_table)
```

**Advantages over scanning:**
- Stealthier (no probe packets)
- More complete (sees intermittent devices)
- Authoritative (switch knows definitively)
- HIPAA-safer (no probing medical devices)

## Device Classification

```python
def _classify_device(self, device: Dict) -> str:
    services = device.get('services', [])
    os = device.get('os', '').lower()

    # Server: ports 22, 80, 443, 3306, 5432, etc.
    if any(s['port'] in [22, 80, 443, 3306, 5432] for s in services):
        if 'linux' in os:
            return 'linux_server'
        elif 'windows server' in os:
            return 'windows_server'

    # Network: ports 23, 161, 162
    if any(s['port'] in [23, 161, 162] for s in services):
        return 'network_infrastructure'

    # Printer: ports 515, 631, 9100
    if any(s['port'] in [515, 631, 9100] for s in services):
        return 'printer'

    # Medical device: DICOM ports 104, 2761, 2762
    if any(s['port'] in [104, 2761, 2762] for s in services):
        return 'medical_device'

    return 'unknown'
```

## Tier Assignment

| Tier | Device Types | Monitoring |
|------|--------------|------------|
| 1 | Linux/Windows servers, network gear, firewalls | Full agent |
| 2 | Database, app, web servers, workstations | Agent + app module |
| 3 | Medical devices, EHR, PACS | Manual config required |

## Automated Enrollment Pipeline

```python
class AutoEnrollment:
    async def process_discovered_devices(self, devices: List[Dict]):
        for device in devices:
            if device.get('monitored', False):
                continue

            if not self._should_monitor(device):
                await self._mark_excluded(device, reason='out_of_scope')
                continue

            device_type = device.get('device_type', 'unknown')

            if device_type in ['linux_server', 'windows_server']:
                await self._enroll_agent_based(device)
            elif device_type == 'network_infrastructure':
                await self._enroll_snmp_monitoring(device)
            elif device_type in ['windows_workstation', 'macos_workstation']:
                await self._mark_excluded(device, reason='endpoint_device')
            else:
                await self._enroll_agentless(device)
```

## HIPAA Considerations

1. **No PHI Exposure**: Discovery scans system/network layer only
2. **Minimal Footprint**: Stealth scanning, rate-limited
3. **Audit Trail**: Log every scan with timestamp
4. **Access Control**: Least-privilege, credentials in Vault
5. **Device Privacy**: Don't classify based on PHI-revealing patterns

## Configuration

```yaml
discovery:
  client_id: clinic-001

  subnets:
    - 192.168.1.0/24    # Main office
    - 192.168.10.0/24   # Server VLAN
    - 10.0.1.0/24       # Medical devices (scan with caution)

  scan_schedule:
    full_scan: "0 2 * * 0"   # Sunday 2 AM
    quick_scan: "0 */4 * * *" # Every 4 hours

  methods:
    - active_nmap
    - passive_arp
    - snmp_walk
    - mdns_browse
    - switch_api

  enrollment:
    auto_enroll_tiers: [1, 2]
    manual_approval_tier: 3

    excluded_types:
      - windows_workstation
      - macos_workstation
      - printer
      - medical_device

  security:
    stealth_mode: true
    rate_limit_packets_per_sec: 100

  hipaa:
    avoid_phi_bearing_ports: [3306, 5432, 1433, 1521]
    log_all_discoveries: true
```

## Dashboard Report

```markdown
## Automated Device Discovery Report

### Discovery Summary (October 2025)
- Total discovered: 47
- Enrolled in monitoring: 32
- Excluded (out of scope): 12
- Pending manual approval: 3

### Enrolled Breakdown
| Tier | Device Type | Count | Method |
|------|-------------|-------|--------|
| 1 | Linux Server | 8 | Agent |
| 1 | Windows Server | 4 | Agent |
| 1 | Network Infra | 6 | SNMP |
| 1 | Firewall | 2 | Syslog + SNMP |

### Excluded Devices
| Type | Count | Reason |
|------|-------|--------|
| Windows Workstation | 8 | Out of scope |
| Printer | 3 | Not compliance-critical |

### Pending Manual Approval
- 10.0.1.45 - PACS server (Tier 3)
- 10.0.1.62 - Medical modality (Tier 3)
```
