# Auto-Discovery + Auto-Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Appliance daemon continuously discovers all subnet devices, auto-deploys Go agents to AD-joined machines, and provides one-click "Take Over" for non-AD devices (Linux/Mac/standalone Windows).

**Architecture:** Extends existing ARP scan + AD enumeration with active OS probing. AD auto-deploy extends `autodeploy.go` (daemon-autonomous). Non-AD "Take Over" uses credentials delivered via checkin response, deployed via SSH/WinRM. Unified `device_status` state machine tracks lifecycle.

**Tech Stack:** Go (daemon), Python/FastAPI (backend), React/TypeScript (frontend), PostgreSQL, SSH (golang.org/x/crypto/ssh), age encryption

**Spec:** `docs/superpowers/specs/2026-03-22-auto-discovery-auto-deploy-design.md`

---

## File Map

### New Files (Go Daemon)
| File | Responsibility |
|------|---------------|
| `appliance/internal/daemon/probes.go` | SSH banner, WinRM, HTTP probe functions |
| `appliance/internal/daemon/classify.go` | OS classification from probe results |
| `appliance/internal/daemon/deploy_ssh.go` | SSH-based agent deployment for Linux/macOS |
| `appliance/internal/daemon/lifecycle.go` | Device lifecycle state transitions + archive |
| `appliance/internal/daemon/probes_test.go` | Probe unit tests |
| `appliance/internal/daemon/classify_test.go` | Classification unit tests |
| `appliance/internal/daemon/deploy_ssh_test.go` | SSH deploy unit tests |

### Modified Files (Go Daemon)
| File | What Changes |
|------|-------------|
| `appliance/internal/daemon/netscan.go` | 3-min ARP, call probes after discovery |
| `appliance/internal/daemon/autodeploy.go` | Cross-ref go_agents, report deploy status, binary cache |
| `appliance/internal/daemon/phonehome.go` | Add `PendingDeploys` to CheckinResponse, `DeployResults` to CheckinRequest |
| `appliance/internal/daemon/daemon.go` | Wire probe sweep goroutine, pass go_agents data to autodeploy |

### New Files (Backend)
| File | Responsibility |
|------|---------------|
| `mcp-server/central-command/backend/migrations/096_device_discovery_autodeploy.sql` | Schema migration |

### Modified Files (Backend)
| File | What Changes |
|------|-------------|
| `mcp-server/dashboard_api/device_sync.py` | Extend DeviceSyncEntry with probe fields, device_status upsert |
| `mcp-server/dashboard_api/sites.py` | Checkin: `pending_deploys` + `deploy_results`, "Take Over" endpoint |
| `mcp-server/central-command/backend/sites.py` | `_add_manual_device` arg order fix for Take Over |

> **IMPORTANT:** The real checkin handler is `appliance_checkin()` in `mcp-server/dashboard_api/sites.py:2183`, NOT the stub at `mcp-server/main.py:4575`. The `appliances_router` is registered at `main.py:1183` and overrides the stub. All checkin changes go in `dashboard_api/sites.py`.

### Modified Files (Frontend)
| File | What Changes |
|------|-------------|
| `mcp-server/central-command/frontend/src/pages/SiteDevices.tsx` | Coverage tier column, filter toggle, Take Over button |
| `mcp-server/central-command/frontend/src/components/shared/AddDeviceModal.tsx` | Pre-fill from probe data, deploy trigger |

### New Files (Frontend)
| File | Responsibility |
|------|---------------|
| `mcp-server/central-command/frontend/src/components/shared/DeployProgress.tsx` | Deploy status stepper |

---

## Task 1: Database Migration — discovered_devices Extensions

**Files:**
- Create: `mcp-server/central-command/backend/migrations/096_device_discovery_autodeploy.sql`

> **Note:** Verify latest migration number before creating. Currently 095 is latest. If another migration has landed, adjust the number accordingly.

- [ ] **Step 1: Write migration SQL**

```sql
-- Migration 096: Auto-discovery + auto-deploy columns on discovered_devices
-- Extends device tracking with OS probing, lifecycle state, and deploy tracking

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

-- Index for finding devices needing deployment
CREATE INDEX IF NOT EXISTS idx_discovered_devices_deploy_status
ON discovered_devices (device_status) WHERE device_status IN ('pending_deploy', 'deploying', 'ad_managed');

-- Index for finding unmanaged devices per site
CREATE INDEX IF NOT EXISTS idx_discovered_devices_unmanaged
ON discovered_devices (site_id, device_tag) WHERE device_tag IS NULL AND device_status = 'take_over_available';
```

- [ ] **Step 2: Register migration in startup**

Read `mcp-server/app/main.py` startup to find where migrations are applied. Add migration 096 to the sequence.

- [ ] **Step 3: Test migration locally**

Run: `ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -c \"SELECT column_name FROM information_schema.columns WHERE table_name='discovered_devices' ORDER BY ordinal_position\""`

Verify new columns exist after deploy.

- [ ] **Step 4: Commit**

```bash
git add mcp-server/central-command/backend/migrations/096_device_discovery_autodeploy.sql
git commit -m "feat: migration 096 — discovered_devices probe + deploy columns"
```

---

## Task 2: OS Probing — SSH Banner + WinRM Detection

**Files:**
- Create: `appliance/internal/daemon/probes.go`
- Create: `appliance/internal/daemon/probes_test.go`

- [ ] **Step 1: Write failing tests for SSH banner parsing**

```go
// probes_test.go
package daemon

import (
    "testing"
)

func TestParseSSHBanner(t *testing.T) {
    tests := []struct {
        name    string
        banner  string
        wantOS  string
        wantDist string
    }{
        {"Ubuntu", "SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.6", "linux", "ubuntu"},
        {"Debian", "SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u2", "linux", "debian"},
        {"macOS", "SSH-2.0-OpenSSH_8.6 Apple", "macos", ""},
        {"RHEL", "SSH-2.0-OpenSSH_8.0 Red Hat Enterprise Linux", "linux", "rhel"},
        {"CentOS", "SSH-2.0-OpenSSH_7.4 CentOS", "linux", "centos"},
        {"Generic Linux", "SSH-2.0-OpenSSH_8.4", "linux", ""},
        {"Windows OpenSSH", "SSH-2.0-OpenSSH_for_Windows_8.1", "windows", ""},
        {"Empty", "", "", ""},
    }
    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            os, dist := parseSSHBanner(tt.banner)
            if os != tt.wantOS {
                t.Errorf("parseSSHBanner(%q) os = %q, want %q", tt.banner, os, tt.wantOS)
            }
            if dist != tt.wantDist {
                t.Errorf("parseSSHBanner(%q) dist = %q, want %q", tt.banner, dist, tt.wantDist)
            }
        })
    }
}
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd appliance && go test ./internal/daemon/ -run TestParseSSHBanner -v`
Expected: FAIL — `parseSSHBanner` not defined

- [ ] **Step 3: Implement probes.go**

```go
// probes.go — Active OS probing for discovered devices
package daemon

import (
    "context"
    "fmt"
    "net"
    "net/http"
    "strings"
    "sync"
    "time"
)

// ProbeResult contains OS fingerprint data from active probing
type ProbeResult struct {
    IP            string
    SSHBanner     string
    SSHOpen       bool
    WinRMOpen     bool
    HTTPBanner    string
    OSType        string // linux, macos, windows, network, unknown
    Distro        string // ubuntu, debian, rhel, centos, ""
    OSFingerprint string // raw banner string
}

// probeHost runs SSH banner grab + WinRM check on a single IP
func probeHost(ctx context.Context, ip string) ProbeResult {
    result := ProbeResult{IP: ip}

    // SSH banner grab (port 22, 3s timeout)
    sshBanner, sshOpen := grabSSHBanner(ctx, ip, 22)
    result.SSHBanner = sshBanner
    result.SSHOpen = sshOpen

    // WinRM check (port 5985, 3s timeout)
    result.WinRMOpen = checkWinRM(ctx, ip)

    // Classify OS from probes
    result.OSType, result.Distro = classifyFromProbes(result)
    result.OSFingerprint = sshBanner

    return result
}

// grabSSHBanner connects to SSH port and reads the server banner
func grabSSHBanner(ctx context.Context, ip string, port int) (string, bool) {
    addr := fmt.Sprintf("%s:%d", ip, port)
    dialer := net.Dialer{Timeout: 3 * time.Second}
    conn, err := dialer.DialContext(ctx, "tcp", addr)
    if err != nil {
        return "", false
    }
    defer conn.Close()

    conn.SetReadDeadline(time.Now().Add(3 * time.Second))
    buf := make([]byte, 256)
    n, err := conn.Read(buf)
    if err != nil || n == 0 {
        return "", true // port open but no banner
    }
    return strings.TrimSpace(string(buf[:n])), true
}

// checkWinRM tests if WinRM port 5985 is open
func checkWinRM(ctx context.Context, ip string) bool {
    addr := fmt.Sprintf("%s:5985", ip)
    dialer := net.Dialer{Timeout: 3 * time.Second}
    conn, err := dialer.DialContext(ctx, "tcp", addr)
    if err != nil {
        return false
    }
    conn.Close()
    return true
}

// parseSSHBanner extracts OS type and distro from SSH version string
func parseSSHBanner(banner string) (osType, distro string) {
    if banner == "" {
        return "", ""
    }
    lower := strings.ToLower(banner)

    switch {
    case strings.Contains(lower, "apple"):
        return "macos", ""
    case strings.Contains(lower, "openssh_for_windows"):
        return "windows", ""
    case strings.Contains(lower, "ubuntu"):
        return "linux", "ubuntu"
    case strings.Contains(lower, "debian"):
        return "linux", "debian"
    case strings.Contains(lower, "red hat"):
        return "linux", "rhel"
    case strings.Contains(lower, "centos"):
        return "linux", "centos"
    case strings.Contains(lower, "openssh"):
        return "linux", "" // generic Linux
    default:
        return "unknown", ""
    }
}

// classifyFromProbes determines OS type from combined probe results
func classifyFromProbes(r ProbeResult) (string, string) {
    // SSH banner is strongest signal
    if r.SSHOpen && r.SSHBanner != "" {
        return parseSSHBanner(r.SSHBanner)
    }
    // WinRM open = Windows
    if r.WinRMOpen {
        return "windows", ""
    }
    // SSH open but no banner = likely Linux/Mac
    if r.SSHOpen {
        return "linux", ""
    }
    return "unknown", ""
}

// probeHosts runs probes on a list of IPs concurrently (max 10 at a time)
func probeHosts(ctx context.Context, ips []string) []ProbeResult {
    results := make([]ProbeResult, len(ips))
    sem := make(chan struct{}, 10) // concurrency limit

    var wg sync.WaitGroup
    for i, ip := range ips {
        wg.Add(1)
        go func(idx int, addr string) {
            defer wg.Done()
            sem <- struct{}{}
            defer func() { <-sem }()
            results[idx] = probeHost(ctx, addr)
        }(i, ip)
    }
    wg.Wait()
    return results
}
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd appliance && go test ./internal/daemon/ -run TestParseSSHBanner -v`
Expected: PASS

- [ ] **Step 5: Write tests for probeHost (integration-style, mock-friendly)**

```go
func TestClassifyFromProbes(t *testing.T) {
    tests := []struct {
        name     string
        result   ProbeResult
        wantOS   string
        wantDist string
    }{
        {"SSH Ubuntu", ProbeResult{SSHOpen: true, SSHBanner: "SSH-2.0-OpenSSH_8.9p1 Ubuntu-3"}, "linux", "ubuntu"},
        {"WinRM only", ProbeResult{WinRMOpen: true}, "windows", ""},
        {"SSH no banner", ProbeResult{SSHOpen: true}, "linux", ""},
        {"Nothing", ProbeResult{}, "unknown", ""},
    }
    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            os, dist := classifyFromProbes(tt.result)
            if os != tt.wantOS || dist != tt.wantDist {
                t.Errorf("got (%q, %q), want (%q, %q)", os, dist, tt.wantOS, tt.wantDist)
            }
        })
    }
}
```

- [ ] **Step 6: Run all probe tests**

Run: `cd appliance && go test ./internal/daemon/ -run TestClassify -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add appliance/internal/daemon/probes.go appliance/internal/daemon/probes_test.go
git commit -m "feat: OS probing — SSH banner grab + WinRM detection + classification"
```

---

## Task 3: Enhanced ARP Discovery + Probe Sweep Integration

**Files:**
- Modify: `appliance/internal/daemon/netscan.go:20` (interval), `308-411` (discovery)
- Modify: `appliance/internal/daemon/daemon.go:460-493` (runCycle)

- [ ] **Step 1: Reduce ARP scan interval from 15min to 3min**

In `netscan.go:20`, change `netScanInterval` from `15 * time.Minute` to `3 * time.Minute`.

- [ ] **Step 2: Add probe sweep to netscan.go**

After `discoverARPDevices()` returns, add a function that:
1. Collects all discovered IPs
2. Calls `probeHosts()` from probes.go
3. Merges probe results into discoveredDevice entries
4. Adds new fields: `OSType`, `Distro`, `OSFingerprint`, `ProbeSSH`, `ProbeWinRM`

Extend the `discoveredDevice` struct (line 25-30) with:
```go
type discoveredDevice struct {
    IPAddress     string
    MACAddress    string
    Hostname      string
    Interface     string
    // New probe fields
    OSType        string
    Distro        string
    OSFingerprint string
    ProbeSSH      bool
    ProbeWinRM    bool
    ADJoined      bool
    HasAgent      bool
}
```

- [ ] **Step 3: Wire probe sweep into discovery cycle**

In `netscan.go`, after `discoverARPDevices()` and `resolveHostnames()`, add:
```go
func (ds *driftScanner) enrichWithProbes(ctx context.Context, devices []discoveredDevice) {
    ips := make([]string, len(devices))
    for i, d := range devices {
        ips[i] = d.IPAddress
    }
    probes := probeHosts(ctx, ips)
    for i, p := range probes {
        devices[i].OSType = p.OSType
        devices[i].Distro = p.Distro
        devices[i].OSFingerprint = p.OSFingerprint
        devices[i].ProbeSSH = p.SSHOpen
        devices[i].ProbeWinRM = p.WinRMOpen
    }
}
```

- [ ] **Step 4: Update device sync payload**

In the function that sends devices to Central Command (device sync), include the new probe fields in the JSON payload. Check `daemon.go` for where `DiscoveryResults` is assembled (around line 497-532 in `runCheckin()`).

- [ ] **Step 5: Test compilation + existing tests still pass**

Run: `cd appliance && go build ./... && go test ./internal/daemon/ -v`
Expected: All existing tests PASS, build succeeds

- [ ] **Step 6: Commit**

```bash
git add appliance/internal/daemon/netscan.go appliance/internal/daemon/daemon.go
git commit -m "feat: 3-min ARP scan + OS probe sweep on discovered devices"
```

---

## Task 4: Backend — Accept Probe Data in Device Sync

**Files:**
- Modify: `mcp-server/dashboard_api/device_sync.py:36-61` (DeviceSyncEntry)

- [ ] **Step 1: Write test for extended device sync**

```python
# In tests/ — test device sync accepts new probe fields
async def test_device_sync_with_probes(mock_pool):
    """Device sync should accept and store probe fields."""
    entry = {
        "device_id": "arp-08:00:27:aa:bb:cc",
        "hostname": "northvalley-linux",
        "ip_address": "192.168.88.239",
        "mac_address": "08:00:27:aa:bb:cc",
        "device_type": "workstation",
        "os_name": "linux",
        "os_version": "Ubuntu 22.04",
        "discovery_source": "arp",
        "compliance_status": "unknown",
        "os_fingerprint": "OpenSSH_8.9p1 Ubuntu-3ubuntu0.6",
        "distro": "ubuntu",
        "probe_ssh": True,
        "probe_winrm": False,
        "ad_joined": False,
    }
    # Should not raise
    validated = DeviceSyncEntry(**entry)
    assert validated.os_fingerprint == "OpenSSH_8.9p1 Ubuntu-3ubuntu0.6"
    assert validated.distro == "ubuntu"
    assert validated.probe_ssh is True
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd packages/compliance-agent && source venv/bin/activate && python -m pytest tests/test_device_sync_probes.py -v`
Expected: FAIL — `os_fingerprint` not a field on DeviceSyncEntry

- [ ] **Step 3: Extend DeviceSyncEntry model**

In `mcp-server/dashboard_api/device_sync.py:36-61`, add to the Pydantic model:
```python
os_fingerprint: Optional[str] = None
distro: Optional[str] = None
probe_ssh: Optional[bool] = None
probe_winrm: Optional[bool] = None
probe_snmp: Optional[bool] = None
ad_joined: Optional[bool] = None
```

- [ ] **Step 4: Extend sync_devices() upsert**

In `sync_devices()` (~line 94-200), add the new columns to the INSERT/ON CONFLICT UPDATE SQL. The upsert should write `os_fingerprint`, `distro`, `probe_ssh`, `probe_winrm`, `ad_joined`, and set `last_probe_at = NOW()` when probe fields are present.

Also update `device_status` based on probe results:
- `ad_joined = True` and no active go_agent → `device_status = 'ad_managed'`
- `probe_ssh = True` and not `ad_joined` → `device_status = 'take_over_available'`
- `probe_winrm = True` and not `ad_joined` → `device_status = 'take_over_available'`
- Nothing open → `device_status = 'discovered'`

- [ ] **Step 5: Run tests**

Run: `cd packages/compliance-agent && python -m pytest tests/test_device_sync_probes.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add mcp-server/app/dashboard_api/device_sync.py tests/
git commit -m "feat: device sync accepts probe fields + auto-classifies device_status"
```

---

## Task 5: Checkin Response — pending_deploys for Take Over Devices

**Files:**
- Modify: `mcp-server/dashboard_api/sites.py:2183` (the REAL checkin handler — `appliance_checkin()`)
- Modify: `appliance/internal/daemon/phonehome.go:171-188` (CheckinResponse)

> **WARNING:** The stub at `mcp-server/main.py:4575` is NOT used. The real handler is at `dashboard_api/sites.py:2183`, registered via `appliances_router` at `main.py:1183`.

- [ ] **Step 1: Add pending_deploys query to checkin endpoint**

In `dashboard_api/sites.py` `appliance_checkin()` handler (~line 2183), near the end where the response is assembled, add:

```python
# Query devices pending deployment for this site
pending_deploys = []
pending_rows = await conn.fetch("""
    SELECT dd.local_device_id, dd.ip_address, dd.hostname, dd.os_name,
           sc.encrypted_data, sc.credential_type
    FROM discovered_devices dd
    JOIN site_credentials sc ON sc.site_id = dd.site_id
        AND sc.credential_name LIKE dd.hostname || ' (%'
    WHERE dd.site_id = $1
        AND dd.device_status = 'pending_deploy'
    LIMIT 5
""", site_id)

for row in pending_rows:
    cred_data = json.loads(row["encrypted_data"])
    pending_deploys.append({
        "device_id": row["local_device_id"],
        "ip_address": row["ip_address"],
        "hostname": row["hostname"],
        "os_type": row["os_name"],
        "deploy_method": "ssh" if row["credential_type"] in ("ssh_key", "ssh_password") else "winrm",
        "username": cred_data.get("username", ""),
        "password": cred_data.get("password", ""),
        "ssh_key": cred_data.get("private_key", ""),
        "agent_binary_url": f"https://api.osiriscare.net/updates/osiris-agent-{row['os_name']}-amd64",
    })

# Update status to deploying
if pending_deploys:
    device_ids = [p["device_id"] for p in pending_deploys]
    await conn.execute("""
        UPDATE discovered_devices SET device_status = 'deploying',
            agent_deploy_attempted_at = NOW()
        WHERE site_id = $1 AND local_device_id = ANY($2)
    """, site_id, device_ids)
```

Add `pending_deploys` to the checkin response dict.

- [ ] **Step 2: Add deploy_results processing to checkin**

Near the start of `appliances_checkin()`, process incoming deploy results:

```python
deploy_results = data.get("deploy_results", [])
for result in deploy_results:
    new_status = "agent_active" if result["status"] == "success" else "deploy_failed"
    await conn.execute("""
        UPDATE discovered_devices
        SET device_status = $1,
            agent_deploy_error = $2,
            deploy_attempts = deploy_attempts + 1
        WHERE site_id = $3 AND local_device_id = $4
    """, new_status, result.get("error"), site_id, result["device_id"])
```

- [ ] **Step 3: Extend Go CheckinResponse struct**

In `phonehome.go:171-188`, add:
```go
type PendingDeploy struct {
    DeviceID       string `json:"device_id"`
    IPAddress      string `json:"ip_address"`
    Hostname       string `json:"hostname"`
    OSType         string `json:"os_type"`
    DeployMethod   string `json:"deploy_method"` // "ssh" or "winrm"
    Username       string `json:"username"`
    Password       string `json:"password"`
    SSHKey         string `json:"ssh_key,omitempty"`
    AgentBinaryURL string `json:"agent_binary_url"`
}

// Add to CheckinResponse struct:
PendingDeploys []PendingDeploy `json:"pending_deploys,omitempty"`
```

Add `DeployResult` struct to CheckinRequest:
```go
type DeployResult struct {
    DeviceID string `json:"device_id"`
    Status   string `json:"status"` // "success" or "failed"
    AgentID  string `json:"agent_id,omitempty"`
    Error    string `json:"error,omitempty"`
}

// Add to CheckinRequest struct:
DeployResults []DeployResult `json:"deploy_results,omitempty"`
```

- [ ] **Step 4: Test compilation**

Run: `cd appliance && go build ./...`
Expected: Build succeeds

- [ ] **Step 5: Commit**

```bash
git add mcp-server/dashboard_api/sites.py appliance/internal/daemon/phonehome.go
git commit -m "feat: pending_deploys in checkin response + deploy_results reporting"
```

---

## Task 6: SSH Agent Deployment (Linux/macOS)

**Files:**
- Create: `appliance/internal/daemon/deploy_ssh.go`
- Create: `appliance/internal/daemon/deploy_ssh_test.go`

- [ ] **Step 1: Write failing test**

```go
// deploy_ssh_test.go
package daemon

import (
    "strings"
    "testing"
)

func TestBuildInstallScript_Linux(t *testing.T) {
    script := buildInstallScript("linux", "/tmp/osiris-agent", "https://api.osiriscare.net", "site-abc-123")
    if !strings.Contains(script, "systemctl") {
        t.Error("Linux install script should use systemctl")
    }
    if !strings.Contains(script, "/opt/osiriscare") {
        t.Error("Linux install path should be /opt/osiriscare")
    }
    if !strings.Contains(script, "site-abc-123") {
        t.Error("Install script should contain site_id")
    }
}

func TestBuildInstallScript_MacOS(t *testing.T) {
    script := buildInstallScript("macos", "/tmp/osiris-agent", "https://api.osiriscare.net", "site-abc-123")
    if !strings.Contains(script, "launchctl") {
        t.Error("macOS install script should use launchctl")
    }
    if !strings.Contains(script, "/Library/OsirisCare") {
        t.Error("macOS install path should be /Library/OsirisCare")
    }
}
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd appliance && go test ./internal/daemon/ -run TestBuildInstallScript -v`
Expected: FAIL

- [ ] **Step 3: Implement deploy_ssh.go**

```go
// deploy_ssh.go — SSH-based agent deployment for Linux and macOS
package daemon

import (
    "context"
    "encoding/base64"
    "fmt"
    "os"
    "strings"

    "github.com/osiriscare/appliance/internal/sshexec"
)

// deployViaSSH deploys the Go agent to a Linux or macOS host via SSH
func (d *Daemon) deployViaSSH(ctx context.Context, deploy PendingDeploy, siteID string) error {
    // sshexec.Target uses *string for optional fields
    var password, privKey *string
    if deploy.Password != "" {
        password = &deploy.Password
    }
    if deploy.SSHKey != "" {
        privKey = &deploy.SSHKey
    }

    target := sshexec.Target{
        Hostname:   deploy.IPAddress,
        Port:       22,
        Username:   deploy.Username,
        Password:   password,
        PrivateKey: privKey,
    }

    // Step 1: Upload binary via base64 over SSH (no Upload method on sshexec.Executor)
    binaryPath, err := d.getLocalBinaryPath(deploy.OSType)
    if err != nil {
        return fmt.Errorf("get binary: %w", err)
    }

    binaryData, err := os.ReadFile(binaryPath)
    if err != nil {
        return fmt.Errorf("read binary: %w", err)
    }

    // Chunk base64 transfer to avoid SSH buffer limits (20KB chunks)
    b64 := base64.StdEncoding.EncodeToString(binaryData)
    remoteTmp := "/tmp/osiris-agent"
    chunkSize := 20000
    uploadScript := fmt.Sprintf("rm -f %s\n", remoteTmp)
    for i := 0; i < len(b64); i += chunkSize {
        end := i + chunkSize
        if end > len(b64) {
            end = len(b64)
        }
        uploadScript += fmt.Sprintf("echo -n '%s' >> %s.b64\n", b64[i:end], remoteTmp)
    }
    uploadScript += fmt.Sprintf("base64 -d %s.b64 > %s && rm %s.b64 && chmod 755 %s\n",
        remoteTmp, remoteTmp, remoteTmp, remoteTmp)

    if _, err := d.sshExec.Execute(ctx, target, uploadScript); err != nil {
        return fmt.Errorf("upload binary: %w", err)
    }

    // Step 2: Run install script
    apiURL := d.config.APIEndpoint // e.g. "https://api.osiriscare.net"
    script := buildInstallScript(deploy.OSType, remoteTmp, apiURL, siteID)
    out, err := d.sshExec.Execute(ctx, target, script)
    if err != nil {
        return fmt.Errorf("install: %w (output: %s)", err, out)
    }

    return nil
}

// buildInstallScript generates the platform-specific install commands
func buildInstallScript(osType, binaryPath, apiURL, siteID string) string {
    switch osType {
    case "macos":
        return fmt.Sprintf(`
set -e
sudo mkdir -p /Library/OsirisCare /Library/Application\ Support/OsirisCare /Library/Logs/OsirisCare
sudo mv %s /Library/OsirisCare/osiris-agent
sudo chmod 755 /Library/OsirisCare/osiris-agent
sudo chown root:wheel /Library/OsirisCare/osiris-agent

sudo tee /Library/OsirisCare/config.json > /dev/null << 'CONF'
{"api_url":"%s","site_id":"%s","check_interval":300,"data_dir":"/Library/Application Support/OsirisCare"}
CONF

sudo tee /Library/LaunchDaemons/com.osiriscare.agent.plist > /dev/null << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.osiriscare.agent</string>
<key>ProgramArguments</key><array><string>/Library/OsirisCare/osiris-agent</string></array>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>/Library/Logs/OsirisCare/agent.log</string>
<key>StandardErrorPath</key><string>/Library/Logs/OsirisCare/agent.err</string>
</dict></plist>
PLIST

sudo launchctl unload /Library/LaunchDaemons/com.osiriscare.agent.plist 2>/dev/null || true
sudo launchctl load /Library/LaunchDaemons/com.osiriscare.agent.plist
echo "DEPLOY_SUCCESS"
`, binaryPath, apiURL, siteID)

    case "linux":
        return fmt.Sprintf(`
set -e
sudo mkdir -p /opt/osiriscare /var/lib/osiriscare
sudo mv %s /opt/osiriscare/osiris-agent
sudo chmod 755 /opt/osiriscare/osiris-agent

sudo tee /opt/osiriscare/config.json > /dev/null << 'CONF'
{"api_url":"%s","site_id":"%s","check_interval":300,"data_dir":"/var/lib/osiriscare"}
CONF

sudo tee /etc/systemd/system/osiriscare-agent.service > /dev/null << 'UNIT'
[Unit]
Description=OsirisCare Compliance Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/opt/osiriscare/osiris-agent
WorkingDirectory=/opt/osiriscare
Restart=always
RestartSec=30
User=root

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable osiriscare-agent
sudo systemctl restart osiriscare-agent
echo "DEPLOY_SUCCESS"
`, binaryPath, apiURL, siteID)

    default:
        return "echo DEPLOY_UNSUPPORTED_OS; exit 1"
    }
}
```

- [ ] **Step 4: Run tests**

Run: `cd appliance && go test ./internal/daemon/ -run TestBuildInstallScript -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add appliance/internal/daemon/deploy_ssh.go appliance/internal/daemon/deploy_ssh_test.go
git commit -m "feat: SSH-based agent deployment for Linux + macOS"
```

---

## Task 7: Auto-Deploy Orchestrator — Process pending_deploys

**Files:**
- Modify: `appliance/internal/daemon/autodeploy.go:52-120` (struct, runAutoDeployIfNeeded)
- Modify: `appliance/internal/daemon/daemon.go:460-493` (runCycle)

- [ ] **Step 1: Add pending deploy processing to autodeploy.go**

After the existing AD auto-deploy logic in `runAutoDeployOnce()` (~line 374), add a new method:

```go
// processPendingDeploys handles "Take Over" deploys from Central Command
func (a *autoDeployer) processPendingDeploys(ctx context.Context, deploys []PendingDeploy, siteID string) []DeployResult {
    var results []DeployResult
    for _, deploy := range deploys {
        result := DeployResult{DeviceID: deploy.DeviceID}

        var err error
        switch deploy.DeployMethod {
        case "ssh":
            err = a.daemon.deployViaSSH(ctx, deploy, siteID)
        case "winrm":
            err = a.deployViaWinRM(ctx, deploy)
        default:
            err = fmt.Errorf("unknown deploy method: %s", deploy.DeployMethod)
        }

        if err != nil {
            result.Status = "failed"
            result.Error = err.Error()
            slog.Error("deploy failed", "device", deploy.Hostname, "error", err)
        } else {
            result.Status = "success"
            slog.Info("deploy succeeded", "device", deploy.Hostname, "os", deploy.OSType)
        }
        results = append(results, result)
    }
    return results
}
```

- [ ] **Step 2: Wire into daemon.go runCycle**

In `daemon.go:runCycle()` (~line 460), after checkin, pass `PendingDeploys` to autodeploy:

```go
// After checkin response is parsed:
if len(resp.PendingDeploys) > 0 {
    results := d.deployer.processPendingDeploys(ctx, resp.PendingDeploys, d.siteID)
    d.pendingDeployResults = results // stored for next checkin request
}
```

In `runCheckin()` (~line 497), include deploy results in the request:
```go
req.DeployResults = d.pendingDeployResults
d.pendingDeployResults = nil // clear after sending
```

- [ ] **Step 3: Add go_agents cross-reference to AD auto-deploy**

In `runAutoDeployOnce()`, before deploying to AD-discovered hosts, check if the host already has an active Go agent (from last checkin response `go_agents` data). Skip hosts that already have agents.

- [ ] **Step 4: Test compilation + existing tests**

Run: `cd appliance && go build ./... && go test ./... -v`
Expected: Build succeeds, all tests pass

- [ ] **Step 5: Commit**

```bash
git add appliance/internal/daemon/autodeploy.go appliance/internal/daemon/daemon.go
git commit -m "feat: auto-deploy orchestrator — process pending deploys + skip hosts with agents"
```

---

## Task 8: Backend — "Take Over" Endpoint

**Files:**
- Modify: `mcp-server/app/dashboard_api/sites.py:857-968` (extend _add_manual_device)

- [ ] **Step 1: Write test**

```python
@pytest.mark.asyncio
async def test_take_over_sets_pending_deploy(mock_pool):
    """Take Over should save creds AND set device_status = pending_deploy."""
    # ... test that POSTing to takeover endpoint results in:
    # 1. site_credentials row created
    # 2. discovered_devices.device_status = 'pending_deploy'
```

- [ ] **Step 2: Create Take Over endpoint**

In `sites.py`, add a new endpoint that wraps `_add_manual_device` but also sets `device_status = 'pending_deploy'`:

```python
@router.post("/{site_id}/devices/takeover")
async def take_over_device(
    site_id: str,
    device: ManualDeviceAdd,
    user: Dict = Depends(require_operator),
    pool = Depends(get_pool),
):
    """Save credentials and trigger agent deployment for a discovered device."""
    result = await _add_manual_device(pool, site_id, device)  # Note: arg order is (pool, site_id, device)

    # Set device_status to pending_deploy
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE discovered_devices
            SET device_status = 'pending_deploy'
            WHERE site_id = $1 AND (
                ip_address = $2 OR hostname = $3
            )
        """, site_id, device.ip_address, device.hostname)

    return result
```

- [ ] **Step 3: Run test**

Run: `cd packages/compliance-agent && source venv/bin/activate && python -m pytest tests/test_takeover.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add mcp-server/app/dashboard_api/sites.py tests/
git commit -m "feat: Take Over endpoint — save creds + set pending_deploy status"
```

---

## Task 9: Frontend — Coverage Tier Column + Take Over Button

**Files:**
- Modify: `mcp-server/central-command/frontend/src/pages/SiteDevices.tsx:166-193` (CoverageBadge)
- Modify: `mcp-server/central-command/frontend/src/components/shared/AddDeviceModal.tsx`

- [ ] **Step 1: Extend CoverageBadge with new states**

In `SiteDevices.tsx`, replace the existing 3-level coverage badge (~line 166-193) with:

```tsx
const coverageLevels: Record<string, { label: string; color: string; bg: string }> = {
  agent_active: { label: 'Agent', color: 'text-emerald-400', bg: 'bg-emerald-500/15' },
  deploying: { label: 'Deploying...', color: 'text-amber-400', bg: 'bg-amber-500/15' },
  deploy_failed: { label: 'Failed', color: 'text-red-400', bg: 'bg-red-500/15' },
  ad_managed: { label: 'AD — auto-deploy', color: 'text-blue-400', bg: 'bg-blue-500/15' },
  take_over_available: { label: 'Take Over', color: 'text-yellow-400', bg: 'bg-yellow-500/15' },
  pending_deploy: { label: 'Pending...', color: 'text-amber-400', bg: 'bg-amber-500/15' },
  ignored: { label: 'Ignored', color: 'text-slate-500', bg: 'bg-slate-500/10' },
  discovered: { label: 'New', color: 'text-slate-400', bg: 'bg-slate-500/10' },
};
```

- [ ] **Step 2: Add "Take Over" button to device rows**

For devices with `device_status === 'take_over_available'`, render a "Take Over" button instead of a plain badge. Clicking it opens AddDeviceModal pre-filled with the device's hostname, IP, OS type.

```tsx
{device.device_status === 'take_over_available' ? (
  <button
    onClick={() => setTakeOverDevice(device)}
    className="px-2 py-1 text-xs font-medium rounded bg-yellow-500/15 text-yellow-400 hover:bg-yellow-500/25 transition-colors"
  >
    Take Over
  </button>
) : (
  <span className={`px-2 py-1 text-xs font-medium rounded ${level.bg} ${level.color}`}>
    {level.label}
  </span>
)}
```

- [ ] **Step 3: Pre-fill AddDeviceModal for Take Over**

In AddDeviceModal, accept optional `prefill` prop:
```tsx
interface AddDeviceModalProps {
  // ... existing props
  prefill?: {
    hostname: string;
    ip_address: string;
    os_type: string;
    mac_address?: string;
  };
}
```

When `prefill` is provided, pre-populate the form fields and change the submit URL to the `/devices/takeover` endpoint.

- [ ] **Step 4: Add filter toggle (Managed vs All Devices)**

Add a toggle above the device table:
```tsx
const [showAll, setShowAll] = useState(false);
const filteredDevices = showAll
  ? devices
  : devices.filter(d => ['agent_active', 'ad_managed', 'deploying', 'pending_deploy'].includes(d.device_status));
```

- [ ] **Step 5: Create DeployProgress.tsx**

New component that shows deploy status steps:
```tsx
// DeployProgress.tsx
interface DeployProgressProps {
  status: string; // pending_deploy, deploying, agent_active, deploy_failed
  error?: string;
}

export const DeployProgress: React.FC<DeployProgressProps> = ({ status, error }) => {
  const steps = ['Connecting', 'Uploading', 'Installing', 'Verifying'];
  const activeStep = status === 'deploying' ? 1 : status === 'agent_active' ? 4 : 0;

  return (
    <div className="flex items-center gap-2 text-xs">
      {steps.map((step, i) => (
        <span key={step} className={i < activeStep ? 'text-emerald-400' : 'text-slate-500'}>
          {i < activeStep ? '\u2713' : i === activeStep ? '\u25CB' : '\u00B7'} {step}
        </span>
      ))}
      {status === 'deploy_failed' && error && (
        <span className="text-red-400 ml-2" title={error}>Failed</span>
      )}
    </div>
  );
};
```

- [ ] **Step 6: Verify ESLint + tsc**

Run: `cd mcp-server/central-command/frontend && npx tsc --noEmit && npx eslint src/ --quiet`
Expected: 0 errors, 0 warnings

- [ ] **Step 6: Commit**

```bash
git add mcp-server/central-command/frontend/src/
git commit -m "feat: coverage tier column + Take Over button + device filter"
```

---

## Task 10: Rogue Device Alerting

**Files:**
- Modify: `appliance/internal/daemon/netscan.go` (detection logic)
- Modify: `mcp-server/app/main.py` (L1 rule)

- [ ] **Step 1: Add rogue detection to netscan.go**

After ARP discovery, compare current device list against previous cycle's list. New MACs that weren't in the previous cycle are potential rogue devices.

```go
// Track known MACs across scan cycles
type rogueDetector struct {
    knownMACs     map[string]time.Time // MAC → first seen
    baselineUntil time.Time             // suppress alerts during first 24h
    alertCount    int                   // rate limit counter
    alertWindow   time.Time             // rate limit window start
    mu            sync.Mutex
}

func (rd *rogueDetector) checkForRogues(devices []discoveredDevice) []discoveredDevice {
    rd.mu.Lock()
    defer rd.mu.Unlock()

    // Suppress during baseline period
    if time.Now().Before(rd.baselineUntil) {
        for _, d := range devices {
            rd.knownMACs[d.MACAddress] = time.Now()
        }
        return nil
    }

    // Rate limit: max 10 alerts per hour
    if time.Since(rd.alertWindow) > time.Hour {
        rd.alertCount = 0
        rd.alertWindow = time.Now()
    }

    var rogues []discoveredDevice
    for _, d := range devices {
        if _, known := rd.knownMACs[d.MACAddress]; !known {
            rd.knownMACs[d.MACAddress] = time.Now()
            if rd.alertCount < 10 {
                rogues = append(rogues, d)
                rd.alertCount++
            }
        }
    }
    return rogues
}
```

- [ ] **Step 1b: Write tests for rogue detection**

```go
func TestRogueDetector_BaselineSuppression(t *testing.T) {
    rd := &rogueDetector{
        knownMACs:     make(map[string]time.Time),
        baselineUntil: time.Now().Add(24 * time.Hour),
    }
    devices := []discoveredDevice{{MACAddress: "aa:bb:cc:dd:ee:ff"}}
    rogues := rd.checkForRogues(devices)
    if len(rogues) != 0 {
        t.Error("should suppress during baseline period")
    }
    if _, ok := rd.knownMACs["aa:bb:cc:dd:ee:ff"]; !ok {
        t.Error("should still learn MACs during baseline")
    }
}

func TestRogueDetector_NewDevice(t *testing.T) {
    rd := &rogueDetector{
        knownMACs:     map[string]time.Time{"aa:bb:cc:dd:ee:ff": time.Now()},
        baselineUntil: time.Now().Add(-1 * time.Hour), // baseline expired
        alertWindow:   time.Now(),
    }
    devices := []discoveredDevice{
        {MACAddress: "aa:bb:cc:dd:ee:ff"}, // known
        {MACAddress: "11:22:33:44:55:66"}, // new = rogue
    }
    rogues := rd.checkForRogues(devices)
    if len(rogues) != 1 || rogues[0].MACAddress != "11:22:33:44:55:66" {
        t.Errorf("expected 1 rogue, got %d", len(rogues))
    }
}

func TestRogueDetector_RateLimit(t *testing.T) {
    rd := &rogueDetector{
        knownMACs:     make(map[string]time.Time),
        baselineUntil: time.Now().Add(-1 * time.Hour),
        alertCount:    10, // at limit
        alertWindow:   time.Now(),
    }
    devices := []discoveredDevice{{MACAddress: "aa:bb:cc:dd:ee:ff"}}
    rogues := rd.checkForRogues(devices)
    if len(rogues) != 0 {
        t.Error("should suppress when at rate limit")
    }
}
```

Run: `cd appliance && go test ./internal/daemon/ -run TestRogueDetector -v`

- [ ] **Step 2: Report rogue devices as drift events**

Rogue devices get reported as drift findings with type `NETWORK-ROGUE-DEVICE`, which the backend processes through the existing incident pipeline.

- [ ] **Step 3: Add L1 rule for NETWORK-ROGUE-DEVICE**

In `main.py` or via migration, add L1 rule:
```python
{
    "incident_type": "NETWORK-ROGUE-DEVICE",
    "action": "escalate_l3",
    "runbook_id": "ESC-ROGUE-DEVICE-001",
    "description": "Unrecognized device on network — requires admin review"
}
```

This escalates to L3 (human review) since rogue devices need a human decision.

- [ ] **Step 4: Commit**

```bash
git add appliance/internal/daemon/netscan.go mcp-server/
git commit -m "feat: rogue device alerting — 24h baseline, rate limited, L3 escalation"
```

---

## Task 11: Device Lifecycle State Machine

**Files:**
- Create: `appliance/internal/daemon/lifecycle.go`
- Modify: `mcp-server/app/dashboard_api/device_sync.py` (archive logic)

- [ ] **Step 1: Write test for lifecycle transitions**

```go
func TestLifecycleTransitions(t *testing.T) {
    tests := []struct {
        name      string
        current   string
        event     string
        want      string
    }{
        {"discovered to probed", "discovered", "probed", "probed"},
        {"probed to ad_managed", "probed", "ad_joined", "ad_managed"},
        {"probed to take_over", "probed", "ssh_open", "take_over_available"},
        {"ad_managed to deploying", "ad_managed", "deploy_start", "deploying"},
        {"deploying to active", "deploying", "agent_heartbeat", "agent_active"},
        {"deploying to failed", "deploying", "deploy_error", "deploy_failed"},
        {"active to stale", "agent_active", "heartbeat_timeout", "agent_stale"},
        {"stale to offline", "agent_stale", "offline_7d", "agent_offline"},
        {"offline to archived", "agent_offline", "archive_30d", "archived"},
        {"archived reappears", "archived", "seen", "discovered"},
        {"ignored stays ignored", "ignored", "probed", "ignored"},
    }
    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            got := nextState(tt.current, tt.event)
            if got != tt.want {
                t.Errorf("nextState(%q, %q) = %q, want %q", tt.current, tt.event, got, tt.want)
            }
        })
    }
}
```

- [ ] **Step 2: Implement lifecycle.go**

```go
// lifecycle.go — Device lifecycle state machine
package daemon

// nextState returns the next device_status given current state and event
func nextState(current, event string) string {
    // Ignored devices stay ignored unless explicitly un-ignored
    if current == "ignored" && event != "unignore" {
        return "ignored"
    }
    // Archived devices revert to discovered on reappearance
    if current == "archived" && event == "seen" {
        return "discovered"
    }

    transitions := map[string]map[string]string{
        "discovered": {"probed": "probed"},
        "probed": {
            "ad_joined":   "ad_managed",
            "ssh_open":    "take_over_available",
            "winrm_open":  "take_over_available",
        },
        "ad_managed":          {"deploy_start": "deploying"},
        "take_over_available": {"creds_saved": "pending_deploy"},
        "pending_deploy":      {"deploy_start": "deploying"},
        "deploying": {
            "agent_heartbeat": "agent_active",
            "deploy_error":    "deploy_failed",
        },
        "deploy_failed":       {"deploy_start": "deploying", "creds_saved": "pending_deploy"},
        "agent_active":        {"heartbeat_timeout": "agent_stale"},
        "agent_stale":         {"agent_heartbeat": "agent_active", "offline_7d": "agent_offline"},
        "agent_offline":       {"agent_heartbeat": "agent_active", "archive_30d": "archived"},
        "ignored":             {"unignore": "discovered"},
    }

    if states, ok := transitions[current]; ok {
        if next, ok := states[event]; ok {
            return next
        }
    }
    return current // no valid transition, stay in current state
}
```

- [ ] **Step 3: Add archive logic to device_sync.py**

In the backend, add a periodic check (in the existing 5-min reconciliation job):
```python
# Archive devices not seen in 30 days
await conn.execute("""
    UPDATE discovered_devices
    SET device_status = 'archived'
    WHERE last_seen_at < NOW() - INTERVAL '30 days'
        AND device_status NOT IN ('ignored', 'archived')
""")
```

- [ ] **Step 4: Run tests**

Run: `cd appliance && go test ./internal/daemon/ -run TestLifecycle -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add appliance/internal/daemon/lifecycle.go appliance/internal/daemon/lifecycle_test.go mcp-server/app/dashboard_api/device_sync.py
git commit -m "feat: device lifecycle state machine + 30-day auto-archive"
```

---

## Task 12: Integration Test + Deploy to northvalley-linux

**Files:**
- No new files — this is a deployment verification task

- [ ] **Step 1: Deploy backend changes via git push**

```bash
git push origin main
```

Wait for CI/CD to deploy to VPS.

- [ ] **Step 2: Run migration on VPS**

Verify migration 096 applied:
```bash
ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -c \"SELECT column_name FROM information_schema.columns WHERE table_name='discovered_devices' AND column_name IN ('device_status','os_fingerprint','probe_ssh','ad_joined') ORDER BY column_name\""
```

Expected: 4 rows returned

- [ ] **Step 3: Build daemon with probe support**

```bash
cd appliance && make build-linux
```

Deploy to physical appliance via fleet order or direct update.

- [ ] **Step 4: Verify probe sweep runs**

Check appliance logs for probe results:
```bash
ssh root@192.168.88.241 "journalctl -u appliance-daemon --since '5 minutes ago' | grep -i probe"
```

Expected: Probe results showing discovered devices with OS classification

- [ ] **Step 5: Verify northvalley-linux appears in UI**

Navigate to Site Devices page for the physical appliance site. northvalley-linux should appear as "Discovered — Ubuntu" with a "Take Over" button.

- [ ] **Step 6: Take Over northvalley-linux**

Click "Take Over" on northvalley-linux → enter SSH credentials → verify agent deploys successfully.

- [ ] **Step 7: Verify agent pushes checks**

Check `go_agents` table for northvalley-linux:
```bash
ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -c \"SELECT agent_id, hostname, status, checks_passed, checks_total FROM go_agents WHERE hostname LIKE '%northvalley%'\""
```

Expected: Active agent with checks flowing

- [ ] **Step 8: Commit any fixes discovered during integration**

```bash
git add -A && git commit -m "fix: integration test fixes for auto-discovery deploy"
```

---

## Task Summary

| Task | Description | Type |
|------|-------------|------|
| 1 | DB migration — discovered_devices extensions | Backend |
| 2 | OS probing — SSH banner + WinRM detection | Go daemon |
| 3 | Enhanced ARP discovery + probe sweep integration | Go daemon |
| 4 | Backend accepts probe data in device sync | Backend |
| 5 | Checkin response — pending_deploys + deploy_results | Backend + Go |
| 6 | SSH agent deployment (Linux/macOS) | Go daemon |
| 7 | Auto-deploy orchestrator — process pending deploys | Go daemon |
| 8 | "Take Over" endpoint | Backend |
| 9 | Frontend — coverage tier + Take Over button | Frontend |
| 10 | Rogue device alerting | Go daemon + Backend |
| 11 | Device lifecycle state machine | Go daemon + Backend |
| 12 | Integration test + deploy to northvalley-linux | Verification |

**Dependencies:** Task 1 must be first (schema). Tasks 2-3 are independent of 4-5. Task 6 before 7. Tasks 8-9 can run in parallel. Task 10-11 are independent. Task 12 is last.
