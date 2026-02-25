# Python Backend Patterns

## Stack
- FastAPI (async)
- SQLAlchemy + asyncpg (PostgreSQL)
- SQLite (agent-side)
- gRPC (Go agent communication)
- Pydantic (validation)

## Three-Tier Healing Architecture

### Flow
```
Incident → L1 Deterministic (70-80%, <100ms, $0)
        → L2 LLM Planner (15-20%, 2-5s, ~$0.001)
        → L3 Human Escalation (5-10%)
        → Data Flywheel (promotes L2→L1)
```

### L1 Deterministic Rules
```yaml
# /var/lib/msp/rules/l1_rules.json (synced from Central Command)
- id: L1-FIREWALL-001
  conditions:
    - field: check_type
      operator: eq
      value: firewall_status
    - field: platform
      operator: ne
      value: nixos
  action: execute_runbook
  runbook_id: RB-WIN-FIREWALL-001
```

### L2 LLM Planner (Centralized Architecture)
```
Appliance (Go)                    Central Command (Python)
┌─────────────────┐               ┌──────────────────────┐
│ PHI scrub on-   │  POST /api/   │ l2_planner.py        │
│ device (12 cat) │──agent/l2/──→│ analyze_incident()   │──→ Anthropic API
│ Budget check    │    plan       │ record_l2_decision() │    (key on VPS)
│ Guardrails post │←─────────────│ Maps to LLMDecision  │
│ Execute WinRM/  │               └──────────────────────┘
│ SSH + telemetry │
└─────────────────┘
```
- Go planner: `appliance/internal/l2planner/` (PHI scrubber, guardrails, budget, telemetry)
- Python backend: `l2_planner.py` analyze_incident() → claude-sonnet-4
- Endpoint: `POST /api/agent/l2/plan` in main.py
- API key on Central Command only, never on appliances
```python
# L2Decision from l2_planner.py
@dataclass
class L2Decision:
    runbook_id: Optional[str]
    reasoning: str
    confidence: float  # 0.0-1.0
    requires_human_review: bool  # if < 0.7
    pattern_signature: str  # For learning loop
```

### L3 Escalation
- Slack webhook
- PagerDuty Events API v2
- Email (SMTP/SendGrid)
- Microsoft Teams

## FastAPI Router Pattern

```python
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/sites", tags=["sites"])

class SiteCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    partner_id: Optional[str] = None

@router.post("/", response_model=Site)
async def create_site(
    data: SiteCreate,
    db: AsyncSession = Depends(get_db),
    user: Dict = Depends(require_auth)
):
    """Create a new site."""
    site = Site(**data.dict())
    db.add(site)
    await db.commit()
    return site

@router.get("/{site_id}")
async def get_site(
    site_id: str,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Site).where(Site.id == site_id)
    )
    site = result.scalar_one_or_none()
    if not site:
        raise HTTPException(404, f"Site {site_id} not found")
    return site
```

## Database Patterns

### AsyncSession Dependency
```python
async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
```

### Raw asyncpg Pool
```python
_pool: Optional[asyncpg.Pool] = None

async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=2,
            max_size=10
        )
    return _pool

# Usage
pool = await get_pool()
async with pool.acquire() as conn:
    rows = await conn.fetch("SELECT * FROM sites WHERE id = $1", _uid(site_id))
```

### _uid() UUID Conversion (CRITICAL)
asyncpg requires `uuid.UUID` objects for UUID columns. FastAPI path params are strings.
**Every path param passed to a UUID column MUST be wrapped with `_uid()`.**

```python
from .db_utils import _uid, _row_dict, _rows_list

# _uid() is idempotent — safe on strings AND UUID objects
_uid("550e8400-...")  # → uuid.UUID
_uid(uuid.UUID(...))  # → passes through
_uid("garbage")       # → HTTPException(400)

# WRONG — will crash with DataTypeMismatchError:
await conn.fetch("SELECT * FROM sites WHERE id = $1", site_id)

# CORRECT:
await conn.fetch("SELECT * FROM sites WHERE id = $1", _uid(site_id))
```

**Exception:** Values from asyncpg query results (e.g. `user["org_id"]` from auth) are already UUID objects. Values from `sites.site_id` are VARCHAR(50), not UUID.

**`db_utils.py`** — Shared dependency-free module (avoids circular imports):
- `_uid(s)` → converts string to uuid.UUID, idempotent
- `_row_dict(row)` → asyncpg Record to JSON-safe dict
- `_rows_list(rows)` → list of _row_dict

All portal backends import from db_utils: `companion.py`, `partners.py`, `partner_auth.py`, `client_portal.py`, `hipaa_modules.py`, `billing.py`

### SQLite (Agent-side)
```python
conn = sqlite3.connect(db_path)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=FULL")  # Crash-safe
cursor = conn.execute(query, params)
conn.commit()
```

## gRPC Integration (Go Agent v0.3.0)

### Proto Definition
```protobuf
service ComplianceAgent {
  rpc Register(RegisterRequest) returns (RegisterResponse);
  rpc ReportDrift(stream DriftEvent) returns (stream DriftAck);
  rpc ReportHealing(HealingResult) returns (HealingAck);
  rpc Heartbeat(HeartbeatRequest) returns (HeartbeatResponse);
  rpc ReportRMMStatus(RMMStatusReport) returns (RMMAck);
}

// RegisterRequest includes needs_certificates (field 6) for mTLS auto-enrollment
// RegisterResponse includes ca_cert_pem (6), agent_cert_pem (7), agent_key_pem (8)
// DriftAck includes optional HealCommand for bidirectional healing
// HeartbeatResponse includes repeated HealCommand pending_commands
```

### Python Servicer (with CA)
```python
class ComplianceAgentServicer(compliance_pb2_grpc.ComplianceAgentServicer):
    def __init__(self, agent_registry, ..., agent_ca=None):
        self.agent_ca = agent_ca  # AgentCA for mTLS enrollment

    def Register(self, request, context):
        # If agent needs certs, issue via CA
        if request.needs_certificates and self.agent_ca:
            cert_pem, key_pem, ca_pem = self.agent_ca.issue_agent_cert(
                hostname=request.hostname, agent_id=agent_id)
            return RegisterResponse(..., ca_cert_pem=ca_pem,
                agent_cert_pem=cert_pem, agent_key_pem=key_pem)
```

### Agent Deployment Pipeline
```
Boot → CA Init → gRPC Server (TLS) → DNS SRV Registration → GPO Deployment
```
- `agent_ca.py`: ECDSA P-256 CA, 10-year validity, 365-day agent certs
- `dns_registration.py`: Registers `_osiris-grpc._tcp` SRV record via PowerShell on DC
- `gpo_deployment.py`: Uploads agent to SYSVOL, creates idempotent startup script, links GPO

## Error Handling

### HTTPException Pattern
```python
# Validation error
if not data.valid:
    raise HTTPException(400, "Invalid input")

# Not found
if not resource:
    raise HTTPException(404, f"Resource {id} not found")

# Auth error
if not user:
    raise HTTPException(401, "Not authenticated")

# Rate limit
raise HTTPException(429, f"Rate limited. Try again in {seconds}s")
```

### Safe Enum Conversion (routes.py)
```python
# DB may contain unknown/null enum values — never trust raw Severity()/ResolutionLevel()
def _safe_severity(sev) -> Severity:
    if not sev:
        return Severity.MEDIUM
    try:
        return Severity(sev)
    except (ValueError, KeyError):
        return Severity.MEDIUM
```

### Graceful Degradation
```python
try:
    result = await llm_provider.generate(prompt)
except TimeoutError:
    # Fallback to next provider
    result = await fallback_provider.generate(prompt)
except Exception as e:
    logger.error(f"LLM failed: {e}")
    return L2Decision(runbook_id=None, requires_human_review=True)
```

## Configuration

### Environment Variables
```python
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://...")
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "30"))
```

### Pydantic Config
```python
class Settings(BaseModel):
    site_id: str
    mcp_url: str
    poll_interval: int = 60
    dry_run: bool = False

    class Config:
        env_prefix = "MSP_"
```

## Order Signing & Security

### Ed25519 Order Signing (Session 127)
```
Central Command (Python)                  Appliance (Go)
┌──────────────────────┐                 ┌────────────────────────┐
│ order_signing.py     │   checkin resp  │ internal/crypto/       │
│ sign_admin_order()   │──────────────→ │ OrderVerifier          │
│ sign_fleet_order()   │  server_pubkey  │ VerifyOrder()          │
│                      │  + signed order │ VerifyRulesBundle()    │
│ main.py sign_data()  │                 │                        │
│ NaCl Ed25519 privkey │                 │ internal/orders/       │
└──────────────────────┘                 │ verifySignature()      │
                                         │ verifyHostScope()      │
                                         └────────────────────────┘
```
- All orders (admin, fleet, healing) signed with Ed25519 private key on Central Command
- Go daemon verifies signatures before executing any order
- `target_appliance_id` in signed payload prevents cross-appliance replay attacks
- Fleet-wide orders (no target) are allowed on any appliance
- Parameter allowlists: `nixos_rebuild` (flake_ref pattern), `update_agent`/`update_iso` (HTTPS domain allowlist), `sync_promoted_rule` (YAML schema validation + action allowlist)
- L1 rules bundle from `/agent/sync` is signed; Go L1 engine verifies before loading
- Migration 054: adds nonce/signature/signed_payload columns to admin_orders, fleet_orders, orders tables

### Key files
- `backend/order_signing.py` — Shared signing helper (sign_admin_order, sign_fleet_order)
- `appliance/internal/crypto/verify.go` — Go Ed25519 verification package
- `appliance/internal/orders/processor.go` — Order dispatch with signature + host scope + param validation

## Network Scanner & Device Compliance

### Architecture
```
Nmap scan → ports in SQLite → compliance runner (7 HIPAA checks) → device_compliance table
→ local-portal syncs compliance_details to Central Command → device_compliance_details (PG)
→ dashboard Device Inventory page shows compliance rate + per-device drill-down
```

### Key files
- `packages/network-scanner/src/network_scanner/compliance/network_checks.py` — 7 HIPAA check classes
- `packages/network-scanner/src/network_scanner/compliance/runner.py` — Orchestrator, runs after each scan
- `packages/network-scanner/src/network_scanner/scanner_service.py` — Hooks runner after `db.complete_scan()`
- `packages/network-scanner/src/network_scanner/config.py` — `NETWORK_RANGES=auto` auto-detects subnets
- `packages/local-portal/src/local_portal/services/central_sync.py` — Includes compliance_details in sync
- `backend/device_sync.py` — Device sync models + DB ops + **device_sync_router** (the one main.py uses)
- `backend/routes/device_sync.py` — Alternate router (NOT used by main.py — see note below)
- `backend/migrations/060_device_compliance_details.sql` — PG table for compliance check details

### IMPORTANT: Router duplication
`device_sync.py` defines both functions AND a `device_sync_router` at line 411. `routes/device_sync.py` also has a `router`. **main.py imports from `device_sync.py` directly.** New endpoints in `routes/device_sync.py` are unreachable. Always add to `device_sync.py`.

## Key Files
- `backend/db_utils.py` - Shared _uid(), _row_dict(), _rows_list() (dependency-free, no circular imports)
- `backend/auth.py` - Authentication (436 lines); require_admin, require_companion, require_partner
- `backend/companion.py` - Compliance Companion portal (all 10 HIPAA modules for cross-org companion users)
- `backend/hipaa_modules.py` - Client-facing HIPAA module endpoints (SRA, policies, training, BAAs, IR, contingency, workforce, physical, officers, gap) + document upload/download/delete + policy template list/detail
- `backend/hipaa_templates.py` - Template data for policy/SRA/physical/gap generation + OFFICER_DESIGNATION_TEMPLATE
- `backend/client_portal.py` - Client portal (magic link auth, session cookies, dashboard, compliance views)
- `backend/partners.py` - Partner management (provisions, credentials, assets, users, API keys)
- `backend/partner_auth.py` - Partner OAuth login + admin approve/reject
- `backend/billing.py` - Stripe billing (subscriptions, webhooks)
- `backend/sites.py` - Site management
- `backend/escalation_engine.py` - L3 notifications (861 lines)
- `backend/l2_planner.py` - LLM integration (507 lines)
- `compliance_agent/grpc_server.py` - gRPC servicer (with agent_ca for mTLS enrollment)
- `compliance_agent/agent_ca.py` - ECDSA P-256 CA for agent mTLS auto-enrollment
- `compliance_agent/dns_registration.py` - DNS SRV record registration via WinRM on DC
- `compliance_agent/gpo_deployment.py` - GPO-based zero-friction agent deployment
- `compliance_agent/level1_deterministic.py` - Rule engine (92 rules: builtin + YAML + JSON synced)
- `compliance_agent/auto_healer.py` - Healing orchestrator (circuit breaker + persistent flap suppression)
- `compliance_agent/runbooks/windows/executor.py` - WinRM executor (session cache, retry backoff, tempfile for scripts >2KB to bypass cmd.exe 8191 char limit)
- `compliance_agent/appliance_client.py` - MCP server client (PHI scrub at transport boundary, pre_scrubbed flag for signed payloads)
- `backend/cve_watch.py` - CVE Watch (NVD sync + fleet matching + 7 REST endpoints; asyncpg returns JSONB as string—needs isinstance guard)
- `backend/email_alerts.py` - L3 critical email alerts (SMTP/TLS, HTML+plaintext, severity-colored headers, HIPAA controls, drift details as JSON)
- `backend/evidence_chain.py` - OTS blockchain anchoring (LEB128 varint parser, BTC block height extraction, WebSocket broadcast on submission)
- `backend/framework_sync.py` - Compliance Library (OSCAL sync from NIST GitHub + YAML seed + 7 REST endpoints, 498 lines)
- `backend/sites.py` - Site management + order acknowledge/complete (admin_orders table, auto-expires stale orders). OrderType enum: force_checkin, run_drift, sync_rules, restart_agent, nixos_rebuild, update_agent, update_iso, view_logs, diagnostic, deploy_sensor, remove_sensor, deploy_linux_sensor, remove_linux_sensor, sensor_status, update_credentials. Non-AD device join: `POST /{site_id}/devices/manual` (ManualDeviceAdd model, _add_manual_device shared helper). CredentialCreate extended with port/private_key/distro. Checkin delivers linux_targets with sudo_password (password fallback).
- `backend/portal.py` - Client portal (magic link auth, session cookies). `POST /site/{site_id}/devices` for portal device join (uses shared _add_manual_device). Portal data includes device_count.
- `compliance_agent/incident_db.py` - SQLite incident DB + flap_suppressions table
- `main.py` - Flywheel promotion loop (Step 0: generates patterns from L2 telemetry, Step 1-3: evaluate/promote/sync). l1_rules query filters source != 'builtin' to avoid double-serve.
