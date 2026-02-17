# Database Patterns

## Technology Stack

| Component | Database | Driver | Purpose |
|-----------|----------|--------|---------|
| Central Command | PostgreSQL | asyncpg + SQLAlchemy | Multi-tenant dashboard |
| Compliance Agent | SQLite | sqlite3 | Local incident tracking |
| Offline Queue | SQLite (WAL) | sqlite3 | Evidence persistence |

## PostgreSQL Schema

### Core Tables (46 migrations)

```sql
-- Sites and appliances
CREATE TABLE sites (
    id VARCHAR PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    partner_id VARCHAR REFERENCES partners(id),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE appliances (
    id VARCHAR PRIMARY KEY,
    site_id VARCHAR REFERENCES sites(id) ON DELETE CASCADE,
    hostname VARCHAR(255),
    ip_address INET,
    is_online BOOLEAN DEFAULT false,
    last_checkin TIMESTAMP
);

-- Compliance tracking
CREATE TABLE compliance_snapshots (
    id SERIAL PRIMARY KEY,
    site_id VARCHAR REFERENCES sites(id),
    snapshot_at TIMESTAMP DEFAULT NOW(),
    flake_hash VARCHAR(64),
    patching_status BOOLEAN,
    backup_status BOOLEAN,
    encryption_status BOOLEAN
);

-- Evidence bundles
CREATE TABLE evidence_bundles (
    id SERIAL PRIMARY KEY,
    site_id VARCHAR REFERENCES sites(id),
    bundle_type VARCHAR(50),
    bundle_hash VARCHAR(64),
    hipaa_controls JSONB,
    generated_at TIMESTAMP DEFAULT NOW()
);

-- Orders (runbook execution)
CREATE TABLE orders (
    id VARCHAR PRIMARY KEY,
    appliance_id VARCHAR REFERENCES appliances(id),
    runbook_id VARCHAR NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Filtered index for pending orders
CREATE INDEX idx_orders_pending
ON orders(appliance_id, status)
WHERE status = 'pending';

-- CVE Watch (migration 040)
CREATE TABLE cve_entries (
    id UUID PRIMARY KEY,
    cve_id VARCHAR(20) UNIQUE,     -- CVE-2025-1234
    severity VARCHAR(10),           -- critical/high/medium/low
    cvss_score DECIMAL(3,1),
    affected_cpes JSONB,
    published_date TIMESTAMPTZ
);

CREATE TABLE cve_fleet_matches (
    id UUID PRIMARY KEY,
    cve_id UUID REFERENCES cve_entries(id),
    appliance_id VARCHAR(255),
    site_id VARCHAR(255),
    status VARCHAR(20) DEFAULT 'open',  -- open/mitigated/accepted_risk
    UNIQUE(cve_id, appliance_id)
);

CREATE TABLE cve_watch_config (
    id UUID PRIMARY KEY,
    watched_cpes JSONB,             -- CPE strings to monitor
    sync_interval_hours INTEGER DEFAULT 6,
    last_sync_at TIMESTAMPTZ,
    enabled BOOLEAN DEFAULT true
);

-- Compliance Library / Framework Sync (migration 041)
CREATE TABLE compliance_frameworks (
    id UUID PRIMARY KEY,
    name VARCHAR(100) UNIQUE,       -- HIPAA, SOC2, PCI-DSS, etc.
    version VARCHAR(50),
    source_url TEXT,                 -- OSCAL/official source
    total_controls INTEGER DEFAULT 0,
    last_synced_at TIMESTAMPTZ,
    enabled BOOLEAN DEFAULT true
);

CREATE TABLE framework_controls (
    id UUID PRIMARY KEY,
    framework_id UUID REFERENCES compliance_frameworks(id),
    control_id VARCHAR(50),         -- AC-1, 164.312(a)(1), etc.
    title TEXT,
    description TEXT,
    category VARCHAR(100),
    UNIQUE(framework_id, control_id)
);

CREATE TABLE control_check_mappings (
    id UUID PRIMARY KEY,
    control_id UUID REFERENCES framework_controls(id),
    check_type VARCHAR(100),        -- Maps to agent check types
    UNIQUE(control_id, check_type)
);

CREATE TABLE framework_sync_log (
    id UUID PRIMARY KEY,
    framework_id UUID REFERENCES compliance_frameworks(id),
    synced_at TIMESTAMPTZ DEFAULT NOW(),
    controls_added INTEGER DEFAULT 0,
    controls_updated INTEGER DEFAULT 0,
    source VARCHAR(50)              -- oscal_nist, yaml_seed, manual
);
```

### Multi-Tenant Pattern
```sql
-- All queries include site_id filter
SELECT * FROM incidents
WHERE site_id = $1
ORDER BY created_at DESC
LIMIT 50;

-- Partner-scoped access
SELECT s.* FROM sites s
JOIN partners p ON s.partner_id = p.id
WHERE p.id = $1;
```

## SQLite Schema (Agent)

### Incident Database
```sql
-- incidents table
CREATE TABLE incidents (
    id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL,
    host_id TEXT NOT NULL,
    incident_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    raw_data TEXT,  -- JSON
    pattern_signature TEXT,
    created_at TEXT,
    resolution_level TEXT,  -- L1, L2, L3
    outcome TEXT  -- success, failed, escalated
);

CREATE INDEX idx_incidents_pattern ON incidents(pattern_signature);
CREATE INDEX idx_incidents_type ON incidents(incident_type);
CREATE INDEX idx_incidents_site ON incidents(site_id);

-- Pattern statistics (materialized)
CREATE TABLE pattern_stats (
    pattern_signature TEXT PRIMARY KEY,
    total_count INTEGER DEFAULT 0,
    l1_count INTEGER DEFAULT 0,
    l2_count INTEGER DEFAULT 0,
    l3_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    promotion_eligible BOOLEAN DEFAULT FALSE
);

-- Persistent flap suppression: once a check flaps 3x and escalates to L3,
-- healing is disabled until an operator clears it. Survives agent restarts.
-- Prevents infinite L3 escalation loops (e.g., Windows GPO overriding firewall).
CREATE TABLE flap_suppressions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL,
    host_id TEXT NOT NULL,
    incident_type TEXT NOT NULL,
    suppressed_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    cleared_at TEXT,       -- NULL = active suppression
    cleared_by TEXT,       -- operator who cleared it
    UNIQUE(site_id, host_id, incident_type)
);
```

### Offline Queue
```sql
CREATE TABLE queued_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_json TEXT NOT NULL,
    retry_count INTEGER DEFAULT 0,
    next_retry_at TEXT,
    uploaded_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Exponential backoff: 2^retry_count minutes (max 60)
```

## Connection Patterns

### AsyncPG Pool
```python
async def get_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        DATABASE_URL,
        min_size=2,
        max_size=10
    )

# Usage
pool = await get_pool()
async with pool.acquire() as conn:
    rows = await conn.fetch(
        "SELECT * FROM sites WHERE partner_id = $1",
        partner_id
    )
```

### SQLAlchemy AsyncSession
```python
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

engine = create_async_engine(DATABASE_URL)
async_session = sessionmaker(engine, class_=AsyncSession)

async def get_db():
    async with async_session() as session:
        yield session

# Usage in route
@router.get("/sites")
async def list_sites(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Site))
    return result.scalars().all()
```

### SQLite with WAL
```python
conn = sqlite3.connect(db_path)
conn.execute("PRAGMA journal_mode=WAL")      # Write-ahead logging
conn.execute("PRAGMA synchronous=FULL")      # Crash-safe
conn.execute("PRAGMA foreign_keys=ON")       # Enforce FK constraints
```

## Query Patterns

### Aggregation with FILTER
```sql
SELECT
    site_id,
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE outcome = 'success') as success_count,
    COUNT(*) FILTER (WHERE resolution_level = 'L1') as l1_count
FROM incidents
GROUP BY site_id;
```

### Upsert Pattern (SQLite)
```sql
INSERT INTO pattern_stats (pattern_signature, total_count, success_count)
VALUES (?, 1, ?)
ON CONFLICT(pattern_signature) DO UPDATE SET
    total_count = total_count + 1,
    success_count = success_count + excluded.success_count;
```

### Pagination
```python
async def get_incidents(
    db: AsyncSession,
    site_id: str,
    limit: int = 50,
    offset: int = 0
):
    query = (
        select(Incident)
        .where(Incident.site_id == site_id)
        .order_by(Incident.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(query)
    return result.scalars().all()
```

## Migrations

### File Naming
```
migrations/
├── 001_portal_tables.sql
├── 002_orders_table.sql
├── 003_partner_infrastructure.sql
├── ...
├── 026_latest.sql
├── ...
├── 040_cve_watch.sql
├── 041_framework_sync.sql
├── 042_client_healing_logs.sql
├── 043_fix_evidence_chain_race.sql
├── 044_flywheel_fixes.sql
├── 045_audit_fixes.sql
└── 046_runbook_id_fix.sql
```

### Migration Pattern
```sql
-- Always idempotent
CREATE TABLE IF NOT EXISTS new_table (...);

ALTER TABLE existing_table
ADD COLUMN IF NOT EXISTS new_column VARCHAR(255);

CREATE INDEX IF NOT EXISTS idx_name ON table(column);
```

## Key Files
- `mcp-server/migrations/*.sql` - 46 migration files
- `compliance_agent/incident_db.py` - SQLite incident tracking
- `compliance_agent/offline_queue.py` - Evidence queue
- `backend/db_queries.py` - PostgreSQL queries
- `backend/fleet.py` - asyncpg pool management
