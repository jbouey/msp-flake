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
# rules/l1_baseline.json
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

### L2 LLM Planner
```python
# level2_llm.py
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
    rows = await conn.fetch("SELECT * FROM sites WHERE id = $1", site_id)
```

### SQLite (Agent-side)
```python
conn = sqlite3.connect(db_path)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=FULL")  # Crash-safe
cursor = conn.execute(query, params)
conn.commit()
```

## gRPC Integration

### Proto Definition
```protobuf
service ComplianceAgent {
  rpc Register(RegisterRequest) returns (RegisterResponse);
  rpc ReportDrift(stream DriftEvent) returns (stream DriftAck);
  rpc Heartbeat(HeartbeatRequest) returns (HeartbeatResponse);
}

message DriftEvent {
  string agent_id = 1;
  string hostname = 2;
  string check_type = 3;
  bool passed = 4;
  string expected = 5;
  string actual = 6;
}
```

### Python Servicer
```python
class ComplianceAgentServicer(compliance_pb2_grpc.ComplianceAgentServicer):
    def ReportDrift(self, request_iterator, context):
        for event in request_iterator:
            logger.info(f"Drift: {event.hostname}/{event.check_type}")

            if not event.passed:
                self._route_drift_to_healing(event)

            yield compliance_pb2.DriftAck(
                event_id=f"{event.agent_id}-{event.timestamp}",
                received=True
            )
```

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

## Key Files
- `backend/auth.py` - Authentication (436 lines)
- `backend/sites.py` - Site management
- `backend/escalation_engine.py` - L3 notifications (861 lines)
- `backend/l2_planner.py` - LLM integration (507 lines)
- `compliance_agent/grpc_server.py` - gRPC servicer
- `compliance_agent/level1_deterministic.py` - Rule engine (92 rules: builtin + YAML + JSON synced)
- `compliance_agent/auto_healer.py` - Healing orchestrator (circuit breaker + persistent flap suppression)
- `compliance_agent/appliance_client.py` - MCP server client (PHI scrub at transport boundary, pre_scrubbed flag for signed payloads)
- `backend/cve_watch.py` - CVE Watch (NVD sync + fleet matching + 7 REST endpoints; asyncpg returns JSONB as string—needs isinstance guard)
- `backend/evidence_chain.py` - OTS blockchain anchoring (LEB128 varint parser, BTC block height extraction)
- `backend/framework_sync.py` - Compliance Library (OSCAL sync from NIST GitHub + YAML seed + 7 REST endpoints, 498 lines)
- `compliance_agent/incident_db.py` - SQLite incident DB + flap_suppressions table
