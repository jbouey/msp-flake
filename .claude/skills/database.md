# Database Patterns

## Technology Stack

| Component | Database | Driver | Purpose |
|-----------|----------|--------|---------|
| Central Command | PostgreSQL | asyncpg + SQLAlchemy | Multi-tenant dashboard |
| Compliance Agent | SQLite | sqlite3 | Local incident tracking |
| Offline Queue | SQLite (WAL) | sqlite3 | Evidence persistence |

## PostgreSQL Schema

### Core Tables (26 migrations)

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
└── 026_latest.sql
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
- `mcp-server/migrations/*.sql` - 26 migration files
- `compliance_agent/incident_db.py` - SQLite incident tracking
- `compliance_agent/offline_queue.py` - Evidence queue
- `backend/db_queries.py` - PostgreSQL queries
- `backend/fleet.py` - asyncpg pool management
