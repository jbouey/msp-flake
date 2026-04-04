# Per-Appliance API Keys Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move API key auth from per-site to per-appliance so multiple appliances on the same site don't invalidate each other's keys on rekey.

**Architecture:** Add `appliance_id` column to `api_keys` table. Auth lookup joins on `appliance_id` when present, falls back to site-level for backward compat. Rekey deactivates only the requesting appliance's keys. No daemon changes needed — daemon already sends MAC in every request.

**Tech Stack:** PostgreSQL migration, Python (asyncpg + SQLAlchemy), existing test framework

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `mcp-server/central-command/backend/migrations/119_per_appliance_api_keys.sql` | Add appliance_id to api_keys, backfill |
| Modify | `mcp-server/central-command/backend/shared.py:252-324` | Auth lookup by appliance_id first, site fallback |
| Modify | `mcp-server/central-command/backend/provisioning.py:623-700` | Rekey scoped to appliance_id |
| Modify | `mcp-server/main.py:1468-1510` | Same auth change in main.py copy |

---

### Task 1: Migration — add appliance_id to api_keys

**Files:**
- Create: `mcp-server/central-command/backend/migrations/119_per_appliance_api_keys.sql`

- [ ] **Step 1: Write migration**

```sql
-- Migration 119: Per-appliance API keys
--
-- Moves API key ownership from site-level to appliance-level.
-- Each appliance gets its own key so rekey on one appliance
-- doesn't invalidate siblings on the same site.

-- Add appliance_id column (nullable for backward compat during transition)
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS appliance_id TEXT;

-- Create index for auth lookups by appliance
CREATE INDEX IF NOT EXISTS idx_api_keys_appliance_active
    ON api_keys (appliance_id, active) WHERE active = true;

-- Backfill: assign existing active keys to the most-recently-checked-in
-- appliance for each site. This handles the common case of 1 appliance per site.
-- For multi-appliance sites, the first appliance to rekey will get its own key.
UPDATE api_keys ak
SET appliance_id = (
    SELECT sa.appliance_id
    FROM site_appliances sa
    WHERE sa.site_id = ak.site_id
    ORDER BY sa.last_checkin DESC NULLS LAST
    LIMIT 1
)
WHERE ak.appliance_id IS NULL
  AND ak.active = true;
```

- [ ] **Step 2: Run migration on VPS**

```bash
ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -f /dev/stdin" < mcp-server/central-command/backend/migrations/119_per_appliance_api_keys.sql
```

Expected: `ALTER TABLE`, `CREATE INDEX`, `UPDATE N`

- [ ] **Step 3: Verify**

```bash
ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -t -c \"SELECT id, site_id, appliance_id, key_prefix, active FROM api_keys ORDER BY id DESC LIMIT 5\""
```

Expected: existing keys now have `appliance_id` populated

- [ ] **Step 4: Commit**

```bash
git add mcp-server/central-command/backend/migrations/119_per_appliance_api_keys.sql
git commit -m "migration 119: per-appliance API keys — add appliance_id to api_keys"
```

---

### Task 2: Auth lookup — per-appliance with site fallback

**Files:**
- Modify: `mcp-server/central-command/backend/shared.py:252-324`

- [ ] **Step 1: Update require_appliance_bearer in shared.py**

Replace the auth function to look up by appliance_id first (if the request body contains mac_address), then fall back to site-level lookup for backward compat:

```python
async def require_appliance_bearer(request: Request) -> str:
    """Validate appliance Bearer token from Authorization header.

    Auth priority:
    1. Per-appliance key: api_keys.appliance_id matches request MAC
    2. Site-level key: api_keys.site_id matches (backward compat, no appliance_id set)

    Returns the site_id associated with the key.
    On auth failure for known appliances, tracks auth_failure_count.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    api_key = auth_header[7:]
    if not api_key:
        raise HTTPException(status_code=401, detail="Empty API key")

    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    from sqlalchemy import text
    async with async_session() as db:
        # Single query: match by key_hash. The appliance_id column tells us
        # whether this is a per-appliance or site-level key.
        result = await db.execute(
            text("""
                SELECT ak.site_id, ak.appliance_id FROM api_keys ak
                WHERE ak.key_hash = :key_hash AND ak.active = true
                LIMIT 1
            """),
            {"key_hash": key_hash}
        )
        row = result.fetchone()

    if row:
        # Reset auth failure tracking on successful auth
        if row.appliance_id:
            try:
                from dashboard_api.fleet import get_pool
                from dashboard_api.tenant_middleware import admin_connection
                pool = await get_pool()
                async with admin_connection(pool) as conn:
                    await conn.execute("""
                        UPDATE site_appliances
                        SET auth_failure_count = 0,
                            auth_failure_since = NULL,
                            last_auth_failure = NULL
                        WHERE appliance_id = $1
                          AND auth_failure_count > 0
                    """, row.appliance_id)
            except Exception:
                pass  # Non-critical
        return row.site_id

    # Auth failed — track failure for dashboard visibility
    try:
        body_bytes = await request.body()
        import json as _json
        body = _json.loads(body_bytes)
        site_id = body.get("site_id")
        mac = body.get("mac_address")
        if site_id and mac:
            from dashboard_api.provisioning import normalize_mac
            mac_norm = normalize_mac(mac)
            appliance_id = f"{site_id}-{mac_norm}"
            from dashboard_api.fleet import get_pool
            from dashboard_api.tenant_middleware import admin_connection
            pool = await get_pool()
            async with admin_connection(pool) as conn:
                await conn.execute("""
                    UPDATE site_appliances
                    SET auth_failure_count = COALESCE(auth_failure_count, 0) + 1,
                        last_auth_failure = NOW(),
                        auth_failure_since = COALESCE(auth_failure_since, NOW())
                    WHERE appliance_id = $1
                """, appliance_id)
            logger.warning(
                "Auth failed for known appliance",
                appliance_id=appliance_id,
                site_id=site_id,
            )
            raise HTTPException(
                status_code=401,
                detail={"error": "API key mismatch", "code": "AUTH_KEY_MISMATCH"}
            )
    except HTTPException:
        raise
    except Exception:
        pass

    raise HTTPException(status_code=401, detail="Invalid API key")
```

- [ ] **Step 2: Update require_appliance_bearer in main.py**

The main.py copy at line ~1468 should import and delegate to the shared version instead of duplicating. Replace the function body:

```python
async def require_appliance_bearer(request: Request) -> str:
    """Validate appliance Bearer token. Delegates to shared implementation."""
    from dashboard_api.shared import require_appliance_bearer as _shared_auth
    return await _shared_auth(request)
```

- [ ] **Step 3: Commit**

```bash
git add mcp-server/central-command/backend/shared.py mcp-server/main.py
git commit -m "feat: per-appliance auth — lookup by appliance_id with site fallback"
```

---

### Task 3: Rekey scoped to requesting appliance only

**Files:**
- Modify: `mcp-server/central-command/backend/provisioning.py:673-700`

- [ ] **Step 1: Change rekey to scope key deactivation to appliance_id**

Replace lines 673-688 in `rekey_appliance()`:

```python
        # Generate new API key
        raw_api_key = secrets.token_urlsafe(32)
        api_key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()

        # Deactivate old keys for THIS APPLIANCE only (not the whole site)
        deactivated = await conn.execute("""
            UPDATE api_keys SET active = false
            WHERE site_id = $1 AND appliance_id = $2 AND active = true
        """, req.site_id, appliance_id)
        
        # Also deactivate any site-level keys (no appliance_id) that this
        # appliance was using before per-appliance keys were introduced.
        # This is a one-time migration path — after rekey, the appliance
        # has its own key and site-level keys are left alone for siblings.
        await conn.execute("""
            UPDATE api_keys SET active = false
            WHERE site_id = $1 AND appliance_id IS NULL AND active = true
            AND NOT EXISTS (
                SELECT 1 FROM site_appliances sa
                WHERE sa.site_id = $1
                AND sa.appliance_id != $2
            )
        """, req.site_id, appliance_id)
        logger.info(f"Rekey: deactivated old keys for appliance {appliance_id}: {deactivated}")

        # Insert new per-appliance key
        await conn.execute("""
            INSERT INTO api_keys (site_id, appliance_id, key_hash, key_prefix, description, active, created_at)
            VALUES ($1, $2, $3, $4, 'Auto-rekeyed after auth failure', true, NOW())
        """, req.site_id, appliance_id, api_key_hash, raw_api_key[:8])
```

- [ ] **Step 2: Commit**

```bash
git add mcp-server/central-command/backend/provisioning.py
git commit -m "fix: rekey scoped to requesting appliance — siblings keep their keys"
```

---

### Task 4: Fix pilot appliance — manual rekey

- [ ] **Step 1: Generate a new API key for the pilot**

```bash
# Generate key and insert for the pilot appliance specifically
ssh root@178.156.162.116 "docker exec mcp-server python3 -c \"
import secrets, hashlib
key = secrets.token_urlsafe(32)
key_hash = hashlib.sha256(key.encode()).hexdigest()
print(f'KEY={key}')
print(f'HASH={key_hash}')
print(f'PREFIX={key[:8]}')
\""
```

Then insert the key and update the appliance config:

```bash
# Insert per-appliance key for pilot
ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -c \"
    INSERT INTO api_keys (site_id, appliance_id, key_hash, key_prefix, description, active, created_at)
    VALUES ('north-valley-branch-2', 'physical-appliance-pilot-1aea78-84:3A:5B:91:B6:61',
            '<HASH>', '<PREFIX>', 'Per-appliance key for pilot', true, NOW())
\""

# Update pilot config.yaml with new key
ssh root@192.168.88.241 "sed -i 's/^api_key:.*/api_key: <KEY>/' /var/lib/msp/config.yaml"
```

- [ ] **Step 2: Restart pilot daemon and verify checkin**

```bash
ssh root@192.168.88.241 "systemctl restart appliance-daemon"
# Wait 60s, check DB
ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -t -c \"SELECT appliance_id, agent_version, last_checkin FROM site_appliances ORDER BY last_checkin DESC\""
```

Expected: Pilot shows recent last_checkin, north-valley unaffected

- [ ] **Step 3: Commit docs**

```bash
git add .agent/claude-progress.json
git commit -m "fix: pilot appliance rekeyed with per-appliance API key"
```

---

### Task 5: Deploy and verify both appliances

- [ ] **Step 1: Push to trigger CI/CD**

```bash
git push origin main
```

- [ ] **Step 2: Rebuild container**

```bash
ssh root@178.156.162.116 "cd /opt/mcp-server && docker compose up -d --build mcp-server"
```

- [ ] **Step 3: Verify both appliances check in successfully**

Wait 2 minutes, then:

```bash
ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -t -c \"SELECT appliance_id, agent_version, last_checkin FROM site_appliances ORDER BY last_checkin DESC\""
```

Expected: Both appliances showing recent checkins, both at v0.3.77

- [ ] **Step 4: Verify rekey isolation**

Trigger a rekey on one appliance and confirm the other is unaffected:

```bash
# Check that both have separate keys
ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -t -c \"SELECT id, site_id, appliance_id, key_prefix, active FROM api_keys WHERE active = true ORDER BY id DESC\""
```

Expected: Two separate active keys, each with a different appliance_id

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "verified: per-appliance API keys — both appliances checking in independently"
```
