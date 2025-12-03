# Network Topology & VM Inventory

**Last Updated:** 2025-12-03
**Environment:** Development/Test Lab

---

## Network Diagram

```
                         ┌─────────────────┐
                         │    INTERNET     │
                         └────────┬────────┘
                                  │
                         ┌────────▼────────┐
                         │  Mac Host       │
                         │ 174.178.63.139  │
                         │ (Remote Access) │
                         └────────┬────────┘
                                  │
            ┌─────────────────────┼─────────────────────┐
            │                     │                     │
   ┌────────▼────────┐   ┌───────▼────────┐   ┌───────▼────────┐
   │  NAT Network    │   │ Host-Only Net  │   │  Port Forwards │
   │  10.0.3.0/24    │   │ 192.168.56.0/24│   │  (SSH/WinRM)   │
   │  (Internet)     │   │ (VM-to-VM)     │   │                │
   └────────┬────────┘   └───────┬────────┘   └────────────────┘
            │                    │
   ┌────────▼────────┐   ┌───────▼────────┐
   │  MCP Server     │   │  Compliance    │
   │  10.0.3.4       │   │  Appliance     │
   │  NixOS          │   │  192.168.56.103│
   │  Port 8000      │   │  NixOS         │
   └─────────────────┘   │  Web UI: 8080  │
                         └───────┬────────┘
                                 │ WinRM (5985)
                         ┌───────▼────────┐
                         │  Windows DC    │
                         │  192.168.56.102│
                         │  "wintest"     │
                         │  msp.local     │
                         └────────────────┘
```

---

## VM Inventory

### Compliance Appliance (Primary Work Target)

| Property | Value |
|----------|-------|
| **Role** | Compliance monitoring agent |
| **OS** | NixOS 24.05 |
| **Host-Only IP** | 192.168.56.103 |
| **NAT IP** | 10.0.3.5 (when connected) |
| **SSH Port** | 22 (internal), 4444 (forwarded to Mac) |
| **Web UI** | http://192.168.56.103:8080 |
| **Username** | root |
| **Auth** | SSH key (Mac's ~/.ssh/id_ed25519) |

**Access from Mac:**
```bash
ssh -p 4444 root@174.178.63.139
```

**Access Web UI:**
```bash
ssh -f -N -L 9080:192.168.56.103:8080 jrelly@174.178.63.139
open http://localhost:9080
```

---

### MCP Server

| Property | Value |
|----------|-------|
| **Role** | Central control plane |
| **OS** | NixOS 24.05 |
| **NAT IP** | 10.0.3.4 |
| **SSH Port** | 22 (internal), 4445 (forwarded to Mac) |
| **API Port** | 8000 |
| **Username** | root |
| **Auth** | SSH key |

**Access from Mac:**
```bash
ssh -p 4445 root@174.178.63.139
```

---

### Windows Domain Controller

| Property | Value |
|----------|-------|
| **Role** | Test Windows Server + AD |
| **OS** | Windows Server 2016 |
| **Hostname** | wintest |
| **Domain** | msp.local |
| **Host-Only IP** | 192.168.56.102 |
| **WinRM Port** | 5985 (internal), 55985 (forwarded to Mac) |

**Credentials:**
| Account | Username | Password |
|---------|----------|----------|
| Domain Admin | MSP\vagrant | vagrant |
| Local Admin | .\Administrator | Vagrant123! |

**Access from Mac (WinRM):**
```python
import winrm
s = winrm.Session('http://127.0.0.1:55985/wsman', 
                  auth=('MSP\\vagrant','vagrant'), 
                  transport='ntlm')
print(s.run_ps('whoami').std_out.decode())
```

**Known Issue:** Windows Firewall blocks WinRM from other VMs (192.168.56.0/24). Works from Mac host (192.168.56.1).

**Fix (run on Windows):**
```powershell
New-NetFirewallRule -Name "WinRM_HostOnly" `
  -DisplayName "WinRM from Host-Only Network" `
  -Enabled True -Direction Inbound -Protocol TCP `
  -LocalPort 5985 -RemoteAddress 192.168.56.0/24 -Action Allow
```

---

## Port Forwarding Summary

| Service | Guest Port | Mac Port | Target VM |
|---------|------------|----------|-----------|
| SSH (Appliance) | 22 | 4444 | 192.168.56.103 |
| SSH (MCP) | 22 | 4445 | 10.0.3.4 |
| WinRM | 5985 | 55985 | 192.168.56.102 |
| Web UI | 8080 | 9080 (tunnel) | 192.168.56.103 |

---

## Network Segments

### NAT Network (msp-network)
- **Subnet:** 10.0.3.0/24
- **Gateway:** 10.0.3.1
- **Purpose:** Internet access for VMs
- **Connected:** MCP Server, Appliance (secondary NIC)

### Host-Only Network (vboxnet0)
- **Subnet:** 192.168.56.0/24
- **Host IP:** 192.168.56.1
- **Purpose:** VM-to-VM and host-to-VM communication
- **Connected:** Appliance, Windows DC

---

## Connectivity Matrix

| From → To | Appliance | MCP Server | Windows DC | Mac Host |
|-----------|-----------|------------|------------|----------|
| **Appliance** | - | ✅ (10.0.3.x) | ⚠️ (needs FW fix) | ✅ |
| **MCP Server** | ✅ | - | ❌ (no route) | ✅ |
| **Windows DC** | ⚠️ | ❌ | - | ✅ |
| **Mac Host** | ✅ | ✅ | ✅ | - |

⚠️ = Requires Windows Firewall rule

---

## Quick Diagnostics

**Test Appliance connectivity:**
```bash
# From Mac
ssh -p 4444 root@174.178.63.139 'ping -c 1 192.168.56.102'
```

**Test WinRM from Mac:**
```bash
curl -u 'MSP\vagrant:vagrant' --ntlm \
  http://127.0.0.1:55985/wsman -H "Content-Type: application/soap+xml"
```

**Check VM status (on Mac host):**
```bash
ssh jrelly@174.178.63.139 'VBoxManage list runningvms'
```

---

## Troubleshooting

### "Connection timed out" to Windows from Appliance
1. Windows Firewall blocking - add rule above
2. Wrong network adapter - ensure both VMs on vboxnet0
3. Windows VM not running - check VirtualBox

### Web UI not loading
1. Ensure tunnel is active: `ssh -f -N -L 9080:192.168.56.103:8080 jrelly@174.178.63.139`
2. Check service: `ssh -p 4444 root@174.178.63.139 'systemctl status msp-web-ui'`
3. Don't use localhost:8080 on Mac (Jenkins runs there)

### MCP Server unreachable from Appliance
1. Check NAT network connectivity
2. Verify MCP service: `ssh -p 4445 root@174.178.63.139 'systemctl status msp-server'`
