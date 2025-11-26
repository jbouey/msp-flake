# Compliance Agent Web UI

Local web dashboard for compliance monitoring, evidence browsing, and audit trail verification.

## Overview

The Web UI provides visual proof of compliance status per CLAUDE.md sections 14-15:
- Real-time compliance dashboard with HIPAA control status
- Evidence bundle browser with search/filter
- Report generation (PDF/HTML)
- Audit log viewer with hash chain verification
- Data Flywheel metrics (L1/L2/L3 resolution rates)

## Quick Start

### Enable in NixOS Configuration

```nix
services.compliance-agent = {
  enable = true;
  siteId = "clinic-001";

  # Enable web dashboard
  webUI = {
    enable = true;
    port = 8080;
    bindAddress = "0.0.0.0";  # All interfaces (or "127.0.0.1" for local only)
  };
};
```

### Rebuild and Access

```bash
# Rebuild NixOS
nixos-rebuild switch

# Check service status
systemctl status compliance-agent-web

# Access dashboard
open http://<agent-ip>:8080
```

## Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `webUI.enable` | `false` | Enable the web dashboard |
| `webUI.port` | `8080` | Port to listen on |
| `webUI.bindAddress` | `127.0.0.1` | Bind address (`0.0.0.0` for all interfaces) |

## Dashboard Pages

### 1. Main Dashboard (`/`)

**KPI Cards:**
- Compliance Score (0-100%)
- Controls Passing/Failed
- Average MTTR (Mean Time To Resolution)

**Incident Summary (24h):**
- Auto-resolved count
- Escalated count
- L1/L2/L3 breakdown with progress bars

**Data Flywheel:**
- Patterns tracked
- Promotion candidates
- Rules promoted to L1
- Resolution breakdown (30d)

**HIPAA Controls Table:**
- Control name and description
- HIPAA citation (164.308, 164.312, etc.)
- Status badge (pass/warn/fail)
- Last checked timestamp
- Evidence bundle count
- Auto-fix count

### 2. Evidence Browser (`/evidence`)

**Stats Cards:**
- Total bundles
- Successful/Failed counts
- Check types

**Filters:**
- By check type (patching, backup, logging, etc.)
- By outcome (success, failed, warning)

**Evidence Table:**
- Bundle ID (truncated hash)
- Check type
- Outcome badge
- HIPAA controls
- Timestamp
- Download link

### 3. Reports (`/reports`)

**Report Types:**
- Daily Summary
- Weekly Executive
- Monthly Compliance Packet

**Formats:**
- HTML (web view)
- PDF (print-ready)

**Automation Schedule:**
- Daily at 6:00 AM
- Weekly on Monday
- Monthly on 1st

### 4. Audit Log (`/audit`)

**Hash Chain Integrity:**
- Real-time verification status
- Total links count
- First/last link timestamps

**Audit Log Table:**
- Timestamp
- Hash (truncated)
- Previous hash
- Log count

**Architecture Diagram:**
- Visual explanation of hash chain linking

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | Overall compliance status |
| `/api/incidents/summary` | GET | 24h incident metrics |
| `/api/flywheel` | GET | Data flywheel stats |
| `/api/controls` | GET | HIPAA control status |
| `/api/evidence` | GET | Evidence bundle list |
| `/api/hash-chain/status` | GET | Hash chain verification |
| `/api/reports/generate` | GET | Generate report |

### Example API Response

```bash
curl http://localhost:8080/api/status
```

```json
{
  "status": "warning",
  "score": 75.0,
  "last_check": "2025-11-24T10:30:00Z",
  "checks_passed": 6,
  "checks_failed": 1,
  "checks_warning": 1
}
```

## Security

### Firewall

When `webUI.enable = true`, the NixOS module automatically adds:

```nft
chain input {
  tcp dport 8080 accept
}
```

### Access Control

- Default bind to `127.0.0.1` (localhost only)
- Set `bindAddress = "0.0.0.0"` to allow network access
- Consider putting behind reverse proxy (nginx) with auth for production

### Systemd Hardening

The web service runs with the same hardening as the main agent:
- `ProtectSystem = strict`
- `NoNewPrivileges = true`
- `PrivateDevices = true`
- Restricted capabilities and system calls

## Development

### Run Locally

```bash
cd /Users/dad/Documents/Msp_Flakes/packages/compliance-agent

# Install dependencies
pip install -e ".[dev]"

# Run web UI
compliance-web --host 127.0.0.1 --port 8080 \
  --evidence-dir ./test_evidence \
  --db-path ./test_incidents.db \
  --site-id test-clinic \
  --host-id test-host
```

### CLI Options

```
usage: compliance-web [-h] [--host HOST] [--port PORT]
                      [--evidence-dir EVIDENCE_DIR] [--db-path DB_PATH]
                      [--site-id SITE_ID] [--host-id HOST_ID]

Options:
  --host          Host to bind to (default: 0.0.0.0)
  --port          Port to bind to (default: 8080)
  --evidence-dir  Evidence directory path
  --db-path       Incident database path
  --site-id       Site identifier
  --host-id       Host identifier
```

## Templates

HTML templates are in `src/compliance_agent/web_templates/`:

| Template | Purpose |
|----------|---------|
| `base.html` | Base layout with nav, CSS, dark theme |
| `dashboard.html` | Main dashboard |
| `evidence.html` | Evidence browser |
| `reports.html` | Report generation |
| `audit.html` | Audit log viewer |

### Theme

Dark theme with CSS variables:
- `--bg-primary`: #1a1a2e
- `--bg-secondary`: #16213e
- `--accent-green`: #4ade80
- `--accent-yellow`: #facc15
- `--accent-red`: #f87171
- `--accent-blue`: #60a5fa

Print-friendly styles included via `@media print`.

## HIPAA Compliance

The Web UI supports HIPAA Security Rule compliance:

| Control | Citation | Feature |
|---------|----------|---------|
| Audit Controls | 164.312(b) | Hash chain verification, audit log |
| Information System Activity Review | 164.308(a)(1)(ii)(D) | Dashboard, evidence browser |
| Security Incident Procedures | 164.308(a)(6) | Incident summary, L1/L2/L3 tracking |
| Contingency Plan | 164.308(a)(7) | Backup status in controls table |

**PHI Disclaimer:** This dashboard displays system metadata only. No Protected Health Information (PHI) is processed, stored, or displayed.

## Windows Integration

### Connecting to Windows Servers

The web UI can pull compliance data from Windows servers via WinRM.

#### Configure Windows Target

```python
# When creating the web UI
from compliance_agent.web_ui import ComplianceWebUI

ui = ComplianceWebUI(
    site_id="clinic-001",
    windows_targets=[
        {
            "hostname": "127.0.0.1",  # Or Windows server IP
            "port": 5985,             # WinRM HTTP (5986 for HTTPS)
            "username": "MSP\\vagrant",
            "password": "vagrant",
            "use_ssl": False
        }
    ]
)
```

#### Via SSH Tunnel (for remote VMs)

```bash
# Create tunnel to Windows VM
ssh -f -N -L 5985:127.0.0.1:5985 user@remote-host

# Then connect to localhost:5985
```

#### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/windows/targets` | GET | List configured Windows targets |
| `/api/windows/collect` | POST | Trigger compliance collection |
| `/api/windows/health/{hostname}` | GET | Check Windows target health |

#### Example: Trigger Collection

```bash
curl -X POST http://localhost:8080/api/windows/collect
```

Response:
```json
{
  "score": 75.0,
  "status": "warning",
  "total_checks": 4,
  "passed": 3,
  "failed": 1,
  "by_target": {
    "192.168.1.100": {"passed": 3, "failed": 1}
  }
}
```

#### Windows Checks Available

| Check ID | Name | HIPAA Control |
|----------|------|---------------|
| RB-WIN-PATCH-001 | Windows Patch Compliance | 164.308(a)(5)(ii)(B) |
| RB-WIN-AV-001 | Windows Defender Health | 164.308(a)(5)(ii)(B) |
| RB-WIN-BACKUP-001 | Windows Backup Status | 164.308(a)(7)(ii)(A) |
| RB-WIN-AUDIT-001 | Windows Audit Logging | 164.312(b) |
| RB-WIN-FW-001 | Windows Firewall | 164.312(a)(1) |
| RB-WIN-ENC-001 | BitLocker Encryption | 164.312(a)(2)(iv) |

### Requirements

- `pywinrm` package: `pip install pywinrm`
- WinRM enabled on Windows target
- Network access to WinRM port (5985/5986)

### WinRM Setup on Windows

```powershell
# Enable WinRM
Enable-PSRemoting -Force

# Allow unencrypted for HTTP (dev only)
Set-Item WSMan:\localhost\Service\AllowUnencrypted -Value $true

# Allow Basic auth (dev only)
Set-Item WSMan:\localhost\Service\Auth\Basic -Value $true

# For production, use HTTPS with certificates
```

## Troubleshooting

### Service Won't Start

```bash
# Check logs
journalctl -u compliance-agent-web -f

# Common issues:
# - Port already in use
# - Missing evidence directory
# - Database file permissions
```

### Dashboard Shows No Data

1. Verify main agent is running: `systemctl status compliance-agent`
2. Check evidence directory has bundles: `ls /var/lib/compliance-agent/evidence/`
3. Check incident database exists: `ls /var/lib/compliance-agent/incidents.db`

### Hash Chain Shows "No Chain"

The hash chain is populated by the main agent. Ensure:
1. Agent has been running and producing evidence
2. Hash chain file exists at `/var/lib/compliance-agent/hash-chain/chain.jsonl`
