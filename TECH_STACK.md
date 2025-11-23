# MSP HIPAA Compliance Platform - Technology Stack

**Last Updated:** November 7, 2025
**Version:** Phase 2 Day 10 (Agent Core Implementation - 71% Complete)

---

## Overview

This document describes the complete technology stack for the MSP HIPAA Compliance Platform, including:
- **Phase 1:** NixOS flake scaffold with systemd hardening (Week 5 - COMPLETE)
- **Phase 2:** Python compliance agent core implementation (Week 6 - IN PROGRESS)
- **Demo Environment:** Docker-based quick-start (COMPLETE)
- **Production Target:** NixOS-based deployment (IN PROGRESS)

**Current Implementation Status:**
- Lines of Code: 4,239 production + ~2,910 test (112 tests)
- Test Coverage: 69% test/code ratio
- Modules Complete: Config, Crypto, Utils, Models, Evidence, Queue, MCP Client, Drift, Healing
- Next: Main Agent Loop (Day 11)

---

## Architecture Layers

```
┌─────────────────────────────────────────────────────────────┐
│                     PRESENTATION LAYER                       │
│  Grafana Dashboards • Prometheus UI • FastAPI Docs          │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                     APPLICATION LAYER                        │
│  MCP Server • Planner • Executor • Guardrails • CLI Tools   │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                    COMPLIANCE AGENT LAYER                    │
│  Config • Crypto • Evidence • Drift • Healing • Queue        │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                        DATA LAYER                            │
│  Prometheus • SQLite • Evidence Registry • WORM Storage      │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                   INFRASTRUCTURE LAYER                       │
│  Docker (Demo) • NixOS (Production) • Cloud Provider         │
└─────────────────────────────────────────────────────────────┘
```
"
---

## Core Technologies

### 1. Programming Languages

| Language | Usage | Version | Why Chosen |
|----------|-------|---------|------------|
| **Python** | Primary backend, MCP server, automation scripts | 3.11+ | Rich ecosystem, FastAPI, LLM SDKs |
| **Nix** | Infrastructure configuration, deterministic builds | 2.18+ | Reproducibility, declarative config |
| **Bash** | Deployment scripts, system integration | 5.0+ | Universal availability, system glue |
| **YAML** | Configuration files, runbooks, dashboards | 1.2 | Human-readable, widely supported |
| **SQL** | Evidence registry, audit logs | SQLite 3 | Embedded, WORM-compatible |

---

### 2. Compliance Agent Core (Python)

**Location:** `packages/compliance-agent/src/compliance_agent/`
**Language:** Python 3.11+
**Dependencies:** Pydantic 2.5+, cryptography 41.0+, aiohttp 3.9+

#### Module Architecture

```python
compliance_agent/
├── __init__.py           # Package initialization
├── config.py             # Configuration management (321 lines)
├── crypto.py             # Ed25519 signing/verification (338 lines)
├── utils.py              # Utilities and helpers (361 lines)
├── models.py             # Pydantic data models (421 lines)
├── evidence.py           # Evidence generation (398 lines)
├── queue.py              # Offline queue with SQLite (436 lines)
├── mcp_client.py         # MCP communication with mTLS (448 lines)
├── drift.py              # Drift detection (6 checks, 629 lines)
├── healing.py            # Self-healing (6 remediations, 887 lines)
└── agent.py              # Main loop (TODO - Day 11)
```

#### Configuration Management (`config.py`)

**Purpose:** Type-safe configuration with environment variable mapping

**Key Components:**
```python
class AgentConfig(BaseModel):
    # Identity (4 options)
    site_id: str
    host_id: str
    deployment_mode: Literal["reseller", "direct"]
    reseller_id: Optional[str]

    # MCP Server (3 options)
    mcp_url: str
    mcp_api_key_file: Path
    mcp_poll_interval_sec: int = 60

    # Evidence (2 options)
    evidence_dir: Path
    evidence_retention_days: int = 90

    # Maintenance (2 options)
    maintenance_window: str = "02:00-04:00"
    timezone: str = "UTC"

    # Secrets (3 options)
    client_cert_file: Optional[Path]
    client_key_file: Optional[Path]
    signing_key_file: Path

    # Baseline (3 options)
    baseline_path: Path
    policy_version: str
    auto_apply_updates: bool = False

    # Logging (2 options)
    log_level: str = "INFO"
    log_file: Optional[Path]

    # Advanced (8 options)
    ntp_max_skew_ms: int = 5000
    backup_path: Optional[Path]
    restore_test_schedule: str = "weekly"
    av_health_check: bool = True
    firewall_enforce: bool = True
    encryption_verify: bool = True
    service_health_checks: List[str] = []
    custom_checks_dir: Optional[Path]

    # Computed properties
    @property
    def maintenance_window_start(self) -> time:
        return parse_time(self.maintenance_window.split('-')[0])

    @property
    def maintenance_window_end(self) -> time:
        return parse_time(self.maintenance_window.split('-')[1])
```

**Validation:**
- Deployment mode enforcement (reseller requires reseller_id)
- Maintenance window format validation (HH:MM-HH:MM)
- File existence checks for secrets and baseline
- Log level validation (DEBUG, INFO, WARNING, ERROR, CRITICAL)

**Loading from Environment:**
```python
def load_config() -> AgentConfig:
    """Load configuration from environment variables (set by NixOS)"""
    config_dict = {
        'site_id': os.environ['SITE_ID'],
        'host_id': os.environ.get('HOST_ID', socket.gethostname()),
        'deployment_mode': os.environ.get('DEPLOYMENT_MODE', 'reseller'),
        # ... 24 more options
    }
    return AgentConfig(**config_dict)
```

#### Cryptography (`crypto.py`)

**Purpose:** Ed25519 signatures for evidence bundles and integrity verification

**Key Classes:**

```python
class Ed25519Signer:
    """Sign data with Ed25519 private key"""

    def __init__(self, private_key_path: Path):
        self._private_key = self._load_private_key()

    def sign(self, data: Union[bytes, str, Dict[Any, Any]]) -> bytes:
        """Returns 64-byte Ed25519 signature"""

    def sign_file(self, file_path: Path) -> bytes:
        """Sign file contents, return signature"""

    def get_public_key_bytes(self) -> bytes:
        """Returns 32-byte public key"""

    def get_public_key_pem(self) -> str:
        """Returns PEM-encoded public key"""

class Ed25519Verifier:
    """Verify Ed25519 signatures"""

    def __init__(self, public_key: Union[bytes, str, Path]):
        self._public_key = self._load_public_key(public_key)

    def verify(self, data: Union[bytes, str, Dict[Any, Any]],
               signature: bytes) -> bool:
        """Verify signature, returns True if valid"""

    def verify_file(self, file_path: Path, signature: bytes) -> bool:
        """Verify file signature"""

# Utility functions
def generate_keypair() -> tuple[bytes, bytes]:
    """Generate new Ed25519 keypair (for testing)"""

def sha256_hash(data: Union[bytes, str, Path]) -> str:
    """Compute SHA256 hash (hex string)"""

def verify_hash(data: Union[bytes, str, Path],
                expected_hash: str) -> bool:
    """Verify SHA256 hash"""
```

**Security Features:**
- Modern Ed25519 algorithm (RFC 8032)
- 64-byte signatures (compact, efficient)
- Supports PEM and raw byte formats
- No secret key material logged
- Constant-time verification (via cryptography lib)

#### Utilities (`utils.py`)

**Purpose:** Helper functions for maintenance windows, commands, NTP, I/O

**Key Components:**

```python
class MaintenanceWindow:
    """Maintenance window with midnight crossing support"""

    def __init__(self, start: time, end: time):
        self.start = start
        self.end = end

    def is_in_window(self, now: Optional[datetime] = None) -> bool:
        """Check if current time is in maintenance window"""

    def next_window_start(self, now: Optional[datetime] = None) -> datetime:
        """Calculate next window start time"""

    def time_until_window(self, now: Optional[datetime] = None) -> timedelta:
        """Time until next maintenance window"""

class CommandResult:
    """Structured command execution result"""
    exit_code: int
    stdout: str
    stderr: str
    duration_sec: float
    success: bool

# Utility functions
def apply_jitter(base_value: float, jitter_pct: float = 10.0) -> float:
    """Add random jitter (±10%) to value"""

async def get_ntp_offset_ms() -> Optional[int]:
    """Query NTP offset via timedatectl"""

async def is_system_running() -> bool:
    """Check systemctl is-system-running"""

async def get_nixos_generation() -> Optional[int]:
    """Get current NixOS generation number"""

async def run_command(cmd: list[str],
                     timeout: Optional[int] = None,
                     check: bool = True) -> CommandResult:
    """Async command execution with timeout"""

async def read_secret_file(path: Path) -> str:
    """Read secret file (async)"""

async def write_json_file(path: Path, data: dict):
    """Write JSON file (async, atomic)"""

async def read_json_file(path: Path) -> dict:
    """Read JSON file (async)"""

def setup_logging(log_level: str = "INFO",
                 log_file: Optional[Path] = None):
    """Configure logging with format and level"""
```

**Key Features:**
- Maintenance windows handle midnight crossing (22:00-02:00)
- Jitter for poll intervals (prevents thundering herd)
- NTP offset monitoring (HIPAA timestamp accuracy)
- Async command execution with timeout
- Atomic JSON writes (write to temp, then rename)

#### Data Models (`models.py`)

**Purpose:** Pydantic models for type-safe data structures

**Key Models:**

```python
class ActionTaken(BaseModel):
    """Single remediation action within evidence bundle"""
    step: int = Field(..., ge=1)
    action: str
    command: Optional[str] = None
    exit_code: Optional[int] = None
    duration_sec: Optional[float] = Field(default=None, ge=0)
    result: Optional[str] = None

class EvidenceBundle(BaseModel):
    """Complete evidence bundle for compliance audit trail"""
    # Metadata
    version: str = "1.0"
    bundle_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    site_id: str
    host_id: str
    deployment_mode: Literal["reseller", "direct"]
    reseller_id: Optional[str] = None

    # Timestamps
    timestamp_start: datetime
    timestamp_end: datetime

    # Policy & Configuration
    policy_version: str
    ruleset_hash: Optional[str] = None
    nixos_revision: Optional[str] = None
    derivation_digest: Optional[str] = None
    ntp_offset_ms: Optional[int] = None

    # Check Information
    check: str  # patching, av_health, backup, logging, firewall, encryption
    hipaa_controls: Optional[List[str]] = None

    # State Capture
    pre_state: Dict[str, Any] = Field(default_factory=dict)
    post_state: Dict[str, Any] = Field(default_factory=dict)

    # Actions
    action_taken: List[ActionTaken] = Field(default_factory=list)

    # Rollback
    rollback_available: bool = False
    rollback_generation: Optional[int] = None

    # Outcome
    outcome: Literal["success", "failed", "reverted", "deferred", "alert",
                     "rejected", "expired"]
    error: Optional[str] = None

    # Order Information
    order_id: Optional[str] = None
    runbook_id: Optional[str] = None

    # Validators
    @validator('timestamp_end')
    def validate_end_after_start(cls, v, values):
        if 'timestamp_start' in values and v < values['timestamp_start']:
            raise ValueError('timestamp_end must be after timestamp_start')
        return v

    @property
    def duration_sec(self) -> float:
        return (self.timestamp_end - self.timestamp_start).total_seconds()

class MCPOrder(BaseModel):
    """MCP order from central server"""
    order_id: str
    runbook_id: str
    params: Dict[str, Any] = Field(default_factory=dict)
    nonce: str
    ttl: int = Field(..., ge=60)
    issued_at: datetime
    signature: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        age_seconds = (datetime.utcnow() - self.issued_at).total_seconds()
        return age_seconds > self.ttl

class DriftResult(BaseModel):
    """Result of drift detection check"""
    check: str
    drifted: bool
    pre_state: Dict[str, Any] = Field(default_factory=dict)
    recommended_action: Optional[str] = None
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    hipaa_controls: Optional[List[str]] = None

class RemediationResult(BaseModel):
    """Result of remediation attempt"""
    check: str
    outcome: Literal["success", "failed", "reverted", "deferred", "alert"]
    pre_state: Dict[str, Any] = Field(default_factory=dict)
    post_state: Dict[str, Any] = Field(default_factory=dict)
    actions: List[ActionTaken] = Field(default_factory=list)
    error: Optional[str] = None
    rollback_available: bool = False
    rollback_generation: Optional[int] = None

class QueuedEvidence(BaseModel):
    """Evidence bundle queued for upload to MCP"""
    id: int
    bundle_id: str
    bundle_path: str
    signature_path: str
    created_at: datetime
    retry_count: int = Field(default=0, ge=0)
    last_error: Optional[str] = None
```

**Validation Features:**
- Type safety with Pydantic
- Required fields enforced
- Enums for restricted values
- Regex validation for patterns
- Custom validators for business logic
- JSON serialization with datetime handling

#### Evidence Generation (`evidence.py`)

**Purpose:** Create, store, verify, and manage evidence bundles

**Key Class:**

```python
class EvidenceGenerator:
    """Generate and store evidence bundles"""

    def __init__(self, config: AgentConfig, signer: Ed25519Signer):
        self.config = config
        self.signer = signer
        self.evidence_dir = config.evidence_dir

    async def create_evidence(
        self,
        check: str,
        outcome: str,
        pre_state: Dict[str, Any],
        post_state: Optional[Dict[str, Any]] = None,
        actions: Optional[List[ActionTaken]] = None,
        error: Optional[str] = None,
        timestamp_start: Optional[datetime] = None,
        timestamp_end: Optional[datetime] = None,
        hipaa_controls: Optional[List[str]] = None,
        rollback_available: bool = False,
        rollback_generation: Optional[int] = None,
        order_id: Optional[str] = None,
        runbook_id: Optional[str] = None,
        ntp_offset_ms: Optional[int] = None,
        nixos_revision: Optional[str] = None,
        derivation_digest: Optional[str] = None,
        ruleset_hash: Optional[str] = None
    ) -> EvidenceBundle:
        """Create evidence bundle with all metadata"""

    async def store_evidence(
        self,
        bundle: EvidenceBundle,
        sign: bool = True
    ) -> tuple[Path, Optional[Path]]:
        """
        Store evidence bundle to disk

        Structure: /var/lib/compliance-agent/evidence/YYYY/MM/DD/<uuid>/
        Files: bundle.json + bundle.sig (if signed)
        """

    async def verify_evidence(
        self,
        bundle_path: Path,
        signature_path: Optional[Path] = None
    ) -> bool:
        """Verify evidence bundle signature"""

    async def load_evidence(self, bundle_id: str) -> Optional[EvidenceBundle]:
        """Load evidence bundle from disk by ID"""

    async def list_evidence(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        check_type: Optional[str] = None,
        outcome: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[EvidenceBundle]:
        """List evidence bundles matching criteria"""

    async def prune_old_evidence(
        self,
        retention_count: int,
        retention_days: int
    ) -> int:
        """Prune old evidence bundles (keeps last N, never <retention_days)"""

    async def get_evidence_stats(self) -> Dict[str, Any]:
        """Get statistics about stored evidence"""
```

**Storage Structure:**
```
/var/lib/compliance-agent/evidence/
├── 2025/
│   ├── 11/
│   │   ├── 05/
│   │   │   ├── abc123-uuid/
│   │   │   │   ├── bundle.json
│   │   │   │   └── bundle.sig
│   │   │   └── def456-uuid/
│   │   │       ├── bundle.json
│   │   │       └── bundle.sig
│   │   └── 06/
│   │       └── ...
│   └── 12/
│       └── ...
```

**Evidence Features:**
- Date-based directory organization (YYYY/MM/DD)
- Unique bundle IDs (UUID v4)
- Detached signatures (bundle.sig separate from bundle.json)
- Comprehensive metadata capture
- Query and filter capabilities
- Automatic pruning with retention policies
- Statistics and reporting

#### Test Coverage

**Test Files:**
```
tests/
├── __init__.py
├── test_crypto.py        # 10 tests, 232 lines
├── test_utils.py         # 9 tests, 187 lines
├── test_evidence.py      # 14 tests, 310 lines
├── test_queue.py         # 16 tests, 441 lines
├── test_mcp_client.py    # 15 tests, 470 lines
├── test_drift.py         # 25 tests, 570 lines
└── test_healing.py       # 23 tests, ~700 lines
```

**Test Statistics:**
- **Total Tests:** 112
- **Total Test Code:** ~2,910 lines
- **Test/Code Ratio:** 69%
- **Framework:** pytest + pytest-asyncio

**Coverage Areas:**
- Keypair generation and key formats
- Signing and verification (bytes, strings, JSON, files)
- Hash computation and verification
- Maintenance window logic (including midnight crossing)
- Command execution (success, failure, timeout)
- Evidence creation with various parameters
- Evidence storage and signature verification
- Evidence loading and querying
- Tamper detection
- Pruning and statistics
- Offline queue with SQLite WAL mode
- Queue retry logic with exponential backoff
- MCP client with mTLS authentication
- Health checks and order polling
- Evidence upload with multipart forms
- Drift detection (6 checks: patching, AV, backup, logging, firewall, encryption)
- Self-healing (6 remediations with rollback support)
- Maintenance window enforcement
- Health check verification after remediation

---

### 3. Web Framework & API

#### FastAPI (Primary Backend)

**Version:** 0.121.0+
**Purpose:** MCP server HTTP API
**Key Features:**
- Automatic OpenAPI documentation
- Pydantic data validation
- Async/await support
- Type hints throughout

**Dependencies:**
```python
fastapi==0.121.0
uvicorn[standard]==0.38.0  # ASGI server
pydantic==2.12.4           # Data validation
python-multipart==0.0.20   # Form parsing
```

**Endpoints:**
```python
GET  /                  # Service info
GET  /health            # Health check
GET  /status            # Compliance status
GET  /incidents         # List incidents
POST /incidents         # Create incident
POST /remediate         # Execute runbook
POST /reset             # Reset demo state
```

---

### 4. LLM Integration

#### OpenAI API

**Version:** 2.7.1+
**Model:** GPT-4o (gpt-4o-2024-08-06)
**Purpose:** Runbook selection, incident triage
**Token Limit:** 8K context, 2K response

**Implementation:**
```python
import openai

# Planner uses LLM to select runbook
response = openai.ChatCompletion.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": incident_prompt}],
    max_tokens=200,
    temperature=0.1  # Low temp for deterministic selection
)
```

**Future Alternatives:**
- Azure OpenAI (enterprise deployments)
- Llama 3 8B (self-hosted, cost reduction)
- Claude 3.5 Sonnet (alternative provider)

---

### 5. Metrics & Monitoring

#### Prometheus (Time-Series Database)

**Version:** Latest (2.x)
**Purpose:** Metrics storage, alerting
**Retention:** 15 days (configurable)
**Scrape Interval:** 30 seconds

**Metrics Exposed:**
```
msp_compliance_score         # 0-100 percentage
msp_control_status           # pass/warn/fail per control
msp_incidents_total          # Counter by type, severity
msp_remediations_total       # Counter by status
msp_remediation_duration     # Histogram in seconds
msp_evidence_bundles         # Counter, signed/unsigned
```

**Configuration:**
```yaml
# prometheus/prometheus.yml
global:
  scrape_interval: 30s
  evaluation_interval: 30s

scrape_configs:
  - job_name: 'msp-metrics'
    static_configs:
      - targets: ['metrics-exporter:9090']
```

#### Prometheus Client (Python Exporter)

**Version:** 0.20.0+
**Purpose:** Export custom metrics
**Port:** 9090

**Implementation:**
```python
from prometheus_client import Gauge, Counter, Histogram

# Define metrics
compliance_score = Gauge('msp_compliance_score', 'Overall compliance %')
incidents_total = Counter('msp_incidents_total', 'Total incidents',
                         ['type', 'severity'])

# Update metrics
compliance_score.set(95.0)
incidents_total.labels(type='backup', severity='high').inc()
```

---

### 6. Visualization

#### Grafana

**Version:** 12.2.1+
**Purpose:** Compliance dashboards, visualization
**Authentication:** Admin user (configurable)

**Data Sources:**
- Prometheus (primary)
- Loki (future - log aggregation)

**Dashboards:**
1. **MSP HIPAA Compliance Dashboard** (primary)
   - 10 panels
   - 30-second auto-refresh
   - Real-time incident tracking

**Provisioning:**
```yaml
# grafana/provisioning/datasources/prometheus.yml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
```

---

### 7. Event Queue & Cache

#### Redis

**Version:** 7.0+ (Alpine)
**Purpose:** Event queue, rate limiting, session storage
**Persistence:** AOF (Append-Only File)

**Use Cases:**
```python
# Rate limiting (cooldown keys)
redis.setex(f"rate:{client}:{tool}", 300, "1")  # 5-min cooldown

# Incident queue
redis.lpush("incidents:pending", json.dumps(incident))

# Session state
redis.hset(f"session:{client}", "compliance_score", 95.0)
```

**Configuration:**
```bash
redis-server --appendonly yes  # Enable persistence
```

**Future Migration:**
- NATS JetStream (for multi-tenant durability)
- Redis Cluster (for scale-out)

---

### 8. Infrastructure as Code

#### NixOS (Production Target)

**Version:** 24.05+
**Purpose:** Deterministic OS builds, reproducibility
**Package Manager:** Nix 2.18+

**Key Concepts:**
- **Flakes:** Hermetic, reproducible configurations
- **Derivations:** Build instructions with cryptographic hashing
- **Store:** `/nix/store` - content-addressed package storage

**Example Flake:**
```nix
{
  description = "MSP Client Station";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.05";
  };

  outputs = { self, nixpkgs }: {
    nixosConfigurations.msp-client = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        ./modules/log-watcher.nix
        ./modules/health-checks.nix
        ./modules/remediation-tools.nix
      ];
    };
  };
}
```

**Why NixOS:**
- ✅ Reproducible builds (same inputs → identical output)
- ✅ Atomic upgrades/rollbacks
- ✅ Cryptographic verification
- ✅ SBOM generation built-in
- ✅ Compliance-friendly (Anduril STIG exists)

#### Docker (Demo Environment)

**Version:** 24.0+
**Compose Version:** 2.x
**Purpose:** Portable demo, development

**Services:**
```yaml
services:
  prometheus:     # Metrics storage
  grafana:        # Dashboards
  redis:          # Event queue
  mcp-server:     # Backend API
  metrics-exporter: # Custom exporter
```

**Why Docker (Demo Only):**
- ✅ Fast setup (< 1 minute)
- ✅ Works on Mac/Linux/Windows
- ✅ Isolated environment
- ✅ Easy reset (`docker-compose down -v`)
- ❌ Not for production (NixOS preferred)

---

### 9. Data Validation & Schemas

#### Pydantic

**Version:** 2.12.4+
**Purpose:** Data validation, type safety, API schemas

**Note:** Comprehensive Pydantic usage documented in Section 2 (Compliance Agent Core). The compliance agent uses Pydantic extensively for:
- Configuration validation (AgentConfig)
- Evidence bundle models (EvidenceBundle, ActionTaken)
- MCP order validation (MCPOrder)
- Drift and remediation results

**Benefits:**
- Automatic FastAPI validation
- Type-safe configuration
- JSON serialization with datetime handling
- Custom validators for business logic
- Runtime validation with clear error messages

---

### 10. Security & Compliance

#### Cryptography & Signing

**Cosign** (Evidence Signing)
**Version:** 2.x+
**Purpose:** Sign evidence bundles, SBOM artifacts

```bash
# Sign evidence bundle
cosign sign-blob \
  --key /path/to/signing-key \
  --output-signature evidence.sig \
  evidence.json

# Verify signature
cosign verify-blob \
  --key /path/to/public-key \
  --signature evidence.sig \
  evidence.json
```

**GPG** (Alternative Signing)
**Version:** 2.4+
**Purpose:** Fallback signing method

**LUKS** (Full-Disk Encryption)
**Version:** 2.x
**Purpose:** Encrypt client system disks
**Algorithm:** AES-256-XTS

**SOPS** (Secrets Management)
**Version:** 3.8+
**Purpose:** Encrypt secrets in Git
**Backend:** age, AWS KMS, GCP KMS

```yaml
# .sops.yaml
creation_rules:
  - path_regex: secrets/.*\.yaml$
    age: age1xxx...
```

---

### 11. SBOM Generation

#### Syft

**Version:** Latest
**Purpose:** Software Bill of Materials generation
**Formats:** SPDX 2.3, CycloneDX

```bash
# Generate SBOM for container
syft msp-server:latest -o spdx-json > sbom.spdx.json

# Generate SBOM for NixOS system
nix-store --query --requisites /run/current-system | \
  xargs -I{} nix-store --query --hash {} > sbom.txt
```

**Future:** Native NixOS SBOM export (built-in)

---

### 12. Time Synchronization

#### Chrony (NTP Client)

**Version:** 4.x
**Purpose:** Network Time Protocol sync
**Accuracy:** ±100ms (Essential tier)

```conf
# /etc/chrony/chrony.conf
server time.nist.gov iburst
server time.cloudflare.com iburst
server pool.ntp.org iburst

maxdrift 100  # Max allowed drift (ms)
```

**Professional Tier:** GPS time source (Stratum 0)
**Enterprise Tier:** Bitcoin blockchain time anchor

---

### 13. Logging & Audit

#### Journald (System Logs)

**Version:** Built into systemd
**Purpose:** Structured logging
**Retention:** 90 days (configurable)

```bash
# Query logs
journalctl -u msp-watcher.service --since "1 hour ago"

# Export logs
journalctl -o json --since today > logs.json
```

#### Auditd (Security Audit)

**Version:** 3.x
**Purpose:** Kernel-level audit trail
**HIPAA Controls:** §164.312(b)

```bash
# Audit rules
-w /etc/shadow -p wa -k auth-changes
-w /var/log/audit/ -p wa -k audit-log-changes
-a always,exit -F arch=b64 -S adjtimex -k time-change
```

---

### 14. Object Storage (WORM)

#### AWS S3 (with Object Lock)

**Purpose:** Write-Once-Read-Many evidence storage
**Retention:** 2 years (Enterprise tier)
**Encryption:** AES-256 server-side

```python
import boto3

s3 = boto3.client('s3')

# Upload with object lock
s3.put_object(
    Bucket='msp-evidence-worm',
    Key=f'evidence/{client_id}/{bundle_id}.json',
    Body=evidence_json,
    ObjectLockMode='GOVERNANCE',
    ObjectLockRetainUntilDate=retention_date
)
```

**Alternatives:**
- MinIO (self-hosted S3-compatible)
- Glacier Deep Archive (long-term archival)

---

### 15. Testing & Quality

#### Pytest

**Version:** 7.4.0+
**Purpose:** Unit tests, integration tests
**Extensions:** pytest-asyncio 0.21.0+, pytest-cov 4.1.0+

**Current Test Suite:**
```
packages/compliance-agent/tests/
├── __init__.py
├── test_crypto.py        # 10 tests, 232 lines
│   ├── Keypair generation and formats
│   ├── Signing/verification (bytes, strings, JSON, files)
│   └── Hash computation and verification
├── test_utils.py         # 9 tests, 187 lines
│   ├── Maintenance windows (including midnight crossing)
│   ├── Command execution (success, failure, timeout)
│   └── Jitter and time utilities
├── test_evidence.py      # 14 tests, 310 lines
│   ├── Evidence creation with various parameters
│   ├── Storage and signature verification
│   ├── Loading and querying
│   └── Pruning and statistics
├── test_queue.py         # 16 tests, 441 lines
│   ├── SQLite initialization with WAL mode
│   ├── Enqueue/dequeue operations
│   ├── Retry logic with exponential backoff
│   ├── Queue statistics and pruning
│   └── Concurrent operations
├── test_mcp_client.py    # 15 tests, 470 lines
│   ├── mTLS SSL context creation
│   ├── Health check and order operations
│   ├── Evidence upload (with/without signature)
│   ├── Retry logic with exponential backoff
│   └── Context manager lifecycle
├── test_drift.py         # 25 tests, 570 lines
│   ├── All 6 drift checks (success scenarios)
│   ├── Drift detection scenarios
│   ├── Command failure handling
│   ├── Integration testing (check_all)
│   └── HIPAA control mapping
└── test_healing.py       # 23 tests, ~700 lines
    ├── All 6 remediations (success scenarios)
    ├── Failure scenarios with error handling
    ├── Maintenance window enforcement
    ├── Automatic rollback scenarios
    ├── Health check verification
    └── Dispatcher routing and exceptions
```

**Test Statistics:**
- Total Tests: 112
- Total Test Code: ~2,910 lines
- Production Code: 4,239 lines
- Test/Code Ratio: 69%
- All tests use fixtures and async/await patterns

**Running Tests:**
```bash
cd packages/compliance-agent
pip install -e ".[dev]"
pytest                          # Run all tests
pytest -v                       # Verbose output
pytest --cov=compliance_agent   # Coverage report
pytest -k evidence              # Run evidence tests only
```

**Future:**
- Property-based testing with Hypothesis
- Integration tests with NixOS VM tests
- Performance benchmarks
- Mutation testing

---

## Technology Comparison: Demo vs Production

| Component | Demo (Docker) | Production (NixOS) |
|-----------|---------------|-------------------|
| **OS** | Host OS + Docker | NixOS 24.05 |
| **Deployment** | docker-compose | NixOS flakes |
| **Package Manager** | pip, apt | Nix |
| **Configuration** | .env files | .nix modules |
| **Secrets** | Environment vars | SOPS + age |
| **Persistence** | Docker volumes | /nix/store + /var |
| **Updates** | `docker pull` | `nix flake update` |
| **Rollback** | Re-deploy old image | `nixos-rebuild --rollback` |
| **SBOM** | Syft manual | Nix automatic |
| **Signing** | Manual cosign | Nix binary cache signing |

---

## Dependency Graph

```
Compliance Agent (Python Package)
├── Pydantic 2.5+ → typing-extensions
├── cryptography 41.0+ → cffi → OpenSSL
├── aiohttp 3.9+ → aiofiles → asyncio
└── modules
    ├── config.py (321 lines)
    ├── crypto.py (338 lines)
    ├── utils.py (361 lines)
    ├── models.py (421 lines)
    ├── evidence.py (398 lines)
    ├── queue.py (436 lines)
    ├── mcp_client.py (448 lines)
    ├── drift.py (629 lines)
    ├── healing.py (887 lines)
    └── agent.py (TODO - Day 11)

MCP Server
├── FastAPI → Uvicorn → Python 3.11
├── Pydantic → typing-extensions
├── OpenAI SDK → httpx → certifi
├── Redis client → redis-py
├── Prometheus client → python
└── Evidence pipeline
    ├── jsonschema
    ├── boto3 (S3)
    └── cosign (signing)

Metrics Exporter
├── Prometheus client
└── Python 3.11

Grafana
├── Prometheus (data source)
└── Dashboard JSON (provisioning)

Prometheus
├── Metrics exporter (scrape target)
└── YAML config

NixOS (Production)
├── Nix flakes
│   ├── flake-compliance.nix (main flake)
│   ├── modules/compliance-agent.nix (546 lines)
│   └── packages/compliance-agent (Python package above)
├── systemd (service management + hardening)
├── journald (logging)
├── auditd (audit trail)
├── chrony (time sync)
└── nftables (egress allowlist)
```

---

## Version Pinning Strategy

### Demo Environment (Docker)
- **Python packages:** Pinned in `Dockerfile` RUN commands
- **Base images:** Use specific tags (`python:3.11-slim`, not `latest`)
- **Alpine versions:** Explicit version tags

### Production Environment (NixOS)
- **Flake lock:** `flake.lock` pins all inputs cryptographically
- **Store paths:** Content-addressed (hash includes all dependencies)
- **Rollback:** Previous generations preserved automatically

---

## Performance Characteristics

### Resource Requirements

| Service | CPU | RAM | Disk | Network |
|---------|-----|-----|------|---------|
| **MCP Server** | 0.5 core | 512 MB | 100 MB | Low |
| **Prometheus** | 1 core | 2 GB | 10 GB | Medium |
| **Grafana** | 0.5 core | 512 MB | 500 MB | Low |
| **Redis** | 0.25 core | 256 MB | 100 MB | Low |
| **Metrics Exporter** | 0.1 core | 128 MB | 10 MB | Low |
| **Total (Demo)** | 2.5 cores | 3.5 GB | 11 GB | - |

### Scaling Targets

| Metric | Small | Medium | Large |
|--------|-------|--------|-------|
| **Clients** | 1-10 | 10-50 | 50-200 |
| **Incidents/day** | < 100 | 100-1000 | 1000+ |
| **Evidence bundles/mo** | 30 | 300 | 1500 |
| **Prometheus retention** | 15 days | 30 days | 90 days |
| **Infrastructure** | 1 VM | 3 VMs | Cluster |

---

## Security Considerations

### Authentication & Authorization

**Current (Demo):**
- Grafana: Basic auth (admin/admin)
- MCP Server: No auth (localhost only)
- Prometheus: No auth (localhost only)

**Production Target:**
- Grafana: OAuth2/SAML integration
- MCP Server: API key + mTLS
- Prometheus: Bearer token auth
- All services: Behind reverse proxy (nginx + TLS)

### Network Security

**Demo:**
```yaml
networks:
  msp-network:
    driver: bridge  # Isolated Docker network
```

**Production:**
```nix
networking.firewall = {
  enable = true;
  allowedTCPPorts = [ 443 ];  # Only HTTPS public
  trustedInterfaces = [ "wg0" ];  # WireGuard VPN
};
```

---

## Data Flow Architecture

```
┌──────────────┐
│ Client Site  │ (NixOS flake deployed)
│  - Watcher   │ ──logs/events──┐
│  - Runbooks  │                │
└──────────────┘                │
                                ↓
                        ┌───────────────┐
                        │  Event Queue  │ (Redis/NATS)
                        └───────┬───────┘
                                ↓
┌────────────────────────────────────────────┐
│              MCP Server                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │ Planner  │→ │ Executor │→ │ Evidence │ │
│  │  (LLM)   │  │(Runbooks)│  │ Pipeline │ │
│  └──────────┘  └──────────┘  └──────────┘ │
└────────────────────────────────────────────┘
         │                │              │
         ↓                ↓              ↓
   ┌─────────┐      ┌─────────┐   ┌──────────┐
   │Prometheus│     │ Client  │   │ WORM S3  │
   │(Metrics) │     │ (Fix)   │   │(Evidence)│
   └────┬────┘      └─────────┘   └──────────┘
        │
        ↓
   ┌─────────┐
   │ Grafana │ ← User views dashboard
   │(Display)│
   └─────────┘
```

---

## Future Technology Additions

### Short-term (Week 6-8)
- [ ] NATS JetStream (replace Redis for events)
- [ ] MinIO (self-hosted WORM storage)
- [ ] Vault (secrets management)
- [ ] Loki (log aggregation)

### Medium-term (Month 2-3)
- [ ] Llama 3 8B (cost reduction)
- [ ] PostgreSQL (replace SQLite for multi-client)
- [ ] Terraform (infrastructure provisioning)
- [ ] Ansible (temporary - until full NixOS migration)

### Long-term (Month 4+)
- [ ] Kubernetes (multi-tenant orchestration)
- [ ] Istio (service mesh)
- [ ] ArgoCD (GitOps deployments)
- [ ] Temporal (workflow orchestration)

---

## Compliance Mappings

### Technology → HIPAA Control

| Technology | HIPAA Control | Purpose |
|------------|---------------|---------|
| **LUKS encryption** | §164.312(a)(2)(iv) | Encryption at rest |
| **TLS/mTLS** | §164.312(e)(1) | Transmission security |
| **Auditd** | §164.312(b) | Audit controls |
| **Chrony/NTP** | §164.312(b) | Accurate timestamps |
| **Evidence signatures** | §164.312(c)(1) | Integrity controls |
| **Access logs** | §164.308(a)(1)(ii)(D) | Activity review |
| **WORM storage** | §164.316(b)(2)(i) | Retention requirements |
| **SBOM** | §164.308(a)(8) | Evaluation process |

---

## Development Tools

### Local Development

```bash
# Python environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run locally (without Docker)
uvicorn mcp-server.server_minimal:app --reload

# Run tests
pytest tests/ -v

# Format code
black mcp-server/
ruff check mcp-server/

# Type checking
mypy mcp-server/
```

### CI/CD (Future)

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run tests
        run: pytest
      - name: Build Docker
        run: docker-compose build
      - name: Integration tests
        run: ./tests/integration.sh
```

---

## References & Documentation

### Official Documentation
- **NixOS Manual:** https://nixos.org/manual/nixos/stable/
- **FastAPI Docs:** https://fastapi.tiangolo.com/
- **Prometheus Docs:** https://prometheus.io/docs/
- **Grafana Docs:** https://grafana.com/docs/
- **OpenAI API:** https://platform.openai.com/docs/

### Internal Documentation
- [CLAUDE.md](./CLAUDE.md) - Project master reference
- [README.md](./README.md) - Project overview
- [DEMO_TECHNICAL_GUIDE.md](./DEMO_TECHNICAL_GUIDE.md) - Architecture deep-dive

### Compliance References
- **HIPAA Security Rule:** 45 CFR Part 164, Subpart C
- **Anduril NixOS STIG:** NIST National Checklist Program
- **NIST 800-53:** Security and Privacy Controls

---

## Glossary

| Term | Definition |
|------|------------|
| **MCP** | Model Context Protocol - structured LLM-tool interface |
| **WORM** | Write-Once-Read-Many - immutable storage |
| **SBOM** | Software Bill of Materials - dependency inventory |
| **Flake** | Hermetic Nix configuration with pinned dependencies |
| **Derivation** | Nix build instruction with cryptographic hash |
| **MTTR** | Mean Time To Remediation - incident resolution metric |
| **AOF** | Append-Only File - Redis persistence mode |
| **LUKS** | Linux Unified Key Setup - disk encryption |

---

## Implementation Status Summary

### Phase 1: NixOS Flake Scaffold (Week 5)
**Status:** ✅ COMPLETE

**Deliverables:**
- flake-compliance.nix with full package/module/test structure
- modules/compliance-agent.nix (546 lines, 27 options)
- Systemd hardening (15+ directives)
- nftables egress allowlist
- SOPS integration
- VM integration tests (7 test cases)
- Example configurations (reseller + direct modes)

### Phase 2: Python Compliance Agent Core (Week 6)
**Status:** 🚧 IN PROGRESS (Day 10/14 - 71% Complete)

**Completed Modules:**
- ✅ config.py (321 lines) - Configuration management with 27 options
- ✅ crypto.py (338 lines) - Ed25519 signing/verification
- ✅ utils.py (361 lines) - Maintenance windows, commands, NTP
- ✅ models.py (421 lines) - Pydantic models (7 major classes)
- ✅ evidence.py (398 lines) - Evidence generation and storage
- ✅ queue.py (436 lines) - SQLite offline queue with exponential backoff
- ✅ mcp_client.py (448 lines) - HTTP client with mTLS and retry logic
- ✅ drift.py (629 lines) - 6 drift detection checks with HIPAA mapping
- ✅ healing.py (887 lines) - 6 self-healing remediations with rollback
- ✅ test_crypto.py (10 tests, 232 lines)
- ✅ test_utils.py (9 tests, 187 lines)
- ✅ test_evidence.py (14 tests, 310 lines)
- ✅ test_queue.py (16 tests, 441 lines)
- ✅ test_mcp_client.py (15 tests, 470 lines)
- ✅ test_drift.py (25 tests, 570 lines)
- ✅ test_healing.py (23 tests, ~700 lines)

**Total Progress:**
- Production Code: 4,239 lines
- Test Code: ~2,910 lines (69% test/code ratio)
- Tests: 112 passing
- Modules Complete: 9/10 (90%)

**Remaining Work (Days 11-14):**
- Day 11: agent.py (main event loop, ~300 lines, 10-12 tests)
- Day 12: Demo stack integration
- Day 13: Integration tests (E2E scenarios)
- Day 14: Polish and documentation

### Demo Environment (Docker)
**Status:** ✅ COMPLETE

**Components:**
- Grafana dashboard with 10 panels
- Prometheus metrics exporter
- MCP server with planner/executor split
- Demo CLI for incident triggering
- Docker Compose stack
- Full demo instructions

### Production Deployment (NixOS)
**Status:** 🚧 IN PROGRESS

**Ready:**
- NixOS module structure
- Systemd service with hardening
- Secrets management (SOPS)
- Network security (nftables)
- Configuration validation

**Pending:**
- Complete Python agent implementation
- Integration with MCP server
- Full E2E testing
- Production deployment guides

---

**Current Status:** Phase 2 Day 10 - Self-Healing Complete
**Next Milestone:** Phase 2 Day 11 - Main Agent Loop
**Production Readiness:** ~71% (10/14 days complete)
**Target Completion:** Week 6 + 4 days (Phase 2 complete)
