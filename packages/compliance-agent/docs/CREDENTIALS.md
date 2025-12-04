# MSP Compliance Agent - Credentials & Access Reference

**Last Updated:** 2025-11-23
**Status:** Development/Testing Environment

---

## Quick Reference

| System | Host | Port | Username | Password |
|--------|------|------|----------|----------|
| Windows DC (Domain) | 127.0.0.1 (via tunnel) | 55985 (WinRM) | MSP\\vagrant | vagrant |
| Windows DC (Local) | 127.0.0.1 (via tunnel) | 55985 (WinRM) | .\\Administrator | Vagrant123! |
| Remote Mac (VMs) | 174.178.63.139 | 22 (SSH) | jrelly | SSH key |
| MCP Server | localhost | 8000 | - | - |
| Redis Queue | localhost | 6379 | - | - |

---

## 1. Windows Domain Controller (VirtualBox on Remote Mac)

### Connection Architecture

```
Local Mac ──SSH Tunnel──▶ Remote Mac (174.178.63.139) ──NAT──▶ Windows DC
localhost:55985          127.0.0.1:55985                      guest:5985
```

### VM & Domain Details

| Property | Value |
|----------|-------|
| **VM Name** | win-test-vm_default_1763941055603_9826 |
| **Computer Name** | WINTEST |
| **FQDN** | wintest.msp.local |
| **OS** | Windows Server 2022 (64-bit) |
| **Role** | Active Directory Domain Controller |
| **Domain** | msp.local |
| **NetBIOS Name** | MSP |
| **Domain Mode** | Windows2016Domain |
| **WinRM Port** | 5985 (guest) → 55985 (NAT forwarded) |
| **Domain User** | MSP\\vagrant |
| **Domain Password** | vagrant |
| **Local Admin** | .\\Administrator |
| **Local Admin Password** | Vagrant123! |
| **DSRM Password** | Vagrant123! |
| **Transport** | NTLM |
| **Host Machine** | Remote Mac (174.178.63.139) |

### SSH Tunnel Setup (Required First)

```bash
# Create SSH tunnel to access Windows VM via host-only network
# NOTE: Use host-only IP (192.168.56.102:5985), NOT NAT (127.0.0.1:55985)
ssh -f -N -L 55985:192.168.56.102:5985 jrelly@174.178.63.139

# Verify tunnel is running
lsof -i:55985
```

### Environment Variables

```bash
# For running tests via SSH tunnel
export WIN_TEST_HOST="127.0.0.1:55985"
export WIN_TEST_USER="vagrant"
export WIN_TEST_PASS="vagrant"
```

### Test Connection

```bash
# From compliance-agent directory (with venv activated)
cd /Users/dad/Documents/Msp_Flakes/packages/compliance-agent
source venv/bin/activate

# Quick Python test with domain credentials
python3 << 'EOF'
import winrm

session = winrm.Session(
    'http://127.0.0.1:55985/wsman',
    auth=('MSP\\vagrant', 'vagrant'),
    transport='ntlm'
)

result = session.run_ps('whoami; (Get-ADDomain).DNSRoot')
print(result.std_out.decode().strip())
EOF
```

### AD Operations Example

```bash
# Query AD users
python3 << 'EOF'
import winrm

session = winrm.Session(
    'http://127.0.0.1:55985/wsman',
    auth=('MSP\\vagrant', 'vagrant'),
    transport='ntlm'
)

result = session.run_ps('Get-ADUser -Filter * | Select Name, SamAccountName | Format-Table')
print(result.std_out.decode())
EOF
```

### VM Management (Run on Remote Mac)

```bash
# SSH to remote Mac
ssh jrelly@174.178.63.139

# Check VM status
/Applications/VirtualBox.app/Contents/MacOS/VBoxManage list runningvms

# Get VM details
/Applications/VirtualBox.app/Contents/MacOS/VBoxManage showvminfo "win-test-vm_default_1763941055603_9826" | grep -E "State:|NIC"

# Start VM if not running
/Applications/VirtualBox.app/Contents/MacOS/VBoxManage startvm "win-test-vm_default_1763941055603_9826" --type headless

# Stop VM
/Applications/VirtualBox.app/Contents/MacOS/VBoxManage controlvm "win-test-vm_default_1763941055603_9826" poweroff
```

---

## 2. MCP Server (Development)

### Connection Details

| Property | Value |
|----------|-------|
| **URL** | http://localhost:8000 |
| **Protocol** | HTTP (dev) / HTTPS with mTLS (prod) |
| **Framework** | FastAPI |

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/orders` | GET | Poll for orders |
| `/evidence` | POST | Submit evidence bundle |
| `/tools` | GET | List available tools |

### Start Server (Development)

```bash
cd /Users/dad/Documents/Msp_Flakes/mcp-server
python -m uvicorn server:app --reload --port 8000
```

---

## 3. Redis Queue (Development)

### Connection Details

| Property | Value |
|----------|-------|
| **Host** | localhost |
| **Port** | 6379 |
| **Database** | 0 |
| **Auth** | None (dev) |

### Test Connection

```bash
# Using redis-cli
redis-cli ping
# Expected: PONG

# Check keys
redis-cli keys "*"
```

### Start Redis (if needed)

```bash
# macOS with Homebrew
brew services start redis

# Or run directly
redis-server
```

---

## 4. Remote Mac (VM Host)

### SSH Access

| Property | Value |
|----------|-------|
| **Host** | 174.178.63.139 |
| **Port** | 22 |
| **User** | jrelly (SSH key auth) |

```bash
# SSH connection (uses SSH key)
ssh jrelly@174.178.63.139

# Test connection
ssh jrelly@174.178.63.139 "echo connected"
```

### VMs Running on Remote Mac

| VM | Purpose | Status |
|----|---------|--------|
| nixos-24.05... | NixOS test environment | Running |
| mcp-server | MCP server VM | Running |
| win-test-vm | Windows Server 2022 DC (msp.local) | Running |

### Key Paths

| Key | Path |
|-----|------|
| SSH Private Key | ~/.ssh/id_ed25519 |
| SSH Public Key | ~/.ssh/id_ed25519.pub |

---

## 5. Test Certificates & Keys (Development)

### Paths (Compliance Agent)

```bash
# State directory
/var/lib/msp-compliance-agent/

# Evidence storage
/var/lib/msp-compliance-agent/evidence/

# Queue database
/var/lib/msp-compliance-agent/queue.db

# Signing keys (test)
/tmp/msp-test/signing_key
/tmp/msp-test/client_cert.pem
/tmp/msp-test/client_key.pem
```

### Generate Test Keys

```bash
# Ed25519 signing key
openssl genpkey -algorithm ED25519 -out signing_key

# Self-signed cert for testing
openssl req -x509 -newkey rsa:4096 -keyout client_key.pem \
  -out client_cert.pem -days 365 -nodes \
  -subj "/CN=test-client"
```

---

## 6. Python Environment

### Virtual Environment

```bash
# Location
/Users/dad/Documents/Msp_Flakes/packages/compliance-agent/venv/

# Activate
source venv/bin/activate

# Python version
/usr/local/bin/python3.13
```

### Required Packages

```bash
pip install pywinrm pydantic pytest pytest-asyncio pyyaml
```

---

## 7. Environment Variables Reference

### Test Environment

```bash
# Windows DC (via SSH tunnel)
export WIN_TEST_HOST="127.0.0.1:55985"
export WIN_TEST_USER="MSP\\vagrant"
export WIN_TEST_PASS="vagrant"
export WIN_TEST_DOMAIN="msp.local"

# Agent Config (from NixOS module)
export SITE_ID="test-site-001"
export HOST_ID="test-host-001"
export DEPLOYMENT_MODE="direct"
export MCP_URL="http://localhost:8000"
export BASELINE_PATH="/etc/msp/baseline.yaml"
export STATE_DIR="/var/lib/msp-compliance-agent"
export CLIENT_CERT_FILE="/path/to/client_cert.pem"
export CLIENT_KEY_FILE="/path/to/client_key.pem"
export SIGNING_KEY_FILE="/path/to/signing_key"
export POLL_INTERVAL="60"
export LOG_LEVEL="INFO"
```

---

## 8. Project Paths

| Description | Path |
|-------------|------|
| Project Root | /Users/dad/Documents/Msp_Flakes |
| Compliance Agent | /Users/dad/Documents/Msp_Flakes/packages/compliance-agent |
| Agent Source | /Users/dad/Documents/Msp_Flakes/packages/compliance-agent/src/compliance_agent |
| Tests | /Users/dad/Documents/Msp_Flakes/packages/compliance-agent/tests |
| NixOS Module | /Users/dad/Documents/Msp_Flakes/modules/compliance-agent.nix |
| MCP Server | /Users/dad/Documents/Msp_Flakes/mcp-server |

---

## 9. Running Tests

```bash
cd /Users/dad/Documents/Msp_Flakes/packages/compliance-agent
source venv/bin/activate

# Full test suite (161 tests)
python -m pytest tests/ -v --tb=short

# Core tests only (excludes Windows integration)
python -m pytest tests/ -v --tb=short --ignore=tests/test_windows_integration.py

# Specific test files
python -m pytest tests/test_agent.py -v
python -m pytest tests/test_auto_healer.py -v
python -m pytest tests/test_auto_healer_integration.py -v

# Windows integration (requires SSH tunnel first)
# Step 1: Create SSH tunnel
ssh -f -N -L 55985:127.0.0.1:55985 jrelly@174.178.63.139

# Step 2: Run tests with domain credentials
export WIN_TEST_HOST="127.0.0.1:55985"
export WIN_TEST_USER="MSP\\vagrant"
export WIN_TEST_PASS="vagrant"
python -m pytest tests/test_windows_integration.py -v
```

---

## 10. Security Notes

### Development vs Production

| Setting | Development | Production |
|---------|-------------|------------|
| WinRM Auth | NTLM (Domain) | Kerberos/HTTPS |
| AD Domain | msp.local (test) | Production AD |
| MCP Server | HTTP | HTTPS + mTLS |
| Redis | No auth | AUTH + TLS |
| Secrets | Plaintext | SOPS encrypted |

### Never Commit

- Passwords or API keys
- Private keys (*.pem, *_key)
- .env files with secrets
- SOPS encrypted files without proper key management

---

## Troubleshooting

### Windows VM Not Responding

```bash
# Check VM status
VBoxManage showvminfo "win-test-vm" | grep State

# Check WinRM port
nc -zv 192.168.56.10 5985

# Restart VM
vagrant reload
```

### MCP Server Connection Failed

```bash
# Check if running
curl http://localhost:8000/health

# Check port
lsof -i :8000
```

### Redis Connection Failed

```bash
# Check service
brew services list | grep redis

# Start if needed
brew services start redis
```

---

**Document Maintainer:** MSP Automation Team
**Classification:** Internal Development Use Only
