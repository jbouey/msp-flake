# Auto-Discovery + Auto-Deploy Agent System

**Date:** 2026-03-22
**Status:** Draft
**Session:** 183 (brainstorm) → 184 (implementation)

## Problem

Clinic networks are messy. Random computers, personal devices, printers, IoT devices — all on the same subnet as HIPAA-regulated workstations. The current workflow requires an admin to manually create fleet orders to deploy Go agents one-by-one. Devices not in Active Directory are invisible until someone manually adds them.

**Goal:** The appliance daemon continuously discovers every device on the subnet, auto-deploys Go agents to AD-joined machines, and presents non-AD devices in the UI for one-click "Take Over" with credential entry. No manual fleet orders. Full network visibility for HIPAA audits.

## Architecture Overview

```
Subnet 192.168.88.0/24
┌──────────────────────────────────────────────────────────┐
│  ARP Watcher (passive, real-time)                        │
│  + Active Sweep (every 3 min, probes SSH/WinRM/SNMP)     │
│  + AD Enumeration (existing, domain-joined machines)      │
└──────────────────────┬───────────────────────────────────┘
                       │ discovered_devices[] in checkin
                       ▼
┌──────────────────────────────────────────────────────────┐
│  Central Command                                         │
│  - Upserts discovered_devices with probe results         │
│  - Marks AD-joined devices for auto-deploy               │
│  - Returns pending_deploys[] in checkin response         │
└──────────────────────┬───────────────────────────────────┘
                       │
           ┌───────────┼───────────┐
           ▼           ▼           ▼
    AD-joined Win   "Take Over"   Ignored/Tagged
    (auto-deploy)   (admin creds)  (inventory only)
           │           │
           ▼           ▼
    Go Agent deployed + pushing checks to Central Command
```

## Lab Environment

| Host | IP | Platform | Current State |
|------|-----|----------|---------------|
| NVDC01 | .250 | Windows Server 2019 DC | Go Agent deployed |
| NVWS01 | .251 | Windows 10 | Go Agent deployed |
| iMac | .50 | macOS 11.7 | Go Agent deployed |
| northvalley-linux | .239/.240 | Ubuntu x64 | No agent — needs "Take Over" |
| NixOS appliance | .241 | NixOS | Runs daemon (not an agent target) |

---

## Section 1: Continuous Discovery Engine

### Current State

- ARP scan runs every 15 minutes, reads `/proc/net/arp` passively
- AD enumeration via PowerShell over WinRM on domain controller
- OUI lookup for manufacturer hints (500+ prefixes)
- No active probing, no OS fingerprinting, no real-time detection

### New Design

Two-layer discovery loop in the Go daemon (`appliance/internal/daemon/`):

**Layer 1 — ARP Watcher (passive, real-time):**
- Persistent goroutine monitors ARP broadcasts on the LAN
- Catches devices the instant they join the network
- Feeds into the same `discoveredDevice` struct as the existing ARP scan
- Minimal CPU/network overhead — purely passive listener

**Layer 2 — Active Sweep (every 3 minutes):**
- Probes the entire /24 subnet (or configured range)
- For each live IP, runs OS fingerprinting probes:

| Probe | Port | What It Reveals |
|-------|------|-----------------|
| SSH banner grab | 22 | Linux distro + version, or macOS ("Apple") |
| WinRM check | 5985 | Windows (version from HTTP response headers) |
| SNMP query | 161 | Network gear, managed printers, UPS |
| HTTP header | 80/443 | Embedded web UIs (printers, IoT, medical) |

- Combines with existing OUI lookup (MAC → manufacturer → device class hint)
- Skips the appliance's own IP and known infrastructure (gateway, DNS)

**OS Classification Logic:**
```
SSH banner contains "Ubuntu"         → os_type: "linux", distro: "ubuntu"
SSH banner contains "Debian"         → os_type: "linux", distro: "debian"
SSH banner contains "Apple"          → os_type: "macos"
WinRM responds                       → os_type: "windows"
SNMP sysDescr matches printer OIDs   → os_type: "network", device_tag: "printer"
HTTP response from known medical UIs → os_type: "medical"
None of the above                    → os_type: "unknown"
```

**Reported to Central Command in each checkin:**
```json
{
  "discovered_devices": [
    {
      "ip_address": "192.168.88.239",
      "mac_address": "08:00:27:xx:xx:xx",
      "hostname": "northvalley-linux",
      "os_fingerprint": "OpenSSH_8.9p1 Ubuntu-3ubuntu0.6",
      "os_type": "linux",
      "distro": "ubuntu",
      "probe_ssh": true,
      "probe_winrm": false,
      "probe_snmp": false,
      "ad_joined": false,
      "has_agent": false,
      "first_seen": "2026-03-22T12:00:00Z",
      "last_seen": "2026-03-22T17:30:00Z"
    }
  ]
}
```

### Files Changed

| File | Change |
|------|--------|
| `appliance/internal/daemon/netscan.go` | Add active sweep + ARP watcher goroutine |
| `appliance/internal/daemon/probes.go` | New file: SSH/WinRM/SNMP/HTTP probe functions |
| `appliance/internal/daemon/classify.go` | New file: OS classification from probe results |
| `mcp-server/app/dashboard_api/device_sync.py` | Accept new probe fields in sync payload |

---

## Section 2: Auto-Deploy Pipeline

Three deployment paths based on device classification:

### Path 1 — AD-Joined Windows (Automatic, Zero-Click)

```
AD enumeration discovers machine
  → Cross-reference with go_agents table
  → No active agent? Mark deploy_status = "pending"
  → Daemon uses existing domain/service account creds (from checkin windows_targets)
  → Runs configure_workstation_agent logic directly (no fleet order)
  → Downloads binary from https://api.osiriscare.net/updates/
  → Deploys via WinRM (NETLOGON UNC fallback if HTTP blocked)
  → Agent starts, pushes checks within 60 seconds
  → deploy_status = "success"
```

No admin intervention needed. Domain credentials already exist in `site_credentials`.

### Path 2 — Manual "Take Over" (Linux/Mac/Standalone Windows)

```
Device appears in UI as "Discovered — Unmanaged"
  → Admin clicks "Take Over" button on device row
  → Modal pre-fills hostname, IP, MAC, detected OS
  → Admin enters: username + password/SSH key
  → Backend saves credentials + sets deploy_requested = true
  → Next checkin (within 60s): daemon receives pending_deploys[]
  → Daemon connects via SSH (Linux/Mac) or WinRM (standalone Windows)
  → Uploads agent binary, installs service:
      - Linux: systemd unit (osiriscare-agent.service)
      - macOS: launchd plist (com.osiriscare.agent.plist)
      - Windows: Windows service via sc.exe
  → Agent starts, pushes checks within 60 seconds
  → deploy_status = "success"
```

### Path 3 — Ignore/Tag (Inventory Only)

```
Admin sees unknown device (printer, IoT, personal laptop)
  → Clicks "Ignore" or selects tag: printer, iot, personal, medical
  → Device stays in inventory (HIPAA network audit value)
  → No agent deploy attempted
  → Can be un-ignored later
```

### Deployment Status Tracking

New fields on `discovered_devices`:
- `agent_deploy_status`: `none` → `pending` → `deploying` → `success` | `failed`
- `agent_deploy_error`: failure reason text
- `agent_deploy_attempted_at`: timestamp of last attempt

Daemon reports deploy results in next checkin. Frontend polls device list to show live status.

### Files Changed

| File | Change |
|------|--------|
| `appliance/internal/daemon/autodeploy.go` | New file: auto-deploy orchestrator |
| `appliance/internal/daemon/deploy_ssh.go` | New file: SSH-based Linux/macOS deployment |
| `appliance/internal/daemon/deploy_winrm.go` | Extract from orders.go, reuse for auto-deploy |
| `mcp-server/app/main.py` | Add `pending_deploys` to checkin response |
| `mcp-server/app/dashboard_api/sites.py` | "Take Over" endpoint saves creds + sets pending |

---

## Section 3: UI — Discovered Devices + Take Over Flow

### SiteDevices.tsx Changes

**New coverage tier column:**

| Status | Visual | Meaning |
|--------|--------|---------|
| Agent Active | Green dot + "Agent" | Go agent pushing checks |
| Deploying... | Spinner + "Deploying" | Deploy in progress |
| Deploy Failed | Red dot + "Failed" + retry | Error on hover |
| AD Managed | Blue dot + "AD — auto-deploy" | Queued for auto-deploy |
| Discovered | Yellow dot + "Take Over" button | Needs credentials |
| Ignored | Gray dot + "Ignored" | Admin dismissed |

**"Take Over" modal (extends AddDeviceModal):**
- Pre-filled: hostname, IP, MAC, detected OS (from probe fingerprint)
- Admin enters: username + password or SSH key
- OS type auto-selected from fingerprint (editable if wrong)
- "Deploy Agent" button saves creds AND triggers deploy
- Modal stays open showing progress: Connecting → Uploading → Installing → Verifying → Done

**Network Inventory filter:**
- Toggle: "Managed" (has agent or AD) vs "All Devices" (includes printers, unknown, ignored)
- Bulk actions: "Ignore selected", "Tag as printer/IoT"
- HIPAA alert: "X unmanaged devices on your network" for compliance packets

**Auto-deploy notifications:**
- Toast: "Agent deployed to NVDC01 (auto — AD credentials)"
- Deploy history in site audit log

### Files Changed

| File | Change |
|------|--------|
| `frontend/src/pages/SiteDevices.tsx` | Coverage tier column, filter toggle, bulk actions |
| `frontend/src/components/shared/AddDeviceModal.tsx` | Pre-fill from probe data, deploy progress |
| `frontend/src/components/shared/DeployProgress.tsx` | New: step-by-step deploy status display |

---

## Section 4: Data Model Changes

### Migration (new)

```sql
-- Extend discovered_devices for probing + auto-deploy
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS os_fingerprint TEXT;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS os_type TEXT;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS distro TEXT;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS probe_ssh BOOLEAN DEFAULT FALSE;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS probe_winrm BOOLEAN DEFAULT FALSE;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS probe_snmp BOOLEAN DEFAULT FALSE;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS ad_joined BOOLEAN DEFAULT FALSE;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS agent_deploy_status TEXT DEFAULT 'none';
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS agent_deploy_error TEXT;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS agent_deploy_attempted_at TIMESTAMPTZ;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS deploy_attempts INTEGER DEFAULT 0;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS device_tag TEXT;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS last_probe_at TIMESTAMPTZ;

-- Index for finding devices needing deployment
CREATE INDEX IF NOT EXISTS idx_discovered_devices_deploy_status
ON discovered_devices (agent_deploy_status) WHERE agent_deploy_status IN ('pending', 'deploying');

-- Index for finding unmanaged devices
CREATE INDEX IF NOT EXISTS idx_discovered_devices_unmanaged
ON discovered_devices (site_id, device_tag) WHERE device_tag IS NULL AND agent_deploy_status = 'none';
```

### Checkin Response — New `pending_deploys` Array

```json
{
  "pending_deploys": [
    {
      "device_id": "manual-192.168.88.239",
      "ip_address": "192.168.88.239",
      "hostname": "northvalley-linux",
      "os_type": "linux",
      "username": "admin",
      "password": "...",
      "ssh_key": "...",
      "deploy_method": "ssh",
      "agent_binary_url": "https://api.osiriscare.net/updates/osiris-agent-linux-amd64"
    }
  ]
}
```

### Checkin Request — Deploy Status Reporting

```json
{
  "deploy_results": [
    {
      "device_id": "manual-192.168.88.239",
      "status": "success",
      "agent_id": "go-northvalley-linux-a1b2c3d4",
      "error": null
    }
  ]
}
```

No changes to `go_agents` table — once deployed, agents register themselves via existing heartbeat flow.

---

## Section 5: Advanced Enhancements

### 5A — Rogue Device Alerting (Phase 1)

New device on subnet that wasn't previously seen triggers an automatic incident.

**Severity classification:**
- Unknown device with open ports → `high` (potential attack vector on healthcare network)
- New device matching consumer OUI (iPhone, Ring, etc.) → `medium` (personal device)
- Device with MAC address changes → `critical` (possible MAC spoofing)

**Implementation:**
- New incident type: `NETWORK-ROGUE-DEVICE`
- L1 rule auto-creates incident on first sighting of unknown device
- Admin reviews: take over, tag, or ignore
- Feeds into compliance packets: "Network perimeter integrity — X rogue devices detected this period"

**Files:** `appliance/internal/daemon/autodeploy.go` (trigger), `mcp-server/app/main.py` (L1 rule)

### 5B — Agent Self-Healing (Phase 2)

Daemon monitors agent heartbeats via `go_agents.last_heartbeat`.

**Escalation ladder:**
1. Agent silent for 10 minutes → daemon probes via SSH/WinRM health check
2. Agent process dead → auto-redeploy using stored credentials
3. Agent binary corrupted or old version → auto-update via deploy pipeline
4. `deploy_attempts` counter tracks retries — after 3 failed redeploys, escalate to L3 human ticket

**No infinite loops.** Three strikes and it becomes a human problem.

**Files:** `appliance/internal/daemon/autodeploy.go` (health check loop), Go daemon checkin processing

### 5C — Staggered Deployment (Phase 2)

Clinic networks often run on slow DSL/cable links. Blasting 10 deploys simultaneously will saturate the uplink.

**Rules:**
- Deploy in batches of 3 (configurable via site-level `deploy_concurrency`)
- 30-second gap between batches
- Priority order: servers → workstations → everything else
- Binary download cached on appliance — uploaded to each target from local cache, not re-downloaded per host

**Files:** `appliance/internal/daemon/autodeploy.go` (batch scheduler)

### 5D — Pre-Flight Checks (Phase 2)

Before deploying, daemon validates the target is ready:

| Check | Threshold | Deploy Blocked? |
|-------|-----------|-----------------|
| Disk space | < 50MB free | Yes |
| OS version | Below Go 1.22 minimum (macOS 10.15, Win 10, Ubuntu 18.04) | Yes |
| Existing security software (CrowdStrike, SentinelOne) | Detected | Warning, not blocked |
| Existing RMM agent (ConnectWise, Datto, Ninja) | Detected | Warning, not blocked |
| Target reachable | SSH/WinRM connect fails | Yes, retry later |

Pre-flight failures show in UI: "Deploy blocked: 95% disk full" or "Requires macOS 10.15+, found 10.14"

**Files:** `appliance/internal/daemon/preflight.go` (new file)

### 5E — Credential Encryption at Rest (Phase 3)

**Current:** credentials stored as JSON bytes in `site_credentials.encrypted_data` (not actually encrypted).

**Enhancement:**
- Encrypt with age key before storage (reuse existing SOPS/age infrastructure)
- Decrypt only at checkin delivery time (server-side)
- Daemon receives decrypted creds in checkin response (TLS in transit), uses them, never persists to disk
- Credential age tracking: if SSH password creds are >90 days old, flag in UI as "stale credentials"

**Files:** `mcp-server/app/main.py` (encrypt on save, decrypt on deliver), migration for key storage

### 5F — Network Topology Awareness (Phase 3)

**Detection:**
- Daemon detects multiple subnets if appliance has multiple interfaces
- ARP reveals devices on different /24 ranges → group by subnet
- UI groups discovered devices by subnet

**Alerting:**
- Device on unexpected subnet flagged (workstation on server VLAN)
- Future: VLAN-aware scanning with 802.1Q tagging support

**Files:** `appliance/internal/daemon/netscan.go` (multi-subnet), frontend grouping

### 5G — Device Lifecycle State Machine (Phase 1)

Clean state transitions with timestamps:

```
discovered → probed → [ad_managed | take_over_available | ignored]
                              ↓              ↓
                        auto_deploying   pending_deploy
                              ↓              ↓
                         agent_active    agent_active
                              ↓              ↓
                         [agent_stale → auto_redeploy → agent_active]
                              ↓
                         agent_offline → incident_created
```

**Lifecycle rules:**
- Device not seen in 7 days → status `offline`, incident created
- Device not seen in 30 days → auto-archived (visible in history, removed from active list)
- Device reappears → status restored, incident auto-resolved

**Files:** `appliance/internal/daemon/lifecycle.go` (new), `device_sync.py` (archive logic)

### 5H — Compliance Coverage Score (Phase 3)

**Formula:**
```
network_coverage_percentage = (devices with active agents) / (total non-ignored devices) x 100
```

**Display:**
- Prominent gauge on site dashboard
- Feeds into compliance packets: "87% of network devices under active compliance monitoring"
- Target: 100% = all devices either have agent or are explicitly tagged/ignored
- Unmanaged untagged devices drag the score down — incentivizes admin action

**Files:** `_routes_impl.py` (calculation), `SiteDetail.tsx` (gauge), compliance packet generation

### 5I — Remote Uninstall (Phase 3)

**New fleet order type: `remove_agent`**

Daemon connects via SSH/WinRM to target:
1. Stop agent service (systemctl stop / launchctl unload / sc stop)
2. Remove service definition
3. Delete binary and data directory
4. Report removal to Central Command
5. `go_agents` record marked `removed`

**Use cases:**
- Device being decommissioned
- Agent causing issues on target
- Transferring device between sites (uninstall → re-deploy with new site_id)

**Files:** `appliance/internal/daemon/autodeploy.go` (uninstall logic), fleet CLI addition

---

## Implementation Phases

### Phase 1 — Core Auto-Discover + Auto-Deploy
Sections 1-4 + 5A (rogue alerting) + 5G (lifecycle state machine)

**Deliverables:**
- Active subnet sweep with OS fingerprinting (3-min interval)
- ARP watcher for real-time detection
- Auto-deploy to AD-joined Windows devices (zero-click)
- "Take Over" UI flow for Linux/Mac/standalone Windows
- Rogue device incident generation
- Device lifecycle state machine with archive
- DB migration for new discovered_devices columns
- Deploy northvalley-linux Ubuntu VM as first Linux agent

### Phase 2 — Reliability
5B (agent self-healing) + 5C (staggered deployment) + 5D (pre-flight checks)

**Deliverables:**
- Agent health monitoring with auto-redeploy (3-strike limit)
- Batched deployment with configurable concurrency
- Pre-flight validation (disk, OS version, existing software)

### Phase 3 — Polish
5E (credential encryption) + 5F (topology) + 5H (coverage score) + 5I (uninstall)

**Deliverables:**
- Age-encrypted credential storage
- Multi-subnet awareness and UI grouping
- Network coverage percentage metric
- Remote agent uninstall capability

---

## Success Criteria

1. Daemon discovers all 5 lab devices within 3 minutes of boot
2. AD-joined NVDC01 and NVWS01 auto-deploy agents without admin action
3. northvalley-linux appears in UI as "Discovered — Ubuntu" with "Take Over" button
4. Admin enters SSH creds for northvalley-linux → agent deployed within 60 seconds
5. Random new device plugged into network → rogue device incident within 3 minutes
6. Agent process killed on any host → auto-redeployed within 10 minutes
7. Network coverage score shows 100% when all devices managed or tagged
