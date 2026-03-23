# Auto-Discovery + Auto-Deploy Agent System

**Date:** 2026-03-22
**Status:** Reviewed (v2 — review fixes applied)
**Session:** 183 (brainstorm) → 184 (implementation)

## Problem

Clinic networks are messy. Random computers, personal devices, printers, IoT devices — all on the same subnet as HIPAA-regulated workstations. The current workflow requires an admin to manually create fleet orders to deploy Go agents one-by-one. Devices not in Active Directory are invisible until someone manually adds them.

**Goal:** The appliance daemon continuously discovers every device on the subnet, auto-deploys Go agents to AD-joined machines, and presents non-AD devices in the UI for one-click "Take Over" with credential entry. No manual fleet orders. Full network visibility for HIPAA audits.

## Architecture Overview

```
Subnet 192.168.88.0/24
┌──────────────────────────────────────────────────────────────┐
│  ARP Cache Poll (passive, /proc/net/arp, every 3 min)        │
│  + Active Sweep (every 10 min, probes SSH/WinRM)             │
│  + AD Enumeration (existing, domain-joined machines)          │
└──────────────────────┬───────────────────────────────────────┘
                       │ POST /api/devices/sync (existing endpoint)
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  Central Command                                             │
│  - Upserts discovered_devices with probe results             │
│  - AD-joined + no agent = daemon auto-deploys autonomously   │
│  - Non-AD "Take Over" = creds saved, pending_deploys in      │
│    checkin response for daemon to execute                     │
└──────────────────────┬───────────────────────────────────────┘
                       │
           ┌───────────┼───────────┐
           ▼           ▼           ▼
    AD-joined Win   "Take Over"   Ignored/Tagged
    (daemon auto)   (admin creds)  (inventory only)
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

- ARP scan runs every 15 minutes, reads `/proc/net/arp` passively (`netscan.go:308-411`)
- AD enumeration via PowerShell over WinRM (`appliance/internal/discovery/ad.go`)
- OUI lookup for manufacturer hints (500+ prefixes in `oui_lookup.py`)
- Device data sent to Central Command via `POST /api/devices/sync` (separate from checkin)
- No active probing, no OS fingerprinting beyond AD data

### New Design

Two-layer discovery loop in the Go daemon (`appliance/internal/daemon/`):

**Layer 1 — Enhanced ARP + DNS (passive, every 3 minutes):**
- Reads `/proc/net/arp` (existing approach — no raw sockets needed, works under `ProtectSystem=strict`)
- Increased frequency from 15 min to 3 min for faster detection
- Reverse DNS lookup on all discovered IPs (existing in `resolveHostnames()`)
- Cross-references with `go_agents` heartbeat data to mark `has_agent`

**Layer 2 — Active Probe Sweep (every 10 minutes, configurable):**
- Probes IPs found in ARP cache (not blind /24 sweep — only hosts known to be alive)
- Configurable subnet range override for environments with static IPs
- For each live IP, runs OS fingerprinting probes:

| Probe | Port | What It Reveals |
|-------|------|-----------------|
| SSH banner grab | 22 | Linux distro + version, or macOS ("Apple") |
| WinRM check | 5985 | Windows (version from HTTP response headers) |
| HTTP header | 80/443 | Embedded web UIs (printers, IoT, medical) |

- SNMP probing (port 161) is **opt-in** via site config — can trigger IDS/IPS alerts on managed firewalls
- Combines with existing OUI lookup (MAC → manufacturer → device class hint)
- Skips the appliance's own IP and known infrastructure (gateway, DNS)

**OS Classification Logic:**
```
SSH banner contains "Ubuntu"         → os_type: "linux", distro: "ubuntu"
SSH banner contains "Debian"         → os_type: "linux", distro: "debian"
SSH banner contains "Apple"          → os_type: "macos"
WinRM responds                       → os_type: "windows"
HTTP response from known medical UIs → device_tag: "medical"
HTTP response from known printer UIs → device_tag: "printer"
None of the above                    → os_type: "unknown"
```

**Reported to Central Command via existing `POST /api/devices/sync`:**

The existing `DeviceSyncEntry` model is extended with new fields. The daemon already sends device data through this endpoint (not through checkin). New probe fields are added to the sync payload:

```json
{
  "devices": [
    {
      "device_id": "arp-08:00:27:xx:xx:xx",
      "ip_address": "192.168.88.239",
      "mac_address": "08:00:27:xx:xx:xx",
      "hostname": "northvalley-linux",
      "device_type": "workstation",
      "os_name": "linux",
      "os_version": "Ubuntu 22.04",
      "discovery_source": "arp",
      "compliance_status": "unknown",
      "os_fingerprint": "OpenSSH_8.9p1 Ubuntu-3ubuntu0.6",
      "probe_ssh": true,
      "probe_winrm": false,
      "ad_joined": false,
      "has_agent": false,
      "first_seen_at": "2026-03-22T12:00:00Z",
      "last_seen_at": "2026-03-22T17:30:00Z"
    }
  ]
}
```

**Note on device identity:** `device_id` uses MAC address (`arp-{mac}`) rather than IP address to handle DHCP lease changes. The existing `_add_manual_device()` uses `manual-{ip}` which is fragile — we should migrate to MAC-based IDs where available. For manually added devices without a known MAC, IP-based ID remains as fallback.

### Files Changed

| File | Change |
|------|--------|
| `appliance/internal/daemon/netscan.go` | Increase ARP frequency to 3 min, add probe sweep goroutine |
| `appliance/internal/daemon/probes.go` | New file: SSH banner, WinRM, HTTP probe functions |
| `appliance/internal/daemon/classify.go` | New file: OS classification from probe results |
| `mcp-server/app/dashboard_api/device_sync.py` | Extend `DeviceSyncEntry` with probe fields, accept new columns |

---

## Section 2: Auto-Deploy Pipeline

Three deployment paths based on device classification:

### Path 1 — AD-Joined Windows (Daemon-Autonomous, Zero-Click)

**This extends the existing `autodeploy.go` (630 lines)** which already:
- Enumerates AD via `appliance/internal/discovery/ad.go`
- Tests WinRM reachability on discovered machines
- Deploys agents via WinRM with NETLOGON UNC fallback
- Caches agent binary as base64 in memory (`agentB64` field)
- Operates autonomously — no Central Command authorization needed

**What's new:**
- Cross-reference AD-discovered hosts with `go_agents` heartbeat data (from checkin response)
- Skip deployment to hosts that already have an active agent (prevents re-deploy on every cycle)
- Report deployment status back to Central Command in device sync payload
- Cache binary to local filesystem (not just memory) for SSH deploy path reuse

```
Existing autodeploy.go cycle runs
  → AD enumeration discovers machines (already implemented)
  → Cross-reference with go_agents data from last checkin
  → Host has no active agent AND is WinRM-reachable?
  → Deploy using existing WinRM fallback chain (already implemented)
  → Report deploy result in next device sync
```

**No new server-controlled deploy path for AD-joined machines.** The daemon remains autonomous for AD targets — this is the proven architecture.

### Path 2 — Manual "Take Over" (Linux/Mac/Standalone Windows)

For non-AD devices, Central Command mediates because the daemon doesn't have credentials:

```
Device appears in UI as "Discovered — Unmanaged"
  → Admin clicks "Take Over" button on device row
  → Modal pre-fills hostname, IP, MAC, detected OS
  → Admin enters: username + password/SSH key
  → Backend saves credentials to site_credentials table
  → Backend sets agent_deploy_status = "pending" on discovered_devices
  → Next checkin (within 60s): daemon receives pending_deploys[] in response
  → Daemon connects via SSH (reusing infrastructure from linuxscan.go/macosscan.go)
  → Uploads agent binary from local cache, installs service:
      - Linux: systemd unit (osiriscare-agent.service)
      - macOS: launchd plist (com.osiriscare.agent.plist)
      - Standalone Windows: Windows service via WinRM
  → Reports deploy result in next device sync
  → agent_deploy_status updated to "success" or "failed"
```

**SSH implementation note:** The daemon already has SSH client infrastructure in `linuxscan.go` and `macosscan.go` (connection pooling, key auth, password auth, known_hosts via TOFU). The new `deploy_ssh.go` reuses this — it's essentially "connect via SSH, SCP binary, run install commands" using the same SSH client pool.

### Path 3 — Ignore/Tag (Inventory Only)

```
Admin sees unknown device (printer, IoT, personal laptop)
  → Clicks "Ignore" or selects tag: printer, iot, personal, medical
  → Device stays in inventory (HIPAA network audit value)
  → No agent deploy attempted
  → Can be un-ignored later
```

### Deployment Status Tracking

Single unified `device_status` field on `discovered_devices` that combines lifecycle and deploy state (see Section 5G for full state machine):

- `discovered` — just found on network, not yet probed
- `probed` — OS fingerprinted, awaiting classification
- `ad_managed` — AD-joined, daemon will auto-deploy
- `deploying` — deployment in progress
- `agent_active` — Go agent running and reporting
- `agent_stale` — agent heartbeat missed (>10 min)
- `take_over_available` — non-AD, awaiting admin credentials
- `pending_deploy` — admin provided creds, waiting for daemon
- `deploy_failed` — deployment failed (see `agent_deploy_error`)
- `ignored` — admin explicitly dismissed
- `archived` — not seen in 30+ days

Plus supporting fields:
- `agent_deploy_error`: failure reason text
- `agent_deploy_attempted_at`: timestamp of last attempt
- `deploy_attempts`: retry counter

### Take Over Sequence Diagram

```
Admin            Frontend           Backend API        Database         Daemon (checkin)
  │                  │                   │                │                   │
  │ Click "Take Over"│                   │                │                   │
  │─────────────────>│                   │                │                   │
  │                  │ POST /sites/{id}/ │                │                   │
  │                  │ devices/takeover  │                │                   │
  │                  │──────────────────>│                │                   │
  │                  │                   │ INSERT creds   │                   │
  │                  │                   │───────────────>│                   │
  │                  │                   │ UPDATE device  │                   │
  │                  │                   │ status=pending │                   │
  │                  │                   │───────────────>│                   │
  │                  │   200 OK          │                │                   │
  │                  │<──────────────────│                │                   │
  │  "Deploying..."  │                   │                │                   │
  │<─────────────────│                   │                │                   │
  │                  │                   │                │  POST /checkin    │
  │                  │                   │                │<──────────────────│
  │                  │                   │ Query pending  │                   │
  │                  │                   │ deploys        │                   │
  │                  │                   │───────────────>│                   │
  │                  │                   │ Return pending │                   │
  │                  │                   │ _deploys[]     │                   │
  │                  │                   │──────────────────────────────────>│
  │                  │                   │                │                   │
  │                  │                   │                │   SSH connect +   │
  │                  │                   │                │   deploy binary   │
  │                  │                   │                │   ───────────>    │
  │                  │                   │                │                   │
  │                  │                   │                │  POST /devices/   │
  │                  │                   │                │  sync (result)    │
  │                  │                   │                │<──────────────────│
  │                  │                   │ UPDATE device  │                   │
  │                  │                   │ status=active  │                   │
  │                  │                   │───────────────>│                   │
  │                  │ Poll GET devices  │                │                   │
  │                  │──────────────────>│                │                   │
  │  "Agent Active"  │                   │                │                   │
  │<─────────────────│                   │                │                   │
```

### Files Changed

| File | Change |
|------|--------|
| `appliance/internal/daemon/autodeploy.go` | Extend: cross-ref go_agents, report deploy status, local binary cache |
| `appliance/internal/daemon/deploy_ssh.go` | New: SSH-based Linux/macOS deploy (reuses SSH pool from linuxscan.go) |
| `appliance/internal/daemon/phonehome.go` | Extend CheckinRequest with deploy_results, parse pending_deploys |
| `mcp-server/app/main.py` | Add `pending_deploys` to checkin response (query pending devices) |
| `mcp-server/app/dashboard_api/sites.py` | New "Take Over" endpoint: save creds + set pending status |

---

## Section 3: UI — Discovered Devices + Take Over Flow

### SiteDevices.tsx Changes

All frontend paths are relative to `mcp-server/central-command/frontend/src/`.

**New coverage tier column:**

| Status | Visual | Meaning |
|--------|--------|---------|
| Agent Active | Green dot + "Agent" | Go agent pushing checks |
| Deploying... | Spinner + "Deploying" | Deploy in progress |
| Deploy Failed | Red dot + "Failed" + retry | Error on hover |
| AD Managed | Blue dot + "AD — auto-deploy" | Daemon will handle |
| Discovered | Yellow dot + "Take Over" button | Needs credentials |
| Ignored | Gray dot + "Ignored" | Admin dismissed |

**"Take Over" modal (extends AddDeviceModal):**
- Pre-filled: hostname, IP, MAC, detected OS (from probe fingerprint)
- Admin enters: username + password or SSH key
- OS type auto-selected from fingerprint (editable if wrong)
- "Deploy Agent" button saves creds AND triggers deploy
- Modal stays open, polls device status: Connecting → Uploading → Installing → Verifying → Done

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
| `pages/SiteDevices.tsx` | Coverage tier column, filter toggle, bulk actions |
| `components/shared/AddDeviceModal.tsx` | Pre-fill from probe data, deploy progress polling |
| `components/shared/DeployProgress.tsx` | New: step-by-step deploy status display |

---

## Section 4: Data Model Changes

### Migration (next sequential number after current latest)

```sql
-- Extend discovered_devices for probing + auto-deploy
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS os_fingerprint TEXT;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS distro TEXT;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS probe_ssh BOOLEAN DEFAULT FALSE;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS probe_winrm BOOLEAN DEFAULT FALSE;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS probe_snmp BOOLEAN DEFAULT FALSE;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS ad_joined BOOLEAN DEFAULT FALSE;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS device_status TEXT DEFAULT 'discovered';
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS agent_deploy_error TEXT;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS agent_deploy_attempted_at TIMESTAMPTZ;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS deploy_attempts INTEGER DEFAULT 0;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS device_tag TEXT;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS last_probe_at TIMESTAMPTZ;

-- Note: os_name already exists on the table and is reused for os_type classification.
-- os_name stores the OS family ('linux', 'macos', 'windows') from probe classification.
-- os_version stores the specific version string ('Ubuntu 22.04', 'macOS 11.7').
-- The new distro column stores Linux-specific distribution ('ubuntu', 'debian', 'rhel').

-- Migrate legacy agent_deploy_status data if any exists
-- (unified into device_status field — see Section 5G)

-- Index for finding devices needing deployment
CREATE INDEX IF NOT EXISTS idx_discovered_devices_deploy_status
ON discovered_devices (device_status) WHERE device_status IN ('pending_deploy', 'deploying', 'ad_managed');

-- Index for finding unmanaged devices
CREATE INDEX IF NOT EXISTS idx_discovered_devices_unmanaged
ON discovered_devices (site_id, device_tag) WHERE device_tag IS NULL AND device_status = 'take_over_available';
```

### Checkin Response — New `pending_deploys` Array

Only used for Path 2 (Take Over) devices where Central Command has credentials the daemon doesn't:

```json
{
  "pending_deploys": [
    {
      "device_id": "arp-08:00:27:xx:xx:xx",
      "ip_address": "192.168.88.239",
      "hostname": "northvalley-linux",
      "os_type": "linux",
      "deploy_method": "ssh",
      "credential_ref": 42,
      "encrypted_credentials": "<age-encrypted JSON blob>",
      "agent_binary_url": "https://api.osiriscare.net/updates/osiris-agent-linux-amd64"
    }
  ]
}
```

**Credential security (Phase 1 requirement — not deferred to Phase 3):**
- Credentials are encrypted with the appliance's age public key before inclusion in the response
- Daemon decrypts with its local age private key (already available for SOPS)
- Credentials are never persisted to daemon disk — used in-memory only
- Response logging explicitly excludes `pending_deploys` and `encrypted_credentials` fields
- This mirrors the existing `windows_targets` credential delivery but with actual encryption

### Checkin Request — Deploy Status Reporting

```json
{
  "deploy_results": [
    {
      "device_id": "arp-08:00:27:xx:xx:xx",
      "status": "success",
      "agent_id": "go-northvalley-linux-a1b2c3d4",
      "error": null
    }
  ]
}
```

Backend processes `deploy_results` and updates `device_status` on `discovered_devices`.

### Device Sync — Extended Payload

The existing `POST /api/devices/sync` endpoint accepts the new probe fields. Backend `device_sync.py` upserts with:
- `os_fingerprint`, `probe_ssh`, `probe_winrm`, `ad_joined` from daemon probes
- `device_status` transitions based on probe results + go_agents cross-reference
- MAC-based `device_id` (`arp-{mac}`) for stable identity across DHCP changes

No changes to `go_agents` table — once deployed, agents register themselves via existing heartbeat flow.

---

## Section 5: Advanced Enhancements

### 5A — Rogue Device Alerting (Phase 1)

New device on subnet that wasn't previously seen triggers an automatic incident.

**Severity classification:**
- Unknown device with open ports → `high` (potential attack vector on healthcare network)
- New device matching consumer OUI (iPhone, Ring, etc.) → `medium` (personal device)
- Device with MAC address that was previously associated with different IP → `info` (DHCP change, not rogue)
- Known MAC appearing with different hostname → `medium` (possible compromise)

**Suppression rules (to prevent noise):**
- Same MAC returning with different IP = DHCP change, NOT a rogue device. Suppress.
- First 24 hours after daemon boot = baseline establishment. Discovery-only, no rogue alerts.
- Devices matching `device_tag` (printer, iot, personal) are exempt from rogue alerting.
- Rate limit: max 10 rogue alerts per hour per site to prevent alert storms on guest WiFi.

**Implementation:**
- New incident type: `NETWORK-ROGUE-DEVICE`
- L1 rule auto-creates incident on first sighting of truly unknown MAC
- Admin reviews: take over, tag, or ignore
- Feeds into compliance packets: "Network perimeter integrity — X rogue devices detected this period"

**Files:** `appliance/internal/daemon/netscan.go` (detection + suppression), `mcp-server/app/main.py` (L1 rule)

### 5B — Agent Self-Healing (Phase 2)

Daemon monitors agent heartbeats via `go_agents` data received in checkin response.

**Escalation ladder:**
1. Agent silent for 10 minutes → daemon probes via SSH/WinRM health check
2. Agent process dead → auto-redeploy using stored credentials (AD creds for domain machines, or re-request from Central Command for Take Over devices)
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
- **Phase 1 prerequisite:** Binary cached to local filesystem on appliance (not just base64 in memory). Downloaded once from Central Command, uploaded to each target from local cache.

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

**Note:** Phase 1 handles credentials-in-transit encryption via age keys (see Section 4). Phase 3 extends this to at-rest encryption in the database.

**Current:** credentials stored as JSON bytes in `site_credentials.encrypted_data` (misleading name — not actually encrypted).

**Enhancement:**
- Encrypt with age public key before database storage
- Decrypt only at checkin delivery time (server-side, using age private key)
- Credential age tracking: if SSH password creds are >90 days old, flag in UI as "stale credentials"

**Files:** `mcp-server/app/main.py` (encrypt on save, decrypt on deliver), migration for encrypted format

### 5F-pre — SSSD/AD-Joined Linux Detection (Phase 3)

Linux machines joined to AD via SSSD/realmd/Winbind get a computer account in AD. Detection:
- **AD cross-reference:** If `Get-ADComputer` returns a Linux hostname (OS contains "Linux"), mark `ad_joined = true` → auto-deploy path (SSH with domain creds or service account)
- **Kerberos probe:** Port 88 open on the host suggests Kerberos client (likely domain-joined)
- **SSSD fingerprint:** After SSH connect, check for `/etc/sssd/sssd.conf` or `realm list` output
- Enables auto-deploy for enterprise clients (500+ seats) where Linux is domain-managed
- Small clinics (1-50 providers) will almost never hit this path — their Linux is standalone

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

Unified `device_status` field — replaces the separate `agent_deploy_status` from the original design. Single source of truth for device state.

```
discovered → probed → [ad_managed | take_over_available | ignored]
                              ↓              ↓
                        deploying       pending_deploy → deploying
                              ↓              ↓
                         agent_active    agent_active
                              ↓              ↓
                         [agent_stale → auto_redeploy (Phase 2)]
                              ↓
                         agent_offline → incident_created
                              ↓
                         archived (30+ days unseen)
```

**State transition rules:**
- `discovered` → `probed`: OS fingerprint obtained from active sweep
- `probed` → `ad_managed`: device found in AD enumeration results
- `probed` → `take_over_available`: not in AD, has SSH or WinRM open
- `probed` → `ignored`: admin tags as printer/iot/personal
- `ad_managed` → `deploying`: daemon begins auto-deploy
- `take_over_available` → `pending_deploy`: admin provides credentials
- `pending_deploy` → `deploying`: daemon picks up from checkin
- `deploying` → `agent_active`: agent heartbeat received
- `deploying` → `deploy_failed`: deploy error (see `agent_deploy_error`)
- `agent_active` → `agent_stale`: no heartbeat for 10+ minutes
- Any state → `archived`: not seen on network for 30+ days
- `archived` → `discovered`: device reappears on network

**Lifecycle rules:**
- Device not seen in 7 days → incident created (possible hardware removal)
- Device not seen in 30 days → auto-archived (visible in history, removed from active list)
- Device reappears → status restored to `discovered`, re-probed, incident auto-resolved

**Files:** `appliance/internal/daemon/lifecycle.go` (new), `mcp-server/app/dashboard_api/device_sync.py` (archive logic)

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

**Files:** `mcp-server/app/dashboard_api/_routes_impl.py` (calculation), `mcp-server/central-command/frontend/src/pages/SiteDetail.tsx` (gauge), compliance packet generation

### 5I — Remote Uninstall (Phase 3)

**New fleet order type: `remove_agent`**

Daemon connects via SSH/WinRM to target:
1. Stop agent service (systemctl stop / launchctl unload / sc stop)
2. Remove service definition
3. Delete binary and data directory
4. Report removal to Central Command
5. `go_agents` record marked `removed`
6. `discovered_devices.device_status` reverted to `take_over_available` or `ad_managed`

**Partial deploy cleanup:** If a deploy partially completes (binary uploaded, service partially configured), the uninstall path cleans up artifacts before retrying. This runs automatically on `deploy_failed` before the next retry attempt.

**Use cases:**
- Device being decommissioned
- Agent causing issues on target
- Transferring device between sites (uninstall → re-deploy with new site_id)

**Files:** `appliance/internal/daemon/autodeploy.go` (uninstall logic), fleet CLI addition

---

## Implementation Phases

### Phase 1 — Core Auto-Discover + Auto-Deploy
Sections 1-4 + 5A (rogue alerting) + 5G (lifecycle state machine) + credential encryption in transit

**Deliverables:**
- Enhanced ARP polling (3-min) + active probe sweep (10-min) with OS fingerprinting
- Auto-deploy to AD-joined Windows devices (extending existing autodeploy.go)
- "Take Over" UI flow for Linux/Mac/standalone Windows
- SSH deploy path (reusing linuxscan.go/macosscan.go SSH infrastructure)
- Rogue device incident generation with suppression rules
- Unified device lifecycle state machine
- Age-encrypted credential delivery in checkin response
- DB migration for new discovered_devices columns
- Local binary cache on appliance filesystem
- Deploy northvalley-linux Ubuntu VM as first Linux agent

### Phase 2 — Reliability
5B (agent self-healing) + 5C (staggered deployment) + 5D (pre-flight checks)

**Deliverables:**
- Agent health monitoring with auto-redeploy (3-strike limit)
- Batched deployment with configurable concurrency
- Pre-flight validation (disk, OS version, existing software)

### Phase 3 — Polish
5E (credential encryption at rest) + 5F (topology) + 5H (coverage score) + 5I (uninstall)

**Deliverables:**
- Age-encrypted credential storage in database
- Multi-subnet awareness and UI grouping
- Network coverage percentage metric
- Remote agent uninstall + partial deploy cleanup

---

## Success Criteria

1. Daemon discovers all 5 lab devices within 10 minutes of boot (probe sweep cycle)
2. AD-joined NVDC01 and NVWS01 recognized as `ad_managed`, auto-deploy skipped (already have agents)
3. northvalley-linux appears in UI as "Discovered — Ubuntu" with "Take Over" button
4. Admin enters SSH creds for northvalley-linux → agent deployed within 60 seconds
5. Random new device plugged into network → rogue device incident within 10 minutes
6. Agent process killed on any host → auto-redeployed within 10 minutes (Phase 2)
7. Network coverage score shows 100% when all devices managed or tagged (Phase 3)
